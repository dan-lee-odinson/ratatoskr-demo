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
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ratatoskr_demo.decider import DeciderClient, DeciderRejected
from ratatoskr_demo.errors import TerminalError

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
