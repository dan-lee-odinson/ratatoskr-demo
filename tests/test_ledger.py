"""§8 ledger: round trip, atomic write, scrub list."""

import json

import pytest

from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.ledger import Ledger, LedgerEntry


def entry(status="done", at="2026-09-12T00:00:00Z", **kw):
    return LedgerEntry(status=status, title=f"t-{at}", recording_at=at, decided_at=at, **kw)


def test_entries_survive_a_reload(tmp_path):
    path = tmp_path / "processed.json"
    Ledger(path).put("rec_1", entry(todoist_task_ids=["a", "b"], scrubbable=True))
    reloaded = Ledger(path).get("rec_1")
    assert reloaded.todoist_task_ids == ["a", "b"] and reloaded.scrubbable


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    Ledger(tmp_path / "processed.json").put("rec_1", entry())
    assert [p.name for p in tmp_path.iterdir()] == ["processed.json"]


def test_the_ledger_file_has_the_documented_shape(tmp_path):
    path = tmp_path / "processed.json"
    Ledger(path).put("rec_1", entry(reason=None))
    data = json.loads(path.read_text())
    assert set(data["rec_1"]) == {"status", "title", "recording_at", "decided_at", "reason",
                                  "todoist_task_ids", "released_by_ok", "scrubbable",
                                  "sensitivity_not_checked"}


def test_the_scrub_list_is_done_entries_oldest_first(tmp_path):
    ledger = Ledger(tmp_path / "processed.json")
    ledger.put("new", entry(at="2026-09-14T00:00:00Z", scrubbable=True))
    ledger.put("old", entry(at="2026-09-01T00:00:00Z", scrubbable=True))
    ledger.put("kept", entry(status="keep", at="2026-08-01T00:00:00Z"))
    assert [rid for rid, _ in ledger.scrub_list()] == ["old", "new"]


def test_mark_scrubbed_removes_ids_from_the_scrub_list_and_reports_unknown_ones(tmp_path):
    ledger = Ledger(tmp_path / "processed.json")
    ledger.put("a", entry(scrubbable=True))
    assert ledger.mark_scrubbed(["a", "zzz"]) == ["zzz"]
    assert Ledger(tmp_path / "processed.json").scrub_list() == []


def test_an_unreadable_ledger_refuses_to_start_rather_than_reprocess(tmp_path):
    path = tmp_path / "processed.json"
    path.write_text("{ half a file", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        Ledger(path)


def test_an_unknown_status_is_rejected():
    with pytest.raises(ValueError):
        entry(status="maybe")
