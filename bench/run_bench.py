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

"""The bench: the same six questions, asked three ways, over the fixtures' extracted tasks.

    python bench/run_bench.py --estimate        worst-case Bedrock spend, no calls made
    python bench/run_bench.py                   run it; writes bench/RESULTS.md
    python bench/run_bench.py --rotations 0 --rotation-from bench/raw/run-....json --rotation-note "..."
                                                the option-order result carried over from an
                                                earlier run's raw file, or from a rotation file
                                                written task by task by bench/rotation_run.py
                                                (which survives a Decider hang); the note says
                                                what differed when it was measured

For every fixture recording (tests/fixtures and bench/fixtures) the courier's
own triage runs first, so the Vargrheim recording is held by the tripwire
before any model sees it. Then the source-text gate (Strands Decider, on this
machine) reads each remaining transcript, and a recording it holds is not
extracted, exactly as in the courier. Each recording that passes is
extracted ONCE by openai.gpt-oss-20b on Bedrock, with the courier's C9 guard;
the extraction is kept in memory for the run and never written to disk. Then
each extracted task is asked the six route.toml questions (project,
someone_waiting, hard_deadline, worth_researching_soon, real_commitment,
sensitive), over the same state, by three backends, and priority is derived
from the three priority Nouls and the due date, counted from the recording's
own date, by route.priority_rule() (Dan's ruling 7):

    decider   Strands Decider 2B on this machine's CPU, one request per task
    bedrock   openai.gpt-oss-20b, as a JSON-answer prompt with the same question text
    jev       TypeSafe Jev, one request per task (skipped if TYPESAFE_API_KEY is absent)

Recorded per task and backend: wall-clock latency, input and output tokens
where the backend reports them, cost at the configured prices (Decider $0;
Jev at TypeSafe's listed price), and agreement with the expected answers in
the fixtures' `expected` blocks. Every answer is read by the same rules
(ratatoskr_demo/judge.py), so the columns are comparable. The option-order
flip rate is measured for Decider over the canonical tasks.

Every Bedrock call goes through the courier's spend ledger and draws from the
same $5.00 hard stop. RESULTS.md is generated here and never hand-edited: it
records the git blob hash of this file, and a test fails if they differ.
The bench refuses to run while `ratatoskr-demo --loop` is running.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import statistics
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ratatoskr_demo import due, extract, guard, triage  # noqa: E402
from ratatoskr_demo.bedrock import BedrockClient, NetworkLost, make_spend_ledger  # noqa: E402
from ratatoskr_demo.config import Settings, load_settings  # noqa: E402
from ratatoskr_demo.decider import DeciderRejected, DeciderRouter, DeciderUnavailable, make_router  # noqa: E402
from ratatoskr_demo.errors import ConfigurationError, RatatoskrError  # noqa: E402
from ratatoskr_demo.expected import (  # noqa: E402
    FIXTURE_DIRS, ExpectedTask, as_first_labelled, canonical_tasks, load_expected, match_task)
from ratatoskr_demo.extract import Contact, Task, bedrock_model_call, unfence  # noqa: E402
from ratatoskr_demo.judge import SOURCE_SHORT, Judgement, interpret  # noqa: E402
from ratatoskr_demo.route import (  # noqa: E402
    RouteQuestion, load_route_questions, routing_state, without_option_descriptions)
from ratatoskr_demo.source_gate import SourceGate, make_source_gate  # noqa: E402
from ratatoskr_demo.rotation import RotationReport, rebuild, run_rotation  # noqa: E402
from ratatoskr_demo.runlock import refuse_while_loop_runs  # noqa: E402
from ratatoskr_demo.spend import input_token_bound, worst_case_usd  # noqa: E402
from ratatoskr_demo.transcript import NOT_READY, extract_text  # noqa: E402

RESULTS = REPO / "bench" / "RESULTS.md"
ROUTE_TOML = REPO / "ratatoskr_demo" / "prompts" / "route.toml"
# The priority Nouls' labels (someone_waiting, hard_deadline, worth_researching_soon) were first written with
# Dan's ruling 7, committed on 18 Oct 2026; the corrections of ruling 40 keep them beside the new values.
FIRST_LABELLED = "18 Oct"
RAW_DIR = REPO / "bench" / "raw"  # git-ignored: per-call numbers for the write-up, never transcript text
JEV_PRICE_IN_PER_M = 0.042  # USD per 1M input tokens; output tokens free (docs.typesafe.ai/models, 16 Oct 2026)
QUESTIONS = ("project", "priority", "someone_waiting", "hard_deadline", "worth_researching_soon", "real_commitment",
             "sensitive")
ASKED = ("project", "someone_waiting", "hard_deadline", "worth_researching_soon", "real_commitment", "sensitive")
NOULS = ASKED[1:]
SET_BY_THE_NOULS = ("someone_waiting", "hard_deadline", "worth_researching_soon", "neither")
NETWORK_RETRIES = 3
# A read that overruns the client's timeout is abandoned but keeps running inside Decider, and abandoned reads
# pile up (BUILD_REPORT, "Decider hang (finding)"). The bench waits up to 600 s per read, so a slow read
# finishes; the courier keeps [route] decider_timeout_seconds (180 s) for live recordings.
BENCH_DECIDER_TIMEOUT = 600.0

_YES = {"type": "object", "required": ["probability_yes"], "properties": {"probability_yes": {"type": "number"}}}
ANSWER_FORMAT = {
    "type": "object", "additionalProperties": False,
    "required": list(ASKED),
    "properties": {
        "project": {"type": "object", "required": ["choice", "confidence"],
                    "properties": {"choice": {"type": "string"}, "confidence": {"type": "number"}}},
        **{name: _YES for name in NOULS},
    },
}


# --- one recording's tasks, in memory only ---

@dataclass
class BenchTask:
    recording_id: str
    task: Task
    contact: Contact
    expected: ExpectedTask | None
    recorded_on: date | None = None  # "today" for the priority rule's 30-day test

    @property
    def deadline_near(self) -> bool:
        return due.near(self.task.due_string, self.recorded_on)


@dataclass
class Asked:
    backend: str
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float = 0.0
    judged: dict[str, Judgement] = field(default_factory=dict)
    error: str | None = None


@dataclass
class Gated:
    """The source-text gate on one recording: counts and numbers, never the text."""

    recording_id: str
    status: str  # "sensitive" | "unsure" | "clear" | "unavailable"
    windows_asked: int = 0
    windows_total: int = 0
    max_p: float = 0.0
    latency_ms: float = 0.0
    expected: bool | None = None


@dataclass
class Run:
    triage: list[tuple[str, str]] = field(default_factory=list)  # (recording id, decision)
    gated: list[Gated] = field(default_factory=list)
    tasks: list[BenchTask] = field(default_factory=list)
    asked: dict[str, list[Asked]] = field(default_factory=dict)  # backend -> one per task, in task order
    extraction: dict[str, Any] = field(default_factory=dict)
    canonical_tasks: list[BenchTask] = field(default_factory=list)
    canonical: dict[str, list[Asked]] = field(default_factory=dict)  # backend -> one per canonical task
    rotation: RotationReport | None = None
    rotation_from: str | None = None  # the raw file the rotation was carried over from, if it was
    rotation_note: str = ""  # what differed when it was measured (the wording of the project question)
    prior_rotation: RotationReport | None = None  # an earlier measurement, shown beside the rotation
    prior_rotation_from: str | None = None
    prior_rotation_note: str = ""
    rerendered: str | None = None  # when RESULTS.md was re-rendered from the saved state (--render-from)
    as_measured: str = ""  # with --render-from: the "[as measured]" label, until a re-measure replaces the numbers
    option_descriptions: str = "on"  # ruling 15: "on", "off", or "both" (asked twice, side by side)
    asked_off: dict[str, list[Asked]] = field(default_factory=dict)  # "both": the extracted tasks, names only
    canonical_off: dict[str, list[Asked]] = field(default_factory=dict)  # "both": the canonical tasks, names only
    real_tasks: list[BenchTask] = field(default_factory=list)  # the live run's tasks (--real-tasks)
    real: dict[str, list[Asked]] = field(default_factory=dict)
    real_off: dict[str, list[Asked]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    stopped: str | None = None


def recordings() -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    out = []
    for directory in FIXTURE_DIRS:
        for path in sorted(Path(directory).glob("rec_*.json")):
            body = json.loads(path.read_text(encoding="utf-8"))
            out.append((body["data"], body.get("expected")))
    return out


def expected_by_id() -> dict[str, list[ExpectedTask]]:
    return {r.recording_id: list(r.tasks) for r in load_expected()}


# --- the Bedrock column: a JSON-answer prompt with the same question text ---

def answer_prompt(questions: dict[str, RouteQuestion]) -> str:
    lines = ["You answer six typed questions about one task. The state below is everything you know.",
             "For each question give your answer and how sure you are, from 0 to 1.", ""]
    for name in ASKED:
        q = questions[name]
        lines.append(f"## {name} ({q.type})")
        lines.append(q.instructions)
        if q.type == "choice":
            lines.extend(f"- {option}: {description}" for option, description in q.criteria.items())
        else:
            lines.extend(f"- {side}: {description}" for side, description in q.criteria.items())
        lines.append("")
    lines.append("Reply with one JSON object and nothing else, matching this JSON Schema exactly:")
    lines.append(json.dumps(ANSWER_FORMAT, separators=(",", ":")))
    return "\n".join(lines)


def answers_from_json(text: str) -> dict[str, Any]:
    """The Bedrock reply, in Decider's answer shape, so judge.interpret reads every column alike."""
    data = json.loads(unfence(text))
    return {
        "project": {"type": "choice", "choice": data["project"]["choice"],
                    "confidence": data["project"]["confidence"]},
        **{name: {"type": "noul", "noul": data[name]["probability_yes"]} for name in NOULS},
    }


def ask_bedrock(client: BedrockClient, bench_task: BenchTask, questions: dict[str, RouteQuestion]) -> Asked:
    state = json.dumps(routing_state(bench_task.task, bench_task.contact), ensure_ascii=False)
    messages = [{"role": "system", "content": answer_prompt(questions)},
                {"role": "user", "content": f"State:\n{state}"}]
    started = time.perf_counter()
    result = client.chat(messages, purpose="bench-route")
    asked = Asked("bedrock", latency_ms=(time.perf_counter() - started) * 1000, input_tokens=result.input_tokens,
                  output_tokens=result.output_tokens, cost_usd=result.cost_usd)
    try:
        asked.judged = interpret(answers_from_json(result.content or ""), questions,
                                 deadline_near=bench_task.deadline_near)
    except (ValueError, KeyError, TypeError):
        asked.error = "reply was not the answer JSON"
    return asked


def ask_decider(router: Any, bench_task: BenchTask, questions: dict[str, RouteQuestion]) -> Asked:
    started = time.perf_counter()
    try:
        response = router.ask(bench_task.task, bench_task.contact)
    except (DeciderUnavailable, DeciderRejected) as exc:
        return Asked("decider", error=f"unavailable: {exc}")
    usage = response.get("usage") or {}
    return Asked("decider", latency_ms=(time.perf_counter() - started) * 1000,
                 input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                 judged=interpret(response["answers"], questions, deadline_near=bench_task.deadline_near))


def ask_jev(router: Any, bench_task: BenchTask, questions: dict[str, RouteQuestion]) -> Asked:
    started = time.perf_counter()
    try:
        response = router.ask(bench_task.task, bench_task.contact)
    except Exception as exc:  # the SDK's own error classes; a failed ask is reported, not fatal
        return Asked("jev", error=f"{type(exc).__name__}")
    answers = {name: answer.model_dump() for name, answer in response.answers.items()}
    tokens_in = response.usage.input_tokens
    return Asked("jev", latency_ms=(time.perf_counter() - started) * 1000, input_tokens=tokens_in,
                 output_tokens=response.usage.output_tokens, cost_usd=tokens_in * JEV_PRICE_IN_PER_M / 1_000_000,
                 judged=interpret(answers, questions, deadline_near=bench_task.deadline_near))


# --- the run ---

def with_network_retries(client: BedrockClient | None, unit: Callable[[], Any]) -> Any:
    """A lost network: wait about a minute, recheck, and retry the unit of work from its start."""
    for attempt in range(NETWORK_RETRIES):
        try:
            return unit()
        except NetworkLost:
            if client is None or attempt == NETWORK_RETRIES - 1:
                raise
            client.wait_for_network()
    raise AssertionError("unreachable")


def extract_all(settings: Settings, client: BedrockClient, run: Run, gate: SourceGate | None = None) -> None:
    model_call = bedrock_model_call(client)
    expected = expected_by_id()
    n = settings.guard.ngram_words
    for recording, block in recordings():
        rid = str(recording["id"])
        decision = triage.decide(recording, None, settings)
        run.triage.append((rid, type(decision).__name__ + (f" ({decision.reason})" if hasattr(decision, "reason") else "")))
        if not isinstance(decision, triage.Process):
            continue
        text = extract_text(recording)
        if text is NOT_READY:
            continue
        if gate is not None and not gate_recording(gate, rid, str(text), block, run):
            continue  # held by the source-text gate: never extracted, as in the courier

        def unit():
            found = extract.run(text, settings, model_call)
            if guard.any_overlap(text, found.texts(), n):
                found = extract.run(text, settings, model_call, corrective=True)
                if guard.any_overlap(text, found.texts(), n):
                    return None
            return found

        try:
            extraction = with_network_retries(client, unit)
        except extract.ExtractionSchemaError:
            run.extraction[rid] = "schema error after the retry"
            continue
        if extraction is None:
            run.extraction[rid] = "held by the leak guard"
            continue
        run.extraction[rid] = f"{len(extraction.tasks)} task(s)"
        for task in extraction.tasks:
            run.tasks.append(BenchTask(rid, task, extraction.contact, match_task(task, expected.get(rid, [])),
                                       recorded_on=due.recording_date(recording.get("recording_at"))))
        del text  # the transcript leaves memory with this recording


def gate_recording(gate: SourceGate, rid: str, text: str, block: dict[str, Any] | None, run: Run) -> bool:
    """The source-text gate on one recording. True: go on to extraction."""
    expected = (block or {}).get("source_sensitive")
    started = time.perf_counter()
    try:
        verdict = gate.check(text)
    except (DeciderUnavailable, DeciderRejected):
        run.gated.append(Gated(rid, "unavailable", expected=expected))
        return False  # as in the courier: no extraction without the gate (no recording here is tagged urgent)
    run.gated.append(Gated(rid, verdict.status, verdict.windows_asked, verdict.windows_total, verdict.max_p,
                           (time.perf_counter() - started) * 1000, expected))
    return not verdict.hold


def run_bench(settings: Settings, *, bedrock: BedrockClient, decider: Any, jev: Any | None,
              rotations: int, gate: SourceGate | None = None, rotation_from: Path | None = None,
              rotation_note: str = "", option_descriptions: str = "on", real_tasks: list[BenchTask] | None = None,
              out: Callable[[str], None] = print) -> Run:
    questions = load_route_questions(settings)
    names_only = without_option_descriptions(questions)
    if option_descriptions == "off":  # ruling 15: the whole pass with the options as their names alone
        questions = names_only
        decider, jev = with_questions(decider, questions), with_questions(jev, questions)
    run = Run(option_descriptions=option_descriptions, real_tasks=list(real_tasks or []))
    if rotation_from is not None:  # read first: a file that does not fit stops the run before any call
        run.rotation = carried_rotation(rotation_from, questions)
        run.rotation_from = rotation_from.name
        run.rotation_note = " ".join(s for s in (rotation_wording(rotation_from, questions), rotation_note) if s)
    if jev is None:
        run.notes.append("Jev column skipped: no TYPESAFE_API_KEY in .env.")
    try:
        out("source-text gate (Strands Decider), then extracting (openai.gpt-oss-20b on Bedrock) ...")
        extract_all(settings, bedrock, run, gate)
        out(f"  {len(run.tasks)} task(s) from {len(run.extraction)} recording(s)")
        backends: list[tuple[str, Callable[[BenchTask], Asked]]] = [
            ("decider", lambda t: ask_decider(decider, t, questions)),
            ("bedrock", lambda t: with_network_retries(bedrock, lambda: ask_bedrock(bedrock, t, questions))),
        ]
        if jev is not None:
            backends.append(("jev", lambda t: ask_jev(jev, t, questions)))
        for name, ask in backends:
            out(f"asking {name} ...")
            run.asked[name] = [ask(t) for t in run.tasks]
        # The canonical tasks too, with no extraction in between: what each router makes of the
        # task as written in the `expected` block, sensitive details included.
        recorded = load_expected()
        dates = {r.recording_id: r.recorded_on for r in recorded}
        run.canonical_tasks = [BenchTask(rid, expected.task, contact, expected, recorded_on=dates[rid])
                               for rid, expected, contact in canonical_tasks(recorded)]
        for name, ask in backends:
            out(f"asking {name} about the canonical tasks ...")
            run.canonical[name] = [ask(t) for t in run.canonical_tasks]
        for name, ask in backends:
            if run.real_tasks:
                out(f"asking {name} about the real tasks ...")
                run.real[name] = [ask(t) for t in run.real_tasks]
        if option_descriptions == "both":  # ruling 15: the same tasks again, the options as their names alone
            decider_off, jev_off = with_questions(decider, names_only), with_questions(jev, names_only)
            backends_off: list[tuple[str, Callable[[BenchTask], Asked]]] = [
                ("decider", lambda t: ask_decider(decider_off, t, names_only)),
                ("bedrock", lambda t: with_network_retries(bedrock, lambda: ask_bedrock(bedrock, t, names_only))),
            ]
            if jev_off is not None:
                backends_off.append(("jev", lambda t: ask_jev(jev_off, t, names_only)))
            for name, ask in backends_off:
                out(f"asking {name} again, the options without their descriptions ...")
                run.asked_off[name] = [ask(t) for t in run.tasks]
                run.canonical_off[name] = [ask(t) for t in run.canonical_tasks]
                if run.real_tasks:
                    run.real_off[name] = [ask(t) for t in run.real_tasks]
        if rotations:
            out(f"option rotation: {rotations} orders over the canonical tasks (Decider) ...")
            run.rotation = rotate(decider, questions, rotations)
    except ConfigurationError as exc:  # the spend hard stop among them: stop, keep what was measured
        run.stopped = str(exc)
        out(f"STOPPED: {exc}")
    return run


def with_questions(router: Any, questions: dict[str, RouteQuestion]) -> Any:
    """The same router (same client and host), asking another version of the questions."""
    if router is None:
        return None
    if isinstance(router, DeciderRouter):
        return DeciderRouter(router.client, questions, expected_model=router.expected_model)
    from ratatoskr_demo.jev import JevRouter

    return JevRouter(router.client, questions)


def load_real_tasks(paths: list[Path]) -> list[BenchTask]:
    """The live run's guarded extractions, saved by `ratatoskr-demo --once --dry-run --save-tasks` (ruling 15)."""
    out = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            contact = Contact(**row["contact"])
            for t in row["tasks"]:
                out.append(BenchTask(row["recording"], Task(content=t["content"], due_string=t.get("due_string"),
                                                            notes=t.get("notes")), contact, None,
                                     recorded_on=due.recording_date(row.get("recording_at"))))
    return out


def rotate(decider: Any, questions: dict[str, RouteQuestion], n: int) -> RotationReport:
    tasks = canonical_tasks(load_expected())

    def ask(index: int, order: list[str]) -> str | None:
        _, expected, contact = tasks[index]
        try:
            response = decider.ask(expected.task, contact, project_order=order)
        except (DeciderUnavailable, DeciderRejected):
            return None
        judged = interpret(response["answers"], questions)["project"]
        return None if judged.problem else judged.answer

    return run_rotation(list(questions["project"].criteria), len(tasks), n, ask)


def carried_rotation(path: Path, questions: dict[str, RouteQuestion]) -> RotationReport:
    """The option-order result of an earlier run: from a bench raw file, or from a complete rotation file
    written task by task by bench/rotation_run.py. Only the project answer is rotated, so it carries over
    while the canonical tasks and the project options are the same; anything else refuses
    (ConfigurationError), before any call is made."""
    now = [(rid, expected.task.content) for rid, expected, _ in canonical_tasks(load_expected())]
    try:
        if path.suffix == ".jsonl":
            head, rows = _rotation_file(path)
            if len(rows) != len(now):
                raise ConfigurationError(f"--rotation-from {path}: {len(rows)} of {len(now)} tasks written; "
                                         "finish it with bench/rotation_run.py --resume first")
        else:
            rows = json.loads(path.read_text(encoding="utf-8"))["rotation"]["choices"]
        choices = [row["choices"] for row in rows]
        listed = [(row["recording"], row["task"]) for row in rows]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigurationError(f"--rotation-from {path}: no rotation in that file ({exc})") from None
    if listed != now:
        raise ConfigurationError(f"--rotation-from {path}: its canonical tasks are not today's; measure it again")
    options = list(questions["project"].criteria)
    if any(pick is not None and pick not in options for picks in choices for pick in picks):
        raise ConfigurationError(f"--rotation-from {path}: its project options are not today's; measure it again")
    return rebuild(options, choices)


def rotation_wording(path: Path, questions: dict[str, RouteQuestion], conditions: str | None = None) -> str:
    """A rotation file records the project question it was asked with; say whether that is today's.
    `conditions`, when given, replaces the run conditions the file recorded (for public wording)."""
    if path.suffix != ".jsonl":
        return ""  # a bench raw file does not record it: --rotation-note says
    head = _rotation_file(path)[0]
    recorded = head.get("project_instructions")
    said = head.get("conditions") if conditions is None else conditions
    conditions = f" Run conditions: {said}." if said else ""
    if recorded == str(questions["project"].instructions):
        return "It was measured with today's wording of the project question." + conditions
    return f'It was measured with another wording of the project question: "{recorded}".' + conditions


def _rotation_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The header and the task rows (event rows, such as restarts and determinism checks, have a "kind")."""
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or lines[0].get("kind") != "rotation":
        raise ValueError("not a rotation file")
    return lines[0], [row for row in lines[1:] if "kind" not in row]


# --- scoring and the report ---

def agreement(asked: list[Asked], tasks: list[BenchTask]) -> dict[str, Any]:
    """Per question: answered, agreed with the expected answer, sure, and agreed when sure."""
    out: dict[str, Any] = {}
    for q in QUESTIONS:
        pairs = [(a.judged.get(q), t.expected) for a, t in zip(asked, tasks)
                 if t.expected is not None and _expected_answer(q, t.expected) is not None]
        usable = [(j, e) for j, e in pairs if j is not None and not j.problem]
        right = [(j, e) for j, e in usable if j.answer == _expected_answer(q, e)]
        sure = [(j, e) for j, e in usable if _sure(q, j)]
        sure_right = [(j, e) for j, e in sure if j.answer == _expected_answer(q, e)]
        out[q] = {"matched": len(pairs), "usable": len(usable), "agree": len(right), "sure": len(sure),
                  "sure_agree": len(sure_right)}
    holds = [(a, t) for a, t in zip(asked, tasks) if t.expected is not None and "sensitive" in a.judged]
    out["gate"] = {"matched": len(holds),
                   "agree": sum(1 for a, t in holds if _would_hold(a.judged["sensitive"]) == t.expected.sensitive)}
    return out


def _expected_answer(question: str, expected: ExpectedTask) -> Any:
    """The expected answer, from the fixture's own labels (Dan's ruling 7); None: not labelled, not scored."""
    return {"project": expected.project, "priority": expected.priority,
            "someone_waiting": expected.someone_waiting, "hard_deadline": expected.hard_deadline,
            "worth_researching_soon": expected.worth_researching_soon,
            "real_commitment": expected.real_commitment, "sensitive": expected.sensitive}[question]


def _sure(question: str, j: Judgement) -> bool:
    if question == "priority":
        return j.note in SET_BY_THE_NOULS  # set by the questions, not by the project default
    return j.confidence is not None and j.confidence >= j.floor


def _would_hold(j: Judgement) -> bool:
    """The courier's gate: sensitive yes or unsure holds; an unusable answer is 'not checked' (written)."""
    return not j.problem and (j.value is None or j.value is True)


def git_blob_hash(path: Path) -> str:
    """The first 16 hex characters of git's blob hash of the file (LF line endings)."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:16]


def render(run: Run, *, settings: Settings, spend: dict[str, Any], versions: dict[str, str], when: str) -> str:
    tasks = run.tasks
    lines = [
        "# Bench results",
        "",
        "<!-- Generated by bench/run_bench.py. Do not edit by hand: a test checks the hash below. -->",
        f"Generated {when} by `bench/run_bench.py` (git blob `{git_blob_hash(Path(__file__))}`)"
        + (f"; re-rendered {run.rerendered} from the run's saved state with the rotation below, no model asked again."
           if run.rerendered else "."),
        "",
        "The same six questions (project, someone_waiting, hard_deadline, worth_researching_soon, "
        "real_commitment, sensitive), over the same state, asked of three backends for every task extracted "
        "from the fixtures; priority is set from three of them and the due date by `route.priority_rule()`. "
        "Every answer is read by the same rules (`ratatoskr_demo/judge.py`). The fixtures and their expected "
        "answers are invented; see `bench/fixtures/` and the `expected` blocks.",
        "",
        "Project options: " + {"on": "with their one-line descriptions", "off": "their names alone, no descriptions",
                               "both": "asked both ways, side by side below"}[getattr(run, "option_descriptions", "on")]
        + ". Every state carries the guarded task (content, notes, due string) and the contact line "
          "(name, organisation, role, context).",
        "",
        "| | |",
        "|---|---|",
    ]
    if getattr(run, "as_measured", ""):  # a state saved before the field existed has none
        lines[5:5] = [f"**[as measured]** {run.as_measured}", ""]
    lines += [f"| {k} | {v} |" for k, v in versions.items()]
    lines += ["", "## Triage and extraction", ""]
    lines += [f"- `{rid}`: {decision}" + (f"; extracted {run.extraction[rid]}" if rid in run.extraction else "")
              for rid, decision in run.triage]
    matched = sum(1 for t in tasks if t.expected is not None)
    if run.gated:
        lines += ["", "## Source-text gate (Strands Decider, before extraction)", "",
                  "Decider's `sensitive` question asked of each transcript itself, in windows of about "
                  f"{settings.route.source_window_words} words split on speaker turns, before any Bedrock call. "
                  "A recording it holds (sensitive, or unsure) is not extracted.", "",
                  "| recording | windows asked / total | highest p | gate | expected | time |",
                  "|---|---|---|---|---|---|"]
        for g in run.gated:
            held = {"sensitive": "HOLD (sensitive)", "unsure": "HOLD (unsure)", "clear": "clear",
                    "unavailable": "not asked (Decider unavailable)"}[g.status]
            expected = "-" if g.expected is None else ("HOLD" if g.expected else "clear")
            lines.append(f"| {g.recording_id} | {g.windows_asked} / {g.windows_total} | {g.max_p:.2f} | {held} | "
                         f"{expected} | {_ms(g.latency_ms)} |")
        scored = [g for g in run.gated if g.expected is not None and g.status != "unavailable"]
        right = sum(1 for g in scored if (g.status != "clear") == g.expected)
        lines += ["", f"The gate's hold decision matched the expected one for {_frac(right, len(scored))} "
                      "recordings."]
    lines += ["", f"{len(tasks)} task(s) extracted; {matched} matched an expected task, "
                  f"{len(tasks) - matched} did not (extra or reworded beyond the match words).", ""]
    if run.stopped:
        lines += [f"**Stopped early:** {run.stopped}", ""]
    lines += run_notes(run)
    lines += ["## Per backend", "",
              "| backend | tasks | median latency | mean latency | input tokens | output tokens | cost | cost per task |",
              "|---|---|---|---|---|---|---|---|"]
    for name, asked in run.asked.items():
        latencies = [a.latency_ms for a in asked if a.latency_ms is not None]
        tin = sum(a.input_tokens or 0 for a in asked)
        tout = sum(a.output_tokens or 0 for a in asked)
        cost = sum(a.cost_usd for a in asked)
        per = cost / len(asked) if asked else 0.0
        lines.append(f"| {name} | {len(asked)} | {_ms(statistics.median(latencies) if latencies else None)} | "
                     f"{_ms(statistics.fmean(latencies) if latencies else None)} | {tin:,} | {tout:,} | "
                     f"${cost:.6f} | ${per:.6f} |")
    lines += ["", "## Agreement with the expected answers (extracted tasks that matched)", "",
              "`agree`: the backend's answer equals the expected one. `sure`: at or above the question's floor; "
              "for `priority`, set by the priority questions rather than by the project's default. "
              "`agree when sure`: of the sure answers, how many agree. The three priority questions are scored "
              "against their own labels (`worth_researching_soon` is not labelled where the expected priority "
              "is high, since the rule never reaches it there). The 30-day "
              "test counts from each recording's own date. `gate`: the post-extraction hold decision "
              "(sensitive yes or unsure holds) against the expected one.", ""]
    lines += agreement_table(run.asked, tasks)
    if run.canonical:
        canonical_latency = {name: [a.latency_ms for a in asked if a.latency_ms is not None]
                             for name, asked in run.canonical.items()}
        lines += ["", "## Agreement on the canonical tasks (no extraction)", "",
                  f"The {len(run.canonical_tasks)} tasks as written in the fixtures' `expected` blocks, sensitive "
                  "details included, asked of each backend directly. This separates the routers from the "
                  "extraction: an extracted task can lose the detail that made it sensitive.", ""]
        lines += agreement_table(run.canonical, run.canonical_tasks)
        lines += ["", "Median latency on the canonical tasks: " + ", ".join(
            f"{name} {_ms(statistics.median(values) if values else None)}"
            for name, values in canonical_latency.items()) + "."]
        lines += sure_and_wrong(run.canonical, run.canonical_tasks)
    lines += label_corrections(run)
    if getattr(run, "canonical_off", None):
        lines += side_by_side(run.canonical, run.canonical_off, run.canonical_tasks, "the canonical tasks")
    if getattr(run, "asked_off", None):
        lines += side_by_side(run.asked, run.asked_off, tasks, "the extracted tasks that matched")
    if getattr(run, "real", None):
        lines += real_section(run.real, getattr(run, "real_off", {}), run.real_tasks)
    if run.rotation is not None:
        r = run.rotation
        options = len(load_route_questions(settings)["project"].criteria)
        carried = (f" Carried over from `{run.rotation_from}` rather than measured in this run: only the project "
                   "answer is rotated, and the canonical tasks and project options are the same."
                   + (f" {run.rotation_note}" if run.rotation_note else "") if run.rotation_from else "")
        lines += ["", "## Option order (Strands Decider)", "",
                  f"The project options were rotated {r.rotations} ways (with {options} options, "
                  f"{'each option listed first once' if r.rotations == options else 'one step at a time'}) over "
                  f"{r.tasks} canonical tasks: {r.flipped} task(s) changed answer, a flip rate of "
                  f"**{r.flip_rate:.1%}**. The first-listed option was chosen in {r.first_picks} of {r.answers} "
                  f"answers ({r.first_pick_rate:.1%}; an order-blind model rotated through every order picks the "
                  f"first-listed option 1 time in {options}, {1 / options:.1%}). Threshold for the mitigation "
                  f"(`other` first, the rest alphabetical): above 10%, "
                  f"{'exceeded' if r.needs_mitigation else 'not exceeded'}.{carried}"]
        if run.prior_rotation is not None:
            q = run.prior_rotation
            moved = (f"did not move: {r.flipped} of {r.tasks} both times" if (q.flipped, q.tasks) == (r.flipped, r.tasks)
                     else f"moved from {q.flipped} of {q.tasks} ({q.flip_rate:.1%}) to {r.flipped} of {r.tasks} "
                          f"({r.flip_rate:.1%})")
            lines += ["", f"**Beside it, the prior measurement** from `{run.prior_rotation_from}`"
                          + (f" ({run.prior_rotation_note})" if run.prior_rotation_note else "")
                          + f": {q.rotations} orders over {q.tasks} tasks, {q.flipped} task(s) changed answer "
                          f"({q.flip_rate:.1%}), first-listed option {q.first_picks} of {q.answers} "
                          f"({q.first_pick_rate:.1%}). The flip rate {moved}."]
    lines += ["", "## Per task", "", "| recording | task (as extracted) | expected | " +
              " | ".join(run.asked) + " |", "|---|---|---|" + "---|" * len(run.asked)]
    for i, t in enumerate(tasks):
        exp = (f"{t.expected.project}, {t.expected.priority}, "
               f"{'commit' if t.expected.real_commitment else 'maybe'}, "
               f"{'SENSITIVE' if t.expected.sensitive else 'clear'}") if t.expected else "(unmatched)"
        cells = [_cell(run.asked[name][i]) for name in run.asked]
        lines.append(f"| {t.recording_id} | {_md(t.task.content)} | {exp} | " + " | ".join(cells) + " |")
    lines += ["", "## Bedrock spend (whole ledger, all purposes)", "", "```", json.dumps(spend, indent=2), "```", ""]
    return "\n".join(lines)


def label_corrections(run: Run) -> list[str]:
    """Where a label was corrected after the bench ran (Dan's ruling 40): each correction, and the questions it
    touches counted both ways, as first labelled and as corrected. The tables above count the corrected labels."""
    groups = [(f"{len(run.canonical_tasks)} canonical", run.canonical, run.canonical_tasks),
              (f"{len(run.tasks)} extracted", run.asked, run.tasks)]
    seen: dict[tuple[str, str], ExpectedTask] = {}
    for _, _, tasks in groups:
        for t in tasks:
            if t.expected is not None and t.expected.corrections:
                seen.setdefault((t.recording_id, t.expected.task.content), t.expected)
    if not seen:
        return []
    corrections = [(rid, e, c) for (rid, _), e in seen.items() for c in e.corrections]
    dates = sorted({c.on for _, _, c in corrections})
    questions = [q for q in QUESTIONS if any(c.label == q for _, _, c in corrections)]
    word = {True: "yes", False: "no", None: "-"}
    lines = ["", "## Label corrections", "",
             f"{len(corrections)} label(s) on {len(seen)} canonical task(s) were corrected on {', '.join(dates)} "
             "(Dan's ruling 40). Every table above counts the labels as corrected; each fixture keeps the label as "
             "first written beside its correction (`corrections` in its `expected` block).", "",
             "| recording | task | label | first labelled | corrected | reason |", "|---|---|---|---|---|---|"]
    for rid, e, c in corrections:
        now = getattr(e, c.label)
        lines.append(f"| {rid} | {_md(e.task.content)} | {c.label} | {word.get(c.was, c.was)} | "
                     f"{word.get(now, now)} | {_md(c.reason)} |")
    lines += ["", f"The questions they touch, counted both ways (agree / sure / agree when sure), as labelled on "
                  f"{FIRST_LABELLED} and as corrected on {', '.join(dates)}:", "",
              f"| tasks | backend | question | as labelled on {FIRST_LABELLED} | as corrected |", "|---|---|---|---|---|"]
    for name, by_backend, tasks in groups:
        first = [replace(t, expected=as_first_labelled(t.expected) if t.expected else None) for t in tasks]
        for backend, asked in by_backend.items():
            before, after = agreement(asked, first), agreement(asked, tasks)
            for q in questions:
                cell = [f"{s['agree']}/{s['matched']} / {s['sure']} / {s['sure_agree']}/{s['sure']}" for s in
                        (before[q], after[q])]
                lines.append(f"| {name} | {backend} | {q} | {cell[0]} | {cell[1]} |")
    return lines


def labels_now(run: Run) -> None:
    """A saved run keeps the labels it was scored with; a re-render reads them as the fixtures hold them now,
    corrections and all (Dan's ruling 40), so both label states can be shown."""
    current = expected_by_id()
    for t in run.canonical_tasks:
        [t.expected] = [e for e in current.get(t.recording_id, []) if e.task == t.task]
    for t in run.tasks:
        if t.expected is not None:
            t.expected = match_task(t.task, current.get(t.recording_id, []))


def sure_and_wrong(asked_by_backend: dict[str, list[Asked]], tasks: list[BenchTask]) -> list[str]:
    """Where a backend was sure (at or above the floor) and still disagreed with the label, per question.
    A question with sure-and-wrong answers is one its floor does not protect (Dan's ruling 8)."""
    lines = ["", "## Sure and wrong (canonical tasks)", "",
             "Answers at or above their floor that disagree with the label. Where a question has any, its floor "
             "does not protect it; the per-question tables above are the claim, question by question.", "",
             "| backend | question | sure | sure and wrong | labelled yes, said no | labelled no, said yes |",
             "|---|---|---|---|---|---|"]
    found = []
    for name, asked in asked_by_backend.items():
        for q in QUESTIONS:
            sure = wrong = yes_no = no_yes = 0
            for a, t in zip(asked, tasks):
                want = None if t.expected is None else _expected_answer(q, t.expected)
                j = a.judged.get(q)
                if want is None or j is None or j.problem or not _sure(q, j):
                    continue
                sure += 1
                if j.answer != want:
                    wrong += 1
                    yes_no += want is True and j.answer is False
                    no_yes += want is False and j.answer is True
            if wrong:
                boolean = q in NOULS
                lines.append(f"| {name} | {q} | {sure} | {wrong} | {yes_no if boolean else '-'} | "
                             f"{no_yes if boolean else '-'} |")
                found.append((name, q, wrong, sure))
    if not found:
        lines.append("| (none) | | | | | |")
    return lines


def side_by_side(on: dict[str, list[Asked]], off: dict[str, list[Asked]], tasks: list[BenchTask],
                 what: str) -> list[str]:
    """Ruling 15: agreement and sureness with the options' descriptions and with their names alone."""
    lines = ["", f"## With and without the project options' descriptions ({what})", "",
             "The same tasks asked twice: once with each project option's one-line description, once with the "
             "option names alone. agree / sure as above.", "",
             "| backend | question | agree, with | agree, names only | sure, with | sure, names only |",
             "|---|---|---|---|---|---|"]
    for name in on:
        if name not in off:
            continue
        a, b = agreement(on[name], tasks), agreement(off[name], tasks)
        for q in QUESTIONS:
            lines.append(f"| {name} | {q} | {_frac(a[q]['agree'], a[q]['matched'])} | "
                         f"{_frac(b[q]['agree'], b[q]['matched'])} | {_frac(a[q]['sure'], a[q]['matched'])} | "
                         f"{_frac(b[q]['sure'], b[q]['matched'])} |")
    return lines


def real_section(on: dict[str, list[Asked]], off: dict[str, list[Asked]], tasks: list[BenchTask]) -> list[str]:
    """Ruling 15: the live run's real tasks. They have no labels, so counts only, and no task text here (it stays
    in the git-ignored bench/raw/)."""
    lines = ["", f"## Real tasks (the live run's {len(tasks)}, re-run)", "",
             "Unlabelled, so there is no agreement to score: how many answers were sure, and, where both ways were "
             "asked, how many answers changed when the option descriptions were left out. No task text is shown.",
             "", "| backend | question | sure, with | sure, names only | answer changed |", "|---|---|---|---|---|"]
    for name, asked in on.items():
        other = off.get(name)
        for q in QUESTIONS:
            judged = [a.judged.get(q) for a in asked]
            sure = sum(1 for j in judged if j is not None and not j.problem and _sure(q, j))
            if other is None:
                lines.append(f"| {name} | {q} | {sure}/{len(asked)} | - | - |")
                continue
            judged_off = [a.judged.get(q) for a in other]
            sure_off = sum(1 for j in judged_off if j is not None and not j.problem and _sure(q, j))
            changed = sum(1 for j, k in zip(judged, judged_off)
                          if j is not None and k is not None and not j.problem and not k.problem and j.answer != k.answer)
            lines.append(f"| {name} | {q} | {sure}/{len(asked)} | {sure_off}/{len(other)} | {changed} |")
    return lines


def agreement_table(asked_by_backend: dict[str, list[Asked]], tasks: list[BenchTask]) -> list[str]:
    lines = ["| backend | question | agree | sure | agree when sure |", "|---|---|---|---|---|"]
    for name, asked in asked_by_backend.items():
        scores = agreement(asked, tasks)
        for q in QUESTIONS:
            s = scores[q]
            lines.append(f"| {name} | {q} | {_frac(s['agree'], s['matched'])} | {_frac(s['sure'], s['matched'])} | "
                         f"{_frac(s['sure_agree'], s['sure'])} |")
        g = scores["gate"]
        lines.append(f"| {name} | gate (hold?) | {_frac(g['agree'], g['matched'])} | | |")
    return lines


def run_notes(run: Run) -> list[str]:
    return [f"- {note}" for note in run.notes] + ([""] if run.notes else [])


def _cell(a: Asked) -> str:
    if a.error:
        return f"error: {a.error}"
    parts = []
    for q, short in (("project", ""), ("priority", ""), ("real_commitment", "commit "), ("sensitive", "sens ")):
        j = a.judged.get(q)
        if j is None or j.problem or j.answer is None:
            parts.append(f"{short}?")
            continue
        answer = {True: "yes", False: "no"}.get(j.answer, j.answer) if q in ("real_commitment", "sensitive") else j.answer
        if q == "priority":
            answer = f"{answer} ({SOURCE_SHORT.get(j.note or '', '-')})"
        mark = "" if _sure(q, j) else "~"
        confidence = "" if j.confidence is None else f" {j.confidence:.2f}"
        parts.append(f"{short}{answer}{mark}{confidence}")
    return ", ".join(parts) + (f" ({a.latency_ms:.0f} ms)" if a.latency_ms is not None else "")


def _md(text: str) -> str:
    return text.replace("|", "\\|")


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f} ms"


def _frac(a: int, b: int) -> str:
    return "-" if b == 0 else f"{a}/{b} ({a / b:.0%})"


# --- the estimate, shown before any call is made ---

def estimate(settings: Settings, *, option_descriptions: str = "on", real_tasks: int = 0) -> dict[str, Any]:
    """Worst case: every recording needs both retries (4 extraction calls); every expected task plus one
    extra per recording gets one routing call, and each real task one, twice over with "both" (ruling 15);
    every call uses all of max_tokens."""
    questions = load_route_questions(settings)
    m = settings.model
    system = extract.system_prompt(settings) + extract.OUTPUT_FORMAT_NOTE + json.dumps(extract.EXTRACTION_SCHEMA)
    ways = 2 if option_descriptions == "both" else 1
    state_bound = 2000  # a task, its notes and the contact line, in bytes, generously
    route_one = worst_case_usd(input_token_bound(answer_prompt(questions)) + state_bound, m.max_tokens,
                               m.price_in_per_m, m.price_out_per_m)
    extract_worst, extract_calls, route_calls = 0.0, 0, ways * real_tasks
    expected = expected_by_id()
    for recording, _ in recordings():
        if not isinstance(triage.decide(recording, None, settings), triage.Process):
            continue
        text = extract_text(recording)
        body = [{"role": "system", "content": system},
                {"role": "user", "content": extract.user_message(str(text), corrective=True)
                 + extract.SCHEMA_RETRY_NOTE}]
        extract_worst += 4 * worst_case_usd(input_token_bound(body), m.max_tokens, m.price_in_per_m, m.price_out_per_m)
        extract_calls += 4
        route_calls += ways * (2 * len(expected.get(str(recording["id"]), [])) + 1)  # extracted (one extra), canonical
    route_worst = route_calls * route_one
    return {"bedrock_calls_at_most": extract_calls + route_calls, "routing_calls_at_most": route_calls,
            "extraction_worst_usd": round(extract_worst, 4), "routing_worst_usd": round(route_worst, 4),
            "total_worst_usd": round(extract_worst + route_worst, 4)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--estimate", action="store_true", help="print the worst-case Bedrock spend and exit")
    parser.add_argument("--rotations", type=int, default=7, help="option-order rotations for Decider (0: skip)")
    parser.add_argument("--rotation-from", type=Path, default=None,
                        help="carry the option-order result over from an earlier run's raw file, or from a "
                             "rotation file written by bench/rotation_run.py (with --rotations 0)")
    parser.add_argument("--conditions", default="",
                        help="what else was (not) running on the machine, for RESULTS.md")
    parser.add_argument("--rotation-note", default="",
                        help="what differed when the carried rotation was measured, for RESULTS.md")
    parser.add_argument("--render-from", type=Path, default=None,
                        help="re-render RESULTS.md from a run's saved state (bench/raw/run-*.state.pickle), "
                             "with --rotation-from and --prior-rotation-from; no model is asked")
    parser.add_argument("--prior-rotation-from", type=Path, default=None,
                        help="an earlier rotation to show beside this one (with --render-from)")
    parser.add_argument("--prior-rotation-note", default="", help="what differed when the prior one was measured")
    parser.add_argument("--option-descriptions", choices=("on", "off", "both"), default="on",
                        help="the project options with their descriptions, without, or both side by side (ruling 15)")
    parser.add_argument("--real-tasks", type=Path, action="append", default=[],
                        help="a file from `ratatoskr-demo --once --dry-run --save-tasks`: real tasks to ask as well")
    parser.add_argument("--rotation-conditions", default=None,
                        help="with --render-from: the run conditions to state for the carried rotation, "
                             "instead of those its file recorded")
    parser.add_argument("--out", type=Path, default=RESULTS,
                        help="write the report here instead of bench/RESULTS.md (a development run, ruling 39)")
    parser.add_argument("--note", action="append", default=[],
                        help="with --render-from: a line to add to the report's notes (repeatable)")
    parser.add_argument("--as-measured", default=None, metavar="NOTE",
                        help="with --render-from: label the numbers [as measured], with NOTE, until a re-measure")
    args = parser.parse_args(argv)
    if args.rotation_from is not None and args.rotations and args.render_from is None:
        parser.error("--rotation-from replaces the measurement: use it with --rotations 0")

    settings = load_settings()
    if args.estimate:
        e = estimate(settings, option_descriptions=args.option_descriptions,
                     real_tasks=len(load_real_tasks(args.real_tasks)))
        spend = make_spend_ledger(settings)
        e["already_spent_usd"] = round(spend.total_usd(), 6)
        e["hard_stop_usd"] = settings.spend.hard_stop_usd
        print(json.dumps(e, indent=2))
        return 0

    if args.render_from is not None:
        return rerender(args, settings)

    refuse_while_loop_runs(settings.paths.state_dir, "the bench")
    settings = replace(settings, route=replace(settings.route, decider_timeout_seconds=BENCH_DECIDER_TIMEOUT))
    spend = make_spend_ledger(settings)
    bedrock = BedrockClient(settings, spend)
    bedrock.check_model_record()
    questions = load_route_questions(settings)
    decider = make_router(settings, questions)
    decider.check_access()
    jev = None
    if settings.secrets.typesafe_api_key:
        from ratatoskr_demo.jev import JevRouter, make_client

        jev = JevRouter(make_client(settings), questions)
    gate = make_source_gate(settings, questions)
    health = decider.client.health()
    versions = {
        "extraction model": f"`{settings.model.model_id}` on Bedrock (bedrock-mantle, us-east-1), reasoning effort "
                            f"`{settings.model.reasoning_effort}`, max_tokens {settings.model.max_tokens}",
        "Strands Decider": f"`{health.get('checkpoint')}` on {health.get('device')}, strands-decider "
                           f"{_decider_version()}, prefix cache {'on' if health.get('prefix_cache') else 'off'}",
        "Jev": f"`jev-latest` via typesafe-sdk {_version('typesafe-sdk')}" if jev else "skipped (no TYPESAFE_API_KEY)",
        "prices": f"Bedrock ${settings.model.price_in_per_m}/M in, ${settings.model.price_out_per_m}/M out "
                  f"(read {settings.model.docs_read_on}); Jev ${JEV_PRICE_IN_PER_M}/M in, output free; Decider $0",
        "questions": f"`ratatoskr_demo/prompts/route.toml` at git blob `{git_blob_hash(ROUTE_TOML)}`",
        "machine": "the build laptop's CPU (16 logical cores), Decider capped at 4 threads at below-normal "
                   f"priority, {BENCH_DECIDER_TIMEOUT:.0f} s per read at most"
                   + (f"; {args.conditions}" if args.conditions else ""),
    }
    run = run_bench(settings, bedrock=bedrock, decider=decider, jev=jev, rotations=args.rotations, gate=gate,
                    rotation_from=args.rotation_from, rotation_note=args.rotation_note,
                    option_descriptions=args.option_descriptions, real_tasks=load_real_tasks(args.real_tasks))
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    args.out.write_bytes(render(run, settings=settings, spend=spend.summary(), versions=versions,
                               when=when).encode("utf-8"))
    write_raw(run, when)
    state = save_state(run, versions=versions, spend=spend.summary(), when=when)
    print(f"wrote {args.out} and {state.relative_to(REPO)}; Bedrock ledger total "
          f"${spend.total_usd():.6f}")
    return 1 if run.stopped else 0


def save_state(run: Run, *, versions: dict[str, str], spend: dict[str, Any], when: str) -> Path:
    """Everything render() needs, so RESULTS.md can be re-rendered (another rotation) without asking a model.
    Git-ignored, like the raw file; it holds the extracted tasks' text, never a transcript."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"run-{when.replace(' ', 'T').replace(':', '')}.state.pickle"
    path.write_bytes(pickle.dumps({"run": run, "versions": versions, "spend": spend, "when": when}))
    return path


def rerender(args: argparse.Namespace, settings: Settings) -> int:
    """--render-from: RESULTS.md again from a saved run, with the rotation and the prior rotation given."""
    saved = pickle.loads(args.render_from.read_bytes())  # this bench's own file, written by save_state()
    run: Run = saved["run"]
    questions = load_route_questions(settings)
    if args.rotation_from is not None:
        run.rotation = carried_rotation(args.rotation_from, questions)
        run.rotation_from = args.rotation_from.name
        run.rotation_note = " ".join(s for s in (rotation_wording(args.rotation_from, questions,
                                                                  args.rotation_conditions), args.rotation_note) if s)
    if args.prior_rotation_from is not None:
        run.prior_rotation = carried_rotation(args.prior_rotation_from, questions)
        run.prior_rotation_from = args.prior_rotation_from.name
        run.prior_rotation_note = " ".join(s for s in (rotation_wording(args.prior_rotation_from, questions),
                                                       args.prior_rotation_note) if s)
    labels_now(run)
    run.notes = list(run.notes) + list(args.note)
    run.rerendered = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    run.as_measured = args.as_measured or ""
    versions = dict(saved["versions"])
    if args.conditions and "machine" in versions:  # the run's conditions, worded for the public report
        versions["machine"] = versions["machine"].split("; ", 1)[0] + f"; {args.conditions}"
    args.out.write_bytes(render(run, settings=settings, spend=saved["spend"], versions=versions,
                               when=saved["when"]).encode("utf-8"))
    print(f"re-rendered {args.out.name} from {args.render_from.name}")
    return 0


def write_raw(run: Run, when: str) -> None:
    """Per-call numbers for the write-up, in the git-ignored bench/raw/. Never transcript text."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, t in enumerate(run.tasks):
        row = {"recording": t.recording_id, "task": t.task.content, "due_string": t.task.due_string,
               "recorded_on": str(t.recorded_on), "deadline_near": t.deadline_near,
               "expected": None if t.expected is None else {q: _expected_answer(q, t.expected) for q in QUESTIONS}}
        for name, asked in run.asked.items():
            a = asked[i]
            row[name] = {"latency_ms": a.latency_ms, "input_tokens": a.input_tokens, "output_tokens": a.output_tokens,
                         "cost_usd": a.cost_usd, "error": a.error,
                         "answers": {q: {"answer": j.answer, "confidence": j.confidence, "floor": j.floor,
                                         "problem": j.problem, "note": j.note} for q, j in a.judged.items()}}
        rows.append(row)
    canonical = [{"recording": t.recording_id, "task": t.task.content,
                  **{name: {"latency_ms": asked[i].latency_ms, "error": asked[i].error,
                            "answers": {q: {"answer": j.answer, "confidence": j.confidence}
                                        for q, j in asked[i].judged.items()}}
                     for name, asked in run.canonical.items()}}
                 for i, t in enumerate(run.canonical_tasks)]
    rotation = None if run.rotation is None else {
        "carried_from": run.rotation_from,
        "note": run.rotation_note,
        "flip_rate": run.rotation.flip_rate,
        "choices": [{"recording": t.recording_id, "task": t.task.content, "choices": picks}
                    for t, picks in zip(run.canonical_tasks, run.rotation.choices)]}
    gated = [vars(g) for g in run.gated]
    stamp = when.replace(" ", "T").replace(":", "")
    (RAW_DIR / f"run-{stamp}.json").write_text(
        json.dumps({"source_gate": gated, "extracted": rows, "canonical": canonical, "rotation": rotation},
                   indent=2, default=str),
        encoding="utf-8")


def _version(dist: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(dist)
    except PackageNotFoundError:
        return "(version unknown)"


def _decider_version() -> str:
    """Strands Decider lives in its own venv (.venv-decider): read its version from the package metadata there."""
    for dist_info in sorted(REPO.glob(".venv-decider/**/strands_decider-*.dist-info")):
        return dist_info.name[len("strands_decider-"):-len(".dist-info")]
    return _version("strands-decider")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConfigurationError, RatatoskrError) as exc:
        print(f"run_bench: {exc}", file=sys.stderr)
        raise SystemExit(78)
