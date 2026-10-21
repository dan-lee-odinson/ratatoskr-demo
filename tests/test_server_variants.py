"""Dan's ruling 48: the server variants' section. The gate verdict is the gate's own loop, the change list catches a
changed answer, a changed sure state and a probability crossing a floor or band edge, the adoption rule's lines
follow the ruling, and RESULTS.md's section is the one drawn from the committed export."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("server_variants", REPO / "bench" / "server_variants.py")
variants = importlib.util.module_from_spec(spec)
sys.modules["server_variants"] = variants
spec.loader.exec_module(variants)


def task(recording, n, *, project="work", confidence=0.9, sensitive=0.1, label="work", ms=1000.0, waiting=0.9):
    def noul(p, floor=0.6):
        sure = max(p, 1 - p) >= floor
        return {"answer": p >= 0.5, "confidence": max(p, 1 - p), "floor": floor, "value": (p >= 0.5) if sure else None,
                "problem": None, "note": None}
    return {"recording": recording, "n": n, "ms": ms, "expected_project": label,
            "answers": {"project": {"choice": project, "confidence": confidence}, "sensitive": {"p": sensitive},
                        "someone_waiting": {"p": waiting}},
            "judged": {"project": {"answer": project, "confidence": confidence, "floor": 0.7,
                                   "value": project if confidence >= 0.7 else None, "problem": None, "note": None},
                       "sensitive": noul(sensitive), "someone_waiting": noul(waiting, floor=0.7)}}


def labels(n, *, project="work", waiting=True):
    """The fixtures' labels for n synthetic tasks: the project, not sensitive, someone waiting."""
    return [{"project": project, "sensitive": False, "someone_waiting": waiting}] * n


def row(name, tasks, gate):
    scores = {"project": {"matched": len(tasks), "agree": 0, "sure": 0, "sure_agree": 0, "usable": len(tasks)},
              "gate": {"matched": 0, "agree": 0}}
    for t in tasks:
        j = t["judged"]["project"]
        scores["project"]["sure"] += j["value"] is not None
        scores["project"]["sure_agree"] += j["value"] is not None and j["value"] == t["expected_project"]
    return {"row": name, "started": "2026-10-21 21:00 UTC", "conditions": "idle", "gate_floor": 0.6,
            "versions": {"torch": "2.14.1", "cuda": "13.0" if name == "cuda" else None, "driver": "610.74",
                         "host": "a laptop", "weights": "w", **({"card": "a card", "capability": [12, 0]}
                                                                  if name == "cuda" else {})},
            "cold": {"environment_s": 5.0, "weight_load_s": 30.0, "first_read_s": 3.0, "total_s": 38.0, "health_polls": 9},
            "memory": {"host_at_load": {"rss": 2 ** 30, "private": 2 ** 31}, "card_mib_at_load": 5000,
                       "host_peak": {"rss": 2 ** 30, "private": 2 ** 31}, "card_mib_peak": 5200},
            "tasks": tasks, "gate": gate, "scores": scores, "failures": [], "health_at_end": {}}


GATE = [{"recording": "rec_001", "triage": "Process", "windows": [{"p": 0.2, "ms": 900.0}, {"p": 0.3, "ms": 800.0}]},
        {"recording": "rec_002", "triage": "Hold", "windows": []}]


def test_the_gate_verdict_is_the_gates_own_loop():
    assert variants.gate_status([0.1, 0.2], 0.6) == "clear"
    assert variants.gate_status([0.1, 0.5], 0.6) == "unsure"
    assert variants.gate_status([0.45, 0.9, 0.1], 0.6) == "sensitive"
    assert variants.gate_status([0.1, None], 0.6) == "unavailable"


def test_identical_rows_have_no_changes_and_pass_the_adoption_rule():
    cpu = row("cpu", [task("rec_001", 1), task("rec_001", 2)], GATE)
    gpu = copy.deepcopy(cpu) | {"row": "cuda"}
    assert variants.changes(cpu, gpu) == []
    assert [ok for _, ok, _ in variants.adoption(cpu, gpu, labels(2))] == [True, True, True]
    text = "\n".join(variants.section({"rows": {"cpu": cpu, "cuda": gpu}}, labels(2)))
    assert "## Server variants (ruling 48" in text and "None: every answer" in text
    assert "Dan has read the change list: not yet" in text and "| cuda | " in text


def test_a_floor_crossing_and_a_new_confident_misfiling_are_listed_and_fail_the_rule():
    cpu = row("cpu", [task("rec_001", 1, sensitive=0.65), task("rec_001", 2, project="work", confidence=0.6)], GATE)
    gpu = row("cuda", [task("rec_001", 1, sensitive=0.55),  # sure yes -> unsure: still held, but listed
                       task("rec_001", 2, project="home", confidence=0.8)], GATE)  # unsure -> sure and wrong
    found = {(c["where"], c["question"]): c["why"] for c in variants.changes(cpu, gpu)}
    assert "crosses an edge" in found[("rec_001 #1", "sensitive")] and "sure / unsure" in found[("rec_001 #1", "sensitive")]
    assert "answer" in found[("rec_001 #2", "project")]
    rule = {name: (ok, detail) for name, ok, detail in variants.adoption(cpu, gpu, labels(2))}
    assert rule["no sensitive gate window or sensitive task newly cleared"][0] is True  # unsure still holds
    assert rule["no confident project answer newly wrong"] == (False, "rec_001 #2")


def test_a_gate_window_newly_cleared_fails_the_rule():
    cpu = row("cpu", [task("rec_001", 1)], [{"recording": "rec_003", "triage": "Process",
                                              "windows": [{"p": 0.45, "ms": 900.0}]}])
    gpu = row("cuda", [task("rec_001", 1)], [{"recording": "rec_003", "triage": "Process",
                                               "windows": [{"p": 0.35, "ms": 400.0}]}])
    rule = {name: ok for name, ok, _ in variants.adoption(cpu, gpu, labels(1))}
    assert rule["no sensitive gate window or sensitive task newly cleared"] is False
    assert {c["question"] for c in variants.changes(cpu, gpu)} == {"gate window", "gate verdict"}


# --- line 3 as ruling 51 amended it ---

LINE_3 = "no question's right-when-sure count drops because an answer became wrong"


def test_a_wrong_answer_appearing_fails_line_3():
    cpu = row("cpu", [task("rec_001", 1, waiting=0.9)], GATE)  # sure yes, and someone is waiting: right
    gpu = row("cuda", [task("rec_001", 1, waiting=0.1)], GATE)  # sure no: wrong
    rule = {name: (ok, detail) for name, ok, detail in variants.adoption(cpu, gpu, labels(1))}
    assert rule[LINE_3] == (False, "rec_001 #1 someone_waiting")
    assert variants.moves(cpu, gpu, labels(1))["coverage"] == []


def test_a_sure_to_unsure_move_at_the_floor_is_coverage_and_does_not_fail_line_3():
    cpu = row("cpu", [task("rec_008", 2, waiting=0.7003)], GATE)  # sure yes at the 0.70 floor
    gpu = row("cuda", [task("rec_008", 2, waiting=0.6979)], GATE)  # unsure, the same answer
    rule = {name: (ok, detail) for name, ok, detail in variants.adoption(cpu, gpu, labels(1))}
    assert rule[LINE_3] == (True, "none")
    [moved] = variants.moves(cpu, gpu, labels(1))["coverage"]
    assert (moved["where"], moved["question"], moved["same_label"]) == ("rec_008 #2", "someone_waiting", True)
    text = "\n".join(variants.section({"rows": {"cpu": cpu, "cuda": gpu}}, labels(1)))
    assert ("- rec_008 #2 someone_waiting: 0.7003 on the CPU against 0.6979 on the GPU at the 0.70 floor, sure to "
            "unsure, the answer yes on both rows") in text


@pytest.mark.skipif(not variants.EXPORT.exists(), reason="the matched pair has not been run yet")
def test_results_md_carries_the_section_drawn_from_the_committed_export():
    data = json.loads(variants.EXPORT.read_text(encoding="utf-8"))
    results = (REPO / "bench" / "RESULTS.md").read_text(encoding="utf-8")
    assert "\n".join(variants.section(data)) in results


@pytest.mark.skipif(not variants.EXPORT.exists(), reason="the matched pair has not been run yet")
def test_the_recorded_exception_matches_the_rows_and_the_amended_rule_passes():
    data = json.loads(variants.EXPORT.read_text(encoding="utf-8"))
    cpu, gpu = data["rows"]["cpu"], data["rows"]["cuda"]
    assert all(ok for _, ok, _ in variants.adoption(cpu, gpu))
    moved = {(c["where"], c["question"]): c for c in variants.moves(cpu, gpu)["coverage"]}
    waiting = moved[("rec_008 #2", "someone_waiting")]
    text = data["exception"]["text"]
    assert f"{waiting['cpu_p']:.4f}" in text and f"{waiting['cuda_p']:.4f}" in text and "0.70 floor" in text
    assert moved[("rec_008 #2", "priority")]["cpu"] == moved[("rec_008 #2", "priority")]["cuda"] == "high"
    assert data["read_by_dan"].startswith("yes")
