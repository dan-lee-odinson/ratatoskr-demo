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

"""The fresh evaluation, replayable: bench/FRESH_EVAL_2026-10-22.md from bench/fresh_eval_2026-10-22.json.

Dan's ruling 43, Part B. The export holds what the run of 21 Oct recorded, taken from the run's own saved state
(bench/raw/fresh-*.state.pickle, which git ignores) and the labels it was scored against: the metadata, every
conversation's outcome from the courier's ledger, its extraction calls from the spend ledger, its task writes from
the Todoist record, and Decider's answers about every labelled task, each with its probability, floor and
whether it was sure, the rule's outcome and the step that set it, and the read times. Fictional text only.

    python bench/replay_fresh.py            regenerate the report from the export (no network, model or key)
    python bench/replay_fresh.py --check    the same, compared with the committed report; exit 1 if it differs
    python bench/replay_fresh.py --export   write the export from the saved run (needs bench/raw/)

The report is drawn by the same function that drew it on 21 Oct, `run_bench.render_fresh`, with the facts its
header asked git for (the runner's blob, the tag's commit, whether the courier differed from the tag) taken from
the export as they stood for the run, not asked of git again: the code has moved on since.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pickle
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parents[1]
EXPORT = REPO / "bench" / "fresh_eval_2026-10-22.json"
REPORT = REPO / "bench" / "FRESH_EVAL_2026-10-22.md"
STATE = REPO / "bench" / "raw" / "fresh-2026-10-21T0457TUTC.state.pickle"
COMMAND = "python bench/replay_fresh.py"
REPLAY_LINE = (f"- **Replay:** every table here is regenerated from `bench/fresh_eval_2026-10-22.json` by `{COMMAND}`, "
               "with no network, no model and no key (ruling 43).")
NOT_RECORDED = ("The run recorded, per conversation, the courier's outcome, its extraction calls and its task "
                "writes, but not the probability of each source-gate window (only the verdict reached the ledger) "
                "nor the courier's own answers about the tasks it extracted (only what it wrote: the labels and "
                "the priority). Decider's answers are recorded for every labelled task, asked as written.")


def _bench() -> Any:
    if "run_bench" in sys.modules:
        return sys.modules["run_bench"]
    spec = importlib.util.spec_from_file_location("run_bench", REPO / "bench" / "run_bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_bench"] = module  # dataclasses look their module up while the class is built
    spec.loader.exec_module(module)
    return module


# --- the export ---

def _judgement(j: Any) -> dict[str, Any]:
    return {"raw": j.raw, "confidence": j.confidence, "floor": j.floor, "value": j.value, "problem": j.problem,
            "note": j.note, "answer": j.answer, "options": [list(o) for o in getattr(j, "options", ())],
            "sure": j.value is not None}


def export(state_path: Path = STATE) -> dict[str, Any]:
    """The run's record, from its saved state and the labels it was scored against."""
    bench = _bench()

    class Unpickler(pickle.Unpickler):
        def find_class(self, module: str, name: str) -> Any:
            if module in ("__main__", "run_bench"):
                return getattr(bench, name)
            return super().find_class(module, name)

    run = Unpickler(open(state_path, "rb")).load()["run"]
    labels_text = (Path(run.directory) / "labels.json").read_text(encoding="utf-8")
    if hashlib.sha256(labels_text.encode("utf-8")).hexdigest() != run.labels_sha256:
        raise SystemExit("replay_fresh: labels.json is not the file the run was scored against")
    report = REPORT.read_text(encoding="utf-8")
    header = {  # as they stood for the run; each is checked against the report the run produced
        "runner_blob": bench.git_blob_hash(REPO / "bench" / "run_bench.py"),
        "freeze_commit": bench._git("rev-parse", "--short", f"{bench.FREEZE_TAG}^{{commit}}"),
        "courier_as_tagged": True,
    }
    assert f"(git blob `{header['runner_blob']}`)" in report, "run_bench.py has changed since the report"
    assert f"(commit `{header['freeze_commit']}`)" in report and "are those of the tag" in report
    written_for = {c.recording_id: c.written for c in run.conversations}

    def written(t: Any) -> bool:
        """Whether the courier wrote a task matching this labelled one (by its match words)."""
        labelled = [x for x in run.tasks if x.recording_id == t.recording_id]
        for args in written_for.get(t.recording_id, []):
            content = str(args.get("content", "")).removeprefix(bench.MAYBE_PREFIX)
            hit = bench.match_task(bench.Task(content=content, due_string=None, notes=None),
                                   [x.expected for x in labelled])
            if hit is t.expected:
                return True
        return False

    return {
        "about": ("The fresh evaluation of 21 Oct 2026 (Dan's ruling 39 Part 3, rulings 40 and 41), as the run "
                  "recorded it; bench/replay_fresh.py regenerates bench/FRESH_EVAL_2026-10-22.md from this file "
                  "(ruling 43). Fictional conversations only."),
        "not_recorded": NOT_RECORDED,
        "tag": bench.FREEZE_TAG,
        "labels_sha256": bench._grouped(run.labels_sha256),  # grouped: the secret scan reads a long hex run as a key
        "labels_commit": run.labels_commit,
        "labels_json": labels_text,
        "header": header,
        "started": run.started,
        "finished": run.finished,
        "versions": run.versions,
        "notes": run.notes,
        "stopped": run.stopped,
        "spend_before": run.spend_before,
        "spend_after": run.spend_after,
        "reads_ms": run.reads,
        "disagreements": None,
        "conversations": [
            {"id": c.recording_id, "set": c.kind, "status": c.status, "hold_reason": c.reason if c.status == "hold"
             else None, "reason": c.reason, "extraction_calls": c.extraction_calls, "seconds": c.seconds,
             "gate_windows": None, "written": c.written}
            for c in run.conversations],
        "labelled_tasks": [
            {"recording": t.recording_id, "set": kind, "recorded_on": t.recorded_on.isoformat(),
             "task": {"content": t.task.content, "due_string": t.task.due_string, "notes": t.task.notes},
             "contact": {"name": t.contact.name, "organisation": t.contact.organisation, "role": t.contact.role,
                         "context": t.contact.context},
             "decider": {"latency_ms": a.latency_ms, "input_tokens": a.input_tokens, "output_tokens": a.output_tokens,
                         "error": a.error, "answers": {q: _judgement(j) for q, j in a.judged.items()}},
             "outcome": {"project": a.judged["project"].value if "project" in a.judged else None,
                         "priority": a.judged["priority"].answer if "priority" in a.judged else None,
                         "priority_step": a.judged["priority"].note if "priority" in a.judged else None},
             "written": written(t)}
            for t, kind, a in zip(run.tasks, run.kinds, run.asked)],
    }


# --- the replay ---

def _run_from(data: dict[str, Any], directory: Path) -> Any:
    """The run's objects again, from the export."""
    bench = _bench()
    from ratatoskr_demo.judge import Judgement

    labels = json.loads(data["labels_json"])
    expected = {c["id"]: bench.fresh_expected(c) for c in labels["conversations"]}
    run = bench.FreshRun(labels_sha256=_plain(data["labels_sha256"]), labels_commit=data["labels_commit"],
                         started=data["started"], finished=data["finished"], reads=data["reads_ms"],
                         spend_before=data["spend_before"], spend_after=data["spend_after"],
                         versions=data["versions"], notes=data["notes"], stopped=data["stopped"],
                         directory=str(directory))
    run.conversations = [bench.FreshConversation(c["id"], c["set"], status=c["status"], reason=c["reason"],
                                                 extraction_calls=c["extraction_calls"], written=c["written"],
                                                 seconds=c["seconds"]) for c in data["conversations"]]
    taken: dict[str, int] = {}
    for t in data["labelled_tasks"]:
        i = taken.get(t["recording"], 0)
        taken[t["recording"]] = i + 1
        e = expected[t["recording"]][i]
        assert e.task.content == t["task"]["content"], "the export's tasks are not in the labels' order"
        run.tasks.append(bench.BenchTask(t["recording"], bench.Task(**t["task"]), bench.Contact(**t["contact"]), e,
                                         recorded_on=date.fromisoformat(t["recorded_on"])))
        run.kinds.append(t["set"])
        d = t["decider"]
        judged = {q: Judgement(q, raw=j["raw"], confidence=j["confidence"], floor=j["floor"], value=j["value"],
                               problem=j["problem"], note=j["note"], answer=j["answer"],
                               options=tuple(tuple(o) for o in j["options"]))
                  for q, j in d["answers"].items()}
        run.asked.append(bench.Asked("decider", latency_ms=d["latency_ms"], input_tokens=d["input_tokens"],
                                     output_tokens=d["output_tokens"], judged=judged, error=d["error"]))
    return run


def _plain(digest: str) -> str:
    return digest.replace(" ", "")


@contextmanager
def _as_recorded(header: dict[str, Any]) -> Iterator[None]:
    """The header's git facts as they stood for the run, in place of asking git now."""
    bench = _bench()
    ask, blob = bench._git, bench.git_blob_hash

    def recorded_git(*args: str) -> str | None:
        if args[:1] == ("rev-parse",):
            return header["freeze_commit"]
        if args[:1] == ("diff",):
            return "" if header["courier_as_tagged"] else "differs"
        return ask(*args)

    bench._git, bench.git_blob_hash = recorded_git, lambda path: header["runner_blob"]
    try:
        yield
    finally:
        bench._git, bench.git_blob_hash = ask, blob


def replay(data: dict[str, Any]) -> str:
    """bench/FRESH_EVAL_2026-10-22.md, drawn again from the export alone."""
    bench = _bench()
    if hashlib.sha256(data["labels_json"].encode("utf-8")).hexdigest() != _plain(data["labels_sha256"]):
        raise SystemExit("replay_fresh: the export's labels do not match their recorded hash")
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "labels.json").write_text(data["labels_json"], encoding="utf-8", newline="\n")
        run = _run_from(data, Path(directory))
        with _as_recorded(data["header"]):
            text = bench.render_fresh(run, settings=None, disagreements=data["disagreements"])
    lines = text.split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith("- **The fresh labels:**")) + 1
    return "\n".join(lines[:at] + [REPLAY_LINE] + lines[at:])


def section(text: str, heading: str) -> str:
    """One section of the report, its heading to the next."""
    return heading + text.split(heading, 1)[1].split("\n## ", 1)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="compare with the committed report; write nothing")
    parser.add_argument("--export", action="store_true", help="write the export from the saved run first")
    args = parser.parse_args(argv)
    if args.export:
        EXPORT.write_bytes((json.dumps(export(), indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        print(f"wrote {EXPORT.relative_to(REPO)}")
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    text = replay(data)
    tables = text.count("\n|---")
    if args.check:
        same = text == REPORT.read_text(encoding="utf-8")
        print(f"{REPORT.relative_to(REPO)}: {tables} tables regenerated from {EXPORT.relative_to(REPO)}; "
              + ("identical, byte for byte" if same else "DIFFERS"))
        return 0 if same else 1
    REPORT.write_bytes(text.encode("utf-8"))
    print(f"wrote {REPORT.relative_to(REPO)}: {tables} tables, from {EXPORT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
