"""Dan's rulings 13 (every hold notice carries the recording id only) and 14 (a transcript under 8 words is
skipped before the source gate: no read, no notice, marked seen)."""

import json

import httpx
import pytest

from ratatoskr_demo.commands import format_demo_reply, format_status
from ratatoskr_demo.cycle import MIN_TRANSCRIPT_WORDS, Outcome, too_short
from tests.conftest import FIXTURES, ScriptedModel, make_settings, runtime_for
from tests.test_decider import attach, decider_answers

TITLE = "Vargrime Onboarding Pack Review"  # the live run's title, built by Pocket from the conversation


def fixtures_with(tmp_path, changes):
    """tests/fixtures, with {recording id: {field: value}} applied to each copy."""
    target = tmp_path / "fx"
    target.mkdir(exist_ok=True)
    for path in FIXTURES.glob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        for record in body["data"] if isinstance(body["data"], list) else [body["data"]]:
            record.update(changes.get(record["id"], {}))
        (target / path.name).write_text(json.dumps(body), encoding="utf-8")
    return target


def ledger(settings):
    return json.loads(settings.ledger_path.read_text()) if settings.ledger_path.exists() else {}


# --- ruling 13 ---

def test_the_tripwire_hold_notice_carries_the_id_only_not_the_title_nor_the_term(tmp_path, clock, services):
    _, telegram, _ = services
    settings = make_settings(tmp_path, {"route": {"backend": "none"}},
                             fixtures=fixtures_with(tmp_path, {"rec_002": {"title": TITLE}}))
    runtime = runtime_for(settings, ScriptedModel(), clock)
    runtime.cycle.run()
    assert "Held rec_002: stop-list. Tag it ok in Pocket to release it." in telegram.texts
    assert not any(TITLE in t or "vargrime" in t.lower() or "vargrheim" in t.lower() for t in telegram.texts)
    assert ledger(settings)["rec_002"]["reason"] == "stoplist:vargrheim"  # the local ledger keeps the reason


@pytest.mark.parametrize("kind", ["tag hold", "source gate", "after extraction"])
def test_no_hold_notice_of_any_kind_carries_a_title(tmp_path, clock, services, kind):
    todoist, telegram, router = services
    fake = attach(router)
    changes = {rid: {"title": f"Title of {rid} with a secret"} for rid in ("rec_001", "rec_005")}
    if kind == "tag hold":
        changes["rec_001"]["tags"] = [{"color": "#888", "id": "tag_hold", "name": "hold"}]
    elif kind == "source gate":
        fake.gate_reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=0.95))
    else:
        fake.reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=0.95))
    settings = make_settings(tmp_path, {"route": {"backend": "decider"}}, fixtures=fixtures_with(tmp_path, changes))
    runtime_for(settings, ScriptedModel(), clock, router=fake.router()).cycle.run()
    held = [t for t in telegram.texts if t.startswith("Held rec_001")]
    assert held and not any("with a secret" in t for t in telegram.texts)


RELEASE = "Tag it ok in Pocket to release it."


@pytest.mark.parametrize("kind", ["stop-list", "tag hold", "source gate", "after extraction", "leak guard"])
def test_every_hold_notice_carries_the_release_instruction_and_no_title(tmp_path, clock, services, kind):
    """Dan's ruling 28: a hold the owner can release says so, whatever held it; and (ruling 13) the notice
    is the id, the kind of hold and that instruction, never the title."""
    from tests.test_leak import adversarial

    _, telegram, router = services
    fake = attach(router)
    changes = {rid: {"title": f"Title of {rid} with a secret"} for rid in ("rec_001", "rec_002", "rec_005")}
    model, rid = ScriptedModel(), "rec_001"
    if kind == "stop-list":
        rid = "rec_002"
    elif kind == "tag hold":
        changes["rec_001"]["tags"] = [{"color": "#888", "id": "tag_hold", "name": "hold"}]
    elif kind == "source gate":
        fake.gate_reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=0.95))
    elif kind == "after extraction":
        fake.reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=0.95))
    else:  # the model quotes the transcript on the first try and on the corrective retry
        model = ScriptedModel(replies={"Priya": adversarial("notes")})
    settings = make_settings(tmp_path, {"route": {"backend": "decider"}}, fixtures=fixtures_with(tmp_path, changes))
    runtime_for(settings, model, clock, router=fake.router()).cycle.run()
    assert ledger(settings)[rid]["status"] == "hold"
    [notice] = [t for t in telegram.texts if t.startswith(f"Held {rid}:")]
    assert notice.endswith(RELEASE) and notice.count(".") == 2  # "Held <id>: <kind>. Tag it ok ... it."
    assert not any("with a secret" in t for t in telegram.texts)


def test_status_lists_holds_by_id_only(tmp_path, clock, services):
    settings = make_settings(tmp_path, {"route": {"backend": "none"}},
                             fixtures=fixtures_with(tmp_path, {"rec_002": {"title": TITLE}}))
    runtime = runtime_for(settings, ScriptedModel(), clock)
    runtime.cycle.run()
    status = format_status(runtime.state)
    assert "• held: rec_002" in status and TITLE not in status


def test_the_demo_reply_to_a_tripwire_hold_does_not_repeat_the_term():
    reply = format_demo_reply(Outcome("hold", reason="stoplist:vargrheim"), "tg-1")
    assert reply == "held · tripwire\nA stop-list term was found. Nothing reached any model."


# --- ruling 14 ---

@pytest.mark.parametrize("text, words", [
    ("", 0),
    ("Speaker 1: Testing.", 1),  # an accidental press
    ("Speaker 1: one two three Speaker 2: four five six seven", 7),  # speaker labels are not words
    ("one two three four five six seven eight", None),  # 8 words: a conversation, however short
])
def test_too_short_counts_words_without_speaker_labels(text, words):
    assert too_short(text) == words and MIN_TRANSCRIPT_WORDS == 8


def test_a_transcript_under_eight_words_is_skipped_before_the_gate_and_marked_seen(tmp_path, clock, services):
    _, telegram, router = services
    fake = attach(router)
    settings = make_settings(tmp_path, {"route": {"backend": "decider"}},
                             fixtures=fixtures_with(tmp_path, {"rec_001": {"transcript": "Speaker 1: Testing."}}))
    model = ScriptedModel()
    runtime = runtime_for(settings, model, clock, router=fake.router())
    runtime.cycle.run()
    assert not any("Testing" in json.dumps(body) for body in fake.all_bodies)  # no read
    assert not any("rec_001" in t for t in telegram.texts)  # no notice
    entry = ledger(settings)["rec_001"]
    assert entry["status"] == "done" and entry["reason"] == "too-short"  # marked seen
    reads = len(fake.all_bodies)
    clock.advance(3600)
    runtime.cycle.run()
    assert len(fake.all_bodies) == reads  # not looked at again (rec_005 was done the first time too)


def test_the_demo_reply_to_a_short_text_says_it_was_skipped():
    assert format_demo_reply(Outcome("skip", reason="too-short"), "tg-1") == (
        "skipped · under 8 words\nNothing was asked or written.")
