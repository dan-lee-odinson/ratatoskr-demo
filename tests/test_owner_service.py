"""Dan's ruling 61, P1: the owner service, the companion app's one real connection, inside the courier.

Per route: GET /owner/pending (the token; unsorted tasks with their text only where this process sent it to
Telegram; holds ID only) and POST /owner/pick (ruling 43's resolver: applied once, acknowledged only after the
Todoist update, recorded `project_by: owner, via: app`). The double-apply cases: a pick sent twice from the app, once
from the app after once from Telegram, once from Telegram after once from the app, and two at the same moment, each
leave one change. Loopback only; the app's files are served and nothing outside app/ is.
"""

import json
import logging
import threading
import urllib.error
import urllib.request

import pytest

from ratatoskr_demo import owner_service, resolve
from ratatoskr_demo.errors import TerminalError
from ratatoskr_demo.ledger import LedgerEntry
from tests.test_resolve import OWNER, PROJECTS, RID, SID, FakeTelegram, FakeTodoistLabels, ledger_with_pick, tap

TOKEN = "test-owner-token-0123456789abcdef"


def service(tmp_path, todoist=None):
    state = ledger_with_pick(tmp_path)
    todoist = todoist or FakeTodoistLabels()
    return owner_service.OwnerService(state, todoist, OWNER, PROJECTS, 0.7, TOKEN), state, todoist


class Running:
    """The service on a free loopback port, for the tests that go through HTTP."""

    def __init__(self, svc):
        self.stop, ready = threading.Event(), []
        owner_service.serve(svc, 0, self.stop, on_ready=ready.append)
        self.base = f"http://127.0.0.1:{ready[0]}"

    def call(self, method, path, body=None, token=TOKEN):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()


@pytest.fixture
def running(tmp_path):
    svc, state, todoist = service(tmp_path)
    run = Running(svc)
    yield run, svc, state, todoist
    run.stop.set()


# --- GET /owner/pending ---

def test_pending_needs_the_token(running):
    run, *_ = running
    assert run.call("GET", "/owner/pending", token=None)[0] == 401
    assert run.call("GET", "/owner/pending", token="wrong-token")[0] == 401


def test_pending_lists_the_unsorted_task_with_its_text_only_where_telegram_was_sent_it(tmp_path):
    svc, state, todoist = service(tmp_path)
    run = Running(svc)
    try:
        code, body = run.call("GET", "/owner/pending")
        [task] = json.loads(body)["items"]
        assert code == 200 and (task["kind"], task["pick"], task["ref"], task["text"]) == ("task", SID, f"{RID} #1",
                                                                                              None)
        assert task["project"]["confidence"] == 0.55 and task["project"]["probabilities"] == {}
        assert task["resolved"] is None
        svc.remember(SID, "Email the reviewer the revised figure", {"research": 0.48, "writing": 0.31})
        [task] = json.loads(run.call("GET", "/owner/pending")[1])["items"]
        assert task["text"] == "Email the reviewer the revised figure" and task["project"]["choice"] == "research"
        assert "Email" not in (tmp_path / "processed.json").read_text(encoding="utf-8")  # memory only, never the ledger
    finally:
        run.stop.set()


def test_a_hold_is_listed_id_only(tmp_path):
    svc, state, _ = service(tmp_path)
    state.ledger_put("rec_x", LedgerEntry("hold", "A private title", "2026-10-21T10:00:00Z", "2026-10-21T10:01:00Z",
                                          reason="source-sensitive"))
    hold = next(i for i in svc.pending()["items"] if i["kind"] == "hold")
    assert (hold["ref"], hold["gate"], hold["verdict"], hold["viewer"]) == ("rec_x", "text gate", "sensitive", None)
    assert "A private title" not in json.dumps(hold)  # the title is content: never on the card


# --- POST /owner/pick ---

def test_a_pick_from_the_app_is_applied_once_acknowledged_and_recorded_as_the_owners_via_the_app(running):
    run, svc, state, todoist = running
    code, body = run.call("POST", "/owner/pick", {"sid": SID, "project": "writing"})
    assert code == 200 and json.loads(body)["status"] == "acknowledged"
    [(task_id, labels, uuid)] = todoist.updates
    assert (task_id, labels) == ("t1", ["pocket", "proj:writing"])
    assert uuid == resolve._command_uuid(SID, "writing")  # the same command uuid a Telegram tap would send
    record = state.pick_find(SID)[1]
    assert (record["project_by"], record["project"], record["via"]) == ("owner", "writing", "app")
    code, body = run.call("POST", "/owner/pick", {"sid": SID, "project": "writing"})  # the app sends it again
    assert code == 409 and json.loads(body)["status"] == "already answered" and len(todoist.updates) == 1


def test_a_pick_from_the_app_after_a_telegram_tap_changes_nothing(tmp_path):
    svc, state, todoist = service(tmp_path)
    resolve.Resolver(state, FakeTelegram(), todoist, OWNER, PROJECTS).handle(tap("research"))
    assert svc.pick(SID, "writing") == (409, {"status": "already answered", "project": "research", "by": "owner",
                                              "via": None, "at": state.pick_find(SID)[1]["at"]})
    assert [u[1] for u in todoist.updates] == [["pocket", "proj:research"]]


def test_a_telegram_tap_after_a_pick_from_the_app_changes_nothing(tmp_path):
    svc, state, todoist = service(tmp_path)
    assert svc.pick(SID, "writing")[0] == 200
    telegram = FakeTelegram()
    resolve.Resolver(state, telegram, todoist, OWNER, PROJECTS).handle(tap("research"))
    assert telegram.answers == [("cb-1", "already filed as writing")] and len(todoist.updates) == 1


def test_two_picks_at_the_same_moment_leave_one_change(tmp_path):
    class SlowTodoist(FakeTodoistLabels):
        def update_labels(self, task_id, labels, *, command_uuid):
            threading.Event().wait(0.2)  # a slow Todoist: the second pick arrives while the first is applying
            super().update_labels(task_id, labels, command_uuid=command_uuid)

    svc, state, todoist = service(tmp_path, SlowTodoist())
    results = []
    threads = [threading.Thread(target=lambda k=k: results.append(svc.pick(SID, k))) for k in ("writing", "research")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(todoist.updates) == 1 and sorted(code for code, _ in results) == [200, 409]


def test_todoist_refusing_is_a_failure_with_nothing_recorded_and_a_retry_works(tmp_path):
    svc, state, todoist = service(tmp_path, FakeTodoistLabels(fail=TerminalError("todoist said no")))
    code, body = svc.pick(SID, "writing")
    assert code == 502 and body["status"] == "failed" and "project_by" not in state.pick_find(SID)[1]
    todoist.fail = None
    assert svc.pick(SID, "writing")[0] == 200 and len(todoist.updates) == 1


def test_an_unknown_task_or_project_and_a_bad_body_are_refused(running):
    run, svc, state, todoist = running
    assert run.call("POST", "/owner/pick", {"sid": "0000000000", "project": "writing"})[0] == 404
    assert run.call("POST", "/owner/pick", {"sid": SID, "project": "not-a-project"})[0] == 400
    assert run.call("POST", "/owner/pick", {"sid": SID})[0] == 400
    assert run.call("POST", "/owner/pick", {"sid": SID, "project": "writing"}, token=None)[0] == 401
    assert todoist.updates == []


# --- the server ---

def test_the_service_listens_on_the_loopback_address_only_and_serves_the_app_and_nothing_else(running):
    run, *_ = running
    assert owner_service.HOST == "127.0.0.1" and run.base.startswith("http://127.0.0.1:")
    code, body = run.call("GET", "/", token=None)
    assert code == 200 and b"mobile web prototype" in body
    assert run.call("GET", "/state.js", token=None)[0] == 200
    assert run.call("GET", "/../config.toml", token=None)[0] == 404
    assert run.call("GET", "/%2e%2e/config.toml", token=None)[0] == 404


def test_the_token_never_reaches_the_log(running, caplog):
    run, *_ = running
    with caplog.at_level(logging.DEBUG):
        run.call("POST", "/owner/pick", {"sid": SID, "project": "writing"})
        run.call("GET", "/owner/pending")
    assert TOKEN not in caplog.text


# --- the courier: the flag, the token, the cycle's hook ---

def test_owner_service_goes_with_loop_and_needs_a_usable_token(tmp_path, clock, monkeypatch):
    from ratatoskr_demo import cli
    from ratatoskr_demo.errors import ConfigurationError
    from tests.conftest import ScriptedModel, make_settings, runtime_for

    def never(*args, **kwargs):  # the refusal must come first: no settings, no secrets file, nothing run
        raise AssertionError("got past the argument check")

    monkeypatch.setattr(cli, "load_settings", never)
    with pytest.raises(SystemExit):
        cli.main(["--once", "--owner-service"])
    for env in ({}, {"OWNER_SERVICE_TOKEN": "short"}):
        runtime = runtime_for(make_settings(tmp_path, env=env), ScriptedModel(), clock)
        with pytest.raises(ConfigurationError, match="OWNER_SERVICE_TOKEN"):
            cli.start_owner_service(runtime, threading.Event(), port=0)


def test_the_courier_serves_the_owner_routes_and_hands_them_what_telegram_was_sent(tmp_path, clock, services):
    from ratatoskr_demo import cli
    from tests.conftest import ScriptedModel, make_settings, runtime_for, wrist_messages
    from tests.test_decider import attach
    from tests.test_resolve import unsure_project

    todoist, telegram, router = services
    fake = attach(router)
    fake.reply = unsure_project
    settings = make_settings(tmp_path, {"route": {"backend": "decider"}}, env={"OWNER_SERVICE_TOKEN": TOKEN})
    runtime = runtime_for(settings, ScriptedModel(), clock, router=fake.router())
    stop, ready = threading.Event(), []
    try:
        service = cli.start_owner_service(runtime, stop, port=0, on_ready=ready.append)
        assert runtime.cycle.deps.on_unsorted == service.remember
        runtime.cycle.run()  # unsorted tasks: their Telegram messages go out with buttons, and the service sees them
        keyed = [m["text"].split("\n")[1] for m in telegram.messages if "reply_markup" in m]
        request = urllib.request.Request(f"http://127.0.0.1:{ready[0]}/owner/pending",
                                         headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            tasks = [i for i in json.loads(response.read())["items"] if i["kind"] == "task"]
        assert keyed and sorted(t["text"] for t in tasks) == sorted(keyed)  # the text Telegram was sent, no other
        assert all(t["project"]["probabilities"]["work"] == 0.41 and t["project"]["choice"] == "work" for t in tasks)
        ledger = settings.ledger_path.read_text(encoding="utf-8")
        assert not any(text in ledger for text in keyed)  # held in memory only
    finally:
        stop.set()
