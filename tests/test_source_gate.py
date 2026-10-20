"""The source-text gate (Dan's ruling, 17 Oct 2026): Decider reads the transcript before any Bedrock call."""

import json
import threading

import httpx
import pytest
import respx

from ratatoskr_demo.config import Settings
from ratatoskr_demo.decider import DeciderClient
from ratatoskr_demo.route import load_route_questions
from ratatoskr_demo.source_gate import SOURCE_INSTRUCTION, SourceGate, windows
from tests.conftest import FIXTURES, SENTINEL, ScriptedModel, make_settings, planted_fixtures, runtime_for
from tests.test_decider import FakeDecider, decider_answers

QUESTIONS = load_route_questions(Settings())
LOOPBACK = {"127.0.0.1", "localhost"}
MEDICAL = ("Speaker 1: Keep this between us, but I'm starting medical leave next week; my surgery got moved up. "
           "Speaker 2: I'll give your Tuesday talk for you.")


def noul(p):
    return lambda body: httpx.Response(200, json=decider_answers(sensitive=p))


def any_host_decider(router) -> FakeDecider:
    """A FakeDecider answering /v1/systemone on ANY host, so a request sent off this machine is seen, not lost."""
    fake = FakeDecider()
    router.post(url__regex=r"^https?://[^/]+/v1/systemone$").mock(side_effect=fake.systemone)
    router.get(url__regex=r"^https?://[^/]+/health$").mock(side_effect=fake.health_reply)
    return fake


@pytest.fixture
def gate_decider(services):
    return any_host_decider(services[2])


def decider_settings(tmp_path, **kw):
    return make_settings(tmp_path, {"route": {"backend": "decider"}}, **kw)


def gate_runtime(tmp_path, clock, fake, model=None, **kw):
    settings = decider_settings(tmp_path, **kw)
    model = model or ScriptedModel()
    return settings, runtime_for(settings, model, clock, router=fake.router()), model


def ledger(settings):
    return json.loads(settings.ledger_path.read_text()) if settings.ledger_path.exists() else {}


# --- windows ---

def test_windows_split_on_speaker_turns_and_stay_under_the_word_count():
    turn = "Speaker 1: " + "word " * 120
    parts = windows(turn * 6, 300)
    assert all(len(w.split()) <= 300 for w in parts) and len(parts) == 3
    assert " ".join(parts).split() == (turn * 6).split()  # nothing lost, nothing added


def test_a_turn_longer_than_a_window_is_cut_into_pieces():
    assert [len(w.split()) for w in windows("Speaker 1: " + "x " * 650, 300)] == [300, 300, 52]


def test_line_breaks_are_turns_too_and_empty_text_has_no_windows():
    assert windows("A: one\nB: two", 300) == ["A: one B: two"]
    assert windows("   ", 300) == []


# --- the gate on its own ---

def gate(reply, words=300):
    with respx.mock(assert_all_called=False) as router:
        fake = any_host_decider(router)
        fake.gate_reply = reply
        result = SourceGate(DeciderClient("http://127.0.0.1:8000", http=httpx.Client()), QUESTIONS["sensitive"],
                            window_words=words).check(("Speaker 1: " + "word " * 100) * 7)
    return result, fake


def test_the_gate_asks_the_reworded_sensitive_question_with_route_tomls_criteria():
    _, fake = gate(noul(0.05))
    body = fake.gate_bodies[0]
    assert isinstance(body["state"], str)
    assert body["questions"] == {"sensitive": {"type": "noul", "instructions": SOURCE_INSTRUCTION,
                                               "criteria": QUESTIONS["sensitive"].criteria}}
    assert fake.bodies == []  # no routing request: the gate asks one question


def test_every_window_clear_is_clear():
    verdict, fake = gate(noul(0.05))
    assert (verdict.status, verdict.windows_asked, verdict.hold) == ("clear", 4, False)  # 7 turns of 102 words, 2 per window


def test_one_sensitive_window_holds_the_recording_and_the_gate_stops_there():
    replies = iter([0.05, 0.93, 0.05])
    verdict, fake = gate(lambda body: httpx.Response(200, json=decider_answers(sensitive=next(replies))))
    assert (verdict.status, verdict.reason, verdict.windows_asked, verdict.windows_total) == (
        "sensitive", "source-sensitive", 2, 4)
    assert verdict.max_p == pytest.approx(0.93)


@pytest.mark.parametrize("p", [0.55, 0.45, 0.41])
def test_an_unsure_window_holds_too(p):
    verdict, _ = gate(noul(p))
    assert (verdict.status, verdict.reason, verdict.hold) == ("unsure", "source-unsure", True)


@pytest.mark.parametrize("p, status", [(0.60, "sensitive"), (0.40, "clear"), (0.59, "unsure")])
def test_the_floor_is_0_60_and_exactly_at_it_counts(p, status):
    verdict, _ = gate(noul(p))
    assert verdict.status == status


def test_the_gate_logs_counts_and_the_highest_probability_never_the_text(caplog):
    caplog.set_level("DEBUG")
    with respx.mock(assert_all_called=False) as router:
        any_host_decider(router)
        SourceGate(DeciderClient("http://127.0.0.1:8000", http=httpx.Client()), QUESTIONS["sensitive"]).check(
            "Speaker 1: my code word is " + SENTINEL)
    assert "1 of 1 window(s) asked, max p=0.05 -> clear" in caplog.text and SENTINEL not in caplog.text


# --- in the cycle ---

def test_the_gate_runs_after_the_tripwire_and_before_any_bedrock_call(tmp_path, clock, services, gate_decider):
    settings, runtime, model = gate_runtime(tmp_path, clock, gate_decider)
    runtime.cycle.run()
    states = [b["state"] for b in gate_decider.gate_bodies]
    assert not any("Vargrheim" in s or "Marcus" in s for s in states)  # rec_002: the tripwire, first
    assert len(gate_decider.gate_bodies) == 2 and len(model.calls) == 2  # rec_001 and rec_005: gate, then model
    assert ledger(settings)["rec_002"]["reason"] == "stoplist:vargrheim"


def test_a_sensitive_source_holds_before_extraction_with_an_id_only_notice(tmp_path, clock, services, gate_decider):
    todoist, telegram, _ = services
    gate_decider.gate_reply = lambda body: httpx.Response(
        200, json=decider_answers(sensitive=0.95 if "Priya" in body["state"] else 0.05))
    settings, runtime, model = gate_runtime(tmp_path, clock, gate_decider)
    runtime.cycle.run()
    assert ledger(settings)["rec_001"]["status"] == "hold" and ledger(settings)["rec_001"]["reason"] == "source-sensitive"
    assert not any("Priya" in user for _, user in model.calls)  # never reached Bedrock
    assert "rec_001" not in json.dumps(todoist.payloads)
    assert "Held rec_001: source-sensitive. Tag it ok in Pocket to release it." in telegram.texts
    assert not any("Space Coast" in t for t in telegram.texts)  # the id only, not the title


def test_an_unsure_source_holds_as_well(tmp_path, clock, services, gate_decider):
    gate_decider.gate_reply = noul(0.5)
    settings, runtime, model = gate_runtime(tmp_path, clock, gate_decider)
    runtime.cycle.run()
    assert ledger(settings)["rec_001"]["reason"] == "source-unsure" and model.calls == []


def test_an_ok_release_is_not_gated_again(tmp_path, clock, services, gate_decider):
    from tests.test_jev import tagged_fixtures

    gate_decider.gate_reply = noul(0.95)
    settings, runtime, model = gate_runtime(tmp_path, clock, gate_decider)
    runtime.cycle.run()
    assert ledger(settings)["rec_001"]["reason"] == "source-sensitive"
    asked = len(gate_decider.gate_bodies)
    clock.advance(300)
    _, again, model2 = gate_runtime(tmp_path, clock, gate_decider, fixtures=tagged_fixtures(tmp_path, "rec_001", "ok"))
    again.cycle.run()
    assert ledger(settings)["rec_001"]["status"] == "done" and len(gate_decider.gate_bodies) == asked
    assert any("Priya" in user for _, user in model2.calls)


def test_with_routing_off_there_is_no_gate_and_no_decider_request(tmp_path, clock, services, gate_decider):
    settings = make_settings(tmp_path)  # backend "none"
    runtime_for(settings, ScriptedModel(), clock).cycle.run()
    assert gate_decider.gate_bodies == [] and ledger(settings)["rec_001"]["status"] == "done"


def test_with_jev_routing_the_gate_is_skipped_and_the_post_extraction_check_remains(tmp_path, clock, services,
                                                                                  gate_decider):
    from tests.test_jev import FakeJev, jev_settings
    from ratatoskr_demo.cli import build_runtime

    settings = jev_settings(tmp_path)
    runtime = build_runtime(settings, dry_run=False, model_call=ScriptedModel(), http=httpx.Client(), clock=clock,
                            router=FakeJev().router())
    assert runtime.cycle.deps.source_gate is None
    runtime.cycle.run()
    assert gate_decider.gate_bodies == [] and ledger(settings)["rec_001"]["status"] == "done"


def test_decider_down_makes_the_recording_wait_without_any_bedrock_call(tmp_path, clock, services, gate_decider):
    gate_decider.gate_reply = lambda body: httpx.ConnectError("refused")
    settings, runtime, model = gate_runtime(tmp_path, clock, gate_decider)
    runtime.cycle.run()
    assert "rec_001" not in ledger(settings) and model.calls == []
    assert runtime.cycle.deps.retry.attempts("rec_001") == 1  # retried on a later poll (C7 backoff)


def test_decider_down_lets_an_urgent_recording_past_the_gate_and_it_is_written_unrouted(tmp_path, clock, services,
                                                                                         gate_decider, caplog):
    """Ruling 12: Decider unreachable (here the gate's read fails), an urgent recording goes past the gate, is
    extracted, and is written unrouted; the other recordings wait (tests/test_urgent.py has every branch)."""
    from tests.test_jev import tagged_fixtures

    gate_decider.gate_reply = lambda body: httpx.ConnectError("refused")
    settings, runtime, model = gate_runtime(tmp_path, clock, gate_decider,
                                            fixtures=tagged_fixtures(tmp_path, "rec_001", "urgent"))
    caplog.set_level("WARNING")
    runtime.cycle.run()
    assert ledger(settings)["rec_001"]["status"] == "done" and "rec_005" not in ledger(settings)
    assert any("Priya" in user for _, user in model.calls) and not any("Lena" in user for _, user in model.calls)
    assert gate_decider.bodies == []  # no routing read after the gate's read failed
    assert "tagged urgent and Decider unreachable, so it goes on without it" in caplog.text

def test_a_dry_run_says_it_would_hold_on_the_source(tmp_path, clock, services, gate_decider):
    from ratatoskr_demo.cli import build_runtime

    gate_decider.gate_reply = noul(0.95)
    printed = []
    settings = decider_settings(tmp_path)
    runtime = build_runtime(settings, dry_run=True, model_call=ScriptedModel(), http=httpx.Client(), clock=clock,
                            router=gate_decider.router())
    runtime.cycle.deps.out = printed.append
    runtime.cycle.run()
    assert "    would hold: source-sensitive" in printed and not settings.ledger_path.exists()


# --- /demo: the confidential note is held before extraction ---

def test_the_medical_leave_note_sent_with_demo_is_held_before_extraction(tmp_path, clock, services, gate_decider):
    from ratatoskr_demo.commands import TelegramReceiver

    todoist, _, _ = services
    gate_decider.gate_reply = lambda body: httpx.Response(
        200, json=decider_answers(sensitive=0.94 if "medical leave" in body["state"] else 0.05))
    settings, runtime, model = gate_runtime(tmp_path, clock, gate_decider)
    sent = []

    class Recorder:
        def send_message(self, text):
            sent.append(text)

    receiver = TelegramReceiver(lambda o: [], Recorder(), 4242, runtime.state, threading.Event(),
                                process_text=runtime.cycle.process_text)
    receiver.handle({"update_id": 1, "message": {"message_id": 21, "date": 1790000000, "chat": {"id": 4242},
                                                 "text": "/demo " + MEDICAL}})
    assert sent == ["held · source-sensitive\nNothing was written."]
    assert model.calls == [] and todoist.payloads == [] and gate_decider.bodies == []
    assert ledger(settings)["tg-21"]["reason"] == "source-sensitive"


# --- T-LEAK, as the ruling changes it ---

def test_t_leak_transcript_text_reaches_only_the_loopback_decider_and_the_extraction(tmp_path, clock, services,
                                                                                    gate_decider, caplog):
    """The sentinel MAY reach Decider, only on 127.0.0.1 / localhost; it reaches Bedrock only as the extraction
    itself; it never reaches a routing request, a log, the ledger, Telegram, Todoist or any file."""
    todoist, telegram, _ = services
    caplog.set_level("DEBUG")
    settings, runtime, model = gate_runtime(tmp_path, clock, gate_decider, fixtures=planted_fixtures(tmp_path))
    runtime.cycle.run()

    carrying = {host for host, body in zip(gate_decider.hosts, gate_decider.all_bodies) if SENTINEL in json.dumps(body)}
    assert any(SENTINEL in b["state"] for b in gate_decider.gate_bodies)  # the gate really read the transcript
    assert carrying and carrying <= LOOPBACK, f"transcript text sent to {carrying - LOOPBACK}"
    assert SENTINEL not in json.dumps(gate_decider.bodies)  # routing: the guarded task only
    places = {"log": caplog.text, "telegram": json.dumps(telegram.messages), "todoist": json.dumps(todoist.payloads)}
    for path in settings.paths.state_dir.rglob("*"):
        if path.is_file():
            places[path.name] = path.read_bytes().decode("utf-8", "replace")
    for where, text in places.items():
        assert SENTINEL not in text, f"transcript sentinel leaked into {where}"
