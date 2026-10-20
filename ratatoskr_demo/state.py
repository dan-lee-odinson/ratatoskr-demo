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

"""The one shared-state object, guarded by one lock.

Two threads touch shared state: the scheduler (runs the poll job, writes the
ledger) and the Telegram receiver (answers /status, runs /demo through the
cycle). They never touch each other's data directly: every read and write
goes through a method here, and every method holds `self._lock`.

Nothing in here ever holds transcript text (C1): only ids, titles, counts,
times, one-line job results and the last few log lines.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ratatoskr_demo.ledger import Ledger, LedgerEntry

RECENT_LOG_LINES = 5


@dataclass
class DayCounts:
    day: str
    processed: int = 0
    held: int = 0
    errors: int = 0
    not_ready: int = 0
    routed: int = 0
    unrouted: int = 0


@dataclass
class JobResult:
    last_run_at: float | None = None
    last_result: str = "not run yet"


class State:
    def __init__(self, ledger: Ledger, *, clock: Callable[[], float] = time.time) -> None:
        self._lock = threading.Lock()
        self._ledger = ledger
        self._clock = clock
        self._counts = DayCounts(day=self._today())
        self._jobs: dict[str, JobResult] = {}
        self._last_poll_at: float | None = None
        self._next_poll_at: float | None = None
        self._recent_log: deque[str] = deque(maxlen=RECENT_LOG_LINES)
        self._pending_notices: list[str] = []
        self._notices_sent: set[str] = set()
        self._not_ready_seen: set[str] = set()

    # --- ledger ---

    def ledger_get(self, recording_id: str) -> LedgerEntry | None:
        with self._lock:
            return self._ledger.get(recording_id)

    def ledger_put(self, recording_id: str, entry: LedgerEntry) -> None:
        with self._lock:
            self._ledger.put(recording_id, entry)

    def ledger_entries(self) -> dict[str, LedgerEntry]:
        with self._lock:
            return self._ledger.entries()

    def ledger_holds(self) -> list[tuple[str, LedgerEntry]]:
        with self._lock:
            return self._ledger.holds()

    # --- today's counts ---

    def count(self, name: str, amount: int = 1, *, recording_id: str | None = None) -> None:
        """Add to today's counter. not_ready counts each recording once a day."""
        with self._lock:
            self._roll_day()
            if name == "not_ready" and recording_id is not None:
                if recording_id in self._not_ready_seen:
                    return
                self._not_ready_seen.add(recording_id)
            setattr(self._counts, name, getattr(self._counts, name) + amount)

    def counts(self) -> DayCounts:
        with self._lock:
            self._roll_day()
            return DayCounts(**vars(self._counts))

    def _roll_day(self) -> None:
        today = self._today()
        if self._counts.day != today:
            self._counts = DayCounts(day=today)
            self._not_ready_seen.clear()

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock()).date().isoformat()

    # --- polls and jobs ---

    def record_poll(self, *, next_in_seconds: float) -> None:
        with self._lock:
            now = self._clock()
            self._last_poll_at = now
            self._next_poll_at = now + next_in_seconds

    def record_job(self, name: str, result: str) -> None:
        with self._lock:
            self._jobs[name] = JobResult(last_run_at=self._clock(), last_result=result.splitlines()[0][:200] if result else "")

    def register_job(self, name: str) -> None:
        with self._lock:
            self._jobs.setdefault(name, JobResult())

    # --- log lines and notices ---

    def add_log_line(self, line: str) -> None:
        with self._lock:
            self._recent_log.append(line)

    def queue_notice(self, text: str) -> None:
        """A notice that could not be sent now; the next cycle retries it."""
        with self._lock:
            self._pending_notices.append(text)

    def take_pending_notices(self) -> list[str]:
        with self._lock:
            notices, self._pending_notices = self._pending_notices, []
            return notices

    def first_time(self, key: str) -> bool:
        """True the first time a one-off notice key is seen (no repeats)."""
        with self._lock:
            if key in self._notices_sent:
                return False
            self._notices_sent.add(key)
            return True

    def forget(self, key: str) -> None:
        with self._lock:
            self._notices_sent.discard(key)

    # --- the /status summary ---

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._roll_day()
            now = self._clock()
            return {
                "service": "ratatoskr-demo",
                "last_poll_at": _iso(self._last_poll_at),
                "next_poll_in_s": None if self._next_poll_at is None else max(0, round(self._next_poll_at - now)),
                "today": {
                    "processed": self._counts.processed,
                    "held": self._counts.held,
                    "errors": self._counts.errors,
                    "not_ready": self._counts.not_ready,
                    "routed": self._counts.routed,
                    "unrouted": self._counts.unrouted,
                },
                "holds": [
                    {"id": rid, "title": entry.title, "recording_at": entry.recording_at}
                    for rid, entry in self._ledger.holds()
                ],
                "jobs": [
                    {"name": name, "last_run_at": _iso(job.last_run_at), "last_result": job.last_result}
                    for name, job in sorted(self._jobs.items())
                ],
                "recent_log": list(self._recent_log),
            }


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


class StateLogHandler(logging.Handler):
    """Copies the last few log lines into State for the /status summary."""

    def __init__(self, state: State) -> None:
        super().__init__(level=logging.INFO)
        self._state = state
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._state.add_log_line(self.format(record))
        except Exception:  # a logging failure must never take the service down
            self.handleError(record)
