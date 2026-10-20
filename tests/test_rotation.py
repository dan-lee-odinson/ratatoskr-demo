"""The option-order test, the canonical tasks it runs over, --decider-check, and --inspect-shape."""

import httpx
import pytest
import respx

from ratatoskr_demo.config import Settings
from ratatoskr_demo.expected import canonical_tasks, load_expected, match_task
from ratatoskr_demo.extract import Task
from ratatoskr_demo.rotation import FLIP_THRESHOLD, mitigated_order, rotations, run_rotation
from ratatoskr_demo.route import load_route_questions
from tests.test_decider import URL, attach, decider_answers

OPTIONS = ["research", "school", "work", "writing", "community", "personal", "other"]


def test_n_rotations_list_each_option_first_once_when_n_is_the_option_count():
    orders = rotations(OPTIONS, 7)
    assert [order[0] for order in orders] == OPTIONS
    assert all(sorted(order) == sorted(OPTIONS) for order in orders)
    with pytest.raises(ValueError):
        rotations(OPTIONS, 8)


def test_a_model_that_always_picks_the_first_option_flips_every_task():
    report = run_rotation(OPTIONS, 5, 7, lambda index, order: order[0])
    assert report.flip_rate == 1.0 and report.first_pick_rate == 1.0 and report.needs_mitigation


def test_a_model_that_ignores_the_order_never_flips():
    report = run_rotation(OPTIONS, 5, 7, lambda index, order: "research")
    assert report.flip_rate == 0.0 and not report.needs_mitigation
    assert report.first_picks == 5  # "research" was listed first in one order out of seven, for each task


def test_the_threshold_is_ten_percent_and_the_mitigation_is_other_first_then_alphabetical():
    assert FLIP_THRESHOLD == 0.10
    assert mitigated_order(OPTIONS) == ["other", "community", "personal", "research", "school", "work", "writing"]
    report = run_rotation(OPTIONS, 10, 2, lambda index, order: order[0] if index == 0 else "work")
    assert report.flip_rate == 0.1 and not report.needs_mitigation  # exactly at the threshold is not above it


def test_every_canonical_task_matches_only_itself():
    for recording in load_expected():
        for expected in recording.tasks:
            assert match_task(expected.task, recording.tasks) is expected, expected.task.content


def test_the_canonical_tasks_cover_every_project_a_pleasantry_and_sensitive_cases():
    tasks = canonical_tasks(load_expected())
    assert {e.project for _, e, _ in tasks} == set(OPTIONS)
    assert sum(not e.real_commitment for _, e, _ in tasks) >= 1
    assert sum(e.sensitive for _, e, _ in tasks) >= 2


def test_match_words_survive_rewording_and_an_unrelated_task_matches_nothing():
    rec_001 = [r for r in load_expected() if r.recording_id == "rec_001"][0]
    reworded = Task("Send Priya my paper by Friday", None, None)
    assert match_task(reworded, rec_001.tasks).project == "research"
    assert match_task(Task("Buy milk", None, None), rec_001.tasks) is None


# --- the CLI checks, against a mocked local Decider ---

def test_rotate_options_reports_the_flip_rate(tmp_path):
    from ratatoskr_demo.cli import rotate_options
    from ratatoskr_demo.decider import DeciderClient, DeciderRouter

    questions = load_route_questions(Settings())
    lines = []
    with respx.mock(assert_all_called=False) as router:
        fake = attach(router)

        def first_listed(body):
            first = next(iter(body["questions"]["project"]["criteria"]))
            return httpx.Response(200, json=decider_answers(project=(first, 0.9)))

        fake.reply = first_listed
        decider = DeciderRouter(DeciderClient(URL, http=httpx.Client()), questions)
        assert rotate_options(Settings(), questions, 3, out=lines.append, router=decider) == 0
    assert lines[0].startswith("option rotation:") and "MITIGATE" in lines[0]
    assert len(fake.bodies) == 3 * len(canonical_tasks(load_expected()))


def test_decider_check_prints_the_four_answers_and_what_was_expected():
    from ratatoskr_demo.cli import decider_check
    from ratatoskr_demo.decider import DeciderClient, DeciderRouter

    questions = load_route_questions(Settings())
    lines = []
    with respx.mock(assert_all_called=False) as router:
        fake = attach(router)
        decider = DeciderRouter(DeciderClient(URL, http=httpx.Client()), questions)
        assert decider_check(Settings(), questions, out=lines.append, router=decider) == 0
    assert len(fake.bodies) == 1
    assert lines[1] == "task:    Email Priya the orbital thermal bounds preprint"
    assert lines[2].startswith("decider: project research 0.91")
    assert lines[3] == "expected: project research, priority high, commitment True, sensitive False"


def test_decider_check_says_plainly_when_decider_is_not_up():
    from ratatoskr_demo.cli import decider_check
    from ratatoskr_demo.decider import DeciderClient, DeciderRouter

    questions = load_route_questions(Settings())
    lines = []
    with respx.mock() as router:
        router.route(host="127.0.0.1").mock(side_effect=httpx.ConnectError("refused"))
        decider = DeciderRouter(DeciderClient(URL, http=httpx.Client()), questions)
        assert decider_check(Settings(), questions, out=lines.append, router=decider) == 1
    assert "not usable" in lines[-1]


# --- --inspect-shape: names and types, never values ---

def test_the_shape_names_keys_and_types_and_never_a_value():
    from ratatoskr_demo.shape import describe

    recording = {"id": "rec_real", "title": "A private title", "duration": 312, "tags": [],
                 "transcript": [{"speaker": "Speaker 1", "text": "something private", "start": 0.5},
                                {"text": "more private words", "end": 9}],
                 "summarizations": None}
    lines = describe(recording, "data")
    text = "\n".join(lines)
    for value in ("rec_real", "A private title", "312", "Speaker 1", "something private", "0.5", "9"):
        assert value not in text
    assert "  transcript: list[object]" in lines
    assert "    [].text: str" in lines
    assert "    [].speaker: str (in some elements)" in lines
    assert "  summarizations: null" in lines and "  tags: list[empty]" in lines
