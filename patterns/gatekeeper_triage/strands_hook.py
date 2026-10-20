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

"""Gatekeeper triage as a Strands Agents hook: Decider decides before a tool may run.

    from strands import Agent
    from decider_client import gate_source_text
    from strands_hook import GatekeeperHook

    verdict = gate_source_text(note)  # 1. the note, before any model reads it
    if verdict.verdict == "hold":
        ...  # a person reviews it; the agent never runs
    else:
        hook = GatekeeperHook(tools={"add_task"})  # 2. then each add_task call
        agent = Agent(model=..., tools=[add_task], hooks=[hook])
        agent(note)

The SOURCE text (the transcript, note or email the agent is acting on) is
gated first, with decider_client.gate_source_text(), before the agent is
invoked: once the agent runs, the model has read the note. An agent writes
its own tool arguments and can drop the detail that made the source
sensitive, which is why the arguments alone are only the fallback.

`GatekeeperHook` registers a `BeforeToolCallEvent` callback. For each pending
call to a guarded tool it runs the pattern over the call's arguments
(decider_client.triage: the tripwire, then the six typed questions in one
read). A "hold" sets `event.cancel_tool`, so the tool does not run and the
agent is told why; `hook.decisions` records how each call was judged. Given
`source_text`, the hook also gates that text once, before the first guarded
call, and a hold there cancels every guarded call; by then the model has
read it, so this is a backstop, not the gate.

Hook API read from strands-agents 1.61.1 (strands/hooks/events.py and
registry.py, 16 Oct 2026): BeforeToolCallEvent carries `tool_use` (name,
input, toolUseId) and a writable `cancel_tool` ("a user defined message that
when set, will cancel the tool call").

The demo at the bottom (`python strands_hook.py "<note>"`) gives an agent one
tool, add_task, and uses the Strands OpenAI-compatible model provider pointed
at Amazon Bedrock's bedrock-mantle endpoint for its one generative step:

    OpenAIModel(client_args={"api_key": BEDROCK_API_KEY, "base_url": MANTLE_URL},
                model_id="openai.gpt-oss-20b", params={...}, stream=False)

The Converse provider is not used: the project's IAM policy allows only
bedrock-mantle. Requires `pip install "strands-agents[openai]==1.61.1"`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from strands import tool
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decider_client import DeciderClient, Questions, Triage, Verdict, load_questions  # noqa: E402

MANTLE_URL = "https://bedrock-mantle.us-east-1.api.aws/v1"
MODEL_ID = "openai.gpt-oss-20b"


def default_state(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """What Decider is shown about a pending tool call: its name and its arguments."""
    return {"tool": tool_name, "arguments": arguments}


class GatekeeperHook(HookProvider):
    def __init__(self, client: DeciderClient | None = None, questions: Questions | None = None, *,
                 tools: set[str] | None = None, source_text: str | None = None,
                 state_of: Callable[[str, dict[str, Any]], Any] = default_state) -> None:
        self.client = client or DeciderClient()
        self.questions = questions or load_questions()
        self.tools = tools  # None guards every tool
        self.source_text = source_text  # the text the agent acts on, gated first when present
        self.source_verdict: Verdict | None = None
        self.state_of = state_of
        self.decisions: list[tuple[str, Triage | Verdict]] = []

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if self.tools is not None and name not in self.tools:
            return
        if self.source_text is not None:
            if self.source_verdict is None:  # once per hook, before the first guarded call
                self.source_verdict = self.client.gate_source_text(self.source_text, self.questions)
            if self.source_verdict.verdict == "hold":
                self.decisions.append((name, self.source_verdict))
                event.cancel_tool = (f"HELD by gatekeeper triage on the source text ({self.source_verdict.reason}). "
                                     "The tool did not run; a person will review it. Do not call it again.")
                return
        arguments = event.tool_use.get("input") or {}
        triage = self.client.triage(self.state_of(name, arguments), self.questions,
                                    text=json.dumps(arguments, ensure_ascii=False))
        self.decisions.append((name, triage))
        if triage.verdict == "hold":
            event.cancel_tool = (f"HELD by gatekeeper triage ({triage.reason}). The tool did not run; "
                                 "a person will review it. Do not call it again for this request.")


# --- the demo: one agent, one guarded tool, Bedrock for the generative step ---

ADDED: list[dict[str, Any]] = []


@tool
def add_task(content: str, notes: str = "") -> str:
    """Add one follow-up task to the task list.

    Args:
        content: The task, as a short imperative sentence.
        notes: Any detail needed to act on it.
    """
    ADDED.append({"content": content, "notes": notes})
    return f"added: {content}"


SYSTEM_PROMPT = ("You turn a note about a conversation into follow-up tasks. Call add_task once for each "
                 "concrete commitment in the note, then reply with one short sentence. Do not quote the note.")


def bedrock_model(api_key: str, *, http_client: Any = None, max_tokens: int = 1000) -> Any:
    """The Strands OpenAI-compatible provider, pointed at bedrock-mantle."""
    from strands.models.openai import OpenAIModel

    client_args: dict[str, Any] = {"api_key": api_key, "base_url": MANTLE_URL}
    if http_client is not None:
        client_args["http_client"] = http_client
    return OpenAIModel(client_args=client_args, model_id=MODEL_ID,
                       params={"max_tokens": max_tokens, "reasoning_effort": "low"}, stream=False)


def make_agent(model: Any, hook: GatekeeperHook) -> Any:
    from strands import Agent

    return Agent(model=model, tools=[add_task], hooks=[hook], system_prompt=SYSTEM_PROMPT, callback_handler=None)


def describe(decision: Triage | Verdict) -> str:
    if isinstance(decision, Verdict):
        return (f"{decision.verdict} ({decision.reason}) on the source text: {decision.windows_asked} of "
                f"{decision.windows_total} window(s), max p={decision.max_p:.2f}")
    return decision.line()


def main(argv: list[str], *, client: DeciderClient | None = None,
         build_agent: Callable[[GatekeeperHook], Any] | None = None) -> int:
    note = " ".join(argv) or "Met Priya at the meetup. I promised to email her my thermal preprint by Friday."
    client = client or DeciderClient()
    questions = load_questions()
    verdict = client.gate_source_text(note, questions)  # 1. the note, before any model reads it
    print(f"source: {describe(verdict)}")
    if verdict.verdict == "hold":
        print("held before the agent ran: nothing of this note went to the model")
        return 0
    hook = GatekeeperHook(client, questions, tools={"add_task"})  # 2. then each add_task call's arguments
    build_agent = build_agent or (lambda h: make_agent(bedrock_model(os.environ["BEDROCK_API_KEY"]), h))
    build_agent(hook)(note)
    for name, decision in hook.decisions:
        print(f"{name}: {describe(decision)}")
    print(f"tasks added: {len(ADDED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

