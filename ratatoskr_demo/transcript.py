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

"""Turn a Pocket recording's `transcript` field into plain text.

Pocket documents the `transcript` field but shows it only as `null`, so its
populated shape is UNVERIFIED. Rather than guess a richer shape, this module
accepts the few shapes the handoff lists and fails loud on anything else. The
first real recording (tests/fixtures/real_recording.json) is the authority.

Rules, applied in order:
  1. str                              -> the string itself
  2. {"text": str, ...}               -> the text
  3. [{"text": str, ...}, ...]        -> segments joined with newlines,
                                         "speaker: " prefixed when present
  4. {"segments": [...]}              -> rule 3 on the segments
  5. None with a transcript_error     -> NOT_READY
  6. None with no error               -> NOT_READY (transcription pending)
Anything else raises UnrecognisedTranscriptShape carrying the keys, never the
values (C1, C7). "Done" means rules 1-4 produced non-empty text; the recording
`state` field is deliberately ignored because its values are undocumented.
"""

from __future__ import annotations

from typing import Any, Mapping

from ratatoskr_demo.errors import TerminalError


class TranscriptNotReady:
    """Sentinel type: the transcript does not exist yet. Try again next poll."""

    _instance: "TranscriptNotReady | None" = None

    def __new__(cls) -> "TranscriptNotReady":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NOT_READY"


NOT_READY = TranscriptNotReady()


class UnrecognisedTranscriptShape(TerminalError):
    """The transcript is present but in a shape we do not understand."""


def extract_text(recording: Mapping[str, Any]) -> str | TranscriptNotReady:
    """Return the transcript as text, or NOT_READY. Raises on unknown shapes."""
    raw = recording.get("transcript")
    if raw is None:
        return NOT_READY  # rules 5 and 6 are the same outcome
    text = _text_from(raw)
    return text if text.strip() else NOT_READY


def _text_from(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Mapping):
        if isinstance(raw.get("text"), str):
            return raw["text"]
        if isinstance(raw.get("segments"), list):
            return _join_segments(raw["segments"])
        raise _unrecognised(raw)
    if isinstance(raw, list):
        return _join_segments(raw)
    raise UnrecognisedTranscriptShape(f"unrecognised transcript shape: type {type(raw).__name__}")


def _join_segments(segments: list[Any]) -> str:
    lines = []
    for segment in segments:
        if not isinstance(segment, Mapping) or not isinstance(segment.get("text"), str):
            raise _unrecognised(segment)
        speaker = segment.get("speaker")
        prefix = f"{speaker}: " if "speaker" in segment and speaker is not None else ""
        lines.append(prefix + segment["text"])
    return "\n".join(lines)


def _unrecognised(value: Any) -> UnrecognisedTranscriptShape:
    if isinstance(value, Mapping):
        keys = tuple(sorted(str(k) for k in value.keys()))
        return UnrecognisedTranscriptShape(
            f"unrecognised transcript shape; keys: {', '.join(keys) or '(none)'}", keys=keys
        )
    return UnrecognisedTranscriptShape(f"unrecognised transcript shape: segment of type {type(value).__name__}")
