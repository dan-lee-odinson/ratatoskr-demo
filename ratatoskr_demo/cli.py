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

"""Command line: ratatoskr-demo --once | --loop | --dry-run | --scrub-list | --status | ...

    --once                 one poll cycle, then exit
    --loop                 run until stopped: the scheduler (30 s polls) and the
                           Telegram receiver (/status, /demo <text>)
    --dry-run              with --once: print decisions and the tasks it WOULD
                           create; writes nothing (no Todoist, no Telegram, no ledger)
    --scrub-list           print done recordings you can delete in Pocket, oldest first
    --mark-scrubbed ID..   flip those ids off the scrub list after deleting them
    --status               print the same summary /status gives on Telegram
    --spend                print the Bedrock spend ledger's totals
    --inspect-shape ID     print the key names and types of one real Pocket recording,
                           and nothing else: no values, no text
    --decider-check        send ONE canonical fixture task to the local Strands Decider
                           and print the four answers with their confidences
    --rotate-options N     the option-order test: ask the local Decider every canonical
                           fixture task with the project options rotated N ways, and
                           report the flip rate

Secrets come from .env through config.py. A configuration problem exits with
code 78 (EX_CONFIG), as does the spend hard stop.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx

from ratatoskr_demo.backoff import RetryBook
from ratatoskr_demo.bedrock import BedrockClient, NetworkLost, make_spend_ledger
from ratatoskr_demo.commands import TelegramReceiver, format_status, telegram_get_updates
from ratatoskr_demo.config import PLACEHOLDER_MARK, Settings, load_settings, require_secrets, runtime_environment
from ratatoskr_demo.cycle import Cycle, CycleDeps
from ratatoskr_demo.errors import ConfigurationError, TransientError
from ratatoskr_demo.extract import ModelCall, bedrock_model_call
from ratatoskr_demo.jobs import Job, Scheduler
from ratatoskr_demo.ledger import Ledger
from ratatoskr_demo.notify import Notifier
from ratatoskr_demo.pocket import PocketClient
from ratatoskr_demo.route import Router, load_route_questions
from ratatoskr_demo.runlock import hold_loop_lock, refuse_while_loop_runs
from ratatoskr_demo.source_gate import SourceGate
from ratatoskr_demo.devices.envelope import Envelope, Text
from ratatoskr_demo.devices.registry import build_inputs
from ratatoskr_demo.state import State, StateLogHandler
from ratatoskr_demo.todoist import TodoistClient

log = logging.getLogger("ratatoskr_demo")

EX_CONFIG = 78
UNGATED_WARNING = ("backend=none: no sensitivity gate; nothing is held for sensitivity "  # rulings 37, 38
                   "(the stop-list tripwire still runs)")


@dataclass
class Runtime:
    settings: Settings
    state: State
    cycle: Cycle
    http: httpx.Client
    notifier: Notifier | None
    router: Router | None = None
    bedrock: BedrockClient | None = None


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.dry_run and not args.once:
        parser.error("--dry-run goes with --once")
    if (args.save_tasks or args.demo_text) and not args.dry_run:
        parser.error("--save-tasks and --demo-text go with --once --dry-run")
    configure_logging()
    try:
        settings = load_settings()
        if args.option_descriptions is not None:  # ruling 15: for measuring only
            settings = replace(settings, route=replace(settings.route,
                                                       option_descriptions=args.option_descriptions == "on"))
        questions = load_route_questions(settings)  # route.toml is validated at startup
        if args.scrub_list or args.mark_scrubbed:
            return _scrub(settings, args.mark_scrubbed)
        if args.status:
            print(format_status(State(Ledger(settings.ledger_path))))
            return 0
        if args.spend:
            print(json.dumps(make_spend_ledger(settings).summary(), indent=2))
            return 0
        if args.inspect_shape:
            return inspect_shape(settings, args.inspect_shape)
        if args.decider_check:
            refuse_while_loop_runs(settings.paths.state_dir, "--decider-check")
            return decider_check(settings, questions)
        if args.rotate_options:
            refuse_while_loop_runs(settings.paths.state_dir, "--rotate-options")
            return rotate_options(settings, questions, args.rotate_options)
        if args.loop:
            require_secrets(settings)
            runtime = build_runtime(settings, dry_run=False)
            check_startup(runtime)
            with hold_loop_lock(settings.paths.state_dir):  # the bench and the checks wait for it
                return run_loop(runtime)
        if not args.dry_run:
            require_secrets(settings)
        runtime = build_runtime(settings, dry_run=args.dry_run)
        saved = save_tasks(runtime) if args.save_tasks else None
        check_startup(runtime)
        print(runtime.cycle.run())
        if args.demo_text:  # the /demo sentence through the same dry run (ruling 15's real-task set)
            envelope = Envelope(device_id="cli", kind="text", ts=datetime.now(timezone.utc),
                                payload=Text(text=args.demo_text))
            outcome = runtime.cycle.process_text("demo-cli", envelope)
            print(f"demo text: {outcome.status}{f' ({outcome.reason})' if outcome.reason else ''}")
        if saved is not None:
            print(f"saved the guarded extractions to {saved}")
        return EX_CONFIG if runtime.cycle.stopped else 0
    except ConfigurationError as exc:
        print(f"ratatoskr-demo: configuration error: {exc}", file=sys.stderr)
        log.error("configuration error: %s", exc)
        _notify_config_error(exc)
        return EX_CONFIG


REAL_TASKS_DIR = Path(__file__).resolve().parents[1] / "bench" / "raw"  # git-ignored


def save_tasks(runtime: Runtime) -> Path:
    """--save-tasks: each guarded extraction of this dry run, one JSON line per recording, for the bench's
    --real-tasks (ruling 15). Model output that passed the guard, never the transcript; git-ignored."""
    REAL_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    path = REAL_TASKS_DIR / f"real-tasks-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SUTC')}.jsonl"

    def write(rid: str, recording_at: str, extraction: Any) -> None:
        c = extraction.contact
        row = {"recording": rid, "recording_at": recording_at,
               "contact": {"name": c.name, "organisation": c.organisation, "role": c.role, "context": c.context},
               "tasks": [{"content": t.content, "due_string": t.due_string, "notes": t.notes}
                         for t in extraction.tasks]}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    runtime.cycle.deps.on_extraction = write
    runtime.cycle.revisit_done = True  # the live run's recordings are done in the ledger: read them again
    return path


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ratatoskr-demo", description="Pocket -> Todoist courier with gatekeeper triage")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run one poll cycle and exit")
    mode.add_argument("--loop", action="store_true", help="poll and answer Telegram until stopped")
    mode.add_argument("--scrub-list", action="store_true", help="list recordings you can delete in Pocket")
    mode.add_argument("--mark-scrubbed", nargs="+", metavar="ID", help="mark recordings as deleted in Pocket")
    mode.add_argument("--status", action="store_true", help="print a status summary")
    mode.add_argument("--spend", action="store_true", help="print the Bedrock spend ledger totals")
    mode.add_argument("--inspect-shape", metavar="RECORDING_ID", help="print key names and types of one recording")
    mode.add_argument("--decider-check", action="store_true", help="one canonical task to the local Decider")
    mode.add_argument("--rotate-options", type=int, metavar="N", help="the option-order test, N rotations")
    p.add_argument("--dry-run", action="store_true", help="with --once: show what would happen, write nothing")
    p.add_argument("--save-tasks", action="store_true",
                   help="with --once --dry-run: save each guarded extraction to bench/raw/ for the bench's --real-tasks")
    p.add_argument("--demo-text", metavar="TEXT", help="with --once --dry-run: also take TEXT through, as /demo would")
    p.add_argument("--option-descriptions", choices=("on", "off"), default=None,
                   help="the project options with or without their descriptions (for measuring only)")
    return p


def configure_logging() -> None:
    for stream in (sys.stdout, sys.stderr):  # the Windows console defaults to cp1252: "·" and "—" need UTF-8
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stderr)
    # HTTP libraries log full request URLs at INFO, and Telegram's URL contains
    # the bot token. Keep them at WARNING so no secret reaches a log.
    for noisy in ("httpx", "httpcore", "httpx2", "httpcore2", "openai", "typesafe_sdk", "strands"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_runtime(
    settings: Settings,
    *,
    dry_run: bool,
    model_call: ModelCall | None = None,
    http: httpx.Client | None = None,
    clock: Callable[[], float] = time.time,
    router: Router | None = None,
    bedrock: BedrockClient | None = None,
    source_gate: SourceGate | None = None,
) -> Runtime:
    """Assemble the real objects, for the inputs and outputs [devices] enables.
    Tests pass their own model_call, http, router and source gate."""
    http = http or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
    if not dry_run:
        settings.paths.state_dir.mkdir(parents=True, exist_ok=True)
    state = State(Ledger(settings.ledger_path), clock=clock)
    app_log = logging.getLogger("ratatoskr_demo")
    for old in [h for h in app_log.handlers if isinstance(h, StateLogHandler)]:
        app_log.removeHandler(old)  # one State feeds the recent log, never a stale one
    app_log.addHandler(StateLogHandler(state))

    secrets = settings.secrets
    if model_call is None and bedrock is None and secrets.bedrock_api_key and PLACEHOLDER_MARK not in secrets.bedrock_api_key:
        bedrock = BedrockClient(settings, make_spend_ledger(settings))
    if model_call is None and bedrock is not None:
        model_call = bedrock_model_call(bedrock)
    outputs = settings.devices.outputs
    notifier = None if dry_run or "telegram" not in outputs else Notifier(
        secrets.telegram_bot_token, secrets.telegram_chat_id, http=http)
    decider_health = None  # only the decider backend has a Decider to wait for (ruling 9)
    if settings.route.backend == "decider":
        from ratatoskr_demo.decider import make_health_check, make_router
        from ratatoskr_demo.source_gate import make_source_gate

        questions = load_route_questions(settings)
        router = router or make_router(settings, questions)
        source_gate = source_gate or make_source_gate(settings, questions)
        decider_health = make_health_check(settings)
    if router is None and settings.route.backend == "jev":
        from ratatoskr_demo.jev import JevRouter, make_client  # only when Jev is actually selected

        router = JevRouter(make_client(settings), load_route_questions(settings))
    deps = CycleDeps(
        inputs=build_inputs(settings, http=http, clock=clock),
        todoist=None if dry_run or "todoist" not in outputs else TodoistClient(secrets.todoist_api_token, http=http),
        notifier=notifier,
        model_call=model_call,
        retry=RetryBook(
            None if dry_run else settings.paths.state_dir / "retry.json",
            base_seconds=settings.poll.interval_seconds,
            cap_seconds=settings.poll.backoff_max_seconds,
        ),
        router=router,
        source_gate=source_gate,
        decider_health=decider_health,
        clock=clock,
    )
    cycle = Cycle(settings, state, deps, dry_run=dry_run)
    return Runtime(settings=settings, state=state, cycle=cycle, http=http, notifier=notifier, router=router,
                   bedrock=bedrock)


def check_startup(runtime: Runtime) -> None:
    """At startup: a warning when there is no gate, the model record against its baseline, and the router.

    A changed model record or a rejected key stops (exit 78). No network yet
    is not an error: the record is then checked before the first Bedrock call.
    """
    if runtime.settings.route.backend == "none":
        log.warning(UNGATED_WARNING)
    if runtime.bedrock is not None:
        try:
            runtime.bedrock.check_model_record()
        except (NetworkLost, TransientError) as exc:
            log.warning("bedrock: could not check the model record at startup (%s); will check before the first call",
                        exc)
    check = getattr(runtime.router, "check_access", None)
    if check is not None:
        check()


def start_receiver(runtime: Runtime, stop: threading.Event, *, get_updates=None) -> TelegramReceiver:
    """Start the one non-main thread: the Telegram receiver (/status and /demo)."""
    s = runtime.settings
    receiver_http = httpx.Client(timeout=httpx.Timeout(45.0, connect=10.0))  # its own client for its own thread
    receiver = TelegramReceiver(
        get_updates or telegram_get_updates(s.secrets.telegram_bot_token or "", receiver_http),
        Notifier(s.secrets.telegram_bot_token, s.secrets.telegram_chat_id, http=receiver_http),
        s.secrets.telegram_chat_id or 0,
        runtime.state,
        stop,
        process_text=runtime.cycle.process_text,
        backend=s.route.backend,
    )
    receiver.start()
    return receiver


def run_loop(runtime: Runtime, *, stop: threading.Event | None = None, get_updates=None) -> int:
    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
    if "telegram" in runtime.settings.devices.outputs:
        start_receiver(runtime, stop, get_updates=get_updates)
    log.info("ratatoskr-demo started: inputs %s, outputs %s; polling every %ds",
             ", ".join(runtime.settings.devices.inputs), ", ".join(runtime.settings.devices.outputs) or "none",
             runtime.settings.poll.interval_seconds)
    scheduler = Scheduler([Job("pocket", runtime.settings.poll.interval_seconds, runtime.cycle.run_job)], runtime.state)
    try:
        while not stop.is_set():
            scheduler.tick()
            if runtime.cycle.stopped:
                log.error("stopping: %s", runtime.cycle.stopped)
                stop.set()
                return EX_CONFIG
            stop.wait(1.0)
    finally:
        stop.set()
        log.info("ratatoskr-demo stopped")
    return 0


def inspect_shape(settings: Settings, recording_id: str) -> int:
    """Key names and types of one real recording. No values, no text, nothing else."""
    from ratatoskr_demo.shape import describe

    if not settings.secrets.pocket_api_key:
        raise ConfigurationError("missing secret(s) in .env: POCKET_API_KEY")
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as http:
        recording = PocketClient(settings.secrets.pocket_api_key, http=http).get_recording(recording_id)
    print("\n".join(describe(recording, "data")))
    return 0


def decider_check(settings: Settings, questions, out: Callable[[str], None] = print, router=None) -> int:
    """One canonical fixture task (rec_001's first) to the local Decider; the four answers printed."""
    from ratatoskr_demo.decider import DeciderRejected, DeciderUnavailable, make_router
    from ratatoskr_demo.expected import canonical_tasks, load_expected
    from ratatoskr_demo.judge import interpret, trace_line

    rid, expected, contact = canonical_tasks(load_expected())[0]
    router = router or make_router(settings, questions)
    try:
        router.check_access()
        response = router.ask(expected.task, contact)
    except (DeciderUnavailable, DeciderRejected) as exc:
        out(f"decider-check: Strands Decider not usable at {settings.route.decider_url}: {exc}")
        return 1
    judged = interpret(response["answers"], questions)
    usage = response.get("usage") or {}
    out(f"decider-check: 1 canonical fixture task ({rid}) -> {response.get('model')} "
        f"({usage.get('input_tokens')} input / {usage.get('output_tokens')} output tokens, "
        f"{response.get('latency_ms')} ms)")
    out(f"task:    {expected.task.content}")
    out(trace_line(judged))
    out(f"expected: project {expected.project}, priority {expected.priority}, "
        f"commitment {expected.real_commitment}, sensitive {expected.sensitive}")
    return 0


def rotate_options(settings: Settings, questions, n: int, out: Callable[[str], None] = print, router=None) -> int:
    """The option-order test against the local Decider, over the canonical fixture tasks."""
    from ratatoskr_demo.decider import DeciderRejected, DeciderUnavailable, make_router
    from ratatoskr_demo.expected import canonical_tasks, load_expected
    from ratatoskr_demo.judge import interpret
    from ratatoskr_demo.rotation import run_rotation

    tasks = canonical_tasks(load_expected())
    options = list(questions["project"].criteria)
    router = router or make_router(settings, questions)

    def ask(index: int, order: list[str]) -> str | None:
        _, expected, contact = tasks[index]
        try:
            response = router.ask(expected.task, contact, project_order=order)
        except (DeciderUnavailable, DeciderRejected):
            return None
        judged = interpret(response["answers"], questions)["project"]
        return None if judged.problem else judged.answer

    try:
        report = run_rotation(options, len(tasks), n, ask)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from None
    out(report.summary())
    for (rid, expected, _), picks in zip(tasks, report.choices):
        mark = "FLIP" if len(set(picks)) > 1 else "    "
        out(f"  {mark} {rid}  {expected.task.content[:48]:<48}  {' '.join(str(p) for p in picks)}")
    return 0


def _scrub(settings: Settings, mark: list[str] | None) -> int:
    ledger = Ledger(settings.ledger_path)
    if mark:
        unknown = ledger.mark_scrubbed(mark)
        for rid in unknown:
            print(f"unknown recording id: {rid}", file=sys.stderr)
        return 1 if unknown else 0
    items = ledger.scrub_list()
    if not items:
        print("Nothing to scrub.")
    for rid, entry in items:
        print(f"{entry.recording_at}  {rid}  {entry.title}")
    return 0


def _notify_config_error(exc: ConfigurationError) -> None:
    """Best effort: if Telegram itself is configured, say why we stopped."""
    try:
        env = runtime_environment()
        token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat or PLACEHOLDER_MARK in token + chat:
            return
        with httpx.Client(timeout=10.0) as http:
            Notifier(token, int(chat), http=http).send_message(f"Ratatoskr Demo stopped: configuration error: {exc}")
    except Exception:
        pass  # we are already exiting with the real error printed


if __name__ == "__main__":
    raise SystemExit(main())
