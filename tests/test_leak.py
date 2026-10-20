"""T-LEAK (C1), the secrets grep (C6), the key-never-in-logs check, and the adversarial-model guard (C9)."""

import json
import re
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from ratatoskr_demo.commands import format_status
from ratatoskr_demo.extract import CORRECTIVE_INSTRUCTION
from ratatoskr_demo.config import GuardSettings
from ratatoskr_demo.guard import any_overlap, normalise
from ratatoskr_demo.transcript import extract_text
from tests.conftest import (
    REC_001_TASKS, SENTINEL, ScriptedModel, extraction_json, make_settings, planted_fixtures, runtime_for,
)

REPO = Path(__file__).resolve().parents[1]
NGRAM_WORDS = GuardSettings().ngram_words  # 8: the leak guard's measure of a quoted span
# Ruling 13: the live run's tripwire recording, whose Pocket title (built from the conversation) carried
# the protected name. A title may stay in the local ledger; it may not reach a shared surface.
TITLE = "Vargrime Onboarding Pack Review"


def test_t_leak_the_sentinel_appears_nowhere_after_a_full_cycle(tmp_path, clock, services, caplog, monkeypatch):
    import ratatoskr_demo.cycle as cycle_module

    todoist, telegram, _ = services
    reached_route = []
    real_route = cycle_module.route
    monkeypatch.setattr(cycle_module, "route",
                        lambda e, s, r=None, today=None: reached_route.append(repr(e)) or real_route(e, s, r, today=today))
    caplog.set_level("DEBUG")

    fixtures = planted_fixtures(tmp_path)
    for path in fixtures.glob("*.json"):  # Pocket's list and the recording itself both carry the title
        body = json.loads(path.read_text(encoding="utf-8"))
        for record in body["data"] if isinstance(body["data"], list) else [body["data"]]:
            if record.get("id") == "rec_002":
                record["title"] = TITLE
        path.write_text(json.dumps(body), encoding="utf-8")
    settings = make_settings(tmp_path, fixtures=fixtures)
    model = ScriptedModel()
    runtime = runtime_for(settings, model, clock)
    runtime.cycle.run()
    clock.advance(300)
    runtime.cycle.run()

    # The sentinel really was in the transcripts the model saw, so the test can bite.
    assert any(SENTINEL in user for _, user in model.calls)

    places = {
        "ledger file": settings.ledger_path.read_text(encoding="utf-8"),
        "log capture": caplog.text,
        "recent log in state": "\n".join(runtime.state.snapshot()["recent_log"]),
        "telegram payloads": json.dumps(telegram.messages),
        "todoist payloads": json.dumps(todoist.payloads),
        "telegram /status reply": format_status(runtime.state),
        "what reached route()": "\n".join(reached_route),
    }
    for path in settings.paths.state_dir.rglob("*"):
        if path.is_file():
            places[f"state file {path.name}"] = path.read_bytes().decode("utf-8", "replace")

    for where, text in places.items():
        assert SENTINEL not in text, f"transcript sentinel leaked into {where}"
    shared = ("telegram payloads", "todoist payloads", "telegram /status reply", "what reached route()")
    for where in shared:
        assert TITLE not in places[where], f"the held recording's title reached {where}"
    assert json.loads(settings.ledger_path.read_text())["rec_002"]["status"] == "hold"  # it really was held
    assert len(reached_route) == 2 and todoist.tasks and telegram.messages  # the cycle really ran


def fixture_transcripts(*directories):
    """Every transcript in the fixture recordings of these directories (detail files and list pages)."""
    texts = []
    for directory in directories:
        for path in sorted(Path(directory).rglob("*.json")):
            body = json.loads(path.read_text(encoding="utf-8"))
            data = body.get("data") if isinstance(body, dict) else None
            for record in data if isinstance(data, list) else [data] if isinstance(data, dict) else []:
                text = extract_text(record)
                if isinstance(text, str):
                    texts.append(text)
    return texts


def test_no_eight_word_span_of_any_fixture_transcript_reaches_any_output_of_a_full_cycle(tmp_path, clock, services,
                                                                                        caplog):
    """C1, as the Safety section states it: the transcript lives in memory for one cycle; nothing the courier
    writes or sends carries an eight-word span of it (the leak guard's own measure)."""
    todoist, telegram, _ = services
    caplog.set_level("DEBUG")
    settings = make_settings(tmp_path)
    runtime = runtime_for(settings, ScriptedModel(), clock)
    runtime.cycle.run()
    clock.advance(300)
    runtime.cycle.run()
    transcripts = fixture_transcripts(settings.fixtures_dir)
    outputs = {"log capture": caplog.text, "telegram payloads": json.dumps(telegram.messages),
               "todoist payloads": json.dumps(todoist.payloads), "telegram /status reply": format_status(runtime.state)}
    for path in settings.paths.state_dir.rglob("*"):
        if path.is_file():
            outputs[f"state file {path.name}"] = path.read_bytes().decode("utf-8", "replace")
    assert len(transcripts) >= 4 and todoist.tasks and telegram.messages  # the cycle really ran on them
    for where, text in outputs.items():
        assert not any(any_overlap(t, [text], NGRAM_WORDS) for t in transcripts), f"a transcript span reached {where}"


def test_no_eight_word_span_of_any_bench_transcript_is_in_the_committed_results():
    results = (REPO / "bench" / "RESULTS.md").read_text(encoding="utf-8")
    transcripts = fixture_transcripts(REPO / "bench" / "fixtures", REPO / "tests" / "fixtures")
    assert len(transcripts) >= 12
    assert not any(any_overlap(t, [results], NGRAM_WORDS) for t in transcripts)


def test_no_eight_word_span_of_any_fixture_transcript_is_in_a_notebook():
    """Ruling 35: a notebook is committed with its outputs; it reads transcripts at run time and prints none.
    Each cell's source and every output it carries are read as text."""
    notebooks = sorted((REPO / "notebooks").glob("*.ipynb"))
    transcripts = fixture_transcripts(REPO / "bench" / "fixtures", REPO / "tests" / "fixtures")
    assert notebooks and len(transcripts) >= 12
    for path in notebooks:
        texts = []
        for cell in json.loads(path.read_text(encoding="utf-8"))["cells"]:
            texts.append("".join(cell.get("source", [])))
            for out in cell.get("outputs", []):
                texts.append("".join(out.get("text", [])))
                texts.extend("".join(v) if isinstance(v, list) else str(v) for v in out.get("data", {}).values())
        assert not any(any_overlap(t, texts, NGRAM_WORDS) for t in transcripts), path.name


# --- C9: an adversarial model that quotes the transcript ---

SPAN = "we're hiring a junior ops person in the spring, keep an eye out"


def adversarial(field: str):
    contact = {"name": "Priya Venkataraman", "organisation": "Aldrin Microsystems", "role": None,
               "context": "Met at a meetup."}
    tasks = [dict(t) for t in REC_001_TASKS]
    if field == "content":
        tasks[1]["content"] = "Note " + SPAN
    elif field == "notes":
        tasks[1]["notes"] = "She said " + SPAN
    else:
        contact["context"] = "Meetup chat; " + SPAN
    return extraction_json(contact=contact, tasks=tasks)


@pytest.mark.parametrize("field", ["content", "notes", "contact.context"])
def test_the_guard_blocks_a_verbatim_span_retries_once_then_holds(tmp_path, clock, services, field):
    todoist, telegram, _ = services
    model = ScriptedModel(replies={
        "Priya": adversarial(field),
        "Lena": extraction_json(contact={"name": "Lena", "organisation": None, "role": None,
                                         "context": "Hallway chat."}, tasks=[]),
    })
    settings = make_settings(tmp_path)
    runtime_for(settings, model, clock).cycle.run()

    rec_001_calls = [user for _, user in model.calls if "Priya" in user]
    assert len(rec_001_calls) == 2  # the first try and exactly one corrective retry
    assert rec_001_calls[1].endswith(CORRECTIVE_INSTRUCTION)

    entry = json.loads(settings.ledger_path.read_text())["rec_001"]
    assert entry["status"] == "hold" and entry["reason"] == "leak-guard"

    outbound = json.dumps(todoist.payloads) + json.dumps(telegram.messages)
    assert normalise(SPAN) not in normalise(outbound)
    assert not any("rec_001" in args["description"] for args in todoist.tasks.values())
    [notice] = [t for t in telegram.texts if "rec_001" in t]
    assert notice == "Held rec_001: leak-guard. Tag it ok in Pocket to release it."  # the id only (ruling 28)


def test_a_corrected_retry_goes_through(tmp_path, clock, services):
    todoist, _, _ = services
    replies = iter([adversarial("notes"), extraction_json(tasks=REC_001_TASKS)])

    def fixing_model(system, user):
        if "Priya" in user:
            return next(replies)
        return extraction_json(tasks=[])

    settings = make_settings(tmp_path)
    runtime_for(settings, fixing_model, clock).cycle.run()
    assert json.loads(settings.ledger_path.read_text())["rec_001"]["status"] == "done"
    assert len([a for a in todoist.tasks.values() if "rec_001" in a["description"]]) == 3


# --- C6: no secrets in the repository ---

# Built by concatenation so this file does not match itself.
SECRET_PATTERNS = [
    re.compile("pk" + r"_[A-Za-z0-9]{8,}"),                       # Pocket
    re.compile("sk" + r"-ant-[A-Za-z0-9_\-]{8,}"),                # Anthropic (none here; kept from the private repo)
    re.compile("api" + r"key_[A-Za-z0-9_]{16,}"),                 # TypeSafe
    re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40,}(?![0-9a-fA-F])"),  # any 40+ hex run (a Todoist token is 40 hex)
    # any 40+ base64 run that mixes letters and digits (a Bedrock API key is base64)
    re.compile(r"(?<![A-Za-z0-9+/=])(?=[A-Za-z0-9+/]*[0-9])(?=[A-Za-z0-9+/]*[A-Za-z])[A-Za-z0-9+/]{40,}={0,2}"),
    re.compile(r"(?<![0-9])[0-9]{8,10}:" + r"[A-Za-z0-9_-]{30,}"),  # a Telegram bot token (35 after the colon)
]
BINARY_PROBE_BYTES = 8192


def repo_files(root: Path = REPO):
    """What git could commit, and nothing else (Dan's ruling 20): tracked files, and untracked ones that
    .gitignore does not keep out. Ignored files (the ledger, the venvs, the media's audio and video) are never
    listed, so never opened. The .env file is skipped by name as well: no test ever opens it."""
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=root, capture_output=True, check=True).stdout
    for name in listed.decode("utf-8").split("\0"):
        path = root / name
        if not name or path.name == ".env" or path.name.endswith(".env"):
            continue
        if path.is_file():  # a tracked file deleted from the working tree is still in the index
            yield path


def is_binary(path: Path) -> bool:
    """A null byte in the first 8 KB: a binary file, skipped and never read as text (ruling 20)."""
    with path.open("rb") as fh:
        return b"\0" in fh.read(BINARY_PROBE_BYTES)


def secret_hits(paths):
    hits = []
    for path in paths:
        if path.name == ".env.example" or is_binary(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.name}: {pattern.pattern[:40]}")  # the pattern, never the match
    return hits


def test_no_secret_looking_strings_exist_outside_env_example():
    """C6: key prefixes followed by key material, any 40+ hex run, any 40+ base64 run
    with letters and digits, or a Telegram bot token. The bare prefixes may appear
    in prose (CLAUDE.md names them), never followed by a key."""
    assert secret_hits(repo_files()) == []


def test_the_secrets_grep_would_catch_a_planted_key_of_every_shape(tmp_path):
    planted = tmp_path / "oops.py"
    planted.write_text(
        'TODOIST = "' + "0123456789abcdef" * 3 + '"\n'
        'POCKET = "pk' + '_live1234abcd"\n'
        'JEV = "api' + 'key_' + "x7" * 20 + '"\n'
        'BEDROCK = "AB' + "SKQmVkcm9ja0FQSUtleS1" * 3 + '"\n'
        'TELEGRAM = "1234567' + '89:AA' + "Hx_y-Z0" * 5 + '"\n', encoding="utf-8")
    assert len(secret_hits([planted])) == 5


def test_the_env_file_is_git_ignored():
    """Tokens live in .env, which git ignores (and which no test opens): asked of git, not of .gitignore's text."""
    for name in (".env", "config/.env"):
        assert subprocess.run(["git", "check-ignore", "-q", name], cwd=REPO).returncode == 0, name


def test_the_grep_never_reads_the_env_file():
    assert not any(path.name == ".env" or path.name.endswith(".env") for path in repo_files())


def test_the_scan_covers_what_git_could_commit_and_never_reads_a_binary_file_as_text(tmp_path, monkeypatch):
    """Ruling 20: a planted 40-hex string is flagged in a tracked file (and in an untracked one git could
    still commit), not in an ignored file, which is never even listed; a binary file is never read as text."""
    hexed = "0123456789abcdef" * 3  # 48 hex, built here so this file does not match itself
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    for name in ("ignored/notes.json", "tracked.json", "untracked.json"):
        (tmp_path / name).write_text(f'{{"hash": "{hexed}"}}\n', encoding="utf-8")
    (tmp_path / "sound.wav").write_bytes(b"RIFF\0\0\0\0WAVE" + hexed.encode())
    subprocess.run(["git", "add", ".gitignore", "tracked.json", "sound.wav"], cwd=tmp_path, check=True)

    read_as_text, real_read_text = [], Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: read_as_text.append(self.name) or
                        real_read_text(self, *a, **k))
    listed = {path.name for path in repo_files(tmp_path)}
    hits = secret_hits(repo_files(tmp_path))

    assert listed == {".gitignore", "tracked.json", "untracked.json", "sound.wav"}  # notes.json: ignored
    assert sorted({hit.split(":")[0] for hit in hits}) == ["tracked.json", "untracked.json"]
    assert "notes.json" not in read_as_text and "sound.wav" not in read_as_text


# --- the key, and every other secret, never reaches a log, ledger, record or exception ---

def secret_shaped_env():
    """Values shaped like each real secret, built at runtime so the grep above does not see them."""
    return {
        "BEDROCK_API_KEY": "AB" + "SKQmVkcm9ja0FQSUtleS1" * 3,
        "TODOIST_API_TOKEN": "0a1b2c3d4e" * 4,
        "POCKET_API_KEY": "pk" + "_" + "Live7Key9" * 3,
        "TYPESAFE_API_KEY": "api" + "key_" + "z9" * 30,
        "TELEGRAM_BOT_TOKEN": "1234567" + "89:AA" + "Hx_y-Z0" * 5,
    }


def test_no_secret_value_reaches_a_log_a_ledger_a_record_or_an_exception(tmp_path, clock, caplog):
    from ratatoskr_demo.bedrock import BedrockClient, make_spend_ledger
    from ratatoskr_demo.cli import build_runtime
    from ratatoskr_demo.errors import RatatoskrError
    from ratatoskr_demo.todoist import SYNC_URL
    from tests.conftest import FakeTelegram, FakeTodoist
    from tests.test_bedrock import BASE, FakeMantle, write_baseline

    caplog.set_level("DEBUG")
    secrets = secret_shaped_env()
    write_baseline(tmp_path)
    settings = make_settings(tmp_path, {"paths": {"model_baseline": str(tmp_path / "model_baseline.json")}},
                             env=secrets)
    todoist, telegram, mantle = FakeTodoist(), FakeTelegram(), FakeMantle()
    mantle.content = extraction_json(tasks=REC_001_TASKS)
    errors = []
    with respx.mock(assert_all_called=False) as router:
        router.post(SYNC_URL).mock(side_effect=todoist.handler)
        router.post(f"https://api.telegram.org/bot{secrets['TELEGRAM_BOT_TOKEN']}/sendMessage").mock(
            side_effect=telegram.handler)
        router.get(f"{BASE}/models").mock(side_effect=mantle.models)
        router.post(f"{BASE}/chat/completions").mock(side_effect=mantle.chat)
        client = BedrockClient(settings, make_spend_ledger(settings), http=httpx.Client())
        runtime = build_runtime(settings, dry_run=False, http=httpx.Client(), clock=clock, bedrock=client)
        runtime.cycle.run()
        for status in (401, 403, 400, 429, 503):  # every error path's message, too
            mantle.fail = status
            try:
                client.chat([{"role": "user", "content": "x"}], purpose="test")
            except RatatoskrError as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        mantle.fail, mantle.model_name = None, "someone.else"
        try:
            client.chat([{"role": "user", "content": "x"}], purpose="test")
        except RatatoskrError as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    assert todoist.tasks and wrist_count(telegram) >= 3 and len(errors) == 6  # it all really ran
    places = {
        "log capture": caplog.text,
        "telegram bodies": json.dumps(telegram.messages),
        "todoist bodies": json.dumps(todoist.payloads),
        "bedrock bodies": json.dumps(mantle.bodies),
        "exception texts": "\n".join(errors),
        "settings repr": repr(settings),
    }
    for path in settings.paths.state_dir.rglob("*"):
        if path.is_file():
            places[f"state file {path.name}"] = path.read_bytes().decode("utf-8", "replace")
    for name, value in secrets.items():
        for where, text in places.items():
            assert value not in text, f"{name} leaked into {where}"


def test_the_bot_token_is_masked_in_httpx_logs_at_any_level(caplog):
    """httpx logs request URLs at INFO, and Telegram's URL holds the token: the filter masks it."""
    from ratatoskr_demo.notify import Notifier

    token = secret_shaped_env()["TELEGRAM_BOT_TOKEN"]
    caplog.set_level("DEBUG")
    import logging
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    with respx.mock() as router:
        router.post(f"https://api.telegram.org/bot{token}/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True}))
        Notifier(token, 4242, http=httpx.Client()).send_message("hello")
    assert "/bot<redacted>" in caplog.text and token not in caplog.text


def wrist_count(telegram):
    return sum(1 for t in telegram.texts if len(t.splitlines()) == 3)
