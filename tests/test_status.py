"""The thread count, the Telegram receiver (/status and its survival), and the loop's stops."""

import threading

from ratatoskr_demo.cli import start_receiver
from ratatoskr_demo.commands import TelegramReceiver
from ratatoskr_demo.ledger import Ledger
from ratatoskr_demo.state import State
from tests.conftest import ScriptedModel, make_settings, runtime_for


def test_there_are_exactly_two_threads_after_startup(tmp_path, clock):
    runtime = runtime_for(make_settings(tmp_path), ScriptedModel(), clock)
    stop = threading.Event()
    before = threading.active_count()
    assert before == 1

    def long_poll(offset):
        stop.wait(30)
        return []

    receiver = start_receiver(runtime, stop, get_updates=long_poll)
    try:
        assert threading.active_count() == 2
        assert {t.name for t in threading.enumerate()} == {"MainThread", "telegram-receiver"}
    finally:
        stop.set()
        receiver.thread.join(5)


class Recorder:
    def __init__(self):
        self.sent = []

    def send_message(self, text):
        self.sent.append(text)


def test_the_telegram_status_command_answers_only_dan(tmp_path):
    state = State(Ledger(tmp_path / "p.json"))
    notifier = Recorder()
    receiver = TelegramReceiver(lambda offset: [], notifier, 4242, state, threading.Event())
    receiver.handle({"message": {"chat": {"id": 999}, "text": "/status"}})
    receiver.handle({"message": {"chat": {"id": 4242}, "text": "/delete everything"}})
    assert notifier.sent == []
    receiver.handle({"message": {"chat": {"id": 4242}, "text": "/status@RatatoskrDemoBot"}})
    assert len(notifier.sent) == 1 and notifier.sent[0].startswith("Ratatoskr Demo status")


def test_the_telegram_receiver_survives_any_error_and_keeps_polling(tmp_path):
    from ratatoskr_demo.errors import TerminalError

    stop = threading.Event()
    calls = []

    def get_updates(offset):
        calls.append(offset)
        if len(calls) == 1:
            raise TerminalError("telegram: HTTP 404")
        if len(calls) == 2:
            raise KeyError("surprise")
        stop.set()
        return [{"update_id": 7, "message": {"chat": {"id": 4242}, "text": "/status"}}]

    notifier = Recorder()
    receiver = TelegramReceiver(get_updates, notifier, 4242, State(Ledger(tmp_path / "p.json")), stop)
    receiver.stop.wait = lambda seconds: None  # no real sleeping in tests
    receiver.run()
    assert len(calls) == 3 and len(notifier.sent) == 1


def test_the_loop_stops_by_itself_at_the_spend_hard_stop(tmp_path, clock, services):
    from ratatoskr_demo.cli import EX_CONFIG, run_loop
    from ratatoskr_demo.spend import SpendStop

    def broke(system, user):
        raise SpendStop("Bedrock spend hard stop: nothing was sent")

    runtime = runtime_for(make_settings(tmp_path), broke, clock)
    stop = threading.Event()

    def idle(offset):
        stop.wait(1)
        return []

    assert run_loop(runtime, stop=stop, get_updates=idle) == EX_CONFIG
    assert runtime.cycle.stopped
