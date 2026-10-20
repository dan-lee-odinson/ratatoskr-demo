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

"""Amazon Bedrock, reached through bedrock-mantle's OpenAI-compatible API.

This module is the only code that talks to Bedrock, and it talks only to
`[model] model_id` (config.py allows exactly `openai.gpt-oss-20b`):

    GET  {base_url}/models            the model record check (no inference, no cost)
    POST {base_url}/chat/completions  every inference call

The key (BEDROCK_API_KEY) is sent only in the Authorization header. Nothing
here logs a header, a request body or a response body.

Safeguards, all failing closed:

- Spend (spend.py). Every chat call reserves its worst case before it is sent
  and is refused past the hard stop; the reservation is settled with the
  reported usage, kept at worst case if no answer came back, or released if
  the network was lost before anything was sent.
- Model identity. Every response's `model` must name the model requested;
  otherwise ModelIdentityError (a configuration error: stop).
- Model record. Before the first chat call, the entry for the model in
  GET /models must equal config/model_baseline.json; otherwise stop. Lists of
  plain values are compared as sets: mantle returns `data_retention.
  allowed_modes` in a different order on every call (seen in the smoke test,
  16 Oct 2026), so order alone is not a difference. Every value still is.

Failures are sorted for C7 and for the spend ledger:

    lost network before the request went out: DNS failure,  -> NetworkLost (transient),
        no route, refused or reset while connecting, connect    reservation released
        timeout, a pool timeout, a failed write
    the request went out and the connection then dropped    -> TransientError, worst case
        (reset while reading, server disconnected), or a
        timeout while waiting for the answer
    HTTP 429, 5xx                                           -> TransientError, worst case
    HTTP 401 / 403                                          -> ConfigurationError, worst case
    any other 4xx (our request shape or model id)           -> ConfigurationError, worst case
    an answer naming another model                          -> ModelIdentityError, settled

A connection reset after the request was written counts as sent (a call that
was sent and never answered stays at worst case), so only failures before the
request could reach Bedrock are released. An error response carries no usage,
so it stays at worst case too: the ledger can over-count, never under-count.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from ratatoskr_demo.config import Settings
from ratatoskr_demo.errors import ConfigurationError, TransientError
from ratatoskr_demo.spend import SpendLedger, input_token_bound, nano_to_usd

log = logging.getLogger(__name__)

NETWORK_RECHECK_SECONDS = 60.0  # "wait about a minute, recheck"


class NetworkLost(TransientError):
    """The request could not have reached Bedrock. Nothing is counted; retry the unit of work."""

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="network")


class ModelIdentityError(ConfigurationError):
    """A response named a different model than the one requested."""


class ModelRecordChanged(ConfigurationError):
    """The model's entry in GET /models no longer matches the saved baseline."""


# The order matters: ConnectTimeout is a TimeoutException, not a ConnectError.
_LOST_NETWORK = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.WriteError)
_SENT_UNANSWERED = (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.RemoteProtocolError)


@dataclass(frozen=True)
class ChatResult:
    model: str
    content: str | None
    tool_arguments: str | None  # the first tool call's arguments, when the model called a tool
    finish_reason: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float


class BedrockClient:
    def __init__(self, settings: Settings, spend: SpendLedger, *, http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        m = settings.model
        self.model_id = m.model_id
        self.base_url = m.base_url.rstrip("/")
        self.max_tokens = m.max_tokens
        self.reasoning_effort = m.reasoning_effort
        self.baseline_path = settings.paths.model_baseline
        self.spend = spend
        self._key = settings.secrets.bedrock_api_key
        self._http = http or httpx.Client(timeout=httpx.Timeout(m.timeout_seconds, connect=10.0))
        self._sleep = sleep
        self._record_checked = False

    # --- the model record ---

    def list_models(self) -> list[dict[str, Any]]:
        body = self._get("/models")
        data = body.get("data")
        if not isinstance(data, list):
            raise ConfigurationError(f"bedrock: GET /models has no data list (keys: {sorted(body)})")
        return [entry for entry in data if isinstance(entry, dict)]

    def model_record(self) -> dict[str, Any]:
        for entry in self.list_models():
            if entry.get("id") == self.model_id:
                return entry
        raise ModelRecordChanged(f"bedrock: {self.model_id} is not listed by GET /models")

    def check_model_record(self) -> None:
        """Compare the live record with config/model_baseline.json; stop on any difference."""
        baseline = canonical(load_baseline(self.baseline_path))
        live = canonical(self.model_record())
        if live != baseline:
            changed = sorted(k for k in set(live) | set(baseline) if live.get(k) != baseline.get(k))
            raise ModelRecordChanged(f"bedrock: the record for {self.model_id} changed (fields: {', '.join(changed)})")
        self._record_checked = True
        log.info("bedrock: model record for %s matches the baseline", self.model_id)

    # --- inference ---

    def chat(self, messages: list[dict[str, Any]], *, purpose: str, response_format: dict | None = None,
             tools: list[dict] | None = None, tool_choice: Any = None, max_tokens: int | None = None) -> ChatResult:
        """One Chat Completions call, through the spend ledger. Raises the kinds listed above."""
        if not self._record_checked:
            self.check_model_record()
        limit = max_tokens or self.max_tokens
        body: dict[str, Any] = {"model": self.model_id, "messages": messages, "max_tokens": limit}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if response_format is not None:
            body["response_format"] = response_format
        if tools is not None:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice

        reservation = self.spend.reserve(purpose=purpose, model=self.model_id,
                                         input_tokens_bound=input_token_bound(body), max_tokens=limit)
        try:
            response = self._http.post(f"{self.base_url}/chat/completions", json=body, headers=self._headers())
        except _LOST_NETWORK as exc:
            self.spend.release(reservation)
            raise NetworkLost(f"bedrock: network lost ({type(exc).__name__})") from None
        except _SENT_UNANSWERED as exc:
            self.spend.unanswered(reservation, type(exc).__name__)
            raise TransientError(f"bedrock: sent, no answer ({type(exc).__name__})", kind="timeout") from None
        except httpx.TransportError as exc:  # anything else at the transport level: count it, retry later
            self.spend.unanswered(reservation, type(exc).__name__)
            raise TransientError(f"bedrock: transport error ({type(exc).__name__})", kind="network") from None

        if response.status_code >= 400:
            self.spend.unanswered(reservation, f"HTTP {response.status_code}")
            raise _http_error(response)
        try:
            data = response.json()
            usage = data["usage"]
            input_tokens, output_tokens = int(usage["prompt_tokens"]), int(usage["completion_tokens"])
        except (ValueError, KeyError, TypeError):
            self.spend.unanswered(reservation, "unreadable body")
            raise ConfigurationError("bedrock: a response without readable usage") from None
        cost = self.spend.settle(reservation, input_tokens=input_tokens, output_tokens=output_tokens)
        if data.get("model") != self.model_id:
            log.error("bedrock: asked for %s, the response named %r; stopping", self.model_id, data.get("model"))
            raise ModelIdentityError(
                f"bedrock: asked for {self.model_id} but the response named {data.get('model')!r}")
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        arguments = None
        if tool_calls and isinstance(tool_calls[0], dict):
            arguments = (tool_calls[0].get("function") or {}).get("arguments")
        log.info("bedrock: %s, %d input / %d output tokens, $%.6f (%s)",
                 self.model_id, input_tokens, output_tokens, nano_to_usd(cost), purpose)
        return ChatResult(model=data["model"], content=message.get("content"), tool_arguments=arguments,
                          finish_reason=choice.get("finish_reason"), input_tokens=input_tokens,
                          output_tokens=output_tokens, cost_usd=nano_to_usd(cost))

    def wait_for_network(self, *, attempts: int = 30) -> None:
        """After NetworkLost: wait about a minute, recheck, and repeat until the host answers."""
        host = urlsplit(self.base_url).hostname or ""
        for _ in range(attempts):
            self._sleep(NETWORK_RECHECK_SECONDS)
            try:
                with socket.create_connection((host, 443), timeout=10):
                    return
            except OSError:
                log.warning("bedrock: network still down; waiting another minute")
        raise TransientError("bedrock: the network did not come back", kind="network")

    # --- plumbing ---

    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise ConfigurationError("BEDROCK_API_KEY is missing from .env")
        return {"Authorization": f"Bearer {self._key}"}

    def _get(self, path: str) -> dict[str, Any]:
        try:
            response = self._http.get(f"{self.base_url}{path}", headers=self._headers())
        except _LOST_NETWORK as exc:
            raise NetworkLost(f"bedrock: network lost ({type(exc).__name__})") from None
        except httpx.TransportError as exc:
            raise TransientError(f"bedrock: transport error ({type(exc).__name__})", kind="network") from None
        if response.status_code >= 400:
            raise _http_error(response)
        try:
            body = response.json()
        except ValueError:
            raise ConfigurationError(f"bedrock: GET {path} returned a non-JSON body") from None
        if not isinstance(body, dict):
            raise ConfigurationError(f"bedrock: GET {path} returned a non-object body")
        return body


def _http_error(response: httpx.Response) -> Exception:
    status = response.status_code
    if status == 429:
        return TransientError("bedrock: HTTP 429 throttled", kind="rate-limit")
    if status >= 500:
        return TransientError(f"bedrock: HTTP {status}", kind="server-error")
    if status in (401, 403):
        return ConfigurationError(f"bedrock: HTTP {status}; check BEDROCK_API_KEY and the IAM policy")
    return ConfigurationError(f"bedrock: HTTP {status}; check [model] model_id and the request shape")


def canonical(value: Any) -> Any:
    """The record with every list of plain values sorted, so only order is ignored."""
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        items = [canonical(item) for item in value]
        if all(isinstance(item, (str, int, float, bool)) or item is None for item in items):
            return sorted(items, key=lambda item: (type(item).__name__, str(item)))
        return items
    return value


def load_baseline(path: Path) -> dict[str, Any]:
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"model baseline {path} is missing; refusing to call Bedrock") from None
    except ValueError:
        raise ConfigurationError(f"model baseline {path} is not valid JSON") from None
    if not isinstance(baseline, dict):
        raise ConfigurationError(f"model baseline {path} must be one JSON object")
    return baseline


def make_spend_ledger(settings: Settings) -> SpendLedger:
    return SpendLedger(settings.spend_path, hard_stop_usd=settings.spend.hard_stop_usd,
                       price_in_per_m=settings.model.price_in_per_m, price_out_per_m=settings.model.price_out_per_m)
