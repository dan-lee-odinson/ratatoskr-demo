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

"""The Telegram receiver thread: answers /status and /demo, and nothing else.

It long-polls Telegram's getUpdates (30 s). Only messages whose chat id equals
TELEGRAM_CHAT_ID are read at all; anything from another chat is dropped
before its text is looked at.

    /status        counts from the ledger and today's numbers
    /demo <text>   a Text envelope through the full cycle, now. The
                   reply is the wrist message for each task, a one-line trace of
                   the four Decider answers with confidences, and whether the task
                   was written, held, or marked [maybe]. The text is a transcript
                   for every purpose: never logged, never persisted (C1).

The thread never dies (an exception is logged by type and the loop backs off),
and it never writes the ledger itself: /demo goes through Cycle.process_text,
under the cycle lock.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

import httpx

from ratatoskr_demo.cycle import Outcome
from ratatoskr_demo.devices.envelope import Envelope
from ratatoskr_demo.devices.text_adapter import command_of, from_telegram
from ratatoskr_demo.devices.wrist_adapter import format_task
from ratatoskr_demo.errors import ConfigurationError, RatatoskrError, TransientError, raise_for_http, transport_error
from ratatoskr_demo.judge import trace_line
from ratatoskr_demo.notify import API_BASE, Notifier
from ratatoskr_demo.state import State
from ratatoskr_demo.todoist import MAYBE_PREFIX

log = logging.getLogger(__name__)

LONG_POLL_SECONDS = 30
MAX_BACKOFF = 300.0

GetUpdates = Callable[[int | None], list[dict[str, Any]]]
ProcessText = Callable[[str, Envelope], Outcome]


def telegram_get_updates(bot_token: str, http: httpx.Client, base_url: str = API_BASE) -> GetUpdates:
    url = f"{base_url.rstrip('/')}/bot{bot_token}/getUpdates"

    def get(offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": LONG_POLL_SECONDS, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            response = http.get(url, params=params, timeout=LONG_POLL_SECONDS + 10)
        except httpx.TransportError as exc:
            raise transport_error(exc, "telegram") from None
        raise_for_http(response, "telegram")
        body = response.json()
        if not isinstance(body, dict):
            return []
        return body.get("result") or []

    return get


def format_status(state: State) -> str:
    snap = state.snapshot()
    entries = state.ledger_entries()
    by_status = {s: sum(1 for e in entries.values() if e.status == s) for s in ("done", "hold", "keep", "error")}
    today = snap["today"]
    lines = [
        "Ratatoskr Demo status",
        f"Last poll: {snap['last_poll_at'] or 'not yet'}; next in {snap['next_poll_in_s']} s",
        f"Today: processed {today['processed']}, held {today['held']}, errors {today['errors']}, "
        f"not ready {today['not_ready']}; routed {today['routed']}, unrouted {today['unrouted']}",
        f"Ledger: {by_status['done']} done, {by_status['hold']} on hold, "
        f"{by_status['keep']} keep, {by_status['error']} error",
    ]
    for hold in snap["holds"]:
        lines.append(f"• held: {hold['id']}")  # ruling 13: the id only; a Pocket title can carry a protected name
    return "\n".join(lines)


def format_demo_reply(outcome: Outcome, rid: str, *, backend: str = "decider") -> str:
    """The /demo reply. Guarded task text, labels and numbers; never the text that was sent."""
    if outcome.status == "done":
        blocks = []
        for task in outcome.tasks:
            lines = [format_task(task.routed, task.content, sensitivity_unchecked=outcome.sensitivity_unchecked)]
            if task.routed.trace:
                lines.append(trace_line({j.question: j for j in task.routed.trace}, backend))
            marked = ", marked [maybe]" if task.content.startswith(MAYBE_PREFIX) else ""
            lines.append(f"written to {outcome.project_name}{marked}" if outcome.project_name
                         else f"not written: the todoist output is off{marked}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)
    if outcome.status == "hold":
        reason = outcome.reason or "hold"
        if reason.startswith("stoplist:"):  # ruling 13: the term itself is not repeated on Telegram
            return "held · tripwire\nA stop-list term was found. Nothing reached any model."
        lines = [f"held · {reason}", "Nothing was written."]
        lines.extend(trace_line({j.question: j for j in t.routed.trace}, backend)
                     for t in outcome.tasks if t.routed.trace)
        return "\n".join(lines)
    if outcome.status == "skip" and outcome.reason == "too-short":  # ruling 14
        return "skipped · under 8 words\nNothing was asked or written."
    if outcome.status == "skip":
        return f"already handled ({rid})"
    if outcome.status == "stopped":
        return f"stopped: {outcome.reason}"
    return f"not processed: {outcome.reason or outcome.status}"


class TelegramReceiver:
    def __init__(self, get_updates: GetUpdates, notifier: Notifier, chat_id: int, state: State,
                 stop: threading.Event, *, process_text: ProcessText | None = None, backend: str = "decider") -> None:
        self.get_updates, self.notifier, self.chat_id, self.state, self.stop = get_updates, notifier, chat_id, state, stop
        self.process_text = process_text
        self.backend = backend
        self.thread = threading.Thread(target=self.run, name="telegram-receiver", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        offset: int | None = None
        delay = 5.0
        while not self.stop.is_set():
            try:
                updates = self.get_updates(offset)
                delay = 5.0
            except Exception as exc:  # this thread must never die: /status would go silent
                reason = str(exc) if isinstance(exc, RatatoskrError) else type(exc).__name__
                log.warning("telegram receiver: %s; retrying in %ds", reason, delay)
                self.stop.wait(delay)
                delay = min(delay * 2, MAX_BACKOFF)
                continue
            for update in updates:
                try:
                    offset = int(update.get("update_id", 0)) + 1
                    self.handle(update)
                except Exception as exc:
                    log.warning("telegram receiver: skipped an update (%s)", type(exc).__name__)

    def handle(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        if chat != self.chat_id:
            return  # another chat: not read, not answered
        command = command_of(message.get("text") or "")
        if command == "/status":
            self._reply(format_status(self.state))
        elif command == "/demo" and self.process_text is not None:
            parsed = from_telegram(message)
            if parsed is None:
                self._reply("Usage: /demo <text to treat as a transcript>")
                return
            rid, envelope = parsed
            log.info("telegram receiver: /demo %s received", rid)
            self._reply(format_demo_reply(self.process_text(rid, envelope), rid, backend=self.backend))

    def _reply(self, text: str) -> None:
        try:
            self.notifier.send_message(text)
        except (TransientError, ConfigurationError) as exc:
            log.warning("telegram receiver: could not reply (%s)", exc)
