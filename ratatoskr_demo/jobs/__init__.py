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

"""A tiny job registry and the scheduler loop.

A job is a name, an interval, and a small synchronous function that returns a
one-line result. The scheduler runs on the main thread: each tick it runs
every due job in turn. A job that raises is caught and logged, and never stops
the loop or the other jobs. Adding a future job (a shared-folder watcher, a
morning brief) means registering one more Job, not touching Pocket code.

No threads, no asyncio, no pools here. The one other thread, the Telegram
receiver, lives in commands.py.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

from ratatoskr_demo.state import State

log = logging.getLogger(__name__)


@dataclass
class Job:
    name: str
    interval_seconds: float
    run: Callable[[], str]
    next_run_at: float = field(default=0.0)


class Scheduler:
    def __init__(self, jobs: list[Job], state: State, *, clock: Callable[[], float] = time.time) -> None:
        self.jobs = jobs
        self.state = state
        self.clock = clock
        for job in jobs:
            state.register_job(job.name)

    def tick(self) -> None:
        """Run every job that is due, one after another."""
        for job in self.jobs:
            now = self.clock()
            if now < job.next_run_at:
                continue
            job.next_run_at = now + job.interval_seconds
            self.state.record_job(job.name, self._run_one(job))

    def _run_one(self, job: Job) -> str:
        try:
            return job.run() or "ok"
        except Exception as exc:  # one job's crash must not stop the others
            # Log the exception type and where it happened, not its message:
            # a message from deep inside a library could carry data (C1).
            where = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
            place = f"{where.filename.rsplit('/', 1)[-1]}:{where.lineno}" if where else "?"
            log.error("job %s failed: %s at %s", job.name, type(exc).__name__, place)
            return f"error: {type(exc).__name__}"

    def run_forever(self, stop: threading.Event, *, tick_seconds: float = 1.0) -> None:
        while not stop.is_set():
            self.tick()
            stop.wait(tick_seconds)
