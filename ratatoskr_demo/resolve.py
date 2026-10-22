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

"""The owner files an unsorted task from Telegram (Dan's ruling 43, 21 Oct 2026).

A task whose project answer was below its floor is written with no `proj:`
label, and its wrist message reads `unsorted · <priority> · <p>`. That message
now carries one button per project, in the order of Decider's own probability
for that task (best first, so the model's ranking is used even where it
declined to decide), and a last button, `Leave unsorted`. A tap:

    a project   the existing Todoist task gets the `proj:<key>` label the sorted
                path would have given it (its other labels as written stay), the
                message's first line becomes `<project> · <priority> · <p> →
                filed by owner`, the buttons go
    Leave       nothing in Todoist; `unsorted · <priority> · <p> → left by owner`
    again       `already filed as <project>` / `left unsorted`: no Todoist call,
                no ledger write
    unknown     `unknown task`, nothing else
    other chat  answered with nothing, logged at WARNING
    Todoist     refused: `<project> · <priority> · <p> → not filed (Todoist
                error), tap again`, the buttons stay, the ledger is not written

The ledger records each resolution against the task (`project_by: owner`, the
key or `unsorted`, the time); tasks the model sorted carry no such record.
Neither Decider nor Bedrock is asked: the owner's answer is applied by code.
Callback data is `pick:<short id>:<key>`, at most 64 bytes (Telegram's
limit); the short id is derived from the recording id and the task's ordinal,
and the ledger keeps what it stands for. Nothing here stores or sends task
text: an edit rewrites the first line of the message Telegram hands back.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol

from ratatoskr_demo.errors import RatatoskrError
from ratatoskr_demo.photos import ReleaseTaps, SceneTaps, is_release_tap, is_scene_tap

log = logging.getLogger(__name__)

PREFIX = "pick"
LEAVE = "unsorted"  # the key of the last button, `Leave unsorted`
LEAVE_TEXT = "Leave unsorted"
UNSORTED_LINE = "unsorted"
CALLBACK_LIMIT = 64  # bytes, Telegram's limit on callback_data
ROW = 4  # buttons per row
SEP = " · "
# One pick applied at a time, from Telegram or from the app's owner service (ruling 61): the check that a task is not
# answered yet and the answer's Todoist update and ledger record happen under it, so two taps leave one change.
_PICK_LOCK = threading.Lock()


class Telegram(Protocol):
    def edit_message(self, message_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None: ...

    def answer_callback(self, callback_id: str, text: str | None = None) -> None: ...


class Todoist(Protocol):
    def update_labels(self, task_id: str, labels: list[str], *, command_uuid: str) -> None: ...


def short_id(recording_id: str, ordinal: int) -> str:
    """Ten hex characters standing for one task: the recording id and the task's place in it."""
    return hashlib.sha256(f"{recording_id}#{ordinal}".encode("utf-8")).hexdigest()[:10]


def callback_data(sid: str, key: str) -> str:
    return f"{PREFIX}:{sid}:{key}"


def parse(data: str) -> tuple[str, str] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def ordered(projects: Iterable[str], probabilities: Mapping[str, float] | None, top: str | None = None) -> list[str]:
    """The projects, Decider's most probable first; ties (and projects it gave no number) keep the config order.
    Without the probabilities, its top choice alone goes first."""
    keys = list(projects)
    p = dict(probabilities or {})
    if not p and top in keys:
        p = {top: 1.0}
    position = {key: i for i, key in enumerate(keys)}
    return sorted(keys, key=lambda key: (-p.get(key, 0.0), position[key]))


def keyboard(sid: str, projects: Iterable[str], probabilities: Mapping[str, float] | None = None,
             top: str | None = None) -> dict[str, Any]:
    """An inline keyboard: the projects in Decider's order, then `Leave unsorted`, four to a row."""
    buttons = []
    for key in ordered(projects, probabilities, top):
        data = callback_data(sid, key)
        if key == LEAVE or len(data.encode("utf-8")) > CALLBACK_LIMIT:
            log.warning("unsorted keyboard: project %r left off (its callback data would be invalid)", key)
            continue
        buttons.append({"text": key, "callback_data": data})
    buttons.append({"text": LEAVE_TEXT, "callback_data": callback_data(sid, LEAVE)})
    return {"inline_keyboard": [buttons[i:i + ROW] for i in range(0, len(buttons), ROW)]}


def pick_record(*, ordinal: int, task_id: str, labels: list[str], priority: str, p: str) -> dict[str, Any]:
    """What the ledger keeps for one unsorted task until the owner resolves it. No task text."""
    return {"ordinal": ordinal, "task_id": task_id, "labels": list(labels), "priority": priority, "p": p}


def first_line_parts(line: str) -> tuple[str, str]:
    """The priority and the probability shown on a wrist message's first line (`unsorted · high · 0.55`)."""
    parts = line.split(SEP)
    return (parts[1] if len(parts) > 1 else "-"), (parts[2] if len(parts) > 2 else "-")


def resolved_text(original: str | None, line: str) -> str:
    """The message as Telegram returned it, its first line replaced; nothing else in it changes."""
    rest = (original or "").split("\n")[1:]
    return "\n".join([line, *rest])


class Resolver:
    """Applies the owner's tap on an unsorted task's keyboard. Runs on the Telegram receiver's thread."""

    def __init__(self, state: Any, telegram: Telegram, todoist: Todoist | None, chat_id: int,
                 projects: Iterable[str], *, clock: Callable[[], float] | None = None, via: str | None = None) -> None:
        self.state, self.telegram, self.todoist, self.chat_id = state, telegram, todoist, chat_id
        self.projects = list(projects)
        self.clock = clock
        self.via = via  # recorded with the answer when given: "app" from the owner service (ruling 61)
        self.scenes = SceneTaps(state, telegram, chat_id, clock=clock)  # an unsure photo's buttons (ruling 50)
        self.releases = ReleaseTaps(state, telegram, chat_id, clock=clock)  # an unsure hold's Release (ruling 52)

    def handle(self, query: Mapping[str, Any]) -> None:
        if is_scene_tap(str(query.get("data") or "")):  # its own owner-only check and first-answer-stands rule
            self.scenes.handle(dict(query))
            return
        if is_release_tap(str(query.get("data") or "")):  # the same: owner only, the first tap stands
            self.releases.handle(dict(query))
            return
        callback_id = str(query.get("id", ""))
        message = query.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        if chat != self.chat_id:
            log.warning("telegram: a button tap from another chat was ignored")
            self._answer(callback_id)
            return
        with _PICK_LOCK:
            self._pick(query, callback_id, message)

    def _pick(self, query: Mapping[str, Any], callback_id: str, message: Mapping[str, Any]) -> None:
        parsed = parse(str(query.get("data") or ""))
        found = self.state.pick_find(parsed[0]) if parsed else None
        if parsed is None or found is None or (parsed[1] != LEAVE and parsed[1] not in self.projects):
            self._answer(callback_id, "unknown task")
            return
        sid, key = parsed
        rid, record = found
        if record.get("project_by"):  # resolved already: the first answer stands
            done = record.get("project")
            self._answer(callback_id, "left unsorted" if done == LEAVE else f"already filed as {done}")
            return
        priority, p = record.get("priority", "-"), record.get("p", "-")
        message_id, original = message.get("message_id"), message.get("text")
        if key == LEAVE:
            self._record(rid, sid, LEAVE)
            self._edit(message_id, resolved_text(original, SEP.join([UNSORTED_LINE, priority, p]) + " → left by owner"))
            self._answer(callback_id)
            return
        labels = [label for label in record.get("labels", []) if not label.startswith("proj:")] + [f"proj:{key}"]
        try:
            if self.todoist is None:
                raise RatatoskrError("the todoist output is off")
            self.todoist.update_labels(str(record["task_id"]), labels, command_uuid=_command_uuid(sid, key))
        except RatatoskrError as exc:
            log.warning("telegram: filing task %s as %s failed (%s); the ledger is unchanged", sid, key, exc)
            self._edit(message_id, resolved_text(original, SEP.join([key, priority, p])
                                                 + " → not filed (Todoist error), tap again"),
                       reply_markup=message.get("reply_markup"))
            self._answer(callback_id)
            return
        self._record(rid, sid, key)
        self._edit(message_id, resolved_text(original, SEP.join([key, priority, p]) + " → filed by owner"))
        self._answer(callback_id)

    def _record(self, rid: str, sid: str, key: str) -> None:
        now = self.clock() if self.clock else datetime.now(timezone.utc).timestamp()
        fields = {"project_by": "owner", "project": key,
                  "at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="seconds")}
        if self.via:
            fields["via"] = self.via
        self.state.pick_resolve(rid, sid, fields)

    def _edit(self, message_id: Any, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        if message_id is None:
            return
        try:
            self.telegram.edit_message(int(message_id), text, reply_markup=reply_markup)
        except RatatoskrError as exc:  # the resolution stands; only the message is stale
            log.warning("telegram: could not edit the message (%s)", exc)

    def _answer(self, callback_id: str, text: str | None = None) -> None:
        try:
            self.telegram.answer_callback(callback_id, text)
        except RatatoskrError as exc:
            log.warning("telegram: could not answer the button tap (%s)", exc)


def _command_uuid(sid: str, key: str) -> str:
    """The same task and the same choice always send the same command uuid, so Todoist applies it once."""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ratatoskr-demo:pick:{sid}:{key}"))
