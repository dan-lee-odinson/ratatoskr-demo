"""The route seam: its boundaries, the backends, route.toml, and the Todoist wiring."""

import tomllib
from dataclasses import replace
from datetime import date
from importlib import resources

import pytest

from ratatoskr_demo.config import RouteSettings, Settings
from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.extract import parse_extraction
from ratatoskr_demo.route import RoutedTask, load_route_questions, route, validate_route_questions
from ratatoskr_demo.todoist import build_task_args
from tests.conftest import REC_001_TASKS, SENTINEL, ScriptedModel, extraction_json, make_settings, planted_fixtures, runtime_for

EXTRACTION = parse_extraction(extraction_json(tasks=REC_001_TASKS))
NONE = Settings(route=RouteSettings(backend="none"))


def test_route_is_identity_when_backend_is_none():
    routed = route(EXTRACTION, NONE)
    assert routed.contact == EXTRACTION.contact
    assert routed.confidence == EXTRACTION.confidence
    assert [r.task for r in routed.tasks] == list(EXTRACTION.tasks)
    assert all(r.project is None and r.priority is None and r.real_commitment is None for r in routed.tasks)
    assert not any(r.is_routed for r in routed.tasks)


def test_route_never_receives_transcript_text(tmp_path, clock, services, monkeypatch):
    """By sentinel: run the whole cycle and record everything that reaches route()."""
    import ratatoskr_demo.cycle as cycle_module

    reached, days = [], []
    real_route = cycle_module.route

    def spy(extraction, settings, router=None, *, today=None):
        reached.append(extraction)
        days.append(today)
        return real_route(extraction, settings, router, today=today)

    monkeypatch.setattr(cycle_module, "route", spy)
    settings = make_settings(tmp_path, fixtures=planted_fixtures(tmp_path))
    runtime_for(settings, ScriptedModel(), clock).cycle.run()

    assert len(reached) == 2  # rec_001 and rec_005
    # "today" for the priority rule is each recording's own date, never the wall clock (ruling 7)
    assert sorted(days) == [date(2026, 9, 12), date(2026, 9, 14)]
    for extraction in reached:
        assert SENTINEL not in repr(extraction)
        assert "Speaker 1" not in repr(extraction)


def test_route_refuses_anything_but_an_extraction():
    with pytest.raises(TypeError):
        route("Speaker 1: a raw transcript", NONE)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        route({"transcript": "raw pocket record"}, NONE)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        route("Speaker 1: a raw transcript", Settings(), object())  # type: ignore[arg-type]


def test_route_never_alters_task_text():
    before = [(t.content, t.notes, t.due_string) for t in EXTRACTION.tasks]
    routed = route(EXTRACTION, NONE)
    after = [(t.content, t.notes, t.due_string) for t in routed.tasks]
    assert after == before  # same text, same order, same count
    assert [(t.content, t.notes, t.due_string) for t in EXTRACTION.tasks] == before  # input untouched


def test_unrouted_tasks_are_written_to_todoist_unchanged(tmp_path, clock, services):
    todoist, _telegram, _router = services
    settings = make_settings(tmp_path)
    runtime_for(settings, ScriptedModel(), clock).cycle.run()

    rec_001 = [a for a in todoist.tasks.values() if "Pocket id: rec_001" in a["description"]]  # in write order
    assert [a["content"] for a in rec_001] == [t["content"] for t in REC_001_TASKS]
    for args in rec_001:
        assert args["labels"] == ["pocket", "ungated"]  # no proj: label; routing off is ungated (ruling 37)
        assert "priority" not in args  # Todoist default
        assert not args["content"].startswith("[maybe]")


def test_an_unknown_backend_fails_at_startup(tmp_path):
    with pytest.raises(ConfigurationError, match="gpt-router"):
        make_settings(tmp_path, {"route": {"backend": "gpt-router"}})
    with pytest.raises(ConfigurationError):
        make_settings(tmp_path, {"route": {"backend": "Jev"}})  # names are exact
    assert make_settings(tmp_path, {"route": {"backend": "jev"}}).route.backend == "jev"
    assert make_settings(tmp_path, {"route": {"backend": "decider"}}).route.backend == "decider"


def test_a_routing_backend_without_a_router_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="no router"):
        route(EXTRACTION, Settings())  # the default backend, decider, with no router built


def test_an_unknown_backend_exits_the_cli_with_a_configuration_error(tmp_path, monkeypatch):
    from ratatoskr_demo.cli import EX_CONFIG, main

    config = tmp_path / "config.toml"
    config.write_text('[route]\nbackend = "gpt-router"\n', encoding="utf-8")
    monkeypatch.setenv("RATATOSKR_CONFIG", str(config))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert main(["--status"]) == EX_CONFIG


def test_route_toml_loads_and_each_entry_has_instructions_criteria_min_confidence():
    questions = load_route_questions(Settings())
    names = ("project", "someone_waiting", "hard_deadline", "worth_researching_soon", "real_commitment", "sensitive")
    assert set(questions) == set(names)
    assert [questions[n].type for n in names] == ["choice", "noul", "noul", "noul", "noul", "noul"]
    for q in questions.values():
        assert q.instructions and q.criteria and 0 < q.min_confidence <= 1
    assert list(questions["project"].criteria) == [
        "research", "school", "work", "writing", "community", "personal", "other"]
    assert questions["project"].defaults == {
        "research": "medium", "school": "high", "work": "high", "writing": "low", "community": "medium",
        "personal": "medium", "other": "low"}
    for name in names[1:]:
        assert set(questions[name].criteria) == {"true", "false"}
    assert questions["someone_waiting"].instructions == (  # Dan's ruling 7, word for word
        "Is a specific person — named, or identified by relationship or role (your sister, my boss, the client) — "
        "expecting a reply or a deliverable from the owner, whether or not a date was given?")
    assert questions["hard_deadline"].instructions == (
        "Is there a stated date, time or event by which this must be done, after which it is late or the chance "
        "is gone?")
    assert questions["worth_researching_soon"].instructions == (
        "Is this worth investigating or following up on in the next few weeks even though nobody is waiting on a "
        "reply by a date?")
    assert questions["sensitive"].criteria["false"].startswith("Ordinary professional follow-ups")
    # Dan's ruling 7b: "the owner", never a name, in every question text and yes/no description
    for q in questions.values():
        assert "Dan" not in str(q.instructions) and not any("Dan" in str(v) for v in q.criteria.values()), q.name
    assert [questions[n].min_confidence for n in names] == [0.7, 0.7, 0.7, 0.7, 0.7, 0.6]


def test_route_toml_documents_the_noul_confidence_rule():
    text = resources.files("ratatoskr_demo").joinpath("prompts", "route.toml").read_text(encoding="utf-8")
    assert "max(p, 1 - p)" in text
    import ratatoskr_demo.route as route_module
    assert "max(p, 1 - p)" in route_module.__doc__


def test_route_toml_matches_the_typesafe_question_shapes():
    """Each entry builds a real typesafe-sdk question (a runtime dependency since v1.1)."""
    import typesafe_sdk as sdk
    raw = tomllib.loads(resources.files("ratatoskr_demo").joinpath("prompts", "route.toml").read_text(encoding="utf-8"))
    sdk.Choice(instructions=raw["project"]["instructions"], criteria=raw["project"]["criteria"])
    for name in ("someone_waiting", "hard_deadline", "worth_researching_soon", "real_commitment", "sensitive"):
        sdk.Noul(instructions=raw[name]["instructions"], criteria=sdk.NoulCriteria(**raw[name]["criteria"]))


@pytest.mark.parametrize("breakage, message", [
    (lambda r: r.pop("someone_waiting"), "exactly the entries"),
    (lambda r: r.pop("hard_deadline"), "exactly the entries"),
    (lambda r: r.update(waiting_by_date=r["someone_waiting"]), "exactly the entries"),  # ruling 2's question is gone
    (lambda r: r["project"].pop("min_confidence"), "missing"),
    (lambda r: r.update(priority={"type": "score"}), "exactly the entries"),  # the old Score is gone
    (lambda r: r["worth_researching_soon"].update(type="score"), "type must be"),
    (lambda r: r.pop("sensitive"), "exactly the entries"),
    (lambda r: r["project"].pop("defaults"), "defaults"),
    (lambda r: r["project"]["defaults"].pop("personal"), "missing \\['personal'\\]"),
    (lambda r: r["project"]["defaults"].update(research="urgent"), "must be one of"),
    (lambda r: r["sensitive"]["criteria"].pop("true"), "must have `true`"),
    (lambda r: r["sensitive"]["criteria"].update(maybe="x"), "must have `true`"),
    (lambda r: r["real_commitment"].update(defaults={}), "unknown key"),
    (lambda r: r["real_commitment"].update(type="choice"), "type must be"),
    (lambda r: r["project"]["criteria"].update({"two words": {"what": "x"}}), "one word"),
    (lambda r: r["project"].update(min_confidence=1.5), "min_confidence"),
])
def test_a_broken_route_toml_is_a_configuration_error(breakage, message):
    raw = tomllib.loads(resources.files("ratatoskr_demo").joinpath("prompts", "route.toml").read_text(encoding="utf-8"))
    breakage(raw)
    with pytest.raises(ConfigurationError, match=message):
        validate_route_questions(raw)


def test_a_broken_route_toml_in_prompts_dir_fails_at_startup(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "route.toml").write_text("[project]\ntype = 'choice'\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_route_questions(make_settings(tmp_path))


# --- the downstream wiring: annotations become a label, a priority, a prefix ---

def _args(task):
    return build_task_args(task, recording_id="rec_9", ordinal=1, contact=EXTRACTION.contact,
                           project_id="p1", label="pocket")


def test_a_project_annotation_adds_a_proj_label_and_stays_in_the_inbox():
    args = _args(RoutedTask(task=EXTRACTION.tasks[0], project="research"))
    assert args["labels"] == ["pocket", "proj:research"]
    assert args["project_id"] == "p1"


def test_a_priority_annotation_is_passed_to_todoist():
    assert _args(RoutedTask(task=EXTRACTION.tasks[0], priority=4))["priority"] == 4


def test_real_commitment_false_prefixes_maybe_in_the_writer_only():
    task = RoutedTask(task=EXTRACTION.tasks[0], real_commitment=False)
    assert _args(task)["content"] == "[maybe] " + EXTRACTION.tasks[0].content
    assert task.content == EXTRACTION.tasks[0].content  # the routed task itself is unchanged


def test_real_commitment_true_leaves_content_alone():
    assert _args(RoutedTask(task=EXTRACTION.tasks[0], real_commitment=True))["content"] == EXTRACTION.tasks[0].content


def test_the_trace_never_reaches_todoist_and_does_not_count_for_equality():
    from ratatoskr_demo.judge import Judgement

    trace = (Judgement("project", "research", 0.9, 0.7, "research"),)
    task = RoutedTask(task=EXTRACTION.tasks[0], project="research", trace=trace)
    assert task == RoutedTask(task=EXTRACTION.tasks[0], project="research")
    assert "0.9" not in str(_args(task)) and "Judgement" not in str(_args(task))


def test_every_router_reads_the_same_state():
    from ratatoskr_demo.jev import routing_state as jev_state
    from ratatoskr_demo.route import routing_state

    task, contact = EXTRACTION.tasks[0], EXTRACTION.contact
    assert routing_state(task, contact) == jev_state(task, contact)
    assert set(routing_state(task, contact)) == {"task", "contact"}


# --- priority_rule: three Nouls, the due date and the project default (Dan's ruling 7, 17 Oct 2026) ---

DEFAULTS = {"research": "medium", "school": "high", "writing": "low"}


@pytest.mark.parametrize("waiting, deadline, near, soon, project, expected", [
    (True, None, True, None, None, (4, "someone_waiting")),           # 1. waiting yes -> high, whatever else
    (True, False, False, False, "writing", (4, "someone_waiting")),
    (False, True, True, None, None, (4, "hard_deadline")),            # 2. a near deadline yes -> high
    (None, True, True, None, "writing", (4, "hard_deadline")),        #    a sure yes decides the high step
    (False, True, False, True, "research", (3, "worth_researching_soon")),  # a far deadline counts as no
    (False, True, False, False, "school", (2, "neither")),            #    ... so March is low (rec_001 #2)
    (None, True, False, False, "writing", (2, "default:waiting")),    #    far is not yes: waiting unsure decides
    (None, False, True, True, "research", (3, "default:waiting")),    # the walk stops at an unsure answer
    (False, None, True, True, "school", (4, "default:deadline")),
    (False, None, False, True, "research", (3, "worth_researching_soon")),  # unsure but far: it cannot count
    (False, False, True, True, "research", (3, "worth_researching_soon")),  # 3. soon yes -> medium
    (False, False, True, None, "writing", (2, "default:soon")),       #    soon unsure -> the default
    (False, False, True, False, "school", (2, "neither")),            # 4. low, not the project default
    (None, None, True, None, None, (None, "default:waiting")),        # no project: no priority, source kept
])
def test_the_priority_rule_in_order(waiting, deadline, near, soon, project, expected):
    from ratatoskr_demo.route import priority_rule

    assert priority_rule(waiting, deadline, near, soon, project, DEFAULTS) == expected


def test_every_priority_source_is_named():
    from ratatoskr_demo.route import PRIORITY_SOURCES

    assert PRIORITY_SOURCES == ("someone_waiting", "hard_deadline", "worth_researching_soon", "neither",
                                "default:waiting", "default:deadline", "default:soon")


def test_every_canonical_task_s_labels_give_its_expected_priority_under_the_rule():
    """The fixtures' three priority labels and due dates, put through the rule, give the expected priority."""
    from ratatoskr_demo.due import near
    from ratatoskr_demo.expected import load_expected
    from ratatoskr_demo.route import TODOIST_PRIORITY, priority_rule

    defaults = load_route_questions(Settings())["project"].defaults
    for r in load_expected():
        for t in r.tasks:
            level, source = priority_rule(t.someone_waiting, t.hard_deadline, near(t.task.due_string, r.recorded_on),
                                          t.worth_researching_soon, t.project, defaults)
            assert level == TODOIST_PRIORITY[t.priority], (r.recording_id, t.task.content, source)
