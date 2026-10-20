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

"""Read a System One answer set (Strands Decider's, or one in the same shape) against route.toml.

Strands Decider answers in the shape of the public Jev API:

    choice  {"type": "choice", "choice": str, "probabilities": {...}, "confidence": float}
    noul    {"type": "noul",   "noul": float}          probability of yes; no confidence field

`interpret()` applies each question's floor from route.toml and returns one
Judgement per question, plus a derived `priority`. The rules:

  - A Choice uses the `confidence` returned. A Noul's confidence is
    max(p, 1 - p), and its value is p >= 0.5 at or above the floor.
  - Below its floor a value stays None. A value exactly at the floor counts.
  - priority is not asked: route.priority_rule() derives it from the three
    Nouls someone_waiting, hard_deadline and worth_researching_soon and from
    whether the due date is within 30 days of the recording (due.near),
    falling back to the routed project's default; the Judgement's `note`
    says which step set it (Dan's rulings 2 and 7, 17 Oct 2026; they
    replaced a three-level Score that Strands Decider never answered above
    its floor).
  - An answer that is missing, of the wrong type, or out of range is
    unusable: its value is None and `problem` says why.

Pure: no I/O. The bench uses the same function for every backend, so each
column is judged by the same rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ratatoskr_demo.route import RouteQuestion, priority_rule

PRIORITY_SHORT = {2: "low", 3: "med", 4: "high"}
PRIORITY_LEVEL = {2: "low", 3: "medium", 4: "high"}
NOULS = ("someone_waiting", "hard_deadline", "worth_researching_soon", "real_commitment", "sensitive")
# The short names the wrist line and the trace use for priority_rule()'s steps.
SOURCE_SHORT = {"someone_waiting": "waiting", "hard_deadline": "deadline", "worth_researching_soon": "soon",
                "neither": "none", "default:waiting": "default:waiting", "default:deadline": "default:deadline",
                "default:soon": "default:soon", "urgent": "urgent"}


@dataclass(frozen=True)
class Judgement:
    """One question's answer, as Ratatoskr reads it. Labels and numbers only, never task text."""

    question: str
    raw: str  # what the model said, e.g. "research", "p=0.97"; for priority, "high (someone_waiting)"
    confidence: float | None
    floor: float
    value: Any  # the annotation, or None below the floor / when unusable
    problem: str | None = None
    note: str | None = None  # for priority: which step of priority_rule() set it
    answer: Any = None  # the raw top answer: choice name, level name, or p >= 0.5 (for agreement scoring)

    @property
    def accepted(self) -> bool:
        return self.value is not None


def interpret(answers: Mapping[str, Any], questions: Mapping[str, RouteQuestion], *,
              deadline_near: bool = True) -> dict[str, Judgement]:
    """`deadline_near` is due.near(task's due string, the recording's date); True when unknown."""
    project = _project(answers.get("project"), questions["project"])
    nouls = {name: _noul(name, answers.get(name), questions[name]) for name in NOULS}
    return {
        "project": project,
        "priority": _priority(nouls, deadline_near, project, questions["project"]),
        **nouls,
    }


def sensitivity_of(j: Judgement) -> str | None:
    """The sensitive Noul as one of four states the cycle acts on."""
    if j.problem:
        return None  # unusable answer: not checked
    if j.value is None:
        return "unsure"  # a usable answer below its floor (at 0.60: p between 0.40 and 0.60)
    return "sensitive" if j.value else "clear"


def trace_line(judged: Mapping[str, Judgement], backend: str = "decider") -> str:
    """One line for the /demo reply: every answer with its confidence, and how priority was set."""
    parts = []
    for name, label in (("project", "project"), ("someone_waiting", "waiting"), ("hard_deadline", "deadline"),
                        ("worth_researching_soon", "soon"), ("real_commitment", "commit"), ("sensitive", "sensitive")):
        j = judged.get(name)
        if j is None or j.problem:
            parts.append(f"{label} unusable")
            continue
        confidence = "-" if j.confidence is None else f"{j.confidence:.2f}"
        mark = "" if j.accepted else " (below floor)"
        parts.append(f"{label} {j.raw} {confidence}{mark}")
    priority = judged.get("priority")
    if priority is not None:
        level = priority.answer or "-"
        parts.insert(1, f"priority {level} by {SOURCE_SHORT.get(priority.note or '', 'nothing')}")
    return f"{backend}: " + " · ".join(parts)


def _unusable(name: str, floor: float, why: str) -> Judgement:
    return Judgement(name, raw="-", confidence=None, floor=floor, value=None, problem=why)


def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


def _typed(answer: Any, kind: str) -> Mapping[str, Any] | None:
    if isinstance(answer, Mapping) and answer.get("type", kind) == kind:
        return answer
    return None


def _project(answer: Any, q: RouteQuestion) -> Judgement:
    a = _typed(answer, "choice")
    if a is None:
        return _unusable("project", q.min_confidence, "missing or not a choice answer")
    choice, confidence = a.get("choice"), _probability(a.get("confidence"))
    if choice not in q.criteria or confidence is None:
        return _unusable("project", q.min_confidence, "choice not among the route.toml options")
    value = choice if confidence >= q.min_confidence else None
    return Judgement("project", choice, confidence, q.min_confidence, value, answer=choice)


def _priority(nouls: Mapping[str, Judgement], deadline_near: bool, project: Judgement,
              projects: RouteQuestion) -> Judgement:
    """priority_rule() over the three Nouls and the due date; `note` records which step set it."""
    value, source = priority_rule(nouls["someone_waiting"].value, nouls["hard_deadline"].value, deadline_near,
                                  nouls["worth_researching_soon"].value, project.value, projects.defaults)
    level = PRIORITY_LEVEL.get(value) if value is not None else None
    far = "" if deadline_near else ", due over 30 days out"
    return Judgement("priority", raw=f"{level or '-'} ({source}{far})", confidence=None, floor=0.0,
                     value=value, note=source, answer=level)


def _noul(name: str, answer: Any, q: RouteQuestion) -> Judgement:
    a = _typed(answer, "noul")
    if a is None:
        return _unusable(name, q.min_confidence, "missing or not a noul answer")
    p = _probability(a.get("noul"))
    if p is None:
        return _unusable(name, q.min_confidence, "noul outside 0-1")
    confidence = max(p, 1.0 - p)  # the ruling: a Noul's confidence is max(p, 1 - p)
    value = (p >= 0.5) if confidence >= q.min_confidence else None
    return Judgement(name, f"p={p:.2f}", confidence, q.min_confidence, value, answer=p >= 0.5)
