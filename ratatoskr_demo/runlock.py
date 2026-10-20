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

"""One Strands Decider client at a time: the loop holds a lock; the bench and the checks look for it.

Strands Decider 0.1.0 (the PyPI release) can return one request's answers to
another when two requests reach the server at the same moment (its issue #17,
fixed after 0.1.0). Inside the courier a cycle lock already serializes polls
and /demo. Between processes, `ratatoskr-demo --loop` holds an exclusive OS
lock on ledger/loop.lock for as long as it runs, and the bench,
`--rotate-options` and `--decider-check` refuse to start while it is held.

The lock is the operating system's (msvcrt on Windows, flock elsewhere), so
it is released when the loop exits, even if it crashes. Nothing here signals
or inspects another process.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ratatoskr_demo.errors import ConfigurationError

LOCK_NAME = "loop.lock"


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def hold_loop_lock(state_dir: Path) -> Iterator[None]:
    """Held by --loop for its whole life. A second loop is a configuration error."""
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT)
    try:
        if not _try_lock(fd):
            raise ConfigurationError("another ratatoskr-demo --loop is already running")
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def loop_is_running(state_dir: Path) -> bool:
    path = state_dir / LOCK_NAME
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if _try_lock(fd):
            _unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


def refuse_while_loop_runs(state_dir: Path, what: str) -> None:
    if loop_is_running(state_dir):
        raise ConfigurationError(
            f"{what} talks to Strands Decider, and ratatoskr-demo --loop is running: stop the loop first "
            "(Decider 0.1.0 can mix up answers to requests that arrive at the same moment)")
