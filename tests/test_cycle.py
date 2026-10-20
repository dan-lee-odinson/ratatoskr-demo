"""End-to-end cycle over the fixtures, every external mocked (§8, C7, C8, P4)."""

import json

import httpx
import pytest
import respx

from ratatoskr_demo.errors import TransientError
from ratatoskr_demo.pocket import BASE_URL
from tests.conftest import (
    FIXTURES, REC_001_TASKS, ScriptedModel, extraction_json, make_settings, runtime_for, wrist_messages,
)


def ledger(settings):
    return json.loads(settings.ledger_path.read_text())


# --- the five named cycle tests ---

def test_a_normal_recording_produces_todoist_tasks_and_a_done_entry(tmp_path, clock, services):
    todoist, telegram, _ = services
    settings = make_settings(tmp_path)
    runtime_for(settings, ScriptedModel(), clock).cycle.run()

    entry = ledger(settings)["rec_001"]
    assert entry["status"] == "done" and entry["scrubbable"] is True
    assert len(entry["todoist_task_ids"]) == 3
    written = [todoist.tasks[i] for i in entry["todoist_task_ids"]]
    assert [a["content"] for a in written] == [t["content"] for t in REC_001_TASKS]
    assert all(a["labels"] == ["pocket"] and a["project_id"] == todoist.projects["Ratatoskr Demo"] for a in written)
    assert written[0]["due"] == {"string": "Friday"}
    wrist = wrist_messages(telegram)
    assert [m.splitlines()[1] for m in wrist][:3] == [t["content"] for t in REC_001_TASKS]  # one per task


def test_a_vargrheim_mention_produces_a_hold_a_telegram_notice_and_no_todoist_call(tmp_path, clock, services):
    todoist, telegram, _ = services
    settings = make_settings(tmp_path)
    model = ScriptedModel()
    runtime_for(settings, model, clock).cycle.run()

    entry = ledger(settings)["rec_002"]
    assert entry["status"] == "hold" and entry["reason"] == "stoplist:vargrheim"
    assert "Held rec_002: stop-list. Tag it ok in Pocket to release it." in telegram.texts  # ruling 13: id only
    assert not any("rec_002" in args["description"] for args in todoist.tasks.values())
    assert not any("Marcus" in user for _, user in model.calls)  # never sent to the model


def test_a_keep_tagged_recording_is_never_processed_and_never_scrubbable(tmp_path, clock, services):
    settings = make_settings(tmp_path)
    model = ScriptedModel()
    runtime = runtime_for(settings, model, clock)
    runtime.cycle.run()
    entry = ledger(settings)["rec_003"]
    assert entry["status"] == "keep" and entry["scrubbable"] is False
    assert not any("course notes" in user or "problem set" in user for _, user in model.calls)
    assert "rec_003" not in [rid for rid, _ in runtime.state.ledger_entries().items()
                             if runtime.state.ledger_entries()[rid].scrubbable]


def test_a_second_run_creates_no_duplicate_tasks(tmp_path, clock, services):
    todoist, _, _ = services
    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, ScriptedModel(), clock)
    runtime.cycle.run()
    count = len(todoist.tasks)
    clock.advance(300)
    runtime.cycle.run()
    runtime_for(settings, ScriptedModel(), clock).cycle.run()  # a restart reads the ledger again
    assert len(todoist.tasks) == count == 4  # three for rec_001, one "Met Lena" for rec_005


def test_a_not_ready_recording_is_left_for_the_next_cycle(tmp_path, clock, services):
    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, ScriptedModel(), clock)
    runtime.cycle.run()
    assert "rec_004" not in ledger(settings)
    assert runtime.state.counts().not_ready == 1


def test_a_conversation_without_commitments_writes_one_met_task(tmp_path, clock, services):
    todoist, _, _ = services
    settings = make_settings(tmp_path)
    runtime_for(settings, ScriptedModel(), clock).cycle.run()
    [task_id] = ledger(settings)["rec_005"]["todoist_task_ids"]
    assert todoist.tasks[task_id]["content"] == "Met Lena — no follow-ups"


def test_a_keep_tag_added_after_processing_takes_it_off_the_scrub_list(tmp_path, clock, services):
    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, ScriptedModel(), clock)
    runtime.cycle.run()
    assert ledger(settings)["rec_001"]["scrubbable"] is True

    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    for path in FIXTURES.glob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        for record in body["data"] if isinstance(body["data"], list) else [body["data"]]:
            if record["id"] == "rec_001":
                record["tags"] = [{"color": "#888", "id": "t", "name": "keep"}]
        (fixtures / path.name).write_text(json.dumps(body), encoding="utf-8")
    runtime_for(make_settings(tmp_path, fixtures=fixtures), ScriptedModel(), clock).cycle.run()
    assert ledger(settings)["rec_001"]["scrubbable"] is False


# --- C7: transient failures never touch the ledger ---

def _failing_model(exc):
    def model(system, user):
        raise exc
    return model


TRANSIENT_CLASSES = {
    "dns failure": httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
    "no route": httpx.ConnectError("[Errno 113] No route to host"),
    "timeout": httpx.ReadTimeout("timed out"),
    "http 429": httpx.Response(429),
    "http 503": httpx.Response(503),
}


@pytest.mark.parametrize("name", TRANSIENT_CLASSES)
def test_a_todoist_transient_failure_leaves_the_ledger_untouched(tmp_path, clock, services, name):
    todoist, _, _ = services
    todoist.fail = lambda command: TRANSIENT_CLASSES[name] if command["type"] == "item_add" else None
    settings = make_settings(tmp_path)
    runtime_for(settings, ScriptedModel(), clock).cycle.run()
    entries = ledger(settings)
    assert "rec_001" not in entries and "rec_005" not in entries
    assert not any(e["status"] == "error" for e in entries.values())


@pytest.mark.parametrize("name", TRANSIENT_CLASSES)
def test_a_telegram_transient_failure_leaves_the_ledger_untouched(tmp_path, clock, services, name):
    _, telegram, _ = services
    telegram.fail = lambda payload: TRANSIENT_CLASSES[name] if "Held rec_002" in payload["text"] else None
    settings = make_settings(tmp_path)
    runtime_for(settings, ScriptedModel(), clock).cycle.run()
    assert "rec_002" not in ledger(settings)  # the hold is re-decided and re-announced next poll


@pytest.mark.parametrize("kind", ["timeout", "network", "rate-limit", "server-error"])
def test_a_bedrock_transient_failure_leaves_the_ledger_untouched(tmp_path, clock, services, kind):
    settings = make_settings(tmp_path)
    runtime_for(settings, _failing_model(TransientError(f"bedrock: {kind}", kind=kind)), clock).cycle.run()
    entries = ledger(settings)
    assert "rec_001" not in entries and "rec_005" not in entries


def test_a_transiently_failing_recording_is_retried_with_growing_backoff(tmp_path, clock, services):
    settings = make_settings(tmp_path)
    model = ScriptedModel()
    broken = {"on": True}

    def flaky(system, user):
        if broken["on"]:
            raise TransientError("bedrock: timeout", kind="timeout")
        return model(system, user)

    runtime = runtime_for(settings, flaky, clock)
    runtime.cycle.run()
    retry = runtime.cycle.deps.retry
    assert retry.attempts("rec_001") == 1
    clock.advance(100)
    runtime.cycle.run()  # too soon: skipped, not retried
    assert retry.attempts("rec_001") == 1
    clock.advance(300)
    runtime.cycle.run()
    assert retry.attempts("rec_001") == 2
    broken["on"] = False
    clock.advance(600)
    runtime.cycle.run()
    assert ledger(settings)["rec_001"]["status"] == "done"
    assert retry.attempts("rec_001") == 0


def test_a_recording_failing_transiently_for_a_day_gets_one_notice(tmp_path, clock, services):
    _, telegram, _ = services
    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, _failing_model(TransientError("bedrock: timeout", kind="timeout")), clock)
    for _ in range(40):
        runtime.cycle.run()
        clock.advance(3600)
    stuck = [t for t in telegram.texts if "failing to process for over 24 hours" in t and "rec_001" in t]
    assert len(stuck) == 1


def test_the_backoff_is_capped_at_one_hour(tmp_path, clock, services):
    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, _failing_model(TransientError("x", kind="timeout")), clock)
    assert runtime.cycle.deps.retry.delay_for(20) == 3600


# --- C7: a terminal failure is written once and never retried ---

def test_a_terminal_failure_is_written_once_and_never_retried(tmp_path, clock, services):
    _, telegram, _ = services
    calls = []

    def bad_model(system, user):
        calls.append(user)
        return json.dumps({"contact": {}, "tasks": "nope", "confidence": "high"})

    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, bad_model, clock)
    runtime.cycle.run()
    first = len(calls)
    assert first == 4  # rec_001 and rec_005, each tried twice (one schema retry)
    entries = ledger(settings)
    assert entries["rec_001"]["status"] == "error"
    errors = [t for t in telegram.texts if t.startswith("Error on recording rec_001")]
    assert len(errors) == 1 and "keys:" in errors[0]

    clock.advance(3600)
    runtime.cycle.run()
    assert len(calls) == first  # never retried
    assert len([t for t in telegram.texts if t.startswith("Error on recording rec_001")]) == 1


def test_an_unrecognised_transcript_shape_is_terminal_and_reports_keys_only(tmp_path, clock, services):
    _, telegram, _ = services
    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    for path in FIXTURES.glob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        for record in body["data"] if isinstance(body["data"], list) else [body["data"]]:
            if record["id"] == "rec_005":
                record["transcript"] = {"utterances": "Lena said something private"}
        (fixtures / path.name).write_text(json.dumps(body), encoding="utf-8")
    settings = make_settings(tmp_path, fixtures=fixtures)
    runtime_for(settings, ScriptedModel(), clock).cycle.run()
    assert ledger(settings)["rec_005"]["status"] == "error"
    [notice] = [t for t in telegram.texts if "rec_005" in t]
    assert "utterances" in notice and "private" not in notice


def test_a_configuration_problem_stops_the_cycle_and_touches_nothing(tmp_path, clock, services):
    todoist, telegram, _ = services
    todoist.fail = lambda command: httpx.Response(401)
    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, ScriptedModel(), clock)
    result = runtime.cycle.run_job()
    assert result.startswith("error")
    entries = ledger(settings) if settings.ledger_path.exists() else {}
    assert not any(e["status"] in ("done", "error") for e in entries.values())
    assert sum("needs attention" in t for t in telegram.texts) == 1
    runtime.cycle.run_job()
    assert sum("needs attention" in t for t in telegram.texts) == 1  # said once, not every poll


# --- C8: partial Todoist failure, then a re-run: exactly three tasks ---

def test_a_partial_todoist_failure_yields_exactly_three_tasks_on_re_run(tmp_path, clock, services):
    todoist, _, _ = services
    accepted = {"count": 0}

    def two_then_fail(command):
        if command["type"] != "item_add" or "rec_001" not in command["args"]["description"]:
            return None
        if command["uuid"] in todoist.executed:
            return None
        if accepted["count"] >= 2:
            return httpx.ConnectError("network dropped")
        accepted["count"] += 1
        return None

    todoist.fail = two_then_fail
    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, ScriptedModel(), clock)
    runtime.cycle.run()
    rec_001 = lambda: [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]
    assert len(rec_001()) == 2
    assert "rec_001" not in ledger(settings)  # the ledger write never happened

    todoist.fail = lambda command: None  # network back
    clock.advance(3600)
    runtime.cycle.run()
    assert len(rec_001()) == 3
    assert ledger(settings)["rec_001"]["status"] == "done"


# --- P4: no network at boot ---

def test_the_cycle_survives_no_network_at_boot_and_completes_when_it_returns(tmp_path, clock, services, caplog):
    _, _, router = services
    listing = json.loads((FIXTURES / "list_recordings_page1.json").read_text(encoding="utf-8"))
    down = {"calls": 0}

    def pocket_list(request):
        down["calls"] += 1
        if down["calls"] <= 3:
            raise httpx.ConnectError("Network is unreachable")
        return httpx.Response(200, json=listing)

    def pocket_detail(request):
        rid = request.url.path.rsplit("/", 1)[-1]
        for path in FIXTURES.glob("rec_*.json"):
            body = json.loads(path.read_text(encoding="utf-8"))
            if body["data"]["id"] == rid:
                return httpx.Response(200, json=body)
        return httpx.Response(404)

    router.get(f"{BASE_URL}/public/recordings").mock(side_effect=pocket_list)
    router.get(url__regex=rf"{BASE_URL}/public/recordings/rec_\d+").mock(side_effect=pocket_detail)

    settings = make_settings(tmp_path, fixtures=None)
    runtime = runtime_for(settings, ScriptedModel(), clock)
    caplog.set_level("INFO")
    for _ in range(12):  # an hour of polls: backoff 300, 600, 1200 ...
        runtime.cycle.run_job()
        clock.advance(300)
    assert 4 <= down["calls"] < 12  # recovered, and backoff skipped some polls while down
    assert "network up" in caplog.text
    assert ledger(settings)["rec_001"]["status"] == "done"


def test_a_rejected_done_notice_never_turns_a_done_recording_into_an_error(tmp_path, clock, services):
    _, telegram, _ = services
    telegram.fail = lambda payload: httpx.Response(400) if "commit" in payload["text"] else None
    settings = make_settings(tmp_path)
    runtime_for(settings, ScriptedModel(), clock).cycle.run()
    assert ledger(settings)["rec_001"]["status"] == "done"


def test_finished_recordings_are_skipped_without_fetching_them_again(tmp_path, clock, services):
    """done / keep / error come from the ledger alone: no detail call to Pocket each poll."""
    calls = []

    def bad_for_rec_001(system, user):
        return "not json" if "Priya" in user else ScriptedModel()(system, user)

    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, bad_for_rec_001, clock)
    runtime.cycle.run()
    assert {e["status"] for e in ledger(settings).values()} >= {"done", "keep", "error", "hold"}

    pocket = runtime.cycle.deps.inputs["pocket"].client  # the cycle reaches Pocket only through its adapter
    real_get = pocket.get_recording
    pocket.get_recording = lambda rid: calls.append(rid) or real_get(rid)
    clock.advance(300)
    runtime.cycle.run()
    assert sorted(calls) == ["rec_002", "rec_004"]  # only the held one (ok-tag check) and the not-ready one


def test_a_lost_network_waits_at_least_a_minute_before_the_recording_is_retried(tmp_path, clock, services):
    from ratatoskr_demo.bedrock import NetworkLost

    settings = make_settings(tmp_path, {"poll": {"interval_seconds": 30}})
    runtime = runtime_for(settings, _failing_model(NetworkLost("bedrock: network lost (ConnectError)")), clock)
    runtime.cycle.run()
    record = runtime.cycle.deps.retry._records["rec_001"]
    assert record.next_attempt_at - clock() >= 60  # not the 30 s a poll interval would give
    assert "rec_001" not in ledger(settings)


def test_the_spend_hard_stop_stops_the_cycle_touches_no_recording_and_tells_dan_once(tmp_path, clock, services):
    from ratatoskr_demo.spend import SpendStop

    _, telegram, _ = services
    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, _failing_model(SpendStop("Bedrock spend hard stop: nothing was sent")), clock)
    result = runtime.cycle.run_job()
    assert result.startswith("error") and runtime.cycle.stopped
    entries = ledger(settings) if settings.ledger_path.exists() else {}
    assert not any(e["status"] in ("done", "error") for e in entries.values())
    clock.advance(300)
    assert runtime.cycle.run_job().startswith("stopped")
    assert sum("hard stop" in t for t in telegram.texts) == 1
