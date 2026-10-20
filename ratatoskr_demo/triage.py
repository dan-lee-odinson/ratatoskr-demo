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

"""Decide what to do with one recording. Pure: no I/O.

`decide()` looks at the recording, its ledger entry and the settings, and
returns exactly one Decision. Precedence, first match wins:

    keep -> already -> not-ready -> hold-tag -> ledger-hold -> stop-list -> process

The stop-list is checked AFTER the ledger-hold step on purpose: once Dan tags
a held recording `ok`, it is released and the stop-list must not re-trigger.

Decisions never carry transcript text, so a Decision is always safe to log.
To release a manual `hold` tag, Dan removes `hold` and adds `ok`: while the
`hold` tag is present it wins, exactly as the precedence above says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Union

from metaphone import doublemetaphone

from ratatoskr_demo.config import Settings, StopTerm
from ratatoskr_demo.ledger import LedgerEntry
from ratatoskr_demo.transcript import NOT_READY, extract_text


@dataclass(frozen=True)
class Skip:
    reason: str  # "keep" or "already:<status>"


@dataclass(frozen=True)
class NotReady:
    """No transcript yet; leave it for the next poll."""


@dataclass(frozen=True)
class Hold:
    reason: str  # "tag:hold" or "stoplist:<term>" (the term, never the text around it)


@dataclass(frozen=True)
class Release:
    """Was on hold, Dan tagged it `ok`: process it now."""


@dataclass(frozen=True)
class StillHeld:
    """On hold, no `ok` yet: nothing to do, and no second notice."""


@dataclass(frozen=True)
class Process:
    """Nothing stands in the way: extract and write tasks."""


Decision = Union[Skip, NotReady, Hold, Release, StillHeld, Process]


def decide(recording: Mapping[str, Any], entry: LedgerEntry | None, settings: Settings) -> Decision:
    """Apply the rules in order. May raise UnrecognisedTranscriptShape."""
    rules = settings.triage
    tags = tag_names(recording)

    if rules.tag_keep.lower() in tags:
        return Skip("keep")
    if entry is not None and entry.status in ("done", "keep", "error"):
        return Skip(f"already:{entry.status}")

    text = extract_text(recording)
    if text is NOT_READY:
        return NotReady()

    if rules.tag_hold.lower() in tags:
        return Hold("tag:hold")
    if entry is not None and entry.status == "hold":
        return Release() if rules.tag_ok.lower() in tags else StillHeld()

    term = stop_list_hit(str(recording.get("title") or ""), str(text), rules.stop_list)
    if term is not None:
        return Hold(f"stoplist:{term}")
    return Process()


def tag_names(recording: Mapping[str, Any]) -> set[str]:
    """Lower-cased tag names; tags arrive as [{"name": ...}, ...] (as Pocket documents them)."""
    names = set()
    for tag in recording.get("tags") or []:
        if isinstance(tag, Mapping) and isinstance(tag.get("name"), str):
            names.add(tag["name"].strip().lower())
    return names


_WORD = re.compile(r"[^\W\d_]+")  # a run of letters


def stop_list_hit(title: str, text: str, stop_list: Iterable[StopTerm]) -> str | None:
    """Return the first stop-list term found, else None. Code only, no model (Dan's ruling 6, 17 Oct 2026).

    A term is found when (a) it or any of its listed spellings is in the title or the transcript,
    ignoring case, or (b) it sounds like a word there: its Double Metaphone code matches the code of
    any single word, or of any two neighbouring words written together, because a transcriber can
    split a name ("var grime"). Codes are computed per call and dropped with it: no transcript word
    outlives the cycle.
    """
    lowered = (title.lower(), text.lower())
    codes: dict[str, frozenset[str]] = {}
    candidates: set[str] | None = None
    for entry in stop_list:
        if any(needle.lower() in haystack for needle in (entry.term, *entry.spellings) for haystack in lowered):
            return entry.term
        sound = _codes("".join(_WORD.findall(entry.term.lower())), codes)
        if not sound:
            continue
        if candidates is None:
            candidates = _candidates(lowered)
        if any(sound & _codes(word, codes) for word in candidates):
            return entry.term
    return None


def _candidates(haystacks: Iterable[str]) -> set[str]:
    """Every word, and every two neighbouring words joined, within the title and within the transcript."""
    out: set[str] = set()
    for haystack in haystacks:
        words = _WORD.findall(haystack)
        out.update(words)
        out.update(a + b for a, b in zip(words, words[1:]))
    return out


def _codes(word: str, memo: dict[str, frozenset[str]]) -> frozenset[str]:
    if word not in memo:
        memo[word] = frozenset(code for code in doublemetaphone(word) if code) if word else frozenset()
    return memo[word]
