"""Dan's ruling 35: notebooks/gatekeeper_triage.ipynb runs end to end with no Decider reachable (recorded
mode), with no error cell, and the bench table it prints is bench/RESULTS.md's. Skipped without nbclient
(`pip install -e ".[notebook]"`)."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")

REPO = Path(__file__).resolve().parents[1]
NOTEBOOK = REPO / "notebooks" / "gatekeeper_triage.ipynb"
NAMES = ("decider", "bedrock", "jev")


RUN = ("import sys, nbformat, nbclient\n"
       "nb = nbformat.read(sys.argv[1], as_version=4)\n"
       "nbclient.NotebookClient(nb, timeout=300, kernel_name='python3',\n"
       "                        resources={'metadata': {'path': sys.argv[3]}}).execute()\n"
       "nbformat.write(nb, sys.argv[2])\n")


def execute(tmp_path, notebook=NOTEBOOK):
    """Runs a temp copy of the notebook from notebooks/ (so it finds the repository), with DECIDER_URL on a port
    nothing listens on: the probe fails and the notebook replays the recorded answers. In a subprocess, so the
    kernel client's threads never outlive it in this process (test_status counts threads)."""
    copy, executed = tmp_path / notebook.name, tmp_path / f"executed-{notebook.name}"
    shutil.copy(notebook, copy)
    started = time.perf_counter()
    done = subprocess.run([sys.executable, "-c", RUN, str(copy), str(executed), str(REPO / "notebooks")],
                          env={**os.environ, "DECIDER_URL": "http://127.0.0.1:9"}, capture_output=True, text=True,
                          timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]
    return nbformat.read(executed, as_version=4), time.perf_counter() - started


def outputs(nb):
    for cell in nb.cells:
        for out in cell.get("outputs", []):
            yield cell, out


def results_table():
    """RESULTS.md's canonical-task table, read independently of the notebook: (backend, question) -> (sure, right)."""
    text = (REPO / "bench" / "RESULTS.md").read_text(encoding="utf-8")
    section = text.split("## Agreement on the canonical tasks (no extraction)")[1].split("\n## ")[0]
    table = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0] in NAMES and cells[3]:
            table[(cells[0], cells[1])] = (cells[3], cells[4])
    return table


def fresh_table():
    """FRESH_EVAL's Decider rows on the ordinary set, read independently of the notebook: question -> (sure, right)."""
    text = (REPO / "bench" / "FRESH_EVAL_2026-10-22.md").read_text(encoding="utf-8")
    section = text.split("## Every question on the labelled tasks")[1].split("\n## ")[0]
    table = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0].startswith("ordinary") and cells[3]:
            table[cells[1]] = (cells[3], cells[4])
    return table


def test_the_notebook_runs_in_recorded_mode_with_no_error_and_prints_the_bench_s_own_numbers(tmp_path):
    nb, seconds = execute(tmp_path)
    assert not [out for _, out in outputs(nb) if out.get("output_type") == "error"]
    text = "\n".join(out.get("text", "") for _, out in outputs(nb) if out.get("output_type") == "stream")
    assert "Mode: RECORDED" in text and "Mode: LIVE" not in text
    assert "priority high, set by someone_waiting" in text and "set by default:waiting" in text
    markdown = [out["data"]["text/markdown"] for _, out in outputs(nb) if "text/markdown" in out.get("data", {})]
    [bench] = [m for m in markdown if m.startswith("| question | Strands Decider sure")]
    printed = {}
    for line in bench.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        for i, name in enumerate(NAMES):
            printed[(name, cells[0])] = (cells[1 + 2 * i], cells[2 + 2 * i])
    assert printed == results_table() and len(printed) == 21
    [fresh] = [m for m in markdown if m.startswith("| question | baseline: sure")]  # section 9 (ruling 39)
    shown = {}
    for line in fresh.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        shown[cells[0]] = ((cells[1], cells[2]), (cells[3], cells[4]))
    baseline = {q: v for (name, q), v in results_table().items() if name == "decider"}
    assert shown == {q: (baseline[q], f) for q, f in fresh_table().items()} and len(shown) >= 7
    assert any("labels.json" in m and "eval-freeze-2026-10-22" in m for m in markdown)  # the run's metadata
    assert "the same as the committed report: True" in text  # section 9's replay cell (ruling 43)
    [gate] = [m for m in markdown if "p(sensitive)" in m]
    assert "| rec_001 |" in gate and "**proceed**" in gate and "**hold** (source-sensitive)" in gate
    print(f"recorded mode: {seconds:.1f} s")
