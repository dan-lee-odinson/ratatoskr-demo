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

"""The Jev route backend: TypeSafe's Jev, for the bench only.

Carried over from the private repository's v1.1 (its JevRouter) so the bench
can compare Strands Decider with the router it replaces. It is never the
default; `[route] backend = "jev"` selects it. Its requests, failure handling
and pinned host are unchanged. Its reading of the answers is not: since Dan's
rulings of 17 Oct 2026 replaced the priority Score with Nouls and a rule, every router
reads its answers with ratatoskr_demo/judge.py, so the rules cannot drift.

For each extracted task it sends ONE request to Jev, a System One model that
returns typed, calibrated judgments rather than text, asking the six
questions from prompts/route.toml at once (project: Choice; someone_waiting,
hard_deadline, worth_researching_soon, real_commitment, sensitive: Noul).

Contract, read from https://docs.typesafe.ai on 2026-09-20 (API reference,
Python SDK 0.7.0 reference, and the Choice / Score / Noul / Confidence pages):

  POST https://api.typesafe.ai/v1/systemone   {model, state, questions}
  GET  https://api.typesafe.ai/v1/models      needs the key, runs no inference;
                                              used at startup to check the key
  Choice -> choice, probabilities, confidence (0-1, from the spread)
  Noul   -> noul = probability of yes. "There is no separate confidence field."

What leaves the process. The `state` is built by routing_state() from the
guarded Extraction only: the task's content, notes and due string, and the
contact line (name, organisation, role) with its context. Nothing else: no
transcript, no recording title, no Pocket field, no recording id.

`sensitivity` is only an annotation here; the cycle acts on it.

Failures never fail the recording. Routing only annotates, so whatever goes
wrong here, the task is still written, unrouted (C7 as routing applies it):
  - no connection, timeout, HTTP 429 or 5xx  -> transient: unrouted this
    cycle, logged; Jev is left alone for a minute so the other tasks in the
    same recording do not each wait out a timeout
  - HTTP 401 / 403                           -> a configuration error. At
    startup, check_access() raises it and the service exits 78. If the key
    stops working later, tasks go unrouted and Dan is told once.
  - a malformed response, or HTTP 400 / 404 / 422 (Jev rejecting the
    questions we sent)                       -> terminal for routing only:
    unrouted, logged, Dan told once. The recording is still done.

Logging: the model id and token counts of every request, and the resulting
labels. Never the state.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Callable

import typesafe_sdk as ts

from ratatoskr_demo import due, judge
from ratatoskr_demo.config import Settings
from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.extract import Contact, Task
from ratatoskr_demo.judge import Judgement, sensitivity_of
from ratatoskr_demo.route import RouteQuestion, RoutedTask

log = logging.getLogger(__name__)

JEV_MODEL = "jev-latest"
REQUEST_TIMEOUT = 10.0  # seconds per HTTP attempt
RETRY = dict(max_retries=2, timeout=20.0)  # the SDK retries 408/429/5xx/network within 20 s in all
PAUSE_AFTER_TRANSIENT = 60.0  # seconds to leave Jev alone after a transient failure

TRANSIENT = (ts.TypeSafeAPIConnectionError, ts.TypeSafeRateLimitError, ts.TypeSafeInternalServerError)
AUTH = (ts.TypeSafeAuthenticationError, ts.TypeSafePermissionDeniedError)
REJECTED = (ts.TypeSafeAPIResponseValidationError, ts.TypeSafeBadRequestError,
            ts.TypeSafeNotFoundError, ts.TypeSafeUnprocessableEntityError)


def routing_state(task: Task, contact: Contact) -> dict[str, Any]:
    """Everything Jev is shown about one task (R3). Guarded text only."""
    return {
        "task": {"content": task.content, "notes": task.notes, "due_string": task.due_string},
        "contact": {
            "name": contact.name,
            "organisation": contact.organisation,
            "role": contact.role,
            "context": contact.context,
        },
    }


def build_questions(questions: dict[str, RouteQuestion]) -> dict[str, Any]:
    """route.toml entries -> SDK question objects (validated shapes, see route.py)."""
    built: dict[str, Any] = {}
    for name, q in questions.items():
        if q.type == "choice":
            built[name] = ts.Choice(instructions=q.instructions, criteria=q.criteria)
        elif q.type == "score":
            built[name] = ts.Score(instructions=q.instructions, criteria=q.criteria)
        else:
            built[name] = ts.Noul(instructions=q.instructions, criteria=ts.NoulCriteria(**q.criteria))
    return built


def interpret(answers: dict[str, Any], questions: dict[str, RouteQuestion], *,
              deadline_near: bool = True) -> dict[str, Judgement]:
    """Jev's SDK answers, read by the same rules as every other router (judge.py)."""
    plain = {name: answer.model_dump() if hasattr(answer, "model_dump") else answer
             for name, answer in answers.items()}
    return judge.interpret(plain, questions, deadline_near=deadline_near)


def make_client(settings: Settings, **overrides: Any) -> ts.TypeSafeClient:
    """The real SDK client. The key comes from Settings (the env file), never elsewhere."""
    key = settings.secrets.typesafe_api_key
    if not key:
        raise ConfigurationError("[route] backend = \"jev\" needs TYPESAFE_API_KEY in .env")
    return ts.TypeSafeClient(
        api_key=key,
        model=JEV_MODEL,
        timeout=REQUEST_TIMEOUT,
        retry=ts.RetryPolicy(**RETRY),
        base_url="https://api.typesafe.ai",  # pinned: no environment variable can redirect the key
        **overrides,
    )


class JevRouter:
    def __init__(self, client: ts.TypeSafeClient, questions: dict[str, RouteQuestion],
                 *, clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self.questions = questions
        self._sdk_questions = build_questions(questions)
        self._clock = clock
        self._paused_until = 0.0
        self._notices: list[str] = []

    def check_access(self) -> None:
        """Startup check. A rejected key is a configuration error (exit 78);
        no network yet is fine (P4): routing will simply wait for it."""
        try:
            self.client.models.list()
        except AUTH as exc:
            raise ConfigurationError(f"TypeSafe rejected TYPESAFE_API_KEY (HTTP {exc.status})") from None
        except (*TRANSIENT, ts.TypeSafeError) as exc:
            log.warning("route: could not reach TypeSafe at startup (%s); will try per task", type(exc).__name__)

    def ask(self, task: Task, contact: Contact) -> Any:
        """One request, the three questions. Raises SDK errors; annotate() sorts them."""
        response = self.client.system_one(state=routing_state(task, contact), questions=self._sdk_questions)
        log.info("route: %s, %s input / %s output tokens",
                 response.model, response.usage.input_tokens, response.usage.output_tokens)
        return response

    def annotate(self, task: Task, contact: Contact, today: date | None = None) -> RoutedTask:
        """A RoutedTask for this task: annotated where Jev is sure enough, else unrouted."""
        if self._clock() < self._paused_until:
            return RoutedTask(task=task)
        try:
            response = self.ask(task, contact)
        except TRANSIENT as exc:
            self._paused_until = self._clock() + PAUSE_AFTER_TRANSIENT
            log.warning("route: TypeSafe unavailable (%s); task left unrouted", type(exc).__name__)
            return RoutedTask(task=task)
        except AUTH as exc:
            log.error("route: TypeSafe rejected the key (HTTP %s); task left unrouted", exc.status)
            self._notices.append("TypeSafe rejected TYPESAFE_API_KEY; tasks are being written unrouted.")
            return RoutedTask(task=task)
        except REJECTED as exc:
            where = getattr(exc, "field_path", None) or getattr(exc, "status", "")
            log.error("route: unusable TypeSafe response (%s %s); task left unrouted", type(exc).__name__, where)
            self._notices.append(f"TypeSafe routing failed ({type(exc).__name__}); tasks are being written unrouted.")
            return RoutedTask(task=task)

        judged = interpret(response.answers, self.questions, deadline_near=due.near(task.due_string, today))
        for j in judged.values():
            if j.problem:
                log.error("route: %s answer unusable (%s); left unset", j.question, j.problem)
        routed = RoutedTask(task=task, project=judged["project"].value, priority=judged["priority"].value,
                            real_commitment=judged["real_commitment"].value,
                            sensitivity=sensitivity_of(judged["sensitive"]),
                            priority_source=judged["priority"].note, trace=tuple(judged.values()))
        log.info("route: project=%s priority=%s (%s) real_commitment=%s sensitivity=%s", routed.project,
                 routed.priority, routed.priority_source, routed.real_commitment, routed.sensitivity)
        return routed

    def take_notices(self) -> list[str]:
        notices, self._notices = self._notices, []
        return notices
