"""Device independence (Dan's ruling, 17 Oct 2026): the courier runs with no wearable at all."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ratatoskr_demo.cli import EX_CONFIG, build_runtime, main
from ratatoskr_demo.config import load_settings, require_secrets, settings_from_mapping
from ratatoskr_demo.devices.envelope import Envelope, Text
from ratatoskr_demo.devices.registry import INPUT_NAMES, build_inputs
from ratatoskr_demo.errors import ConfigurationError
from tests.conftest import FIXTURES, ScriptedModel, extraction_json

MINIMUM = {"BEDROCK_API_KEY": "k", "TELEGRAM_BOT_TOKEN": "TEST-TOKEN", "TELEGRAM_CHAT_ID": "4242",
           "TODOIST_API_TOKEN": "t"}


def settings_for(tmp_path, devices, route="none", env=None, fixtures=True):
    base = {"RATATOSKR_STATE_DIR": str(tmp_path / "state"), **MINIMUM, **(env or {})}
    if fixtures:
        base["RATATOSKR_FIXTURES"] = str(FIXTURES)
    return settings_from_mapping({"devices": devices, "route": {"backend": route}}, base)


# --- secrets: only for the adapters that are on ---

@pytest.mark.parametrize("route", ["none", "decider"])
def test_the_minimum_install_starts_with_four_secrets_and_the_text_input(tmp_path, route):
    settings = settings_for(tmp_path, {"inputs": ["text"]}, route=route, fixtures=False)
    require_secrets(settings)  # no Pocket key, no TypeSafe key: none needed
    runtime = build_runtime(settings, dry_run=False, model_call=ScriptedModel(), http=httpx.Client())
    assert set(runtime.cycle.deps.inputs) == {"text"}
    assert runtime.cycle.run().startswith("ok: 0 listed")


def test_the_minimum_install_through_the_command_line(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('[devices]\ninputs = ["text"]\n[route]\nbackend = "none"\n', encoding="utf-8")
    env = tmp_path / "x.env"
    env.write_text("".join(f"{k}={v}\n" for k, v in MINIMUM.items()), encoding="utf-8")
    monkeypatch.setenv("RATATOSKR_CONFIG", str(config))
    monkeypatch.setenv("RATATOSKR_ENV_FILE", str(env))
    monkeypatch.setenv("RATATOSKR_STATE_DIR", str(tmp_path / "state"))
    import ratatoskr_demo.cli as cli

    monkeypatch.setattr(cli, "check_startup", lambda runtime: None)  # no model record check: no network here
    assert main(["--once"]) == 0


def test_pocket_enabled_without_its_key_fails_and_names_the_adapter(tmp_path, capsys, monkeypatch):
    settings = settings_for(tmp_path, {"inputs": ["text", "pocket"]}, fixtures=False)
    with pytest.raises(ConfigurationError, match=r"POCKET_API_KEY \(for the pocket input\)"):
        require_secrets(settings)
    config = tmp_path / "config.toml"
    config.write_text('[devices]\ninputs = ["text", "pocket"]\n[route]\nbackend = "none"\n', encoding="utf-8")
    env = tmp_path / "x.env"
    env.write_text("".join(f"{k}={v}\n" for k, v in MINIMUM.items()), encoding="utf-8")
    monkeypatch.setenv("RATATOSKR_CONFIG", str(config))
    monkeypatch.setenv("RATATOSKR_ENV_FILE", str(env))
    import ratatoskr_demo.cli as cli

    monkeypatch.setattr(cli, "_notify_config_error", lambda exc: None)
    assert main(["--once"]) == EX_CONFIG
    assert "POCKET_API_KEY (for the pocket input)" in capsys.readouterr().err


@pytest.mark.parametrize("devices, missing, named", [
    ({"inputs": ["text"], "outputs": ["todoist", "telegram"]}, "TODOIST_API_TOKEN", "the todoist output"),
    ({"inputs": ["text"], "outputs": ["todoist", "telegram"]}, "TELEGRAM_BOT_TOKEN", "the telegram output"),
    ({"inputs": ["pocket"], "outputs": ["todoist"]}, "TODOIST_API_TOKEN", "the todoist output"),
])
def test_a_missing_secret_names_the_adapter_that_needs_it(tmp_path, devices, missing, named):
    settings = settings_for(tmp_path, devices, env={missing: "", "POCKET_API_KEY": "p"}, fixtures=False)
    with pytest.raises(ConfigurationError, match=f"{missing} \\(for {named}\\)"):
        require_secrets(settings)


def test_secrets_of_disabled_adapters_are_ignored(tmp_path):
    settings = settings_for(tmp_path, {"inputs": ["pocket"], "outputs": ["todoist"]},
                            env={"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "POCKET_API_KEY": "p"},
                            fixtures=False)
    require_secrets(settings)  # no Telegram output, no text input: no Telegram secret needed


def test_the_typesafe_key_is_needed_only_for_jev_or_the_bench(tmp_path):
    from ratatoskr_demo.config import needed_secrets

    settings = settings_for(tmp_path, {"inputs": ["text"]}, fixtures=False)
    assert "TYPESAFE_API_KEY" not in needed_secrets(settings)
    assert "TYPESAFE_API_KEY" in needed_secrets(settings, bench=True)
    with pytest.raises(ConfigurationError, match="TYPESAFE_API_KEY"):
        require_secrets(settings_for(tmp_path, {"inputs": ["text"]}, route="jev", fixtures=False))


@pytest.mark.parametrize("devices, message", [
    ({"inputs": []}, "inputs"), ({"inputs": ["fax"]}, "inputs"), ({"inputs": ["text", "text"]}, "inputs"),
    ({"outputs": ["email"]}, "outputs"), ({"inputs": ["text"], "outputs": ["todoist"]}, "needs the \"telegram\" output"),
])
def test_bad_device_lists_are_configuration_errors(tmp_path, devices, message):
    with pytest.raises(ConfigurationError, match=message):
        settings_for(tmp_path, devices)


def test_the_shipped_demo_config_enables_pocket_and_text():
    settings = load_settings({"RATATOSKR_CONFIG": str(Path(__file__).resolve().parents[1] / "config.toml")})
    assert settings.devices.inputs == ("pocket", "text") and settings.devices.outputs == ("todoist", "telegram")


# --- every input through the registry, one fixture envelope through the full cycle ---

def full_cycle_runtime(tmp_path, clock, inputs):
    settings = settings_for(tmp_path, {"inputs": inputs})
    model = ScriptedModel(default=extraction_json(tasks=[{"content": "Send the calibration notes", "due_string": None,
                                                          "notes": None}]))
    return settings, build_runtime(settings, dry_run=False, model_call=model, http=httpx.Client(), clock=clock)


def test_the_registry_builds_every_input_with_one_interface(tmp_path, clock):
    settings = settings_for(tmp_path, {"inputs": list(INPUT_NAMES)})
    inputs = build_inputs(settings, http=httpx.Client(), clock=clock)
    assert list(inputs) == ["text", "pocket", "glasses"]
    for adapter in inputs.values():
        for method in ("list_new", "source", "fetch_text"):
            assert callable(getattr(adapter, method)), (adapter.name, method)


@pytest.mark.parametrize("name", INPUT_NAMES)
def test_each_input_runs_a_fixture_envelope_through_the_full_cycle(tmp_path, clock, services, name):
    todoist, telegram, _ = services
    settings, runtime = full_cycle_runtime(tmp_path, clock, [name] if name != "glasses" else ["glasses"])
    if name == "text":
        envelope = Envelope("telegram", "text", datetime(2026, 10, 17, tzinfo=timezone.utc),
                            Text("Speaker 1: Could you send me the calibration notes? Speaker 2: Yes, tomorrow."))
        outcome = runtime.cycle.process_text("tg-7", envelope)
        assert outcome.status == "done"
        item = "tg-7"
    else:
        runtime.cycle.run()
        item = {"pocket": "rec_001", "glasses": "gls_001"}[name]
    entry = json.loads(settings.ledger_path.read_text())[item]
    assert entry["status"] == "done" and entry["todoist_task_ids"]
    assert any(f"Pocket id: {item} #1" in args["description"] for args in todoist.tasks.values())


def test_the_glasses_stub_lists_nothing_without_a_fixture(tmp_path, clock):
    settings = settings_for(tmp_path, {"inputs": ["glasses"]}, fixtures=False)
    assert build_inputs(settings, http=httpx.Client(), clock=clock)["glasses"].list_new() == []


def test_the_cycle_never_reaches_pocket_directly():
    import ratatoskr_demo.cycle as cycle

    source = Path(cycle.__file__).read_text(encoding="utf-8")
    assert "ratatoskr_demo.pocket" not in source and "PocketClient" not in source and "PocketAdapter" not in source


# --- outputs ---

def test_with_the_todoist_output_off_nothing_is_written_and_the_wrist_still_hears(tmp_path, clock, services):
    todoist, telegram, _ = services
    settings = settings_for(tmp_path, {"inputs": ["pocket"], "outputs": ["telegram"]})
    runtime = build_runtime(settings, dry_run=False, model_call=ScriptedModel(), http=httpx.Client(), clock=clock)
    assert runtime.cycle.deps.todoist is None
    runtime.cycle.run()
    assert todoist.payloads == []
    assert json.loads(settings.ledger_path.read_text())["rec_001"]["status"] == "done"
    assert any("Email Priya" in t for t in telegram.texts)


def test_with_the_telegram_output_off_nothing_is_sent_and_holds_still_hold(tmp_path, clock, services):
    todoist, telegram, _ = services
    settings = settings_for(tmp_path, {"inputs": ["pocket"], "outputs": ["todoist"]})
    runtime = build_runtime(settings, dry_run=False, model_call=ScriptedModel(), http=httpx.Client(), clock=clock)
    assert runtime.cycle.deps.notifier is None
    runtime.cycle.run()
    assert telegram.messages == []
    entries = json.loads(settings.ledger_path.read_text())
    assert entries["rec_002"]["status"] == "hold" and entries["rec_001"]["status"] == "done"
