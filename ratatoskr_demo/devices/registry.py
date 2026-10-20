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

"""The input adapters behind one interface, and the registry that builds the enabled ones.

Dan's ruling, 17 Oct 2026: the courier must run with no wearable at all.
`[devices] inputs` names the inputs to enable (any of "text", "pocket",
"glasses"); the cycle talks only to adapters, never to pocket.py.

Every input adapter implements:

    list_new() -> [Envelope]          what is there to look at now
    source(envelope) -> Source        its ledger id, title, tags and time: never its text
    fetch_text(envelope) -> str       the transcript, at extraction time (or NOT_READY)

`source()` is the one addition to the two methods in the ruling: triage needs
an item's tags (keep, hold, ok, urgent) and title, and the ledger its id,
before its text is fetched.

    text      the Telegram /demo command; the receiver submits each message and
              the cycle handles it at once
    pocket    the Pocket recorder (pocket.py behind pocket_adapter.py)
    glasses   a STUB: it returns fixture envelopes only, and reads no video
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from ratatoskr_demo.devices.envelope import Envelope

INPUT_NAMES = ("text", "pocket", "glasses")
OUTPUT_NAMES = ("todoist", "telegram")


@dataclass(frozen=True)
class Source:
    """What the cycle may know about an input before its text. Never the text."""

    item_id: str  # the ledger id: a Pocket recording id, tg-<message id>, a glasses fixture id
    title: str
    tags: tuple[str, ...]  # lower-case tag names
    recorded_at: str


class InputAdapter(Protocol):
    name: str

    def list_new(self) -> list[Envelope]: ...

    def source(self, envelope: Envelope) -> Source: ...

    def fetch_text(self, envelope: Envelope) -> Any: ...  # str, or transcript.NOT_READY


def build_inputs(settings: Any, *, http: Any, clock: Callable[[], float]) -> dict[str, InputAdapter]:
    """The enabled input adapters, in the order [devices] inputs lists them."""
    from ratatoskr_demo.devices.glasses_adapter import GlassesStub
    from ratatoskr_demo.devices.pocket_adapter import PocketAdapter
    from ratatoskr_demo.devices.text_adapter import TextAdapter
    from ratatoskr_demo.pocket import PocketClient

    built: dict[str, InputAdapter] = {}
    for name in settings.devices.inputs:
        if name == "pocket":
            client = PocketClient(settings.secrets.pocket_api_key, http=http, fixtures_dir=settings.fixtures_dir)
            built[name] = PocketAdapter(client, clock=clock, lookback_days=settings.poll.lookback_days)
        elif name == "text":
            built[name] = TextAdapter()
        elif name == "glasses":
            fixture = (Path(settings.fixtures_dir) / "glasses" / "fixture.json") if settings.fixtures_dir else None
            built[name] = GlassesStub(fixture if fixture is not None and fixture.is_file() else None)
    return built
