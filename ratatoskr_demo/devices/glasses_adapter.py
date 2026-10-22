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

"""The glasses adapter (input): photos from a watched folder (Dan's ruling 47), and the video stub.

PhotoFolder is real: each image file in `ledger/inbox/photos/` is one glasses
envelope carrying an ImageRef. Its text is what ratatoskr_demo.photos lets
through: the file must be readable, the image gate on the vision server must
clear it, and the OCR on the laptop reads it; any hold comes back as a
PhotoHold, before any model or none at all. For the evaluation the folder was
fed from the desktop.

GlassesStub, below, is the video slot as it was. STUB: it does not work and is
not part of the demo.

It exists so the device seam has the slot, and it implements the same input
interface as Pocket and the text command so the cycle can be shown to need
nothing else. It reads no video. With no fixture it lists nothing; with a
fixture (tests/fixtures/glasses/fixture.json) it lists one envelope carrying a
VideoRef and returns the fixture's stand-in caption as the "transcript". A
real adapter would turn what the glasses capture into text here.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ratatoskr_demo.decider import DeciderRejected, DeciderUnavailable
from ratatoskr_demo.devices.envelope import Envelope, ImageRef, VideoRef
from ratatoskr_demo.devices.registry import Source
from ratatoskr_demo.errors import TransientError
from ratatoskr_demo.photos import UNREADABLE, ImageGate, Ocr, PhotoHold, SceneDecision, SceneResult, unreadable

DEVICE_ID = "glasses"
IS_STUB = True  # the video slot (GlassesStub); the photo folder is real (ruling 47)


def receive(envelope: Envelope) -> Envelope:
    """Return the envelope unchanged. Only a glasses envelope with a VideoRef is accepted."""
    if envelope.kind != "glasses" or not isinstance(envelope.payload, VideoRef):
        raise TypeError("the glasses stub takes only a glasses envelope carrying a VideoRef")
    return envelope


class GlassesStub:
    name = "glasses"

    def __init__(self, fixture: Path | None = None) -> None:
        self._items: dict[Envelope, dict[str, Any]] = {}
        if fixture is not None:
            data = json.loads(Path(fixture).read_text(encoding="utf-8"))
            envelope = receive(Envelope(device_id=DEVICE_ID, kind="glasses", ts=datetime.fromisoformat(data["ts"]),
                                        payload=VideoRef(uri=data["uri"])))
            self._items[envelope] = data

    def list_new(self) -> list[Envelope]:
        return list(self._items)

    def source(self, envelope: Envelope) -> Source:
        data = self._items[envelope]
        return Source(item_id=data["id"], title=data.get("title", "glasses (stub)"), tags=(),
                      recorded_at=data["ts"])

    def fetch_text(self, envelope: Envelope) -> Any:
        """STUB: the fixture's stand-in caption, not anything read from video."""
        return self._items[envelope]["caption"]


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def photo_id(path: Path) -> str:
    """A stable id for a photo that never carries its file name."""
    return "photo-" + hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12]


class PhotoFolder:
    """Photos dropped in a folder, one envelope each, held or read before anything leaves the laptop."""

    name = "glasses"

    def __init__(self, folder: Path, gate: ImageGate, ocr: Ocr, scene: SceneDecision | None = None) -> None:
        self.folder, self.gate, self.ocr, self.scene = Path(folder), gate, ocr, scene
        self.records: dict[str, dict[str, Any]] = {}  # per photo id: what goes in the ledger (numbers, no text)
        self._paths: dict[str, Path] = {}  # per photo id: its file, for the scene read (ruling 50); memory only

    def list_new(self) -> list[Envelope]:
        """Every file in the folder that looks like a photo, oldest first; the ledger skips what is finished.
        A file with an image name that is not one is listed too, so it is held as unreadable, never skipped."""
        if not self.folder.is_dir():
            return []
        files = sorted((p for p in self.folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
                       key=lambda p: (p.stat().st_mtime, p.name))
        return [Envelope(device_id=DEVICE_ID, kind="glasses", ts=_mtime(p), payload=ImageRef(uri=p.resolve().as_uri()))
                for p in files]

    def source(self, envelope: Envelope) -> Source:
        path = _path(envelope)
        return Source(item_id=photo_id(path), title="photo", tags=(), recorded_at=_mtime(path).isoformat())

    def fetch_text(self, envelope: Envelope) -> Any:
        """The OCR text, or a PhotoHold: unreadable (no model asked), or the image gate's yes or unsure."""
        path = _path(envelope)
        record: dict[str, Any] = {"extraction_calls": 0}
        self.records[photo_id(path)] = record
        self._paths[photo_id(path)] = path
        problem = unreadable(path)
        if problem is not None:
            record["unreadable"] = problem
            return PhotoHold(UNREADABLE)
        try:
            result = self.gate.check(path)
        except (DeciderUnavailable, DeciderRejected) as exc:  # as the text gate: the photo waits, nothing sent
            raise TransientError(f"image gate: Strands Decider (vision) could not be asked ({exc}); waiting",
                                 kind="decider") from None
        record.update(gate_p=round(result.p, 4), gate_seconds=round(result.seconds, 3))
        if result.reason is not None:
            return PhotoHold(result.reason, result.p)
        started = time.perf_counter()
        text, confidence = self.ocr.read(path)
        record.update(ocr_confidence=confidence, ocr_seconds=round(time.perf_counter() - started, 3))
        return text

    def decide_scene(self, item_id: str, text: str) -> SceneResult:
        """Ruling 50: what the picture represents, one read of the vision server with the picture and its OCR
        text. Unreachable or unreadable: the photo waits, as at the image gate, with nothing sent."""
        if self.scene is None:
            raise TransientError("scene: no scene decision configured; waiting", kind="decider")
        try:
            return self.scene.decide(self._paths[item_id], text)
        except (DeciderUnavailable, DeciderRejected) as exc:
            raise TransientError(f"scene: Strands Decider (vision) could not be asked ({exc}); waiting",
                                 kind="decider") from None


def _path(envelope: Envelope) -> Path:
    if not isinstance(envelope.payload, ImageRef):
        raise TypeError("the photo folder takes only a glasses envelope carrying an ImageRef")
    from urllib.parse import unquote, urlparse

    parsed = urlparse(envelope.payload.uri)
    raw = unquote(parsed.path)
    return Path(raw[1:] if len(raw) > 2 and raw[0] == "/" and raw[2] == ":" else raw)  # file:///C:/... on Windows


def _mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc)
