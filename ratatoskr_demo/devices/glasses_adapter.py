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

"""The glasses adapter (input). STUB: it does not work and is not part of the demo.

It exists so the device seam has the slot, and it implements the same input
interface as Pocket and the text command so the cycle can be shown to need
nothing else. It reads no video. With no fixture it lists nothing; with a
fixture (tests/fixtures/glasses/fixture.json) it lists one envelope carrying a
VideoRef and returns the fixture's stand-in caption as the "transcript". A
real adapter would turn what the glasses capture into text here.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ratatoskr_demo.devices.envelope import Envelope, VideoRef
from ratatoskr_demo.devices.registry import Source

DEVICE_ID = "glasses"
IS_STUB = True


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
