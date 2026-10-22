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

"""The photo path's checks before anything leaves the laptop (Dan's ruling 47, 21-22 Oct 2026).

A photo in the watched folder goes through, in order:

    readable   the file exists, decodes as PNG, JPEG, WebP or GIF, and is not blank
               (near-uniform: its grayscale pixels' standard deviation is under
               BLANK_STDDEV of 255). Otherwise HOLD "unreadable", no model asked:
               Decider's confidence does not tell an empty picture from a full one
               (a blank page read p 0.64, an empty state 0.63, in ruling 47's Part A)
    image gate Strands Decider with --vision (its own venv, 127.0.0.1:8001) answers
               "Is this picture of something that must never leave this machine?"
               with the text gate's criteria, at the text gate's floor from
               route.toml (0.60; unsure is p between 0.40 and 0.60). Yes or unsure:
               HOLD, id-only notice. The checkpoint was not trained on images (its
               model card); the floor is the text gate's, a starting point, not tuned
    OCR        RapidOCR (onnxruntime, on the laptop) reads the picture's text; it
               becomes the transcript, and the per-line OCR confidence goes in the
               ledger. Nothing is sent anywhere

From the transcript on, the courier's path is unchanged: the stop-list tripwire,
the text gate on the published Decider server, the extraction, the routing read,
the rule, Todoist, Telegram. So a photo passes the image gate and both text checks
before any cloud call.

Then, after the text gate and before anything is extracted (Dan's ruling 50, made
two-way by ruling 54), the scene: one read of the vision server, the picture and its
OCR text as the state, one choice question, "What does this picture represent?", at
the routing read's floor for a choice question (route.toml's project floor, 0.70: a
starting point, and the checkpoint was not trained on images):

    commitment     a promise in either direction: the courier's path as it is; the
                   extraction's own text carries who owes whom
    reference      no cloud call: the OCR text is kept on the laptop as
                   ledger/reference/<id>.txt with the picture's id and date, the
                   wrist line reads `reference · kept on the laptop`, nothing goes
                   to Todoist
    unsure         nothing sent anywhere: the wrist line `scene unsure · <p>` with
                   two buttons, Commitment and Reference; the owner's tap is
                   recorded (`scene_by: owner`) and the chosen path runs on the
                   next cycle; a second tap changes nothing

The direction of a promise is not a model answer here: the second addendum of the
image evaluation showed the checkpoint could not read it from a picture and its text
(a mirrored pair with identical probabilities, a note owed to the owner answered as
owed by him at 0.71), so it is read by the extraction and by the owner (ruling 54).

A photo held as unsure by the image gate or the text gate (Dan's ruling 52) gets one
button on its id-only notice, Release; one held as sensitive gets none. The owner's
tap records the release and sends nothing anywhere; on the next cycle the photo
continues from the check after the one that held it (from the image gate: the OCR,
the tripwire, the text gate, the scene; from the text gate: the scene, its text read
again on the laptop), and every later check runs as usual. A release is recorded with
the file's sha256 and carried forward for that unchanged file on every later cycle
(ruling 57), so a released photo whose scene the owner then answers is not stopped by
the gate he released it from; a file whose hash differs inherits nothing. An owner's
release is not a model's clear, and the ledger records it as the owner's.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ratatoskr_demo.decider import DeciderClient, DeciderRejected
from ratatoskr_demo.errors import RatatoskrError, TerminalError

log = logging.getLogger(__name__)

FORMATS = ("PNG", "JPEG", "WEBP", "GIF")
BLANK_STDDEV = 2.0  # of 255: below it, the picture is near-uniform and holds as blank
PICTURE_QUESTION = "Is this picture of something that must never leave this machine?"
UNREADABLE = "unreadable"
GATE_REASONS = {"sensitive": "image-sensitive", "unsure": "image-unsure"}


@dataclass(frozen=True)
class PhotoHold:
    """What the photo adapter hands the cycle instead of a transcript: hold it, ask nothing further."""

    reason: str
    p: float | None = None


def unreadable(path: Path) -> str | None:
    """Why the photo cannot be read, before any model: "missing", "not an image", "unsupported format" (not
    PNG, JPEG, WebP or GIF) or "blank"; None if it can be."""
    from PIL import Image, ImageStat

    if not path.is_file():
        return "missing"
    try:
        with Image.open(path) as image:
            if image.format not in FORMATS:
                return "unsupported format"
            image.load()
            spread = ImageStat.Stat(image.convert("L")).stddev[0]
    except Exception:  # Pillow raises many kinds for a file it cannot decode
        return "not an image"
    return "blank" if spread < BLANK_STDDEV else None


@dataclass(frozen=True)
class GateResult:
    status: str  # "clear", "sensitive" or "unsure"
    p: float
    seconds: float

    @property
    def reason(self) -> str | None:
        return GATE_REASONS.get(self.status)


class ImageGate:
    """The picture question, asked of the vision server about one photo."""

    def __init__(self, client: DeciderClient, sensitive: Any) -> None:
        self.client = client
        self.floor = float(sensitive.min_confidence)  # the text gate's floor, from route.toml
        self.question = {"type": "noul", "instructions": PICTURE_QUESTION, "criteria": sensitive.criteria}

    def check(self, path: Path) -> GateResult:
        image = base64.b64encode(path.read_bytes()).decode("ascii")
        started = time.perf_counter()
        response = self.client.systemone({"state": "", "images": [image], "questions": {"picture": self.question}})
        seconds = time.perf_counter() - started
        try:
            p = float(response["answers"]["picture"]["noul"])
        except (KeyError, TypeError, ValueError):
            raise DeciderRejected("the image gate's reply had no picture answer") from None
        if not 0.0 <= p <= 1.0:
            raise DeciderRejected("the image gate's answer was not a probability")
        return GateResult(judge(p, self.floor), p, seconds)


def judge(p: float, floor: float) -> str:
    """Yes at or above the floor holds, unsure (confidence under it) holds, no at or above it clears."""
    if max(p, 1.0 - p) < floor:
        return "unsure"
    return "sensitive" if p >= 0.5 else "clear"


def make_image_gate(settings: Any, questions: dict[str, Any]) -> ImageGate:
    r = settings.route
    return ImageGate(DeciderClient(r.vision_url, timeout=r.decider_timeout_seconds), questions["sensitive"])


class Ocr:
    """RapidOCR on the laptop: the picture's text, one line per detected line, and each line's confidence."""

    def __init__(self, engine: Callable[[str], Any] | None = None) -> None:
        self._engine = engine

    def read(self, path: Path) -> tuple[str, list[float]]:
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR  # the photos extra; loaded only when a photo is read

            self._engine = RapidOCR()
        try:
            result, _ = self._engine(str(path))
        except Exception as exc:  # a reader that fails on a picture the gate cleared: nothing was sent anywhere
            raise TerminalError(f"photo: the OCR could not read it ({type(exc).__name__})") from None
        lines = [(str(r[1]), float(r[2])) for r in (result or []) if str(r[1]).strip()]
        return "\n".join(text for text, _ in lines), [round(score, 4) for _, score in lines]


# --- the scene: what the picture represents (Dan's ruling 50; two-way since ruling 54) ---

SCENE_QUESTION = "What does this picture represent?"
SCENE_CRITERIA = {
    "commitment": ("A promise in either direction: something the owner promised a specific person or owes by a date, "
                   "or something a specific person promised the owner or owes the owner."),
    "reference": ("Information worth keeping, with no promise in either direction: a menu, a poster, a schedule, a "
                  "catalog page, a card with contact details and nothing owed."),
}
UNSURE = "unsure"
SCENE_ACTIONS = {"commitment": "extracted", "reference": "kept on the laptop", UNSURE: "asked the owner"}
SCENE_HOLD = "scene-unsure"
REFERENCE_LINE = "reference · kept on the laptop"
SCENE_PREFIX = "scene"
SCENE_BUTTONS = (("Commitment", "commitment"), ("Reference", "reference"))
SEP = " · "


@dataclass(frozen=True)
class SceneResult:
    answer: str  # one of SCENE_CRITERIA, or UNSURE when the top choice is below the floor
    choice: str  # Decider's top choice
    p: float  # its confidence
    seconds: float
    options: tuple[tuple[str, float], ...] = ()  # each option's probability as Decider gave it (numbers only)


class SceneDecision:
    """The scene question, asked of the vision server: the picture and its OCR text as the state."""

    def __init__(self, client: DeciderClient, floor: float) -> None:
        self.client = client
        self.floor = float(floor)  # the routing read's floor for a choice question, from route.toml
        self.question = {"type": "choice", "instructions": SCENE_QUESTION, "criteria": dict(SCENE_CRITERIA)}

    def decide(self, path: Path, text: str) -> SceneResult:
        image = base64.b64encode(path.read_bytes()).decode("ascii")
        started = time.perf_counter()
        response = self.client.systemone({"state": text, "images": [image], "questions": {"scene": self.question}})
        seconds = time.perf_counter() - started
        try:
            answer = response["answers"]["scene"]
            choice, p = str(answer["choice"]), float(answer["confidence"])
        except (KeyError, TypeError, ValueError):
            raise DeciderRejected("the scene reply had no choice answer") from None
        if choice not in SCENE_CRITERIA or not 0.0 <= p <= 1.0:
            raise DeciderRejected("the scene answer was not one of its options with a probability")
        given = answer.get("probabilities") if isinstance(answer.get("probabilities"), dict) else {}
        options = tuple((k, round(float(v), 4)) for k, v in given.items()
                        if k in SCENE_CRITERIA and isinstance(v, (int, float)))
        return SceneResult(choice if p >= self.floor else UNSURE, choice, p, seconds, options)


def make_scene_decision(settings: Any, questions: dict[str, Any]) -> SceneDecision:
    r = settings.route
    return SceneDecision(DeciderClient(r.vision_url, timeout=r.decider_timeout_seconds),
                         questions["project"].min_confidence)


def keep_reference(folder: Path, item_id: str, date: str, text: str) -> Path:
    """The OCR text of a reference picture, kept on the laptop and nowhere else, with the picture's id and date."""
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / f"{item_id}.txt"
    note.write_text(f"id: {item_id}\ndate: {date}\n\n{text}\n", encoding="utf-8")
    return note


def scene_sid(item_id: str) -> str:
    """Ten hex characters standing for one picture's scene keyboard."""
    return hashlib.sha256(f"{SCENE_PREFIX}:{item_id}".encode("utf-8")).hexdigest()[:10]


def scene_line(p: float | None) -> str:
    return SEP.join(["scene unsure", f"{p:.2f}" if p is not None else "-"])


def scene_keyboard(sid: str) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": text, "callback_data": f"{SCENE_PREFIX}:{sid}:{key}"}
                                 for text, key in SCENE_BUTTONS]]}


def is_scene_tap(data: str) -> bool:
    return data.startswith(SCENE_PREFIX + ":")


def parse_scene(data: str) -> tuple[str, str] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != SCENE_PREFIX or not parts[1] or parts[2] not in SCENE_CRITERIA:
        return None
    return parts[1], parts[2]


class SceneTaps:
    """The owner's answer to an unsure scene, from its buttons, on the Telegram receiver of ruling 43. Only the
    owner's chat is answered; the first answer stands, and a second tap changes nothing. No model is asked."""

    def __init__(self, state: Any, telegram: Any, chat_id: int, *, clock: Callable[[], float] | None = None) -> None:
        self.state, self.telegram, self.chat_id, self.clock = state, telegram, chat_id, clock

    def handle(self, query: dict[str, Any]) -> None:
        callback_id = str(query.get("id", ""))
        message = query.get("message") or {}
        if (message.get("chat") or {}).get("id") != self.chat_id:
            log.warning("telegram: a scene button tap from another chat was ignored")
            self._answer(callback_id)
            return
        parsed = parse_scene(str(query.get("data") or ""))
        found = self.state.scene_find(parsed[0]) if parsed else None
        if parsed is None or found is None:
            self._answer(callback_id, "unknown picture")
            return
        (_, answer), (item_id, image) = parsed, found
        label = dict((key, text) for text, key in SCENE_BUTTONS)
        if image.get("scene_by") == "owner":  # answered already: the first answer stands
            self._answer(callback_id, f"already answered: {label.get(image.get('scene'), image.get('scene'))}")
            return
        now = self.clock() if self.clock else datetime.now(timezone.utc).timestamp()
        self.state.scene_resolve(item_id, {"scene": answer, "scene_by": "owner",
                                           "scene_at": datetime.fromtimestamp(now, tz=timezone.utc)
                                           .isoformat(timespec="seconds")})
        rest = (message.get("text") or "").split("\n")[1:]
        line = f"{scene_line(image.get('scene_p'))} → {label[answer]}, by owner; runs on the next cycle"
        try:
            self.telegram.edit_message(int(message["message_id"]), "\n".join([line, *rest]))
        except (KeyError, TypeError, ValueError, RatatoskrError) as exc:  # the answer stands; the message is stale
            log.warning("telegram: could not edit the scene message (%s)", type(exc).__name__)
        self._answer(callback_id)

    def _answer(self, callback_id: str, text: str | None = None) -> None:
        try:
            self.telegram.answer_callback(callback_id, text)
        except RatatoskrError as exc:
            log.warning("telegram: could not answer the scene button tap (%s)", exc)


# --- the owner's release of an unsure hold (Dan's ruling 52) ---

# A photo held as unsure by one of these gates gets a Release button; one held as sensitive (a yes) gets none.
RELEASABLE = {"image-unsure": "image gate", "source-unsure": "text gate"}
GATE_ORDER = ("image gate", "text gate")  # the order a photo meets them in
RELEASE_PREFIX = "release"


def release_sid(item_id: str, gate: str) -> str:
    """Ten hex characters standing for one picture's Release button at one gate."""
    return hashlib.sha256(f"{RELEASE_PREFIX}:{item_id}:{gate}".encode("utf-8")).hexdigest()[:10]


def release_keyboard(sid: str) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "Release", "callback_data": f"{RELEASE_PREFIX}:{sid}"}]]}


def is_release_tap(data: str) -> bool:
    return data.startswith(RELEASE_PREFIX + ":")


def file_sha256(path: Path) -> str:
    """The photo file's sha256: an owner's release holds for this file, unchanged, and for no other (ruling 57)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def passed_gates(earlier: dict[str, Any] | None, sha: str | None) -> frozenset[str]:
    """The gates this photo need not meet again on this cycle (ruling 57), from its ledger record: each gate the owner
    released for this file (a release recorded by the owner with the file's sha256), and both gates once the owner
    has answered its scene (they had cleared, or been released, before the scene was asked). Only for the same
    unchanged file: a file whose hash differs from the recorded one is a new picture and inherits nothing."""
    if not earlier or not sha or earlier.get("sha256") != sha:
        return frozenset()
    released = [GATE_ORDER.index(r["held_by"]) for r in earlier.get("releases") or []
                if r.get("by") == "owner" and r.get("sha256") == sha and r.get("held_by") in GATE_ORDER]
    if earlier.get("scene_by") == "owner":
        released.append(len(GATE_ORDER) - 1)
    # a gate is only reached once every gate before it has cleared or been released for this file
    return frozenset(GATE_ORDER[: max(released) + 1]) if released else frozenset()


class ReleaseTaps:
    """The owner's Release on an unsure hold, on the Telegram receiver of ruling 43: only the owner's chat is
    answered, and a second tap changes nothing. The tap sends nothing anywhere; the picture continues on the next
    cycle from the check after the one that held it, and every later check runs as usual."""

    def __init__(self, state: Any, telegram: Any, chat_id: int, *, clock: Callable[[], float] | None = None) -> None:
        self.state, self.telegram, self.chat_id, self.clock = state, telegram, chat_id, clock

    def handle(self, query: dict[str, Any]) -> None:
        callback_id = str(query.get("id", ""))
        message = query.get("message") or {}
        if (message.get("chat") or {}).get("id") != self.chat_id:
            log.warning("telegram: a release button tap from another chat was ignored")
            self._answer(callback_id)
            return
        parts = str(query.get("data") or "").split(":")
        found = self.state.release_find(parts[1]) if len(parts) == 2 and parts[1] else None
        if found is None:
            self._answer(callback_id, "unknown picture")
            return
        item_id, release = found
        if release.get("by") == "owner":  # released already: nothing changes
            self._answer(callback_id, "already released")
            return
        now = self.clock() if self.clock else datetime.now(timezone.utc).timestamp()
        self.state.release_resolve(item_id, parts[1], {
            "by": "owner", "at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="seconds")})
        lines = (message.get("text") or "").split("\n")
        lines[0] = f"{lines[0]} → released by owner; continues on the next cycle"
        try:
            self.telegram.edit_message(int(message["message_id"]), "\n".join(lines))
        except (KeyError, TypeError, ValueError, RatatoskrError) as exc:  # the release stands; the message is stale
            log.warning("telegram: could not edit the release message (%s)", type(exc).__name__)
        self._answer(callback_id)

    def _answer(self, callback_id: str, text: str | None = None) -> None:
        try:
            self.telegram.answer_callback(callback_id, text)
        except RatatoskrError as exc:
            log.warning("telegram: could not answer the release button tap (%s)", exc)
