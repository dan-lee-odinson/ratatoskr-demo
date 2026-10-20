"""Dan's ruling 23 (19 Oct 2026): with backend "decider", nothing is written with its sensitivity not checked.

A reply Decider gave that cannot be used (an HTTP error, a non-JSON body, no answers) makes the recording
wait at routing, exactly as the same reply makes it wait at the source gate, urgent or not. A malformed
`sensitive` answer counts as unsure: the recording is held, with an id-only notice. The urgent exception
(ruling 12, Decider unreachable) is untouched; tests/test_urgent.py covers it.
"""

import json

import httpx
import pytest

from tests.conftest import REC_001_TASKS, ScriptedModel, make_settings, runtime_for, wrist_messages
from tests.test_decider import attach, decider_answers, status
from tests.test_jev import tagged_fixtures

CONTENTS = {t["content"] for t in REC_001_TASKS}  # rec_001 is the fixture recording whose tasks reach routing
UNUSABLE =[status(422), status(400), lambda body: httpx.Response(200, json={"answers": "x"}),
            lambda body: httpx.Response(200, content=b"not json")]
UNUSABLE_IDS = ["422", "400", "no-answers", "not-json"]


@pytest.fixture
def fake(services):
    return attach(services[2])


def run(tmp_path, clock, fake, fixtures=None):
    settings = make_settings(tmp_path, {"route": {"backend": "decider"}}, **({"fixtures": fixtures} if fixtures else {}))
    runtime = runtime_for(settings, ScriptedModel(), clock, router=fake.router())
    runtime.cycle.run()
    return settings, runtime


def ledger(settings):
    return json.loads(settings.ledger_path.read_text()) if settings.ledger_path.exists() else {}


def nothing_written_for_rec_001(services, settings):
    todoist, telegram, _ = services
    return (not [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]
            and not any(t.splitlines()[1] in CONTENTS for t in wrist_messages(telegram))
            and "rec_001" not in ledger(settings))


@pytest.mark.parametrize("reply", UNUSABLE, ids=UNUSABLE_IDS)
def test_an_unusable_routing_reply_makes_the_recording_wait_with_nothing_written(tmp_path, clock, services, fake,
                                                                               reply):
    _, telegram, _ = services
    fake.reply = reply
    settings, runtime = run(tmp_path, clock, fake)
    assert nothing_written_for_rec_001(services, settings)
    assert not any("not checked" in t or "unrouted" in t for t in telegram.texts)  # no task, no announcement
    assert runtime.cycle.deps.retry.attempts("rec_001") == 1  # waiting, retried later
    assert runtime.cycle._waiting_on_decider == {"rec_001"}
    # its first task's read only; and, as at the gate, Decider answered, so it is not marked down
    assert len(fake.bodies) == 1 and runtime.cycle._decider_up is True


@pytest.mark.parametrize("reply", UNUSABLE, ids=UNUSABLE_IDS)
def test_an_unusable_routing_reply_makes_an_urgent_recording_wait_too(tmp_path, clock, services, fake, reply):
    """Ruling 12's urgent tag is for Decider unreachable, and an unusable reply is not that."""
    fake.reply = reply
    settings, runtime = run(tmp_path, clock, fake, fixtures=tagged_fixtures(tmp_path, "rec_001", "urgent"))
    assert nothing_written_for_rec_001(services, settings)
    assert not any(t.splitlines()[0] == "urgent · unrouted" for t in wrist_messages(services[1]))


def test_the_recording_is_written_once_decider_answers_usably_again(tmp_path, clock, services, fake):
    todoist, telegram, _ = services
    fake.reply = status(422)
    settings, runtime = run(tmp_path, clock, fake)
    fake.reply = lambda body: httpx.Response(200, json=decider_answers())
    clock.advance(3600)
    runtime.cycle.run()
    assert ledger(settings)["rec_001"]["status"] == "done"
    assert len([a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]) == 3
    assert not any("not checked" in t for t in telegram.texts)


@pytest.mark.parametrize("p", [0.45, 0.55])
def test_a_sensitive_answer_below_its_floor_at_routing_holds_the_recording(tmp_path, clock, services, fake, p):
    """Ruling 28's claim 2, at routing with Decider: unsure (p between 0.40 and 0.60) holds, never passes."""
    todoist, telegram, _ = services
    fake.reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=p))
    settings, runtime = run(tmp_path, clock, fake)
    entry = ledger(settings)["rec_001"]
    assert entry["status"] == "hold" and entry["reason"] == "sensitive-unsure"
    assert "Held rec_001: sensitive-unsure. Tag it ok in Pocket to release it." in telegram.texts
    assert not [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]


@pytest.mark.parametrize("sensitive", [None, {"type": "noul", "noul": "high"}], ids=["missing", "not-a-number"])
def test_a_malformed_sensitive_answer_holds_the_recording_with_an_id_only_notice(tmp_path, clock, services, fake,
                                                                               sensitive):
    todoist, telegram, _ = services

    def reply(body):
        payload = decider_answers()
        if sensitive is None:
            payload["answers"].pop("sensitive")
        else:
            payload["answers"]["sensitive"] = sensitive
        return httpx.Response(200, json=payload)

    fake.reply = reply
    settings, runtime = run(tmp_path, clock, fake)
    entry = ledger(settings)["rec_001"]
    assert entry["status"] == "hold" and entry["reason"] == "sensitive-unsure"
    assert "Held rec_001: sensitive-unsure. Tag it ok in Pocket to release it." in telegram.texts
    assert not [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]
    assert not any("not checked" in t for t in telegram.texts)
