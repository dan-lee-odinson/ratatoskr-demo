"""Dan's ruling 15 (18 Oct 2026, restated): the project options with and without their one-line descriptions,
side by side, on the fixtures and on the live run's real tasks. The Decider state does not change: it has
always carried the guarded task and the contact line."""

import json
import re
from pathlib import Path

import httpx
import pytest

import ratatoskr_demo.cli as cli
from ratatoskr_demo.config import Settings
from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.route import load_route_questions, without_option_descriptions
from tests.conftest import (
    REC_001_TASKS, SENTINEL, ScriptedModel, make_settings, planted_fixtures, runtime_for,
)
from tests.test_bench import bench, mocked_run

REPO = Path(__file__).resolve().parents[1]
QUESTIONS = load_route_questions(Settings())


# --- (a): the options already carry prompts/context.md's descriptions -----------------------------------------

def test_every_area_in_context_md_is_a_project_option_with_its_description():
    context = (REPO / "ratatoskr_demo" / "prompts" / "context.md").read_text(encoding="utf-8")
    areas = dict(re.findall(r"^- (\w+): (.+)$", context, re.M))
    options = QUESTIONS["project"].criteria
    assert areas and all(name.lower() in options for name in areas)
    for name, description in areas.items():  # the same words, give or take "and" and commas
        words = set(re.findall(r"[a-z]+", description.lower())) - {"and", "a", "the", "its", "with", "being"}
        assert words <= set(re.findall(r"[a-z]+", options[name.lower()].lower())) | {"built"}, name


# --- (b): the switch ---------------------------------------------------------------------------------------

def test_without_option_descriptions_leaves_each_option_its_name_and_every_other_question_as_written():
    off = without_option_descriptions(QUESTIONS)
    assert off["project"].criteria == {name: name for name in QUESTIONS["project"].criteria}
    assert all(off[q] == QUESTIONS[q] for q in QUESTIONS if q != "project")
    assert off["project"].defaults == QUESTIONS["project"].defaults


def test_the_setting_switches_the_loaded_questions_and_is_a_true_or_false(tmp_path):
    settings = make_settings(tmp_path, {"route": {"option_descriptions": False}})
    assert load_route_questions(settings)["project"].criteria["research"] == "research"
    assert load_route_questions(make_settings(tmp_path / "b"))["project"].criteria["research"].startswith("Papers")
    with pytest.raises(ConfigurationError, match="true or false"):
        make_settings(tmp_path / "c", {"route": {"option_descriptions": "no"}})


# --- the bench, both ways -----------------------------------------------------------------------------------

def is_names_only(body):
    criteria = body["questions"]["project"]["criteria"]
    return all(key == value for key, value in criteria.items())


def test_both_asks_every_backend_twice_and_the_report_sets_them_side_by_side(tmp_path):
    run, text, mantle, decider = mocked_run(tmp_path, option_descriptions="both")
    canonical = len(run.canonical_tasks)
    assert set(run.canonical_off) == {"decider", "bedrock"} and len(run.canonical_off["decider"]) == canonical
    names_only = [b for b in decider.bodies if is_names_only(b)]
    assert len(names_only) == canonical + len(run.tasks)  # the second pass, and only that one, without descriptions
    assert "## With and without the project options' descriptions (the canonical tasks)" in text
    assert "## With and without the project options' descriptions (the extracted tasks that matched)" in text
    assert "Project options: asked both ways, side by side below." in text


def test_off_asks_every_project_question_with_the_names_alone(tmp_path):
    run, text, mantle, decider = mocked_run(tmp_path, option_descriptions="off")
    assert decider.bodies and all(is_names_only(b) for b in decider.bodies)
    assert "Project options: their names alone, no descriptions." in text


def test_the_real_tasks_are_asked_both_ways_and_reported_as_counts_without_their_text(tmp_path):
    real = tmp_path / "real-tasks.jsonl"
    real.write_text(json.dumps({
        "recording": "pocket-live-1", "recording_at": "2026-10-18T14:01:00Z",
        "contact": {"name": "Priya", "organisation": "Aldrin Microsystems", "role": None, "context": "a meetup"},
        "tasks": REC_001_TASKS}) + "\n", encoding="utf-8")
    tasks = bench.load_real_tasks([real])
    assert [t.task.content for t in tasks] == [t["content"] for t in REC_001_TASKS]
    assert str(tasks[0].recorded_on) == "2026-10-18" and tasks[0].expected is None
    run, text, _, _ = mocked_run(tmp_path, option_descriptions="both", real_tasks=tasks)
    assert len(run.real["decider"]) == len(run.real_off["decider"]) == 3
    assert "## Real tasks (the live run's 3, re-run)" in text
    section = text.split("## Real tasks")[1].split("\n## ")[0]
    assert "| decider | project | 3/3 | 3/3 | 0 |" in section
    assert not any(t["content"] in section for t in REC_001_TASKS)  # counts, never the task text


def test_the_estimate_counts_the_second_pass_and_the_real_tasks(tmp_path):
    one = bench.estimate(make_settings(tmp_path))
    both = bench.estimate(make_settings(tmp_path), option_descriptions="both", real_tasks=5)
    assert both["extraction_worst_usd"] == one["extraction_worst_usd"]  # extracted once, asked twice
    assert both["routing_calls_at_most"] == 2 * one["routing_calls_at_most"] + 10
    assert both["bedrock_calls_at_most"] == one["bedrock_calls_at_most"] + one["routing_calls_at_most"] + 10
    assert both["routing_worst_usd"] > 2 * one["routing_worst_usd"]


# --- the CLI: the real-task set is captured in a dry run ----------------------------------------------------------

def test_save_tasks_writes_each_guarded_extraction_and_never_the_transcript(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(cli, "REAL_TASKS_DIR", tmp_path / "raw")
    settings = make_settings(tmp_path, fixtures=planted_fixtures(tmp_path))
    runtime = cli.build_runtime(settings, dry_run=True, model_call=ScriptedModel(), http=httpx.Client(), clock=clock)
    runtime.cycle.deps.out = lambda line: None
    path = cli.save_tasks(runtime)
    runtime.cycle.run()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["recording"] for r in rows] == ["rec_001", "rec_005"]
    assert [t["content"] for t in rows[0]["tasks"]] == [t["content"] for t in REC_001_TASKS]
    assert SENTINEL not in path.read_text(encoding="utf-8")  # the transcript's planted word never reaches the file
    assert path.parent == tmp_path / "raw"


def test_the_cli_takes_the_demo_text_through_the_same_dry_run_and_saves_its_tasks(tmp_path, clock, monkeypatch,
                                                                                 capsys):
    monkeypatch.setattr(cli, "REAL_TASKS_DIR", tmp_path / "raw")
    settings = make_settings(tmp_path)
    real_build, seen = cli.build_runtime, []

    def build(s, dry_run):
        seen.append(s)
        return real_build(s, dry_run=True, model_call=ScriptedModel(), http=httpx.Client(), clock=clock)

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_runtime", build)
    code = cli.main(["--once", "--dry-run", "--save-tasks", "--option-descriptions", "off",
                     "--demo-text", "I promised to email Priya the preprint by Friday, and the slides too"])
    out = capsys.readouterr().out
    assert code == 0 and "demo text: dry-run" in out
    assert seen[0].route.option_descriptions is False
    [saved] = (tmp_path / "raw").glob("real-tasks-*.jsonl")
    assert "demo-cli" in [json.loads(line)["recording"] for line in saved.read_text(encoding="utf-8").splitlines()]


def test_save_tasks_reads_the_recordings_already_done_again_and_leaves_the_ledger_as_it_was(
        tmp_path, clock, monkeypatch, services):
    """On 19 Oct the live run's recordings are done in the ledger; the dry run must still take them through."""
    monkeypatch.setattr(cli, "REAL_TASKS_DIR", tmp_path / "raw")
    settings = make_settings(tmp_path, fixtures=planted_fixtures(tmp_path))
    live = runtime_for(settings, ScriptedModel(), clock)
    live.cycle.deps.out = lambda line: None
    live.cycle.run()
    ledger = settings.ledger_path.read_bytes()
    assert {rid for rid, e in live.state.ledger_entries().items() if e.status == "done"} >= {"rec_001", "rec_005"}

    model = ScriptedModel()
    dry = cli.build_runtime(settings, dry_run=True, model_call=model, http=httpx.Client(), clock=clock)
    dry.cycle.deps.out = lambda line: None
    path = cli.save_tasks(dry)
    dry.cycle.run()
    assert [json.loads(line)["recording"] for line in path.read_text(encoding="utf-8").splitlines()] == [
        "rec_001", "rec_005"]
    assert settings.ledger_path.read_bytes() == ledger

    again = runtime_for(settings, model := ScriptedModel(), clock)  # outside a dry run, done stays done
    again.cycle.revisit_done = True
    again.cycle.deps.out = lambda line: None
    again.cycle.run()
    assert model.calls == [] and settings.ledger_path.read_bytes() == ledger


def test_save_tasks_and_demo_text_need_a_dry_run(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--once", "--save-tasks"])
    assert "--save-tasks and --demo-text go with --once --dry-run" in capsys.readouterr().err
