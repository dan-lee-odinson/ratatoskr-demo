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

"""The `expected` blocks written into the fixtures: canonical tasks and the answers they should get.

Each fixture file (the body of GET /public/recordings/{id}) may carry, beside
`data`, an `expected` block. It is test and bench data only; the Pocket
client never reads it.

    "expected": {
      "triage": "process",                       or "hold:stoplist:vargrheim", "skip:keep", ...
      "source_sensitive": false,                 should the source-text gate hold the whole recording?
      "contact": {name, organisation, role, context},
      "tasks": [
        {"match": [["preprint"], ["paper"]],     keyword groups that identify this task in an extraction
         "content": "...", "due_string": ..., "notes": ...,   the canonical task
         "project": "research", "priority": "high",
         "someone_waiting": true, "hard_deadline": true,   the three priority Nouls, labelled
         "worth_researching_soon": null,                  (null: not scored; the rule never
                                                           reaches soon when priority is high)
         "real_commitment": true, "sensitive": false}
      ]
    }

The canonical tasks let the option-order test and the Decider check run
without any Bedrock call. The bench matches each extracted task to the one
canonical task with a `match` group whose words all appear in its content
and notes (a phrase counts as one word). An extracted task that matches no
canonical task, or more than one, is reported as unmatched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from ratatoskr_demo.due import recording_date
from ratatoskr_demo.extract import Contact, Task
from ratatoskr_demo.guard import normalise

REPO = Path(__file__).resolve().parents[1]
FIXTURE_DIRS = (REPO / "tests" / "fixtures", REPO / "bench" / "fixtures")


@dataclass(frozen=True)
class ExpectedTask:
    match: tuple[tuple[str, ...], ...]  # any one group, all of its words
    task: Task
    project: str
    priority: str
    real_commitment: bool
    sensitive: bool
    someone_waiting: bool | None = None  # None: not labelled, not scored
    hard_deadline: bool | None = None
    worth_researching_soon: bool | None = None


@dataclass(frozen=True)
class ExpectedRecording:
    recording_id: str
    triage: str
    source_sensitive: bool | None
    contact: Contact | None
    tasks: tuple[ExpectedTask, ...]
    recorded_on: date | None = None  # the recording's own date: "today" for the priority rule


def load_expected(dirs: Iterable[Path] = FIXTURE_DIRS) -> list[ExpectedRecording]:
    out = []
    for directory in dirs:
        for path in sorted(Path(directory).glob("rec_*.json")):
            body = json.loads(path.read_text(encoding="utf-8"))
            block = body.get("expected")
            if not isinstance(block, dict):
                continue
            out.append(_recording(str(body["data"]["id"]), block, recording_date(body["data"].get("recording_at"))))
    return out


def canonical_tasks(recordings: Iterable[ExpectedRecording]) -> list[tuple[str, ExpectedTask, Contact]]:
    """(recording id, expected task, contact) for every canonical task."""
    return [(r.recording_id, t, r.contact) for r in recordings if r.contact is not None for t in r.tasks]


def match_task(task: Task, expected: Iterable[ExpectedTask]) -> ExpectedTask | None:
    """The one expected task with a match group whose words all appear in this task's content and notes."""
    text = normalise(" ".join(s for s in (task.content, task.notes) if s))
    words = set(text.split())
    hits = [e for e in expected if any(all(_has(word, text, words) for word in group) for group in e.match)]
    return hits[0] if len(hits) == 1 else None


def _has(word: str, text: str, words: set[str]) -> bool:
    needle = normalise(word)
    return needle in words if " " not in needle else needle in text


def _recording(rid: str, block: dict[str, Any], recorded_on: date | None = None) -> ExpectedRecording:
    contact = block.get("contact")
    return ExpectedRecording(
        recording_id=rid,
        triage=str(block.get("triage", "process")),
        source_sensitive=block.get("source_sensitive"),
        contact=Contact(**contact) if isinstance(contact, dict) else None,
        tasks=tuple(
            ExpectedTask(match=tuple(tuple(group) for group in t["match"]), task=Task(content=t["content"], due_string=t.get("due_string"),
                                                             notes=t.get("notes")),
                         project=t["project"], priority=t["priority"], real_commitment=bool(t["real_commitment"]),
                         sensitive=bool(t["sensitive"]), someone_waiting=t.get("someone_waiting"),
                         hard_deadline=t.get("hard_deadline"), worth_researching_soon=t.get("worth_researching_soon"))
            for t in block.get("tasks", [])
        ),
        recorded_on=recorded_on,
    )
