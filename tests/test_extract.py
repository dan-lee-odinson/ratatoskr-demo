"""§6 extraction: prompt rules, schema validation, retry, and error sorting."""

import json
import re

import pytest

from ratatoskr_demo.config import Settings
from ratatoskr_demo.errors import TerminalError
from ratatoskr_demo.extract import (
    CORRECTIVE_INSTRUCTION, EXTRACTION_SCHEMA, Extraction, ExtractionSchemaError, parse_extraction, run,
    system_prompt,
)
from tests.conftest import REC_001_TASKS, extraction_json
from tests.test_bedrock import bedrock, mantle  # noqa: F401  (the mocked-mantle fixtures)

SETTINGS = Settings()


def test_the_prompt_file_contains_do_not_quote():
    assert "Do not quote" in system_prompt(SETTINGS)


def test_the_prompt_contains_no_instruction_to_quote():
    prompt = system_prompt(SETTINGS).lower()
    for positive in re.findall(r"[^.\n]*\bquot\w*[^.\n]*", prompt):
        assert "do not quote" in positive or "never" in positive, positive


def test_the_context_file_names_the_seven_demo_areas_and_nothing_personal():
    prompt = system_prompt(SETTINGS)
    for area in ("Research:", "School:", "Work:", "Writing:", "Community:", "Personal:"):
        assert area in prompt
    assert "Dan writes this" not in prompt


def test_a_valid_response_parses_into_an_extraction():
    extraction = parse_extraction(extraction_json(tasks=REC_001_TASKS))
    assert isinstance(extraction, Extraction)
    assert [t.content for t in extraction.tasks][0] == "Email Priya the orbital thermal bounds preprint"
    assert extraction.contact.organisation == "Aldrin Microsystems"


def test_an_extraction_has_no_transcript_attribute_and_none_can_be_added():
    extraction = parse_extraction(extraction_json())
    assert not hasattr(extraction, "transcript")
    with pytest.raises((AttributeError, TypeError)):
        extraction.transcript = "x"  # slots + frozen


@pytest.mark.parametrize("mutate, key", [
    (lambda d: d["tasks"].append({"content": "x" * 101, "due_string": None, "notes": None}), "tasks[3].content"),
    (lambda d: d["tasks"][0].update(notes="n" * 301), "tasks[0].notes"),
    (lambda d: d["contact"].update(context="c" * 201), "contact.context"),
    (lambda d: d.update(confidence="certain"), "confidence"),
    (lambda d: d["tasks"][0].update(extra="field"), "tasks[0].extra"),
    (lambda d: d["contact"].pop("role"), "contact.role"),
])
def test_schema_violations_name_the_key_not_the_value(mutate, key):
    data = json.loads(extraction_json(tasks=[dict(t) for t in REC_001_TASKS]))
    mutate(data)
    with pytest.raises(ExtractionSchemaError) as info:
        parse_extraction(json.dumps(data))
    assert key in info.value.keys
    assert "Priya" not in str(info.value)


def test_a_schema_violation_is_retried_once_and_then_succeeds():
    replies = iter(["not json", extraction_json(tasks=REC_001_TASKS)])
    calls = []

    def model(system, user):
        calls.append(user)
        return next(replies)

    extraction = run("a transcript", SETTINGS, model)
    assert len(extraction.tasks) == 3
    assert len(calls) == 2 and "did not match the required schema" in calls[1]


def test_a_second_schema_violation_is_terminal():
    calls = []

    def model(system, user):
        calls.append(user)
        return "{}"

    with pytest.raises(ExtractionSchemaError) as info:
        run("a transcript", SETTINGS, model)
    assert isinstance(info.value, TerminalError)
    assert len(calls) == 2


def test_the_corrective_run_appends_the_describe_do_not_quote_instruction():
    seen = []
    run("a transcript", SETTINGS, lambda s, u: seen.append(u) or extraction_json(), corrective=True)
    assert seen[0].endswith(CORRECTIVE_INSTRUCTION)


def test_the_schema_forbids_extra_keys_everywhere():
    assert EXTRACTION_SCHEMA["additionalProperties"] is False
    assert EXTRACTION_SCHEMA["properties"]["contact"]["additionalProperties"] is False
    assert EXTRACTION_SCHEMA["properties"]["tasks"]["items"]["additionalProperties"] is False


# --- the Bedrock ModelCall: response_format with the extraction schema ---

def test_the_bedrock_model_call_gives_the_schema_in_the_prompt_and_returns_the_text(tmp_path, bedrock):
    """JSON in text: mantle's response_format did not enforce the schema (see bedrock_model_call)."""
    from ratatoskr_demo.extract import OUTPUT_FORMAT_NOTE, bedrock_model_call

    client, mantle = bedrock
    text = bedrock_model_call(client)("system words", "user words")
    assert parse_extraction(text).tasks == ()
    [body] = mantle.bodies
    assert body["model"] == "openai.gpt-oss-20b" and body["max_tokens"] == 2000 and body["reasoning_effort"] == "low"
    system, user = body["messages"]
    assert system["role"] == "system" and user == {"role": "user", "content": "user words"}
    assert system["content"].startswith("system words" + OUTPUT_FORMAT_NOTE)
    assert json.loads(system["content"][len("system words" + OUTPUT_FORMAT_NOTE):]) == EXTRACTION_SCHEMA
    assert "response_format" not in body and "tools" not in body


@pytest.mark.parametrize("wrapped", ["```json\n{}\n```", "```\n{}\n```", "  {}  "])
def test_a_reply_in_a_code_fence_is_unwrapped_and_nothing_else_is_repaired(wrapped):
    from ratatoskr_demo.extract import unfence

    assert unfence(wrapped) == "{}"
    assert unfence('{\n{"contact": 1}') == '{\n{"contact": 1}'  # a broken reply stays broken: a schema error


def test_a_fenced_reply_from_bedrock_parses(tmp_path, bedrock):
    from ratatoskr_demo.extract import bedrock_model_call

    client, mantle = bedrock
    mantle.content = "```json\n" + extraction_json(tasks=REC_001_TASKS) + "\n```"
    assert len(parse_extraction(bedrock_model_call(client)("s", "u")).tasks) == 3


def test_a_reply_cut_off_at_max_tokens_is_a_schema_violation_so_it_gets_the_retry(tmp_path, bedrock):
    from ratatoskr_demo.extract import bedrock_model_call

    client, mantle = bedrock
    mantle.finish_reason = "length"
    with pytest.raises(ExtractionSchemaError):
        bedrock_model_call(client)("s", "u")


def test_an_empty_reply_is_a_schema_violation(tmp_path, bedrock):
    from ratatoskr_demo.extract import bedrock_model_call

    client, mantle = bedrock
    mantle.content = None
    with pytest.raises(ExtractionSchemaError):
        bedrock_model_call(client)("s", "u")
