"""§5: one test per triage branch, plus the precedence cases the handoff names."""

import pytest

from ratatoskr_demo.config import Settings, StopTerm, TriageSettings
from ratatoskr_demo.ledger import LedgerEntry
from ratatoskr_demo.transcript import UnrecognisedTranscriptShape
from ratatoskr_demo.triage import Hold, NotReady, Process, Release, Skip, StillHeld, decide

SETTINGS = Settings()


def recording(transcript="We agreed I would send the deck.", title="Meetup", tags=()):
    return {"id": "rec_x", "title": title, "transcript": transcript, "tags": [{"name": t} for t in tags]}


def entry(status, **kw):
    return LedgerEntry(status=status, title="t", recording_at="2026-09-01T00:00:00Z",
                       decided_at="2026-09-01T00:00:00Z", **kw)


def test_a_keep_tag_skips_forever():
    assert decide(recording(tags=["keep"]), None, SETTINGS) == Skip("keep")


@pytest.mark.parametrize("status", ["done", "keep", "error"])
def test_a_recording_already_in_the_ledger_is_skipped(status):
    assert decide(recording(), entry(status), SETTINGS) == Skip(f"already:{status}")


def test_a_recording_without_a_transcript_is_not_ready():
    assert decide(recording(transcript=None), None, SETTINGS) == NotReady()


def test_a_hold_tag_holds():
    assert decide(recording(tags=["hold"]), None, SETTINGS) == Hold("tag:hold")


def test_a_held_recording_tagged_ok_is_released():
    assert decide(recording(tags=["ok"]), entry("hold"), SETTINGS) == Release()


def test_a_held_recording_without_ok_is_still_held():
    assert decide(recording(), entry("hold"), SETTINGS) == StillHeld()


def test_a_stop_list_term_in_the_transcript_holds():
    assert decide(recording(transcript="I work for Vargrheim."), None, SETTINGS) == Hold("stoplist:vargrheim")


def test_a_stop_list_term_in_the_title_holds():
    assert decide(recording(title="Vargrheim sync"), None, SETTINGS) == Hold("stoplist:vargrheim")


def test_an_ordinary_recording_is_processed():
    assert decide(recording(), None, SETTINGS) == Process()


def test_an_ok_tag_releases_a_stop_list_hold_and_the_stop_list_does_not_retrigger():
    held = entry("hold", reason="stoplist:vargrheim")
    assert decide(recording(transcript="Vargrheim came up.", tags=["ok"]), held, SETTINGS) == Release()


def test_stop_list_match_is_case_insensitive():
    assert decide(recording(transcript="vArGrHeIm"), None, SETTINGS) == Hold("stoplist:vargrheim")


def test_stop_list_hit_reports_the_term_and_not_the_surrounding_text():
    decision = decide(recording(transcript="secret project name at Vargrheim is Falcon"), None, SETTINGS)
    assert decision == Hold("stoplist:vargrheim")
    assert "Falcon" not in repr(decision) and "secret" not in repr(decision)


# --- Dan's ruling 6 (17 Oct 2026): how a transcriber spells the name ---

TERM_ONLY = Settings(triage=TriageSettings(stop_list=(StopTerm("vargrheim"),)))  # no spellings: the sound decides


@pytest.mark.parametrize("heard", ["Vargrime", "Vargrhyme", "var grime", "VARGRHEIM"])
def test_a_misspelled_or_split_stop_list_name_still_holds(heard):
    transcript = f"Speaker 1: I do program management for {heard}, the aerospace outfit."
    assert decide(recording(transcript=transcript), None, TERM_ONLY) == Hold("stoplist:vargrheim")


def test_a_name_that_sounds_like_the_term_holds_in_the_title_too():
    assert decide(recording(title="Call with Vargrime"), None, TERM_ONLY) == Hold("stoplist:vargrheim")


@pytest.mark.parametrize("word", ["Varga", "Vargas", "Vargo", "Varghese"])
def test_a_dissimilar_word_that_shares_the_prefix_does_not_hold(word):
    assert decide(recording(transcript=f"Speaker 1: {word} sent the onboarding checklist."), None, TERM_ONLY) == Process()


def test_a_listed_spelling_holds_even_when_it_does_not_sound_like_the_term():
    rules = Settings(triage=TriageSettings(stop_list=(StopTerm("vargrheim", ("vgh",)),)))
    assert decide(recording(transcript="Notes from the VGH call."), None, rules) == Hold("stoplist:vargrheim")


def test_a_sound_alike_hold_reports_the_term_never_the_word_heard():
    decision = decide(recording(transcript="Speaker 1: the Vargrhyme contract"), None, TERM_ONLY)
    assert decision == Hold("stoplist:vargrheim") and "rhyme" not in repr(decision).lower()


def test_no_fixture_recording_but_the_one_naming_the_client_holds():
    from ratatoskr_demo.expected import FIXTURE_DIRS
    import json

    tripped = []
    for directory in FIXTURE_DIRS:
        for path in sorted(directory.glob("rec_*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))["data"]
            if data.get("transcript") and decide(data, None, TERM_ONLY) == Hold("stoplist:vargrheim"):
                tripped.append(data["id"])
    assert tripped == ["rec_002"]


def test_an_ok_tag_has_no_effect_on_a_recording_that_was_never_held():
    assert decide(recording(tags=["ok"]), None, SETTINGS) == Process()


def test_tags_match_case_insensitively():
    assert decide(recording(tags=["KEEP"]), None, SETTINGS) == Skip("keep")


def test_keep_wins_over_everything_else():
    assert decide(recording(transcript=None, tags=["keep", "hold"]), entry("error"), SETTINGS) == Skip("keep")


def test_an_unrecognised_transcript_shape_propagates_as_a_terminal_error():
    with pytest.raises(UnrecognisedTranscriptShape):
        decide(recording(transcript={"weird": 1}), None, SETTINGS)
