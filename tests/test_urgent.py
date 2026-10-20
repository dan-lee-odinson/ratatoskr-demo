"""Dan's ruling 12 (18 Oct 2026): the urgent tag, bounded to Strands Decider being unreachable.

Only when Decider is unreachable this cycle (no answer from /health, or a read that failed or timed
out), a recording tagged urgent skips the source gate, is extracted, and its tasks are written to the
project with priority high and the label "unrouted"; the wrist reads "urgent · unrouted". The tripwire
still runs first. A verdict Decider gave is never bypassed: with Decider up, urgent changes nothing.
"""

import json

import httpx
import pytest

from ratatoskr_demo.extract import Task
from ratatoskr_demo.route import urgent_unrouted
from ratatoskr_demo.todoist import build_task_args
from tests.conftest import REC_001_TASKS, ScriptedModel, make_settings, runtime_for, wrist_messages
from tests.test_decider import CONTACT, attach, decider_answers
from tests.test_jev import tagged_fixtures

CONTENTS = {t["content"] for t in REC_001_TASKS}


@pytest.fixture
def fake(services):
    return attach(services[2])


def run(tmp_path, clock, fake, tagged="rec_001"):
    settings = make_settings(tmp_path, {"route": {"backend": "decider"}},
                             fixtures=tagged_fixtures(tmp_path, tagged, "urgent"))
    model = ScriptedModel()
    runtime = runtime_for(settings, model, clock, router=fake.router())
    runtime.cycle.run()
    return settings, runtime, model


def ledger(settings):
    return json.loads(settings.ledger_path.read_text()) if settings.ledger_path.exists() else {}


def rec_001(todoist):
    return [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]


def test_an_urgent_task_is_written_with_priority_high_and_the_unrouted_label():
    args = build_task_args(urgent_unrouted(Task(content="Send the deck", due_string=None, notes=None)),
                           recording_id="r", ordinal=1, contact=CONTACT, project_id="p", label="pocket")
    assert args["labels"] == ["pocket", "unrouted"] and args["priority"] == 4 and args["project_id"] == "p"


def test_decider_unreachable_an_urgent_recording_is_extracted_and_written_unrouted(tmp_path, clock, services, fake):
    todoist, telegram, _ = services
    fake.health_up = False
    settings, runtime, model = run(tmp_path, clock, fake)
    assert fake.all_bodies == []  # Decider unreachable: not one read
    written = rec_001(todoist)
    assert len(written) == 3 and all(a["labels"] == ["pocket", "unrouted"] and a["priority"] == 4 for a in written)
    wrist = [t for t in wrist_messages(telegram) if t.splitlines()[1] in CONTENTS]
    assert len(wrist) == 3 and all(t.splitlines()[0] == "urgent · unrouted" for t in wrist)
    assert all(t.splitlines()[2].endswith("prio urgent") for t in wrist)
    assert ledger(settings)["rec_001"]["status"] == "done"
    assert "rec_005" not in ledger(settings)  # not urgent: it waits (ruling 9)


def test_a_gate_read_timeout_lets_an_urgent_recording_past_and_no_routing_read_follows(tmp_path, clock, services,
                                                                                       fake):
    todoist, _, _ = services
    fake.gate_reply = lambda body: httpx.ReadTimeout("slow")
    settings, runtime, model = run(tmp_path, clock, fake)
    assert len(fake.gate_bodies) == 1 and fake.bodies == []  # a read timed out: none sent again this cycle
    assert all(a["labels"] == ["pocket", "unrouted"] for a in rec_001(todoist)) and len(rec_001(todoist)) == 3


def test_decider_unreachable_mid_recording_the_verdicts_given_stand_and_the_rest_is_written_unrouted(
        tmp_path, clock, services, fake):
    todoist, _, _ = services
    replies = iter([httpx.Response(200, json=decider_answers())])
    fake.reply = lambda body: next(replies, httpx.ReadTimeout("slow"))
    settings, runtime, model = run(tmp_path, clock, fake)
    written = rec_001(todoist)
    assert written[0]["labels"] == ["pocket", "proj:research"]  # Decider's answer for task 1 stands
    assert all(a["labels"] == ["pocket", "unrouted"] and a["priority"] == 4 for a in written[1:])
    assert len(fake.bodies) == 2  # task 2's read timed out; task 3 was not asked


def test_decider_unreachable_mid_recording_a_sensitive_verdict_already_given_still_holds(tmp_path, clock, services,
                                                                                         fake):
    todoist, _, _ = services
    replies = iter([httpx.Response(200, json=decider_answers(sensitive=0.95))])
    fake.reply = lambda body: next(replies, httpx.ReadTimeout("slow"))
    settings, runtime, model = run(tmp_path, clock, fake)
    assert ledger(settings)["rec_001"]["status"] == "hold" and rec_001(todoist) == []


def test_decider_up_urgent_changes_nothing(tmp_path, clock, services, fake):
    todoist, telegram, _ = services
    settings, runtime, model = run(tmp_path, clock, fake)
    written = rec_001(todoist)
    assert len(written) == 3 and all(a["labels"] == ["pocket", "proj:research"] for a in written)
    assert not any(t.splitlines()[0] == "urgent · unrouted" for t in wrist_messages(telegram))
    assert len(fake.bodies) == 3 and fake.gate_bodies  # every read asked as for any other recording


def test_decider_up_a_hold_verdict_is_never_bypassed_by_urgent(tmp_path, clock, services, fake):
    todoist, _, _ = services
    fake.gate_reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=0.95))
    settings, runtime, model = run(tmp_path, clock, fake)
    assert ledger(settings)["rec_001"]["status"] == "hold" and rec_001(todoist) == [] and model.calls == []


def test_an_unusable_gate_reply_is_not_unreachable_so_an_urgent_recording_waits(tmp_path, clock, services, fake):
    todoist, _, _ = services
    fake.gate_reply = lambda body: httpx.Response(422, json={"detail": "simulated"})
    settings, runtime, model = run(tmp_path, clock, fake)
    assert "rec_001" not in ledger(settings) and rec_001(todoist) == [] and model.calls == []


def test_the_tripwire_still_runs_first_for_an_urgent_recording(tmp_path, clock, services, fake):
    fake.health_up = False
    settings, runtime, model = run(tmp_path, clock, fake, tagged="rec_002")
    assert ledger(settings)["rec_002"]["reason"] == "stoplist:vargrheim"
    assert not any("vargrheim" in user.lower() for _, user in model.calls)
