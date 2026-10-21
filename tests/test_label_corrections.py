"""Dan's ruling 40 (20 Oct 2026): twelve labels corrected after the bench ran (ten in the ruling, and rec_007#2's
priority and soon labels on Dan's answer of the same day), each original kept beside its correction with the date
and the reason, and the report counting the questions they touch both ways."""

import json
import pickle
import sys

import pytest

from ratatoskr_demo import expected as ex
from ratatoskr_demo.expected import as_first_labelled, load_expected
from ratatoskr_demo.judge import Judgement
from tests.conftest import make_settings
from tests.test_bench import bench, mocked_run

CORRECTED = {  # (recording, task content) -> {label: (as first labelled, as corrected)}
    ("rec_001", "Email Priya the orbital thermal bounds preprint"): {"hard_deadline": (True, False)},
    ("rec_001", "Check the Aldrin Microsystems careers page for a junior ops role"): {"hard_deadline": (True, False)},
    ("rec_001", "Email Priya next week"): {"hard_deadline": (True, False)},
    ("rec_006", "Send Tomasz the updated methods section covering how outliers were handled"):
        {"hard_deadline": (True, False)},
    ("rec_007", "Email Professor Lindqvist about the registration hold"):  # and its priority, so the rule reaches it
        {"hard_deadline": (True, False), "worth_researching_soon": (None, True), "priority": ("high", "medium")},
    ("rec_008", "Send Celeste the onboarding checklist"): {"hard_deadline": (True, False)},
    ("rec_009", "Send Rafael my résumé for the flight controller trainee role"): {"hard_deadline": (True, False)},
    ("rec_014", "Tell the session chair I'm presenting in Mara's place"):
        {"hard_deadline": (True, False), "someone_waiting": (False, True)},
    ("rec_015", "Introduce Joaquim to Hedda Nyquist about the acquisition"): {"hard_deadline": (True, False)},
}


def corrected_tasks():
    return {(r.recording_id, e.task.content): e for r in load_expected() for e in r.tasks if e.corrections}


def test_the_ten_corrections_are_in_the_fixtures_with_the_originals_dates_and_reasons():
    found = corrected_tasks()
    assert set(found) == set(CORRECTED)
    for key, e in found.items():
        assert {c.label: (c.was, getattr(e, c.label)) for c in e.corrections} == CORRECTED[key]
        assert all(c.on and c.reason.startswith("Dan's ruling 40") for c in e.corrections)
    assert sum(len(e.corrections) for e in found.values()) == 12


def test_as_first_labelled_puts_every_corrected_label_back_and_changes_nothing_else():
    chair = corrected_tasks()[("rec_014", "Tell the session chair I'm presenting in Mara's place")]
    first = as_first_labelled(chair)
    assert (first.hard_deadline, first.someone_waiting) == (True, False)
    assert (chair.hard_deadline, chair.someone_waiting) == (False, True)
    assert (first.task, first.project, first.priority, first.corrections) == (chair.task, chair.project,
                                                                              chair.priority, ())
    untouched = next(e for r in load_expected() for e in r.tasks if not e.corrections)
    assert as_first_labelled(untouched) == untouched


@pytest.mark.parametrize("bad", [{"label": "hard_deadline", "was": True, "on": "2026-10-20"},  # no reason
                                 {"label": "hard_deadline", "on": "2026-10-20", "reason": "r"},  # no original
                                 {"label": "deadline", "was": True, "on": "2026-10-20", "reason": "r"}])
def test_a_correction_without_its_original_date_or_reason_is_refused(bad):
    with pytest.raises(ValueError):
        ex._correction(bad)


def canonical_with(answer):
    """Every canonical task, and a Decider that is sure of `answer` for every hard_deadline and someone_waiting."""
    tasks = [bench.BenchTask(rid, e.task, c, e) for rid, e, c in bench.canonical_tasks(load_expected())]
    asked = [bench.Asked("decider", judged={q: Judgement(q, "p", 0.9, 0.7, answer, answer=answer)
                                            for q in ("hard_deadline", "someone_waiting")}) for _ in tasks]
    return tasks, asked


def test_the_report_counts_the_corrected_questions_both_ways():
    tasks, asked = canonical_with(False)
    run = bench.Run(canonical_tasks=tasks, canonical={"decider": asked})
    lines = bench.label_corrections(run)
    assert "## Label corrections" in lines and any("| rec_014 | Tell the session chair" in line for line in lines)
    first = [as_first_labelled(t.expected) for t in tasks]
    for q in ("hard_deadline", "someone_waiting"):
        before = sum(1 for e in first if getattr(e, q) is False)
        after = sum(1 for t in tasks if getattr(t.expected, q) is False)
        assert before != after
        row = next(line for line in lines if line.startswith(f"| {len(tasks)} canonical | decider | {q} |"))
        assert row == (f"| {len(tasks)} canonical | decider | {q} | {before}/{len(tasks)} / {len(tasks)} / "
                       f"{before}/{len(tasks)} | {after}/{len(tasks)} / {len(tasks)} / {after}/{len(tasks)} |")
    assert bench.label_corrections(bench.Run()) == []  # nothing corrected among the tasks: no section


def test_a_re_render_reads_the_labels_as_they_stand_now_and_writes_where_it_is_told(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(bench, "RESULTS", tmp_path / "RESULTS.md")
    (tmp_path / "a").mkdir()
    run, _, _, _ = mocked_run(tmp_path / "a")
    for t in run.canonical_tasks + run.tasks:  # a run saved before the corrections: the labels as first written
        t.expected = as_first_labelled(t.expected)
    state = bench.save_state(run, versions={"test": "mocked"}, spend={"total_usd": 0}, when="2026-10-19 00:00 UTC")
    out = tmp_path / "dev.md"
    assert bench.main(["--render-from", str(state), "--out", str(out), "--note", "asked on 19 Oct"]) == 0
    text = out.read_text(encoding="utf-8")
    assert not (tmp_path / "RESULTS.md").exists()  # --out: RESULTS.md untouched
    assert "## Label corrections" in text and "- asked on 19 Oct" in text
    chair = next(t for t in pickle.loads(state.read_bytes())["run"].canonical_tasks
                 if t.task.content.startswith("Tell the session chair"))
    assert chair.expected.hard_deadline is True  # the saved state is not rewritten
    bench.labels_now(run)
    assert next(t for t in run.canonical_tasks if t.task.content.startswith("Tell the session chair")
                ).expected.hard_deadline is False
