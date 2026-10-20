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

"""The three kinds of failure Ratatoskr distinguishes.

Getting the kind right matters more than the message:

- TransientError: the network or a service hiccuped (timeout, DNS, no route,
  HTTP 429 or 5xx). The recording stays unprocessed and is retried on a later
  poll. It is NEVER written to the ledger as `error`.
- TerminalError: this one recording broke the data contract (unknown
  transcript shape, model output that still violates the schema after one
  retry). It is written to the ledger as `error` exactly once and never retried.
- ConfigurationError: something only a human can fix (bad key, bad config
  file, unknown route backend). The cycle stops and Dan is told; recordings are
  left untouched, because a wrong API key is not the recording's fault and
  must not burn every recording into `error`.

Messages on these exceptions never contain transcript text or model output —
only ids, JSON keys and HTTP status codes (C1).
"""

from __future__ import annotations

import httpx


class RatatoskrError(Exception):
    """Base class, so callers can tell our failures from bugs."""


class TransientError(RatatoskrError):
    """Retry later; leave the ledger alone."""

    def __init__(self, message: str, *, kind: str = "transient") -> None:
        super().__init__(message)
        self.kind = kind


class TerminalError(RatatoskrError):
    """This recording can never succeed as-is; mark it `error` once.

    `keys` carries the JSON keys that explain the failure — never values.
    """

    def __init__(self, message: str, *, keys: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.keys = tuple(keys)


class ConfigurationError(RatatoskrError):
    """Needs a human: stop, say so, touch nothing."""


def raise_for_http(response: httpx.Response, service: str) -> None:
    """Turn an HTTP error status into the right kind of failure.

    429 and 5xx are the service's problem and pass; 401/403 are ours (a wrong
    or revoked token) and need a human; any other 4xx means we sent something
    the service rejects for this item, which retrying will not fix.
    """
    status = response.status_code
    if status < 400:
        return
    if status == 429:
        raise TransientError(f"{service}: HTTP 429 rate limited", kind="rate-limit")
    if status >= 500:
        raise TransientError(f"{service}: HTTP {status}", kind="server-error")
    if status in (401, 403):
        raise ConfigurationError(f"{service}: HTTP {status} — check the token in .env")
    raise TerminalError(f"{service}: HTTP {status}")


def transport_error(exc: httpx.TransportError, service: str) -> TransientError:
    """Wrap a network-level failure (DNS, refused, timeout) as transient."""
    kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
    return TransientError(f"{service}: {kind} ({type(exc).__name__})", kind=kind)
