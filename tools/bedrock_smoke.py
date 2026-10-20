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

"""Bedrock smoke test and probes (handoff §6): run once before building on the endpoint.

    python tools/bedrock_smoke.py [--write-baseline] [--probe]

1. GET /v1/models (no inference, no cost) and confirm openai.gpt-oss-20b is listed.
   With --write-baseline, save its entry as config/model_baseline.json.
2. One tiny chat completion through the spend ledger; confirm the response's
   `model` names the model requested.
3. With --probe: does mantle validate and honor `reasoning_effort`, and which
   structured-output path works (response_format json_schema, then tool calling)?

Every call goes through ratatoskr_demo.bedrock and the spend ledger. Prints
model ids, finish reasons, token counts and costs; never a header or the key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ratatoskr_demo.bedrock import BedrockClient, canonical, make_spend_ledger  # noqa: E402
from ratatoskr_demo.config import load_settings  # noqa: E402
from ratatoskr_demo.errors import ConfigurationError  # noqa: E402

TINY = [{"role": "user", "content": "Reply with the single word: ready"}]
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["colour", "count"],
    "properties": {"colour": {"type": "string"}, "count": {"type": "integer"}},
}
STRUCTURED = [{"role": "user", "content": "Return JSON: the colour of a clear daytime sky, and the number 3."}]
EFFORT_PROMPT = [{"role": "user", "content": "Is 391 a prime number? Answer yes or no, then one short reason."}]


def show(label: str, result) -> None:
    print(f"{label}: model={result.model!r} finish={result.finish_reason} "
          f"tokens in/out={result.input_tokens}/{result.output_tokens} cost=${result.cost_usd:.6f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    spend = make_spend_ledger(settings)
    client = BedrockClient(settings, spend)

    models = client.list_models()
    ids = sorted(str(m.get("id")) for m in models)
    print(f"GET /v1/models: {len(ids)} models listed; {settings.model.model_id} listed: "
          f"{settings.model.model_id in ids}")
    record = client.model_record()
    print(f"record for {settings.model.model_id}: {json.dumps(record, sort_keys=True)}")
    if args.write_baseline:
        path = settings.paths.model_baseline
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(canonical(record), indent=2, sort_keys=True) + "\n").encode("utf-8"))
        print(f"baseline written to {path}")

    result = client.chat(TINY, purpose="smoke", max_tokens=200)
    show("tiny chat", result)
    print(f"  response model names the model requested: {result.model == settings.model.model_id}")
    print(f"  content: {result.content!r}")

    if args.probe:
        probe_effort(client)
        probe_structured(client)
    print(f"spend ledger: {json.dumps(spend.summary())}")
    return 0


def probe_effort(client: BedrockClient) -> None:
    saved = client.reasoning_effort
    try:
        client.reasoning_effort = "bogus"
        try:
            client.chat(TINY, purpose="probe-effort", max_tokens=200)
            print("reasoning_effort='bogus': accepted (the field is not validated)")
        except ConfigurationError as exc:
            print(f"reasoning_effort='bogus': rejected ({exc})")
        for effort in ("low", "high"):
            client.reasoning_effort = effort
            show(f"reasoning_effort={effort}", client.chat(EFFORT_PROMPT, purpose="probe-effort", max_tokens=2000))
    finally:
        client.reasoning_effort = saved


def probe_structured(client: BedrockClient) -> None:
    fmt = {"type": "json_schema", "json_schema": {"name": "probe", "strict": True, "schema": SCHEMA}}
    try:
        result = client.chat(STRUCTURED, purpose="probe-structured", response_format=fmt, max_tokens=1000)
        show("response_format json_schema", result)
        report_json(result.content)
    except ConfigurationError as exc:
        print(f"response_format json_schema: rejected ({exc})")
    tool = {"type": "function", "function": {"name": "record", "description": "Record the answer.",
                                             "parameters": SCHEMA}}
    try:
        result = client.chat(STRUCTURED, purpose="probe-structured", tools=[tool],
                             tool_choice={"type": "function", "function": {"name": "record"}}, max_tokens=1000)
        show("tool calling (forced)", result)
        report_json(result.tool_arguments)
    except ConfigurationError as exc:
        print(f"tool calling (forced): rejected ({exc})")


def report_json(text: str | None) -> None:
    try:
        value = json.loads(text or "")
        ok = isinstance(value, dict) and set(value) == {"colour", "count"} and isinstance(value["count"], int)
        print(f"  parses as JSON: True; matches the schema: {ok}; value: {value}")
    except ValueError:
        print(f"  parses as JSON: False; text: {text!r}")


if __name__ == "__main__":
    raise SystemExit(main())
