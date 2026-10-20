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

"""The wrist adapter (output): one Telegram message per task, shaped for a 128-pixel watch face.

The watch shows what the phone receives; nothing else about notify.py
changes. Each message has three lines:

    research · high · 0.92                       <project> · <priority> · <confidence>
    Email Priya the preprint                     the task as written to Todoist
    commit yes 0.91 · sens clear 0.82 · prio waiting   the remaining judgments, and which
                                                 step of priority_rule() set the priority:
                                                 waiting, deadline, soon, none, or
                                                 default:waiting / default:deadline /
                                                 default:soon (the unsure question)

Line one never exceeds 24 characters. Priority is always low / med / high
(the director's choice, 16 Oct 2026: "community · medium · 0.71" would be 25),
the confidence is the project answer's, and a project below its floor shows
as "unsorted". A value that is not there shows as "-".

Line three says "sens not checked" when a task is written without a usable
sensitivity answer: with backend "decider" only under the urgent tag while
Decider is unreachable (ruling 12), since an unusable reply makes the recording
wait and a malformed `sensitive` answer holds it (ruling 23); with the
bench-only "jev" backend, when its reply was unusable (the private
repository's rule that the notice must say so). It says "sens -" when routing
is off or the task was never routed (the "Met ..." task).
"""

from __future__ import annotations

from typing import Any, Mapping

from ratatoskr_demo.judge import PRIORITY_SHORT, SOURCE_SHORT
from ratatoskr_demo.route import URGENT_SOURCE

URGENT_FIRST_LINE = "urgent · unrouted"
from ratatoskr_demo.route import RoutedTask

DEVICE_ID = "artemis"
FIRST_LINE_MAX = 24
SEP = " · "
UNSORTED = "unsorted"


def format_task(routed: RoutedTask, content: str, *, sensitivity_unchecked: bool) -> str:
    """The three-line wrist message for one task. `content` is the Todoist content (with any "[maybe] ")."""
    return "\n".join([first_line(routed), content, third_line(routed, sensitivity_unchecked=sensitivity_unchecked)])


def first_line(routed: RoutedTask) -> str:
    if routed.priority_source == URGENT_SOURCE:
        return URGENT_FIRST_LINE  # ruling 12: written while Decider was unreachable
    judged = _judged(routed)
    project = routed.project or UNSORTED
    priority = PRIORITY_SHORT.get(routed.priority, "-") if routed.priority is not None else "-"
    confidence = _confidence(judged.get("project"))
    line = SEP.join([project, priority, confidence])
    if len(line) > FIRST_LINE_MAX:  # a renamed project could be long: keep the numbers, trim the name
        room = FIRST_LINE_MAX - len(SEP.join(["", priority, confidence]))
        line = SEP.join([project[: max(room, 1)], priority, confidence])
    return line


def third_line(routed: RoutedTask, *, sensitivity_unchecked: bool) -> str:
    judged = _judged(routed)
    commit = judged.get("real_commitment")
    if commit is None or commit.problem:
        commit_text = "commit -"
    else:
        word = {True: "yes", False: "no"}.get(routed.real_commitment, "unsure")
        commit_text = f"commit {word} {_confidence(commit)}"
    sensitive = judged.get("sensitive")
    if routed.sensitivity is None:
        sens_text = "sens not checked" if sensitivity_unchecked else "sens -"
    else:
        sens_text = f"sens {routed.sensitivity} {_confidence(sensitive)}"
    prio_text = f"prio {SOURCE_SHORT.get(routed.priority_source or '', '-')}"
    return commit_text + SEP + sens_text + SEP + prio_text


def _judged(routed: RoutedTask) -> Mapping[str, Any]:
    return {getattr(j, "question", ""): j for j in routed.trace}


def _confidence(judgement: Any) -> str:
    value = getattr(judgement, "confidence", None)
    return "-" if value is None else f"{value:.2f}"
