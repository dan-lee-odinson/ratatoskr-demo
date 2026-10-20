"""The Strands Decider route backend, with the local server mocked (respx on 127.0.0.1:8000)."""

import json

import httpx
import pytest
import respx

from ratatoskr_demo.config import Settings
from ratatoskr_demo.decider import DeciderUnavailable, DeciderClient, DeciderRouter, build_questions, request_body
from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.extract import parse_extraction
from ratatoskr_demo.route import RoutedTask, RouterRejected, load_route_questions, routing_state
from tests.conftest import (
    REC_001_TASKS, SENTINEL, ScriptedModel, extraction_json, make_settings, planted_fixtures, runtime_for,
    wrist_messages,
)

URL = "http://127.0.0.1:8000"
QUESTIONS = load_route_questions(Settings())
EXTRACTION = parse_extraction(extraction_json(tasks=REC_001_TASKS))
TASK, CONTACT = EXTRACTION.tasks[0], EXTRACTION.contact


def decider_answers(project=("research", 0.91), waiting=0.88, deadline=0.30, soon=0.2, commit=0.94, sensitive=0.06):
    out = {}
    if project is not None:
        out["project"] = {"type": "choice", "choice": project[0], "confidence": project[1],
                          "probabilities": {"research": 0.91, "work": 0.05, "other": 0.04}}
    if waiting is not None:
        out["someone_waiting"] = {"type": "noul", "noul": waiting}
    if deadline is not None:
        out["hard_deadline"] = {"type": "noul", "noul": deadline}
    if soon is not None:
        out["worth_researching_soon"] = {"type": "noul", "noul": soon}
    if commit is not None:
        out["real_commitment"] = {"type": "noul", "noul": commit}
    if sensitive is not None:
        out["sensitive"] = {"type": "noul", "noul": sensitive}
    return {"model": "strands-decider-2B-hobson-v21", "answers": out,
            "usage": {"input_tokens": 640, "output_tokens": 4}, "latency_ms": 1480.5}


class FakeDecider:
    """`strands-decider serve`, simulated: records every body, replies as told."""

    def __init__(self, reply=None):
        self.reply = reply or (lambda body: httpx.Response(200, json=decider_answers()))
        self.bodies: list[dict] = []  # routing requests
        self.gate_bodies: list[dict] = []  # source-text gate requests
        self.hosts: list[str] = []
        self.all_bodies: list[dict] = []
        self.gate_reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=0.05))
        self.health = {"status": "ok", "model": "strands-decider-2B-hobson-v21",
                       "checkpoint": "StrandsAgents/strands-decider-2B-hobson-v21", "device": "cpu"}
        self.health_up = True  # False: /health refuses the connection (ruling 9's tests)
        self.health_calls = 0
        self.order: list[str] = []  # "health" / "read", in arrival order

    def systemone(self, request):
        self.order.append("read")
        body = json.loads(request.content)
        self.hosts.append(request.url.host)
        self.all_bodies.append(body)  # in arrival order, beside `hosts`
        if isinstance(body["state"], str):  # the source-text gate: a window of transcript
            self.gate_bodies.append(body)
            result = self.gate_reply(body)
        else:  # routing: one guarded task and its contact line
            self.bodies.append(body)
            result = self.reply(body)
        if isinstance(result, Exception):
            raise result
        return result

    def health_reply(self, request):
        self.health_calls += 1
        self.order.append("health")
        if not self.health_up:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=self.health)

    def router(self, clock=None):
        kwargs = {"clock": clock} if clock else {}
        return DeciderRouter(DeciderClient(URL, http=httpx.Client()), QUESTIONS,
                             expected_model="StrandsAgents/strands-decider-2B-hobson-v21", **kwargs)


def attach(router) -> FakeDecider:
    """Register a FakeDecider's two endpoints on a respx router (one router per test)."""
    fake = FakeDecider()
    router.post(f"{URL}/v1/systemone").mock(side_effect=fake.systemone)
    router.get(f"{URL}/health").mock(side_effect=fake.health_reply)
    return fake


@pytest.fixture
def decider():
    with respx.mock(assert_all_called=False) as router:
        yield attach(router)


@pytest.fixture
def cycle_decider(services):
    """The Decider on the same respx router as the mocked Todoist and Telegram."""
    return attach(services[2])


def status(code):
    return lambda body: httpx.Response(code, json={"detail": "simulated"})


# --- six judgments, one read ---

def test_one_request_carries_all_six_questions_over_one_state(decider):
    decider.router().annotate(TASK, CONTACT)
    [body] = decider.bodies
    assert set(body) == {"state", "questions"}
    assert body["state"] == routing_state(TASK, CONTACT)
    assert {name: q["type"] for name, q in body["questions"].items()} == {
        "project": "choice", "someone_waiting": "noul", "hard_deadline": "noul", "worth_researching_soon": "noul",
        "real_commitment": "noul", "sensitive": "noul"}
    assert list(body["questions"]["project"]["criteria"]) == list(QUESTIONS["project"].criteria)
    assert body["questions"]["someone_waiting"]["criteria"] == QUESTIONS["someone_waiting"].criteria
    assert body["questions"]["hard_deadline"]["criteria"] == QUESTIONS["hard_deadline"].criteria
    assert body["questions"]["sensitive"]["criteria"] == QUESTIONS["sensitive"].criteria


def test_the_state_is_the_guarded_task_and_contact_line_and_nothing_else(decider):
    decider.router().annotate(TASK, CONTACT)
    state = decider.bodies[0]["state"]
    assert set(state["task"]) == {"content", "notes", "due_string"}
    assert set(state["contact"]) == {"name", "organisation", "role", "context"}


def test_a_project_order_reorders_the_options_and_keeps_each_description():
    order = ["other", "community", "personal", "research", "school", "work", "writing"]
    criteria = build_questions(QUESTIONS, project_order=order)["project"]["criteria"]
    assert list(criteria) == order
    assert all(criteria[name] == QUESTIONS["project"].criteria[name] for name in order)


def test_every_answer_above_its_floor_annotates_the_task(decider):
    routed = decider.router().annotate(TASK, CONTACT)
    assert (routed.project, routed.priority, routed.real_commitment, routed.sensitivity) == ("research", 4, True, "clear")
    assert routed.priority_source == "someone_waiting"
    assert routed.task is TASK
    assert [j.question for j in routed.trace] == [
        "project", "priority", "someone_waiting", "hard_deadline", "worth_researching_soon", "real_commitment",
        "sensitive"]


@pytest.mark.parametrize("p, state", [(0.92, "sensitive"), (0.60, "sensitive"), (0.55, "unsure"),
                                      (0.45, "unsure"), (0.40, "clear"), (0.05, "clear")])
def test_the_sensitive_answer_has_four_states_around_its_floor(decider, p, state):
    decider.reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=p))
    assert decider.router().annotate(TASK, CONTACT).sensitivity == state


def test_a_noul_is_judged_by_max_p_and_one_minus_p(decider):
    decider.reply = lambda body: httpx.Response(200, json=decider_answers(commit=0.2))
    assert decider.router().annotate(TASK, CONTACT).real_commitment is False  # 0.8 sure of "no"


def test_below_its_floor_a_project_is_unsorted_and_the_priority_has_no_default(decider):
    decider.reply = lambda body: httpx.Response(200, json=decider_answers(project=("work", 0.5), waiting=0.55))
    routed = decider.router().annotate(TASK, CONTACT)
    assert routed.project is None and routed.priority is None


def test_an_unsure_priority_takes_the_routed_projects_default(decider):
    decider.reply = lambda body: httpx.Response(200, json=decider_answers(project=("school", 0.9), waiting=0.55))
    routed = decider.router().annotate(TASK, CONTACT)
    assert (routed.priority, routed.priority_source) == (4, "default:waiting")  # school defaults to high


def test_a_deadline_counts_from_the_recordings_date_and_a_far_one_does_not_count(decider):
    """Dan's ruling 7: "March" heard on 12 Sep is about six months out, so the deadline yes does not count."""
    from datetime import date

    from ratatoskr_demo.extract import Task

    march = Task(content="Check the careers page", due_string="March", notes=None)
    decider.reply = lambda body: httpx.Response(200, json=decider_answers(waiting=0.1, deadline=0.9, soon=0.1))
    far = decider.router().annotate(march, CONTACT, date(2026, 9, 12))
    assert (far.priority, far.priority_source) == (2, "neither")
    near = decider.router().annotate(march, CONTACT, date(2027, 2, 20))
    assert (near.priority, near.priority_source) == (4, "hard_deadline")
    unknown = decider.router().annotate(march, CONTACT)  # no date to count from: as if unreadable, so near
    assert (unknown.priority, unknown.priority_source) == (4, "hard_deadline")


# --- failures never fail the recording ---

@pytest.mark.parametrize("failure", [lambda b: httpx.ConnectError("refused"), lambda b: httpx.ReadTimeout("slow"),
                                     status(429), status(503)], ids=["refused", "timeout", "429", "503"])
def test_an_unavailable_decider_raises_so_the_cycle_makes_the_recording_wait(decider, failure):
    """Ruling 9: no unrouted task while Decider is missing; the cycle, not the router, stops further reads."""
    decider.reply = failure
    router = decider.router()
    with pytest.raises(DeciderUnavailable):
        router.annotate(TASK, CONTACT)
    assert len(decider.bodies) == 1 and router.take_notices() == []


@pytest.mark.parametrize("reply", [status(422), status(400), lambda b: httpx.Response(200, json={"answers": "x"}),
                                   lambda b: httpx.Response(200, content=b"not json")],
                         ids=["422", "400", "no-answers", "not-json"])
def test_a_rejected_or_unreadable_reply_raises_so_the_cycle_makes_the_recording_wait(decider, reply):
    """Ruling 23: never an unrouted task, never an announcement that tasks are written unrouted."""
    decider.reply = reply
    router = decider.router()
    with pytest.raises(RouterRejected):
        router.annotate(TASK, CONTACT)
    assert len(decider.bodies) == 1 and router.take_notices() == []


def test_a_server_running_another_checkpoint_is_a_configuration_error(decider):
    decider.health["checkpoint"] = "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610"
    with pytest.raises(ConfigurationError, match="decider_model"):
        decider.router().check_access()


def test_a_server_not_up_yet_is_not_an_error_at_startup():
    with respx.mock() as router:
        router.get(f"{URL}/health").mock(side_effect=httpx.ConnectError("refused"))
        FakeDecider().router().check_access()


def test_the_body_built_for_a_task_is_the_one_sent():
    assert request_body(TASK, CONTACT, QUESTIONS)["state"] == routing_state(TASK, CONTACT)


# --- through the whole cycle ---

def decider_settings(tmp_path, **kw):
    return make_settings(tmp_path, {"route": {"backend": "decider"}}, **kw)


def test_the_judgments_reach_todoist_and_the_wrist(tmp_path, clock, services, cycle_decider):
    todoist, telegram, _ = services
    settings = decider_settings(tmp_path)
    runtime_for(settings, ScriptedModel(), clock, router=cycle_decider.router()).cycle.run()
    rec_001 = [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]
    assert [a["labels"] for a in rec_001] == [["pocket", "proj:research"]] * 3
    assert [a["priority"] for a in rec_001] == [4, 4, 4]
    wrist = wrist_messages(telegram)
    assert wrist[0].splitlines() == ["research · high · 0.91", REC_001_TASKS[0]["content"],
                                     "commit yes 0.94 · sens clear 0.94 · prio waiting"]
    assert len(cycle_decider.bodies) == 3  # one read per task, not one per question
    assert len(cycle_decider.gate_bodies) == 2  # and the source gate read rec_001 and rec_005 first


def test_a_sensitive_task_holds_the_recording_before_anything_is_written(tmp_path, clock, services, cycle_decider):
    todoist, telegram, _ = services

    def reply(body):
        sensitive = 0.93 if "careers page" in json.dumps(body["state"]) else 0.05
        return httpx.Response(200, json=decider_answers(sensitive=sensitive))

    cycle_decider.reply = reply
    settings = decider_settings(tmp_path)
    runtime_for(settings, ScriptedModel(), clock, router=cycle_decider.router()).cycle.run()
    entry = json.loads(settings.ledger_path.read_text())["rec_001"]
    assert entry["status"] == "hold" and entry["reason"] == "sensitive"
    assert "rec_001" not in json.dumps(todoist.payloads)
    for task in REC_001_TASKS:
        assert all(task["content"] not in t for t in telegram.texts)


def test_a_routing_failure_after_a_clear_gate_writes_nothing_and_the_recording_waits(tmp_path, clock, services,
                                                                                     cycle_decider):
    """Ruling 9: no unrouted task, no "sens not checked" line; the recording waits for Decider."""
    todoist, telegram, _ = services
    cycle_decider.reply = lambda body: httpx.ConnectError("refused")
    settings = decider_settings(tmp_path)
    runtime = runtime_for(settings, ScriptedModel(), clock, router=cycle_decider.router())
    runtime.cycle.run()
    ledger = json.loads(settings.ledger_path.read_text()) if settings.ledger_path.exists() else {}
    assert "rec_001" not in ledger and todoist.tasks == {} and wrist_messages(telegram) == []
    assert runtime.cycle.deps.retry.attempts("rec_001") == 1


def test_t_leak_no_request_to_decider_carries_transcript_title_or_recording_id(tmp_path, clock, services, cycle_decider,
                                                                                  caplog):
    caplog.set_level("DEBUG")
    model = ScriptedModel()
    settings = decider_settings(tmp_path, fixtures=planted_fixtures(tmp_path))
    runtime_for(settings, model, clock, router=cycle_decider.router()).cycle.run()
    assert any(SENTINEL in user for _, user in model.calls)  # the sentinel was in the transcripts
    sent = json.dumps(cycle_decider.bodies)
    assert len(cycle_decider.bodies) == 3
    assert SENTINEL not in sent and "Speaker 1" not in sent and "Space Coast Meetup" not in sent
    assert "rec_00" not in sent
    assert SENTINEL not in caplog.text and "Email Priya" not in caplog.text  # the state is never logged
