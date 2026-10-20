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

"""processed.json: the state of record for which recordings are finished.

One JSON object keyed by recording id. It holds ids, titles, timestamps,
statuses and Todoist task ids — never transcript text (C1). Every write goes
to a temporary file in the same directory, is flushed to disk, and then
atomically renamed over the old file, so a power cut mid-write leaves either
the old ledger or the new one, never half of each (P6).

Statuses:
  done   tasks written; never processed again
  hold   waiting for Dan's `ok` tag; re-examined each poll only for that tag
  keep   tagged `keep`; never processed, never on the scrub list
  error  terminal data-contract failure; never retried
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ratatoskr_demo.errors import ConfigurationError

STATUSES = ("done", "hold", "keep", "error")


@dataclass
class LedgerEntry:
    status: str
    title: str
    recording_at: str
    decided_at: str
    reason: str | None = None
    todoist_task_ids: list[str] = field(default_factory=list)
    released_by_ok: bool = False
    scrubbable: bool = False
    sensitivity_not_checked: bool = False  # written while the Jev sensitivity check could not run

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown ledger status {self.status!r}")


class Ledger:
    """Read once at startup, then kept in memory and written through on change."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, LedgerEntry] = self._load()

    def _load(self) -> dict[str, LedgerEntry]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("top level is not an object")
            return {rid: LedgerEntry(**entry) for rid, entry in raw.items()}
        except (ValueError, TypeError) as exc:
            # Refuse to run rather than start from empty and reprocess everything.
            raise ConfigurationError(
                f"ledger {self.path} is unreadable ({type(exc).__name__}); "
                "refusing to start so nothing is processed twice"
            ) from None

    def get(self, recording_id: str) -> LedgerEntry | None:
        return self._entries.get(recording_id)

    def put(self, recording_id: str, entry: LedgerEntry) -> None:
        self._entries[recording_id] = entry
        self._save()

    def entries(self) -> dict[str, LedgerEntry]:
        return dict(self._entries)

    def holds(self) -> list[tuple[str, LedgerEntry]]:
        items = [(rid, e) for rid, e in self._entries.items() if e.status == "hold"]
        return sorted(items, key=lambda item: item[1].recording_at)

    def scrub_list(self) -> list[tuple[str, LedgerEntry]]:
        """Done recordings Dan can delete in the Pocket app, oldest first."""
        items = [(rid, e) for rid, e in self._entries.items() if e.scrubbable]
        return sorted(items, key=lambda item: item[1].recording_at)

    def mark_scrubbed(self, recording_ids: list[str]) -> list[str]:
        """Flip scrubbable off for ids Dan has deleted. Returns unknown ids."""
        unknown = [rid for rid in recording_ids if rid not in self._entries]
        for rid in recording_ids:
            if rid in self._entries:
                self._entries[rid].scrubbable = False
        self._save()
        return unknown

    def _save(self) -> None:
        data = {rid: asdict(entry) for rid, entry in sorted(self._entries.items())}
        atomic_write_json(self.path, data)


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON so a crash leaves the old file or the new one, never a mix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Make the rename itself durable (ext4 journals it; this forces it now)."""
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
