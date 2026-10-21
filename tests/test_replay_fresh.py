"""Dan's ruling 43, Part B: bench/FRESH_EVAL_2026-10-22.md regenerates from bench/fresh_eval_2026-10-22.json alone,
byte for byte, with no network, no model and no key (this suite runs with the network refused and no .env)."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("replay_fresh", REPO / "bench" / "replay_fresh.py")
replay_fresh = importlib.util.module_from_spec(spec)
sys.modules["replay_fresh"] = replay_fresh
spec.loader.exec_module(replay_fresh)


def committed_export():
    return json.loads(replay_fresh.EXPORT.read_text(encoding="utf-8"))


def test_the_export_regenerates_every_table_of_the_committed_report_byte_for_byte():
    text = replay_fresh.replay(committed_export())
    assert text == replay_fresh.REPORT.read_text(encoding="utf-8")
    assert replay_fresh.REPLAY_LINE in text and text.count("\n|---") == 6


def test_the_export_is_the_runs_record_against_the_committed_labels():
    data = committed_export()
    labels = (REPO / "bench" / "fixtures" / "fresh" / "labels.json").read_text(encoding="utf-8")
    assert data["labels_json"] == labels
    assert replay_fresh._plain(data["labels_sha256"]) == hashlib.sha256(labels.encode("utf-8")).hexdigest()
    assert data["tag"] == "eval-freeze-2026-10-22" and len(data["conversations"]) == 24
    assert len(data["labelled_tasks"]) == 46 and all(len(t["decider"]["answers"]) == 7 for t in data["labelled_tasks"])
    every = {q: a for t in data["labelled_tasks"] for q, a in t["decider"]["answers"].items()}
    assert {"raw", "confidence", "floor", "sure", "note", "options"} <= set(every["priority"])
    assert all(c["gate_windows"] is None for c in data["conversations"]) and data["not_recorded"]
    assert "kernels: reference PyTorch implementation" in data["versions"]["hardware"]


def test_an_export_whose_labels_were_changed_is_refused():
    data = committed_export()
    data["labels_json"] = data["labels_json"].replace('"priority": "high"', '"priority": "low"', 1)
    with pytest.raises(SystemExit, match="do not match their recorded hash"):
        replay_fresh.replay(data)


def test_the_check_command_says_identical(capsys):
    assert replay_fresh.main(["--check"]) == 0
    assert "identical, byte for byte" in capsys.readouterr().out
