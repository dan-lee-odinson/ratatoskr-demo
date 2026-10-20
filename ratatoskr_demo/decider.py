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

"""The default route backend: Strands Decider 2B, served locally.

Selected by `[route] backend = "decider"`. Strands Decider is a System One
model: it returns typed, calibrated judgments rather than text. It runs on
this machine (`strands-decider serve ... --port 8000`, bound to 127.0.0.1, no
authentication), so config.py only accepts a loopback `decider_url`.

Contract, read from github.com/strands-labs/strands-decider on 16 Oct 2026
(README, docs/inference.md, src/.../schema.py and server.py at main 930e6d5;
PyPI 0.1.0 was built from fe39fad):

  POST /v1/systemone   {"state": ..., "questions": {name: question}}
                       -> {"model", "answers": {name: answer}, "usage", "latency_ms"}
  GET  /health         -> {"status", "model", "checkpoint", ...}

The request and response shapes follow the public Jev API, but the Decider
docs say compatibility with Jev itself is not verified, so this module builds
its own requests and reads its own answers (judge.py) instead of reusing the
TypeSafe SDK.

Six judgments, one read. For each task, ONE request carries all six
route.toml questions (project: choice; someone_waiting, hard_deadline,
worth_researching_soon, real_commitment, sensitive: noul) over one `state`, built by route.routing_state(): the
guarded task's content, notes and due string, and the contact line. Never
the transcript, never the recording title, never a Pocket field.

Failures (Dan's ruling 9 for the first; the private repository's v1.1 rules for the others):

  - no connection, timeout, HTTP 429 or 5xx   -> DeciderUnavailable propagates: the cycle makes
    the recording wait, nothing written, and sends no further read that cycle
  - HTTP 400 / 404 / 422, or a malformed body -> unrouted, logged, Dan told once; the task is
    written and its sensitivity counts as "not checked"
  - an answer outside route.toml               -> that field None, the others apply

Logging: the model name, token counts, latency and the resulting labels.
Never the state.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Callable

import httpx

from ratatoskr_demo import due
from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.extract import Contact, Task
from ratatoskr_demo.judge import interpret, sensitivity_of
from ratatoskr_demo.route import RouteQuestion, RoutedTask, RouterRejected, RouterUnavailable, routing_state

log = logging.getLogger(__name__)

SYSTEMONE_PATH = "/v1/systemone"
HEALTH_PATH = "/health"
HEALTH_TIMEOUT = 10.0  # the cycle's /health check before any read (Dan's ruling 9)


class DeciderUnavailable(RouterUnavailable):
    """Decider could not be asked (network, timeout, 429, 5xx): unreachable, for rulings 9 and 12."""


class DeciderRejected(RouterRejected):
    """Decider answered, but refused the request or sent something unreadable: the recording waits, at the
    source gate and at routing alike (Dan's ruling 23)."""


def build_questions(questions: dict[str, RouteQuestion], *, project_order: list[str] | None = None) -> dict[str, Any]:
    """route.toml entries -> the JSON question objects of POST /v1/systemone.

    `project_order` reorders the project options (the option-order test); the
    descriptions stay with their names.
    """
    built: dict[str, Any] = {}
    for name, q in questions.items():
        criteria: Any = q.criteria
        if name == "project" and project_order is not None:
            criteria = {option: q.criteria[option] for option in project_order}
        built[name] = {"type": q.type, "instructions": q.instructions, "criteria": criteria}
    return built


def request_body(task: Task, contact: Contact, questions: dict[str, RouteQuestion],
                 *, project_order: list[str] | None = None) -> dict[str, Any]:
    return {"state": routing_state(task, contact), "questions": build_questions(questions, project_order=project_order)}


class DeciderClient:
    """POST /v1/systemone and GET /health. Raises DeciderUnavailable / DeciderRejected."""

    def __init__(self, base_url: str, *, http: httpx.Client | None = None, timeout: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0))

    def systemone(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", SYSTEMONE_PATH, body)

    def health(self) -> dict[str, Any]:
        return self._call("GET", HEALTH_PATH, None)

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        try:
            response = self._http.request(method, self.base_url + path, json=body)
        except httpx.TransportError as exc:
            raise DeciderUnavailable(type(exc).__name__) from None
        if response.status_code == 429 or response.status_code >= 500:
            raise DeciderUnavailable(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise DeciderRejected(f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise DeciderRejected("non-JSON body") from None
        if not isinstance(data, dict):
            raise DeciderRejected("non-object body")
        return data


class DeciderRouter:
    def __init__(self, client: DeciderClient, questions: dict[str, RouteQuestion], *, expected_model: str = "",
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self.questions = questions
        self.expected_model = expected_model
        self._clock = clock
        self._notices: list[str] = []

    def check_access(self) -> None:
        """Startup check. A server running a different checkpoint is a configuration error;
        a server that is not up yet is not: tasks go unrouted until it is."""
        try:
            health = self.client.health()
        except (DeciderUnavailable, DeciderRejected) as exc:
            log.warning("route: Strands Decider not reachable at startup (%s); tasks stay unrouted until it is", exc)
            return
        checkpoint = str(health.get("checkpoint") or health.get("model") or "")
        if self.expected_model and self.expected_model not in checkpoint and checkpoint not in self.expected_model:
            raise ConfigurationError(
                f"Strands Decider is serving {checkpoint!r}, not [route] decider_model {self.expected_model!r}")
        log.info("route: Strands Decider up (%s, device %s)", checkpoint, health.get("device"))

    def ask(self, task: Task, contact: Contact, *, project_order: list[str] | None = None) -> dict[str, Any]:
        """One request, the six questions. Raises DeciderUnavailable / DeciderRejected."""
        response = self.client.systemone(request_body(task, contact, self.questions, project_order=project_order))
        answers = response.get("answers")
        if not isinstance(answers, dict):
            raise DeciderRejected("no answers object")
        usage = response.get("usage") or {}
        log.info("route: %s, %s input / %s output tokens, %s ms", response.get("model"),
                 usage.get("input_tokens"), usage.get("output_tokens"), response.get("latency_ms"))
        return response

    def annotate(self, task: Task, contact: Contact, today: date | None = None) -> RoutedTask:
        """A RoutedTask for this task: annotated where Decider is sure enough, else unrouted.

        `today` is the recording's date, for the priority rule's 30-day test (due.near).

        Decider unreachable, or the read timed out: DeciderUnavailable propagates, so the cycle can
        make the whole recording wait with nothing written (Dan's ruling 9). An unusable reply
        (DeciderRejected, a RouterRejected) propagates too, and the recording waits as it would at
        the source gate (ruling 23). A malformed `sensitive` answer counts as unsure (ruling 23), so
        the recording is held: never a task written with its sensitivity not checked."""
        try:
            response = self.ask(task, contact)
        except DeciderUnavailable as exc:
            log.warning("route: Strands Decider unavailable (%s); the recording waits", exc)
            raise
        except DeciderRejected as exc:
            log.error("route: unusable Strands Decider response (%s); the recording waits", exc)
            raise

        judged = interpret(response["answers"], self.questions, deadline_near=due.near(task.due_string, today))
        for j in judged.values():
            if j.problem:
                log.error("route: %s answer unusable (%s); left unset", j.question, j.problem)
        if judged["sensitive"].problem:  # ruling 23: not "not checked", but unsure: the recording is held
            sensitivity = "unsure"
        else:
            sensitivity = sensitivity_of(judged["sensitive"])
        routed = RoutedTask(task=task, project=judged["project"].value, priority=judged["priority"].value,
                            real_commitment=judged["real_commitment"].value, sensitivity=sensitivity,
                            priority_source=judged["priority"].note, trace=tuple(judged.values()))
        log.info("route: project=%s priority=%s (%s) real_commitment=%s sensitivity=%s", routed.project,
                 routed.priority, routed.priority_source, routed.real_commitment, routed.sensitivity)
        return routed

    def take_notices(self) -> list[str]:
        notices, self._notices = self._notices, []
        return notices


def make_health_check(settings: Any) -> Callable[[], bool]:
    """GET /health with a short timeout: True only for an answer. The cycle asks it before any read."""
    client = DeciderClient(settings.route.decider_url, timeout=HEALTH_TIMEOUT)

    def healthy() -> bool:
        try:
            client.health()
            return True
        except Exception as exc:  # unreachable, refused, timed out, or an unreadable answer: not there
            log.warning("route: Strands Decider did not answer /health (%s)", type(exc).__name__)
            return False

    return healthy


def make_router(settings: Any, questions: dict[str, RouteQuestion], *, http: httpx.Client | None = None) -> DeciderRouter:
    r = settings.route
    client = DeciderClient(r.decider_url, http=http, timeout=r.decider_timeout_seconds)
    return DeciderRouter(client, questions, expected_model=r.decider_model)
