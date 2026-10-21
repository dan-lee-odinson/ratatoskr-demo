# Copyright 2026 Dan Lee-Odinson
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Server variants (Dan's ruling 48): Strands Decider served on the CPU and on a GPU, a matched pair in one session.

    python bench/server_variants.py --row cpu --python .venv-decider/Scripts/python.exe --conditions "..."
    python bench/server_variants.py --row cuda --python .venv-decider-cuda/Scripts/python.exe --conditions "..."

Each row starts tools/serve_decider.py itself (`--device` cpu or cuda) and times its cold start in three parts:
the environment start (process start to the launcher's first line, printed once torch is imported), the weight
load (to the first healthy /health) and the first read (one warm-up read of the first canonical task). Then,
warmed, it asks the 30 canonical tasks as written in the fixtures' `expected` blocks (labels as corrected; one
six-question read each, judged by the bench's own rules) and every source-gate window of every fixture recording
the tripwire lets through (one read each, every window asked; the verdict is the one the gate's own loop reaches).
It samples the server's host memory (resident and private) and the card's memory (nvidia-smi, the whole card, and
PyTorch's own count from /health on cuda) at load and through the run, then stops the server by its PID.

Nothing is extracted and nothing reaches Bedrock. The export, bench/server_variants_2026-10-21.json, keeps numbers
and labels only: no task text, no transcript text. bench/run_bench.py renders RESULTS.md's "Server variants"
section from it (`section()` here), so a re-render asks no model.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import platform
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
EXPORT = REPO / "bench" / "server_variants_2026-10-21.json"
ROWS = ("cpu", "cuda")
HEALTH_WAIT_SECONDS = 20 * 60
GB = 1024 ** 3


def _bench():
    """bench/run_bench.py, loaded by path (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location("run_bench_for_variants", REPO / "bench" / "run_bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _card_memory_mib() -> int | None:
    try:
        done = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10)
        return int(done.stdout.strip().splitlines()[0])
    except Exception:
        return None


class Sampler:
    """Host memory of the server process every second, the card's every two; the peaks are kept."""

    def __init__(self, pid: int) -> None:
        import psutil

        self.process, self.peak = psutil.Process(pid), {"rss": 0, "private": 0, "card_mib": 0}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def now(self) -> dict[str, Any]:
        info = self.process.memory_info()
        return {"rss": info.rss, "private": getattr(info, "private", info.rss), "card_mib": _card_memory_mib()}

    def _run(self) -> None:
        tick = 0
        while not self._stop.is_set():
            try:
                info = self.process.memory_info()
                self.peak["rss"] = max(self.peak["rss"], info.rss)
                self.peak["private"] = max(self.peak["private"], getattr(info, "private", info.rss))
                if tick % 2 == 0:
                    self.peak["card_mib"] = max(self.peak["card_mib"], _card_memory_mib() or 0)
            except Exception:
                pass
            tick += 1
            self._stop.wait(1.0)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> dict[str, int]:
        self._stop.set()
        self._thread.join(timeout=5)
        return dict(self.peak)


def _answers(response: dict[str, Any]) -> dict[str, Any]:
    """A read's answers as numbers: each Noul's p, the project's choice, confidence and option probabilities."""
    out: dict[str, Any] = {}
    for name, answer in (response.get("answers") or {}).items():
        if not isinstance(answer, dict):
            continue
        if answer.get("type") == "noul":
            out[name] = {"p": answer.get("noul")}
        elif answer.get("type") == "choice":
            out[name] = {"choice": answer.get("choice"), "confidence": answer.get("confidence"),
                         "probabilities": answer.get("probabilities")}
    return out


def _judged(judged: dict[str, Any]) -> dict[str, Any]:
    return {q: {"answer": j.answer, "confidence": j.confidence, "floor": j.floor, "value": j.value,
                "problem": j.problem, "note": j.note} for q, j in judged.items()}


def run_row(row: str, python: str, conditions: str, *, smoke: bool = False) -> dict[str, Any]:
    import httpx
    import psutil

    bench = _bench()
    from ratatoskr_demo import triage
    from ratatoskr_demo.decider import DeciderRejected, DeciderUnavailable
    from ratatoskr_demo.expected import canonical_tasks, load_expected
    from ratatoskr_demo.judge import interpret
    from ratatoskr_demo.route import load_route_questions
    from ratatoskr_demo.source_gate import _p, windows
    from ratatoskr_demo.transcript import NOT_READY, extract_text

    settings = bench.load_settings()
    questions = load_route_questions(settings)
    url = settings.route.decider_url.rstrip("/")
    try:
        httpx.get(f"{url}/health", timeout=3)
        raise SystemExit(f"a server already answers at {url}: stop it first (this row starts its own)")
    except httpx.HTTPError:
        pass
    recorded = load_expected()
    dates = {r.recording_id: r.recorded_on for r in recorded}
    tasks = [bench.BenchTask(rid, expected.task, contact, expected, recorded_on=dates[rid])
             for rid, expected, contact in canonical_tasks(recorded)][:2 if smoke else None]
    router = bench.make_router(settings, questions)
    gate = bench.make_source_gate(settings, questions)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MUTC")
    log_path = REPO / "bench" / "raw" / f"variant-{row}-{stamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    pid_file = REPO / "ledger" / f"variant-{row}.pid"
    pid_file.unlink(missing_ok=True)
    executable = str((REPO / python).resolve())  # Windows resolves a relative program against our own folder
    versions = _versions(executable, row)  # before the server starts, so the cold start is not shared with it
    started_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    t0 = time.perf_counter()
    proc = subprocess.Popen([executable, "-u", "tools/serve_decider.py", "--device", row, "--pid-file", str(pid_file),
                             "--max-commit-gb", "16"], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    marks: dict[str, float] = {}
    lines: list[str] = []

    def read_output() -> None:
        with log_path.open("w", encoding="utf-8") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                lines.append(line.rstrip())
                if line.startswith("serve_decider: pid") and "environment" not in marks:
                    marks["environment"] = time.perf_counter() - t0

    threading.Thread(target=read_output, daemon=True).start()
    failures: list[str] = []
    health_waits = 0
    try:
        while True:  # the weight load ends at the first healthy /health
            if proc.poll() is not None:
                raise SystemExit(f"the {row} server exited (code {proc.returncode}): {lines[-3:]}")
            if time.perf_counter() - t0 > HEALTH_WAIT_SECONDS:
                raise SystemExit(f"the {row} server was not healthy after {HEALTH_WAIT_SECONDS} s")
            try:
                health = httpx.get(f"{url}/health", timeout=5).json()
                break
            except (httpx.HTTPError, ValueError):
                health_waits += 1
                time.sleep(0.25)
        marks["loaded"] = time.perf_counter() - t0
        pid = int(pid_file.read_text(encoding="ascii"))
        sampler = Sampler(pid)
        at_load = sampler.now()
        sampler.start()

        def ask(task):
            started = time.perf_counter()
            try:
                response = router.ask(task.task, task.contact)
            except (DeciderUnavailable, DeciderRejected) as exc:
                failures.append(f"task read: {type(exc).__name__}")
                return {"ms": None, "error": type(exc).__name__}
            ms = (time.perf_counter() - started) * 1000
            judged = interpret(response["answers"], questions, deadline_near=task.deadline_near)
            return {"ms": ms, "answers": _answers(response), "judged": _judged(judged), "_judged": judged}

        warm = ask(tasks[0])  # the first read, cold
        marks["first_read"] = time.perf_counter() - t0
        asked = [ask(t) for t in tasks]

        gated = []
        for recording, block in bench.recordings()[:2] if smoke else bench.recordings():
            rid = str(recording["id"])
            decision = triage.decide(recording, None, settings)
            if not isinstance(decision, triage.Process):
                gated.append({"recording": rid, "triage": type(decision).__name__, "windows": []})
                continue
            text = extract_text(recording)
            if text is NOT_READY:
                continue
            reads = []
            for window in windows(str(text), gate.window_words):
                started = time.perf_counter()
                try:
                    p = _p(gate.client.systemone(gate.body(window)))
                except (DeciderUnavailable, DeciderRejected) as exc:
                    failures.append(f"gate window: {type(exc).__name__}")
                    reads.append({"p": None, "ms": None})
                    continue
                reads.append({"p": p, "ms": (time.perf_counter() - started) * 1000})
            del text
            gated.append({"recording": rid, "triage": "Process", "windows": reads,
                          "expected_sensitive": (block or {}).get("source_sensitive")})
        try:
            health_end = httpx.get(f"{url}/health", timeout=5).json()
        except httpx.HTTPError:
            health_end = {}
        peak = sampler.stop()
    finally:
        try:
            if pid_file.exists():
                psutil.Process(int(pid_file.read_text(encoding="ascii"))).kill()  # this row's server, by its PID
        except psutil.NoSuchProcess:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()  # the launcher process this row started, by its handle
    scores = bench.agreement([bench.Asked("decider", judged=a.get("_judged", {})) for a in asked], tasks)
    for a in asked + [warm]:
        a.pop("_judged", None)
    return {
        "row": row, "started": started_utc, "conditions": conditions, "python": python,
        "versions": versions, "health_at_load": health, "health_at_end": health_end,
        "cold": {"environment_s": marks.get("environment"), "weight_load_s": marks["loaded"] - marks.get("environment", 0),
                 "first_read_s": marks["first_read"] - marks["loaded"], "total_s": marks["first_read"],
                 "health_polls": health_waits},
        "memory": {"host_at_load": {k: at_load[k] for k in ("rss", "private")}, "card_mib_at_load": at_load["card_mib"],
                   "host_peak": {k: peak[k] for k in ("rss", "private")}, "card_mib_peak": peak["card_mib"]},
        "first_read": {"ms": warm.get("ms"), "same_answers_as_warmed": warm.get("answers") == asked[0].get("answers")},
        "tasks": [{"recording": t.recording_id, "n": sum(1 for u in tasks[:i] if u.recording_id == t.recording_id) + 1,
                   **a} for i, (t, a) in enumerate(zip(tasks, asked))],
        "gate": gated, "scores": scores, "failures": failures,
    }


def _versions(python: str, row: str) -> dict[str, Any]:
    probe = ("import json, torch\nd = {'torch': torch.__version__, 'cuda': torch.version.cuda}\n"
             "if torch.cuda.is_available():\n    d.update(card=torch.cuda.get_device_name(0), "
             "capability=list(torch.cuda.get_device_capability(0)))\nprint(json.dumps(d))")
    done = subprocess.run([python, "-c", probe], capture_output=True, text=True, timeout=300)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    driver = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                            capture_output=True, text=True).stdout.strip()
    out["driver"] = driver.split(",")[-1].strip() if driver else None
    out["host"] = f"{platform.processor() or platform.machine()}, {__import__('os').cpu_count()} logical cores"
    out["weights"] = "bfloat16 on the card" if row == "cuda" else "float32 (Decider upcasts its torso on the CPU)"
    return out


# --- the section in RESULTS.md, from the export alone ---

def _q(values: list[float], quantile: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, max(0, math.ceil(quantile * len(values)) - 1))]


def _ms(values: list[float | None]) -> str:
    v = [x for x in values if x is not None]
    return f"{statistics.median(v) / 1000:.2f} s / {_q(v, 0.9) / 1000:.2f} s ({len(v)})" if v else "-"


def gate_status(ps: list[float | None], floor: float) -> str:
    """The source gate's own loop (ratatoskr_demo/source_gate.py) over the windows' p, in order."""
    status = "clear"
    for p in ps:
        if p is None:
            return "unavailable"
        confidence = max(p, 1.0 - p)
        if confidence >= floor and p >= 0.5:
            return "sensitive"
        if confidence < floor:
            status = "unsure"
    return status


def _edges(question: str, floor: float) -> list[float]:
    """The values a probability can cross that change what the courier does: for a Noul p's floor, 0.5 and the
    floor's mirror (the unsure band's edges); for the project, its confidence's floor."""
    return [floor] if question == "project" else sorted({1.0 - floor, 0.5, floor})


def _p_of(task: dict[str, Any], q: str) -> float | None:
    a = (task.get("answers") or {}).get(q) or {}
    return a.get("confidence") if q == "project" else a.get("p")


def changes(cpu: dict[str, Any], gpu: dict[str, Any]) -> list[dict[str, Any]]:
    """Every task question whose answer, sure state or (sensitive) gate verdict differs, or whose probability
    crosses a floor or band edge; every gate window whose p crosses one; every recording whose verdict differs."""
    out = []
    for a, b in zip(cpu["tasks"], gpu["tasks"]):
        key = f"{a['recording']} #{a['n']}"
        for q, ja in (a.get("judged") or {}).items():
            jb = (b.get("judged") or {}).get(q) or {}
            pa, pb = _p_of(a, q), _p_of(b, q)
            reasons = []
            if ja.get("answer") != jb.get("answer"):
                reasons.append("answer")
            if (ja.get("value") is None) != (jb.get("value") is None):
                reasons.append("sure / unsure")
            if pa is not None and pb is not None and any((pa >= e) != (pb >= e) for e in _edges(q, ja["floor"])):
                reasons.append("crosses an edge")
            if reasons:
                out.append({"where": key, "question": q, "cpu": (ja.get("answer"), pa, ja.get("value")),
                            "cuda": (jb.get("answer"), pb, jb.get("value")), "why": reasons})
    for ga, gb in zip(cpu["gate"], gpu["gate"]):
        floor = cpu["gate_floor"]
        pa, pb = [w["p"] for w in ga["windows"]], [w["p"] for w in gb["windows"]]
        for i, (x, y) in enumerate(zip(pa, pb), start=1):
            if x is not None and y is not None and any((x >= e) != (y >= e) for e in _edges("sensitive", floor)):
                out.append({"where": f"{ga['recording']} window {i}", "question": "gate window",
                            "cpu": ("", x, None), "cuda": ("", y, None), "why": ["crosses an edge"]})
        if ga["windows"] and gate_status(pa, floor) != gate_status(pb, floor):
            out.append({"where": ga["recording"], "question": "gate verdict", "cpu": (gate_status(pa, floor), max(pa), None),
                        "cuda": (gate_status(pb, floor), max(pb), None), "why": ["verdict"]})
    return out


def adoption(cpu: dict[str, Any], gpu: dict[str, Any],
             labels: list[dict[str, Any]] | None = None) -> list[tuple[str, bool, str]]:
    """Ruling 48's adoption rule, line by line, against this session's CPU row; line 3 as ruling 51 amended it: a
    right-when-sure count may drop because an answer became unsure (coverage), never because one became wrong."""
    floor = cpu["gate_floor"]
    new_clear_windows = [f"{ga['recording']} window {i}" for ga, gb in zip(cpu["gate"], gpu["gate"])
                         for i, (wa, wb) in enumerate(zip(ga["windows"], gb["windows"]), start=1)
                         if wa["p"] is not None and wb["p"] is not None and wa["p"] >= 1.0 - floor > wb["p"]]
    new_clear_recordings = [ga["recording"] for ga, gb in zip(cpu["gate"], gpu["gate"]) if ga["windows"]
                            and gate_status([w["p"] for w in ga["windows"]], floor) != "clear"
                            and gate_status([w["p"] for w in gb["windows"]], floor) == "clear"]

    def holds(task):  # the courier's check after extraction: a sure yes or an unsure answer holds
        j = (task.get("judged") or {}).get("sensitive") or {}
        return not j.get("problem") and (j.get("value") is None or j.get("value") is True)

    new_clear_tasks = [f"{a['recording']} #{a['n']}" for a, b in zip(cpu["tasks"], gpu["tasks"]) if holds(a) and not holds(b)]
    projects = {f"{t['recording']} #{t['n']}": t.get("expected_project") for t in cpu["tasks"]}
    new_wrong = []
    for a, b in zip(cpu["tasks"], gpu["tasks"]):
        key = f"{a['recording']} #{a['n']}"
        ja, jb = (a.get("judged") or {}).get("project") or {}, (b.get("judged") or {}).get("project") or {}
        sure_wrong = lambda j: j.get("value") is not None and j.get("value") != projects.get(key)  # noqa: E731
        if sure_wrong(jb) and not sure_wrong(ja):
            new_wrong.append(key)
    became_wrong = [f"{w['where']} {w['question']}" for w in moves(cpu, gpu, labels)["wrong"]]
    return [
        ("no sensitive gate window or sensitive task newly cleared", not (new_clear_windows or new_clear_recordings
                                                                          or new_clear_tasks),
         ", ".join(new_clear_windows + new_clear_recordings + new_clear_tasks) or "none"),
        ("no confident project answer newly wrong", not new_wrong, ", ".join(new_wrong) or "none"),
        ("no question's right-when-sure count drops because an answer became wrong", not became_wrong,
         ", ".join(became_wrong) or "none"),
    ]


# The bench's own rules for a sure answer and for the label it is scored against (bench/run_bench.py: _sure,
# _expected_answer, SET_BY_THE_NOULS), so the amended line 3 reads the rows as their counts were made.
SET_BY_THE_NOULS = ("someone_waiting", "hard_deadline", "worth_researching_soon", "neither")


def fixture_labels() -> list[dict[str, Any]]:
    """Each canonical task's labels, in the export's task order, from the committed fixtures (as corrected)."""
    from ratatoskr_demo.expected import canonical_tasks, load_expected

    return [{"project": e.project, "priority": e.priority, "someone_waiting": e.someone_waiting,
             "hard_deadline": e.hard_deadline, "worth_researching_soon": e.worth_researching_soon,
             "real_commitment": e.real_commitment, "sensitive": e.sensitive}
            for _, e, _ in canonical_tasks(load_expected())]


def _sure(question: str, j: dict[str, Any]) -> bool:
    if question == "priority":
        return j.get("note") in SET_BY_THE_NOULS  # set by the questions, not by a default
    return j.get("confidence") is not None and j["confidence"] >= j["floor"]


def moves(cpu: dict[str, Any], gpu: dict[str, Any], labels: list[dict[str, Any]] | None = None) -> dict[str, list]:
    """Ruling 51: per task and question, an answer sure and wrong on the GPU row that was not sure and wrong on the
    CPU row (a wrong answer appearing: line 3 fails), and a sure, right answer on the CPU row that is unsure on the GPU
    row (a coverage change: listed with both probabilities and the floor, not a failure)."""
    labels = fixture_labels() if labels is None else labels
    wrong, coverage = [], []
    for a, b, label in zip(cpu["tasks"], gpu["tasks"], labels):
        for q, want in label.items():
            ja, jb = (a.get("judged") or {}).get(q), (b.get("judged") or {}).get(q)
            if want is None or not ja or not jb or ja.get("problem") or jb.get("problem"):
                continue  # not labelled, or not usable: not scored, as in the bench
            where = f"{a['recording']} #{a['n']}"
            sure_wrong_a = _sure(q, ja) and ja.get("answer") != want
            if _sure(q, jb) and jb.get("answer") != want and not sure_wrong_a:
                wrong.append({"where": where, "question": q, "cpu": ja.get("answer"), "cuda": jb.get("answer"),
                              "label": want})
            elif _sure(q, ja) and ja.get("answer") == want and not _sure(q, jb):
                coverage.append({"where": where, "question": q, "cpu_p": _p_of(a, q), "cuda_p": _p_of(b, q),
                                 "floor": ja.get("floor"), "cpu": ja.get("answer"), "cuda": jb.get("answer"),
                                 "cpu_note": ja.get("note"), "cuda_note": jb.get("note"),
                                 "same_label": ja.get("answer") == jb.get("answer")})
    return {"wrong": wrong, "coverage": coverage}


def section(data: dict[str, Any], labels: list[dict[str, Any]] | None = None) -> list[str]:
    rows = data["rows"]
    if not all(r in rows for r in ROWS):
        return []
    cpu, gpu = rows["cpu"], rows["cuda"]
    lines = ["", "## Server variants (ruling 48, 21 Oct 2026)", "",
             "Strands Decider served on this laptop's CPU (the published path, float32) and on its GPU (optional, "
             "bfloat16 weights), as a matched pair in one session, the CPU row first: the same 30 canonical tasks "
             "as written (labels as corrected) and the same source-gate windows of every fixture recording the "
             "tripwire lets through, under the frozen question wording, floors and checkpoint. Nothing extracted, "
             "nothing sent to Bedrock. Drawn from `bench/server_variants_2026-10-21.json`, written by "
             "`bench/server_variants.py`. The CPU row here is the baseline for the comparison; the CPU figures of "
             "18 October and the fresh evaluation's 11.1 s are other runs on other sets, in their own sections.",
             "", "| row | hardware |", "|---|---|"]
    for r in (cpu, gpu):
        v = r["versions"]
        card = f"{v.get('card')} (compute capability {'.'.join(map(str, v.get('capability') or []))}), " if v.get("card") else ""
        lines.append(f"| {r['row']} | {card}driver {v.get('driver')}; torch {v['torch']}, CUDA {v.get('cuda') or 'none'}; "
                     f"{v['host']}, the server capped at 4 threads; weights {v['weights']}. Conditions: "
                     f"{r['conditions']}. Started {r['started']}. |")
    lines += ["", "Cold start (the server started by the row itself):", "",
              "| row | environment start | weight load | first read | total |", "|---|---|---|---|---|"]
    for r in (cpu, gpu):
        c = r["cold"]
        lines.append(f"| {r['row']} | {c['environment_s']:.1f} s | {c['weight_load_s']:.1f} s | {c['first_read_s']:.1f} s "
                     f"| {c['total_s']:.1f} s |")
    lines += ["", "Warmed (after the first read), median / p90 (reads):", "",
              "| row | six-question routing read | gate window read | host memory at load / peak (private) | "
              "card memory at load / peak (whole card) | failures |", "|---|---|---|---|---|---|"]
    for r in (cpu, gpu):
        m = r["memory"]
        windows_ms = [w["ms"] for g in r["gate"] for w in g["windows"]]
        card = (f"{m['card_mib_at_load'] / 1024:.2f} / {m['card_mib_peak'] / 1024:.2f} GB"
                if r["row"] == "cuda" and m.get("card_mib_at_load") is not None else "-")
        lines.append(f"| {r['row']} | {_ms([t.get('ms') for t in r['tasks']])} | {_ms(windows_ms)} | "
                     f"{m['host_at_load']['private'] / GB:.2f} / {m['host_peak']['private'] / GB:.2f} GB | {card} | "
                     f"{len(r['failures'])} |")
    h = gpu.get("health_at_end") or {}
    if h.get("card_memory_peak_reserved_gb") is not None:
        lines += ["", f"PyTorch's own count on the card (cuda row, /health): {h.get('card_memory_allocated_gb')} GB "
                      f"allocated, {h.get('card_memory_peak_reserved_gb')} GB peak reserved. The card's figure above "
                      "includes the desktop's share of the card."]
    lines += ["", f"Per question, the {len(cpu['tasks'])} canonical tasks (sure / right when sure):", "",
              "| question | cpu sure | cpu right when sure | cuda sure | cuda right when sure |", "|---|---|---|---|---|"]
    for q, s in cpu["scores"].items():
        if q == "gate":
            continue
        g = gpu["scores"][q]
        lines.append(f"| {q} | {s['sure']} of {s['matched']} | {s['sure_agree']} of {s['sure']} | {g['sure']} of "
                     f"{g['matched']} | {g['sure_agree']} of {g['sure']} |")
    found = changes(cpu, gpu)
    lines += ["", f"Per-task change list: every answer, sure / unsure state or gate verdict that differs between the "
                  f"rows, and every probability that crosses a floor or band edge ({len(found)}):", ""]
    if found:
        lines += ["| where | question | cpu (answer, p, written as) | cuda (answer, p, written as) | why |",
                  "|---|---|---|---|---|"]
        for c in found:
            fmt = lambda x: f"{x[0]}, {x[1]:.3f}, {x[2]}" if x[1] is not None else f"{x[0]}, -, {x[2]}"  # noqa: E731
            lines.append(f"| {c['where']} | {c['question']} | {fmt(c['cpu'])} | {fmt(c['cuda'])} | {', '.join(c['why'])} |")
    else:
        lines.append("None: every answer, sure state and gate verdict is the same, and no probability crosses a "
                     "floor or band edge.")
    deltas = [abs(_p_of(a, q) - _p_of(b, q)) for a, b in zip(cpu["tasks"], gpu["tasks"])
              for q in (a.get("answers") or {}) if _p_of(a, q) is not None and _p_of(b, q) is not None]
    deltas += [abs(x["p"] - y["p"]) for ga, gb in zip(cpu["gate"], gpu["gate"])
               for x, y in zip(ga["windows"], gb["windows"]) if x["p"] is not None and y["p"] is not None]
    if deltas:
        lines += ["", f"Probability differences, CPU float32 against GPU bfloat16, over {len(deltas)} answers: median "
                      f"{statistics.median(deltas):.4f}, largest {max(deltas):.4f}."]
    labels = fixture_labels() if labels is None else labels
    lines += ["", "The adoption rule (ruling 48; line 3 as amended by ruling 51), against this session's CPU row:", ""]
    for name, ok, detail in adoption(cpu, gpu, labels):
        lines.append(f"- {name}: {'yes' if ok else 'NO'} ({detail})")
    lines.append("- Dan has read the change list: " + (data.get("read_by_dan") or "not yet"))
    # the questions first, then the priority they set; a yes/no answer as a word
    coverage = sorted(moves(cpu, gpu, labels)["coverage"], key=lambda c: (c["where"], c["question"] == "priority"))
    for c in coverage:
        c.update({k: {True: "yes", False: "no"}.get(c[k], c[k]) for k in ("cpu", "cuda")})
    if coverage:
        lines += ["", "Coverage changes (a sure answer that became unsure, reported under the amended line 3, not a "
                      "failure of it):", ""]
        for c in coverage:
            if c["cpu_p"] is not None and c["cuda_p"] is not None:
                lines.append(f"- {c['where']} {c['question']}: {c['cpu_p']:.4f} on the CPU against {c['cuda_p']:.4f} on "
                             f"the GPU at the {c['floor']:.2f} floor, sure to unsure, the answer {c['cpu']} on both "
                             f"rows" if c["same_label"] else
                             f"- {c['where']} {c['question']}: {c['cpu_p']:.4f} on the CPU against {c['cuda_p']:.4f} on "
                             f"the GPU at the {c['floor']:.2f} floor, sure to unsure, the answer {c['cpu']} then "
                             f"{c['cuda']}")
            else:
                lines.append(f"- {c['where']} {c['question']}: {c['cpu']} on both rows, set by {c['cpu_note']} on the "
                             f"CPU and by {c['cuda_note']} on the GPU" if c["same_label"] else
                             f"- {c['where']} {c['question']}: {c['cpu']} on the CPU, {c['cuda']} on the GPU, sure to "
                             "unsure")
    exception = data.get("exception")
    if exception:
        lines += ["", f"**The exception, recorded (ruling {exception['ruling']}).** {exception['text']}"]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--row", choices=ROWS, required=True)
    parser.add_argument("--python", required=True, help="the venv Python that serves this row")
    parser.add_argument("--conditions", default="")
    parser.add_argument("--smoke", action="store_true",
                        help="two tasks and two recordings, written to bench/raw/: the harness checked end to end")
    args = parser.parse_args(argv)
    export = REPO / "bench" / "raw" / "server_variants_smoke.json" if args.smoke else EXPORT
    data = json.loads(export.read_text(encoding="utf-8")) if export.exists() and not args.smoke else {"rows": {}}
    if args.row == "cuda" and "cpu" not in data["rows"] and not args.smoke:
        raise SystemExit("the CPU row comes first, in the same session")
    row = run_row(args.row, args.python, args.conditions, smoke=args.smoke)
    from ratatoskr_demo.expected import canonical_tasks, load_expected  # the project labels, for the adoption rule
    from ratatoskr_demo.route import load_route_questions

    bench = _bench()
    settings = bench.load_settings()
    labels = [e.project for _, e, _ in canonical_tasks(load_expected())]
    for t, project in zip(row["tasks"], labels):
        t["expected_project"] = project
    row["gate_floor"] = load_route_questions(settings)["sensitive"].min_confidence
    data["rows"][args.row] = row
    data["runner_blob"] = bench.git_blob_hash(Path(__file__))
    export.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    c = row["cold"]
    print(f"{args.row}: cold {c['total_s']:.1f} s (environment {c['environment_s']:.1f}, load {c['weight_load_s']:.1f}, "
          f"first read {c['first_read_s']:.1f}); routing {_ms([t.get('ms') for t in row['tasks']])}; failures "
          f"{len(row['failures'])}; wrote {export.name}")
    if args.smoke:
        print("\n".join(section({"rows": {"cpu": row, "cuda": row}})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
