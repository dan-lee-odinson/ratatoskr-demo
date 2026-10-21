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

"""The device envelope: the one record every input and output passes through.

    Envelope(device_id, kind, ts, payload)

`kind` names the device class: "recorder" (Pocket), "watch" (the wrist),
"glasses" (a stub slot), "text" (the /demo command). `payload` is one of a
tagged union:

    TranscriptRef(recording_id)      a finished recording; the transcript is fetched
                                     by the cycle at extraction time, never stored here
    Text(text)                       typed text, treated exactly as a transcript
    Button(name)                     a press on a device
    TagRequest(recording_id, tag)    a request to tag a recording (for example `ok`)
    AudioRef(uri) / VideoRef(uri)    media held elsewhere, by reference

Validation is hand-rolled and runs on construction, so an invalid envelope
cannot exist. The same rules are published as JSON Schema in
envelope.schema.json, and the tests check every adapter's output against it.

C1: an envelope lives in memory only. `Text.__repr__` never shows the text, so
an envelope that reaches a log line or a traceback carries no transcript.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from typing import Any, Literal, Union

KINDS = ("recorder", "watch", "glasses", "text")
Kind = Literal["recorder", "watch", "glasses", "text"]

DEVICE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
URI = re.compile(r"^[a-z][a-z0-9+.-]*:\S+$")
TEXT_MAX = 100_000


class EnvelopeError(ValueError):
    """An envelope or payload that breaks the schema. Messages name fields, never values."""


@dataclass(frozen=True)
class TranscriptRef:
    recording_id: str
    type: Literal["transcript_ref"] = field(default="transcript_ref", init=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.recording_id, str) and NAME.match(self.recording_id), "recording_id")


@dataclass(frozen=True)
class Text:
    text: str = field(repr=False)
    type: Literal["text"] = field(default="text", init=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.text, str) and self.text.strip() and len(self.text) <= TEXT_MAX, "text")

    def __repr__(self) -> str:  # C1: never the text itself
        return f"Text(<{len(self.text)} characters>)"


@dataclass(frozen=True)
class Button:
    name: str
    type: Literal["button"] = field(default="button", init=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.name, str) and NAME.match(self.name), "name")


@dataclass(frozen=True)
class TagRequest:
    recording_id: str
    tag: str
    type: Literal["tag_request"] = field(default="tag_request", init=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.recording_id, str) and NAME.match(self.recording_id), "recording_id")
        _require(isinstance(self.tag, str) and NAME.match(self.tag), "tag")


@dataclass(frozen=True)
class AudioRef:
    uri: str
    type: Literal["audio_ref"] = field(default="audio_ref", init=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.uri, str) and URI.match(self.uri), "uri")


@dataclass(frozen=True)
class VideoRef:
    uri: str
    type: Literal["video_ref"] = field(default="video_ref", init=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.uri, str) and URI.match(self.uri), "uri")


@dataclass(frozen=True)
class ImageRef:
    """A photo in the watched folder (ruling 47): where it is, never its bytes."""

    uri: str
    type: Literal["image_ref"] = field(default="image_ref", init=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.uri, str) and URI.match(self.uri), "uri")


Payload = Union[TranscriptRef, Text, Button, TagRequest, AudioRef, VideoRef, ImageRef]
PAYLOAD_TYPES: dict[str, type] = {
    "transcript_ref": TranscriptRef, "text": Text, "button": Button,
    "tag_request": TagRequest, "audio_ref": AudioRef, "video_ref": VideoRef, "image_ref": ImageRef,
}


@dataclass(frozen=True)
class Envelope:
    device_id: str
    kind: Kind
    ts: datetime
    payload: Payload

    def __post_init__(self) -> None:
        _require(isinstance(self.device_id, str) and DEVICE_ID.match(self.device_id), "device_id")
        _require(self.kind in KINDS, "kind")
        _require(isinstance(self.ts, datetime) and self.ts.tzinfo is not None, "ts")
        _require(isinstance(self.payload, tuple(PAYLOAD_TYPES.values())), "payload")

    def to_json(self) -> dict[str, Any]:
        """The envelope as JSON, in the shape envelope.schema.json describes. Memory only (C1)."""
        payload = {name: getattr(self.payload, name) for name in self.payload.__dataclass_fields__}
        return {"device_id": self.device_id, "kind": self.kind, "ts": self.ts.isoformat(), "payload": payload}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Envelope":
        _require(isinstance(data, dict) and set(data) == {"device_id", "kind", "ts", "payload"}, "(root)")
        raw = data["payload"]
        _require(isinstance(raw, dict) and raw.get("type") in PAYLOAD_TYPES, "payload.type")
        payload_cls = PAYLOAD_TYPES[raw["type"]]
        fields = {k: v for k, v in raw.items() if k != "type"}
        expected = {name for name, f in payload_cls.__dataclass_fields__.items() if f.init}
        _require(set(fields) == expected, "payload")
        try:
            ts = datetime.fromisoformat(data["ts"])
        except (TypeError, ValueError):
            raise EnvelopeError("envelope field ts is invalid") from None
        return cls(device_id=data["device_id"], kind=data["kind"], ts=ts, payload=payload_cls(**fields))


def schema() -> dict[str, Any]:
    """The published JSON Schema (devices/envelope.schema.json)."""
    text = resources.files("ratatoskr_demo").joinpath("devices", "envelope.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


def _require(ok: Any, where: str) -> None:
    if not ok:
        raise EnvelopeError(f"envelope field {where} is invalid")
