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

"""Telegram send_message(text) to Dan's own chat. Nothing else (C2).

The chat id is fixed when the Notifier is made, from TELEGRAM_CHAT_ID, and the
class has no way to send anywhere else. Text is sent with parse_mode HTML, so
`<`, `>` and `&` are escaped first; anything longer than Telegram's 4096
characters is split into several messages, on line breaks where possible.

Telegram puts the bot token in the URL (/bot<token>/sendMessage), and httpx
logs every request's URL at INFO. The CLI keeps httpx at WARNING, but a
secret must not depend on a log level: importing this module adds a filter to
the httpx and httpcore loggers that masks the token in every record they emit.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from ratatoskr_demo.errors import raise_for_http, transport_error

API_BASE = "https://api.telegram.org"
MAX_MESSAGE = 4096
_BOT_TOKEN_IN_URL = re.compile(r"/bot[^/\s\"']+")


class RedactBotToken(logging.Filter):
    """Mask the Telegram bot token in any log record, whatever its level."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "/bot" in message:
            record.msg, record.args = _BOT_TOKEN_IN_URL.sub("/bot<redacted>", message), ()
        return True


for _name in ("httpx", "httpcore"):
    _logger = logging.getLogger(_name)
    if not any(isinstance(f, RedactBotToken) for f in _logger.filters):
        _logger.addFilter(RedactBotToken())


class Notifier:
    def __init__(self, bot_token: str | None, chat_id: int | None, *, http: httpx.Client, base_url: str = API_BASE) -> None:
        self._http = http
        self._url = f"{base_url.rstrip('/')}/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def send_message(self, text: str) -> None:
        for chunk in split_message(html.escape(text, quote=False)):
            try:
                response = self._http.post(
                    self._url, json={"chat_id": self._chat_id, "text": chunk, "parse_mode": "HTML"}
                )
            except httpx.TransportError as exc:
                raise transport_error(exc, "telegram") from None
            raise_for_http(response, "telegram")


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split already-escaped text into chunks of at most `limit` characters."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
            # never cut inside an HTML entity such as &amp;
            amp = text.rfind("&", max(0, cut - 6), cut)
            if amp != -1 and ";" not in text[amp:cut]:
                cut = amp
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks
