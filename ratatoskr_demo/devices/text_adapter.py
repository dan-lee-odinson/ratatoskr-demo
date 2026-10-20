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

"""The text adapter (input): the Telegram `/demo <text>` command becomes a Text envelope.

The text is treated exactly as a transcript. It goes through triage (the
stop-list tripwire and the source-text gate included, before any Bedrock
call), the C9 leak guard, and C1: it is never persisted and never logged. Its
ledger id is `tg-<message_id>`, so a Telegram update delivered twice is
processed once.

As an input adapter it has the same interface as Pocket and the glasses
stub: the Telegram receiver `submit()`s each /demo message and the cycle
handles it at once; `list_new()` returns any submitted and not yet handled.

Only the chat-id check decides who may use the command, and that check is in
commands.TelegramReceiver, next to /status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ratatoskr_demo.devices.envelope import Envelope, Text
from ratatoskr_demo.devices.registry import Source

DEVICE_ID = "telegram"
COMMAND = "/demo"
DEMO_TITLE = "/demo text"


class TextAdapter:
    name = "text"

    def __init__(self) -> None:
        self._pending: dict[Envelope, str] = {}  # envelope -> ledger id; memory only

    def submit(self, item_id: str, envelope: Envelope) -> None:
        if not isinstance(envelope.payload, Text):
            raise TypeError("the text input takes a Text envelope")
        self._pending[envelope] = item_id

    def done(self, envelope: Envelope) -> None:
        self._pending.pop(envelope, None)

    def list_new(self) -> list[Envelope]:
        return list(self._pending)

    def source(self, envelope: Envelope) -> Source:
        return Source(item_id=self._pending[envelope], title=DEMO_TITLE, tags=(), recorded_at=envelope.ts.isoformat())

    def fetch_text(self, envelope: Envelope) -> Any:
        return envelope.payload.text


def command_of(text: str) -> str:
    """The command word, without a @BotName suffix: "/demo@MyBot hi" -> "/demo"."""
    return text.strip().split(None, 1)[0].split("@", 1)[0] if text.strip() else ""


def demo_text(text: str) -> str | None:
    """The text after `/demo`, or None if this is not a /demo message or it is empty."""
    if command_of(text) != COMMAND:
        return None
    parts = text.strip().split(None, 1)
    rest = parts[1].strip() if len(parts) > 1 else ""
    return rest or None


def from_telegram(message: dict[str, Any]) -> tuple[str, Envelope] | None:
    """(ledger id, Text envelope) for a /demo message; None for anything else."""
    text = demo_text(message.get("text") or "")
    if text is None:
        return None
    message_id = message.get("message_id")
    if not isinstance(message_id, int):
        return None
    ts = datetime.fromtimestamp(message["date"], tz=timezone.utc) if isinstance(message.get("date"), int) \
        else datetime.now(tz=timezone.utc)
    return f"tg-{message_id}", Envelope(device_id=DEVICE_ID, kind="text", ts=ts, payload=Text(text=text))
