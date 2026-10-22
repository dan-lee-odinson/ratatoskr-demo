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

"""The owner service: the companion app's one real connection, inside the courier (Dan's ruling 61, P1).

    ratatoskr-demo --loop --owner-service

A small HTTP server on the loopback address only (127.0.0.1, on `[owner] port`; an Android emulator reaches it at
10.0.2.2), its routes behind a bearer token from the secrets file (OWNER_SERVICE_TOKEN):

    GET  /owner/pending   the live equivalent of the app's fixtures, read from the ledger: each unsorted task, with
                          the owner's answer once given; its text and Decider's per-project probabilities only where
                          this process sent them to Telegram (held in memory, as the message was; never written to
                          the ledger or anywhere else); every hold ID only (its id, input type, gate and verdict)
    POST /owner/pick      {"sid": ..., "project": ...}: ruling 43's resolver, with its owner-only and
                          first-answer-stands rules and its Todoist command uuid, recorded `project_by: owner,
                          via: app`. 200 once the courier has updated the Todoist task; 409 when the task was already
                          answered (from Telegram or the app), 404 for an unknown task, 502 when Todoist refused

and, with no token, the app's own files from app/, so the page and the service share one origin. Release and scene
answers do not go through it: in the app they remain prototype state. No model is asked, nothing is sent to Telegram.
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from ratatoskr_demo import resolve

log = logging.getLogger(__name__)

HOST = "127.0.0.1"  # never another address: the emulator's 10.0.2.2 is this machine's loopback
APP_DIR = Path(__file__).resolve().parents[1] / "app"
BODY_LIMIT = 4096  # bytes: a pick is two short strings
HOLDS = {  # a held entry's reason: the gate that held it and its verdict (anything else: the courier held it)
    "image-sensitive": ("image gate", "sensitive"), "image-unsure": ("image gate", "unsure"),
    "source-sensitive": ("text gate", "sensitive"), "source-unsure": ("text gate", "unsure"),
}


class _Answer:
    """What the resolver would tell Telegram, kept here instead: the app is not Telegram, so nothing is edited."""

    def __init__(self) -> None:
        self.text: str | None = None

    def edit_message(self, message_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        return None

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.text = text


class OwnerService:
    """The two routes' work, apart from HTTP, so the tests call it directly."""

    def __init__(self, state: Any, todoist: Any, chat_id: int, projects: list[str], floor: float, token: str) -> None:
        self.state, self.todoist, self.chat_id = state, todoist, chat_id
        self.projects, self.floor, self.token = list(projects), float(floor), token
        self._sent: dict[str, tuple[str, dict[str, float]]] = {}  # sid: the text and probabilities sent to Telegram

    def remember(self, sid: str, text: str, probabilities: dict[str, float] | None) -> None:
        """The cycle's hook: an unsorted task's text and Decider's probabilities, as its Telegram message carried
        them. Memory only, for this process's lifetime."""
        self._sent[sid] = (text, dict(probabilities or {}))

    def authorized(self, header: str | None) -> bool:
        scheme, _, given = (header or "").partition(" ")
        return scheme == "Bearer" and bool(given) and hmac.compare_digest(given.encode("utf-8"),
                                                                          self.token.encode("utf-8"))

    def pending(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        entries = self.state.ledger_entries()
        for rid, entry in entries.items():
            for sid, pick in (entry.picks or {}).items():
                items.append(self._task(rid, sid, pick))
        for rid, entry in self.state.ledger_holds():
            items.append(self._hold(rid, entry))
        return {"projects": self.projects, "items": items}

    def _task(self, rid: str, sid: str, pick: dict[str, Any]) -> dict[str, Any]:
        text, probabilities = self._sent.get(sid, (None, {}))
        try:
            confidence: float | None = float(pick.get("p"))
        except (TypeError, ValueError):
            confidence = None
        top = max(probabilities, key=probabilities.get) if probabilities else None
        resolved = ({"project": pick.get("project"), "by": pick.get("project_by"), "via": pick.get("via"),
                     "at": pick.get("at")} if pick.get("project_by") else None)
        return {"id": f"pick-{sid}", "kind": "task", "input": "recording", "ref": f"{rid} #{pick.get('ordinal')}",
                "text": text, "due": None, "from": None,
                "project": {"choice": top, "confidence": confidence, "floor": self.floor, "sure": False,
                            "probabilities": probabilities},
                "labels": list(pick.get("labels") or []), "pick": sid, "resolved": resolved,
                "source": {"export": "the courier's ledger, live"}, "illustrative": False}

    def _hold(self, rid: str, entry: Any) -> dict[str, Any]:
        image = entry.image or {}
        if entry.reason == "scene-unsure":
            return {"id": f"hold-{rid}", "kind": "scene", "input": "photo", "ref": rid,
                    "scene": {"choice": image.get("scene_choice"), "confidence": image.get("scene_p"),
                              "floor": self.floor, "options": image.get("scene_options") or {}},
                    "viewer": None, "source": {"export": "the courier's ledger, live"}, "illustrative": False}
        gate, verdict = HOLDS.get(entry.reason or "", ("courier", "held"))
        return {"id": f"hold-{rid}", "kind": "hold", "input": "photo" if image else "recording", "ref": rid,
                "gate": gate, "verdict": verdict, "p": image.get("gate_p") if gate == "image gate" else None,
                "floor": None, "reason": entry.reason, "viewer": None,
                "source": {"export": "the courier's ledger, live"}, "illustrative": False}

    def pick(self, sid: str, project: str) -> tuple[int, dict[str, Any]]:
        """The owner's category from the app, through ruling 43's resolver. Applied once: the resolver's lock, its
        first-answer-stands rule and its Todoist command uuid all hold, as for a Telegram tap."""
        if project != resolve.LEAVE and project not in self.projects:
            return 400, {"status": "refused", "error": "not one of the courier's projects"}
        answer = _Answer()
        resolver = resolve.Resolver(self.state, answer, self.todoist, self.chat_id, self.projects, via="app")
        found = self.state.pick_find(sid)
        if found is None:
            return 404, {"status": "unknown", "error": "no such unsorted task"}
        if found[1].get("project_by"):
            return 409, _done(found[1], "already answered")
        resolver.handle({"id": f"app-{sid}", "data": resolve.callback_data(sid, project),
                         "message": {"chat": {"id": self.chat_id}}})
        record = self.state.pick_find(sid)[1]
        if record.get("project_by") and record.get("project") == project:
            return 200, _done(record, "acknowledged")
        if record.get("project_by"):
            return 409, _done(record, "already answered")
        return 502, {"status": "failed", "error": "Todoist did not take the change; nothing was recorded"}


def _done(record: dict[str, Any], status: str) -> dict[str, Any]:
    return {"status": status, "project": record.get("project"), "by": record.get("project_by"),
            "via": record.get("via"), "at": record.get("at")}


def _handler(service: OwnerService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ratatoskr-owner"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/owner/pending":
                if not service.authorized(self.headers.get("Authorization")):
                    return self._json(401, {"status": "unauthorized"})
                return self._json(200, service.pending())
            return self._static(self.path)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/owner/pick":
                return self._json(404, {"status": "unknown route"})
            if not service.authorized(self.headers.get("Authorization")):
                return self._json(401, {"status": "unauthorized"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= BODY_LIMIT:
                    raise ValueError("body size")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                sid, project = str(body["sid"]), str(body["project"])
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                return self._json(400, {"status": "refused", "error": "a JSON body with sid and project"})
            code, out = service.pick(sid, project)
            log.info("owner service: pick %s -> %s (%s)", sid, project, out["status"])
            return self._json(code, out)

        def _static(self, path: str) -> None:
            name = path.split("?", 1)[0].lstrip("/") or "index.html"
            target = (APP_DIR / name).resolve()
            if APP_DIR.resolve() not in target.parents or not target.is_file():
                return self._json(404, {"status": "not found"})
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: Any) -> None:  # the request line only, never a header
            log.debug("owner service: " + fmt, *args)

    return Handler


def serve(service: OwnerService, port: int, stop: threading.Event,
          on_ready: Callable[[int], None] | None = None) -> threading.Thread:
    """Serve on 127.0.0.1:`port` (0: any free port) until `stop` is set; the server runs on its own thread."""
    server = ThreadingHTTPServer((HOST, port), _handler(service))
    server.daemon_threads = True
    if on_ready is not None:
        on_ready(server.server_address[1])

    def run() -> None:
        thread = threading.Thread(target=server.serve_forever, name="owner-service-http", daemon=True)
        thread.start()
        stop.wait()
        server.shutdown()
        server.server_close()

    watcher = threading.Thread(target=run, name="owner-service", daemon=True)
    watcher.start()
    log.info("owner service on http://%s:%d (the app at /, the routes behind the token)", HOST,
             server.server_address[1])
    return watcher
