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

"""Write tasks into the one Todoist inbox project.

Todoist docs read on 2026-09-20 at https://developer.todoist.com/api/v1/.
Endpoint used: the unified v1 Sync endpoint only, POST /api/v1/sync
(form-encoded; `sync_token`, `resource_types`, `commands` as JSON arrays):

  - read:        sync_token="*", resource_types=["projects"] to find the inbox
                 project by name. We read the project list, never tasks, and
                 never touch any other project (C5).
  - project_add: create the inbox project if missing.
  - item_add:    one command per task, args content, description, project_id,
                 labels, priority, due.

Why /sync and not POST /api/v1/tasks: C8 needs a remote idempotency key, and
the docs promise one only for Sync commands: "Todoist will not execute a
command that has same UUID as a previously executed command." REST requests
may carry an `X-Request-Id` header (a docs curl example shows one, and the
official SDK todoist-api-python 4.0.0 sends one), but the docs never say
Todoist de-duplicates on it, so it cannot carry C8. That is also why this
module uses httpx rather than the official SDK: the SDK wraps only the REST
endpoints. So every Sync command's `uuid` is deterministic:

    uuid5(RATATOSKR_NAMESPACE, f"{recording_id}:{task_ordinal}")

and a retry after a partial failure re-sends the same uuids; tasks Todoist
already created are not created again. The ledger stays the state of record;
the command uuid is the second line of defence. No pre-create search is
needed, because the endpoint has a key.

Two documented facts worth recording here: item_add takes a `due` OBJECT
({"string": "Friday"}), not `due_string`; and priority 4 is the most urgent ("very urgent is the priority 1 on
clients. So, p1 will return 4 in the API").

UNVERIFIED: how long Todoist remembers executed command uuids, and whether a
re-sent (skipped) command appears in temp_id_mapping. If it does not, the
task id for that ordinal is recorded as missing; the task itself exists once.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from ratatoskr_demo.errors import TerminalError, TransientError, raise_for_http, transport_error
from ratatoskr_demo.extract import Contact
from ratatoskr_demo.route import UNGATED, URGENT_SOURCE, RoutedTask

SYNC_URL = "https://api.todoist.com/api/v1/sync"

# Fixed forever: changing it would change every command uuid and defeat C8.
RATATOSKR_NAMESPACE = uuid.UUID("6f1c2b0e-5a7d-4e1b-9c3f-2d8a4b6e7f10")

MAYBE_PREFIX = "[maybe] "
PROJECT_LABEL_PREFIX = "proj:"


def task_command_uuid(recording_id: str, ordinal: int) -> str:
    return str(uuid.uuid5(RATATOSKR_NAMESPACE, f"{recording_id}:{ordinal}"))


def task_temp_id(recording_id: str, ordinal: int) -> str:
    return str(uuid.uuid5(RATATOSKR_NAMESPACE, f"temp:{recording_id}:{ordinal}"))


UNROUTED_LABEL = "unrouted"


def build_task_args(
    task: RoutedTask,
    *,
    recording_id: str,
    ordinal: int,
    contact: Contact,
    project_id: str,
    label: str,
    ungated: bool = False,
) -> dict[str, Any]:
    """The item_add args for one task. Pure, so it is easy to test.

    This is where routing is wired: the annotations become a second label,
    a priority, or the "[maybe] " prefix. With every annotation None (v1),
    the task is written exactly as it would be without routing.
    """
    content = task.content
    if task.real_commitment is False:
        content = MAYBE_PREFIX + content  # the one permitted text change, made here, not in route()

    labels = [label]
    if task.project is not None:
        labels.append(PROJECT_LABEL_PREFIX + task.project)
    if task.priority_source == URGENT_SOURCE:
        labels.append(UNROUTED_LABEL)  # ruling 12: written under the urgent tag while Decider was unreachable
    if ungated:
        labels.append(UNGATED)  # ruling 37: backend "none", no sensitivity check ran

    args: dict[str, Any] = {
        "content": content,
        "description": build_description(task.notes, contact, recording_id, ordinal),
        "project_id": project_id,
        "labels": labels,
    }
    if task.priority is not None:
        args["priority"] = task.priority
    if task.due_string:
        args["due"] = {"string": task.due_string}
    return args


def build_description(notes: str | None, contact: Contact, recording_id: str, ordinal: int) -> str:
    """notes, then "From: <name>, <org> — <context>", then the Pocket id line."""
    who = ", ".join(part for part in (contact.name or "unnamed contact", contact.organisation) if part)
    footer = f"From: {who} — {contact.context}\nPocket id: {recording_id} #{ordinal}"
    return f"{notes}\n\n{footer}" if notes else footer


class TodoistClient:
    def __init__(self, token: str | None, *, http: httpx.Client, url: str = SYNC_URL) -> None:
        self._http = http
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._project_id: str | None = None

    def ensure_project(self, name: str) -> str:
        """Return the inbox project's id, creating the project if it is missing."""
        if self._project_id is not None:
            return self._project_id
        body = self._post({"sync_token": "*", "resource_types": json.dumps(["projects"])})
        for project in body.get("projects") or []:
            if (
                isinstance(project, dict)
                and project.get("name") == name
                and not project.get("is_deleted")
                and not project.get("is_archived")
            ):
                self._project_id = str(project["id"])
                return self._project_id
        command_uuid = str(uuid.uuid5(RATATOSKR_NAMESPACE, f"project:{name}"))
        temp_id = str(uuid.uuid5(RATATOSKR_NAMESPACE, f"project-temp:{name}"))
        body = self._command({"type": "project_add", "temp_id": temp_id, "uuid": command_uuid, "args": {"name": name}})
        project_id = (body.get("temp_id_mapping") or {}).get(temp_id)
        if not project_id:
            raise TerminalError("todoist: project_add returned no project id", keys=("temp_id_mapping",))
        self._project_id = str(project_id)
        return self._project_id

    def create_task(self, args: dict[str, Any], *, recording_id: str, ordinal: int) -> str | None:
        """Send one item_add with its deterministic uuid. Returns the task id if known."""
        temp_id = task_temp_id(recording_id, ordinal)
        body = self._command(
            {"type": "item_add", "temp_id": temp_id, "uuid": task_command_uuid(recording_id, ordinal), "args": args}
        )
        task_id = (body.get("temp_id_mapping") or {}).get(temp_id)
        return str(task_id) if task_id else None

    def _command(self, command: dict[str, Any]) -> dict[str, Any]:
        body = self._post({"commands": json.dumps([command])})
        status = (body.get("sync_status") or {}).get(command["uuid"])
        if status == "ok":
            return body
        if isinstance(status, dict):
            http_code = status.get("http_code")
            if http_code == 429 or (isinstance(http_code, int) and http_code >= 500):
                raise TransientError(f"todoist: command failed with {http_code}", kind="server-error")
            raise TerminalError(
                f"todoist: {command['type']} rejected ({status.get('error_tag', 'unknown')})",
                keys=tuple(sorted(command["args"])),
            )
        raise TerminalError(f"todoist: no sync_status for {command['type']}", keys=tuple(sorted(body)))

    def _post(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._http.post(self._url, data=form, headers=self._headers)
        except httpx.TransportError as exc:
            raise transport_error(exc, "todoist") from None
        raise_for_http(response, "todoist")
        try:
            body = response.json()
        except ValueError:
            raise TerminalError("todoist returned a non-JSON body") from None
        if not isinstance(body, dict):
            raise TerminalError("todoist returned a non-object body")
        return body
