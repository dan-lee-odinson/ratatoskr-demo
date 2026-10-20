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

"""The recorder adapter (input): Pocket recordings in, TranscriptRef envelopes out.

It wraps pocket.PocketClient and adds nothing to what Pocket is asked. Each
`list_new()` lists the recordings in the lookback window and emits one
envelope per recording, oldest first. The envelope carries the recording id
only: the transcript is fetched by the cycle at extraction time, through
`fetch_text()`, and is never stored in an envelope (C1).

The listing's metadata (title, tags, recording time) is kept until the next
listing, so `source()` can answer without fetching the recording again: the
cycle skips finished recordings, honours a late `keep` tag and reports a
terminal failure from it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ratatoskr_demo.devices.envelope import Envelope, TranscriptRef
from ratatoskr_demo.devices.registry import Source
from ratatoskr_demo.pocket import PocketClient
from ratatoskr_demo.transcript import extract_text

DEVICE_ID = "pocket"


class PocketAdapter:
    name = "pocket"

    def __init__(self, client: PocketClient, *, clock: Callable[[], float], lookback_days: int = 3) -> None:
        self.client = client
        self._clock = clock
        self.lookback_days = lookback_days
        self._listed: dict[str, dict[str, Any]] = {}

    def list_new(self) -> list[Envelope]:
        """One envelope per listed recording, oldest first. Raises the client's errors."""
        end = datetime.fromtimestamp(self._clock(), tz=timezone.utc).date()
        items = self.client.list_recordings(end - timedelta(days=self.lookback_days), end)
        self._listed = {str(item["id"]): item for item in items}
        ordered = sorted(items, key=lambda r: str(r.get("recording_at") or ""))
        return [self.envelope(item) for item in ordered]

    def envelope(self, item: dict[str, Any]) -> Envelope:
        return Envelope(device_id=DEVICE_ID, kind="recorder", ts=self._ts(item.get("recording_at")),
                        payload=TranscriptRef(recording_id=str(item["id"])))

    def source(self, envelope: Envelope) -> Source:
        rid = self._rid(envelope)
        item = self._listed.get(rid, {"id": rid})
        tags = tuple(str(t["name"]).strip().lower() for t in item.get("tags") or []
                     if isinstance(t, dict) and isinstance(t.get("name"), str))
        return Source(item_id=rid, title=str(item.get("title") or ""), tags=tags,
                      recorded_at=str(item.get("recording_at") or ""))

    def fetch_text(self, envelope: Envelope) -> Any:
        """The recording's transcript as text, or NOT_READY. Memory only."""
        return extract_text(self.client.get_recording(self._rid(envelope)))

    def _rid(self, envelope: Envelope) -> str:
        if not isinstance(envelope.payload, TranscriptRef):
            raise TypeError("only a TranscriptRef envelope names a Pocket recording")
        return envelope.payload.recording_id

    def _ts(self, value: Any) -> datetime:
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    return parsed
            except ValueError:
                pass
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc)
