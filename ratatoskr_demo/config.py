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

"""Load config.toml and the secrets into one `Settings` object.

Config (non-secret) comes from a TOML file; secrets come from the git-ignored
`.env` file at the repo root, read here and nowhere else (C6). Its values win
over variables of the same name already in the process environment, so the
file Dan wrote is the one source of each key. Paths can be overridden by
environment variable so tests and dry runs use a temp dir:

    RATATOSKR_CONFIG      config file   (default config.toml)
    RATATOSKR_ENV_FILE    secrets file  (default .env)
    RATATOSKR_STATE_DIR   state dir     (default from [paths] state_dir)
    RATATOSKR_PROMPTS_DIR prompts dir   (default from [paths] prompts_dir)
    RATATOSKR_FIXTURES    when set, the Pocket client reads files, not the network

The .env file is never printed: a malformed line is reported by its line
number only, and `Secrets.__repr__` names which keys are present, never values.

Unknown sections or keys are an error rather than silently ignored: a typo in
`[route] backnd = "jev"` must not quietly run with defaults.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from ratatoskr_demo.devices.registry import INPUT_NAMES, OUTPUT_NAMES
from ratatoskr_demo.errors import ConfigurationError

DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_ENV_FILE = Path(".env")

KNOWN_ROUTE_BACKENDS = ("decider", "jev", "none")
KNOWN_MODEL_BACKENDS = ("bedrock",)  # "anthropic" was removed with the private repo's backend

# The only model this project may call. Anything else is a
# startup error, whatever the IAM policy would allow.
ALLOWED_MODELS = ("openai.gpt-oss-20b",)
SPEND_CEILING_USD = 5.00  # the hard stop may be lowered in config, never raised
REASONING_EFFORTS = ("", "low", "medium", "high")  # "" sends no reasoning_effort at all
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

PLACEHOLDER_MARK = "REPLACE_ME"


@dataclass(frozen=True)
class PollSettings:
    interval_seconds: int = 300
    lookback_days: int = 3
    backoff_max_seconds: int = 3600


@dataclass(frozen=True)
class StopTerm:
    """A stop-list entry: the name, and the ways a transcriber has been seen to spell it."""

    term: str
    spellings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TriageSettings:
    stop_list: tuple[StopTerm, ...] = (StopTerm("vargrheim", ("vargrime",)),)
    tag_keep: str = "keep"
    tag_hold: str = "hold"
    tag_ok: str = "ok"
    tag_urgent: str = "urgent"  # only while Strands Decider is unreachable: written unrouted, priority high (ruling 12)


@dataclass(frozen=True)
class GuardSettings:
    ngram_words: int = 8


@dataclass(frozen=True)
class TodoistSettings:
    inbox_project_name: str = "Ratatoskr Demo"
    label: str = "pocket"


@dataclass(frozen=True)
class ModelSettings:
    backend: str = "bedrock"
    model_id: str = "openai.gpt-oss-20b"
    base_url: str = "https://bedrock-mantle.us-east-1.api.aws/v1"
    max_tokens: int = 2000
    reasoning_effort: str = "low"
    docs_read_on: str = "2026-10-16"
    price_in_per_m: float = 0.07  # USD per 1M input tokens, us-east-1, Standard tier
    price_out_per_m: float = 0.30  # USD per 1M output tokens (reasoning tokens are billed as output)
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class SpendSettings:
    hard_stop_usd: float = SPEND_CEILING_USD


@dataclass(frozen=True)
class RouteSettings:
    backend: str = "decider"
    min_confidence: float = 0.7
    decider_url: str = "http://127.0.0.1:8000"
    decider_model: str = "StrandsAgents/strands-decider-2B-hobson-v21"
    decider_timeout_seconds: float = 180.0  # about 35 s per read on a 4-thread laptop CPU, 95 s seen
    source_window_words: int = 300  # the source-text gate's window, well inside Decider's 4,096 tokens
    # The project options with their one-line descriptions (route.toml). False: each option is its name
    # alone; for measuring the descriptions' effect only (Dan's ruling 15), never for the service.
    option_descriptions: bool = True


@dataclass(frozen=True)
class DevicesSettings:
    """Which adapters are on (Dan's ruling, 17 Oct 2026: the courier runs with no wearable at all)."""

    inputs: tuple[str, ...] = ("text",)  # any of "text", "pocket", "glasses"
    outputs: tuple[str, ...] = ("todoist", "telegram")


@dataclass(frozen=True)
class PathSettings:
    state_dir: Path = Path("ledger")
    prompts_dir: Path = Path("prompts")
    model_baseline: Path = Path("config/model_baseline.json")


@dataclass(frozen=True)
class Secrets:
    """Read from .env (or the given environment) only. `None` means not provided."""

    pocket_api_key: str | None = None
    todoist_api_token: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: int | None = None
    bedrock_api_key: str | None = None
    typesafe_api_key: str | None = None  # only the bench's Jev column needs it

    def __repr__(self) -> str:  # never print a secret, even in a traceback
        present = [f.name for f in fields(self) if getattr(self, f.name) is not None]
        return f"Secrets(present={present})"


@dataclass(frozen=True)
class Settings:
    poll: PollSettings = field(default_factory=PollSettings)
    triage: TriageSettings = field(default_factory=TriageSettings)
    guard: GuardSettings = field(default_factory=GuardSettings)
    todoist: TodoistSettings = field(default_factory=TodoistSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    spend: SpendSettings = field(default_factory=SpendSettings)
    route: RouteSettings = field(default_factory=RouteSettings)
    devices: DevicesSettings = field(default_factory=DevicesSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    secrets: Secrets = field(default_factory=Secrets)
    fixtures_dir: Path | None = None

    @property
    def ledger_path(self) -> Path:
        return self.paths.state_dir / "processed.json"

    @property
    def spend_path(self) -> Path:
        return self.paths.state_dir / "spend.json"


_SECTIONS: dict[str, type] = {
    "poll": PollSettings,
    "triage": TriageSettings,
    "guard": GuardSettings,
    "todoist": TodoistSettings,
    "model": ModelSettings,
    "spend": SpendSettings,
    "route": RouteSettings,
    "devices": DevicesSettings,
    "paths": PathSettings,
}

SECRET_NAMES = (
    "POCKET_API_KEY", "TODOIST_API_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "BEDROCK_API_KEY", "TYPESAFE_API_KEY",
)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read the config file named by RATATOSKR_CONFIG (or the default) plus the secrets.

    With no `env`, the process environment is used and the .env file is laid
    over it. Tests pass `env` explicitly and no file is read.
    """
    if env is None:
        env = runtime_environment()
    path = Path(env.get("RATATOSKR_CONFIG", str(DEFAULT_CONFIG_PATH)))
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"config file {path} is not valid TOML: {exc}") from None
    return settings_from_mapping(raw, env)


def runtime_environment() -> dict[str, str]:
    """os.environ with the .env file's values laid over it (the file wins)."""
    env = dict(os.environ)
    env.update(read_env_file(Path(env.get("RATATOSKR_ENV_FILE", str(DEFAULT_ENV_FILE)))))
    return env


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. A missing file is empty; errors give line numbers, never text."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        key, sep, value = stripped.partition("=")
        key = key.strip()
        if not sep or not key or not key.replace("_", "").isalnum():
            raise ConfigurationError(f"{path.name} line {number} is not KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def settings_from_mapping(raw: Mapping[str, Any], env: Mapping[str, str]) -> Settings:
    """Build and validate Settings from parsed TOML and an environment."""
    unknown_sections = set(raw) - set(_SECTIONS)
    if unknown_sections:
        raise ConfigurationError(f"unknown config section(s): {sorted(unknown_sections)}")

    built: dict[str, Any] = {}
    for name, cls in _SECTIONS.items():
        built[name] = _build_section(name, cls, raw.get(name, {}))

    paths = built["paths"]
    if env.get("RATATOSKR_STATE_DIR"):
        paths = PathSettings(state_dir=Path(env["RATATOSKR_STATE_DIR"]), prompts_dir=paths.prompts_dir,
                             model_baseline=paths.model_baseline)
    if env.get("RATATOSKR_PROMPTS_DIR"):
        paths = PathSettings(state_dir=paths.state_dir, prompts_dir=Path(env["RATATOSKR_PROMPTS_DIR"]),
                             model_baseline=paths.model_baseline)
    built["paths"] = paths

    fixtures = env.get("RATATOSKR_FIXTURES")
    settings = Settings(
        **built,
        secrets=_secrets_from_env(env),
        fixtures_dir=Path(fixtures) if fixtures else None,
    )
    _validate(settings)
    return settings


def _build_section(name: str, cls: type, values: Any) -> Any:
    if not isinstance(values, Mapping):
        raise ConfigurationError(f"[{name}] must be a table")
    allowed = {f.name for f in fields(cls)}
    unknown = set(values) - allowed
    if unknown:
        raise ConfigurationError(f"unknown key(s) in [{name}]: {sorted(unknown)}")
    defaults = cls()
    kwargs = {key: _coerce(f"{name}.{key}", value, getattr(defaults, key)) for key, value in values.items()}
    return cls(**kwargs)


def _coerce(key: str, value: Any, default: Any) -> Any:
    """Match the TOML value to the type of the default, or fail loud."""
    if key == "triage.stop_list":
        return _stop_list(value)
    if isinstance(default, Path):
        if not isinstance(value, str):
            raise ConfigurationError(f"{key} must be a path string")
        return Path(value)
    if isinstance(default, tuple):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigurationError(f"{key} must be a list of strings")
        return tuple(value)
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigurationError(f"{key} must be a number")
        return float(value)
    if isinstance(default, bool):  # before int: True is an int too
        if not isinstance(value, bool):
            raise ConfigurationError(f"{key} must be true or false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigurationError(f"{key} must be a whole number")
        return value
    if not isinstance(value, str):
        raise ConfigurationError(f"{key} must be a string")
    return value


def _stop_list(value: Any) -> tuple[StopTerm, ...]:
    """Each entry is a term, or a table {term = "...", spellings = ["...", ...]}."""
    shape = 'triage.stop_list entries must be "term" or {term = "...", spellings = ["...", ...]}'
    if not isinstance(value, list):
        raise ConfigurationError(shape)
    out = []
    for item in value:
        if isinstance(item, str):
            out.append(StopTerm(item))
            continue
        if not isinstance(item, Mapping) or set(item) - {"term", "spellings"} or not isinstance(item.get("term"), str):
            raise ConfigurationError(shape)
        spellings = item.get("spellings", [])
        if not isinstance(spellings, list) or not all(isinstance(s, str) for s in spellings):
            raise ConfigurationError(shape)
        out.append(StopTerm(item["term"], tuple(spellings)))
    return tuple(out)


def _secrets_from_env(env: Mapping[str, str]) -> Secrets:
    def get(name: str) -> str | None:
        value = env.get(name, "").strip()
        return value or None

    chat_id = get("TELEGRAM_CHAT_ID")
    chat_id_int: int | None = None
    if chat_id is not None and PLACEHOLDER_MARK not in chat_id:
        try:
            chat_id_int = int(chat_id)
        except ValueError:
            raise ConfigurationError("TELEGRAM_CHAT_ID must be a number") from None
    return Secrets(
        pocket_api_key=get("POCKET_API_KEY"),
        todoist_api_token=get("TODOIST_API_TOKEN"),
        telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=chat_id_int,
        bedrock_api_key=get("BEDROCK_API_KEY"),
        typesafe_api_key=get("TYPESAFE_API_KEY"),
    )


def _validate(s: Settings) -> None:
    if s.route.backend not in KNOWN_ROUTE_BACKENDS:
        raise ConfigurationError(
            f"[route] backend = {s.route.backend!r} is not a backend this version knows "
            f"(known: {', '.join(KNOWN_ROUTE_BACKENDS)})"
        )
    if not 0.0 < s.route.min_confidence <= 1.0:
        raise ConfigurationError("[route] min_confidence must be above 0 and at most 1")
    decider = urlsplit(s.route.decider_url)
    if decider.scheme != "http" or decider.hostname not in LOOPBACK_HOSTS:
        raise ConfigurationError("[route] decider_url must be http:// on this machine (127.0.0.1)")
    if s.model.backend not in KNOWN_MODEL_BACKENDS:
        raise ConfigurationError(f"[model] backend = {s.model.backend!r} is not known (known: bedrock)")
    if s.model.model_id not in ALLOWED_MODELS:
        raise ConfigurationError(f"[model] model_id must be one of {list(ALLOWED_MODELS)}")
    base = urlsplit(s.model.base_url)
    if base.scheme != "https" or not (base.hostname or "").startswith("bedrock-mantle.") \
            or not (base.hostname or "").endswith(".api.aws"):
        raise ConfigurationError("[model] base_url must be an https bedrock-mantle.<region>.api.aws URL")
    if not 1 <= s.model.max_tokens <= 16000:
        raise ConfigurationError("[model] max_tokens must be 1-16000")
    if s.model.reasoning_effort not in REASONING_EFFORTS:
        raise ConfigurationError('[model] reasoning_effort must be "", "low", "medium" or "high"')
    if s.model.price_in_per_m <= 0 or s.model.price_out_per_m <= 0:
        raise ConfigurationError("[model] prices must be above zero (the spend ledger depends on them)")
    if not 0.0 < s.spend.hard_stop_usd <= SPEND_CEILING_USD:
        raise ConfigurationError(f"[spend] hard_stop_usd must be above 0 and at most {SPEND_CEILING_USD:.2f}")
    inputs, outputs = s.devices.inputs, s.devices.outputs
    if not inputs or set(inputs) - set(INPUT_NAMES) or len(set(inputs)) != len(inputs):
        raise ConfigurationError(f"[devices] inputs must be one or more of {list(INPUT_NAMES)}, each once")
    if set(outputs) - set(OUTPUT_NAMES) or len(set(outputs)) != len(outputs):
        raise ConfigurationError(f"[devices] outputs must be any of {list(OUTPUT_NAMES)}, each once")
    if "text" in inputs and "telegram" not in outputs:
        raise ConfigurationError('[devices] the "text" input is the Telegram /demo command: it needs the "telegram" output')
    if not 50 <= s.route.source_window_words <= 1500:
        raise ConfigurationError("[route] source_window_words must be 50-1500 (Decider reads 4,096 tokens)")
    if s.guard.ngram_words < 2:
        raise ConfigurationError("[guard] ngram_words must be at least 2")
    if s.poll.interval_seconds < 30:
        raise ConfigurationError("[poll] interval_seconds must be at least 30")
    if s.poll.lookback_days < 0:
        raise ConfigurationError("[poll] lookback_days cannot be negative")
    if not s.triage.stop_list or any(not word.strip() for entry in s.triage.stop_list
                                     for word in (entry.term, *entry.spellings)):
        raise ConfigurationError("[triage] stop_list must be a non-empty list of non-empty terms and spellings")


def needed_secrets(settings: Settings, *, need_model: bool = True, bench: bool = False) -> dict[str, tuple[str, object]]:
    """Each secret the enabled adapters need: name -> (who needs it, its value). Disabled adapters need nothing."""
    s, inputs, outputs = settings.secrets, settings.devices.inputs, settings.devices.outputs
    needed: dict[str, tuple[str, object]] = {}
    if need_model:
        needed["BEDROCK_API_KEY"] = ("the extraction model", s.bedrock_api_key)
    if "telegram" in outputs or "text" in inputs:
        needed["TELEGRAM_BOT_TOKEN"] = ("the telegram output", s.telegram_bot_token)
        needed["TELEGRAM_CHAT_ID"] = ("the telegram output", s.telegram_chat_id)
    if "todoist" in outputs:
        needed["TODOIST_API_TOKEN"] = ("the todoist output", s.todoist_api_token)
    if "pocket" in inputs:
        needed["POCKET_API_KEY"] = ("the pocket input", s.pocket_api_key if settings.fixtures_dir is None else "fixtures")
    if settings.route.backend == "jev" or bench:
        needed["TYPESAFE_API_KEY"] = ("the jev route backend" if not bench else "the bench's Jev column",
                                      s.typesafe_api_key)
    return needed


def require_secrets(settings: Settings, *, need_model: bool = True) -> None:
    """Fail loud before running if a secret an enabled adapter needs is missing or still a placeholder (C7)."""
    missing = [f"{name} (for {who})" for name, (who, value) in needed_secrets(settings, need_model=need_model).items()
               if value is None or PLACEHOLDER_MARK in str(value)]
    if missing:
        raise ConfigurationError(f"missing secret(s) in .env: {'; '.join(missing)}")
