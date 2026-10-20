"""bench/rotation_run.py: the rotation written task by task, surviving a Strands Decider hang (17 Oct 2026)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import respx
import httpx

from ratatoskr_demo.config import Settings
from ratatoskr_demo.decider import DeciderUnavailable
from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.expected import canonical_tasks, load_expected
from ratatoskr_demo.route import load_route_questions

REPO = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rr = _load("rotation_run", REPO / "bench" / "rotation_run.py")
bench = _load("run_bench_for_rotation_tests", REPO / "bench" / "run_bench.py")
QUESTIONS = load_route_questions(Settings())
OPTIONS = list(QUESTIONS["project"].criteria)
KEYS = [(rid, expected.task.content) for rid, expected, _ in canonical_tasks(load_expected())]


class FakeServer:
    def __init__(self, healthy=True):
        self.starts = self.stops = 0
        self.health = healthy

    def start(self):
        self.starts += 1
        self.health = True

    def stop(self):
        self.stops += 1

    def healthy(self):
        return self.health


def new_file(tmp_path, n=7):
    out = tmp_path / "rotation.jsonl"
    out.write_text(json.dumps(rr.header(OPTIONS, n, str(QUESTIONS["project"].instructions))) + "\n", encoding="utf-8")
    return out


def lines(path):
    return rr.read_file(path)[1]


def test_each_task_is_written_as_soon_as_it_is_done(tmp_path):
    out = new_file(tmp_path)
    held_when_first_asked = {}

    def ask(index, order):
        held_when_first_asked.setdefault(index, len(lines(out)))  # every earlier task is already in the file
        return "research"

    rr.measure(KEYS, OPTIONS, 7, ask, FakeServer(), out, log=lambda line: None)
    assert len(lines(out)) == len(KEYS)
    assert held_when_first_asked == {i: i for i in range(len(KEYS))}
    assert all(row["choices"] == ["research"] * 7 for row in lines(out))


def test_a_failed_read_restarts_decider_and_asks_that_task_again(tmp_path):
    out = new_file(tmp_path)
    server, failed = FakeServer(), []

    def ask(index, order):
        if index == 3 and not failed:
            failed.append(index)
            raise DeciderUnavailable("ReadTimeout")
        return "work"

    restarts = rr.measure(KEYS[:5], OPTIONS, 7, ask, server, out, log=lambda line: None)  # under 10: no routine restart
    assert [row["index"] for row in lines(out)] == list(range(5))  # task 4 once, not twice
    assert (server.stops, server.starts, restarts) == (1, 1, 1)  # the failure, and only it, restarted Decider


def test_no_answer_from_health_restarts_decider_before_the_task(tmp_path):
    out = new_file(tmp_path)
    server = FakeServer(healthy=False)
    rr.measure(KEYS[:2], OPTIONS, 7, lambda i, o: "other", server, out, log=lambda line: None)
    assert server.starts == 1 and len(lines(out)) == 2


def test_decider_is_restarted_every_ten_tasks_before_memory_builds_up(tmp_path):
    out = new_file(tmp_path)
    server = FakeServer()
    restarts = rr.measure(KEYS, OPTIONS, 7, lambda i, o: "school", server, out, log=lambda line: None)
    assert len(KEYS) == 30 and restarts == 2 and server.starts == 2  # before tasks 11 and 21


def test_a_resumed_run_skips_the_tasks_already_written(tmp_path):
    out = new_file(tmp_path)
    rr.measure(KEYS[:12], OPTIONS, 7, lambda i, o: "writing", FakeServer(), out, log=lambda line: None)
    head, done = rr.read_file(out)
    rr.check_resumable(head, done, OPTIONS, 7, KEYS)
    asked = []
    rr.measure(KEYS, OPTIONS, 7, lambda i, o: asked.append(i) or "writing", FakeServer(), out, done=len(done),
               log=lambda line: None)
    assert sorted(set(asked)) == list(range(12, 30)) and len(lines(out)) == 30


def test_a_file_for_other_tasks_or_options_is_not_resumed(tmp_path):
    out = new_file(tmp_path)
    rr.measure(KEYS[:2], OPTIONS, 7, lambda i, o: "other", FakeServer(), out, log=lambda line: None)
    head, done = rr.read_file(out)
    with pytest.raises(ConfigurationError, match="orders or project options"):
        rr.check_resumable(head, done, OPTIONS, 5, KEYS)
    with pytest.raises(ConfigurationError, match="canonical tasks"):
        rr.check_resumable(head, done, OPTIONS, 7, list(reversed(KEYS)))


def test_three_failures_stop_the_run_and_keep_what_was_written(tmp_path):
    out = new_file(tmp_path)

    def ask(index, order):
        if index == 5:
            raise DeciderUnavailable("ReadTimeout")
        return "personal"

    with pytest.raises(rr.Stopped, match="task 6 failed 3 times"):
        rr.measure(KEYS, OPTIONS, 7, ask, FakeServer(), out, log=lambda line: None)
    assert len(lines(out)) == 5


def test_the_bench_carries_a_complete_rotation_file_and_says_which_wording_it_used(tmp_path):
    out = new_file(tmp_path)
    rr.measure(KEYS, OPTIONS, 7, lambda i, o: o[0] if i == 0 else "research", FakeServer(), out,
               log=lambda line: None)
    report = bench.carried_rotation(out, QUESTIONS)
    assert (report.tasks, report.rotations, report.flipped) == (30, 7, 1)  # task 1 took whatever came first
    assert bench.rotation_wording(out, QUESTIONS) == "It was measured with today's wording of the project question."
    head, rows = rr.read_file(out)
    head["project_instructions"] = "Which of Dan's projects or areas does this task belong to?"
    out.write_text("\n".join(json.dumps(x) for x in [head, *rows]) + "\n", encoding="utf-8")
    assert "another wording" in bench.rotation_wording(out, QUESTIONS)


def test_the_bench_refuses_an_unfinished_rotation_file(tmp_path):
    out = new_file(tmp_path)
    rr.measure(KEYS[:4], OPTIONS, 7, lambda i, o: "work", FakeServer(), out, log=lambda line: None)
    with pytest.raises(ConfigurationError, match="4 of 30 tasks written"):
        bench.carried_rotation(out, QUESTIONS)


def test_the_server_is_stopped_only_by_a_pid_that_is_serve_decider(tmp_path):
    killed = []
    pid_file = tmp_path / "decider.pid"
    pid_file.write_text("4242", encoding="ascii")
    foreign = rr.LocalDecider("http://127.0.0.1:8000", pid_file, tmp_path / "log", command_line=lambda pid: "notepad.exe",
                              kill=killed.append)
    foreign.stop()
    assert killed == []  # a stale pid file naming someone else's process: left alone
    alive = {"up": True}
    ours = rr.LocalDecider("http://127.0.0.1:8000", pid_file, tmp_path / "log",
                           command_line=lambda pid: "python tools/serve_decider.py" if alive["up"] else "",
                           kill=lambda pid: killed.append(pid) or alive.update(up=False))
    ours.stop()
    assert killed == [4242]


def test_health_is_a_short_get_and_any_failure_counts_as_no_answer(tmp_path):
    server = rr.LocalDecider("http://127.0.0.1:8000", tmp_path / "pid", tmp_path / "log")
    with respx.mock(assert_all_called=False) as router:
        router.get("http://127.0.0.1:8000/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
        assert server.healthy()
        router.get("http://127.0.0.1:8000/health").mock(side_effect=httpx.ReadTimeout("hung"))
        assert not server.healthy()


def test_the_serve_commands_memory_guard_trips_only_past_its_limit():
    serve = _load("serve_decider", REPO / "tools" / "serve_decider.py")
    gb = 1024 ** 3
    assert not serve.over_limit(15 * gb, 16) and serve.over_limit(17 * gb, 16)
    assert not serve.over_limit(40 * gb, None)  # no limit given: never


def test_after_each_scheduled_restart_the_previous_task_is_asked_again_and_matches_are_recorded(tmp_path):
    out = new_file(tmp_path)
    memory = iter(range(1000))
    rr.measure(KEYS, OPTIONS, 7, lambda i, o: "research", FakeServer(), out, log=lambda line: None,
               memory=lambda: {"commit_gb": next(memory)})
    rows = rr.read_all(out)[1:]
    checks = [r for r in rows if r.get("kind") == "determinism"]
    starts = [r for r in rows if r.get("kind") == "start"]
    assert [(c["task_index"], c["matches"], c["of"]) for c in checks] == [(9, 7, 7), (19, 7, 7)]
    assert [s["before_task"] for s in starts] == [10, 20] and all("commit_gb" in s["decider_memory"] for s in starts)
    assert all("commit_gb" in r["decider_memory"] for r in rows if "kind" not in r)  # after every task
    assert len(lines(out)) == 30  # the event rows are not tasks


def test_a_determinism_mismatch_is_counted(tmp_path):
    out = new_file(tmp_path)
    server = FakeServer()
    rr.measure(KEYS[:11], OPTIONS, 7, lambda i, o: "work" if server.starts else "school", server, out,
               log=lambda line: None)
    [check] = [r for r in rr.read_all(out)[1:] if r.get("kind") == "determinism"]
    assert (check["task_index"], check["matches"]) == (9, 0)  # the fresh server answered otherwise


def test_a_failed_determinism_check_is_recorded_and_the_run_goes_on(tmp_path):
    out = new_file(tmp_path)
    server, calls = FakeServer(), {"n": 0}

    def ask(index, order):
        calls["n"] += 1
        if server.starts == 1 and index == 9 and calls.setdefault("failed", 0) < 1:
            calls["failed"] = 1
            raise DeciderUnavailable("ReadTimeout")
        return "other"

    rr.measure(KEYS[:12], OPTIONS, 7, ask, server, out, log=lambda line: None)
    [check] = [r for r in rr.read_all(out)[1:] if r.get("kind") == "determinism"]
    assert check["error"] == "ReadTimeout" and len(lines(out)) == 12
