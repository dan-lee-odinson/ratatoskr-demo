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

"""The owner handoff and the wiring rule for a photo scene decision. Plain functions, no model, no I/O.

    handoff(scene)                  an answer at or above the floor acts; under it, "ask the owner" with the choices
    apply_owner_answer(d, answer)   the owner's recorded answer, acting as an answer above the floor would, as the owner's
    route(d, extract, keep)         reference never calls the extractor; commitment does; unsure calls nothing
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from scene import UNSURE, Scene

EXTRACT, KEEP, ASK = "extract", "keep", "ask the owner"
ACTIONS = {"commitment": EXTRACT, "reference": KEEP}  # rename these with the options in questions.toml


@dataclass(frozen=True)
class Decision:
    answer: str  # commitment, reference, or unsure
    action: str  # extract, keep, or ask the owner
    by: str  # "decider", or "owner" once the owner has answered
    confidence: float  # Decider's, kept when the owner answers
    floor: float
    choices: tuple[str, ...] = ()  # what the owner is asked to choose between, while the action is "ask the owner"


def handoff(scene: Scene) -> Decision:
    """Decider's answer acts when its confidence is at or above the floor; under it, the owner is asked."""
    if scene.answer == UNSURE:
        return Decision(UNSURE, ASK, "decider", scene.confidence, scene.floor, tuple(scene.options))
    return Decision(scene.answer, ACTIONS[scene.answer], "decider", scene.confidence, scene.floor)


def apply_owner_answer(decision: Decision, answer: str) -> Decision:
    """The owner's recorded answer to a decision that asked: one of its choices, recorded as the owner's. A decision
    that did not ask, or was answered already, takes no answer: the first answer stands."""
    if decision.action != ASK:
        raise ValueError(f"this decision does not ask the owner (it is {decision.action}, by {decision.by})")
    if answer not in decision.choices:
        raise ValueError(f"the owner's answer must be one of {', '.join(decision.choices)}")
    return Decision(answer, ACTIONS[answer], "owner", decision.confidence, decision.floor)


def route(decision: Decision, extract: Callable[[], Any], keep: Callable[[], Any] | None = None) -> Any:
    """The wiring rule: commitment calls the extractor; reference never does (it calls `keep`, if given); unsure calls
    nothing until the owner answers. Returns what was called, or None when nothing was."""
    if decision.action == EXTRACT:
        return extract()
    if decision.action == KEEP:
        return keep() if keep is not None else None
    return None
