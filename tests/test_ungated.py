"""Dan's ruling 37 (20 Oct 2026): `[route] backend = "none"` runs with no gate, and says so.

At startup the courier logs one warning line; every task it writes in that mode carries the label
"ungated"; and the wrist line shows "ungated" in place of the sensitivity field.
"""

import logging
from types import SimpleNamespace

from ratatoskr_demo import cli
from tests.conftest import REC_001_TASKS, ScriptedModel, make_settings, runtime_for, wrist_messages
from tests.test_devices import demo_setup, demo_update

WARNING = "backend=none: no sensitivity gate; nothing is held for sensitivity (the stop-list tripwire still runs)"


def startup_warnings(settings, caplog):
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="ratatoskr_demo"):
        cli.check_startup(SimpleNamespace(settings=settings, bedrock=None, router=None))
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_backend_none_logs_one_warning_line_at_startup_and_decider_logs_none(tmp_path, caplog):
    assert startup_warnings(make_settings(tmp_path / "none"), caplog) == [WARNING]
    assert startup_warnings(make_settings(tmp_path / "decider", {"route": {"backend": "decider"}}), caplog) == []


def test_every_task_written_with_backend_none_carries_the_ungated_label(tmp_path, clock, services):
    todoist, _telegram, _ = services
    settings = make_settings(tmp_path)
    runtime_for(settings, ScriptedModel(), clock).cycle.run()
    assert len(todoist.tasks) >= len(REC_001_TASKS)  # the cycle really wrote
    assert all("ungated" in args["labels"] for args in todoist.tasks.values())


def test_the_wrist_line_shows_ungated_in_place_of_the_sensitivity_field(tmp_path, clock, services):
    _todoist, telegram, _ = services
    runtime_for(make_settings(tmp_path), ScriptedModel(), clock).cycle.run()
    thirds = [message.splitlines()[2] for message in wrist_messages(telegram)]
    assert thirds and all(line == "commit - · ungated · prio -" for line in thirds)


def test_a_demo_with_backend_none_is_labelled_and_replies_ungated(tmp_path, clock, services):
    todoist, _, _ = services
    settings, runtime, receiver, notifier, fake, model = demo_setup(tmp_path, clock, services, backend="none")
    receiver.handle(demo_update("/demo I promised to email Priya the preprint by Friday"))
    [reply] = notifier.sent
    assert reply.splitlines()[2] == "commit - · ungated · prio -"
    [args] = todoist.tasks.values()
    assert args["labels"] == ["pocket", "ungated"] and fake.bodies == []
