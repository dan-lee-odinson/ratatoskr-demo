"""The device envelope, its adapters, the wrist format, and /demo through the full cycle."""

import itertools
import json
import threading
from datetime import datetime, timezone

import jsonschema
import pytest

from ratatoskr_demo.commands import TelegramReceiver, format_demo_reply
from ratatoskr_demo.devices import glasses_adapter, text_adapter, wrist_adapter
from ratatoskr_demo.devices.envelope import (
    AudioRef, Button, Envelope, EnvelopeError, TagRequest, Text, TranscriptRef, VideoRef, schema,
)
from ratatoskr_demo.devices.pocket_adapter import PocketAdapter
from ratatoskr_demo.extract import Task
from ratatoskr_demo.judge import Judgement
from ratatoskr_demo.ledger import Ledger
from ratatoskr_demo.pocket import PocketClient
from ratatoskr_demo.route import RoutedTask, load_route_questions
from ratatoskr_demo.config import Settings
from ratatoskr_demo.state import State
from tests.conftest import FIXTURES, REC_001_TASKS, SENTINEL, ScriptedModel, extraction_json, make_settings, runtime_for
from tests.test_decider import attach, decider_answers

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
VALIDATOR = jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)


def valid(envelope: Envelope) -> bool:
    errors = list(VALIDATOR.iter_errors(envelope.to_json()))
    return not errors and Envelope.from_json(envelope.to_json()) == envelope


# --- the envelope and the schema ---

def test_the_published_schema_is_itself_valid():
    jsonschema.Draft202012Validator.check_schema(schema())


def test_every_adapters_output_validates_against_the_schema():
    pocket = PocketAdapter(PocketClient(None, fixtures_dir=FIXTURES), clock=lambda: NOW.timestamp())
    envelopes = pocket.list_new()
    _, demo = text_adapter.from_telegram({"message_id": 7, "date": 1790000000, "text": "/demo send the deck"})
    glasses = glasses_adapter.receive(Envelope("glasses", "glasses", NOW, VideoRef("file:///fixtures/clip.mp4")))
    assert len(envelopes) == 5
    for envelope in [*envelopes, demo, glasses]:
        assert valid(envelope), envelope


@pytest.mark.parametrize("payload", [TranscriptRef("rec_001"), Text("hello"), Button("double-press"),
                                     TagRequest("rec_002", "ok"), AudioRef("s3://bucket/a.wav"),
                                     VideoRef("file:///v.mp4")], ids=lambda p: type(p).__name__)
def test_every_payload_type_round_trips_through_the_schema(payload):
    assert valid(Envelope("device-1", "text", NOW, payload))


@pytest.mark.parametrize("build", [
    lambda: Envelope("", "text", NOW, Text("x")),
    lambda: Envelope("Bad Id", "text", NOW, Text("x")),
    lambda: Envelope("d", "phone", NOW, Text("x")),
    lambda: Envelope("d", "text", datetime(2026, 1, 1), Text("x")),  # no time zone
    lambda: Envelope("d", "text", NOW, "not a payload"),
    lambda: Text("   "),
    lambda: TranscriptRef("has spaces"),
    lambda: VideoRef("not a uri"),
], ids=["empty-id", "bad-id", "bad-kind", "naive-ts", "bad-payload", "blank-text", "bad-ref", "bad-uri"])
def test_an_invalid_envelope_cannot_be_built(build):
    with pytest.raises(EnvelopeError):
        build()


def test_the_schema_rejects_what_the_constructor_rejects():
    good = Envelope("d", "text", NOW, Text("x")).to_json()
    for broken in ({**good, "kind": "phone"}, {**good, "extra": 1}, {**good, "payload": {"type": "text"}},
                   {**good, "payload": {"type": "text", "text": "x", "more": 1}}):
        assert list(VALIDATOR.iter_errors(broken))


def test_a_text_payload_never_shows_its_text():
    envelope = Envelope("telegram", "text", NOW, Text("my code word is " + SENTINEL))
    assert SENTINEL not in repr(envelope) and SENTINEL not in str(envelope)


def test_a_transcript_ref_carries_the_id_and_never_the_transcript():
    pocket = PocketAdapter(PocketClient(None, fixtures_dir=FIXTURES), clock=lambda: NOW.timestamp())
    envelope = pocket.list_new()[0]
    assert envelope.payload == TranscriptRef("rec_001") and "Speaker" not in json.dumps(envelope.to_json())
    assert "Speaker 1" in pocket.fetch_text(envelope)  # fetched at extraction time instead
    assert pocket.source(envelope).item_id == "rec_001" and "Speaker" not in repr(pocket.source(envelope))


def test_the_glasses_adapter_is_a_labelled_stub_that_changes_nothing():
    assert glasses_adapter.IS_STUB and "STUB" in glasses_adapter.__doc__
    envelope = Envelope("glasses", "glasses", NOW, VideoRef("file:///clip.mp4"))
    assert glasses_adapter.receive(envelope) is envelope
    with pytest.raises(TypeError):
        glasses_adapter.receive(Envelope("glasses", "glasses", NOW, Text("x")))


# --- the wrist ---

QUESTIONS = load_route_questions(Settings())
PROJECTS = list(QUESTIONS["project"].criteria) + [None]


def routed(project, priority, confidence):
    trace = (Judgement("project", project or "work", confidence, 0.7, project),)
    return RoutedTask(task=Task("Email the deck", None, None), project=project, priority=priority, trace=trace)


@pytest.mark.parametrize("project, priority, confidence",
                         list(itertools.product(PROJECTS, [None, 2, 3, 4], [None, 0.0, 0.71, 1.0])))
def test_the_first_line_always_fits_the_watch_face(project, priority, confidence):
    line = wrist_adapter.first_line(routed(project, priority, confidence))
    assert len(line) <= wrist_adapter.FIRST_LINE_MAX == 24, line


def test_the_longest_project_and_priority_fit_because_priority_is_written_short():
    assert wrist_adapter.first_line(routed("community", 3, 0.71)) == "community · med · 0.71"


def test_the_three_lines_are_header_task_and_the_remaining_judgments():
    trace = (Judgement("project", "research", 0.92, 0.7, "research"),
             Judgement("priority", "high (someone_waiting)", None, 0.0, 4, note="someone_waiting", answer="high"),
             Judgement("real_commitment", "p=0.91", 0.91, 0.7, True),
             Judgement("sensitive", "p=0.18", 0.82, 0.6, False))
    task = RoutedTask(task=Task("Email Priya the preprint", None, None), project="research", priority=4,
                      real_commitment=True, sensitivity="clear", priority_source="someone_waiting", trace=trace)
    assert wrist_adapter.format_task(task, task.content, sensitivity_unchecked=False).splitlines() == [
        "research · high · 0.92", "Email Priya the preprint", "commit yes 0.91 · sens clear 0.82 · prio waiting"]


@pytest.mark.parametrize("source, shown", [("someone_waiting", "waiting"), ("hard_deadline", "deadline"),
                                           ("worth_researching_soon", "soon"), ("neither", "none"),
                                           ("default:waiting", "default:waiting"),
                                           ("default:deadline", "default:deadline"),
                                           ("default:soon", "default:soon"), (None, "-")])
def test_the_wrist_shows_which_step_set_the_priority(source, shown):
    task = RoutedTask(task=Task("x", None, None), priority=3, priority_source=source)
    assert wrist_adapter.third_line(task, sensitivity_unchecked=False).endswith(f"prio {shown}")


def test_an_unrouted_task_shows_dashes_and_says_when_sensitivity_was_not_checked():
    task = RoutedTask(task=Task("Email the deck", None, None))
    assert wrist_adapter.format_task(task, "Email the deck", sensitivity_unchecked=True).splitlines() == [
        "unsorted · - · -", "Email the deck", "commit - · sens not checked · prio -"]
    assert wrist_adapter.third_line(task, sensitivity_unchecked=False) == "commit - · sens - · prio -"


# --- /demo: the Text envelope through the full cycle ---

def demo_update(text, chat=4242, message_id=11):
    return {"update_id": 1, "message": {"message_id": message_id, "date": 1790000000, "chat": {"id": chat},
                                        "text": text}}


class Recorder:
    def __init__(self):
        self.sent = []

    def send_message(self, text):
        self.sent.append(text)


def demo_setup(tmp_path, clock, services, model=None, backend="decider"):
    settings = make_settings(tmp_path, {"route": {"backend": backend}})
    fake = attach(services[2])
    model = model or ScriptedModel(replies={}, default=extraction_json(tasks=[REC_001_TASKS[0]]))
    runtime = runtime_for(settings, model, clock, router=fake.router() if backend == "decider" else None)
    notifier = Recorder()
    receiver = TelegramReceiver(lambda offset: [], notifier, 4242, runtime.state, threading.Event(),
                                process_text=runtime.cycle.process_text)
    return settings, runtime, receiver, notifier, fake, model


def test_demo_from_another_chat_is_ignored_and_reaches_nothing(tmp_path, clock, services):
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    receiver.handle(demo_update("/demo send Priya the preprint", chat=999))
    assert notifier.sent == [] and model.calls == [] and fake.bodies == []
    assert not settings.ledger_path.exists()


def test_demo_runs_the_text_through_the_whole_cycle_and_replies_wrist_trace_and_outcome(tmp_path, clock, services):
    todoist, telegram, _ = services
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    receiver.handle(demo_update("/demo I promised to email Priya the preprint by Friday"))
    [reply] = notifier.sent
    lines = reply.splitlines()
    assert lines[:3] == ["research · high · 0.91", REC_001_TASKS[0]["content"],
                         "commit yes 0.94 · sens clear 0.94 · prio waiting"]
    assert lines[3].startswith("decider: project research 0.91 · priority high by waiting · waiting p=0.88 0.88 · "
                               "deadline p=0.30 0.70 · soon p=0.20 0.80 · commit p=0.94")
    assert lines[4] == "written to Ratatoskr Demo"
    assert len(fake.bodies) == 1 and len(model.calls) == 1
    [args] = todoist.tasks.values()
    assert args["labels"] == ["pocket", "proj:research"] and "Pocket id: tg-11 #1" in args["description"]
    assert json.loads(settings.ledger_path.read_text())["tg-11"]["status"] == "done"
    assert telegram.texts == []  # the reply is the notice: nothing else goes to the chat


def test_a_demo_pleasantry_is_written_marked_maybe(tmp_path, clock, services):
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    fake.reply = lambda body: __import__("httpx").Response(200, json=decider_answers(commit=0.08))
    receiver.handle(demo_update("/demo great talking with you, we should grab coffee sometime"))
    assert notifier.sent[0].splitlines()[1].startswith("[maybe] ")
    assert notifier.sent[0].splitlines()[-1] == "written to Ratatoskr Demo, marked [maybe]"


def test_a_sensitive_demo_is_held_and_the_reply_carries_no_task_text(tmp_path, clock, services):
    todoist, _, _ = services
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    fake.reply = lambda body: __import__("httpx").Response(200, json=decider_answers(sensitive=0.95))
    receiver.handle(demo_update("/demo she told me about her diagnosis over lunch today"))
    [reply] = notifier.sent
    assert reply.splitlines()[:2] == ["held · sensitive", "Nothing was written."]
    assert REC_001_TASKS[0]["content"] not in reply and todoist.tasks == {}
    assert reply.splitlines()[2].startswith("decider: project research")


def test_the_tripwire_holds_a_demo_naming_the_client_before_any_model_or_decider_call(tmp_path, clock, services):
    todoist, _, _ = services
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    receiver.handle(demo_update("/demo Lunch with the VARGRHEIM board; send them the numbers"))
    assert model.calls == [] and fake.bodies == [] and todoist.payloads == []
    [reply] = notifier.sent
    assert reply.splitlines()[0] == "held · tripwire" and "Nothing reached any model" in reply
    assert "Lunch" not in reply and "numbers" not in reply
    assert json.loads(settings.ledger_path.read_text())["tg-11"]["reason"] == "stoplist:vargrheim"


def test_the_tripwire_runs_before_the_extraction_and_route_backends_are_even_reached(tmp_path, clock, monkeypatch,
                                                                                       services):
    import ratatoskr_demo.cycle as cycle_module

    reached = []
    monkeypatch.setattr(cycle_module.extract, "run", lambda *a, **k: reached.append("extract"))
    monkeypatch.setattr(cycle_module, "route", lambda *a, **k: reached.append("route"))
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    rid, envelope = text_adapter.from_telegram(demo_update("/demo Vargrheim wants the deck")["message"])
    outcome = runtime.cycle.process_text(rid, envelope)
    assert outcome.status == "hold" and reached == []


def test_the_same_demo_message_delivered_twice_is_processed_once(tmp_path, clock, services):
    todoist, _, _ = services
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    receiver.handle(demo_update("/demo I will email Priya the preprint before the weekend"))
    receiver.handle(demo_update("/demo I will email Priya the preprint before the weekend"))
    assert len(todoist.tasks) == 1 and notifier.sent[1] == "already handled (tg-11)"


def test_a_demo_under_eight_words_is_skipped_with_no_read_and_no_model_call(tmp_path, clock, services):
    """Ruling 14: a text under 8 words is not a conversation; nothing is asked, nothing written."""
    todoist, _, _ = services
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    receiver.handle(demo_update("/demo testing one two"))
    assert notifier.sent == ["skipped \u00b7 under 8 words\nNothing was asked or written."]
    assert model.calls == [] and fake.all_bodies == [] and todoist.payloads == []


def test_t_leak_demo_text_is_never_persisted_or_logged(tmp_path, clock, services, caplog):
    todoist, telegram, _ = services
    caplog.set_level("DEBUG")
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    receiver.handle(demo_update(f"/demo I promised Priya the preprint, my code word is {SENTINEL}"))
    assert any(SENTINEL in user for _, user in model.calls)  # the model really saw it
    places = {"log": caplog.text, "reply": "\n".join(notifier.sent), "todoist": json.dumps(todoist.payloads),
              "decider": json.dumps(fake.bodies), "telegram": json.dumps(telegram.messages)}
    for path in settings.paths.state_dir.rglob("*"):
        if path.is_file():
            places[path.name] = path.read_text(encoding="utf-8")
    for where, text in places.items():
        assert SENTINEL not in text, f"demo text leaked into {where}"


def test_demo_without_text_gets_the_usage_line(tmp_path, clock, services):
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services)
    receiver.handle(demo_update("/demo"))
    assert notifier.sent == ["Usage: /demo <text to treat as a transcript>"] and model.calls == []


def test_demo_reply_formats_for_the_other_outcomes():
    from ratatoskr_demo.cycle import Outcome

    assert format_demo_reply(Outcome("hold", reason="leak-guard"), "tg-1").splitlines()[0] == "held · leak-guard"
    assert format_demo_reply(Outcome("stopped", reason="spend hard stop"), "tg-1") == "stopped: spend hard stop"
    assert format_demo_reply(Outcome("error", reason="transient: x"), "tg-1") == "not processed: transient: x"


def test_the_text_adapter_reads_only_demo_commands():
    assert text_adapter.demo_text("/demo  hello there ") == "hello there"
    assert text_adapter.demo_text("/demo@RatatoskrDemoBot hi") == "hi"
    assert text_adapter.demo_text("/demonstrate x") is None and text_adapter.demo_text("/demo") is None
    assert text_adapter.from_telegram({"message_id": 1, "text": "/status"}) is None


def test_a_state_built_without_a_cycle_still_answers_status(tmp_path):
    receiver = TelegramReceiver(lambda o: [], Recorder(), 4242, State(Ledger(tmp_path / "p.json")), threading.Event())
    receiver.handle(demo_update("/demo hello"))  # no cycle wired: /demo is simply not available
    assert receiver.notifier.sent == []
