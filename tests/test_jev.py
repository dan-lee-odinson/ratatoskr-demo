"""The Jev router (bench only), with the SDK mocked at its HTTP transport (no network).

The fake sits *under* the real typesafe-sdk client (httpx2.MockTransport), so
request building, response parsing and the SDK's own exception classes are
all the real thing. Only TypeSafe's server is simulated.
"""

import json
from dataclasses import replace

import httpx2
import pytest
import typesafe_sdk as ts

from ratatoskr_demo.config import Settings
from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.extract import Contact, Extraction, Task, parse_extraction
from ratatoskr_demo.jev import JevRouter, interpret, make_client, routing_state
from ratatoskr_demo.route import RoutedTask, load_route_questions, route
from tests.conftest import (
    REC_001_TASKS, SENTINEL, ScriptedModel, extraction_json, make_settings, planted_fixtures, runtime_for,
    wrist_messages,
)

QUESTIONS = load_route_questions(Settings())  # the shipped route.toml: every floor is 0.7
EXTRACTION = parse_extraction(extraction_json(tasks=REC_001_TASKS))
TASK, CONTACT = EXTRACTION.tasks[0], EXTRACTION.contact


def answers(project=("research", 0.92), waiting=0.9, deadline=0.2, soon=0.3, noul=0.95, sensitive=0.05):
    """A Jev response body. Any argument may be None to leave that answer out.

    Defaults: project research (sure), someone_waiting yes (sure) -> high -> Todoist 4,
    hard_deadline no, worth_researching_soon no, a real commitment (sure), and confidently
    NOT sensitive.
    """
    out = {}
    if project is not None:
        choice, confidence = project
        out["project"] = {"type": "choice", "choice": choice, "confidence": confidence,
                          "probabilities": {"work": 0.02, "research": 0.9, "personal": 0.03, "other": 0.05}}
    if waiting is not None:
        out["someone_waiting"] = {"type": "noul", "noul": waiting}
    if deadline is not None:
        out["hard_deadline"] = {"type": "noul", "noul": deadline}
    if soon is not None:
        out["worth_researching_soon"] = {"type": "noul", "noul": soon}
    if noul is not None:
        out["real_commitment"] = {"type": "noul", "noul": noul}
    if sensitive is not None:
        out["sensitive"] = {"type": "noul", "noul": sensitive}
    return {"model": "jev-1.13.0", "usage": {"input_tokens": 812, "output_tokens": 60}, "answers": out}


class FakeJev:
    """TypeSafe's server, simulated: records every body, replies as told."""

    def __init__(self, reply=None):
        self.reply = reply if reply is not None else (lambda n: httpx2.Response(200, json=answers()))
        self.bodies: list[dict] = []
        self.models_reply = lambda: httpx2.Response(
            200, json={"models": [{"name": "jev-1.13.0", "description": "d", "release_date": "2026-01-01"}]})

    def handler(self, request):
        if request.url.path.endswith("/models"):
            return self.models_reply()
        self.bodies.append(json.loads(request.content))
        result = self.reply(len(self.bodies))
        if isinstance(result, Exception):
            raise result
        return result

    def client(self):
        return ts.TypeSafeClient(api_key="test-key", transport=httpx2.MockTransport(self.handler),
                                 retry=ts.RetryPolicy(max_retries=0))

    def router(self, questions=QUESTIONS, clock=None):
        kwargs = {"clock": clock} if clock else {}
        return JevRouter(self.client(), questions, **kwargs)


def reply(body_or_status):
    if isinstance(body_or_status, int):
        return lambda n: httpx2.Response(body_or_status, json={"error": "simulated"})
    return lambda n: httpx2.Response(200, json=body_or_status)


# --- the floor, per question ---

def test_each_question_is_routed_above_its_floor():
    routed = FakeJev().router().annotate(TASK, CONTACT)
    assert routed.project == "research"
    assert routed.priority == 4  # score 1.8 -> high -> Todoist 4
    assert routed.real_commitment is True
    assert routed.sensitivity == "clear"  # confidently not sensitive
    assert routed.task is TASK  # R4: the same frozen task, annotated


def test_a_confident_no_is_routed_as_false():
    routed = FakeJev(reply(answers(noul=0.05))).router().annotate(TASK, CONTACT)
    assert routed.real_commitment is False  # confidence max(0.05, 0.95) = 0.95


@pytest.mark.parametrize("field, body", [
    ("project", answers(project=("research", 0.55))),
    ("real_commitment", answers(noul=0.62)),   # confidence 0.62
    ("real_commitment", answers(noul=0.35)),   # confidence 0.65
])
def test_each_question_is_left_none_below_its_floor_and_the_others_still_route(field, body):
    routed = FakeJev(reply(body)).router().annotate(TASK, CONTACT)
    values = {"project": routed.project, "priority": routed.priority,
              "real_commitment": routed.real_commitment, "sensitivity": routed.sensitivity}
    assert values[field] is None
    assert all(v is not None for k, v in values.items() if k != field)


@pytest.mark.parametrize("p, state", [
    (0.92, "sensitive"), (0.60, "sensitive"),     # sure yes (0.60 is exactly the floor)
    (0.59, "unsure"), (0.55, "unsure"), (0.45, "unsure"), (0.41, "unsure"),   # the 0.40-0.60 band
    (0.40, "clear"), (0.05, "clear"),             # sure no
])
def test_the_sensitive_answer_becomes_sensitive_unsure_or_clear(p, state):
    assert FakeJev(reply(answers(sensitive=p))).router().annotate(TASK, CONTACT).sensitivity == state


def test_a_missing_sensitive_answer_means_not_checked():
    routed = FakeJev(reply(answers(sensitive=None))).router().annotate(TASK, CONTACT)
    assert routed.sensitivity is None and routed.project == "research"  # the other answers still apply


def test_typesafe_unreachable_means_not_checked():
    routed = FakeJev(lambda n: httpx2.ConnectError("down")).router().annotate(TASK, CONTACT)
    assert routed.sensitivity is None


def test_the_noul_floor_is_applied_to_max_p_and_1_minus_p():
    parsed = ts.SystemOneResponse.model_validate_json(json.dumps(answers(noul=0.2)))  # as the SDK parses a reply
    judged = interpret(parsed.answers, QUESTIONS)
    assert judged["real_commitment"].confidence == pytest.approx(0.8)
    assert judged["real_commitment"].value is False  # p itself (0.2) is below 0.7; the ruling says 0.8


def test_exactly_at_the_floor_counts_as_sure_enough():
    routed = FakeJev(reply(answers(project=("research", 0.7)))).router().annotate(TASK, CONTACT)
    assert routed.project == "research"


@pytest.mark.parametrize("waiting, deadline, soon, priority, source", [
    (0.9, 0.1, 0.1, 4, "someone_waiting"),          # waiting yes -> high
    (0.9, 0.9, 0.9, 4, "someone_waiting"),          # waiting yes wins
    (0.1, 0.9, 0.1, 4, "hard_deadline"),            # else a near deadline yes -> high
    (0.55, 0.9, 0.1, 4, "hard_deadline"),           # a sure yes decides the high step, waiting unsure
    (0.1, 0.1, 0.9, 3, "worth_researching_soon"),   # else soon yes -> medium
    (0.55, 0.1, 0.9, 3, "default:waiting"),         # the walk stops at the unsure waiting: research's default
    (0.1, 0.1, 0.1, 2, "neither"),                  # all sure no -> low
    (0.1, 0.1, 0.55, 3, "default:soon"),            # soon unsure -> the default
    (0.1, 0.55, 0.1, 3, "default:deadline"),        # deadline unsure -> the default
], ids=["waiting", "all-yes", "deadline", "deadline-waiting-unsure", "soon", "soon-waiting-unsure", "neither",
        "soon-unsure", "deadline-unsure"])
def test_priority_comes_from_the_three_nouls_by_the_rule_and_records_its_source(waiting, deadline, soon, priority,
                                                                                 source):
    routed = FakeJev(reply(answers(waiting=waiting, deadline=deadline, soon=soon))).router().annotate(TASK, CONTACT)
    assert (routed.priority, routed.priority_source) == (priority, source)


def test_each_question_uses_its_own_floor_from_route_toml():
    strict = dict(QUESTIONS)
    strict["project"] = replace(QUESTIONS["project"], min_confidence=0.95)
    routed = FakeJev().router(strict).annotate(TASK, CONTACT)
    assert routed.project is None and routed.priority == 4  # 0.92 < 0.95, while 0.81 >= 0.7


def test_a_choice_outside_the_route_toml_options_is_unusable_and_left_none():
    routed = FakeJev(reply(answers(project=("marketing", 0.99)))).router().annotate(TASK, CONTACT)
    assert routed.project is None and routed.priority == 4


# --- what is sent (R3) ---

def test_the_state_sent_is_the_guarded_task_and_contact_and_nothing_else():
    fake = FakeJev()
    fake.router().annotate(TASK, CONTACT)
    [body] = fake.bodies
    assert set(body) == {"model", "state", "questions"}
    assert body["state"] == routing_state(TASK, CONTACT)
    assert set(body["state"]) == {"task", "contact"}
    assert set(body["state"]["task"]) == {"content", "notes", "due_string"}
    assert set(body["state"]["contact"]) == {"name", "organisation", "role", "context"}
    assert set(body["questions"]) == {"project", "someone_waiting", "hard_deadline", "worth_researching_soon",
                                      "real_commitment", "sensitive"}  # one request
    assert {q["type"] for q in body["questions"].values()} == {"choice", "noul"}


def test_the_key_comes_from_settings_and_the_host_is_pinned(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://elsewhere.example")
    monkeypatch.setenv("TYPESAFE_API_KEY", "key-from-the-shell")
    seen = []
    settings = make_settings(tmp_path, env={"TYPESAFE_API_KEY": "key-from-the-env-file"})

    def handler(request):
        seen.append((request.url.host, request.headers["authorization"]))
        return httpx2.Response(200, json=answers())

    JevRouter(make_client(settings, transport=httpx2.MockTransport(handler)), QUESTIONS).annotate(TASK, CONTACT)
    assert seen == [("api.typesafe.ai", "Bearer key-from-the-env-file")]


def test_without_a_key_the_client_is_a_configuration_error(tmp_path):
    with pytest.raises(ConfigurationError, match="TYPESAFE_API_KEY"):
        make_client(make_settings(tmp_path))


# --- failures never fail the recording ---

@pytest.mark.parametrize("failure", [
    httpx2.ConnectError("dns"), httpx2.ReadTimeout("slow"),
    httpx2.Response(429, json={}), httpx2.Response(503, json={}), httpx2.Response(529, json={}),
], ids=["connection", "timeout", "429", "503", "529"])
def test_a_transient_failure_leaves_the_task_unrouted(failure):
    fake = FakeJev(lambda n: failure)
    router = fake.router()
    routed = router.annotate(TASK, CONTACT)
    assert routed == RoutedTask(task=TASK)
    router.annotate(EXTRACTION.tasks[1], CONTACT)
    assert len(fake.bodies) == 1  # Jev is left alone for a minute; the next task does not wait
    assert router.take_notices() == []  # transient: logged, not announced


def test_after_the_pause_jev_is_tried_again():
    now = {"t": 0.0}
    calls = iter([httpx2.ConnectError("down"), httpx2.Response(200, json=answers())])
    fake = FakeJev(lambda n: next(calls))
    router = fake.router(clock=lambda: now["t"])
    assert not router.annotate(TASK, CONTACT).is_routed
    now["t"] += 61
    assert router.annotate(TASK, CONTACT).is_routed


@pytest.mark.parametrize("body", [{"model": "m", "answers": {}}, {"unexpected": True}])
def test_a_malformed_response_leaves_the_task_unrouted_and_is_announced_once(body):
    router = FakeJev(reply(body)).router()
    assert router.annotate(TASK, CONTACT) == RoutedTask(task=TASK)
    assert len(router.take_notices()) == 1


@pytest.mark.parametrize("status", [400, 404, 422])
def test_jev_rejecting_our_questions_leaves_the_task_unrouted(status):
    router = FakeJev(reply(status)).router()
    assert router.annotate(TASK, CONTACT) == RoutedTask(task=TASK)
    assert router.take_notices()


@pytest.mark.parametrize("status", [401, 403])
def test_a_key_rejected_mid_run_leaves_tasks_unrouted_and_tells_dan(status):
    router = FakeJev(reply(status)).router()
    assert router.annotate(TASK, CONTACT) == RoutedTask(task=TASK)
    assert "rejected" in router.take_notices()[0]


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_at_startup_is_a_configuration_error(status):
    fake = FakeJev()
    fake.models_reply = lambda: httpx2.Response(status, json={"error": "bad key"})
    with pytest.raises(ConfigurationError, match="TYPESAFE_API_KEY"):
        fake.router().check_access()


def test_no_network_at_startup_is_not_a_configuration_error():
    fake = FakeJev()
    fake.models_reply = lambda: (_ for _ in ()).throw(httpx2.ConnectError("no route"))
    fake.router().check_access()  # P4: logged, and routing waits for the network


# --- R3 / R4 through route() ---

def test_route_with_jev_keeps_every_task_text_and_order():
    routed = route(EXTRACTION, make_settings_jev(), FakeJev().router())
    assert [(t.content, t.notes, t.due_string) for t in routed.tasks] == [
        (t.content, t.notes, t.due_string) for t in EXTRACTION.tasks]
    assert all(r.task is t for r, t in zip(routed.tasks, EXTRACTION.tasks))


def test_a_router_that_rewrites_a_task_is_refused():
    class Rogue:
        def annotate(self, task, contact, today=None):
            return RoutedTask(task=Task(content=task.content.upper(), due_string=None, notes=None), project="research")

    with pytest.raises(TypeError):
        route(EXTRACTION, make_settings_jev(), Rogue())


def test_route_with_jev_still_refuses_anything_but_an_extraction():
    with pytest.raises(TypeError):
        route("Speaker 1: raw transcript", make_settings_jev(), FakeJev().router())  # type: ignore[arg-type]


def make_settings_jev():
    return replace(Settings(), route=replace(Settings().route, backend="jev"))


# --- through the whole cycle ---

def jev_settings(tmp_path, **kw):
    return make_settings(tmp_path, {"route": {"backend": "jev"}}, env={"TYPESAFE_API_KEY": "test-key"}, **kw)


def run_cycle_with(tmp_path, clock, fake, **kw):
    from ratatoskr_demo.cli import build_runtime
    import httpx

    settings = jev_settings(tmp_path, **kw)
    runtime = build_runtime(settings, dry_run=False, model_call=ScriptedModel(), http=httpx.Client(),
                            clock=clock, router=fake.router())
    runtime.cycle.run()
    return settings, runtime


def test_routed_annotations_reach_todoist(tmp_path, clock, services):
    todoist, _, _ = services
    fake = FakeJev(lambda n: httpx2.Response(200, json=answers(noul=0.1 if n == 2 else 0.95)))
    settings, runtime = run_cycle_with(tmp_path, clock, fake)
    rec_001 = [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]
    assert [a["labels"] for a in rec_001] == [["pocket", "proj:research"]] * 3
    assert [a["priority"] for a in rec_001] == [4, 4, 4]
    assert rec_001[1]["content"] == "[maybe] " + REC_001_TASKS[1]["content"]  # the writer's one permitted change
    assert rec_001[0]["content"] == REC_001_TASKS[0]["content"]
    ledger = json.loads(settings.ledger_path.read_text())
    assert ledger["rec_001"]["status"] == "done"
    assert runtime.state.counts().routed == 3


def test_a_transient_jev_failure_writes_the_tasks_unrouted_and_the_recording_is_done(tmp_path, clock, services):
    todoist, _, _ = services
    settings, runtime = run_cycle_with(tmp_path, clock, FakeJev(lambda n: httpx2.ConnectError("down")))
    ledger = json.loads(settings.ledger_path.read_text())
    assert ledger["rec_001"]["status"] == "done" and ledger["rec_005"]["status"] == "done"
    rec_001 = [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]
    assert [a["content"] for a in rec_001] == [t["content"] for t in REC_001_TASKS]
    assert all(a["labels"] == ["pocket"] and "priority" not in a for a in rec_001)
    assert runtime.state.counts().routed == 0 and runtime.state.counts().errors == 0


def test_a_mid_run_key_rejection_is_told_to_dan_once_and_writing_goes_on(tmp_path, clock, services):
    todoist, telegram, _ = services
    settings, _ = run_cycle_with(tmp_path, clock, FakeJev(reply(401)))
    assert json.loads(settings.ledger_path.read_text())["rec_001"]["status"] == "done"
    assert sum("TypeSafe rejected" in t for t in telegram.texts) == 1


@pytest.mark.parametrize("mode", ["--once", "--loop"])
def test_an_auth_failure_at_startup_exits_78(tmp_path, monkeypatch, mode):
    from ratatoskr_demo import cli, jev

    config = tmp_path / "config.toml"
    config.write_text('[route]\nbackend = "jev"\n', encoding="utf-8")
    env = {"RATATOSKR_CONFIG": str(config), "RATATOSKR_STATE_DIR": str(tmp_path / "state"),
           "RATATOSKR_FIXTURES": str(planted_fixtures(tmp_path)), "POCKET_API_KEY": "p", "TODOIST_API_TOKEN": "t",
           "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "4242", "BEDROCK_API_KEY": "a", "TYPESAFE_API_KEY": "bad"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    rejected = FakeJev()
    rejected.models_reply = lambda: httpx2.Response(401, json={"error": "invalid key"})
    monkeypatch.setattr(jev, "make_client", lambda settings, **kw: rejected.client())
    monkeypatch.setattr(cli, "_notify_config_error", lambda exc: None)  # no real Telegram call
    monkeypatch.setattr(cli, "run_loop", lambda runtime: pytest.fail("the loop must not start"))
    assert cli.main([mode]) == cli.EX_CONFIG
    assert not (tmp_path / "state" / "processed.json").exists()
    assert rejected.bodies == []  # nothing was routed with a rejected key


def test_the_typesafe_key_is_required_only_for_the_jev_backend(tmp_path):
    from ratatoskr_demo.config import require_secrets

    require_secrets(make_settings(tmp_path))  # backend none: no TypeSafe key needed
    with pytest.raises(ConfigurationError, match="TYPESAFE_API_KEY"):
        require_secrets(make_settings(tmp_path, {"route": {"backend": "jev"}}))
    require_secrets(jev_settings(tmp_path))


# --- T-LEAK, extended to what is sent to Jev ---

def test_t_leak_the_state_sent_to_jev_contains_no_transcript_sentinel(tmp_path, clock, services, caplog):
    todoist, _, _ = services
    caplog.set_level("DEBUG")
    fake = FakeJev()
    model = ScriptedModel()
    from ratatoskr_demo.cli import build_runtime
    import httpx

    settings = jev_settings(tmp_path, fixtures=planted_fixtures(tmp_path))
    build_runtime(settings, dry_run=False, model_call=model, http=httpx.Client(), clock=clock,
                  router=fake.router()).cycle.run()

    assert any(SENTINEL in user for _, user in model.calls)  # the sentinel was in the transcripts
    assert len(fake.bodies) == 3  # rec_001's three tasks; rec_005's "Met ..." task is added after routing
    sent = json.dumps(fake.bodies)
    assert SENTINEL not in sent
    assert "Speaker 1" not in sent and "Space Coast Meetup" not in sent  # no transcript, no title
    assert "rec_00" not in sent  # no recording id either
    for body in fake.bodies:
        assert set(body["state"]) == {"task", "contact"}
    assert SENTINEL not in caplog.text and "Email Priya" not in caplog.text  # state never logged


# --- 21 Sep 2026: three priority levels, project defaults, and `sensitive` ---

def test_a_priority_below_its_floor_uses_the_routed_projects_default():
    # research's default in route.toml is "medium" -> Todoist 3; work's is "high" -> 4; writing's "low" -> 2
    unsure = dict(waiting=0.55, soon=0.45)
    research = FakeJev(reply(answers(project=("research", 0.9), **unsure))).router().annotate(TASK, CONTACT)
    work = FakeJev(reply(answers(project=("work", 0.9), **unsure))).router().annotate(TASK, CONTACT)
    writing = FakeJev(reply(answers(project=("writing", 0.9), **unsure))).router().annotate(TASK, CONTACT)
    assert (research.priority, work.priority, writing.priority) == (3, 4, 2)


def test_a_priority_below_its_floor_stays_blank_when_the_project_is_unrouted_too():
    routed = FakeJev(reply(answers(project=("research", 0.5), waiting=0.55, soon=0.45))).router().annotate(TASK, CONTACT)
    assert routed.project is None and routed.priority is None


def test_an_unusable_priority_answer_also_falls_back_to_the_project_default():
    routed = FakeJev(reply(answers(project=("school", 0.9), waiting=None, soon=None))).router().annotate(TASK, CONTACT)
    assert routed.priority == 4  # school defaults to high


def test_a_confident_priority_wins_over_the_project_default():
    routed = FakeJev(reply(answers(project=("writing", 0.9), waiting=0.95))).router().annotate(TASK, CONTACT)
    assert routed.priority == 4  # Jev said high; writing's default "low" is not used


def test_sensitive_is_only_an_annotation_in_route():
    class SaysSensitive:
        def annotate(self, task, contact, today=None):
            return RoutedTask(task=task, project="research", sensitivity="sensitive")

    routed = route(EXTRACTION, make_settings_jev(), SaysSensitive())  # no hold, no raise: route() only annotates
    assert all(t.sensitivity == "sensitive" for t in routed.tasks)
    from ratatoskr_demo.todoist import build_task_args
    args = build_task_args(routed.tasks[0], recording_id="r", ordinal=1, contact=CONTACT, project_id="p", label="pocket")
    assert "sensitive" not in json.dumps(args)  # the annotation never reaches Todoist


def sensitive_for(marker, p=0.92):
    """Jev replies: sensitive (p) for tasks whose text contains `marker`, else not."""
    def reply_for(request_number, request_body):
        text = json.dumps(request_body["state"])
        return httpx2.Response(200, json=answers(sensitive=p if marker in text else 0.05))
    return reply_for


class BodyAwareJev(FakeJev):
    """A FakeJev whose reply can depend on the state it was sent."""

    def __init__(self, reply_for):
        super().__init__()
        self.reply_for = reply_for
        self.reply = lambda n: self.reply_for(n, self.bodies[-1])


def test_a_sensitive_task_holds_the_recording_and_no_todoist_payload_is_sent(tmp_path, clock, services):
    todoist, telegram, _ = services
    fake = BodyAwareJev(sensitive_for("careers page"))  # one of rec_001's three tasks
    settings, runtime = run_cycle_with(tmp_path, clock, fake)

    entry = json.loads(settings.ledger_path.read_text())["rec_001"]
    assert entry["status"] == "hold" and entry["reason"] == "sensitive" and entry["todoist_task_ids"] == []
    sent = json.dumps(todoist.payloads)
    assert "rec_001" not in sent  # not one Todoist request carried anything of rec_001
    for task in REC_001_TASKS:
        assert task["content"] not in sent
    [notice] = [t for t in telegram.texts if "rec_001" in t]
    assert notice == "Held rec_001: sensitive. Tag it ok in Pocket to release it."  # the id only
    for task in REC_001_TASKS:  # id and title only: no task text in any Telegram message
        assert all(task["content"] not in t for t in telegram.texts)
    assert runtime.state.counts().held == 2 and runtime.state.counts().processed == 1  # + rec_002; rec_005 done


def test_the_ok_tag_releases_a_sensitive_hold_and_it_is_not_checked_again(tmp_path, clock, services):
    todoist, telegram, _ = services
    fake = BodyAwareJev(sensitive_for("careers page"))
    settings, _ = run_cycle_with(tmp_path, clock, fake)
    assert json.loads(settings.ledger_path.read_text())["rec_001"]["reason"] == "sensitive"

    tagged = tagged_fixtures(tmp_path, "rec_001", "ok")
    clock.advance(300)
    run_cycle_with(tmp_path, clock, fake, fixtures=tagged)  # Jev still says sensitive
    entry = json.loads(settings.ledger_path.read_text())["rec_001"]
    assert entry["status"] == "done" and entry["released_by_ok"] is True
    rec_001 = [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]
    assert [a["content"] for a in rec_001] == [t["content"] for t in REC_001_TASKS]
    assert sum("rec_001" in t and "sensitive" in t for t in telegram.texts) == 1  # held once, not again


def test_a_task_confidently_not_sensitive_is_written_without_a_note(tmp_path, clock, services):
    _, telegram, _ = services
    settings, runtime = run_cycle_with(tmp_path, clock, BodyAwareJev(sensitive_for("careers page", p=0.05)))
    entry = json.loads(settings.ledger_path.read_text())["rec_001"]
    assert entry["status"] == "done" and entry["sensitivity_not_checked"] is False
    wrist = wrist_messages(telegram)
    assert wrist and not any("not checked" in t for t in wrist)


def test_a_dry_run_says_it_would_hold_a_sensitive_recording(tmp_path, clock):
    from ratatoskr_demo.cli import build_runtime
    import httpx

    printed = []
    settings = jev_settings(tmp_path)
    runtime = build_runtime(settings, dry_run=True, model_call=ScriptedModel(), http=httpx.Client(),
                            clock=clock, router=BodyAwareJev(sensitive_for("careers page")).router())
    runtime.cycle.deps.out = printed.append
    runtime.cycle.run()
    assert "    would hold: sensitive" in printed
    assert not settings.ledger_path.exists()


def tagged_fixtures(tmp_path, recording_id, tag):
    from tests.conftest import FIXTURES

    target = tmp_path / f"fx-{tag}"
    target.mkdir(exist_ok=True)
    for path in FIXTURES.glob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        for record in body["data"] if isinstance(body["data"], list) else [body["data"]]:
            if record["id"] == recording_id:
                record["tags"] = [{"color": "#888", "id": f"tag_{tag}", "name": tag}]
        (target / path.name).write_text(json.dumps(body), encoding="utf-8")
    return target


# --- Dan's rulings, 21 Sep 2026: unsure holds; unreachable is written and said so ---

@pytest.mark.parametrize("p", [0.55, 0.45], ids=["unsure-yes", "unsure-no"])
def test_an_unsure_sensitivity_holds_the_recording_as_sensitive_unsure(tmp_path, clock, services, p):
    todoist, telegram, _ = services
    settings, runtime = run_cycle_with(tmp_path, clock, BodyAwareJev(sensitive_for("careers page", p=p)))
    entry = json.loads(settings.ledger_path.read_text())["rec_001"]
    assert entry["status"] == "hold" and entry["reason"] == "sensitive-unsure"
    assert "rec_001" not in json.dumps(todoist.payloads)  # nothing of it reached Todoist
    [notice] = [t for t in telegram.texts if "rec_001" in t]
    assert notice == ("Held rec_001: sensitive-unsure. "
                      "Tag it ok in Pocket to release it.")
    for task in REC_001_TASKS:
        assert all(task["content"] not in t for t in telegram.texts)


def test_the_ok_tag_releases_an_unsure_hold_like_a_sensitive_one(tmp_path, clock, services):
    todoist, telegram, _ = services
    fake = BodyAwareJev(sensitive_for("careers page", p=0.5))
    settings, _ = run_cycle_with(tmp_path, clock, fake)
    assert json.loads(settings.ledger_path.read_text())["rec_001"]["reason"] == "sensitive-unsure"
    clock.advance(300)
    run_cycle_with(tmp_path, clock, fake, fixtures=tagged_fixtures(tmp_path, "rec_001", "ok"))
    entry = json.loads(settings.ledger_path.read_text())["rec_001"]
    assert entry["status"] == "done" and entry["released_by_ok"] is True
    assert entry["sensitivity_not_checked"] is False  # Dan reviewed it; no note
    assert len([a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]) == 3


def test_sure_sensitive_wins_over_unsure_in_the_same_recording(tmp_path, clock, services):
    def mixed(n, body):
        text = json.dumps(body["state"])
        p = 0.95 if "careers page" in text else 0.5 if "preprint" in text else 0.05
        return httpx2.Response(200, json=answers(sensitive=p))

    settings, _ = run_cycle_with(tmp_path, clock, BodyAwareJev(mixed))
    assert json.loads(settings.ledger_path.read_text())["rec_001"]["reason"] == "sensitive"


@pytest.mark.parametrize("failure", [httpx2.ConnectError("down"), httpx2.Response(503, json={}),
                                     httpx2.Response(422, json={})], ids=["unreachable", "5xx", "rejected"])
def test_typesafe_unreachable_or_erroring_writes_the_tasks_and_says_sensitivity_not_checked(
        tmp_path, clock, services, failure):
    todoist, telegram, _ = services
    settings, runtime = run_cycle_with(tmp_path, clock, FakeJev(lambda n: failure))
    entry = json.loads(settings.ledger_path.read_text())["rec_001"]
    assert entry["status"] == "done" and entry["sensitivity_not_checked"] is True
    assert len([a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]) == 3
    rec_001 = [t for t in wrist_messages(telegram) if t.splitlines()[1] in {x["content"] for x in REC_001_TASKS}]
    assert len(rec_001) == 3 and all("sens not checked" in t.splitlines()[2] for t in rec_001)


def test_an_unusable_sensitive_answer_alone_also_means_not_checked(tmp_path, clock, services):
    _, telegram, _ = services
    settings, _ = run_cycle_with(tmp_path, clock, FakeJev(reply(answers(sensitive=None))))
    assert json.loads(settings.ledger_path.read_text())["rec_001"]["sensitivity_not_checked"] is True
    assert any("sens not checked" in t.splitlines()[2] for t in wrist_messages(telegram))


def test_with_routing_off_there_is_no_sensitivity_note(tmp_path, clock, services):
    _, telegram, _ = services
    settings = make_settings(tmp_path)  # backend "none"
    runtime_for(settings, ScriptedModel(), clock).cycle.run()
    assert json.loads(settings.ledger_path.read_text())["rec_001"]["sensitivity_not_checked"] is False
    assert not any("not checked" in t for t in telegram.texts)


def test_a_dry_run_says_when_sensitivity_was_not_checked(tmp_path, clock):
    from ratatoskr_demo.cli import build_runtime
    import httpx

    printed = []
    runtime = build_runtime(jev_settings(tmp_path), dry_run=True, model_call=ScriptedModel(), http=httpx.Client(),
                            clock=clock, router=FakeJev(lambda n: httpx2.ConnectError("down")).router())
    runtime.cycle.deps.out = printed.append
    runtime.cycle.run()
    assert "    sensitivity not checked" in printed


@pytest.mark.parametrize("states, verdict", [
    (["clear", "clear"], None),
    (["clear", None], "not-checked"),
    (["unsure", None], "sensitive-unsure"),
    (["unsure", "sensitive", None], "sensitive"),
    ([], None),
])
def test_the_recordings_verdict_is_the_worst_of_its_tasks(states, verdict):
    from ratatoskr_demo.cycle import sensitivity_verdict

    tasks = [RoutedTask(task=TASK, sensitivity=s) for s in states]
    assert sensitivity_verdict(tasks) == verdict
