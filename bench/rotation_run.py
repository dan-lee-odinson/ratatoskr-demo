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

"""The option-order rotation on its own: written task by task, and safe against a Strands Decider hang.

    python bench/rotation_run.py                     measure 7 orders; writes bench/raw/rotation-<stamp>.jsonl
    python bench/rotation_run.py --resume FILE       carry on after the last task FILE holds
    python bench/run_bench.py --rotations 0 --rotation-from FILE
                                                     the bench then reports this rotation in RESULTS.md

Why (BUILD_REPORT, "Decider hang (finding)"): Strands Decider 0.1.0 hung three times on 17 Oct 2026,
each well into a long run of reads, and the bench writes its results only at the end, so each hang
cost the run. Here:

- each task's picks (one per order) are appended to the .jsonl file and flushed as soon as that task
  is done, so a hang costs at most the task in progress;
- this script runs its own Decider server (tools/serve_decider.py: 4 threads, below-normal priority),
  checks /health before every task, and when /health does not answer or a read fails, stops the
  server by the exact PID in its pid file, starts a fresh one and asks that task again (three tries);
- it also restarts the server every 10 tasks, before memory builds up (the hangs came after hundreds
  of reads, with the server's commit far above its start-up size), and after each such scheduled
  restart asks the previous task again in every order on the fresh server and records how many
  picks match (the determinism check);
- it records the server's working set, commit and CPU after every start and every task (event rows
  carry a "kind"; task rows do not);
- a resumed run checks that the file was made for today's canonical tasks and project options, in
  order, and skips the tasks already written.

It never touches a server it did not start: it refuses to begin while something already answers on
the Decider URL, and it stops only a PID whose command line is tools/serve_decider.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ratatoskr_demo.config import load_settings  # noqa: E402
from ratatoskr_demo.decider import DeciderRejected, DeciderUnavailable, make_router  # noqa: E402
from ratatoskr_demo.errors import ConfigurationError  # noqa: E402
from ratatoskr_demo.expected import canonical_tasks, load_expected  # noqa: E402
from ratatoskr_demo.judge import interpret  # noqa: E402
from ratatoskr_demo.rotation import rotations  # noqa: E402
from ratatoskr_demo.route import load_route_questions  # noqa: E402
from ratatoskr_demo.runlock import refuse_while_loop_runs  # noqa: E402

RAW_DIR = REPO / "bench" / "raw"
RESTART_EVERY = 10  # tasks (70 reads at 7 orders) per server
HEALTH_TIMEOUT = 15.0
MAX_TRIES = 3
MAX_COMMIT_GB = 16  # tools/serve_decider.py --max-commit-gb: the server exits past it, and is restarted
READ_TIMEOUT = 600.0  # as the bench: a slow read finishes instead of being abandoned inside Decider
DECIDER_PYTHON = REPO / ".venv-decider" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


class Server(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def healthy(self) -> bool: ...


class Stopped(RuntimeError):
    """A task failed MAX_TRIES times; the file keeps every task done so far (resume it)."""


def header(options: list[str], n: int, instructions: str, conditions: str = "") -> dict[str, Any]:
    return {"kind": "rotation", "orders": n, "options": options, "project_instructions": instructions,
            "conditions": conditions, "read_timeout_s": READ_TIMEOUT, "restart_every": RESTART_EVERY,
            "max_commit_gb": MAX_COMMIT_GB, "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def read_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The header and the task rows; event rows (starts, determinism checks) have a "kind" and are left out."""
    lines = read_all(path)
    return lines[0], [row for row in lines[1:] if "kind" not in row]


def read_all(path: Path) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or lines[0].get("kind") != "rotation":
        raise ConfigurationError(f"{path}: not a rotation file")
    return lines


def append(out: Path, row: dict[str, Any]) -> None:
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_resumable(head: dict[str, Any], done: list[dict[str, Any]], options: list[str], n: int,
                    keys: list[tuple[str, str]]) -> None:
    if head.get("orders") != n or head.get("options") != options:
        raise ConfigurationError("the rotation file was made with other orders or project options; start a new one")
    if [(row["recording"], row["task"]) for row in done] != keys[:len(done)]:
        raise ConfigurationError("the rotation file's tasks are not today's canonical tasks; start a new one")


def measure(keys: list[tuple[str, str]], options: list[str], n: int, ask: Callable[[int, list[str]], str | None],
            server: Server, out: Path, *, done: int = 0, restart_every: int = RESTART_EVERY,
            log: Callable[[str], None] = print, memory: Callable[[], dict[str, Any]] = dict,
            previous: list[str | None] | None = None) -> int:
    """Ask every task from `done` on, appending one line per task to `out`. Returns the number of restarts.
    `previous` is the picks of task `done - 1` (on a resume), for the first determinism check."""
    orders = rotations(options, n)
    restarts, served = 0, 0

    def restart(index: int, why: str) -> None:
        nonlocal restarts, served
        server.stop()
        server.start()
        restarts, served = restarts + 1, 0
        append(out, {"kind": "start", "reason": why, "before_task": index, "at": now(), "decider_memory": memory()})

    def determinism(index: int, recorded: list[str | None]) -> None:
        """The previous task, asked again in every order on the freshly started server."""
        try:
            fresh = [ask(index, order) for order in orders]
        except (DeciderUnavailable, DeciderRejected) as exc:
            append(out, {"kind": "determinism", "task_index": index, "error": str(exc), "at": now()})
            log(f"determinism check on task {index + 1}: Decider failed ({exc})")
            return
        matches = sum(a == b for a, b in zip(recorded, fresh))
        append(out, {"kind": "determinism", "task_index": index, "recorded": recorded, "fresh": fresh,
                     "matches": matches, "of": len(orders), "at": now(), "decider_memory": memory()})
        log(f"determinism check on task {index + 1}: {matches}/{len(orders)} picks the same after the restart")

    for index in range(done, len(keys)):
        for attempt in range(1, MAX_TRIES + 1):
            scheduled = served >= restart_every
            if scheduled or not server.healthy():
                why = f"scheduled, after {served} task(s)" if scheduled else "no answer from /health"
                log(f"restarting Decider before task {index + 1} ({why})")
                restart(index, why)
                if scheduled and previous is not None:
                    determinism(index - 1, previous)
            try:
                picks = [ask(index, order) for order in orders]
                break
            except (DeciderUnavailable, DeciderRejected) as exc:
                log(f"task {index + 1}, try {attempt}: Decider failed ({exc}); restarting it")
                restart(index, f"a read failed ({exc})")
        else:
            raise Stopped(f"task {index + 1} failed {MAX_TRIES} times; resume with --resume {out}")
        rid, content = keys[index]
        append(out, {"index": index, "recording": rid, "task": content, "choices": picks, "at": now(),
                     "server_tasks": served + 1, "decider_memory": memory()})  # per task: growth vs starvation
        previous = picks
        served += 1
        log(f"task {index + 1}/{len(keys)} written ({sum(p is not None for p in picks)}/{len(picks)} answered)")
    return restarts


class LocalDecider:
    """tools/serve_decider.py, started by this script and stopped only by the exact PID it wrote."""

    def __init__(self, url: str, pid_file: Path, log_file: Path, *, start_timeout: float = 900.0,
                 command_line: Callable[[int], str] | None = None, kill: Callable[[int], None] | None = None) -> None:
        self.url = url.rstrip("/")
        self.pid_file = pid_file
        self.log_file = log_file
        self.start_timeout = start_timeout
        self.process: subprocess.Popen[bytes] | None = None
        self._command_line = command_line or _command_line
        self._kill = kill or _kill

    def healthy(self) -> bool:
        try:
            return httpx.get(self.url + "/health", timeout=HEALTH_TIMEOUT).status_code == 200
        except httpx.HTTPError:
            return False

    def start(self) -> None:
        self.pid_file.unlink(missing_ok=True)
        with self.log_file.open("ab") as log:
            self.process = subprocess.Popen([str(DECIDER_PYTHON), "tools/serve_decider.py", "--pid-file",
                                             str(self.pid_file), "--max-commit-gb", str(MAX_COMMIT_GB)],
                                            cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ConfigurationError(f"Decider exited while starting; see {self.log_file}")
            if self.pid_file.exists() and self.pid_file.read_text(encoding="ascii").strip() and self.healthy():
                return
            time.sleep(3)
        raise ConfigurationError(f"Decider did not answer /health within {self.start_timeout:.0f} s")

    def stop(self) -> None:
        pid = self.pid()
        if pid is not None and "serve_decider.py" in self._command_line(pid):
            self._kill(pid)
            for _ in range(30):
                if "serve_decider.py" not in self._command_line(pid):
                    break
                time.sleep(1)
        if self.process is not None:
            try:
                self.process.wait(timeout=30)  # the venv launcher exits with its child
            except subprocess.TimeoutExpired:
                pass
            self.process = None

    def memory(self) -> dict[str, Any]:
        """The server's working set and commit (GB) and CPU seconds so far; {} if it cannot be read."""
        pid = self.pid()
        if pid is None or os.name != "nt":
            return {}
        done = subprocess.run(["powershell", "-NoProfile", "-Command",
                               f"$p = Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; if ($p) {{ "
                               "'{0} {1} {2}' -f $p.WorkingSet64, $p.PrivateMemorySize64, $p.CPU }"],
                              capture_output=True, text=True)
        try:
            ws, commit, cpu = done.stdout.split()
            return {"working_set_gb": round(int(ws) / 1024 ** 3, 2), "commit_gb": round(int(commit) / 1024 ** 3, 2),
                    "cpu_s": round(float(cpu.replace(",", ".")), 1)}
        except ValueError:
            return {}

    def pid(self) -> int | None:
        try:
            return int(self.pid_file.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None


def _command_line(pid: int) -> str:
    """The process's command line, or "" if there is no such process."""
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            return ""
    done = subprocess.run(["powershell", "-NoProfile", "-Command",
                           f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                          capture_output=True, text=True)
    return done.stdout.strip()


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/F"], capture_output=True)
    else:
        os.kill(int(pid), 9)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--orders", type=int, default=7, help="project-option orders per task (default 7)")
    parser.add_argument("--resume", type=Path, default=None, help="a rotation file to carry on")
    parser.add_argument("--conditions", default="", help="what else was (not) running on the machine")
    args = parser.parse_args(argv)

    settings = load_settings()
    refuse_while_loop_runs(settings.paths.state_dir, "the rotation run")
    settings = replace(settings, route=replace(settings.route, decider_timeout_seconds=READ_TIMEOUT))
    questions = load_route_questions(settings)
    options = list(questions["project"].criteria)
    tasks = canonical_tasks(load_expected())
    keys = [(rid, expected.task.content) for rid, expected, _ in tasks]
    server = LocalDecider(settings.route.decider_url, settings.paths.state_dir / "decider.pid",
                          settings.paths.state_dir / "decider-serve.log")
    if server.healthy():
        raise ConfigurationError(f"a Decider is already answering at {settings.route.decider_url}; "
                                 "this run starts and stops its own, so stop that one first")
    if args.resume is not None:
        out = args.resume
        head, done_rows = read_file(out)
        check_resumable(head, done_rows, options, args.orders, keys)
        done = len(done_rows)
        print(f"resuming {out.name}: {done} of {len(keys)} tasks already written")
    else:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        out = RAW_DIR / f"rotation-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%MUTC')}.jsonl"
        out.write_text(json.dumps(header(options, args.orders, str(questions["project"].instructions),
                                         args.conditions), ensure_ascii=False) + "\n", encoding="utf-8")
        done = 0
    router = make_router(settings, questions)

    def ask(index: int, order: list[str]) -> str | None:
        _, expected, contact = tasks[index]
        response = router.ask(expected.task, contact, project_order=order)  # raises DeciderUnavailable on a hang
        judged = interpret(response["answers"], questions)["project"]
        return None if judged.problem else judged.answer

    print(f"rotation: {len(keys)} canonical tasks x {args.orders} orders -> {out}")
    server.start()
    append(out, {"kind": "start", "reason": "first start" if done == 0 else "resumed", "before_task": done,
                 "at": now(), "decider_memory": server.memory()})
    previous = done_rows[-1]["choices"] if args.resume is not None and done_rows else None
    try:
        restarts = measure(keys, options, args.orders, ask, server, out, done=done, memory=server.memory,
                           previous=previous)
    except Stopped as exc:
        print(f"STOPPED: {exc}")
        return 1
    finally:
        server.stop()
    print(f"done: {out} ({restarts} restart(s) besides the first start)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
