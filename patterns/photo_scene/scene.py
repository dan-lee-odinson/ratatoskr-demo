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

"""What a picture represents, asked of a Strands Decider vision server in one request.

Standard-library HTTP, and Pillow to read the picture: it must be a PNG, JPEG, WebP or GIF, and it is sent upright
(its EXIF orientation honoured) with its long side at most 2000 px. One `POST /v1/systemone` carries the picture, its
text (what an OCR read from it, or "") as the state, and the scene question; the answer comes back with its
confidence and each option's probability. Below the floor the answer is "unsure". If the server cannot be asked, or
its reply is unusable, `decide()` raises SceneUnavailable: nothing should run on a picture whose scene is not known.
"""

from __future__ import annotations

import base64
import io
import json
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8001"  # strands-decider 0.2.0 served with --vision; on this machine only
UNSURE = "unsure"
FORMATS = ("PNG", "JPEG", "WEBP", "GIF")
LONG_SIDE = 2000
ORIENTATION = 0x0112  # the EXIF orientation tag


class SceneUnavailable(Exception):
    """The vision server could not be asked, or its reply carried no usable scene answer."""


@dataclass(frozen=True)
class SceneQuestion:
    instructions: str
    criteria: dict[str, str]
    floor: float

    def body(self) -> dict[str, Any]:
        return {"type": "choice", "instructions": self.instructions, "criteria": dict(self.criteria)}


def load_question(path: Path | str | None = None) -> SceneQuestion:
    raw = tomllib.loads(Path(path or Path(__file__).with_name("questions.toml")).read_text(encoding="utf-8"))
    scene = raw["scene"]
    return SceneQuestion(str(scene["instructions"]), dict(scene["criteria"]), float(scene["min_confidence"]))


@dataclass(frozen=True)
class Scene:
    answer: str  # one of the options, or "unsure" when the top option is under the floor
    choice: str  # Decider's top option
    confidence: float
    floor: float
    options: tuple[str, ...]  # the question's options, in its order
    probabilities: dict[str, float]  # each option's probability as Decider gave it


def picture_bytes(picture: Path | str | bytes) -> bytes:
    """The picture as it is sent: upright, the long side at most LONG_SIDE; the file's own bytes when it already is."""
    from PIL import Image, ImageOps

    raw = picture if isinstance(picture, bytes) else Path(picture).read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in FORMATS:
            raise ValueError(f"not a PNG, JPEG, WebP or GIF picture ({image.format})")
        if int(image.getexif().get(ORIENTATION) or 1) == 1 and max(image.size) <= LONG_SIDE:
            return raw
        upright = ImageOps.exif_transpose(image)
        upright.thumbnail((LONG_SIDE, LONG_SIDE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        upright.convert("RGB").save(out, "JPEG", quality=95) if image.format == "JPEG" else upright.save(out, "PNG")
        return out.getvalue()


class SceneClient:
    def __init__(self, url: str = DEFAULT_URL, *, timeout: float = 180.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def systemone(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/systemone. Returns the parsed response; raises SceneUnavailable."""
        request = urllib.request.Request(f"{self.url}/v1/systemone", data=json.dumps(body).encode("utf-8"),
                                         method="POST", headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SceneUnavailable(type(exc).__name__) from None
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise SceneUnavailable("no answers in the response")
        return data

    def decide(self, picture: Path | str | bytes, text: str, question: SceneQuestion | None = None) -> Scene:
        """One read: the picture and its text, the scene question; the answer, its confidence, every option's p."""
        q = question or load_question()
        image = base64.b64encode(picture_bytes(picture)).decode("ascii")
        answer = self.systemone({"state": text, "images": [image], "questions": {"scene": q.body()}})["answers"].get(
            "scene") or {}
        choice, confidence = answer.get("choice"), answer.get("confidence")
        if choice not in q.criteria or not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
            raise SceneUnavailable("the scene answer was not one of its options with a probability")
        given = answer.get("probabilities") if isinstance(answer.get("probabilities"), dict) else {}
        probabilities = {k: float(v) for k, v in given.items() if k in q.criteria and isinstance(v, (int, float))}
        return Scene(choice if confidence >= q.floor else UNSURE, choice, float(confidence), q.floor,
                     tuple(q.criteria), probabilities)
