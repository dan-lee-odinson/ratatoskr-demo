"""§4.3: one test per rule, the keys-only error, and the real-device check."""

import json
from pathlib import Path

import pytest

from ratatoskr_demo.transcript import NOT_READY, UnrecognisedTranscriptShape, extract_text

REAL = Path(__file__).parent / "fixtures" / "real_recording.json"


def test_rule_1_a_string_is_returned_as_is():
    assert extract_text({"transcript": "hello there"}) == "hello there"


def test_rule_2_an_object_with_text_returns_the_text():
    assert extract_text({"transcript": {"text": "hello", "language": "en"}}) == "hello"


def test_rule_3_segments_are_joined_with_newlines_and_speakers():
    segments = [{"text": "Hi", "speaker": "A", "start": 0}, {"text": "Hello", "speaker": "B"}, {"text": "bye"}]
    assert extract_text({"transcript": segments}) == "A: Hi\nB: Hello\nbye"


def test_rule_4_an_object_with_segments_uses_rule_3():
    assert extract_text({"transcript": {"segments": [{"text": "one"}, {"text": "two"}]}}) == "one\ntwo"


def test_rule_5_null_with_a_transcript_error_is_not_ready():
    assert extract_text({"transcript": None, "transcript_error": "failed"}) is NOT_READY


def test_rule_6_null_without_an_error_is_not_ready():
    assert extract_text({"transcript": None, "transcript_error": None}) is NOT_READY


def test_an_empty_transcript_is_not_ready():
    assert extract_text({"transcript": "   "}) is NOT_READY


def test_an_unrecognised_shape_raises_with_keys_only():
    with pytest.raises(UnrecognisedTranscriptShape) as info:
        extract_text({"transcript": {"words": "confidential words here", "lang": "en"}})
    assert info.value.keys == ("lang", "words")
    assert "confidential" not in str(info.value)


def test_a_segment_without_text_is_unrecognised():
    with pytest.raises(UnrecognisedTranscriptShape) as info:
        extract_text({"transcript": [{"utterance": "secret stuff"}]})
    assert info.value.keys == ("utterance",)
    assert "secret" not in str(info.value)


@pytest.mark.skipif(not REAL.exists(), reason="real_recording.json arrives with Dan's device")
def test_real_recording_resolves_to_text():
    body = json.loads(REAL.read_text(encoding="utf-8"))
    recording = body.get("data", body)
    text = extract_text(recording)
    assert isinstance(text, str) and text.strip()
