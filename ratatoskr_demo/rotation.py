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

"""The option-order test: does the order of the project options change the answer?

A choice model with a bias toward the first option would answer differently
when the same options are listed in a different order. This test asks the
same task N times, rotating the project option list one step each time (with
N equal to the number of options, every option is listed first once), and
counts a task as flipped if its chosen project is not the same in every
rotation.

    flip rate        = flipped tasks / tasks
    first-pick rate  = answers that chose whatever option was listed first / answers

The handoff's threshold: above 10% flips, the mitigation is to list `other`
first and the rest alphabetically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

FLIP_THRESHOLD = 0.10

# ask(canonical task index, project option order) -> the chosen project, or None if unusable
Ask = Callable[[int, list[str]], str | None]


@dataclass
class RotationReport:
    rotations: int
    tasks: int
    flipped: int
    first_picks: int
    answers: int
    choices: list[list[str | None]] = field(default_factory=list)  # per task, per rotation

    @property
    def flip_rate(self) -> float:
        return self.flipped / self.tasks if self.tasks else 0.0

    @property
    def first_pick_rate(self) -> float:
        return self.first_picks / self.answers if self.answers else 0.0

    @property
    def needs_mitigation(self) -> bool:
        return self.flip_rate > FLIP_THRESHOLD

    def summary(self) -> str:
        return (f"option rotation: {self.tasks} tasks x {self.rotations} orders; "
                f"flipped {self.flipped}/{self.tasks} = {self.flip_rate:.1%}; "
                f"first-listed option chosen {self.first_picks}/{self.answers} = {self.first_pick_rate:.1%}; "
                f"threshold {FLIP_THRESHOLD:.0%}: {'MITIGATE' if self.needs_mitigation else 'ok'}")


def rotations(options: Sequence[str], n: int) -> list[list[str]]:
    """n orders of the options, each one step further round than the last."""
    if not 1 <= n <= len(options):
        raise ValueError(f"--rotate-options must be between 1 and {len(options)} (the number of options)")
    items = list(options)
    return [items[k:] + items[:k] for k in range(n)]


def mitigated_order(options: Sequence[str]) -> list[str]:
    """The handoff's mitigation: `other` first, the rest alphabetically."""
    rest = sorted(o for o in options if o != "other")
    return (["other"] if "other" in options else []) + rest


def run_rotation(options: Sequence[str], task_count: int, n: int, ask: Ask) -> RotationReport:
    orders = rotations(options, n)
    return rebuild(options, [[ask(index, order) for order in orders] for index in range(task_count)])


def rebuild(options: Sequence[str], choices: Sequence[Sequence[str | None]]) -> RotationReport:
    """The report from the choices alone (per task, per rotation), e.g. as recorded in a bench raw file."""
    n = len(choices[0]) if choices else 0
    orders = rotations(options, n) if n else []
    report = RotationReport(rotations=n, tasks=len(choices), flipped=0, first_picks=0, answers=0)
    for picks in choices:
        picks = list(picks)
        report.choices.append(picks)
        for order, pick in zip(orders, picks):
            if pick is not None:
                report.answers += 1
                report.first_picks += pick == order[0]
        if len(set(picks)) > 1:
            report.flipped += 1
    return report
