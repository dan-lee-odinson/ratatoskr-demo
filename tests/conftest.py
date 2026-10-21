"""Shared test helpers: settings in a temp dir, fake services, a fake model.

Nothing here touches the network, and two autouse guards make sure of it:

- the config module is pointed at a .env file that does not exist, so no
  test can load the real keys from the repo root;
- every socket connection and DNS lookup to anything but this machine raises,
  so a request that escaped the mocks fails instead of leaving the laptop.

Pocket is read from fixture files (or mocked with respx), Todoist and
Telegram are respx-mocked with small fakes that behave the way their
documentation says, Bedrock is respx-mocked, and the model is a function.
"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from ratatoskr_demo.cli import Runtime, build_runtime
from ratatoskr_demo.config import Settings, settings_from_mapping
from ratatoskr_demo.todoist import SYNC_URL

FIXTURES = Path(__file__).parent / "fixtures"
SENTINEL = "ZQX-SENTINEL-7731"
TELEGRAM_SEND = "https://api.telegram.org/botTEST-TOKEN/sendMessage"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def hermetic(tmp_path_factory, monkeypatch):
    """No real .env, no real network: the two guarantees every test runs under."""
    monkeypatch.setenv("RATATOSKR_ENV_FILE", str(tmp_path_factory.getbasetemp() / "no-such.env"))
    for name in ("POCKET_API_KEY", "TODOIST_API_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                 "BEDROCK_API_KEY", "TYPESAFE_API_KEY", "RATATOSKR_CONFIG", "RATATOSKR_FIXTURES"):
        monkeypatch.delenv(name, raising=False)
    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK:
            raise OSError(f"test guard: no network connection to {host}")
        return real_connect(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if host not in LOOPBACK and host is not None:
            raise socket.gaierror(f"test guard: no DNS lookup for {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


class FakeClock:
    def __init__(self, start: float = 1_790_000_000.0) -> None:  # mid-September 2026
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTodoist:
    """A Todoist /sync endpoint that honours command uuids, as the docs promise."""

    def __init__(self) -> None:
        self.projects: dict[str, str] = {}
        self.tasks: dict[str, dict[str, Any]] = {}  # task id -> args
        self.executed: set[str] = set()
        self.payloads: list[dict[str, Any]] = []  # every form body received
        self.fail: Callable[[dict[str, Any]], Exception | httpx.Response | None] = lambda command: None
        self._next = 1

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.payloads.append(form)
        if "commands" not in form:
            projects = [{"id": pid, "name": name, "is_deleted": False, "is_archived": False}
                        for name, pid in self.projects.items()]
            return httpx.Response(200, json={"projects": projects, "sync_token": "t"})
        status, mapping = {}, {}
        for command in json.loads(form["commands"]):
            injected = self.fail(command)
            if isinstance(injected, Exception):
                raise injected
            if isinstance(injected, httpx.Response):
                return injected
            if command["uuid"] not in self.executed:
                self.executed.add(command["uuid"])
                new_id = f"id{self._next}"
                self._next += 1
                if command["type"] == "item_update":  # ruling 43: an existing task's fields change, in place
                    self.tasks[command["args"]["id"]].update({k: v for k, v in command["args"].items() if k != "id"})
                    self._next -= 1
                    status[command["uuid"]] = "ok"
                    continue
                if command["type"] == "project_add":
                    self.projects[command["args"]["name"]] = new_id
                else:
                    self.tasks[new_id] = command["args"]
                mapping[command["temp_id"]] = new_id
            status[command["uuid"]] = "ok"
        return httpx.Response(200, json={"sync_status": status, "temp_id_mapping": mapping})


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.fail: Callable[[dict[str, Any]], Exception | httpx.Response | None] = lambda payload: None

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        injected = self.fail(payload)
        if isinstance(injected, Exception):
            raise injected
        if isinstance(injected, httpx.Response):
            return injected
        self.messages.append(payload)
        return httpx.Response(200, json={"ok": True, "result": {}})

    @property
    def texts(self) -> list[str]:
        return [m["text"] for m in self.messages]


def extraction_json(contact: dict | None = None, tasks: list[dict] | None = None, confidence: str = "high") -> str:
    return json.dumps({
        "contact": contact or {"name": "Priya Venkataraman", "organisation": "Aldrin Microsystems",
                               "role": "thermal lead", "context": "Met at a space industry meetup."},
        "tasks": tasks if tasks is not None else [],
        "confidence": confidence,
    })


REC_001_TASKS = [
    {"content": "Email Priya the orbital thermal bounds preprint", "due_string": "Friday", "notes": None},
    {"content": "Check the Aldrin Microsystems careers page for a junior ops role", "due_string": "March",
     "notes": "Spring hiring."},
    {"content": "Look up: Aldrin Microsystems vacuum chamber", "due_string": None, "notes": None},
]


class ScriptedModel:
    """A fake ModelCall. Picks a reply by what the transcript mentions."""

    def __init__(self, replies: dict[str, str] | None = None, default: str | None = None) -> None:
        self.replies = replies if replies is not None else {
            "Priya": extraction_json(tasks=REC_001_TASKS),
            "Lena": extraction_json(contact={"name": "Lena", "organisation": None, "role": None,
                                             "context": "Brief hallway chat at an event."}, tasks=[]),
        }
        self.default = default or extraction_json(tasks=[{"content": "Follow up", "due_string": None, "notes": None}])
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        for marker, reply in self.replies.items():
            if marker in user:
                return reply
        return self.default


def write_config(tmp_path: Path, raw_toml: str = "") -> Path:
    path = tmp_path / "config.toml"
    path.write_text(raw_toml, encoding="utf-8")
    return path


def make_settings(tmp_path: Path, raw: dict | None = None, *, fixtures: Path | None = FIXTURES,
                  env: dict | None = None) -> Settings:
    """Settings for a test. Routing is off unless the test asks for a backend (the default,
    Strands Decider, needs a router, which the routing tests supply); the inputs are Pocket
    (read from the fixtures) and the /demo text."""
    base_env = {
        "RATATOSKR_STATE_DIR": str(tmp_path / "state"),
        "RATATOSKR_PROMPTS_DIR": str(tmp_path / "prompts"),  # empty: packaged prompts are used
        "POCKET_API_KEY": "test-pocket-key",
        "TODOIST_API_TOKEN": "test-todoist-token",
        "TELEGRAM_BOT_TOKEN": "TEST-TOKEN",
        "TELEGRAM_CHAT_ID": "4242",
        "BEDROCK_API_KEY": "test-bedrock-key",
    }
    if fixtures is not None:
        base_env["RATATOSKR_FIXTURES"] = str(fixtures)
    base_env.update(env or {})
    raw = dict(raw or {})
    raw["route"] = {"backend": "none", **raw.get("route", {})}
    raw["devices"] = {"inputs": ["pocket", "text"], **raw.get("devices", {})}  # most fixtures are Pocket recordings
    return settings_from_mapping(raw, base_env)


def planted_fixtures(tmp_path: Path, marker: str = SENTINEL) -> Path:
    """Copy the fixtures with `marker` planted in every non-null transcript (T-LEAK)."""
    target = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, target)
    for path in target.glob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        records = body["data"] if isinstance(body["data"], list) else [body["data"]]
        for record in records:
            if isinstance(record.get("transcript"), str):
                record["transcript"] = record["transcript"] + f" Speaker 1: my code word is {marker}."
        path.write_text(json.dumps(body), encoding="utf-8")
    return target


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def services():
    """respx-mocked Todoist and Telegram. Any other request fails the test."""
    todoist, telegram = FakeTodoist(), FakeTelegram()
    with respx.mock(assert_all_called=False) as router:
        router.post(SYNC_URL).mock(side_effect=todoist.handler)
        router.post(TELEGRAM_SEND).mock(side_effect=telegram.handler)
        yield todoist, telegram, router


def runtime_for(settings: Settings, model: Callable[[str, str], str] | None, clock: FakeClock,
                router=None) -> Runtime:
    return build_runtime(settings, dry_run=False, model_call=model, http=httpx.Client(), clock=clock, router=router)


def wrist_messages(telegram: "FakeTelegram") -> list[str]:
    """The Telegram texts that are wrist-shaped task messages (three lines, the third with 'commit')."""
    return [t for t in telegram.texts if len(t.splitlines()) == 3 and t.splitlines()[2].startswith("commit")]
