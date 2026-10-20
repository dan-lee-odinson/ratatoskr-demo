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

"""The `route` seam: routing backends behind one function.

route(extraction, settings, router, today=...) -> Routed

The cycle calls route() on every recording, after the C9 guard and before the
Todoist write. Routing may add these optional annotations to each task:

    project          str | None    -> Todoist label proj:<project>
    priority         int | None    -> Todoist priority, 1-4 (4 is most urgent), set by
                                      priority_rule() from three yes/no questions and
                                      the due date (`today` is the recording's date)
    priority_source  str | None    -> which step of priority_rule() set it
    real_commitment  bool | None   -> False makes the Todoist writer prefix "[maybe] "
    sensitivity      str | None    -> "sensitive" / "unsure" / "clear", or None when
                                      it could not be checked. The CYCLE acts on it
                                      (holds, or notes "sens not checked");
                                      route() only annotates
    trace            the router's judgements (labels and confidences), for the
                     wrist line and the /demo reply; never written to Todoist

`None` means unrouted. An unrouted task is written exactly as it would be
without routing.

Boundaries, by construction:

- Input. route() accepts only an `Extraction`: contact line, task text,
  notes, context, all already through the C9 guard. It raises TypeError for
  anything else, so it never sees the transcript, the recording title or any
  raw Pocket field. `Extraction` has no transcript attribute and uses slots.
- Output. Routing can only annotate. A RoutedTask wraps the original, frozen
  Task, so routing cannot rewrite content, notes or due_string, and it
  returns every task, in order. The one permitted text change, "[maybe] ", is
  made by the Todoist writer (todoist.build_task_args), not here.

Backends (`[route] backend`):

- "decider" (the default): Strands Decider, served locally on 127.0.0.1
  (ratatoskr_demo/decider.py). The six route.toml questions go in ONE
  request per task, over one state.
- "jev": TypeSafe's Jev (ratatoskr_demo/jev.py), carried over unchanged from
  the private repository; used by the bench, never the default.
- "none": the identity. Every annotation is None and there is no I/O.

Each question has its own floor (`min_confidence` in route.toml). Choice and
Score use the confidence the router returns; a Noul has none, so its
confidence is max(p, 1 - p): 0.9 and 0.1 are both 90% sure (judge.py).

Jev's router (bench only) leaves a task unrouted for an API or response
problem. Strands Decider's router never does. Decider unreachable, or a read
timed out: it raises RouterUnavailable; route() then asks nothing more, leaves
the remaining tasks unrouted, and says how many in Routed.unreached. The cycle
decides: the recording waits with nothing written (Dan's ruling 9), unless it
is tagged urgent (ruling 12). Decider answered but the reply cannot be used (an
HTTP error, a non-JSON body, no answers): it raises RouterRejected; route()
asks nothing more and gives the reason in Routed.rejected, and the recording
waits, urgent or not, as the same reply makes it wait at the source gate (Dan's
ruling 23). route() itself never raises for a router problem.
prompts/route.toml is loaded and validated at startup (load_route_questions).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from datetime import date
from typing import Any, Protocol

from ratatoskr_demo.config import Settings
from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.extract import Contact, Extraction, Task


@dataclass(frozen=True, slots=True)
class RoutedTask:
    """One extracted task plus its optional routing annotations."""

    task: Task
    project: str | None = None
    priority: int | None = None
    real_commitment: bool | None = None
    # "sensitive" | "unsure" | "clear"; None = not checked. Never written anywhere.
    sensitivity: str | None = None
    # Which step of priority_rule() set `priority`: one of PRIORITY_SOURCES, or None.
    priority_source: str | None = None
    # The router's judgements (judge.Judgement), for the wrist line and the /demo
    # reply. Labels and numbers only; not part of equality; never sent to Todoist.
    trace: tuple[Any, ...] = field(default=(), compare=False)

    @property
    def content(self) -> str:
        return self.task.content

    @property
    def notes(self) -> str | None:
        return self.task.notes

    @property
    def due_string(self) -> str | None:
        return self.task.due_string

    @property
    def is_routed(self) -> bool:
        """True if routing changed what Todoist will show (sensitivity never reaches Todoist)."""
        return self.project is not None or self.priority is not None or self.real_commitment is not None


@dataclass(frozen=True, slots=True)
class Routed:
    """The same Extraction, with each task carrying its annotations."""

    contact: Contact
    tasks: tuple[RoutedTask, ...]
    confidence: str
    # How many tasks, at the end, were not asked because the router became unreachable (ruling 9).
    unreached: int = 0
    # Why the router's reply could not be used, if it could not (ruling 23): the recording waits.
    rejected: str | None = None


class RouterUnavailable(Exception):
    """The router could not be asked (Strands Decider: network, timeout, 429, 5xx). route() stops there."""


class RouterRejected(Exception):
    """The router answered, but its reply cannot be used (Strands Decider: an HTTP error, a non-JSON body, no
    answers). route() stops there, and the recording waits (Dan's ruling 23)."""


# The priority source of a task written while Decider is unreachable, under the urgent tag (ruling 12).
URGENT_SOURCE = "urgent"


def routing_state(task: Task, contact: Contact) -> dict[str, Any]:
    """Everything a router is shown about one task. Guarded text only.

    The same state as the private repository's Jev router (jev.routing_state),
    so every route backend, and every bench column, reads the same thing.
    """
    return {
        "task": {"content": task.content, "notes": task.notes, "due_string": task.due_string},
        "contact": {
            "name": contact.name,
            "organisation": contact.organisation,
            "role": contact.role,
            "context": contact.context,
        },
    }


class Router(Protocol):
    """A routing backend: one guarded task in, one RoutedTask out. Raises only RouterUnavailable and
    RouterRejected."""

    def annotate(self, task: Task, contact: Contact, today: date | None = None) -> RoutedTask:
        """May raise RouterUnavailable (unreachable) or RouterRejected (an unusable reply)."""
        ...


def route(extraction: Extraction, settings: Settings, router: Router | None = None, *,
          today: date | None = None) -> Routed:
    """Annotate tasks for Todoist. "none": identity. "decider" / "jev": ask the router, per task.

    `today` is the recording's own date: the priority rule counts a due date from it.
    """
    if not isinstance(extraction, Extraction):
        raise TypeError(
            f"route() takes only a guarded Extraction, not {type(extraction).__name__}; "
            "the transcript and raw Pocket fields must never reach routing"
        )
    backend = settings.route.backend
    if backend == "none":
        return Routed(
            contact=extraction.contact,
            tasks=tuple(RoutedTask(task=task) for task in extraction.tasks),
            confidence=extraction.confidence,
        )
    if backend in ("decider", "jev"):
        if router is None:
            raise ConfigurationError(f'[route] backend = "{backend}" but no router was built')
        routed: list[RoutedTask] = []
        for index, task in enumerate(extraction.tasks):
            try:
                routed.append(_checked(router.annotate(task, extraction.contact, today), task))
            except RouterUnavailable:  # ruling 9: ask nothing more; the cycle decides what the recording does
                rest = extraction.tasks[index:]
                return Routed(contact=extraction.contact, tasks=tuple(routed) + tuple(RoutedTask(task=t) for t in rest),
                              confidence=extraction.confidence, unreached=len(rest))
            except RouterRejected as exc:  # ruling 23: ask nothing more; the recording waits
                rest = extraction.tasks[index:]
                return Routed(contact=extraction.contact, tasks=tuple(routed) + tuple(RoutedTask(task=t) for t in rest),
                              confidence=extraction.confidence, rejected=str(exc) or type(exc).__name__)
        return Routed(contact=extraction.contact, tasks=tuple(routed), confidence=extraction.confidence)
    # config.py already refuses unknown backends at startup; this is the backstop.
    raise ConfigurationError(f"unknown route backend {backend!r}")


def urgent_unrouted(task: Task) -> RoutedTask:
    """Ruling 12: a task written while Decider is unreachable, under the urgent tag: no project, priority
    high, labelled "unrouted" by the Todoist writer, "urgent · unrouted" on the wrist."""
    return RoutedTask(task=task, priority=TODOIST_PRIORITY["high"], priority_source=URGENT_SOURCE)


def _checked(routed: RoutedTask, task: Task) -> RoutedTask:
    """R4 backstop: a router may only annotate the very task it was given."""
    if not isinstance(routed, RoutedTask) or routed.task is not task:
        raise TypeError("a router must return a RoutedTask wrapping the task it was given")
    return routed


# --- prompts/route.toml: the six questions, validated at startup ---

EXPECTED_QUESTIONS = {"project": "choice", "someone_waiting": "noul", "hard_deadline": "noul",
                      "worth_researching_soon": "noul", "real_commitment": "noul", "sensitive": "noul"}

# priority: three levels, lowest first. Each level's text starts with its name,
# and [project.defaults] uses the same names. Todoist: 4 is the most urgent.
PRIORITY_NAMES = ("low", "medium", "high")
TODOIST_PRIORITY = {"low": 2, "medium": 3, "high": 4}

# Which step of priority_rule() set a task's priority; default:<question> names the unsure answer.
PRIORITY_SOURCES = ("someone_waiting", "hard_deadline", "worth_researching_soon", "neither",
                    "default:waiting", "default:deadline", "default:soon")


def priority_rule(someone_waiting: bool | None, hard_deadline: bool | None, deadline_near: bool,
                  worth_researching_soon: bool | None, project: str | None,
                  defaults: dict[str, str] | None) -> tuple[int | None, str]:
    """The Todoist priority from three yes/no answers and the due date (Dan's ruling 7, 17 Oct 2026).

    Each answer is True or False when it cleared its floor, and None when it
    did not (or was unusable). `deadline_near` is due.near(): the due date is
    within 30 days of the recording, or cannot be read. The steps, in order:

      1. someone_waiting yes                         -> high,   "someone_waiting"
      2. hard_deadline yes and deadline_near         -> high,   "hard_deadline"
         (a deadline more than 30 days out counts as no, however sure)
      3. worth_researching_soon yes                  -> medium, "worth_researching_soon"
      4. otherwise                                   -> low,    "neither"

    If the step that would decide rests on an unsure answer, the walk stops and
    the routed project's default applies, recorded as default:waiting,
    default:deadline or default:soon (no project either: no priority, but the
    source is still recorded). A sure yes decides even when an earlier answer
    is unsure only within the high step: waiting unsure with a near deadline
    yes is high; waiting unsure with no deadline is default:waiting.
    """
    deadline = hard_deadline if deadline_near else False
    if someone_waiting is True:
        return TODOIST_PRIORITY["high"], "someone_waiting"
    if deadline is True:
        return TODOIST_PRIORITY["high"], "hard_deadline"
    if someone_waiting is None:
        return _project_default(project, defaults), "default:waiting"
    if deadline is None:
        return _project_default(project, defaults), "default:deadline"
    if worth_researching_soon is True:
        return TODOIST_PRIORITY["medium"], "worth_researching_soon"
    if worth_researching_soon is None:
        return _project_default(project, defaults), "default:soon"
    return TODOIST_PRIORITY["low"], "neither"


def _project_default(project: str | None, defaults: dict[str, str] | None) -> int | None:
    return TODOIST_PRIORITY[defaults[project]] if project is not None and defaults and project in defaults else None

ENTRY_KEYS = {"type", "instructions", "criteria", "min_confidence"}


@dataclass(frozen=True)
class RouteQuestion:
    name: str
    type: str
    instructions: Any
    criteria: Any
    min_confidence: float
    defaults: dict[str, str] | None = None  # project only: option -> low / medium / high


def route_toml_path(settings: Settings) -> Path | None:
    """A local copy in prompts_dir, if present; else None (use the packaged one)."""
    edited = settings.paths.prompts_dir / "route.toml"
    return edited if edited.is_file() else None


def load_route_questions(settings: Settings) -> dict[str, RouteQuestion]:
    """Load and validate route.toml. Called at startup; raises ConfigurationError."""
    path = route_toml_path(settings)
    if path is not None:
        text, where = path.read_text(encoding="utf-8"), str(path)
    else:
        text = resources.files("ratatoskr_demo").joinpath("prompts", "route.toml").read_text(encoding="utf-8")
        where = "packaged prompts/route.toml"
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"{where} is not valid TOML: {exc}") from None
    questions = validate_route_questions(raw, where)
    return questions if settings.route.option_descriptions else without_option_descriptions(questions)


def without_option_descriptions(questions: dict[str, RouteQuestion]) -> dict[str, RouteQuestion]:
    """Ruling 15: the project options as their names alone ("research": "research"), every other question
    as written; to measure what the one-line descriptions do."""
    from dataclasses import replace

    project = questions["project"]
    return {**questions, "project": replace(project, criteria={name: name for name in project.criteria})}


def validate_route_questions(raw: dict[str, Any], where: str = "route.toml") -> dict[str, RouteQuestion]:
    if set(raw) != set(EXPECTED_QUESTIONS):
        raise ConfigurationError(
            f"{where} must have exactly the entries {sorted(EXPECTED_QUESTIONS)}, found {sorted(raw)}"
        )
    questions = {}
    for name, expected_type in EXPECTED_QUESTIONS.items():
        entry = raw[name]
        prefix = f"{where} [{name}]"
        if not isinstance(entry, dict):
            raise ConfigurationError(f"{prefix} must be a table")
        missing = ENTRY_KEYS - set(entry)
        if missing:
            raise ConfigurationError(f"{prefix} is missing {sorted(missing)}")
        allowed = ENTRY_KEYS | ({"defaults"} if name == "project" else set())
        unknown = set(entry) - allowed
        if unknown:
            raise ConfigurationError(f"{prefix} has unknown key(s) {sorted(unknown)}")
        if entry["type"] != expected_type:
            raise ConfigurationError(f"{prefix} type must be {expected_type!r}")
        if not _non_empty(entry["instructions"]):
            raise ConfigurationError(f"{prefix} instructions must not be empty")
        confidence = entry["min_confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 < confidence <= 1:
            raise ConfigurationError(f"{prefix} min_confidence must be a number above 0 and at most 1")
        _validate_criteria(prefix, expected_type, entry["criteria"])
        defaults = _validate_defaults(prefix, entry) if name == "project" else None
        questions[name] = RouteQuestion(
            name=name,
            type=expected_type,
            instructions=entry["instructions"],
            criteria=entry["criteria"],
            min_confidence=float(confidence),
            defaults=defaults,
        )
    return questions


def _validate_defaults(prefix: str, entry: dict[str, Any]) -> dict[str, str]:
    """[project.defaults]: one of low / medium / high for every project, no strays."""
    defaults = entry.get("defaults")
    if not isinstance(defaults, dict):
        raise ConfigurationError(f"{prefix} needs a [project.defaults] table (a default priority per project)")
    projects = set(entry["criteria"])
    if set(defaults) != projects:
        missing, extra = sorted(projects - set(defaults)), sorted(set(defaults) - projects)
        raise ConfigurationError(f"{prefix} defaults must name every project exactly once "
                                 f"(missing {missing}, not a project {extra})")
    for project, level in defaults.items():
        if level not in PRIORITY_NAMES:
            raise ConfigurationError(f"{prefix} default for {project!r} must be one of {list(PRIORITY_NAMES)}")
    return dict(defaults)


def _validate_criteria(prefix: str, kind: str, criteria: Any) -> None:
    if kind == "choice":
        if not isinstance(criteria, dict) or len(criteria) < 2:
            raise ConfigurationError(f"{prefix} criteria must be a table of at least two options")
        for option, description in criteria.items():
            if not option or any(ch.isspace() for ch in option):
                raise ConfigurationError(f"{prefix} option {option!r} must be one word (it becomes a label)")
            if not _non_empty(description):
                raise ConfigurationError(f"{prefix} option {option!r} needs a description")
    elif kind == "score":
        if not isinstance(criteria, list) or len(criteria) != len(PRIORITY_NAMES):
            raise ConfigurationError(f"{prefix} criteria must list exactly {len(PRIORITY_NAMES)} levels")
        for level, name in zip(criteria, PRIORITY_NAMES):
            if not isinstance(level, str) or not level.strip().lower().startswith(f"{name}:"):
                raise ConfigurationError(
                    f"{prefix} levels must be text starting 'low:', 'medium:', 'high:', in that order")
            if not level.split(":", 1)[1].strip():
                raise ConfigurationError(f"{prefix} level {name!r} needs a description")
    elif kind == "noul":
        # `true` is required; `false` is optional (TypeSafe accepts a Noul with one side described).
        if not isinstance(criteria, dict) or "true" not in criteria or not set(criteria) <= {"true", "false"}:
            raise ConfigurationError(f"{prefix} criteria must have `true` and may have `false`, nothing else")
        if not all(_non_empty(v) for v in criteria.values()):
            raise ConfigurationError(f"{prefix} `true` and `false` need descriptions")


def _non_empty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list)):
        return bool(value)
    return False
