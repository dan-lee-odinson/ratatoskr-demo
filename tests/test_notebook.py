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


def test_the_notebook_runs_in_recorded_mode_with_no_error_and_prints_the_bench_s_own_numbers(tmp_path):
    nb, seconds = execute(tmp_path)
    assert not [out for _, out in outputs(nb) if out.get("output_type") == "error"]
    text = "\n".join(out.get("text", "") for _, out in outputs(nb) if out.get("output_type") == "stream")
    assert "Mode: RECORDED" in text and "Mode: LIVE" not in text
    assert "priority high, set by someone_waiting" in text and "set by default:waiting" in text
    markdown = [out["data"]["text/markdown"] for _, out in outputs(nb) if "text/markdown" in out.get("data", {})]
    [bench] = [m for m in markdown if "right when sure" in m]
    printed = {}
    for line in bench.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        for i, name in enumerate(NAMES):
            printed[(name, cells[0])] = (cells[1 + 2 * i], cells[2 + 2 * i])
    assert printed == results_table() and len(printed) == 21
    [gate] = [m for m in markdown if "p(sensitive)" in m]
    assert "| rec_001 |" in gate and "**proceed**" in gate and "**hold** (source-sensitive)" in gate
    print(f"recorded mode: {seconds:.1f} s")
