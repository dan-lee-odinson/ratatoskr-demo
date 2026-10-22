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

"""One poll cycle, and one /demo text: the only module that knows the order of the steps.

Every input reaches the cycle as a device envelope, from an input adapter
(devices/registry.py: list_new, source, fetch_text). The cycle never talks to
pocket.py or any other device directly, and runs with whichever inputs
[devices] enables: the /demo text alone, Pocket, the glasses stub. For each
envelope:

    ledger says done/keep/error?  -> skip (from source(): no text fetched)
    adapter.fetch_text()          the transcript, at this moment only (the envelope never holds it)
    triage.decide()               -> skip / not ready / hold / process
                                     keep -> already -> not ready -> hold tag -> ledger hold
                                     -> stop-list tripwire -> process; the tripwire runs
                                     before ANY model call, Strands Decider included
    under 8 words?                -> skip, marked done: no read, no notice (an accidental press, ruling 14)
    source-text gate              -> Strands Decider (127.0.0.1) reads the transcript in
                                     windows: sensitive or unsure HOLDS, before any Bedrock
                                     call (backend "decider" only; skipped for an ok release)
    extract.run()                 -> Extraction (openai.gpt-oss-20b on Bedrock)
    guard (C9)                    -> quoted the transcript? retry once, then HOLD
    /health                       -> asked before the cycle's first Decider read (ruling 9)
    route()                       -> six Decider judgments per task, one read
    sensitivity                   -> "sensitive" / "unsure" on any task HOLDS the recording
    todoist writer                -> one task each, deterministic uuid (C8)
    ledger: done, then tell Dan   -> one wrist-shaped Telegram message per task

The transcript lives only in local variables of `_handle` and `_process`,
inside one call: it is never stored on `self`, logged, put in an exception
message or written anywhere (C1). It leaves the process only to Bedrock, for
the extraction, and to the local Strands Decider, for the source-text gate.
Everything else that leaves is model output that passed the guard, plus ids,
labels and counts. Notices carry the recording id, never its title: Pocket
builds titles from the conversation (Dan's ruling 13).

Failure handling (C7): TransientError leaves the ledger untouched and backs
the recording off (a lost network waits at least a minute); TerminalError
marks it `error` once and tells Dan; a ConfigurationError stops the cycle and
tells Dan, touching nothing. The spend hard stop (spend.SpendStop) is a
configuration error that also stops the service.

Strands Decider missing (Dan's ruling 9): with backend "decider", a recording
that needs Decider waits (TransientError, nothing written) when /health does
not answer at the cycle's first read, or when a read fails or times out; a
failed read is never asked again, and no read follows it in that cycle. The
gate's `urgent` bypass is unchanged, but routing still waits for Decider.
After three cycles in a row that needed Decider and found it missing, one
notice gives the count of recordings waiting, and nothing else. A reply
Decider gave that cannot be used (an HTTP error, a non-JSON body, no answers)
makes the recording wait, at the gate and at routing alike, urgent or not;
a malformed `sensitive` answer counts as unsure and holds it (Dan's ruling
23). With backend "decider" a task is written with its sensitivity not
checked only under the urgent tag while Decider is unreachable (ruling 12).

A /demo text is processed the same way, under the same cycle lock as a poll,
and returns an Outcome instead of sending notices: the Telegram receiver
replies with it.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ratatoskr_demo import extract, guard, triage
from ratatoskr_demo.backoff import RetryBook
from ratatoskr_demo.config import Settings
from ratatoskr_demo.devices.envelope import Envelope, ImageRef, Text
from ratatoskr_demo.devices.registry import InputAdapter, Source
from ratatoskr_demo.devices.wrist_adapter import first_line, format_task
from ratatoskr_demo.due import recording_date
from ratatoskr_demo.errors import ConfigurationError, RatatoskrError, TerminalError, TransientError
from ratatoskr_demo.extract import Contact, ModelCall, Task
from ratatoskr_demo.ledger import LedgerEntry
from ratatoskr_demo.notify import Notifier
from ratatoskr_demo.photos import (REFERENCE_LINE, RELEASABLE, SCENE_ACTIONS, SCENE_HOLD, UNSURE, PhotoHold,
                                   keep_reference, owner_release, release_keyboard, release_sid, scene_keyboard,
                                   scene_line, scene_sid, waiting_on_line)
from ratatoskr_demo.decider import DeciderRejected, DeciderUnavailable
from ratatoskr_demo import resolve
from ratatoskr_demo.route import (URGENT_SOURCE, Routed, RoutedTask, Router, load_route_questions, route,
                                  urgent_unrouted)
from ratatoskr_demo.source_gate import SourceGate
from ratatoskr_demo.spend import SpendStop
from ratatoskr_demo.state import State
from ratatoskr_demo.todoist import TodoistClient, build_task_args
from ratatoskr_demo.transcript import NOT_READY, extract_text

log = logging.getLogger(__name__)

NETWORK_LOST_WAIT_SECONDS = 60.0  # a lost network: wait about a minute before the unit of work is retried


@dataclass
class CycleDeps:
    """Everything the cycle talks to. todoist and notifier are None in --dry-run, or when
    [devices] outputs leaves them out."""

    inputs: dict[str, InputAdapter]
    todoist: TodoistClient | None
    notifier: Notifier | None
    model_call: ModelCall | None
    retry: RetryBook
    router: Router | None = None  # set when [route] backend is "decider" or "jev"
    source_gate: SourceGate | None = None  # set when [route] backend is "decider"
    decider_health: Callable[[], bool] | None = None  # set when [route] backend is "decider": GET /health
    # --save-tasks (dry runs only): handed each guarded extraction, never the transcript (ruling 15)
    on_extraction: Callable[[str, str, Any], None] | None = None
    clock: Callable[[], float] = time.time
    out: Callable[[str], None] = field(default=print)


@dataclass
class TaskOutcome:
    routed: RoutedTask
    content: str  # as written to Todoist, with any "[maybe] " prefix


@dataclass
class Outcome:
    """What happened to one recording or /demo text. Labels and guarded output only."""

    status: str  # "done" | "hold" | "skip" | "not-ready" | "error" | "stopped" | "dry-run"
    reason: str | None = None
    tasks: list[TaskOutcome] = field(default_factory=list)
    sensitivity_unchecked: bool = False
    ungated: bool = False  # backend "none": no sensitivity check ran (ruling 37)
    project_name: str = ""


class Cycle:
    def __init__(self, settings: Settings, state: State, deps: CycleDeps, *, dry_run: bool = False) -> None:
        self.settings = settings
        self.state = state
        self.deps = deps
        self.dry_run = dry_run
        # --save-tasks (ruling 15): a dry run reads the recordings already done again, to capture their
        # extractions; a dry run writes nothing, so the ledger stays as it is
        self.revisit_done = False
        interval = settings.poll.interval_seconds
        self._list_retry = RetryBook(None, base_seconds=interval, cap_seconds=settings.poll.backoff_max_seconds)
        self._listed_ok: set[str] = set()
        self._lock = threading.Lock()  # a poll and a /demo text never run at the same time
        self.stopped: str | None = None  # set by the spend hard stop: the service stops
        # Dan's ruling 9: whether Strands Decider is there, for this cycle (None: not asked yet), the
        # recordings waiting on it, and how many cycles in a row it has been missing
        self._decider_up: bool | None = None
        self._waiting_on_decider: set[str] = set()
        self._decider_down_cycles = 0
        self._decider_notice_sent = False
        # Ruling 47: the photo being handled, if it is one; its gate probability, OCR confidences and
        # extraction call count go in its ledger entry (numbers only)
        self._image: dict[str, Any] | None = None
        self._photo = False  # ruling 49: a glasses envelope with an ImageRef; its hold notices name the folder
        self._scenes: Any = None  # ruling 50: the photo adapter, which asks the scene question about its pictures
        self._released_from: str | None = None  # ruling 52: the gate the owner released this photo from

    # --- entry points ---

    def run_job(self) -> str:
        """Scheduler entry point: one cycle, with configuration problems reported."""
        try:
            return self.run()
        except ConfigurationError as exc:
            return self._configuration_problem(exc)

    def run(self) -> str:
        with self._lock:
            return self._run()

    def process_text(self, rid: str, envelope: Envelope) -> Outcome:
        """The /demo path: one Text envelope through the full cycle, now. Never raises."""
        if not isinstance(envelope.payload, Text):
            raise TypeError("process_text takes a Text envelope")
        text_input = self.deps.inputs.get("text")
        if text_input is None:
            return Outcome("error", reason='the "text" input is not enabled in [devices] inputs')
        with self._lock:
            if self.stopped:
                return Outcome("stopped", reason=self.stopped)
            self._decider_up = None  # a /demo text checks /health afresh before any read
            text_input.submit(rid, envelope)
            try:
                return self._handle(text_input, envelope, self.state.ledger_get(rid), demo=True)
            except ConfigurationError as exc:
                self._configuration_problem(exc)
                return Outcome("stopped", reason=str(exc))
            except TransientError as exc:
                log.warning("demo %s: %s; nothing written, send it again", rid, exc)
                return Outcome("error", reason=f"transient: {exc}")
            except TerminalError as exc:
                log.error("demo %s: terminal failure: %s", rid, exc)
                return Outcome("error", reason=str(exc))
            finally:
                text_input.done(envelope)

    def _run(self) -> str:
        now = self.deps.clock()
        interval = self.settings.poll.interval_seconds
        if self.stopped:
            return f"stopped: {self.stopped}"
        self._flush_pending_notices()
        self._decider_up = None  # ruling 9: /health is asked again before this cycle's first read

        listed = worked = 0
        problems: list[str] = []
        next_in = float(interval)
        for name, adapter in self.deps.inputs.items():
            key = f"list:{name}"
            if not self._list_retry.ready(key, now):
                problems.append(f"waiting: backing off from {name}")
                continue
            try:
                envelopes = adapter.list_new()
            except TransientError as exc:
                record = self._list_retry.failed(key, now)
                next_in = min(next_in, record.next_attempt_at - now)
                log.warning("%s not reachable (%s); next try in %ds", name, exc, record.next_attempt_at - now)
                problems.append(f"transient: {exc}")
                continue
            except TerminalError as exc:
                log.error("%s list: %s", name, exc)
                if self.state.first_time(f"{name}-contract:{exc}"):
                    self._tell_dan(f"The {name} input's list came back in an unexpected shape: {exc} {list(exc.keys)}")
                problems.append(f"error: {exc}")
                continue
            if (self._list_retry.succeeded(key) or name not in self._listed_ok) and name != "text":
                log.info("network up")
            self._listed_ok.add(name)
            listed += len(envelopes)
            for envelope in envelopes:
                if self._visit(adapter, envelope, now):
                    worked += 1
        if worked == 0:
            log.info("no new recordings")
        self._after_decider_cycle()
        self.state.record_poll(next_in_seconds=next_in)
        if problems and listed == 0:
            return problems[0]
        counts = self.state.counts()
        return (
            f"ok: {listed} listed, {worked} examined; today {counts.processed} processed, "
            f"{counts.held} held, {counts.errors} errors"
        )

    # --- one recording ---

    def _visit(self, adapter: InputAdapter, envelope: Envelope, now: float) -> bool:
        """Handle one envelope from an input. Returns True if its text had to be fetched."""
        source = adapter.source(envelope)
        rid = source.item_id
        entry = self.state.ledger_get(rid)
        if entry is not None and entry.status == "done" and self.revisit_done and self.dry_run:
            entry = None  # read again as if new; this dry run writes nothing
        if entry is not None and entry.status in ("done", "keep", "error"):
            self._keep_tag_after_done(rid, source, entry)
            return False
        if not self.deps.retry.ready(rid, now):
            return False
        try:
            self._handle(adapter, envelope, entry, demo=False)
            self.deps.retry.succeeded(rid)
            self._waiting_on_decider.discard(rid)
        except TransientError as exc:
            self._transient(rid, exc, now)
        except TerminalError as exc:
            try:
                self._terminal(rid, source, exc)
                self.deps.retry.succeeded(rid)
            except TransientError as notify_exc:
                self._transient(rid, notify_exc, now)
        return True

    def _handle(self, adapter: InputAdapter, envelope: Envelope, entry: LedgerEntry | None, *,
                demo: bool) -> Outcome:
        source = adapter.source(envelope)
        rid, title, recording_at = source.item_id, source.title, source.recorded_at
        photo = envelope.kind == "glasses" and isinstance(envelope.payload, ImageRef)
        # ruling 52: the gate the owner released this held photo from; it continues from the check after it
        self._released_from = owner_release(entry) if photo else None
        if self._released_from is not None:
            text = adapter.fetch_text(envelope, released_from=self._released_from)  # memory only (C1)
        else:
            text = adapter.fetch_text(envelope)  # memory only, from here to the end of this call (C1)
        self._image = (getattr(adapter, "records", None) or {}).get(rid)
        self._photo = photo
        self._scenes = adapter if self._photo and hasattr(adapter, "decide_scene") else None
        if self._released_from is not None and self._image is not None:
            earlier = entry.image  # the release and the image gate's earlier read stay in the ledger
            self._image.update({k: earlier[k] for k in ("gate_p", "gate_seconds") if k in earlier})
            self._image["releases"] = [dict(r) for r in earlier.get("releases") or []]
            entry = None
        if isinstance(text, PhotoHold):  # ruling 47: unreadable (no model asked) or held by the image gate
            return self._hold(rid, title, recording_at, text.reason, demo=demo,
                              notice=f"Held {rid}: {text.reason}. Nothing was written.",
                              keyboard=self._offer_release(rid, text.reason))
        if (self._photo and entry is not None and entry.status == "hold" and entry.reason == SCENE_HOLD
                and entry.image.get("scene_by") == "owner"):
            entry = None  # ruling 50: the owner answered the scene question; the chosen path runs now
        recording = {"id": rid, "title": title, "tags": [{"name": tag} for tag in source.tags],
                     "transcript": None if text is NOT_READY else text}
        decision = triage.decide(recording, entry, self.settings)
        if self.dry_run:
            self.deps.out(f"{rid}  {title!r}: {type(decision).__name__}{_reason(decision)}")

        if isinstance(decision, triage.Skip):
            if decision.reason == "keep" and not self.dry_run:
                self.state.ledger_put(rid, self._entry("keep", title, recording_at, reason="tag:keep"))
                log.info("recording %s %r: tagged keep, never processed", rid, title)
            return Outcome("skip", reason=decision.reason)
        if isinstance(decision, triage.NotReady):
            self.state.count("not_ready", recording_id=rid)
            return Outcome("not-ready")
        if isinstance(decision, triage.Hold):
            return self._hold(rid, title, recording_at, decision.reason, demo=demo,
                              notice=self._held_notice(rid, notice_reason(decision.reason)))
        if isinstance(decision, (triage.Release, triage.Process)):
            return self._process(rid, recording, title, recording_at,
                                 released=isinstance(decision, triage.Release), demo=demo)
        return Outcome("hold", reason="still held")  # StillHeld: nothing to do and nothing to say

    def _process(self, rid: str, recording: dict[str, Any], title: str, recording_at: str, *,
                 released: bool, demo: bool) -> Outcome:
        text = extract_text(recording)
        if text is NOT_READY:  # triage already checked; this keeps the types honest
            return Outcome("not-ready")
        # ruling 53: a photo is never an accidental press; its guard is the readability check (no words: unreadable)
        words = None if self._photo else too_short(str(text))
        if words is not None:  # ruling 14: no read, no notice; marked seen, so it is not looked at again
            log.info("recording %s: transcript of %d word(s), under %d; skipped before the source gate",
                     rid, words, MIN_TRANSCRIPT_WORDS)
            if not self.dry_run:
                self.state.ledger_put(rid, self._entry("done", title, recording_at, reason="too-short",
                                                       scrubbable=not demo))
            return Outcome("skip", reason="too-short")
        urgent = self.settings.triage.tag_urgent.lower() in triage.tag_names(recording)
        # before any Bedrock call; a photo the owner released from this gate (ruling 52) is not asked again
        if self.deps.source_gate is not None and not released and self._released_from != RELEASABLE["source-unsure"]:
            verdict = self._gate_source(rid, text, urgent)
            if verdict is not None and verdict.hold:
                return self._hold(rid, title, recording_at, verdict.reason, demo=demo,
                                  notice=self._held_notice(rid, verdict.reason),
                                  keyboard=self._offer_release(rid, verdict.reason))
        waiting_on = False  # ruling 50: a picture of something owed to the owner; its tasks carry `waiting-on`
        if self._scenes is not None:  # after the text gate, before any cloud call
            scene = self._scene(rid, str(text))
            if scene == "reference":
                return self._keep_reference(rid, title, recording_at, str(text), demo=demo)
            if scene == UNSURE:
                return self._ask_scene(rid, title, recording_at, demo=demo)
            waiting_on = scene == "owed_to_owner"
        # Routing will need Decider: while it is unreachable, a recording waits with nothing extracted or
        # written (ruling 9), unless it is tagged urgent (ruling 12: written unrouted, below)
        without_decider = not self._decider_usable(rid, urgent)
        if self.deps.model_call is None:
            self.deps.out("    (extraction skipped: no BEDROCK_API_KEY in .env)")
            return Outcome("dry-run", reason="no model")
        n = self.settings.guard.ngram_words

        model_call = self._counted(self.deps.model_call)
        extraction = extract.run(text, self.settings, model_call)
        if guard.any_overlap(text, extraction.texts(), n):
            log.warning("recording %s: model output quoted the transcript; retrying with a corrective instruction", rid)
            extraction = extract.run(text, self.settings, model_call, corrective=True)
            if guard.any_overlap(text, extraction.texts(), n):
                return self._leak_hold(rid, title, recording_at, demo=demo)

        if self.deps.on_extraction is not None:  # guarded model output only: what Todoist would be sent
            self.deps.on_extraction(rid, recording_at, extraction)
        if without_decider:  # ruling 12: urgent, Decider unreachable this cycle: no read is sent
            routed = Routed(contact=extraction.contact, tasks=tuple(urgent_unrouted(t) for t in extraction.tasks),
                            confidence=extraction.confidence)
        else:
            routed = route(extraction, self.settings, self.deps.router,  # after the guard, before the write
                           today=recording_date(recording_at))
        if routed.rejected:  # ruling 23: an unusable reply makes the recording wait, as at the gate; urgent or not
            self._waiting_on_decider.add(rid)
            raise TransientError(f"routing: unusable Strands Decider reply ({routed.rejected}); waiting",
                                 kind="decider")
        if routed.unreached:  # a read failed or timed out: never re-asked, and none sent again this cycle
            self._decider_up = False
            if not urgent:  # ruling 9: the recording waits; nothing is written
                self._waiting_on_decider.add(rid)
                raise TransientError("Strands Decider read failed; waiting, nothing written", kind="decider")
            # ruling 12: the verdicts Decider gave stand; the tasks it did not reach are written unrouted
            reached = routed.tasks[:len(routed.tasks) - routed.unreached]
            unreached = routed.tasks[len(routed.tasks) - routed.unreached:]
            routed = Routed(contact=routed.contact, tasks=reached + tuple(urgent_unrouted(t.task) for t in unreached),
                            confidence=routed.confidence)
        self._routing_notices()
        # route() only annotates; acting on sensitivity is the cycle's job, and
        # it happens before anything is written. Dan's `ok` tag means he has
        # reviewed the recording: it is not checked again.
        ungated = self.settings.route.backend == "none"  # ruling 37: no gate ran; every task says so
        verdict = sensitivity_verdict(routed.tasks) if not ungated else None
        if released and verdict is not None:
            log.info("recording %s: sensitivity %s, but released by ok; writing it", rid, verdict)
            verdict = None
        if verdict in ("sensitive", "sensitive-unsure"):
            log.warning("recording %s: %s; held for review, nothing written", rid, verdict)
            outcome = self._hold(rid, title, recording_at, verdict, demo=demo,
                                 notice=self._held_notice(rid, verdict))
            outcome.tasks = [TaskOutcome(routed=task, content="") for task in routed.tasks]  # judgments only
            return outcome
        unchecked = verdict == "not-checked"
        if unchecked and self.dry_run:
            self.deps.out(f"    {NOT_CHECKED}")
        tasks = list(routed.tasks) or [no_followups_task(routed.contact)]

        project = self.settings.todoist.inbox_project_name
        writes = self.deps.todoist is not None  # off in a dry run, or when [devices] outputs omits todoist
        if self.dry_run:
            project_id = "(dry run)"
        else:
            project_id = self._todoist().ensure_project(project) if writes else "(todoist output off)"
        payloads = [
            build_task_args(task, recording_id=rid, ordinal=i, contact=routed.contact,
                            project_id=project_id, label=self.settings.todoist.label, ungated=ungated,
                            waiting_on=waiting_on)
            for i, task in enumerate(tasks, start=1)
        ]
        # Check the final strings too: joining notes and context into one
        # description must not create a span that neither field had alone.
        if guard.any_overlap(text, [p["content"] for p in payloads] + [p["description"] for p in payloads], n):
            return self._leak_hold(rid, title, recording_at, demo=demo)
        outcome = Outcome("done", tasks=[TaskOutcome(routed=t, content=p["content"]) for t, p in zip(tasks, payloads)],
                          sensitivity_unchecked=unchecked, ungated=ungated,
                          project_name=project if writes or self.dry_run else "")

        if self.dry_run:
            for args in payloads:
                self.deps.out(f"    would create: {args['content']}  labels={args['labels']}"
                              f"  priority={args.get('priority', '-')}")
            outcome.status = "dry-run"
            return outcome

        if writes:
            todoist = self._todoist()
            task_ids = [todoist.create_task(args, recording_id=rid, ordinal=i)
                        for i, args in enumerate(payloads, start=1)]
        else:
            task_ids = []
            log.info("recording %s: the todoist output is off; %d task(s) not written", rid, len(payloads))
        picks = {} if demo else self._unsorted_picks(rid, outcome.tasks, payloads, task_ids)
        self.state.ledger_put(
            rid,
            self._entry("done", title, recording_at, todoist_task_ids=[t for t in task_ids if t],
                        released_by_ok=released, scrubbable=not demo, sensitivity_not_checked=unchecked,
                        picks=picks),
        )
        self.state.count("processed")
        routed_count = sum(1 for task in tasks if task.is_routed and task.priority_source != URGENT_SOURCE)
        self.state.count("routed", routed_count)
        self.state.count("unrouted", len(tasks) - routed_count)
        if writes:
            log.info("recording %s %r: %d task(s) written to %s", rid, title, len(payloads), project)
        if not demo:
            for i, task in enumerate(outcome.tasks, start=1):  # one wrist-shaped message per task
                text = format_task(task.routed, task.content, sensitivity_unchecked=unchecked, ungated=ungated)
                if waiting_on:  # ruling 50: `waiting-on · <priority> · <scene p>`
                    text = waiting_on_line(text, first_line(task.routed), (self._image or {}).get("scene_p"))
                sid = resolve.short_id(rid, i)
                if sid in picks:  # unsorted: the owner can file it from the message (ruling 43)
                    self._tell_dan_with_keyboard(text, self._unsorted_keyboard(sid, task.routed))
                else:
                    self._tell_dan(text)
        return outcome

    # --- unsorted tasks, filed by the owner from Telegram (ruling 43) ---

    def _unsorted_picks(self, rid: str, tasks: list[TaskOutcome], payloads: list[dict[str, Any]],
                        task_ids: list[str | None]) -> dict[str, dict[str, Any]]:
        """A record for each task written with its project unsure, when Telegram can carry its buttons."""
        if self.dry_run or self.deps.notifier is None:
            return {}
        picks = {}
        for i, (task, args, task_id) in enumerate(zip(tasks, payloads, task_ids), start=1):
            if task_id and _project_unsure(task.routed):
                priority, p = resolve.first_line_parts(first_line(task.routed))
                picks[resolve.short_id(rid, i)] = resolve.pick_record(ordinal=i, task_id=task_id,
                                                                      labels=args["labels"], priority=priority, p=p)
        return picks

    def _unsorted_keyboard(self, sid: str, routed: RoutedTask) -> dict[str, Any]:
        judged = {j.question: j for j in routed.trace}.get("project")
        projects = list(load_route_questions(self.settings)["project"].criteria)
        return resolve.keyboard(sid, projects, dict(judged.options) if judged else None,
                                top=judged.answer if judged else None)

    def _tell_dan_with_keyboard(self, text: str, reply_markup: dict[str, Any]) -> None:
        """As _tell_dan, with the buttons. If Telegram is down the notice is queued without them."""
        if self.dry_run or self.deps.notifier is None:
            return
        try:
            self.deps.notifier.send_with_keyboard(text, reply_markup)
        except TransientError as exc:
            log.warning("telegram not reachable (%s); notice queued without its buttons", exc)
            self.state.queue_notice(text)
        except RatatoskrError as exc:
            log.error("telegram: %s", exc)

    def _gate_source(self, rid: str, text: str, urgent: bool) -> Any:
        """The source-text gate. Decider unreachable: any recording waits, except one tagged urgent, which goes
        past the gate (ruling 12). An unusable reply is not unreachable: Decider answered, so everyone waits."""
        if not self._decider_usable(rid, urgent):  # ruling 9: /health before the cycle's first read
            return None
        try:
            return self.deps.source_gate.check(text)
        except DeciderUnavailable as exc:
            self._decider_up = False  # a read failed: none is sent again this cycle
            if urgent:
                log.warning("recording %s: source gate could not run (%s); tagged %s and Decider unreachable, "
                            "so it goes on without it", rid, exc, self.settings.triage.tag_urgent)
                return None
            self._waiting_on_decider.add(rid)
            raise TransientError(f"source gate: Strands Decider could not be asked ({exc}); waiting",
                                 kind="decider") from None
        except DeciderRejected as exc:
            self._waiting_on_decider.add(rid)
            raise TransientError(f"source gate: unusable Strands Decider reply ({exc}); waiting",
                                 kind="decider") from None

    # --- Strands Decider missing (Dan's ruling 9) ---

    def _decider_usable(self, rid: str, urgent: bool) -> bool:
        """Before any Decider read: /health once per cycle (ruling 9). True: ask it. Unreachable this cycle:
        a recording tagged urgent goes on without Decider (False, ruling 12); any other waits, nothing written."""
        if self.deps.decider_health is None:
            return True  # backend "none" or "jev": no Decider to wait for
        if self._decider_up is None:
            self._decider_up = bool(self.deps.decider_health())
        if self._decider_up:
            return True
        if urgent:
            log.warning("recording %s: Strands Decider unreachable and the recording is tagged %s: written unrouted",
                        rid, self.settings.triage.tag_urgent)
            return False
        self._waiting_on_decider.add(rid)
        raise TransientError("Strands Decider is not answering; waiting, nothing written", kind="decider")

    def _after_decider_cycle(self) -> None:
        """Count cycles in a row that needed Decider and found it missing; at the third, one notice."""
        if self._decider_up is None:
            return  # nothing asked for Decider this cycle
        if self._decider_up:
            self._decider_down_cycles, self._decider_notice_sent = 0, False
            return
        self._decider_down_cycles += 1
        if self._decider_down_cycles >= DECIDER_DOWN_CYCLES_BEFORE_NOTICE and not self._decider_notice_sent:
            n = len(self._waiting_on_decider)
            notice = f"{n} item{'s' if n != 1 else ''} waiting on Decider"
            log.warning("%s (%d cycles in a row without it)", notice, self._decider_down_cycles)
            self._tell_dan(notice)  # a count only: no text, no title
            self._decider_notice_sent = True

    # --- outcomes ---

    def _hold(self, rid: str, title: str, recording_at: str, reason: str, *, notice: str, demo: bool,
              keyboard: dict[str, Any] | None = None) -> Outcome:
        if self.dry_run:
            self.deps.out(f"    would hold: {reason}")
            return Outcome("hold", reason=reason)
        existing = self.state.ledger_get(rid)
        if existing is not None and existing.status == "hold" and self._released_from is None:
            return Outcome("hold", reason=reason)  # already held and already announced: no second notice
        # Notice first: if Telegram is down, the ledger stays untouched and the
        # hold is re-decided next poll, so Dan is never left un-told (C7). A
        # /demo hold is announced by the receiver's reply instead. A photo released
        # by the owner and held again by a later check is a new hold, announced.
        if not demo and self.deps.notifier is not None:  # no telegram output: logged only
            if keyboard is not None:  # ruling 52: an unsure photo's Release
                self._notifier().send_with_keyboard(notice, keyboard)
            else:
                self._notifier().send_message(notice)
        self.state.ledger_put(rid, self._entry("hold", title, recording_at, reason=reason))
        self.state.count("held")
        log.info("recording %s %r: held (%s)", rid, title, reason)
        return Outcome("hold", reason=reason)

    def _leak_hold(self, rid: str, title: str, recording_at: str, *, demo: bool) -> Outcome:
        log.warning("recording %s: held by the leak guard; nothing written", rid)
        return self._hold(rid, title, recording_at, "leak-guard", demo=demo,
                          notice=self._held_notice(rid, "leak-guard"))  # ruling 28

    def _offer_release(self, rid: str, reason: str) -> dict[str, Any] | None:
        """Ruling 52: a photo held as unsure by the image gate or the text gate gets one button, Release; a photo
        held as sensitive (a yes from either gate) gets none."""
        if not self._photo or reason not in RELEASABLE or self._image is None:
            return None
        gate = RELEASABLE[reason]
        sid = release_sid(rid, gate)
        releases = self._image.setdefault("releases", [])
        if not any(r.get("sid") == sid for r in releases):
            releases.append({"held_by": gate, "sid": sid, "by": None, "at": None})
        return release_keyboard(sid)

    # --- the scene of a photo (ruling 50) ---

    def _scene(self, rid: str, text: str) -> str:
        """The owner's answer to an earlier unsure scene, or one read of the vision server. Goes in the ledger."""
        record = self._image if self._image is not None else {}
        earlier = self.state.ledger_get(rid)
        if earlier is not None and earlier.image.get("scene_by") == "owner":
            answer = str(earlier.image["scene"])
            record.update(scene=answer, scene_by="owner", scene_p=earlier.image.get("scene_p"))
        else:
            result = self._scenes.decide_scene(rid, text)
            answer = result.answer
            record.update(scene=answer, scene_choice=result.choice, scene_p=round(result.p, 4),
                          scene_options=dict(result.options), scene_seconds=round(result.seconds, 3),
                          scene_by="decider")
        record["scene_action"] = SCENE_ACTIONS[answer]
        log.info("photo %s: scene %s (p %s, by %s)", rid, answer, record.get("scene_p"), record["scene_by"])
        return answer

    def _keep_reference(self, rid: str, title: str, recording_at: str, text: str, *, demo: bool) -> Outcome:
        """Reference material: kept on the laptop, nothing sent to any cloud service, nothing written to Todoist."""
        if self.dry_run:
            self.deps.out("    would keep it on the laptop as reference")
            return Outcome("dry-run", reason="reference")
        keep_reference(self.settings.paths.state_dir / "reference", rid, recording_at[:10], text)
        if not demo:
            self._tell_dan(REFERENCE_LINE)
        self.state.ledger_put(rid, self._entry("done", title, recording_at, reason="reference"))
        self.state.count("processed")
        log.info("photo %s: reference, kept on the laptop", rid)
        return Outcome("done", reason="reference")

    def _ask_scene(self, rid: str, title: str, recording_at: str, *, demo: bool) -> Outcome:
        """Unsure: nothing sent anywhere but the question to the owner, with three buttons (ruling 43's receiver)."""
        record = self._image if self._image is not None else {}
        sid = scene_sid(rid)
        record["scene_sid"] = sid
        if self.dry_run:
            self.deps.out("    would ask the owner: scene unsure")
            return Outcome("hold", reason=SCENE_HOLD)
        # The question first: if Telegram is down, nothing is recorded and it is asked again next poll (C7).
        if not demo and self.deps.notifier is not None:
            self._notifier().send_with_keyboard(scene_line(record.get("scene_p")), scene_keyboard(sid))
        self.state.ledger_put(rid, self._entry("hold", title, recording_at, reason=SCENE_HOLD))
        self.state.count("held")
        log.info("photo %s: scene unsure; the owner is asked", rid)
        return Outcome("hold", reason=SCENE_HOLD)

    def _held_notice(self, rid: str, reason: str) -> str:
        """A recording is released by its ok tag in Pocket; a photo has no release yet and stays in the inbox
        folder (ruling 49)."""
        if self._photo:
            return f"Held {rid}: {reason} (photo). It stays in the inbox folder."
        return f"Held {rid}: {reason}. Tag it ok in Pocket to release it."

    def _terminal(self, rid: str, source: Source, exc: TerminalError) -> None:
        keys = f" (keys: {', '.join(exc.keys)})" if exc.keys else ""
        if self.dry_run:
            self.deps.out(f"    would mark error: {exc}{keys}")
            return
        log.error("recording %s: terminal failure: %s%s", rid, exc, keys)
        if self.deps.notifier is not None:
            self._notifier().send_message(f"Error on recording {rid}: {exc}{keys}. It will not be retried.")
        self.state.ledger_put(rid, self._entry("error", source.title, source.recorded_at, reason=str(exc)))
        self.state.count("errors")

    def _transient(self, rid: str, exc: TransientError, now: float) -> None:
        wait = NETWORK_LOST_WAIT_SECONDS if exc.kind == "network" else 0.0
        record = self.deps.retry.failed(rid, now, min_delay=wait)
        log.warning("recording %s: %s; will retry in %ds", rid, exc, record.next_attempt_at - now)
        if self.deps.retry.needs_stuck_notice(rid, now) and not self.dry_run:
            self._tell_dan(f"Recording {rid} has been failing to process for over 24 hours ({exc.kind}). Still retrying.")
            self.deps.retry.mark_stuck_notified(rid)

    def _keep_tag_after_done(self, rid: str, source: Source, entry: LedgerEntry) -> None:
        """A `keep` tag added after processing takes the recording off the scrub list."""
        if entry.scrubbable and self.settings.triage.tag_keep.lower() in source.tags and not self.dry_run:
            entry.scrubbable = False
            self.state.ledger_put(rid, entry)

    def _configuration_problem(self, exc: ConfigurationError) -> str:
        log.error("configuration problem: %s", exc)
        if isinstance(exc, SpendStop):
            self.stopped = str(exc)
        if self.state.first_time(f"config:{type(exc).__name__}" if isinstance(exc, SpendStop) else f"config:{exc}"):
            self._tell_dan(f"Ratatoskr Demo needs attention: {exc}")
        return f"error: {exc}"

    # --- helpers ---

    def _counted(self, model_call: ModelCall) -> ModelCall:
        """For a photo, the same model call, counted for its ledger entry (ruling 47)."""
        image = self._image
        if image is None:
            return model_call

        def counted(*args: Any, **kwargs: Any) -> Any:
            image["extraction_calls"] = image.get("extraction_calls", 0) + 1
            return model_call(*args, **kwargs)

        return counted

    def _entry(self, status: str, title: str, recording_at: str, **kwargs: Any) -> LedgerEntry:
        if self._image is not None:
            kwargs.setdefault("image", dict(self._image))
        decided = datetime.fromtimestamp(self.deps.clock(), tz=timezone.utc).isoformat(timespec="seconds")
        return LedgerEntry(status=status, title=title, recording_at=recording_at, decided_at=decided, **kwargs)

    def _tell_dan(self, text: str) -> None:
        """Best-effort notice. If Telegram is down, keep it in memory for the next cycle."""
        if self.dry_run or self.deps.notifier is None:
            return
        try:
            self.deps.notifier.send_message(text)
        except TransientError as exc:
            log.warning("telegram not reachable (%s); notice queued", exc)
            self.state.queue_notice(text)
        except RatatoskrError as exc:  # a notice failing must never undo work already done
            log.error("telegram: %s", exc)

    def _flush_pending_notices(self) -> None:
        if self.deps.notifier is None:
            return
        pending = self.state.take_pending_notices()
        for i, text in enumerate(pending):
            try:
                self._notifier().send_message(text)
            except (RatatoskrError, RuntimeError):
                for rest in pending[i:]:
                    self.state.queue_notice(rest)
                return

    def _routing_notices(self) -> None:
        """A routing problem never fails the recording; Dan hears about each kind once."""
        take = getattr(self.deps.router, "take_notices", None)
        for text in take() if take else []:
            if self.state.first_time(f"route:{text}"):
                self._tell_dan(text)

    def _todoist(self) -> TodoistClient:
        if self.deps.todoist is None:
            raise RuntimeError("todoist client not configured")
        return self.deps.todoist

    def _notifier(self) -> Notifier:
        if self.deps.notifier is None:
            raise RuntimeError("telegram notifier not configured")
        return self.deps.notifier


NOT_CHECKED = "sensitivity not checked"
DECIDER_DOWN_CYCLES_BEFORE_NOTICE = 3  # Dan's ruling 9
MIN_TRANSCRIPT_WORDS = 8  # Dan's ruling 14: fewer is an accidental press, skipped before the source gate
_SPEAKER = re.compile(r"\bSpeaker \d+:")


def sensitivity_verdict(tasks: tuple[RoutedTask, ...] | list[RoutedTask]) -> str | None:
    """What the cycle does about one recording's sensitivity (the private repository's rulings).

      any task "sensitive"           -> "sensitive"         hold
      else any task "unsure"         -> "sensitive-unsure"  hold
      else any task not checked      -> "not-checked"       write, and say so
      else (all clear, or no tasks)  -> None                write
    """
    states = {task.sensitivity for task in tasks}
    if "sensitive" in states:
        return "sensitive"
    if "unsure" in states:
        return "sensitive-unsure"
    if None in states:
        return "not-checked"
    return None


def notice_reason(reason: str) -> str:
    """Ruling 13: a hold notice carries the recording id and the kind of hold, never the title, and never
    the stop-list term: Pocket builds titles from the conversation, and the term is the name the
    tripwire keeps off shared surfaces."""
    return "stop-list" if reason.startswith("stoplist:") else reason


def too_short(text: str) -> int | None:
    """Ruling 14: the word count of a transcript under MIN_TRANSCRIPT_WORDS (speaker labels not counted),
    else None. An accidental press has no conversation to judge."""
    words = len(_SPEAKER.sub(" ", text).split())
    return words if words < MIN_TRANSCRIPT_WORDS else None


def _project_unsure(routed: RoutedTask) -> bool:
    """Decider answered the project question, below its floor: the task is unsorted, and the owner may file it."""
    if routed.priority_source == URGENT_SOURCE:
        return False
    judged = {j.question: j for j in routed.trace}.get("project")
    return judged is not None and not judged.problem and judged.value is None


def no_followups_task(contact: Contact) -> RoutedTask:
    """The one task written when a conversation had no commitments, so the contact is not lost."""
    name = contact.name or "an unnamed contact"
    org = f" ({contact.organisation})" if contact.organisation else ""
    return RoutedTask(task=Task(content=f"Met {name}{org} — no follow-ups", due_string=None, notes=None))


def _reason(decision: triage.Decision) -> str:
    reason = getattr(decision, "reason", None)
    return f" ({reason})" if reason else ""
