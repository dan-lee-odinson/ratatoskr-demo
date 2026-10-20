"""Dan's ruling 9 (18 Oct 2026): with Strands Decider missing, or a read timed out, nothing is written.

At the gate the recording waits (the `urgent` bypass unchanged); at routing it waits too: no Todoist
write, no unrouted task, no "sens not checked" line. Each cycle asks /health before its first read; a
read that failed is never asked again, and no read follows it in the same cycle. After three cycles in
a row without Decider, one notice with a count only.
"""

import json

import httpx
import pytest

from ratatoskr_demo.cycle import DECIDER_DOWN_CYCLES_BEFORE_NOTICE
from tests.conftest import ScriptedModel, make_settings, runtime_for, wrist_messages
from tests.test_decider import attach, decider_answers

WAITING = ("rec_001", "rec_005")  # the fixture recordings that reach Decider (rec_002 trips, 003 keep, 004 not ready)


@pytest.fixture
def fake(services):
    return attach(services[2])


def setup(tmp_path, clock, fake, backend="decider"):
    settings = make_settings(tmp_path, {"route": {"backend": backend}})
    model = ScriptedModel()
    return settings, runtime_for(settings, model, clock, router=fake.router() if backend == "decider" else None), model


def ledger(settings):
    return json.loads(settings.ledger_path.read_text()) if settings.ledger_path.exists() else {}


def nothing_written(services, settings):
    todoist, telegram, _ = services
    return (todoist.tasks == {} and wrist_messages(telegram) == []
            and not any("not checked" in t for t in telegram.texts)
            and not any(rid in ledger(settings) for rid in WAITING))


def test_no_answer_from_health_means_no_read_no_extraction_and_nothing_written(tmp_path, clock, services, fake):
    fake.health_up = False
    settings, runtime, model = setup(tmp_path, clock, fake)
    runtime.cycle.run()
    assert fake.all_bodies == [] and model.calls == []  # not one read, not one Bedrock call
    assert nothing_written(services, settings)
    assert fake.health_calls == 1  # asked once, for the whole cycle
    assert all(runtime.cycle.deps.retry.attempts(rid) == 1 for rid in WAITING)  # waiting, retried later


def test_a_timed_out_routing_read_writes_nothing_and_no_read_follows_in_that_cycle(tmp_path, clock, services, fake):
    fake.reply = lambda body: httpx.ReadTimeout("slow")
    settings, runtime, model = setup(tmp_path, clock, fake)
    runtime.cycle.run()
    assert len(fake.gate_bodies) == 1 and len(fake.bodies) == 1  # rec_001's gate and first task; then nothing
    assert nothing_written(services, settings)


def test_a_failed_gate_read_stops_the_cycles_reads_and_nothing_is_extracted(tmp_path, clock, services, fake):
    fake.gate_reply = lambda body: httpx.ConnectError("refused")
    settings, runtime, model = setup(tmp_path, clock, fake)
    runtime.cycle.run()
    assert len(fake.gate_bodies) == 1 and fake.bodies == [] and model.calls == []
    assert nothing_written(services, settings)


def test_the_next_cycle_asks_health_before_any_read_and_goes_on_when_decider_is_back(tmp_path, clock, services, fake):
    fake.reply = lambda body: httpx.ReadTimeout("slow")
    settings, runtime, model = setup(tmp_path, clock, fake)
    runtime.cycle.run()
    first = len(fake.order)
    fake.reply = lambda body: httpx.Response(200, json=decider_answers())
    clock.advance(3600)
    runtime.cycle.run()
    assert fake.order[first] == "health"  # before the cycle's first read
    assert ledger(settings)["rec_001"]["status"] == "done"
    assert services[0].tasks  # written now that Decider answers


def test_three_cycles_in_a_row_without_decider_send_one_count_only_notice(tmp_path, clock, services, fake):
    _, telegram, _ = services
    fake.health_up = False
    settings, runtime, model = setup(tmp_path, clock, fake)
    for _ in range(DECIDER_DOWN_CYCLES_BEFORE_NOTICE + 2):
        runtime.cycle.run()
        clock.advance(3600)
    notices = [t for t in telegram.texts if "waiting on Decider" in t]
    assert notices == ["2 items waiting on Decider"]  # one notice, a count only: no text, no title, no id
    assert DECIDER_DOWN_CYCLES_BEFORE_NOTICE == 3


def test_two_cycles_without_decider_send_no_notice_and_decider_back_resets_the_count(tmp_path, clock, services, fake):
    _, telegram, _ = services
    fake.health_up = False
    settings, runtime, model = setup(tmp_path, clock, fake)
    for _ in range(DECIDER_DOWN_CYCLES_BEFORE_NOTICE - 1):
        runtime.cycle.run()
        clock.advance(3600)
    assert not any("waiting on Decider" in t for t in telegram.texts)
    fake.health_up = True
    runtime.cycle.run()
    assert runtime.cycle._decider_down_cycles == 0 and ledger(settings)["rec_001"]["status"] == "done"


def test_with_route_backend_none_there_is_no_health_check_and_writing_is_unchanged(tmp_path, clock, services, fake):
    fake.health_up = False
    settings, runtime, model = setup(tmp_path, clock, fake, backend="none")
    runtime.cycle.run()
    assert fake.health_calls == 0 and ledger(settings)["rec_001"]["status"] == "done"
