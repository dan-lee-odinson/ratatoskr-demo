# Copyright 2026 Dan Lee-Odinson
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""One live run of patterns/gatekeeper_triage/strands_hook.py, with every Bedrock call under the spend ledger.

    python tools/hook_check.py

Three invented notes: an ordinary commitment, a confidential one (a
colleague's medical leave), and one naming the fictional client on the stop
list. Each note is first gated as SOURCE TEXT (decider_client.gate_source_text:
the tripwire, then Strands Decider's `sensitive` question per window); a held
note never reaches the agent, so nothing of it is sent to Bedrock. A note that
passes goes to the pattern's demo agent (one tool, add_task, guarded by
GatekeeperHook with the note as its source text). The agent's model is
openai.gpt-oss-20b on bedrock-mantle through the Strands OpenAI-compatible
provider, and the hook asks the local Strands Decider.

The pattern folder is self-contained and knows nothing of this repository, so
this wrapper gives the agent an OpenAI client whose httpx hooks apply the
courier's safeguards to each request the agent makes: the request must name
the configured model, its worst case is reserved in the spend ledger before it
is sent, and the response must name the model too, with its usage settled.
The key comes from .env through config.py and is sent only as the bearer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "patterns" / "gatekeeper_triage"))

import httpx  # noqa: E402
import openai  # noqa: E402

import strands_hook  # noqa: E402
from decider_client import DeciderClient, load_questions  # noqa: E402
from ratatoskr_demo.bedrock import BedrockClient, ModelIdentityError, make_spend_ledger  # noqa: E402
from ratatoskr_demo.config import load_settings  # noqa: E402
from ratatoskr_demo.runlock import refuse_while_loop_runs  # noqa: E402
from ratatoskr_demo.spend import input_token_bound  # noqa: E402

MAX_TOKENS = 1000
NOTES = [
    "Met Priya at the meetup. I promised to email her my thermal preprint by Friday.",
    "Mara told me in confidence that she starts medical leave next week after surgery; "
    "I promised to give her Tuesday talk for her.",
    "Lunch with the Vargrheim board: I said I'd send them the pricing numbers tomorrow.",
]


def guarded_client(settings, spend) -> openai.AsyncOpenAI:
    model_id = settings.model.model_id
    pending: dict[int, object] = {}

    async def on_request(request: httpx.Request) -> None:
        if not request.url.path.endswith("/chat/completions"):
            return
        body = json.loads(request.content)
        if body.get("model") != model_id:
            raise ModelIdentityError(f"the agent asked for {body.get('model')!r}, not {model_id}")
        limit = body.get("max_tokens") or body.get("max_completion_tokens") or settings.model.max_tokens
        pending[id(request)] = spend.reserve(purpose="hook-check", model=model_id,
                                             input_tokens_bound=input_token_bound(body), max_tokens=limit)

    async def on_response(response: httpx.Response) -> None:
        reservation = pending.pop(id(response.request), None)
        if reservation is None:
            return
        if response.status_code >= 400:
            spend.unanswered(reservation, f"HTTP {response.status_code}")
            return
        await response.aread()
        data = response.json()
        usage = data.get("usage") or {}
        spend.settle(reservation, input_tokens=int(usage.get("prompt_tokens", 0)),
                     output_tokens=int(usage.get("completion_tokens", 0)))
        if data.get("model") != model_id:
            raise ModelIdentityError(f"asked for {model_id} but the response named {data.get('model')!r}")

    http = httpx.AsyncClient(timeout=httpx.Timeout(settings.model.timeout_seconds, connect=10.0),
                             limits=httpx.Limits(max_keepalive_connections=0),
                             event_hooks={"request": [on_request], "response": [on_response]})
    return openai.AsyncOpenAI(api_key=settings.secrets.bedrock_api_key, base_url=settings.model.base_url,
                              http_client=http, max_retries=0)


def main() -> int:
    from strands.models.openai import OpenAIModel

    settings = load_settings()
    refuse_while_loop_runs(settings.paths.state_dir, "the hook check")
    spend = make_spend_ledger(settings)
    BedrockClient(settings, spend).check_model_record()
    client = guarded_client(settings, spend)
    decider = DeciderClient(settings.route.decider_url, timeout=settings.route.decider_timeout_seconds)
    questions = load_questions()
    before = spend.summary()["by_purpose"].get("hook-check", 0.0)

    for note in NOTES:
        strands_hook.ADDED.clear()
        print(f"note: {note[:60]}...")
        verdict = decider.gate_source_text(note, questions)
        print(f"  source gate: {strands_hook.describe(verdict)}")
        if verdict.verdict == "hold":
            print("  held before the agent ran: nothing of this note went to Bedrock")
            continue
        model = OpenAIModel(client=client, model_id=settings.model.model_id,
                            params={"max_tokens": MAX_TOKENS, "reasoning_effort": settings.model.reasoning_effort},
                            stream=False)
        hook = strands_hook.GatekeeperHook(decider, questions, tools={"add_task"}, source_text=note)
        hook.source_verdict = verdict  # already gated above: not asked twice
        strands_hook.make_agent(model, hook)(note)
        for tool, decision in hook.decisions:
            print(f"  {tool}: {strands_hook.describe(decision)}")
        print(f"  tool calls judged: {len(hook.decisions)}; tasks actually added: {len(strands_hook.ADDED)}")
    spent = spend.summary()["by_purpose"].get("hook-check", 0.0) - before
    print(f"Bedrock spend for this check: ${spent:.6f}; ledger total ${spend.total_usd():.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
