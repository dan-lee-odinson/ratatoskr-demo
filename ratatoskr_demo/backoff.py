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

"""Exponential backoff for transient failures (C7).

A recording that fails transiently is not marked `error`; it is simply tried
again later. Each consecutive failure doubles the wait (one poll interval, two,
four ...) up to a cap of one hour. If a recording has been failing for more
than 24 hours, Dan gets one Telegram notice about it — once, not every poll.

The book is saved to retry.json in the state dir so a reboot neither forgets
the 24-hour clock nor re-sends the notice. It holds ids and timestamps only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ratatoskr_demo.ledger import atomic_write_json

STUCK_AFTER_SECONDS = 24 * 3600


@dataclass
class RetryRecord:
    first_failed_at: float
    attempts: int
    next_attempt_at: float
    stuck_notified: bool = False


class RetryBook:
    def __init__(self, path: Path | None, *, base_seconds: float, cap_seconds: float) -> None:
        self.path = path
        self.base = base_seconds
        self.cap = cap_seconds
        self._records: dict[str, RetryRecord] = self._load()

    def _load(self) -> dict[str, RetryRecord]:
        if self.path is None or not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {key: RetryRecord(**value) for key, value in raw.items()}
        except (ValueError, TypeError):
            return {}  # losing backoff timing is harmless; the ledger is what matters

    def ready(self, key: str, now: float) -> bool:
        record = self._records.get(key)
        return record is None or now >= record.next_attempt_at

    def delay_for(self, attempts: int) -> float:
        return min(self.base * (2 ** max(attempts - 1, 0)), self.cap)

    def failed(self, key: str, now: float, *, min_delay: float = 0.0) -> RetryRecord:
        """Record a failure. `min_delay` lets a lost network wait about a minute at least."""
        record = self._records.get(key)
        if record is None:
            record = RetryRecord(first_failed_at=now, attempts=0, next_attempt_at=now)
        record.attempts += 1
        record.next_attempt_at = now + max(self.delay_for(record.attempts), min_delay)
        self._records[key] = record
        self._save()
        return record

    def needs_stuck_notice(self, key: str, now: float) -> bool:
        record = self._records.get(key)
        return (
            record is not None
            and not record.stuck_notified
            and now - record.first_failed_at > STUCK_AFTER_SECONDS
        )

    def mark_stuck_notified(self, key: str) -> None:
        if key in self._records:
            self._records[key].stuck_notified = True
            self._save()

    def succeeded(self, key: str) -> bool:
        """Forget a key after success. Returns True if it had been failing."""
        if self._records.pop(key, None) is None:
            return False
        self._save()
        return True

    def attempts(self, key: str) -> int:
        record = self._records.get(key)
        return 0 if record is None else record.attempts

    def _save(self) -> None:
        if self.path is not None:
            atomic_write_json(self.path, {k: asdict(v) for k, v in self._records.items()})
