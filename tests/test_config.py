"""Config loading fails loud; secrets never print; dry run writes nothing."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from ratatoskr_demo.cli import EX_CONFIG, build_runtime, main
from ratatoskr_demo.config import StopTerm, load_settings, require_secrets
from ratatoskr_demo.errors import ConfigurationError
from tests.conftest import FIXTURES, ScriptedModel, make_settings

REPO = Path(__file__).resolve().parents[1]


def test_the_shipped_demo_config_loads(tmp_path):
    settings = load_settings({"RATATOSKR_CONFIG": str(REPO / "config.toml")})
    assert settings.model.model_id == "openai.gpt-oss-20b" and settings.model.max_tokens == 2000
    assert settings.model.base_url == "https://bedrock-mantle.us-east-1.api.aws/v1"
    assert (settings.model.price_in_per_m, settings.model.price_out_per_m) == (0.07, 0.30)
    assert settings.route.backend == "decider" and settings.route.min_confidence == 0.7
    assert settings.route.decider_url == "http://127.0.0.1:8000"
    assert settings.route.decider_model == "StrandsAgents/strands-decider-2B-hobson-v21"
    assert (settings.poll.interval_seconds, settings.poll.lookback_days) == (30, 1)
    assert settings.spend.hard_stop_usd == 5.0
    assert settings.todoist.inbox_project_name == "Ratatoskr Demo" and settings.todoist.label == "pocket"
    assert settings.triage.stop_list == (StopTerm("vargrheim", ("vargrime",)),)


def test_the_code_default_route_backend_is_strands_decider():
    from ratatoskr_demo.config import Settings

    assert Settings().route.backend == "decider"


def test_the_decider_backend_does_not_need_the_typesafe_key(tmp_path):
    settings = load_settings({"RATATOSKR_CONFIG": str(REPO / "config.toml"), "POCKET_API_KEY": "p",
                              "TODOIST_API_TOKEN": "t", "TELEGRAM_BOT_TOKEN": "b", "TELEGRAM_CHAT_ID": "1",
                              "BEDROCK_API_KEY": "k"})
    require_secrets(settings)  # no TYPESAFE_API_KEY, and none needed
    with pytest.raises(ConfigurationError, match="BEDROCK_API_KEY"):
        require_secrets(make_settings(tmp_path, env={"BEDROCK_API_KEY": ""}))


@pytest.mark.parametrize("model_id", ["openai.gpt-oss-120b", "google.gemma-4-31b", "anthropic.claude"])
def test_no_model_but_gpt_oss_20b_can_be_configured(tmp_path, model_id):
    with pytest.raises(ConfigurationError, match="model_id"):
        make_settings(tmp_path, {"model": {"model_id": model_id}})


@pytest.mark.parametrize("url", ["https://bedrock-runtime.us-east-1.amazonaws.com/v1", "http://bedrock-mantle.us-east-1.api.aws/v1",
                                 "https://evil.example/v1"])
def test_the_bedrock_base_url_must_be_an_https_mantle_url(tmp_path, url):
    with pytest.raises(ConfigurationError, match="base_url"):
        make_settings(tmp_path, {"model": {"base_url": url}})


def test_the_decider_url_must_be_this_machine(tmp_path):
    with pytest.raises(ConfigurationError, match="decider_url"):
        make_settings(tmp_path, {"route": {"decider_url": "http://10.0.0.5:8000"}})
    assert make_settings(tmp_path, {"route": {"decider_url": "http://localhost:8000"}})


@pytest.mark.parametrize("limit", [5.01, 50, 0, -1])
def test_the_hard_stop_can_be_lowered_but_never_raised_past_five_dollars(tmp_path, limit):
    with pytest.raises(ConfigurationError, match="hard_stop_usd"):
        make_settings(tmp_path, {"spend": {"hard_stop_usd": limit}})
    assert make_settings(tmp_path, {"spend": {"hard_stop_usd": 1.5}}).spend.hard_stop_usd == 1.5


def test_the_env_file_is_read_and_wins_over_the_process_environment(tmp_path, monkeypatch):
    from ratatoskr_demo.config import runtime_environment

    env_file = tmp_path / "x.env"
    env_file.write_text("# comment\nBEDROCK_API_KEY=from-the-file\nexport TELEGRAM_CHAT_ID='77'\n", encoding="utf-8")
    monkeypatch.setenv("RATATOSKR_ENV_FILE", str(env_file))
    monkeypatch.setenv("BEDROCK_API_KEY", "from-the-shell")
    env = runtime_environment()
    assert env["BEDROCK_API_KEY"] == "from-the-file" and env["TELEGRAM_CHAT_ID"] == "77"


def test_a_malformed_env_file_line_is_reported_by_number_never_by_content(tmp_path):
    from ratatoskr_demo.config import read_env_file

    env_file = tmp_path / "bad.env"
    env_file.write_text("GOOD=1\nthis line holds a s3cret-value\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as info:
        read_env_file(env_file)
    assert "line 2" in str(info.value) and "s3cret" not in str(info.value)


def test_an_unknown_section_is_an_error(tmp_path):
    with pytest.raises(ConfigurationError, match="section"):
        make_settings(tmp_path, {"rout": {"backend": "none"}})


def test_an_unknown_key_is_an_error(tmp_path):
    with pytest.raises(ConfigurationError, match="backnd"):
        make_settings(tmp_path, {"route": {"backnd": "jev"}})


def test_a_wrong_type_is_an_error(tmp_path):
    with pytest.raises(ConfigurationError):
        make_settings(tmp_path, {"poll": {"interval_seconds": "often"}})


def test_stop_list_entries_are_terms_or_term_tables_with_spellings(tmp_path):
    settings = make_settings(tmp_path, {"triage": {"stop_list": [
        "acme", {"term": "vargrheim", "spellings": ["vargrime", "var grime"]}, {"term": "zenith"}]}})
    assert settings.triage.stop_list == (StopTerm("acme"), StopTerm("vargrheim", ("vargrime", "var grime")),
                                         StopTerm("zenith"))


@pytest.mark.parametrize("entry", [{"term": "x", "spelings": ["y"]}, {"spellings": ["y"]}, {"term": "x", "spellings": "y"},
                                   {"term": "x", "spellings": [""]}, {"term": " "}, 7])
def test_a_malformed_stop_list_entry_is_an_error(tmp_path, entry):
    with pytest.raises(ConfigurationError, match="stop_list"):
        make_settings(tmp_path, {"triage": {"stop_list": [entry]}})


def test_placeholder_secrets_are_refused(tmp_path):
    settings = make_settings(tmp_path, env={"TODOIST_API_TOKEN": "REPLACE_ME"})
    with pytest.raises(ConfigurationError, match="TODOIST_API_TOKEN"):
        require_secrets(settings)


def test_secrets_never_appear_in_repr(tmp_path):
    settings = make_settings(tmp_path)
    assert "test-todoist-token" not in repr(settings)


def test_a_missing_config_file_exits_with_ex_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RATATOSKR_CONFIG", str(tmp_path / "nope.toml"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert main(["--status"]) == EX_CONFIG


def test_dry_run_prints_decisions_and_tasks_and_creates_nothing(tmp_path):
    settings = make_settings(tmp_path)
    printed = []
    with respx.mock(assert_all_mocked=True) as router:  # any network call would fail the test
        runtime = build_runtime(settings, dry_run=True, model_call=ScriptedModel(), http=httpx.Client())
        runtime.cycle.deps.out = printed.append
        runtime.cycle.run()
        assert router.calls.call_count == 0
    output = "\n".join(printed)
    assert "rec_001" in output and "Process" in output
    assert "would create: Email Priya the orbital thermal bounds preprint" in output
    assert "rec_002" in output and "would hold: stoplist:vargrheim" in output
    assert "rec_003" in output and "Skip (keep)" in output
    assert "rec_004" in output and "NotReady" in output
    assert "would create: Met Lena — no follow-ups" in output
    assert not settings.paths.state_dir.exists()  # no ledger, no retry file, nothing


def test_scrub_list_and_mark_scrubbed_from_the_command_line(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    state.mkdir()
    (state / "processed.json").write_text(json.dumps({
        "rec_1": {"status": "done", "title": "Meetup", "recording_at": "2026-09-01T00:00:00Z",
                  "decided_at": "2026-09-01T00:00:00Z", "reason": None, "todoist_task_ids": [],
                  "released_by_ok": False, "scrubbable": True}}))
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("RATATOSKR_CONFIG", str(config))
    monkeypatch.setenv("RATATOSKR_STATE_DIR", str(state))
    assert main(["--scrub-list"]) == 0
    assert "rec_1  Meetup" in capsys.readouterr().out
    assert main(["--mark-scrubbed", "rec_1"]) == 0
    main(["--scrub-list"])
    assert "Nothing to scrub." in capsys.readouterr().out
