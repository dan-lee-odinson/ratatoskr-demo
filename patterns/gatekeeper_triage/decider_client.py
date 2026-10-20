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

"""Gatekeeper triage: Strands Decider decides whether an action may proceed, from its source first.

A plain HTTP client for Strands Decider's `POST /v1/systemone`, and the
pattern built on it. Standard library only (Python 3.11+), so this folder can
be copied into any project:

    from decider_client import DeciderClient, load_questions

    client, questions = DeciderClient(), load_questions()   # questions.toml, next to this file
    verdict = client.gate_source_text(transcript, questions)  # 1. gate the source, where there is one
    if verdict.verdict == "hold":
        ...                                                 # do not act; ask a human
    triage = client.triage(state, questions)                # 2. then judge the action itself
    triage.project, triage.priority, triage.maybe           # how to file it if it proceeds

The pattern, in order:

1. Tripwire. A code check, before any model call: if a [gatekeeper] tripwire
   term or one of its listed spellings appears in the text, or a word there
   sounds like the term (Soundex, on single words and on two neighbouring
   words written together), hold. The model cannot override it.
2. Gate the source text, where it exists. An extraction step or an agent can
   drop the detail that makes something sensitive ("cover Mara's talk"
   without "medical leave"), so `gate_source_text()` asks Decider's
   `sensitive` question of the source itself: split on speaker turns into
   windows of about 300 words (Decider reads 4,096 tokens), one question per
   window, and hold if any window is sensitive or unsure. Only when there is
   no source text does the gate fall back to the action's own arguments.
3. One read about the action. The six typed questions go to Decider in ONE
   request over one state: project (choice), someone_waiting, hard_deadline,
   worth_researching_soon, real_commitment and sensitive (noul). Decider
   returns calibrated probabilities, not text.
4. A floor per question. An answer below its `min_confidence` is not used:
   the project becomes unsorted. A noul has no confidence field; its
   confidence is max(p, 1 - p).
5. Priority by a rule in code, walked in order: someone_waiting yes -> high;
   else hard_deadline yes, with the deadline within 30 days -> high; else
   worth_researching_soon yes -> medium; else low. If the step that would
   decide rests on an unsure answer, the project's default applies, recorded
   as default:waiting / default:deadline / default:soon. This client does not
   read dates: the caller says whether the deadline is near (`deadline_near`,
   True when unknown).
6. Hold on unsure. `sensitive` yes holds; `sensitive` unsure (below its floor)
   holds too. Decider unreachable holds as well, unless [gatekeeper]
   on_unavailable = "proceed".

Strands Decider: github.com/strands-labs/strands-decider, served locally with
`strands-decider serve StrandsAgents/strands-decider-2B-hobson-v21 --port 8000`
(127.0.0.1, no authentication). It is the one model this pattern lets read
source text, because it runs on the same machine and keeps nothing. Request
and response shapes read from its README and schema.py on 16 Oct 2026.
"""

from __future__ import annotations

import json
import math
import re
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

DEFAULT_URL = "http://127.0.0.1:8000"
QUESTION_NAMES = ("project", "someone_waiting", "hard_deadline", "worth_researching_soon", "real_commitment",
                  "sensitive")
NOULS = QUESTION_NAMES[1:]
_TURN = re.compile(r"(?=\bSpeaker \d+:)")


@dataclass(frozen=True)
class TripwireTerm:
    term: str
    spellings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Questions:
    entries: dict[str, dict[str, Any]]  # name -> type, instructions, criteria, min_confidence
    project_defaults: dict[str, str]
    tripwire: tuple[TripwireTerm, ...] = ()
    on_unavailable: str = "hold"
    source_window_words: int = 300
    source_instruction: str = ""

    def request_questions(self, project_order: list[str] | None = None) -> dict[str, Any]:
        """The `questions` object of POST /v1/systemone (floors and defaults stay local)."""
        out = {}
        for name in QUESTION_NAMES:
            entry = self.entries[name]
            criteria = entry["criteria"]
            if name == "project" and project_order is not None:
                criteria = {option: criteria[option] for option in project_order}
            out[name] = {"type": entry["type"], "instructions": entry["instructions"], "criteria": criteria}
        return out

    def source_question(self) -> dict[str, Any]:
        """The `sensitive` question, worded for a stretch of source text."""
        sensitive = self.entries["sensitive"]
        return {"sensitive": {"type": "noul", "instructions": self.source_instruction or sensitive["instructions"],
                              "criteria": sensitive["criteria"]}}

    def floor(self, name: str) -> float:
        return float(self.entries[name]["min_confidence"])


def load_questions(path: Path | str | None = None) -> Questions:
    raw = tomllib.loads(Path(path or Path(__file__).with_name("questions.toml")).read_text(encoding="utf-8"))
    gate = raw.get("gatekeeper", {})
    entries = {name: dict(raw[name]) for name in QUESTION_NAMES}
    defaults = dict(entries["project"].pop("defaults", {}))
    on_unavailable = gate.get("on_unavailable", "hold")
    if on_unavailable not in ("hold", "proceed"):
        raise ValueError('[gatekeeper] on_unavailable must be "hold" or "proceed"')
    tripwire = tuple(TripwireTerm(t) if isinstance(t, str) else TripwireTerm(t["term"], tuple(t.get("spellings", ())))
                     for t in gate.get("tripwire", ()))
    return Questions(entries=entries, project_defaults=defaults, tripwire=tripwire,
                     on_unavailable=on_unavailable, source_window_words=int(gate.get("source_window_words", 300)),
                     source_instruction=str(gate.get("source_instruction", "")))


@dataclass(frozen=True)
class Answer:
    question: str
    value: Any  # the choice, or True / False for a noul
    confidence: float
    floor: float

    @property
    def sure(self) -> bool:
        return self.confidence >= self.floor


@dataclass(frozen=True)
class Verdict:
    """The source-text gate's answer: hold or proceed, and why. Counts and numbers only, never the text."""

    verdict: str  # "proceed" or "hold"
    reason: str | None = None  # "tripwire:<term>", "source-sensitive", "source-unsure", "decider-unavailable"
    windows_asked: int = 0
    windows_total: int = 0
    max_p: float = 0.0


@dataclass(frozen=True)
class Triage:
    verdict: str  # "proceed" or "hold"
    reason: str | None = None  # "tripwire:<term>", "sensitive", "sensitive-unsure", "decider-unavailable"
    project: str | None = None  # None: unsorted
    priority: str | None = None  # low / medium / high, or None
    priority_source: str | None = None  # someone_waiting / hard_deadline / worth_researching_soon / neither
    #                                     or default:waiting / default:deadline / default:soon
    maybe: bool = False  # True when Decider is sure this is a pleasantry, not a commitment
    answers: dict[str, Answer] = field(default_factory=dict)
    model: str | None = None
    latency_ms: float | None = None

    def line(self) -> str:
        """One line: every answer with its confidence, and how priority was set."""
        parts = [f"{n} {a.value} {a.confidence:.2f}{'' if a.sure else ' (unsure)'}" for n, a in self.answers.items()]
        parts.append(f"priority {self.priority or '-'} by {self.priority_source or 'nothing'}")
        return f"{self.verdict}{f' ({self.reason})' if self.reason else ''}: " + " · ".join(parts)


class DeciderUnavailable(Exception):
    """Decider could not be asked, or answered something unreadable."""


_WORD = re.compile(r"[a-z]+")
_SOUNDEX = {c: str(d) for d, letters in enumerate(("aeiouyhw", "bfpv", "cgjkqsxz", "dt", "l", "mn", "r"))
            for c in letters}


def soundex(word: str) -> str:
    """American Soundex: the first letter and three digits ("Vargrheim" -> "V626"); "" without letters."""
    letters = [c for c in word.lower() if c in _SOUNDEX]
    if not letters:
        return ""
    digits, last = "", _SOUNDEX[letters[0]]
    for c in letters[1:]:
        code = _SOUNDEX[c]
        if code != "0" and code != last:
            digits += code
        if c not in "hw":  # h and w do not separate two letters with the same code; vowels do
            last = code
    return (letters[0].upper() + digits + "000")[:4]


def tripwire_hit(text: str, terms: tuple[TripwireTerm | str, ...]) -> str | None:
    """The first tripwire term found, else None: the term or a listed spelling in the text (any case), or
    a word there, or two neighbouring words written together, with the term's Soundex code."""
    lowered = text.lower()
    words = _WORD.findall(lowered)
    sounds = {soundex(w) for w in words} | {soundex(a + b) for a, b in zip(words, words[1:])}
    for entry in (TripwireTerm(t) if isinstance(t, str) else t for t in terms):
        if any(needle.lower() in lowered for needle in (entry.term, *entry.spellings)):
            return entry.term
        if soundex(entry.term) and soundex(entry.term) in sounds:
            return entry.term
    return None


def windows(text: str, size: int = 300) -> list[str]:
    """Split on speaker turns (line breaks, or "Speaker N:"), then pack turns into ~`size`-word windows."""
    turns: list[list[str]] = []
    for line in text.splitlines():
        for turn in _TURN.split(line):
            words = turn.split()
            turns.extend(words[i:i + size] for i in range(0, len(words), size))
    out: list[list[str]] = []
    for turn in turns:
        if out and len(out[-1]) + len(turn) <= size:
            out[-1].extend(turn)
        else:
            out.append(list(turn))
    return [" ".join(window) for window in out]


class DeciderClient:
    def __init__(self, url: str = DEFAULT_URL, *, timeout: float = 180.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def systemone(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/systemone. Returns the parsed response; raises DeciderUnavailable."""
        body = json.dumps({"state": state, "questions": questions}).encode("utf-8")
        request = urllib.request.Request(f"{self.url}/v1/systemone", data=body, method="POST",
                                         headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DeciderUnavailable(type(exc).__name__) from None
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise DeciderUnavailable("no answers in the response")
        return data

    def gate_source_text(self, text: str, questions: Questions | None = None) -> Verdict:
        """The gate on source text: tripwire, then `sensitive` per window; any window sensitive or unsure holds."""
        q = questions or load_questions()
        term = tripwire_hit(text, q.tripwire)
        if term is not None:
            return Verdict("hold", f"tripwire:{term}")
        parts = windows(text, q.source_window_words)
        floor, max_p, status, asked = q.floor("sensitive"), 0.0, "clear", 0
        try:
            for window in parts:
                asked += 1
                p = _probability(((self.systemone(window, q.source_question())["answers"].get("sensitive")) or {})
                                 .get("noul"))
                if p is None:
                    raise DeciderUnavailable("no usable sensitive answer")
                max_p = max(max_p, p)
                if max(p, 1 - p) >= floor and p >= 0.5:
                    status = "sensitive"
                    break
                if max(p, 1 - p) < floor:
                    status = "unsure"
        except DeciderUnavailable:
            return Verdict("hold" if q.on_unavailable == "hold" else "proceed", "decider-unavailable",
                           asked, len(parts), max_p)
        if status == "clear":
            return Verdict("proceed", None, asked, len(parts), max_p)
        return Verdict("hold", f"source-{status}", asked, len(parts), max_p)

    def triage(self, state: Any, questions: Questions, *, text: str | None = None,
               project_order: list[str] | None = None, deadline_near: bool = True) -> Triage:
        """Judge the action itself: tripwire, one read of the six questions, floors, hold on unsure.

        `deadline_near`: the action's deadline is within 30 days, or unknown (this client reads no dates)."""
        term = tripwire_hit(text if text is not None else json.dumps(state), questions.tripwire)
        if term is not None:
            return Triage(verdict="hold", reason=f"tripwire:{term}")
        try:
            response = self.systemone(state, questions.request_questions(project_order))
        except DeciderUnavailable:
            return Triage(verdict="hold" if questions.on_unavailable == "hold" else "proceed",
                          reason="decider-unavailable")
        return judge(response, questions, deadline_near=deadline_near)


def gate_source_text(text: str, questions: Questions | None = None, client: DeciderClient | None = None) -> Verdict:
    """The source-text gate as a function: `gate_source_text(transcript) -> Verdict`."""
    return (client or DeciderClient()).gate_source_text(text, questions)


def priority_rule(waiting: bool | None, deadline: bool | None, deadline_near: bool, soon: bool | None,
                  project: str | None, defaults: Mapping[str, str]) -> tuple[str | None, str]:
    """Walked in order: waiting yes -> high; deadline yes and near -> high (a far one counts as no);
    soon yes -> medium; else low. An unsure answer at the step that decides -> the project's default,
    recorded as default:<question> (no project: no priority)."""
    deadline = deadline if deadline_near else False
    fallback = defaults.get(project) if project is not None else None
    if waiting is True:
        return "high", "someone_waiting"
    if deadline is True:
        return "high", "hard_deadline"
    if waiting is None:
        return fallback, "default:waiting"
    if deadline is None:
        return fallback, "default:deadline"
    if soon is True:
        return "medium", "worth_researching_soon"
    if soon is None:
        return fallback, "default:soon"
    return "low", "neither"


def judge(response: Mapping[str, Any], questions: Questions, *, deadline_near: bool = True) -> Triage:
    """Apply the floors, the priority rule and the hold rules to one Decider response. Pure."""
    raw = response.get("answers") or {}
    answers: dict[str, Answer] = {}
    project = _choice(raw.get("project"), questions)
    if project is not None:
        answers["project"] = project
    for name in NOULS:
        noul = _noul(name, raw.get(name), questions)
        if noul is not None:
            answers[name] = noul

    def sure_value(name: str) -> bool | None:
        a = answers.get(name)
        return a.value if a is not None and a.sure else None

    routed_project = project.value if project is not None and project.sure else None
    priority, source = priority_rule(sure_value("someone_waiting"), sure_value("hard_deadline"), deadline_near,
                                     sure_value("worth_researching_soon"), routed_project, questions.project_defaults)
    maybe = sure_value("real_commitment") is False

    sensitive = answers.get("sensitive")
    if sensitive is None:
        verdict, reason = ("hold" if questions.on_unavailable == "hold" else "proceed"), "sensitivity-unanswered"
    elif not sensitive.sure:
        verdict, reason = "hold", "sensitive-unsure"
    elif sensitive.value:
        verdict, reason = "hold", "sensitive"
    else:
        verdict, reason = "proceed", None
    return Triage(verdict=verdict, reason=reason, project=routed_project, priority=priority, priority_source=source,
                  maybe=maybe, answers=answers, model=response.get("model"), latency_ms=response.get("latency_ms"))


def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


def _choice(answer: Any, q: Questions) -> Answer | None:
    if not isinstance(answer, Mapping):
        return None
    confidence = _probability(answer.get("confidence"))
    if answer.get("choice") not in q.entries["project"]["criteria"] or confidence is None:
        return None
    return Answer("project", answer["choice"], confidence, q.floor("project"))


def _noul(name: str, answer: Any, q: Questions) -> Answer | None:
    if not isinstance(answer, Mapping):
        return None
    p = _probability(answer.get("noul"))
    if p is None:
        return None
    return Answer(name, p >= 0.5, max(p, 1.0 - p), q.floor(name))
