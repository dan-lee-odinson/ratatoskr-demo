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

"""The Bedrock spend ledger and its hard stop. Fails closed.

Every Bedrock call goes through three steps here:

    reserve()  BEFORE the call: add the call's worst case (an upper bound on
               its input tokens, plus max_tokens of output, at the configured
               prices) to the running total. If the total would then exceed
               the hard stop, refuse: record the refusal, raise SpendStop, and
               send nothing.
    settle()   after an answer: replace the worst case with the usage the
               response reported.
    release()  when the network was lost before the request could have
               reached Bedrock (DNS, no route, refused or reset, connect
               timeout): remove the reservation. Nothing was sent, so nothing
               is counted.

Anything else leaves the reservation at worst case: a timeout while waiting
for the answer (the request went out), an error response without usage, or a
crash between reserve() and settle(). The total can only over-count.

Money is kept in whole nanodollars (1e-9 USD), rounded UP from the
per-million prices, so the boundary is exact: a call that brings the total to
exactly the hard stop is allowed, one nanodollar more is refused.

The ledger is one JSON file (ledger/spend.json, git-ignored), written
atomically, and re-read under a lock file before every change, because the
courier and the bench are separate processes drawing from the same $5. It
holds counts, prices and purposes, never request or response text (C1).
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any, Callable, Iterator

from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.ledger import atomic_write_json

log = logging.getLogger(__name__)

NANO = 1_000_000_000  # nanodollars per dollar
LOCK_WAIT_SECONDS = 30.0
LOCK_STALE_SECONDS = 120.0
TEMPLATE_OVERHEAD_TOKENS = 256  # chat template and role markers, on top of the request bytes


class SpendStop(ConfigurationError):
    """The hard stop: this call could take the total past the limit. Nothing was sent."""


def usd_to_nano(usd: float | str | Decimal) -> int:
    return int((Decimal(str(usd)) * NANO).to_integral_value(rounding=ROUND_CEILING))


def nano_to_usd(nano: int) -> float:
    return nano / NANO


def cost_nano(input_tokens: int, output_tokens: int, price_in_per_m: float, price_out_per_m: float) -> int:
    """Cost of a call in nanodollars, rounded up. Prices are USD per 1M tokens."""
    exact = (Decimal(input_tokens) * Decimal(str(price_in_per_m))
             + Decimal(output_tokens) * Decimal(str(price_out_per_m))) * NANO / Decimal(1_000_000)
    return int(exact.to_integral_value(rounding=ROUND_CEILING))


def input_token_bound(payload: Any) -> int:
    """An upper bound on a request's input tokens: its UTF-8 bytes plus the chat template.

    gpt-oss uses a byte-level BPE tokenizer, so no token covers less than one
    byte: the byte count can never be smaller than the token count.
    """
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return len(text.encode("utf-8")) + TEMPLATE_OVERHEAD_TOKENS


@dataclass(frozen=True)
class Reservation:
    number: int
    worst_nano: int


class SpendLedger:
    def __init__(self, path: Path, *, hard_stop_usd: float, price_in_per_m: float, price_out_per_m: float,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.hard_stop_nano = usd_to_nano(hard_stop_usd)
        self.price_in = price_in_per_m
        self.price_out = price_out_per_m
        self._clock = clock

    # --- the three steps ---

    def reserve(self, *, purpose: str, model: str, input_tokens_bound: int, max_tokens: int) -> Reservation:
        worst = cost_nano(input_tokens_bound, max_tokens, self.price_in, self.price_out)
        with self._locked() as data:
            total = _total(data)
            refused = total + worst > self.hard_stop_nano
            if refused:  # recorded under the lock, raised after the write
                data["refused"].append({"at": self._now(), "purpose": purpose, "worst_usd": nano_to_usd(worst),
                                        "total_usd": nano_to_usd(total)})
            else:
                number = max((c["n"] for c in data["calls"]), default=0) + 1
                data["calls"].append({
                    "n": number, "at": self._now(), "purpose": purpose, "model": model, "status": "reserved",
                    "worst_nano": worst, "nano": worst, "input_tokens": None, "output_tokens": None,
                })
        if refused:
            log.error("spend: hard stop; refused a %s call (worst case $%.6f; total $%.6f of $%.4f)",
                      purpose, nano_to_usd(worst), nano_to_usd(total), nano_to_usd(self.hard_stop_nano))
            raise SpendStop(
                f"Bedrock spend hard stop: total ${nano_to_usd(total):.4f}, this call could cost up to "
                f"${nano_to_usd(worst):.6f}, limit ${nano_to_usd(self.hard_stop_nano):.2f}; nothing was sent"
            )
        return Reservation(number=number, worst_nano=worst)

    def settle(self, reservation: Reservation, *, input_tokens: int, output_tokens: int) -> int:
        """Replace the worst case with the reported usage. Returns the call's cost in nanodollars."""
        actual = cost_nano(input_tokens, output_tokens, self.price_in, self.price_out)
        with self._locked() as data:
            call = _find(data, reservation)
            call.update(status="settled", nano=actual, input_tokens=input_tokens, output_tokens=output_tokens)
        return actual

    def unanswered(self, reservation: Reservation, why: str) -> None:
        """The request went out and no usable answer came back: it stays at worst case."""
        with self._locked() as data:
            _find(data, reservation).update(status="unanswered", why=why)

    def release(self, reservation: Reservation) -> None:
        """The network was lost before the request went out: nothing is counted."""
        with self._locked() as data:
            data["calls"] = [c for c in data["calls"] if c["n"] != reservation.number]

    # --- reading ---

    def total_nano(self) -> int:
        return _total(self._read())

    def total_usd(self) -> float:
        return nano_to_usd(self.total_nano())

    def summary(self) -> dict[str, Any]:
        data = self._read()
        calls = data["calls"]
        return {
            "total_usd": nano_to_usd(_total(data)),
            "hard_stop_usd": nano_to_usd(self.hard_stop_nano),
            "calls": len(calls),
            "settled": sum(1 for c in calls if c["status"] == "settled"),
            "at_worst_case": sum(1 for c in calls if c["status"] != "settled"),
            "refused": len(data["refused"]),
            "input_tokens": sum(c["input_tokens"] or 0 for c in calls),
            "output_tokens": sum(c["output_tokens"] or 0 for c in calls),
            "by_purpose": _by_purpose(calls),
        }

    # --- the file ---

    def _now(self) -> str:
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat(timespec="seconds")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"calls": [], "refused": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data.get("calls"), list) or not isinstance(data.get("refused"), list):
                raise ValueError("missing calls/refused")
            return data
        except (ValueError, AttributeError):
            # Refuse rather than start from zero: an unreadable ledger must never reset the total.
            raise ConfigurationError(f"spend ledger {self.path} is unreadable; refusing every Bedrock call") from None

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        lock = self.path.with_name(self.path.name + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        _acquire(lock)
        try:
            data = self._read()
            yield data
            data["total_usd"] = nano_to_usd(_total(data))
            data["hard_stop_usd"] = nano_to_usd(self.hard_stop_nano)
            atomic_write_json(self.path, data)
        finally:
            lock.unlink(missing_ok=True)


def _total(data: dict[str, Any]) -> int:
    return sum(int(c["nano"]) for c in data["calls"])


def _find(data: dict[str, Any], reservation: Reservation) -> dict[str, Any]:
    for call in data["calls"]:
        if call["n"] == reservation.number:
            return call
    raise ConfigurationError(f"spend ledger lost reservation {reservation.number}; refusing to continue")


def _by_purpose(calls: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, int] = {}
    for call in calls:
        out[call["purpose"]] = out.get(call["purpose"], 0) + int(call["nano"])
    return {purpose: nano_to_usd(nano) for purpose, nano in sorted(out.items())}


def _acquire(lock: Path) -> None:
    """A lock file shared by every process that writes the ledger (courier and bench)."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock.unlink(missing_ok=True)  # left by a crashed process
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise ConfigurationError(f"spend ledger lock {lock} is held; refusing the call") from None
            time.sleep(0.05)


def worst_case_usd(input_tokens_bound: int, max_tokens: int, price_in_per_m: float, price_out_per_m: float) -> float:
    """For estimates shown before a run (the bench)."""
    return nano_to_usd(cost_nano(input_tokens_bound, max_tokens, price_in_per_m, price_out_per_m))

