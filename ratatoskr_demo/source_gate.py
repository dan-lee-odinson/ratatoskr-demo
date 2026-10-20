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

"""The source-text gate: Strands Decider reads the transcript itself, before any Bedrock call.

Dan's ruling, 17 Oct 2026. The post-extraction sensitivity check can only judge
what the extraction kept, and an extraction can drop the very detail that made
a conversation sensitive ("cover Mara's talk" without "medical leave"). So the
gate runs on the source text, right after the stop-list tripwire and before
extraction:

    sensitive (any window at or above the floor)  -> HOLD "source-sensitive"
    unsure (a window below the floor; at 0.60,    -> HOLD "source-unsure"
      p between 0.40 and 0.60)
    clear (every window sure it is not)           -> on to extraction

A hold sends Dan the recording id only, writes nothing to Todoist, and is
released by the `ok` tag; a recording released with `ok` is not gated again.

Strands Decider is the only backend that may receive transcript text, because
it is bound to 127.0.0.1 (config.py refuses any other host) and persists
nothing. The gate runs only with `[route] backend = "decider"`; with any other
backend it is skipped and the post-extraction check is the only one.

Decider's context is 4,096 tokens, so the transcript is split on speaker turns
into windows of about `[route] source_window_words` words (300), and the
question is asked once per window, stopping at the first sensitive window. At
20-40 s per window on four laptop CPU threads, this is slow.

The question is route.toml's `sensitive` (its criteria and its floor), with the
instruction reworded for a stretch of conversation (Dan's choice). Logged: the
number of windows asked and the highest probability. Never the text.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any

from ratatoskr_demo.decider import DeciderClient, DeciderRejected
from ratatoskr_demo.route import RouteQuestion

log = logging.getLogger(__name__)

SOURCE_INSTRUCTION = ("Does this part of the conversation contain details that should not go into a shared "
                      "task list before the owner has reviewed them?")
DEFAULT_WINDOW_WORDS = 300
_TURN = re.compile(r"(?=\bSpeaker \d+:)")


@dataclass(frozen=True)
class SourceVerdict:
    status: str  # "sensitive" | "unsure" | "clear"
    windows_asked: int
    windows_total: int
    max_p: float
    floor: float

    @property
    def hold(self) -> bool:
        return self.status != "clear"

    @property
    def reason(self) -> str | None:
        return {"sensitive": "source-sensitive", "unsure": "source-unsure"}.get(self.status)


def windows(text: str, size: int = DEFAULT_WINDOW_WORDS) -> list[str]:
    """Split on speaker turns (line breaks, or "Speaker N:"), then pack turns into ~`size`-word windows.

    A single turn longer than `size` words is cut into `size`-word pieces.
    """
    turns: list[list[str]] = []
    for line in text.splitlines():
        for turn in _TURN.split(line):
            words = turn.split()
            for start in range(0, len(words), size):
                if words[start:start + size]:
                    turns.append(words[start:start + size])
    out: list[list[str]] = []
    for turn in turns:
        if out and len(out[-1]) + len(turn) <= size:
            out[-1].extend(turn)
        else:
            out.append(list(turn))
    return [" ".join(window) for window in out]


class SourceGate:
    def __init__(self, client: DeciderClient, sensitive: RouteQuestion, *,
                 window_words: int = DEFAULT_WINDOW_WORDS) -> None:
        self.client = client
        self.question = {"type": "noul", "instructions": SOURCE_INSTRUCTION, "criteria": sensitive.criteria}
        self.floor = sensitive.min_confidence
        self.window_words = window_words

    def body(self, window: str) -> dict[str, Any]:
        return {"state": window, "questions": {"sensitive": self.question}}

    def check(self, text: str) -> SourceVerdict:
        """Ask Decider per window. Raises DeciderUnavailable / DeciderRejected if it cannot be asked."""
        parts = windows(text, self.window_words)
        max_p, status, asked = 0.0, "clear", 0
        for window in parts:
            asked += 1
            p = _p(self.client.systemone(self.body(window)))
            max_p = max(max_p, p)
            confidence = max(p, 1.0 - p)
            if confidence >= self.floor and p >= 0.5:
                status = "sensitive"
                break  # one sensitive window is enough
            if confidence < self.floor:
                status = "unsure"
        log.info("source gate: %d of %d window(s) asked, max p=%.2f -> %s", asked, len(parts), max_p, status)
        return SourceVerdict(status=status, windows_asked=asked, windows_total=len(parts), max_p=max_p,
                             floor=self.floor)


def _p(response: dict[str, Any]) -> float:
    answer = (response.get("answers") or {}).get("sensitive")
    p = answer.get("noul") if isinstance(answer, dict) else None
    if isinstance(p, bool) or not isinstance(p, (int, float)) or math.isnan(p) or not 0.0 <= p <= 1.0:
        raise DeciderRejected("no usable sensitive answer")
    return float(p)


def make_source_gate(settings: Any, questions: dict[str, RouteQuestion], *, http: Any = None) -> SourceGate:
    r = settings.route
    client = DeciderClient(r.decider_url, http=http, timeout=r.decider_timeout_seconds)
    return SourceGate(client, questions["sensitive"], window_words=r.source_window_words)
