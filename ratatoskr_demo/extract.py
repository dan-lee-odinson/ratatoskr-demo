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

"""Ask the model for Dan's follow-ups from one transcript.

The model is called through exactly one function type, `ModelCall`: it takes
a system prompt and a user message and returns the model's raw JSON text.
`bedrock_model_call()` builds the real one (openai.gpt-oss-20b on Amazon
Bedrock, see bedrock.py); tests pass a fake. Another backend would be another
`ModelCall`, and nothing else in the program would change.

The model is given the extraction JSON schema and must reply with exactly
one JSON object (JSON in text, see bedrock_model_call for why), which is
validated strictly here, including the length limits the schema language
cannot enforce. A response that breaks the schema gets one retry; a second
failure is a terminal error for that recording (C7).

`Extraction` deliberately has no transcript attribute, and its dataclasses use
__slots__, so no one can attach one later. Only an Extraction may be passed to
route(), which is how the transcript is kept out of routing by type.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Any, Callable

from ratatoskr_demo.config import Settings
from ratatoskr_demo.errors import TerminalError

if TYPE_CHECKING:
    from ratatoskr_demo.bedrock import BedrockClient

log = logging.getLogger(__name__)

ModelCall = Callable[[str, str], str]

CONTEXT_MAX = 200
CONTENT_MAX = 100
NOTES_MAX = 300
CONFIDENCE_VALUES = ("high", "medium", "low")

CORRECTIVE_INSTRUCTION = "Your previous output quoted the transcript; describe, do not quote."
SCHEMA_RETRY_NOTE = (
    "Your previous output did not match the required schema (problem at: {keys}). "
    "Return only JSON that matches the schema exactly, within the length limits."
)


@dataclass(frozen=True, slots=True)
class Contact:
    name: str | None
    organisation: str | None
    role: str | None
    context: str


@dataclass(frozen=True, slots=True)
class Task:
    content: str
    due_string: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class Extraction:
    contact: Contact
    tasks: tuple[Task, ...]
    confidence: str

    def texts(self) -> list[str]:
        """Every model-written string, for the C9 guard."""
        c = self.contact
        out = [s for s in (c.name, c.organisation, c.role, c.context) if s]
        for task in self.tasks:
            out.extend(s for s in (task.content, task.due_string, task.notes) if s)
        return out


class ExtractionSchemaError(TerminalError):
    """The model's output does not match the extraction schema."""


_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["contact", "tasks", "confidence"],
    "properties": {
        "contact": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "organisation", "role", "context"],
            "properties": {
                "name": _NULLABLE_STRING,
                "organisation": _NULLABLE_STRING,
                "role": _NULLABLE_STRING,
                "context": {"type": "string"},
            },
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["content", "due_string", "notes"],
                "properties": {
                    "content": {"type": "string"},
                    "due_string": _NULLABLE_STRING,
                    "notes": _NULLABLE_STRING,
                },
            },
        },
        "confidence": {"type": "string", "enum": list(CONFIDENCE_VALUES)},
    },
}


def run(transcript: str, settings: Settings, model_call: ModelCall, *, corrective: bool = False) -> Extraction:
    """Extract, retrying once on a schema violation. Raises ExtractionSchemaError after that."""
    system = system_prompt(settings)
    user = user_message(transcript, corrective=corrective)
    try:
        return parse_extraction(model_call(system, user))
    except ExtractionSchemaError as first:
        log.warning("extraction did not match the schema (%s); retrying once", ", ".join(first.keys))
        retry = user + "\n\n" + SCHEMA_RETRY_NOTE.format(keys=", ".join(first.keys) or "unknown")
        return parse_extraction(model_call(system, retry))


def system_prompt(settings: Settings) -> str:
    return read_prompt(settings, "extract.md") + "\n\n# About Dan's projects\n\n" + read_prompt(settings, "context.md")


def user_message(transcript: str, *, corrective: bool) -> str:
    message = (
        "Below is the transcript of one recorded conversation. "
        "Return Dan's follow-ups as JSON matching the schema.\n\n"
        f"<transcript>\n{transcript}\n</transcript>"
    )
    if corrective:
        message += "\n\n" + CORRECTIVE_INSTRUCTION
    return message


def read_prompt(settings: Settings, name: str) -> str:
    """Dan's edited copy in prompts_dir wins; the packaged copy is the fallback."""
    edited = settings.paths.prompts_dir / name
    if edited.is_file():
        return edited.read_text(encoding="utf-8")
    return resources.files("ratatoskr_demo").joinpath("prompts", name).read_text(encoding="utf-8")


def parse_extraction(raw: str) -> Extraction:
    """Validate the model's JSON strictly. Errors name keys, never values."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise ExtractionSchemaError("model output is not JSON", keys=("(root)",)) from None
    _require_object(data, "(root)", {"contact", "tasks", "confidence"})

    contact_raw = data["contact"]
    _require_object(contact_raw, "contact", {"name", "organisation", "role", "context"})
    contact = Contact(
        name=_nullable_str(contact_raw, "name", "contact"),
        organisation=_nullable_str(contact_raw, "organisation", "contact"),
        role=_nullable_str(contact_raw, "role", "contact"),
        context=_bounded_str(contact_raw, "context", "contact", CONTEXT_MAX),
    )

    if not isinstance(data["tasks"], list):
        raise ExtractionSchemaError("tasks is not a list", keys=("tasks",))
    tasks = []
    for i, task_raw in enumerate(data["tasks"]):
        where = f"tasks[{i}]"
        _require_object(task_raw, where, {"content", "due_string", "notes"})
        content = _bounded_str(task_raw, "content", where, CONTENT_MAX)
        if not content.strip():
            raise ExtractionSchemaError("empty task content", keys=(f"{where}.content",))
        notes = _nullable_str(task_raw, "notes", where)
        if notes is not None and len(notes) > NOTES_MAX:
            raise ExtractionSchemaError("notes too long", keys=(f"{where}.notes",))
        tasks.append(Task(content=content, due_string=_nullable_str(task_raw, "due_string", where), notes=notes))

    if data["confidence"] not in CONFIDENCE_VALUES:
        raise ExtractionSchemaError("confidence not high/medium/low", keys=("confidence",))
    return Extraction(contact=contact, tasks=tuple(tasks), confidence=data["confidence"])


def _require_object(value: Any, where: str, keys: set[str]) -> None:
    if not isinstance(value, dict):
        raise ExtractionSchemaError(f"{where} is not an object", keys=(where,))
    missing, extra = keys - set(value), set(value) - keys
    if missing or extra:
        bad = sorted(f"{where}.{k}" for k in missing | extra)
        raise ExtractionSchemaError(f"{where} has missing or unexpected keys", keys=tuple(bad))


def _nullable_str(obj: dict, key: str, where: str) -> str | None:
    value = obj[key]
    if value is not None and not isinstance(value, str):
        raise ExtractionSchemaError(f"{where}.{key} is not a string or null", keys=(f"{where}.{key}",))
    return value


def _bounded_str(obj: dict, key: str, where: str, limit: int) -> str:
    value = obj[key]
    if not isinstance(value, str):
        raise ExtractionSchemaError(f"{where}.{key} is not a string", keys=(f"{where}.{key}",))
    if len(value) > limit:
        raise ExtractionSchemaError(f"{where}.{key} longer than {limit}", keys=(f"{where}.{key}",))
    return value


OUTPUT_FORMAT_NOTE = (
    "\n\n# Output format\n\nReply with one JSON object and nothing else: no code fence, no text before "
    "or after it. It must match this JSON Schema exactly:\n\n"
)


def bedrock_model_call(client: "BedrockClient") -> ModelCall:
    """Build the real ModelCall on Bedrock (openai.gpt-oss-20b through bedrock-mantle).

    Structured output path: JSON in text. The extraction JSON Schema is given
    in the system prompt, the reply must be one JSON object, and
    parse_extraction() validates it strictly; a reply that fails gets the one
    corrective retry in run(), and a second failure is terminal (C7).

    Why not the first two paths, in the order the build was asked to try them
    (measured 16 Oct 2026 on the synthetic fixtures rec_001 and rec_005, five
    calls each, reasoning_effort low):

      response_format json_schema (strict)  accepted by mantle, but 0/10 replies
                                            were valid JSON: they began with a
                                            stray brace ('{ {"contact"...') or a
                                            code fence, so the schema is not enforced
      tool calling, the schema as the one   4/10 valid
        forced tool
      JSON in text, schema in the prompt    10/10 valid, with no repair

    The one leniency: a reply wrapped in a ``` code fence is unwrapped before
    parsing. Nothing else is repaired or guessed; anything that is not exactly
    the schema is a schema error.

    Failures come sorted from bedrock.py: transient (network, timeout, 429,
    5xx) and configuration (401/403, other 4xx, wrong model, spend hard stop).
    A reply cut off at max_tokens, or with no text, is a schema error, so it
    gets the one retry.
    """
    schema_text = json.dumps(EXTRACTION_SCHEMA, separators=(",", ":"))

    def call(system: str, user: str) -> str:
        result = client.chat(
            [{"role": "system", "content": system + OUTPUT_FORMAT_NOTE + schema_text},
             {"role": "user", "content": user}],
            purpose="extract",
        )
        if result.finish_reason == "length":
            raise ExtractionSchemaError("model output cut off at max_tokens", keys=("max_tokens",))
        if not result.content:
            raise ExtractionSchemaError("model returned no text", keys=("(root)",))
        return unfence(result.content)

    return call


def unfence(text: str) -> str:
    """Remove one surrounding ``` (or ```json) code fence, if the whole reply is wrapped in one."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```") and len(stripped) > 6:
        inner = stripped[3:-3]
        first_line, _, rest = inner.partition("\n")
        return (rest if first_line.strip().lower() in ("", "json") else inner).strip()
    return stripped
