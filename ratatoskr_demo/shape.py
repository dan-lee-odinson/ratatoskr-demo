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

"""Describe a JSON value by key names and types only: no values, no text, no lengths.

`ratatoskr-demo --inspect-shape <recording_id>` prints this for one real
Pocket recording, so the populated shape of `transcript` can be confirmed on
real data without any of its content reaching the screen. For a list, the
keys and types of all its elements are merged, so one line describes every
element.
"""

from __future__ import annotations

from typing import Any

MAX_DEPTH = 8


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        inner = sorted({type_name(item) for item in value})
        return f"list[{' | '.join(inner)}]" if inner else "list[empty]"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def describe(value: Any, name: str = "(root)", depth: int = 0) -> list[str]:
    """Lines of `name: type`, indented by nesting. Never a value."""
    pad = "  " * depth
    lines = [f"{pad}{name}: {type_name(value)}"]
    if depth >= MAX_DEPTH:
        return lines
    if isinstance(value, dict):
        for key in sorted(value, key=str):
            lines.extend(describe(value[key], str(key), depth + 1))
    elif isinstance(value, list):
        objects = [item for item in value if isinstance(item, dict)]
        if objects:
            merged: dict[str, list[Any]] = {}
            for item in objects:
                for key, inner in item.items():
                    merged.setdefault(str(key), []).append(inner)
            for key in sorted(merged):
                types = sorted({type_name(inner) for inner in merged[key]})
                optional = "" if len(merged[key]) == len(objects) else " (in some elements)"
                lines.append(f"{pad}  [].{key}: {' | '.join(types)}{optional}")
                nested = [inner for inner in merged[key] if isinstance(inner, (dict, list))]
                if nested:
                    lines.extend(describe(_merge(nested), f"[].{key}", depth + 2)[1:])
    return lines


def _merge(values: list[Any]) -> Any:
    """Merge several dicts (or lists) into one representative value for describe()."""
    if all(isinstance(v, dict) for v in values):
        out: dict[str, Any] = {}
        for value in values:
            for key, inner in value.items():
                out.setdefault(key, inner)
        return out
    flat: list[Any] = []
    for value in values:
        flat.extend(value if isinstance(value, list) else [value])
    return flat
