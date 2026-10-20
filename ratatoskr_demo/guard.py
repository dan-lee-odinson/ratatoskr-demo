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

"""C9 outbound leak guard. Pure functions, no I/O.

The extraction prompt says "Do not quote the transcript" — but a prompt is an
instruction, not a guarantee. This module is the guarantee: before any model
output leaves the process, it is compared with the transcript, and output that
repeats any run of `n` consecutive transcript words (default 8) is rejected.

Both texts are normalised first so trivial differences cannot hide a copy:
lower-cased, apostrophes dropped ("you're" -> "youre"), every other punctuation
mark turned into a space ("aldrin-micro" -> "aldrin micro"), whitespace
collapsed. The same normalisation runs on both sides, so it can only make
matches easier to find, never harder.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable

_APOSTROPHES = {"'", "’", "‘", "ʼ"}


def normalise(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    out = []
    for ch in text.lower():
        if ch in _APOSTROPHES:
            continue
        out.append(" " if unicodedata.category(ch).startswith("P") else ch)
    return " ".join("".join(out).split())


def words(text: str) -> list[str]:
    return normalise(text).split()


def overlap(transcript: str, output: str, n: int) -> bool:
    """True if any n consecutive transcript words appear, in order, in output."""
    if n < 1:
        raise ValueError("n must be at least 1")
    out_words = words(output)
    if len(out_words) < n:
        return False
    source = words(transcript)
    spans = {tuple(source[i : i + n]) for i in range(len(source) - n + 1)}
    if not spans:
        return False
    return any(tuple(out_words[i : i + n]) in spans for i in range(len(out_words) - n + 1))


def any_overlap(transcript: str, outputs: Iterable[str | None], n: int) -> bool:
    """True if any one of several output strings leaks a span.

    Each string is checked on its own, so two fields placed side by side
    cannot manufacture a span that neither contains.
    """
    return any(overlap(transcript, text, n) for text in outputs if text)
