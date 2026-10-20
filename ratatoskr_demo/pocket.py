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

"""Thin client for the Pocket public API. No business logic.

VERIFIED (Pocket docs, read 4 Sep 2026 by the director):
  GET /public/recordings        start_date, end_date, page, limit (max 100)
  GET /public/recordings/{id}   include_transcript, include_summarizations
Auth: "Authorization: Bearer <key>". There is no delete endpoint, and this
client has no method that could delete or change anything (C2).

UNVERIFIED: whether the list call carries transcripts inline. We never
rely on it: the list is used only to discover ids and tags, and every
recording we might process is fetched again with include_transcript=true.

When RATATOSKR_FIXTURES is set, the client reads the JSON files in that
directory instead of the network, for tests and `--dry-run`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ratatoskr_demo.errors import TerminalError, raise_for_http, transport_error

BASE_URL = "https://public.heypocketai.com/api/v1"
PAGE_LIMIT = 100
MAX_PAGES = 50  # 5,000 recordings in one window is far beyond real use; stop runaway paging


class PocketContractError(TerminalError):
    """Pocket answered, but not in the documented shape."""


class PocketClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        http: httpx.Client | None = None,
        fixtures_dir: Path | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._fixtures = fixtures_dir
        self._base = base_url.rstrip("/")
        self._http = http
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def list_recordings(self, start: date, end: date) -> list[dict[str, Any]]:
        """Every recording in the window, following pagination."""
        if self._fixtures is not None:
            return self._fixture_list()
        recordings: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            body = self._get(
                "/public/recordings",
                {"start_date": start.isoformat(), "end_date": end.isoformat(), "page": page, "limit": PAGE_LIMIT},
            )
            data = body.get("data")
            if not isinstance(data, list):
                raise PocketContractError("recording list has no data array", keys=tuple(sorted(body)))
            recordings.extend(item for item in data if isinstance(item, dict) and isinstance(item.get("id"), str))
            pagination = body.get("pagination") or {}
            if not pagination.get("has_more"):
                break
        return recordings

    def get_recording(self, recording_id: str) -> dict[str, Any]:
        """One recording with its transcript."""
        if self._fixtures is not None:
            return self._fixture_detail(recording_id)
        body = self._get(
            f"/public/recordings/{recording_id}",
            {"include_transcript": "true", "include_summarizations": "false"},
        )
        data = body.get("data")
        if not isinstance(data, dict) or data.get("id") != recording_id:
            raise PocketContractError("recording detail has no matching data object", keys=tuple(sorted(body)))
        return data

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("PocketClient needs an http client outside fixtures mode")
        try:
            response = self._http.get(self._base + path, params=params, headers=self._headers)
        except httpx.TransportError as exc:
            raise transport_error(exc, "pocket") from None
        raise_for_http(response, "pocket")
        try:
            body = response.json()
        except ValueError:
            raise PocketContractError("pocket returned a non-JSON body") from None
        if not isinstance(body, dict):
            raise PocketContractError("pocket returned a non-object body")
        return body

    # --- fixtures mode ---

    def _fixture_list(self) -> list[dict[str, Any]]:
        path = self._fixtures / "list_recordings_page1.json"  # type: ignore[operator]
        body = json.loads(path.read_text(encoding="utf-8"))
        return [item for item in body.get("data", []) if isinstance(item, dict)]

    def _fixture_detail(self, recording_id: str) -> dict[str, Any]:
        for path in sorted(self._fixtures.glob("*.json")):  # type: ignore[union-attr]
            if path.name.startswith("list_"):
                continue
            body = json.loads(path.read_text(encoding="utf-8"))
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict) and data.get("id") == recording_id:
                return data
        raise PocketContractError(f"no fixture for recording {recording_id}")
