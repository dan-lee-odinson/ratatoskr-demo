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

"""The image evaluation (Dan's ruling 47): sixteen rendered pictures through the courier's photo path.

Each picture in bench/images/ is placed, one at a time, in the watched folder of a courier whose state lives in a
folder of its own under bench/raw/, and goes through the courier's own cycle once: the readability check, the image
gate on the vision server (port 8001), the OCR on the laptop, the stop-list tripwire, the text gate and the routing
read on the published Decider server (port 8000), the extraction by openai.gpt-oss-20b on Bedrock (through the spend
ledger), the rule, and the Todoist write, kept by a local endpoint that records every Sync command (nothing left the
laptop for Todoist). The OCR's text stays in memory: only which of each picture's key words it recovered is kept.

    python bench/run_images.py --run --decider-pid N --vision-pid M   the run (both servers up); writes the export
    python bench/run_images.py                                       the report from the export (no network, model, key)
    python bench/run_images.py --check                               each report, compared with the committed one

The report is drawn from the export alone, so the second and third need nothing the run needed.

With --second (Dan's ruling 50), the second run: the twenty-four pictures of bench/images/labels_v2.json, with the
scene decision after the text gate, and the courier's Telegram lines kept too, by a local endpoint like Todoist's
(nothing left the laptop for Telegram). bench/image_eval_2026-10-21_scene.json and bench/IMAGE_EVAL_2026-10-21_scene.md; the
first run's export and report are not touched.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import statistics
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / "bench" / "images"
EXPORT = REPO / "bench" / "image_eval_2026-10-21.json"
REPORT = REPO / "bench" / "IMAGE_EVAL_2026-10-21.md"
EXPORT2 = REPO / "bench" / "image_eval_2026-10-21_scene.json"  # the second run (ruling 50)
REPORT2 = REPO / "bench" / "IMAGE_EVAL_2026-10-21_scene.md"
EXPORT3 = REPO / "bench" / "image_eval_2026-10-22_released.json"  # the released pictures (ruling 52)
REPORT3 = REPO / "bench" / "IMAGE_EVAL_2026-10-22_released.md"
EXPORT4 = REPO / "bench" / "image_eval_2026-10-22_released2.json"  # the second addendum (ruling 53)
REPORT4 = REPO / "bench" / "IMAGE_EVAL_2026-10-22_released2.md"
EXPORT5 = REPO / "bench" / "image_eval_2026-10-22_final.json"  # the final run (ruling 54)
REPORT5 = REPO / "bench" / "IMAGE_EVAL_2026-10-22_final.md"
COMMAND = "python bench/run_images.py --check"
GATE_REASONS = ("image-sensitive", "image-unsure")
SCENES = ("owner_owes", "owed_to_owner", "reference", "unsure")
# The five new ordinary pictures the second run's gates held as unsure: n1, n2, n7, n8 by the image gate, n3 by the
# text gate (ruling 52)
RELEASED = ("n1_owed_note.png", "n2_owed_whiteboard.png", "n3_owed_message.png", "n7_owes_whiteboard.png",
            "n8_owes_note.png")
TAP_WAIT_SECONDS = 45 * 60  # per release: the owner's tap, then the run goes on


def _bench() -> Any:
    if "run_bench" in sys.modules:
        return sys.modules["run_bench"]
    spec = importlib.util.spec_from_file_location("run_bench", REPO / "bench" / "run_bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_bench"] = module
    spec.loader.exec_module(module)
    return module


def grouped(digest: str) -> str:
    return " ".join(digest[i:i + 16] for i in range(0, len(digest), 16))


def recovered(text: str, keywords: list[str]) -> list[str]:
    """The key words the OCR's text holds, ignoring case and spaces (the OCR sometimes runs words together)."""
    squashed = "".join(text.lower().split())
    return [k for k in keywords if "".join(k.lower().split()) in squashed]


# --- the run ---

class _Reads:
    """Every Decider read on the published server, timed, with the text gate's probability: per picture."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.gate_p: list[float] = []
        self.gate_seconds: list[float] = []
        self.routing_seconds: list[float] = []
        self.routing_waiting_p: list[float] = []  # the someone_waiting answer of each routing read (ruling 50)

    def wrap(self, client: Any) -> None:
        inner = client.systemone

        def timed(body: dict[str, Any]) -> dict[str, Any]:
            started = time.perf_counter()
            response = inner(body)
            seconds = time.perf_counter() - started
            if isinstance(body.get("state"), str):  # a transcript window: the text gate
                self.gate_seconds.append(round(seconds, 3))
                self.gate_p.append(round(float(response["answers"]["sensitive"]["noul"]), 4))
            else:
                self.routing_seconds.append(round(seconds, 3))
                waiting = (response.get("answers") or {}).get("someone_waiting") or {}
                if isinstance(waiting.get("noul"), (int, float)):
                    self.routing_waiting_p.append(round(float(waiting["noul"]), 4))
            return response

        client.systemone = timed


class _TelegramRecord:
    """A local Bot API endpoint for the courier's Notifier (the second run): answers as Telegram does and keeps
    each message's first line and its buttons. Nothing is sent to Telegram."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def handler(self, request: Any) -> Any:
        import httpx

        payload = json.loads(request.content or b"{}")
        if "text" in payload:
            buttons = [b["text"] for row in (payload.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]
            self.sent.append({"first_line": str(payload["text"]).split("\n")[0], "buttons": buttons})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})


class _ReadingOcr:
    """The photo adapter's OCR, with the text kept only long enough to see which key words it holds."""

    def __init__(self, ocr: Any) -> None:
        self.ocr, self.last_text = ocr, ""

    def read(self, path: Path) -> tuple[str, list[float]]:
        text, confidence = self.ocr.read(path)
        self.last_text = text
        return text, confidence


def _memory(pid: int | None) -> dict[str, float] | None:
    if not pid:
        return None
    import psutil

    m = psutil.Process(pid).memory_info()
    return {"resident_gb": round(m.rss / 2**30, 2), "private_gb": round(getattr(m, "private", 0) / 2**30, 2)}


def run(args: argparse.Namespace) -> dict[str, Any]:
    bench = _bench()
    from ratatoskr_demo.bedrock import BedrockClient, make_spend_ledger
    from ratatoskr_demo.cli import build_runtime
    from ratatoskr_demo.config import load_settings
    from ratatoskr_demo.decider import make_router
    from ratatoskr_demo.route import load_route_questions
    from ratatoskr_demo.runlock import refuse_while_loop_runs
    from ratatoskr_demo.source_gate import make_source_gate

    second = bool(getattr(args, "second", False))  # ruling 50: the twenty-four pictures, the scene decision
    labels_file = "labels_v2.json" if second else "labels.json"
    labels_raw = (IMAGES / labels_file).read_bytes()
    labels = json.loads(labels_raw)
    settings = load_settings()
    refuse_while_loop_runs(settings.paths.state_dir, "the image evaluation")
    settings = replace(settings, route=replace(settings.route, decider_timeout_seconds=bench.BENCH_DECIDER_TIMEOUT))
    spend = make_spend_ledger(settings)
    bedrock = BedrockClient(settings, spend)
    bedrock.check_model_record()
    questions = load_route_questions(settings)
    router = make_router(settings, questions)
    router.check_access()
    gate = make_source_gate(settings, questions)
    health = router.client.health()
    import httpx

    vision_health = httpx.get(f"{settings.route.vision_url}/health", timeout=10).json()
    if not vision_health.get("vision"):
        raise SystemExit("run_images: the server on the vision URL was not started with --vision")
    stamp = time.strftime("%Y%m%dT%H%M%S")
    state_dir = bench.RAW_DIR / f"images-{stamp}" / "state"
    record, telegram = bench.TodoistRecord(), _TelegramRecord()
    local = {"todoist_api_token": "local-record-only"}
    if second:  # the scene's actions include Telegram lines: kept by the local endpoint, never sent
        local.update(telegram_bot_token="local-record-only", telegram_chat_id=1)
    courier = replace(settings, fixtures_dir=None, paths=replace(settings.paths, state_dir=state_dir),
                      devices=replace(settings.devices, inputs=("glasses",),
                                      outputs=("todoist", "telegram") if second else ("todoist",)),
                      secrets=replace(settings.secrets, **local))
    handler = (lambda request: telegram.handler(request) if request.url.host == "api.telegram.org"
               else record.handler(request))
    runtime = build_runtime(courier, dry_run=False, bedrock=bedrock, router=router, source_gate=gate,
                            http=httpx.Client(transport=httpx.MockTransport(handler)))
    reads = _Reads()
    reads.wrap(router.client)
    reads.wrap(gate.client)
    adapter = runtime.cycle.deps.inputs["glasses"]
    ocr = _ReadingOcr(adapter.ocr)
    adapter.ocr = ocr
    inbox = adapter.folder
    inbox.mkdir(parents=True, exist_ok=True)
    runtime.cycle._decider_up = None  # as a cycle starts: /health before the first read (ruling 9)
    spend_before = spend.summary()
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows, stopped = [], None
    from ratatoskr_demo.devices.glasses_adapter import photo_id

    for label in labels["images"]:
        target = inbox / label["file"]
        shutil.copyfile(IMAGES / label["file"], target)
        envelope = next(e for e in adapter.list_new() if e.payload.uri == target.resolve().as_uri())
        rid = photo_id(target)
        reads.reset()
        ocr.last_text = ""
        before = bench.extract_calls(settings.spend_path)
        sent_before = len(telegram.sent)
        began = time.perf_counter()
        runtime.cycle._visit(adapter, envelope, time.time())
        seconds = time.perf_counter() - began
        entry = runtime.state.ledger_get(rid)
        found = recovered(ocr.last_text, label["ocr_keywords"]) if ocr.last_text else []
        ocr.last_text = ""  # the picture's text leaves memory here
        rows.append({
            "file": label["file"], "set": label["set"], "id": rid,
            "status": entry.status if entry else None, "reason": entry.reason if entry else None,
            "image": dict(entry.image) if entry else {},
            "extraction_calls_spend_ledger": bench.extract_calls(settings.spend_path) - before,
            "written": [{"content": a.get("content"), "labels": a.get("labels"), "priority": a.get("priority")}
                        for a in record.items_for(rid)],
            "ocr_keywords_found": found,
            "text_gate_p": reads.gate_p, "text_gate_seconds": reads.gate_seconds,
            "routing_seconds": reads.routing_seconds, "seconds": round(seconds, 2),
        })
        if second:  # what the owner was told, and whether a reference note was kept (only its header is read)
            note, header = state_dir / "reference" / f"{rid}.txt", ""
            if note.is_file():
                with note.open(encoding="utf-8") as fh:
                    header = fh.readline() + fh.readline()
            rows[-1].update(telegram=telegram.sent[sent_before:], routing_waiting_p=reads.routing_waiting_p,
                            reference_note=header.startswith(f"id: {rid}\ndate: "))
        print(f"  {label['file']}: {rows[-1]['status']} ({rows[-1]['reason']}); image gate p "
              f"{rows[-1]['image'].get('gate_p')}; extraction calls {rows[-1]['extraction_calls_spend_ledger']}; "
              f"writes {len(rows[-1]['written'])}", flush=True)
        if entry is None or entry.status not in ("done", "hold"):
            stopped = f"{label['file']}: the courier left no final ledger entry; the run stops here (ruling 47)"
            break
    from importlib.metadata import version

    return {
        "about": (("Ruling 50's image evaluation, the second run, as it recorded it. bench/run_images.py draws "
                   "bench/IMAGE_EVAL_2026-10-21_scene.md from this file alone. Fictional pictures only.") if second else
                  ("Ruling 47's image evaluation, as the run recorded it. bench/run_images.py draws "
                   "bench/IMAGE_EVAL_2026-10-21.md from this file alone. Fictional pictures only.")),
        "labels_file": labels_file,
        "labels_sha256": grouped(hashlib.sha256(labels_raw).hexdigest()),
        "labels_json": labels_raw.decode("utf-8"),
        "code": bench._git("rev-parse", "--short", "HEAD"),
        "runner_blob": bench.git_blob_hash(Path(__file__)),
        "started": started, "finished": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "stopped": stopped,
        "versions": {
            "image gate": (f"`{vision_health.get('checkpoint')}` with `--vision` on {vision_health.get('device')}, "
                           "strands-decider 0.2.0 in its own venv, 127.0.0.1:8001, the picture question at the text "
                           f"gate's floor ({questions['sensitive'].min_confidence:.2f}; unsure between "
                           f"{1 - questions['sensitive'].min_confidence:.2f} and "
                           f"{questions['sensitive'].min_confidence:.2f})"),
            "OCR": (f"RapidOCR (rapidocr-onnxruntime {version('rapidocr-onnxruntime')}, onnxruntime "
                    f"{version('onnxruntime')}, its bundled models), on the laptop"),
            "text gate and routing": (f"`{health.get('checkpoint')}` on {health.get('device')}, strands-decider "
                                      f"{bench._decider_version()}, 127.0.0.1:8000"),
            "extraction model": (f"`{settings.model.model_id}` on Bedrock (bedrock-mantle, us-east-1), reasoning "
                                 f"effort `{settings.model.reasoning_effort}`"),
            "configuration": (f"`config.toml` git blob `{bench.git_blob_hash(REPO / 'config.toml')}`; "
                              f"`ratatoskr_demo/prompts/route.toml` git blob `{bench.git_blob_hash(bench.ROUTE_TOML)}`"),
            "hardware": ("the build laptop's CPU (16 logical cores), each Decider server capped at 4 threads at "
                         f"below-normal priority; kernels: {bench.KERNELS}"),
            **({"scene": ("the same vision server: \"What does this picture represent?\" over the picture and its "
                          "OCR text, one choice of owner_owes, owed_to_owner, reference, at the routing read's floor "
                          f"for a choice question ({questions['project'].min_confidence:.2f}; under it, unsure)")}
               if second else {}),
            "conditions": args.conditions or "not stated",
        },
        "memory_end": {"text server": _memory(args.decider_pid), "vision server": _memory(args.vision_pid)},
        "spend_before": spend_before, "spend_after": spend.summary(),
        "images": rows,
    }


# --- the released pictures (Dan's ruling 52) ---

class _OwnerTelegram:
    """The released run's Telegram: a notice carrying a Release button goes to the owner's chat, for the owner's
    tap; every other line is kept by the local endpoint, as in the second run."""

    def __init__(self, real: Any) -> None:
        self.real, self.sent = real, []
        self.forward = True  # the final run (ruling 54): False while a picture the labels call sensitive is placed

    def handler(self, request: Any) -> Any:
        import httpx

        payload = json.loads(request.content or b"{}")
        buttons = [b for row in (payload.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]
        to_owner = self.forward and any(str(b.get("callback_data", "")).startswith("release:") for b in buttons)
        if "text" in payload:
            self.sent.append({"first_line": str(payload["text"]).split("\n")[0],
                              "buttons": [b["text"] for b in buttons], "to_owner": to_owner})
        if to_owner:  # the one kind of line that leaves the laptop: the owner must see it to tap it
            response = self.real.post(str(request.url), content=request.content,
                                      headers={"content-type": "application/json"})
            return httpx.Response(response.status_code, content=response.content,
                                  headers={"content-type": "application/json"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})


class _VisionReads:
    """Each read of the vision server in one visit: the picture question's probability, and the scene reads."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.picture_p: list[float] = []
        self.scene_reads = 0

    def wrap(self, client: Any) -> None:
        inner = client.systemone

        def counted(body: dict[str, Any]) -> dict[str, Any]:
            response = inner(body)
            if "picture" in body.get("questions", {}):
                self.picture_p.append(round(float(response["answers"]["picture"]["noul"]), 4))
            if "scene" in body.get("questions", {}):
                self.scene_reads += 1
            return response

        client.systemone = counted


def _poll_releases(get_updates: Any, resolver: Any, stop: threading.Event) -> None:
    """The owner's Release taps, read from the bot's updates and handed to ruling 43's resolver (which answers only
    the owner's chat and lets the first tap stand). Every other update is read and left unanswered."""
    from ratatoskr_demo.photos import is_release_tap

    offset = None
    while not stop.is_set():
        try:
            updates = get_updates(offset)
        except Exception:  # the poller must outlive a lost connection
            stop.wait(5)
            continue
        for update in updates:
            offset = int(update.get("update_id", 0)) + 1
            query = update.get("callback_query")
            if query and is_release_tap(str(query.get("data") or "")):
                resolver.handle(query)


def run_released(args: argparse.Namespace) -> dict[str, Any]:
    """The five new ordinary pictures the second run's gates held as unsure, each released by the owner's tap."""
    bench = _bench()
    import httpx

    from ratatoskr_demo.bedrock import BedrockClient, make_spend_ledger
    from ratatoskr_demo.cli import build_runtime
    from ratatoskr_demo.commands import telegram_get_updates
    from ratatoskr_demo.config import load_settings
    from ratatoskr_demo.decider import make_router
    from ratatoskr_demo.devices.glasses_adapter import photo_id
    from ratatoskr_demo.notify import Notifier
    from ratatoskr_demo.photos import RELEASABLE
    from ratatoskr_demo.resolve import Resolver
    from ratatoskr_demo.route import load_route_questions
    from ratatoskr_demo.runlock import refuse_while_loop_runs
    from ratatoskr_demo.source_gate import make_source_gate

    final = bool(getattr(args, "final", False))  # ruling 54: all twenty-four, the two-way scene, labels_v3.json
    labels_file = "labels_v3.json" if final else "labels_v2.json"
    labels_raw = (IMAGES / labels_file).read_bytes()
    labels = json.loads(labels_raw)
    chosen = labels["images"] if final else [x for x in labels["images"] if x["file"] in RELEASED]
    settings = load_settings()
    refuse_while_loop_runs(settings.paths.state_dir, "the image evaluation")
    if not (settings.secrets.telegram_bot_token and settings.secrets.telegram_chat_id):
        raise SystemExit("run_images --released: the owner's taps need the Telegram bot token and chat id in .env")
    settings = replace(settings, route=replace(settings.route, decider_timeout_seconds=bench.BENCH_DECIDER_TIMEOUT))
    spend = make_spend_ledger(settings)
    bedrock = BedrockClient(settings, spend)
    bedrock.check_model_record()
    questions = load_route_questions(settings)
    router = make_router(settings, questions)
    router.check_access()
    gate = make_source_gate(settings, questions)
    health = router.client.health()
    vision_health = httpx.get(f"{settings.route.vision_url}/health", timeout=10).json()
    if not vision_health.get("vision"):
        raise SystemExit("run_images: the server on the vision URL was not started with --vision")
    stamp = time.strftime("%Y%m%dT%H%M%S")
    again = bool(getattr(args, "released2", False))  # ruling 53: the second addendum, fresh copies
    state_dir = bench.RAW_DIR / (f"images-final-{stamp}" if final else
                                 f"images-released{'2' if again else ''}-{stamp}") / "state"
    record = bench.TodoistRecord()
    telegram = _OwnerTelegram(httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)))
    courier = replace(settings, fixtures_dir=None, paths=replace(settings.paths, state_dir=state_dir),
                      devices=replace(settings.devices, inputs=("glasses",), outputs=("todoist", "telegram")),
                      secrets=replace(settings.secrets, todoist_api_token="local-record-only"))
    handler = (lambda request: telegram.handler(request) if request.url.host == "api.telegram.org"
               else record.handler(request))
    runtime = build_runtime(courier, dry_run=False, bedrock=bedrock, router=router, source_gate=gate,
                            http=httpx.Client(transport=httpx.MockTransport(handler)))
    reads, vision = _Reads(), _VisionReads()
    reads.wrap(router.client)
    reads.wrap(gate.client)
    adapter = runtime.cycle.deps.inputs["glasses"]
    vision.wrap(adapter.gate.client)
    vision.wrap(adapter.scene.client)
    ocr = _ReadingOcr(adapter.ocr)
    adapter.ocr = ocr
    inbox = adapter.folder
    inbox.mkdir(parents=True, exist_ok=True)
    receiver_http = httpx.Client(timeout=httpx.Timeout(45.0, connect=10.0))
    owner = Notifier(courier.secrets.telegram_bot_token, courier.secrets.telegram_chat_id, http=receiver_http)
    resolver = Resolver(runtime.state, owner, None, courier.secrets.telegram_chat_id or 0,
                        questions["project"].criteria)
    stop = threading.Event()
    threading.Thread(target=_poll_releases, args=(telegram_get_updates(courier.secrets.telegram_bot_token,
                                                                        receiver_http), resolver, stop),
                     name="release-poller", daemon=True).start()
    runtime.cycle._decider_up = None  # as a cycle starts: /health before the first read (ruling 9)
    spend_before = spend.summary()
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows, stopped = [], None
    try:
        for label in chosen:
            target = inbox / label["file"]
            shutil.copyfile(IMAGES / label["file"], target)
            envelope = next(e for e in adapter.list_new() if e.payload.uri == target.resolve().as_uri())
            rid = photo_id(target)
            before = bench.extract_calls(settings.spend_path)
            sent_before = len(telegram.sent)
            # the owner releases only what the labels call ordinary; a sensitive picture's notice stays local
            releasable = label["set"] == "ordinary"
            telegram.forward = releasable
            visits = []
            while len(visits) < 4:  # at most: held, released, held again by a later gate, released
                reads.reset()
                vision.reset()
                ocr.last_text = ""
                runtime.cycle._visit(adapter, envelope, time.time())
                entry = runtime.state.ledger_get(rid)
                visits.append({
                    "status": entry.status if entry else None, "reason": entry.reason if entry else None,
                    "picture_p": vision.picture_p, "ocr_ran": bool(ocr.last_text),
                    "ocr_keywords_found": recovered(ocr.last_text, label["ocr_keywords"]) if ocr.last_text else [],
                    "text_gate_p": reads.gate_p, "text_gate_seconds": reads.gate_seconds,
                    "scene_reads": vision.scene_reads, "routing_seconds": reads.routing_seconds,
                    "routing_waiting_p": reads.routing_waiting_p,
                })
                ocr.last_text = ""  # the picture's text leaves memory here
                releases = (entry.image.get("releases") if entry else None) or []
                if not (entry and entry.status == "hold" and entry.reason in RELEASABLE and releases
                        and releases[-1].get("by") is None) or not releasable:
                    break
                print(f"  {label['file']}: held ({entry.reason}); waiting for the owner's Release tap on {rid}",
                      flush=True)
                waited = time.monotonic()
                while time.monotonic() - waited < TAP_WAIT_SECONDS:
                    stop.wait(2)
                    last = (runtime.state.ledger_get(rid).image.get("releases") or [{}])[-1]
                    if last.get("by") == "owner":
                        print(f"  {label['file']}: released by the owner at {last.get('at')}", flush=True)
                        break
                else:
                    stopped = (f"{label['file']}: no Release tap within {TAP_WAIT_SECONDS // 60} minutes; the run "
                               "stops here")
                    break
            entry = runtime.state.ledger_get(rid)
            note, header = state_dir / "reference" / f"{rid}.txt", ""
            if note.is_file():  # only its header is read
                with note.open(encoding="utf-8") as fh:
                    header = fh.readline() + fh.readline()
            rows.append({
                "file": label["file"], "set": label["set"], "id": rid,
                "status": entry.status if entry else None, "reason": entry.reason if entry else None,
                "image": dict(entry.image) if entry else {}, "visits": visits,
                "extraction_calls_spend_ledger": bench.extract_calls(settings.spend_path) - before,
                "written": [{"content": a.get("content"), "labels": a.get("labels"), "priority": a.get("priority")}
                            for a in record.items_for(rid)],
                "telegram": telegram.sent[sent_before:],
                "reference_note": header.startswith(f"id: {rid}\ndate: "),
            })
            print(f"  {label['file']}: {rows[-1]['status']} ({rows[-1]['reason']}); scene "
                  f"{rows[-1]['image'].get('scene')} {rows[-1]['image'].get('scene_p')}; extraction calls "
                  f"{rows[-1]['extraction_calls_spend_ledger']}; writes {len(rows[-1]['written'])}", flush=True)
            if stopped:
                break
    finally:
        stop.set()
    from importlib.metadata import version

    return {
        **({"addendum": 2} if again else {}),
        **({"final": True} if final else {}),
        "about": (("Ruling 54's final image evaluation: the twenty-four pictures with the two-way scene, every "
                   "picture the labels call ordinary and a gate held as unsure released by the owner's tap, as the "
                   "run recorded it. bench/run_images.py draws bench/IMAGE_EVAL_2026-10-22_final.md from this file "
                   "alone. Fictional pictures only.") if final else
                  ("Ruling 53's second addendum, with no word floor for photos: " if again else
                   "Ruling 52's addendum to the second image evaluation: ") +
                  "the five new ordinary pictures the gates held as unsure, each released by the owner's tap, as the "
                  "run recorded it. bench/run_images.py draws "
                  f"bench/IMAGE_EVAL_2026-10-22_released{'2' if again else ''}.md from this file alone. Fictional "
                  "pictures only."),
        "labels_file": labels_file,
        "labels_sha256": grouped(hashlib.sha256(labels_raw).hexdigest()),
        "labels_json": labels_raw.decode("utf-8"),
        "code": bench._git("rev-parse", "--short", "HEAD"),
        "runner_blob": bench.git_blob_hash(Path(__file__)),
        "started": started, "finished": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "stopped": stopped,
        "versions": {
            "image gate": (f"`{vision_health.get('checkpoint')}` with `--vision` on {vision_health.get('device')}, "
                           "strands-decider 0.2.0 in its own venv, 127.0.0.1:8001, the picture question at the text "
                           f"gate's floor ({questions['sensitive'].min_confidence:.2f})"),
            "OCR": (f"RapidOCR (rapidocr-onnxruntime {version('rapidocr-onnxruntime')}, onnxruntime "
                    f"{version('onnxruntime')}, its bundled models), on the laptop"),
            "text gate and routing": (f"`{health.get('checkpoint')}` on {health.get('device')}, strands-decider "
                                      f"{bench._decider_version()}, 127.0.0.1:8000"),
            "scene": ("the vision server: \"What does this picture represent?\" over the picture and its OCR text, at "
                      f"the routing read's floor for a choice question ({questions['project'].min_confidence:.2f})"),
            "release": ("the owner's tap on the Release button of the hold's notice, in the owner's Telegram chat, "
                        "read from the bot's updates and recorded by the courier's own release handler"),
            "extraction model": (f"`{settings.model.model_id}` on Bedrock (bedrock-mantle, us-east-1), reasoning "
                                 f"effort `{settings.model.reasoning_effort}`"),
            "configuration": (f"`config.toml` git blob `{bench.git_blob_hash(REPO / 'config.toml')}`; "
                              f"`ratatoskr_demo/prompts/route.toml` git blob `{bench.git_blob_hash(bench.ROUTE_TOML)}`"),
            "hardware": ("the build laptop's CPU (16 logical cores), each Decider server capped at 4 threads at "
                         f"below-normal priority; kernels: {bench.KERNELS}"),
            "conditions": args.conditions or "not stated",
        },
        "memory_end": {"text server": _memory(args.decider_pid), "vision server": _memory(args.vision_pid)},
        "spend_before": spend_before, "spend_after": spend.summary(),
        "images": rows,
    }


# --- the report, from the export alone ---

def _frac(a: int, b: int) -> str:
    return f"{a} of {b}"


def _ms(values: list[float]) -> str:
    if not values:
        return "-"
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, max(0, -(-9 * len(ordered) // 10) - 1))]
    return f"{statistics.median(values):.2f} s / {p90:.2f} s ({len(values)})"


def _category(row: dict[str, Any]) -> str:
    reason = row["reason"] or ""
    if row["status"] == "done":
        if reason == "reference":  # ruling 50: kept on the laptop, nothing sent
            return "kept as reference"
        return "too short" if reason == "too-short" else "written"  # too short: the OCR found under 8 words
    if reason == "scene-unsure":  # ruling 50: the owner is asked, nothing sent
        return "scene unsure"
    if reason == "unreadable":
        return "unreadable"
    if reason in GATE_REASONS:
        return "image gate"
    if reason.startswith("stoplist:"):
        return "tripwire"
    if reason in ("source-sensitive", "source-unsure"):
        return "text gate"
    if reason in ("sensitive", "sensitive-unsure"):
        return "task check"
    if reason == "leak-guard":
        return "leak guard"
    return reason or "no entry"


def _judged(row: dict[str, Any], label: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """The extraction against the label, the projects (written / labelled) and the priorities written."""
    if row["status"] != "done":
        return "-", [], []
    keywords_ok = len(row["ocr_keywords_found"]) == len(label["ocr_keywords"])
    written = row["written"]
    contents = [str(w["content"] or "").lower() for w in written]
    matched, projects = [], []
    for t in label["expected_tasks"]:
        hit = next((w for w, c in zip(written, contents)
                    if any(all(word in c for word in group) for group in t["match"])), None)
        matched.append(hit is not None)
        if hit is not None:
            got = next((x[len("proj:"):] for x in hit["labels"] or [] if x.startswith("proj:")), "unsorted")
            projects.append(f"{got} / {t['project']}")
    priorities = [str(w.get("priority") or "-") for w in written]
    if not label["expected_tasks"]:
        only_notes = all("no follow-ups" in c for c in contents)
        verdict = "right (no commitment)" if only_notes else "extra task"
    elif all(matched):
        verdict = "right"
    else:
        verdict = "OCR miss" if not keywords_ok else f"missed {matched.count(False)} of {len(matched)}"
    return verdict, projects, priorities


def render(data: dict[str, Any]) -> str:
    labels = {x["file"]: x for x in json.loads(data["labels_json"])["images"]}
    rows = data["images"]
    sens = [r for r in rows if r["set"] == "sensitive"]
    ordn = [r for r in rows if r["set"] == "ordinary"]
    n_sens = sum(1 for x in labels.values() if x["set"] == "sensitive")
    n_ord = sum(1 for x in labels.values() if x["set"] == "ordinary")
    held = [r for r in rows if r["status"] == "hold"]
    cats = {r["file"]: _category(r) for r in rows}
    lines = [
        "# Image evaluation",
        "",
        "<!-- Generated by bench/run_images.py from bench/image_eval_2026-10-21.json. Do not edit by hand. -->",
        f"Run {data['started']} to {data['finished']} by `bench/run_images.py` (git blob `{data['runner_blob']}`), "
        f"at commit `{data['code']}`.",
        "",
        "- **An experiment with an untrained checkpoint:** Strands Decider was not trained on images (its model "
        "card); the image gate uses the vision tower that ships inside its base model. Every number here is an "
        "experiment with that checkpoint, and the image gate's floor and unsure band are the text gate's, a "
        "starting point, not a tuned value.",
        "- **Desktop-fed:** the sixteen pictures were rendered by code (`bench/images/render.py`) and placed in the "
        "watched folder from the desktop, one at a time; no camera, no glasses. A real photo is a stated limit.",
        "- **Before the cloud:** a photo passes three checks on the laptop before any cloud call: the image gate "
        "on the picture, then the stop-list tripwire and the text gate on the text the OCR read from it.",
        f"- **The labels:** `bench/images/labels.json`, sha256 `{data['labels_sha256']}`, committed before the "
        "first model call, not edited after the run.",
        f"- **Replay:** `{COMMAND}` draws this report again from `bench/image_eval_2026-10-21.json`, with no "
        "network, no model and no key.",
        "",
        "| | |", "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in data["versions"].items()]
    lines.append("")
    if data["stopped"]:
        lines += [f"**Stopped (a partial run):** {data['stopped']}", ""]
    gate_cleared_sens = [r for r in sens if r["image"].get("gate_p") is not None and cats[r["file"]] not in
                         ("image gate", "unreadable")]
    lines += ["## The counts", "", "| count | result |", "|---|---|",
              f"| sensitive pictures wrongly cleared (written to Todoist) | "
              f"{_frac(sum(1 for r in sens if r['status'] == 'done'), n_sens)} |",
              f"| sensitive pictures cleared by the image gate (then held by a later check, or written) | "
              f"{_frac(len(gate_cleared_sens), n_sens)} |"]
    for name in ("unreadable", "image gate", "tripwire", "text gate", "task check", "leak guard", "too short"):
        lines.append(f"| ordinary pictures held: {name} | {_frac(sum(1 for r in ordn if cats[r['file']] == name), n_ord)} |")
    lines += [f"| Bedrock extraction calls for held pictures (the courier's ledger / the spend ledger) | "
              f"{sum(r['image'].get('extraction_calls', 0) for r in held)} / "
              f"{sum(r['extraction_calls_spend_ledger'] for r in held)} (over {len(held)} held) |",
              f"| task writes for held pictures (the Todoist record) | {sum(len(r['written']) for r in held)} "
              f"(over {len(held)} held) |"]
    judged = {r["file"]: _judged(r, labels[r["file"]]) for r in ordn}
    cleared = [r for r in ordn if r["status"] == "done"]
    verdicts = [judged[r["file"]][0] for r in cleared]
    projects = [p for r in cleared for p in judged[r["file"]][1]]
    lines += [f"| cleared ordinary pictures: extraction right / OCR miss / model miss or extra | "
              f"{sum(v.startswith('right') for v in verdicts)} / {verdicts.count('OCR miss')} / "
              f"{sum(1 for v in verdicts if v.startswith('missed') or v == 'extra task')} (of {len(cleared)}) |",
              f"| project of the matched tasks: right / unsorted / wrong | "
              f"{sum(1 for p in projects if p.split(' / ')[0] == p.split(' / ')[1])} / "
              f"{sum(1 for p in projects if p.startswith('unsorted / ') and not p.endswith('/ unsorted'))} / "
              f"{sum(1 for p in projects if not p.startswith('unsorted / ') and p.split(' / ')[0] != p.split(' / ')[1])} "
              f"(of {len(projects)}) |"]
    every = lambda key: [v for r in rows for v in (r[key] if isinstance(r[key], list) else [r[key]])]  # noqa: E731
    lines += [f"| image gate read, median / p90 | {_ms([r['image']['gate_seconds'] for r in rows if 'gate_seconds' in r['image']])} |",
              f"| OCR, median / p90 | {_ms([r['image']['ocr_seconds'] for r in rows if 'ocr_seconds' in r['image']])} |",
              f"| text gate read, median / p90 | {_ms(every('text_gate_seconds'))} |",
              f"| routing read, median / p90 | {_ms(every('routing_seconds'))} |"]
    for name, m in data["memory_end"].items():
        if m:
            lines.append(f"| {name} memory at the end | {m['resident_gb']} GB resident, {m['private_gb']} GB private |")
    spent = data["spend_after"]["total_usd"] - data["spend_before"]["total_usd"]
    lines += [f"| Bedrock spend | ${spent:.6f} over {data['spend_after']['calls'] - data['spend_before']['calls']} "
              f"call(s); the ledger at ${data['spend_after']['total_usd']:.6f} of the "
              f"${data['spend_after']['hard_stop_usd']:.2f} hard stop |", ""]
    lines += ["Sources: each outcome is the courier's own ledger entry (its state folder under `bench/raw/`), which "
              "also holds the image gate's probability, the OCR's per-line confidence and the extraction call count; "
              "the extraction calls are also read from the Bedrock spend ledger (`ledger/spend.json`, purpose "
              "`extract`) before and after each picture; the writes from the Todoist adapter's record (a local "
              "endpoint that keeps every Sync command). The OCR recovery is which of the label's key words the OCR's "
              "text held, ignoring case and spaces; the text itself was never kept.", ""]
    lines += ["## Per picture", "",
              "| picture | set | image gate p | tripwire | text gate p | outcome | extraction calls | writes | "
              "OCR recovery | extraction vs label | project written / labelled | priority written |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        label = labels[r["file"]]
        p = r["image"].get("gate_p")
        ran_tripwire = r["image"].get("ocr_seconds") is not None
        tripwire = ("held" if cats[r["file"]] == "tripwire" else "passed") if ran_tripwire else "-"
        text_p = ", ".join(f"{x:.2f}" for x in r["text_gate_p"]) or "-"
        outcome = "written" if r["status"] == "done" else f"held ({r['reason']}" + (
            f": {r['image']['unreadable']}" if r["image"].get("unreadable") else "") + ")"
        verdict, projects, priorities = _judged(r, label) if r["set"] == "ordinary" else ("-", [], [])
        recovery = (f"{len(r['ocr_keywords_found'])} of {len(label['ocr_keywords'])}" if ran_tripwire else "-")
        lines.append(f"| {r['file']} | {r['set']} | {'-' if p is None else f'{p:.2f}'} | {tripwire} | {text_p} | "
                     f"{outcome} | {r['image'].get('extraction_calls', 0)} / {r['extraction_calls_spend_ledger']} | "
                     f"{len(r['written'])} | {recovery} | {verdict} | {'; '.join(projects) or '-'} | "
                     f"{', '.join(priorities) or '-'} |")
    lines += ["", "Extraction calls: the courier's ledger / the spend ledger. Priority: Todoist's 4 is high, 3 "
                  "medium, 2 low, as the rule set it.", "",
              "## What this run does not show", "",
              "- Real photographs: the pictures are rendered, sharp and straight; a phone or glasses camera adds "
              "blur, glare, angle and partial text, and the OCR and the image gate were not measured on any.",
              "- A trained image gate: the checkpoint's image answers come from its base model's vision tower, "
              "untrained on images; the floor is the text gate's.",
              "- v2, not used here: a Bedrock vision model in place of the OCR, under the same spend ledger, once the "
              "account's policy allows one. The Bedrock rule for this build is `openai.gpt-oss-20b` only.", ""]
    return "\n".join(lines)


def render2(data: dict[str, Any]) -> str:
    """The second run's report (ruling 50): the first report's tables over twenty-four pictures, and the scene."""
    parsed = json.loads(data["labels_json"])
    labels = {x["file"]: x for x in parsed["images"]}
    rows = data["images"]
    sens = [r for r in rows if r["set"] == "sensitive"]
    ordn = [r for r in rows if r["set"] == "ordinary"]
    n_sens = sum(1 for x in labels.values() if x["set"] == "sensitive")
    n_ord = sum(1 for x in labels.values() if x["set"] == "ordinary")
    held = [r for r in rows if r["status"] == "hold"]
    cats = {r["file"]: _category(r) for r in rows}
    asked = [r for r in rows if r["image"].get("scene") is not None]
    extracted = [r for r in rows if r["image"].get("scene") in ("owner_owes", "owed_to_owner")]
    lines = [
        "# Image evaluation, second run",
        "",
        "<!-- Generated by bench/run_images.py from bench/image_eval_2026-10-21_scene.json. Do not edit by hand. -->",
        f"Run {data['started']} to {data['finished']} by `bench/run_images.py --second` (git blob "
        f"`{data['runner_blob']}`), at commit `{data['code']}`.",
        "",
        "- **The second run (ruling 50):** the sixteen pictures of the first run and eight new ones, through the "
        "courier's photo path with the scene decision after the text gate. Every count here was measured in this "
        "run; the first report (`bench/IMAGE_EVAL_2026-10-21.md`) stands as measured, and nothing in it is restated "
        "here as if re-measured.",
        "- **An experiment with an untrained checkpoint:** Strands Decider was not trained on images (its model "
        "card); the image gate and the scene decision use the vision tower that ships inside its base model. The "
        "image gate's floor is the text gate's and the scene's is the routing read's for a choice question: "
        "starting points, not tuned values.",
        f"- **Desktop-fed:** the {len(labels)} pictures were rendered by code (`bench/images/render.py` and "
        "`render_v2.py`) and placed in the watched folder from the desktop, one at a time; no camera, no glasses. A "
        "real photo is a stated limit.",
        "- **Before the cloud:** a photo passes three checks on the laptop before any cloud call: the image gate "
        "on the picture, then the stop-list tripwire and the text gate on the text the OCR read from it. Then the "
        "scene is decided on the laptop, and only `owner_owes` and `owed_to_owner` go on to the extraction.",
        f"- **The labels:** `bench/images/labels_v2.json`, sha256 `{data['labels_sha256']}`, committed before this "
        "run's first model call, not edited after it; `labels.json` is unchanged.",
        f"- **Replay:** `{COMMAND}` draws this report again from `bench/image_eval_2026-10-21_scene.json`, with no "
        "network, no model and no key.",
        "",
        "| | |", "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in data["versions"].items()]
    lines.append("")
    if data["stopped"]:
        lines += [f"**Stopped (a partial run):** {data['stopped']}", ""]
    sent = lambda r: r["extraction_calls_spend_ledger"] > 0 or bool(r["written"])  # noqa: E731
    gate_cleared_sens = [r for r in sens if r["image"].get("gate_p") is not None and cats[r["file"]] not in
                         ("image gate", "unreadable")]
    lines += [f"## The counts (the second run, {len(rows)} pictures)", "", "| count | result |", "|---|---|",
              f"| sensitive pictures wrongly cleared (sent to Bedrock or written to Todoist) | "
              f"{_frac(sum(1 for r in sens if sent(r)), n_sens)} |",
              f"| sensitive pictures cleared by the image gate (then held, kept or written) | "
              f"{_frac(len(gate_cleared_sens), n_sens)} |"]
    for name in ("unreadable", "image gate", "tripwire", "text gate", "scene unsure", "task check", "leak guard",
                 "too short"):
        lines.append(f"| ordinary pictures held: {name} | {_frac(sum(1 for r in ordn if cats[r['file']] == name), n_ord)} |")
    lines.append(f"| ordinary pictures kept on the laptop as reference | "
                 f"{_frac(sum(1 for r in ordn if cats[r['file']] == 'kept as reference'), n_ord)} |")
    kept = [r for r in rows if cats[r["file"]] == "kept as reference"]
    lines += [f"| Bedrock extraction calls for held pictures (the courier's ledger / the spend ledger) | "
              f"{sum(r['image'].get('extraction_calls', 0) for r in held)} / "
              f"{sum(r['extraction_calls_spend_ledger'] for r in held)} (over {len(held)} held) |",
              f"| Bedrock extraction calls for pictures kept as reference | "
              f"{sum(r['image'].get('extraction_calls', 0) for r in kept)} / "
              f"{sum(r['extraction_calls_spend_ledger'] for r in kept)} (over {len(kept)}) |",
              f"| task writes for held pictures and reference pictures (the Todoist record) | "
              f"{sum(len(r['written']) for r in held + kept)} (over {len(held) + len(kept)}) |"]
    judged = {r["file"]: _judged(r, labels[r["file"]]) for r in extracted}
    verdicts = [judged[r["file"]][0] for r in extracted]
    projects = [p for r in extracted for p in judged[r["file"]][1]]
    lines += [f"| pictures sent to extraction: extraction right / OCR miss / model miss or extra | "
              f"{sum(v.startswith('right') for v in verdicts)} / {verdicts.count('OCR miss')} / "
              f"{sum(1 for v in verdicts if v.startswith('missed') or v == 'extra task')} (of {len(extracted)}) |",
              f"| project of the matched tasks: right / unsorted / wrong | "
              f"{sum(1 for p in projects if p.split(' / ')[0] == p.split(' / ')[1])} / "
              f"{sum(1 for p in projects if p.startswith('unsorted / ') and not p.endswith('/ unsorted'))} / "
              f"{sum(1 for p in projects if not p.startswith('unsorted / ') and p.split(' / ')[0] != p.split(' / ')[1])} "
              f"(of {len(projects)}) |"]
    every = lambda key: [v for r in rows for v in (r[key] if isinstance(r[key], list) else [r[key]])]  # noqa: E731
    lines += [f"| image gate read, median / p90 | {_ms([r['image']['gate_seconds'] for r in rows if 'gate_seconds' in r['image']])} |",
              f"| OCR, median / p90 | {_ms([r['image']['ocr_seconds'] for r in rows if 'ocr_seconds' in r['image']])} |",
              f"| text gate read, median / p90 | {_ms(every('text_gate_seconds'))} |",
              f"| scene read, median / p90 | {_ms([r['image']['scene_seconds'] for r in rows if 'scene_seconds' in r['image']])} |",
              f"| routing read, median / p90 | {_ms(every('routing_seconds'))} |"]
    for name, m in data["memory_end"].items():
        if m:
            lines.append(f"| {name} memory at the end | {m['resident_gb']} GB resident, {m['private_gb']} GB private |")
    spent = data["spend_after"]["total_usd"] - data["spend_before"]["total_usd"]
    lines += [f"| Bedrock spend | ${spent:.6f} over {data['spend_after']['calls'] - data['spend_before']['calls']} "
              f"call(s); the ledger at ${data['spend_after']['total_usd']:.6f} of the "
              f"${data['spend_after']['hard_stop_usd']:.2f} hard stop |", ""]
    lines += ["Sources: as in the first report (the courier's own ledger entry, the Bedrock spend ledger before and "
              "after each picture, the local Todoist record), and the courier's Telegram lines, kept by a local "
              "endpoint that answers as the Bot API does (nothing was sent to Telegram). The scene's answer, "
              "probability, time and action are the ledger's. The OCR's text was not kept by the run; a reference "
              "picture's text is kept by the courier itself, by design, in its state folder under `bench/raw/`, "
              "which git ignores.", ""]
    # --- the scene ---
    lines += ["## The scene decision", "",
              f"Asked of {len(asked)} pictures; the other {len(rows) - len(asked)} were held before it (by the image "
              "gate, the tripwire or the text gate) and are not in the table. Rows: the label; columns: the answer.",
              "", "| label \\ answer | " + " | ".join(SCENES) + " |", "|---|" + "---|" * len(SCENES)]
    for want in SCENES:
        cells = [sum(1 for r in asked if labels[r["file"]]["scene"] == want and r["image"]["scene"] == got)
                 for got in SCENES]
        lines.append(f"| {want} | " + " | ".join(str(c) for c in cells) + " |")
    lines += ["", f"Answer matches the label on {sum(1 for r in asked if labels[r['file']]['scene'] == r['image']['scene'])} "
                  f"of {len(asked)}.", "",
              "### Each scene read", "",
              "Decider's top choice and its confidence (the floor applies to this), and each option's probability as "
              "Decider gave it.", "",
              "| picture | label | top choice | confidence | owner_owes / owed_to_owner / reference | answer | action |",
              "|---|---|---|---|---|---|---|"]
    for r in asked:
        img = r["image"]
        options = img.get("scene_options") or {}
        probs = " / ".join(f"{options[k]:.2f}" if k in options else "-" for k in SCENES[:3])
        lines.append(f"| {r['file']} | {labels[r['file']]['scene']} | {img.get('scene_choice', '-')} | "
                     f"{img['scene_p']:.2f} | {probs} | {img['scene']} | {img.get('scene_action', '-')} |")
    lines += ["", "### Cloud calls by scene answer", "",
              "| scene answer | pictures | Bedrock extraction calls (the courier's ledger / the spend ledger) | expected |",
              "|---|---|---|---|"]
    expect = {"owner_owes": "one per picture", "owed_to_owner": "one per picture", "reference": "0", "unsure": "0"}
    for scene in SCENES:
        group = [r for r in asked if r["image"]["scene"] == scene]
        lines.append(f"| {scene} | {len(group)} | {sum(r['image'].get('extraction_calls', 0) for r in group)} / "
                     f"{sum(r['extraction_calls_spend_ledger'] for r in group)} | {expect[scene]} |")
    lines += ["", "### Actions taken", "",
              "| scene answer | action | tasks written | with `waiting-on` | reference notes kept | Telegram lines |",
              "|---|---|---|---|---|---|"]
    for scene in SCENES:
        group = [r for r in asked if r["image"]["scene"] == scene]
        if not group:
            lines.append(f"| {scene} | - | 0 | 0 | 0 | - |")
            continue
        actions = sorted({r["image"].get("scene_action", "-") for r in group})
        written = [w for r in group for w in r["written"]]
        tele = {}
        for r in group:
            for m in r.get("telegram", []):
                shape = m["first_line"].split(" · ")[0] + (" [" + ", ".join(m["buttons"]) + "]" if m["buttons"] else "")
                tele[shape] = tele.get(shape, 0) + 1
        lines.append(f"| {scene} | {'; '.join(actions)} | {len(written)} | "
                     f"{sum(1 for w in written if 'waiting-on' in (w['labels'] or []))} | "
                     f"{sum(1 for r in group if r.get('reference_note'))} | "
                     f"{'; '.join(f'{k} ×{v}' for k, v in tele.items()) or '-'} |")
    lines += ["", "### The mirrored pairs", "",
              "The same wording, the opposite direction: who owes whom is the whole difference.", "",
              "| pair | picture | label | image gate p | answer | p (owner_owes / owed_to_owner / reference) | action |",
              "|---|---|---|---|---|---|---|"]
    by_file = {r["file"]: r for r in rows}
    for i, pair in enumerate(parsed.get("mirrored", []), start=1):
        for name in pair:
            r = by_file.get(name)
            if r is None:
                lines.append(f"| {i} | {name} | {labels[name]['scene']} | - | not run | - | - |")
                continue
            img = r["image"]
            options = img.get("scene_options") or {}
            probs = " / ".join(f"{options[k]:.2f}" if k in options else "-" for k in SCENES[:3])
            answer = (f"{img['scene']} ({img['scene_p']:.2f})" if img.get("scene") else f"not asked ({cats[name]})")
            gate = f"{img['gate_p']:.2f}" if img.get("gate_p") is not None else "-"
            lines.append(f"| {i} | {name} | {labels[name]['scene']} | {gate} | {answer} | {probs} | "
                         f"{img.get('scene_action', '-')} |")
    owed = [r for r in asked if r["image"]["scene"] == "owed_to_owner"]
    lines += ["", "### owed_to_owner and the waiting question", "",
              "The rule runs unchanged on an `owed_to_owner` picture. Its someone_waiting question asks whether a "
              "specific person is expecting something from the owner, not whether the owner is waiting, so on these "
              "tasks it is expected to answer no, and the rule's waiting step does not set their priority.", ""]
    if owed:
        lines += ["| picture | label | someone_waiting p (each routing read) | priority written |", "|---|---|---|---|"]
        for r in owed:
            lines.append(f"| {r['file']} | {labels[r['file']]['scene']} | "
                         f"{', '.join(f'{p:.2f}' for p in r.get('routing_waiting_p', [])) or '-'} | "
                         f"{', '.join(str(w.get('priority') or '-') for w in r['written']) or '-'} |")
    else:
        lines.append("No picture was answered `owed_to_owner` in this run.")
    lines += ["", "## Per picture", "",
              "| picture | set | image gate p | tripwire | text gate p | scene label | scene answer (p) | outcome | "
              "extraction calls | writes | OCR recovery | extraction vs label | project written / labelled | "
              "priority written |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        label = labels[r["file"]]
        p = r["image"].get("gate_p")
        ran_tripwire = r["image"].get("ocr_seconds") is not None
        tripwire = ("held" if cats[r["file"]] == "tripwire" else "passed") if ran_tripwire else "-"
        text_p = ", ".join(f"{x:.2f}" for x in r["text_gate_p"]) or "-"
        scene = r["image"].get("scene")
        answer = "-" if not scene else (
            f"unsure (top {r['image'].get('scene_choice')}, {r['image']['scene_p']:.2f})" if scene == "unsure"
            else f"{scene} ({r['image']['scene_p']:.2f})")
        outcome = {"written": "written", "kept as reference": "kept as reference"}.get(cats[r["file"]]) or (
            f"held ({r['reason']}" + (f": {r['image']['unreadable']}" if r["image"].get("unreadable") else "") + ")")
        verdict, projects, priorities = _judged(r, label) if r in extracted else ("-", [], [])
        recovery = (f"{len(r['ocr_keywords_found'])} of {len(label['ocr_keywords'])}" if ran_tripwire else "-")
        lines.append(f"| {r['file']} | {r['set']} | {'-' if p is None else f'{p:.2f}'} | {tripwire} | {text_p} | "
                     f"{label['scene']} | {answer} | {outcome} | {r['image'].get('extraction_calls', 0)} / "
                     f"{r['extraction_calls_spend_ledger']} | {len(r['written'])} | {recovery} | {verdict} | "
                     f"{'; '.join(projects) or '-'} | {', '.join(priorities) or '-'} |")
    lines += ["", "Extraction calls: the courier's ledger / the spend ledger. Priority: Todoist's 4 is high, 3 "
                  "medium, 2 low, as the rule set it. Extraction is judged against the label only where the scene sent "
                  "the picture to it.", "",
              "## What this run does not show", "",
              "- Real photographs: the pictures are rendered, sharp and straight; a phone or glasses camera adds "
              "blur, glare, angle and partial text, and the OCR, the image gate and the scene were not measured on "
              "any.",
              "- A trained image gate or scene decision: the checkpoint's image answers come from its base model's "
              "vision tower, untrained on images; the floors are starting points.",
              "- The owner's taps: the run makes each decision and asks the owner where it is unsure; no tap was "
              "made, so what runs after a tap is shown by the tests, not here.",
              "- v2, not used here: a Bedrock vision model in place of the OCR, under the same spend ledger, once the "
              "account's policy allows one. The Bedrock rule for this build is `openai.gpt-oss-20b` only.", ""]
    return "\n".join(lines)


def _after_release(row: dict[str, Any]) -> dict[str, Any]:
    """The visit after the last release: what ran then, and with what."""
    released = [i for i, v in enumerate(row["visits"]) if v["status"] == "hold" and v["reason"] in RELEASE_REASONS]
    return row["visits"][released[-1] + 1] if released and released[-1] + 1 < len(row["visits"]) else {}


RELEASE_REASONS = ("image-unsure", "source-unsure")
MIN_WORDS = 8  # ratatoskr_demo/cycle.py MIN_TRANSCRIPT_WORDS (ruling 14), as the report states it


def _closed(row: dict[str, Any]) -> str:
    """What became of a released picture the scene was not asked about."""
    if row["status"] == "done" and row["reason"] == "too-short":
        return f"closed: under {MIN_WORDS} words (ruling 14)"
    return f"held ({row['reason']})"


def render3(data: dict[str, Any]) -> str:
    """The released pictures' report (ruling 52), from its export alone."""
    parsed = json.loads(data["labels_json"])
    labels = {x["file"]: x for x in parsed["images"]}
    rows = data["images"]
    asked = [r for r in rows if r["image"].get("scene") is not None]
    again = data.get("addendum") == 2  # ruling 53: the second addendum, with no word floor for photos
    export = "bench/image_eval_2026-10-22_released2.json" if again else "bench/image_eval_2026-10-22_released.json"
    lines = [
        "# Image evaluation, second run: the released pictures" + (", second addendum" if again else ""),
        "",
        f"<!-- Generated by bench/run_images.py from {export}. Do not edit by hand. -->",
        f"Run {data['started']} to {data['finished']} by `bench/run_images.py --released{'2' if again else ''}` "
        f"(git blob `{data['runner_blob']}`), at commit `{data['code']}`.",
        "",
    ]
    if again:
        lines += [
            "- **No word floor for photos (ruling 53, after the first addendum):** ruling 14's 8-word floor exists "
            "for recordings, where a press that captures fewer than 8 words is an accidental press, closed with no "
            "notice. A photo is never an accidental press in that sense: its guard is the readability check (it "
            "exists, decodes, is not blank, and now: its text has at least one word), and a whiteboard or a note "
            "with five words on it is the ordinary case. The floor reached photos because the OCR text enters the "
            "courier where a transcript does; the first addendum showed it closing four of its five pictures. "
            "Ruling 53 removed it for photos only; recordings are unchanged. The first addendum "
            "(`bench/IMAGE_EVAL_2026-10-22_released.md`) is unchanged.",
            "- **Fresh copies:** the same five pictures were placed again as fresh inbox copies, in a courier state "
            "of their own, on the same CPU servers.",
        ]
    lines += [
        f"- **Released by the owner (ruling 52):** the {len(RELEASED)} new ordinary pictures the second run's gates "
        "held as unsure (n1, n2, n7 and n8 by the image gate, n3 by the text gate) were placed again, one at a time, "
        "and each, where a gate held it as unsure, was released by Dan's tap on the Release button of its notice. "
        "The releases are the owner's evaluation action, on pictures the labels call ordinary. No picture held as "
        "sensitive was released: a sensitive hold has no button. The second run's other pictures were not run again.",
        "- **What left the laptop:** each Release notice went to the owner's Telegram chat and the tap came back from "
        "it; every other Telegram line and every Todoist write went to local records, as in the second run. Bedrock "
        "calls are counted below.",
        "- **An experiment with an untrained checkpoint:** Strands Decider was not trained on images; the image gate "
        "and the scene use the vision tower inside its base model, at floors that are starting points.",
        f"- **The labels:** `bench/images/labels_v2.json` as committed for the second run, sha256 "
        f"`{data['labels_sha256']}`, not edited.",
        ("- **The first three reports** (`bench/IMAGE_EVAL_2026-10-21.md`, `bench/IMAGE_EVAL_2026-10-21_scene.md` and "
         "`bench/IMAGE_EVAL_2026-10-22_released.md`) are not edited, and nothing in them is restated here as if "
         "re-measured." if again else
         "- **The first two reports** (`bench/IMAGE_EVAL_2026-10-21.md` and `bench/IMAGE_EVAL_2026-10-21_scene.md`) are not "
         "edited, and nothing in them is restated here as if re-measured."),
        f"- **Replay:** `{COMMAND}` draws this report again from `{export}`, with no network, no model and no key.",
        "",
        "| | |", "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in data["versions"].items()]
    lines.append("")
    if data["stopped"]:
        lines += [f"**Stopped (a partial run):** {data['stopped']}", ""]
    lines += ["## Each released picture", "",
              "| picture | label | held by (p) | released (by, at UTC) | then: OCR recovery | text gate p | scene answer "
              "(confidence) | owner_owes / owed_to_owner / reference | action | Bedrock calls | writes |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        label = labels[r["file"]]
        holds = []
        for v in r["visits"]:
            if v["status"] == "hold" and v["reason"] in RELEASE_REASONS:
                p = (v["picture_p"][-1] if v["reason"] == "image-unsure" and v["picture_p"] else
                     (max(v["text_gate_p"]) if v["text_gate_p"] else None))
                holds.append(f"{'image gate' if v['reason'] == 'image-unsure' else 'text gate'} "
                             f"({'-' if p is None else f'{p:.2f}'})")
        released = [f"{x.get('by') or 'not released'}, {(x.get('at') or '-').replace('+00:00', '')}"
                    for x in r["image"].get("releases") or []]
        after = _after_release(r)
        recovery = (f"{len(after['ocr_keywords_found'])} of {len(label['ocr_keywords'])}" if after.get("ocr_ran")
                    else "-")
        text_p = ", ".join(f"{x:.2f}" for x in after.get("text_gate_p", [])) or "not asked"
        img = r["image"]
        options = img.get("scene_options") or {}
        probs = " / ".join(f"{options[k]:.2f}" if k in options else "-" for k in SCENES[:3])
        scene = ("not asked" if not img.get("scene") else
                 f"unsure (top {img.get('scene_choice')}, {img['scene_p']:.2f})" if img["scene"] == "unsure" else
                 f"{img['scene']} ({img['scene_p']:.2f})")
        action = img.get("scene_action") or _closed(r)
        lines.append(f"| {r['file']} | {label['scene']} | {'; '.join(holds) or '-'} | {'; '.join(released) or '-'} | "
                     f"{recovery} | {text_p} | {scene} | {probs} | {action} | "
                     f"{img.get('extraction_calls', 0)} / {r['extraction_calls_spend_ledger']} | {len(r['written'])} |")
    lines += ["", "Bedrock calls: the courier's ledger / the spend ledger. After a release from the image gate the "
                  "photo goes to the OCR, the tripwire, the text gate and the scene; after a release from the text "
                  "gate, to the scene (its text read again on the laptop, the tripwire run again, neither gate "
                  "asked again)."]
    short = [r["file"] for r in rows if r["reason"] == "too-short"]
    if short:
        lines += ["", f"**Closed as too short:** {len(short)} of the {len(rows)} released pictures "
                      f"({', '.join(short)}) were closed after the OCR, before the text gate: their text is under "
                      f"{MIN_WORDS} words, which the courier reads as an accidental press (ruling 14) and closes with no "
                      "read, no notice and nothing written. The rule was made for recordings; it applies to a "
                      "photo's text unchanged, so these pictures never reached the scene."]
    lines += ["",
              "## The scene over the released pictures", "",
              f"Asked of {len(asked)} of {len(rows)}. Rows: the label; columns: the answer.", "",
              "| label \\ answer | " + " | ".join(SCENES) + " |", "|---|" + "---|" * len(SCENES)]
    for want in SCENES:
        cells = [sum(1 for r in asked if labels[r["file"]]["scene"] == want and r["image"]["scene"] == got)
                 for got in SCENES]
        lines.append(f"| {want} | " + " | ".join(str(c) for c in cells) + " |")
    lines += ["", f"Answer matches the label on {sum(1 for r in asked if labels[r['file']]['scene'] == r['image']['scene'])} "
                  f"of {len(asked)}.", "",
              "## The mirrored pairs", "",
              "The same wording, the opposite direction: who owes whom is the whole difference.", "",
              "| pair | picture | label | answer | p (owner_owes / owed_to_owner / reference) | action | writes |",
              "|---|---|---|---|---|---|---|"]
    by_file = {r["file"]: r for r in rows}
    for i, pair in enumerate(parsed.get("mirrored", []), start=1):
        for name in pair:
            r = by_file.get(name)
            if r is None:
                lines.append(f"| {i} | {name} | {labels[name]['scene']} | not run | - | - | - |")
                continue
            img = r["image"]
            options = img.get("scene_options") or {}
            probs = " / ".join(f"{options[k]:.2f}" if k in options else "-" for k in SCENES[:3])
            answer = (f"not asked ({_closed(r)})" if not img.get("scene") else
                      f"unsure (top {img.get('scene_choice')}, {img['scene_p']:.2f})" if img["scene"] == "unsure"
                      else f"{img['scene']} ({img['scene_p']:.2f})")
            lines.append(f"| {i} | {name} | {labels[name]['scene']} | {answer} | {probs} | "
                         f"{img.get('scene_action') or '-'} | {len(r['written'])} |")
    lines += ["", "## Cloud calls by scene answer", "",
              "| scene answer | pictures | Bedrock extraction calls (the courier's ledger / the spend ledger) | expected |",
              "|---|---|---|---|"]
    expect = {"owner_owes": "one per picture", "owed_to_owner": "one per picture", "reference": "0", "unsure": "0"}
    for scene in SCENES:
        group = [r for r in asked if r["image"]["scene"] == scene]
        lines.append(f"| {scene} | {len(group)} | {sum(r['image'].get('extraction_calls', 0) for r in group)} / "
                     f"{sum(r['extraction_calls_spend_ledger'] for r in group)} | {expect[scene]} |")
    held = [r for r in rows if r["image"].get("scene") is None]
    if held:
        lines.append(f"| not asked (closed as too short, or held by a later check) | {len(held)} | "
                     f"{sum(r['image'].get('extraction_calls', 0) for r in held)} / "
                     f"{sum(r['extraction_calls_spend_ledger'] for r in held)} | 0 |")
    if again:  # the second addendum also reports the writes and the actions, by class
        lines += ["", "## Actions and writes by scene answer", "",
                  "| scene answer | pictures | action | tasks written | with `waiting-on` | reference notes kept | "
                  "Telegram lines (local record) |", "|---|---|---|---|---|---|---|"]
        for scene in SCENES:
            group = [r for r in asked if r["image"]["scene"] == scene]
            written = [w for r in group for w in r["written"]]
            tele: dict[str, int] = {}
            for r in group:
                for m in r.get("telegram", []):
                    if m.get("to_owner"):
                        continue  # the Release notices: counted in the table above, not an action of the scene
                    shape = m["first_line"].split(" · ")[0] + (" [" + ", ".join(m["buttons"]) + "]" if m["buttons"]
                                                                else "")
                    tele[shape] = tele.get(shape, 0) + 1
            lines.append(f"| {scene} | {len(group)} | {'; '.join(sorted({r['image'].get('scene_action', '-') for r in group})) or '-'} | "
                         f"{len(written)} | {sum(1 for w in written if 'waiting-on' in (w['labels'] or []))} | "
                         f"{sum(1 for r in group if r.get('reference_note'))} | "
                         f"{'; '.join(f'{k} ×{v}' for k, v in tele.items()) or '-'} |")
    owed = [r for r in asked if r["image"]["scene"] == "owed_to_owner"]
    lines += ["", "## owed_to_owner and the waiting question", "",
              "The rule runs unchanged on an `owed_to_owner` picture; its someone_waiting question asks whether a "
              "specific person is waiting on the owner, so on these tasks it is expected to answer no.", ""]
    if owed:
        lines += ["| picture | someone_waiting p (each routing read) | labels written | priority written |",
                  "|---|---|---|---|"]
        for r in owed:
            after = _after_release(r)
            lines.append(f"| {r['file']} | {', '.join(f'{p:.2f}' for p in after.get('routing_waiting_p', [])) or '-'} | "
                         f"{'; '.join(', '.join(w['labels'] or []) for w in r['written']) or '-'} | "
                         f"{', '.join(str(w.get('priority') or '-') for w in r['written']) or '-'} |")
    else:
        lines.append("No released picture was answered `owed_to_owner`.")
    spent = data["spend_after"]["total_usd"] - data["spend_before"]["total_usd"]
    lines += ["", f"Bedrock spend: ${spent:.6f} over {data['spend_after']['calls'] - data['spend_before']['calls']} "
                  f"call(s); the ledger at ${data['spend_after']['total_usd']:.6f} of the "
                  f"${data['spend_after']['hard_stop_usd']:.2f} hard stop.", "",
              "## What this run does not show", "",
              "- Real photographs: the pictures are rendered, sharp and straight.",
              "- The gates' own reading of these pictures: each was released by the owner after a gate held it as "
              "unsure; what follows is what the later checks and the scene made of a picture the owner chose to let "
              "through.",
              "- A trained image gate or scene decision: the checkpoint's image answers come from its base model's "
              "vision tower, untrained on images.", ""]
    return "\n".join(lines)


SCENES2 = ("commitment", "reference", "unsure")  # the two-way scene (ruling 54) and unsure below its floor
EARLIER = ("bench/IMAGE_EVAL_2026-10-21.md", "bench/IMAGE_EVAL_2026-10-21_scene.md", "bench/IMAGE_EVAL_2026-10-22_released.md",
           "bench/IMAGE_EVAL_2026-10-22_released2.md")


def render4(data: dict[str, Any]) -> str:
    """The final image evaluation's report (ruling 54), from its export alone."""
    parsed = json.loads(data["labels_json"])
    labels = {x["file"]: x for x in parsed["images"]}
    rows = data["images"]
    sens = [r for r in rows if r["set"] == "sensitive"]
    ordn = [r for r in rows if r["set"] == "ordinary"]
    cats = {r["file"]: _category(r) for r in rows}
    asked = [r for r in rows if r["image"].get("scene") is not None]
    released = [(r, x) for r in rows for x in r["image"].get("releases") or []]
    lines = [
        "# Image evaluation, final run",
        "",
        "<!-- Generated by bench/run_images.py from bench/image_eval_2026-10-22_final.json. Do not edit by hand. -->",
        f"Run {data['started']} to {data['finished']} by `bench/run_images.py --final` (git blob "
        f"`{data['runner_blob']}`), at commit `{data['code']}`.",
        "",
        "- **After four photo reports (ruling 54):** this run follows " + ", ".join(f"`{x}`" for x in EARLIER) + ". "
        "Since the first two: a photo held as unsure by the image gate or the text gate can be released by the owner "
        "(ruling 52); the 8-word floor for recordings no longer applies to a photo (ruling 53); and the scene "
        "question is two-way, `commitment` (a promise in either direction) or `reference`, with unsure below its "
        "floor (ruling 54). Why the last: the second addendum showed the checkpoint could not read the direction of "
        "a promise from a picture and its text (a mirrored pair with identical probabilities, the other pair leaning "
        "the wrong way on both sides, and a note owed to the owner answered as owed by him at 0.71, above the "
        "floor, which changed the action). The direction is now read by the extraction and by the owner. What the "
        "measurement supported stays: reference against a commitment, and asking the owner when unsure. The four "
        "earlier reports are unchanged and still rebuild from their exports.",
        "- **Released by the owner:** every picture the labels call ordinary that a gate held as unsure was released "
        "by Dan's tap on the Release button of its notice, as an evaluation action. A picture the labels call "
        "sensitive was not released: a sensitive hold has no button, and a sensitive picture a gate held as unsure "
        "kept its notice on the local record, not offered to the owner.",
        "- **What left the laptop:** the Release notices of ordinary pictures went to the owner's Telegram chat and "
        "the taps came back from it; every other Telegram line and every Todoist write went to local records. "
        "Bedrock calls are counted below.",
        "- **An experiment with an untrained checkpoint:** Strands Decider was not trained on images; the image gate "
        "and the scene use the vision tower inside its base model, at floors that are starting points.",
        f"- **The labels:** `bench/images/labels_v3.json`, derived from `labels_v2.json` by "
        f"`bench/images/labels_v3.py` (owner_owes and owed_to_owner to commitment), sha256 "
        f"`{data['labels_sha256']}`, committed before this run's first model call, not edited after it.",
        f"- **Replay:** `{COMMAND}` draws this report again from `bench/image_eval_2026-10-22_final.json`, with no "
        "network, no model and no key.",
        "",
        "| | |", "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in data["versions"].items()]
    lines.append("")
    if data["stopped"]:
        lines += [f"**Stopped (a partial run):** {data['stopped']}", ""]
    first_hold = {r["file"]: next((v for v in r["visits"] if v["status"] == "hold"), None) for r in rows}
    gate_unsure = [r for r in rows if (first_hold[r["file"]] or {}).get("reason") == "image-unsure"]
    gate_sensitive = [r for r in rows if (first_hold[r["file"]] or {}).get("reason") == "image-sensitive"]
    sent = lambda r: r["extraction_calls_spend_ledger"] > 0 or bool(r["written"])  # noqa: E731
    lines += [f"## The counts ({len(rows)} pictures)", "", "| count | result |", "|---|---|",
              f"| sensitive pictures sent to Bedrock or written to Todoist | "
              f"{_frac(sum(1 for r in sens if sent(r)), len(sens))} |",
              f"| sensitive pictures held by the image gate: as sensitive / as unsure (not released) | "
              f"{sum(1 for r in gate_sensitive if r['set'] == 'sensitive')} / "
              f"{sum(1 for r in gate_unsure if r['set'] == 'sensitive')} of {len(sens)} |",
              f"| ordinary pictures the image gate held as unsure (then released) | "
              f"{_frac(sum(1 for r in gate_unsure if r['set'] == 'ordinary'), len(ordn))} |",
              f"| releases by the owner (pictures / taps) | {len({r['file'] for r, x in released if x.get('by') == 'owner'})}"
              f" / {sum(1 for _, x in released if x.get('by') == 'owner')} |"]
    for name in ("unreadable", "image gate", "tripwire", "text gate", "scene unsure", "task check", "leak guard"):
        lines.append(f"| ordinary pictures held at the end: {name} | "
                     f"{_frac(sum(1 for r in ordn if cats[r['file']] == name), len(ordn))} |")
    lines += [f"| ordinary pictures kept on the laptop as reference | "
              f"{_frac(sum(1 for r in ordn if cats[r['file']] == 'kept as reference'), len(ordn))} |",
              f"| ordinary pictures written (a commitment) | "
              f"{_frac(sum(1 for r in ordn if cats[r['file']] == 'written'), len(ordn))} |"]
    every = lambda key: [v for r in rows for visit in r["visits"] for v in visit.get(key, [])]  # noqa: E731
    lines += [f"| image gate read, median / p90 | {_ms([r['image']['gate_seconds'] for r in rows if 'gate_seconds' in r['image']])} |",
              f"| text gate read, median / p90 | {_ms(every('text_gate_seconds'))} |",
              f"| scene read, median / p90 | {_ms([r['image']['scene_seconds'] for r in rows if 'scene_seconds' in r['image']])} |",
              f"| routing read, median / p90 | {_ms(every('routing_seconds'))} |"]
    for name, m in data["memory_end"].items():
        if m:
            lines.append(f"| {name} memory at the end | {m['resident_gb']} GB resident, {m['private_gb']} GB private |")
    spent = data["spend_after"]["total_usd"] - data["spend_before"]["total_usd"]
    lines += [f"| Bedrock spend | ${spent:.6f} over {data['spend_after']['calls'] - data['spend_before']['calls']} "
              f"call(s); the ledger at ${data['spend_after']['total_usd']:.6f} of the "
              f"${data['spend_after']['hard_stop_usd']:.2f} hard stop |", ""]
    lines += ["## The releases", "", "| picture | set | held by (p) | released by | at (UTC) |", "|---|---|---|---|---|"]
    for r, x in released:
        hold = next((v for v in r["visits"] if v["status"] == "hold" and v["reason"] in RELEASE_REASONS
                     and ("image" if v["reason"] == "image-unsure" else "text") in x.get("held_by", "")), None)
        p = None if hold is None else (hold["picture_p"][-1] if hold["reason"] == "image-unsure" and hold["picture_p"]
                                       else (max(hold["text_gate_p"]) if hold["text_gate_p"] else None))
        lines.append(f"| {r['file']} | {r['set']} | {x.get('held_by')} ({'-' if p is None else f'{p:.2f}'}) | "
                     f"{x.get('by') or 'not released'} | {(x.get('at') or '-').replace('+00:00', '')} |")
    lines += ["", "## The scene, two-way", "",
              f"Asked of {len(asked)} pictures. Rows: the label; columns: the answer.", "",
              "| label \\ answer | " + " | ".join(SCENES2) + " |", "|---|" + "---|" * len(SCENES2)]
    for want in SCENES2:
        cells = [sum(1 for r in asked if labels[r["file"]]["scene"] == want and r["image"]["scene"] == got)
                 for got in SCENES2]
        lines.append(f"| {want} | " + " | ".join(str(c) for c in cells) + " |")
    lines += ["", f"Answer matches the label on "
                  f"{sum(1 for r in asked if labels[r['file']]['scene'] == r['image']['scene'])} of {len(asked)}.", "",
              "## Per picture", "",
              "| picture | set | label | image gate p | released | text gate p | scene answer (confidence) | "
              "commitment / reference | action | Bedrock calls | writes |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        img = r["image"]
        gate_p = next((v["picture_p"][0] for v in r["visits"] if v["picture_p"]), img.get("gate_p"))
        text_p = next((v["text_gate_p"] for v in reversed(r["visits"]) if v["text_gate_p"]), [])
        rel = "; ".join(f"{x.get('held_by')}: {x.get('by') or 'not released'}" for x in img.get("releases") or []) or "-"
        options = img.get("scene_options") or {}
        probs = " / ".join(f"{options[k]:.2f}" if k in options else "-" for k in SCENES2[:2])
        scene = ("-" if not img.get("scene") else
                 f"unsure (top {img.get('scene_choice')}, {img['scene_p']:.2f})" if img["scene"] == "unsure" else
                 f"{img['scene']} ({img['scene_p']:.2f})")
        action = img.get("scene_action") or (f"held ({r['reason']})" if r["status"] == "hold" else _closed(r))
        lines.append(f"| {r['file']} | {r['set']} | {labels[r['file']]['scene']} | "
                     f"{'-' if gate_p is None else f'{gate_p:.2f}'} | {rel} | "
                     f"{', '.join(f'{x:.2f}' for x in text_p) or '-'} | {scene} | {probs} | {action} | "
                     f"{img.get('extraction_calls', 0)} / {r['extraction_calls_spend_ledger']} | {len(r['written'])} |")
    lines += ["", "Bedrock calls: the courier's ledger / the spend ledger. The text gate p is the last read of the text "
                  "gate; a picture released from the text gate is not asked again.", "",
              "## Cloud calls and writes by scene answer", "",
              "| scene answer | pictures | action | Bedrock extraction calls (the courier's ledger / the spend ledger) | "
              "tasks written | reference notes kept | expected |", "|---|---|---|---|---|---|---|"]
    expect = {"commitment": "a call, a write or more, per picture", "reference": "no call, no write",
              "unsure": "no call, no write"}
    for scene in SCENES2:
        group = [r for r in asked if r["image"]["scene"] == scene]
        lines.append(f"| {scene} | {len(group)} | "
                     f"{'; '.join(sorted({r['image'].get('scene_action', '-') for r in group})) or '-'} | "
                     f"{sum(r['image'].get('extraction_calls', 0) for r in group)} / "
                     f"{sum(r['extraction_calls_spend_ledger'] for r in group)} | "
                     f"{sum(len(r['written']) for r in group)} | {sum(1 for r in group if r.get('reference_note'))} | "
                     f"{expect[scene]} |")
    not_asked = [r for r in rows if not r["image"].get("scene")]
    lines.append(f"| not asked (held before the scene) | {len(not_asked)} | - | "
                 f"{sum(r['image'].get('extraction_calls', 0) for r in not_asked)} / "
                 f"{sum(r['extraction_calls_spend_ledger'] for r in not_asked)} | "
                 f"{sum(len(r['written']) for r in not_asked)} | 0 | no call, no write |")
    by_file = {r["file"]: r for r in rows}
    lines += ["", "## The mirrored pairs", "",
              "The same wording, the opposite direction. Both should be a commitment; who owes whom is the "
              "extraction's to read.", "",
              "| pair | picture | answer | commitment / reference | task written |", "|---|---|---|---|---|"]
    for i, pair in enumerate(parsed.get("mirrored", []), start=1):
        for name in pair:
            r = by_file.get(name)
            if r is None:
                continue
            img = r["image"]
            options = img.get("scene_options") or {}
            probs = " / ".join(f"{options[k]:.2f}" if k in options else "-" for k in SCENES2[:2])
            answer = ("not asked" if not img.get("scene") else
                      f"unsure (top {img.get('scene_choice')}, {img['scene_p']:.2f})" if img["scene"] == "unsure" else
                      f"{img['scene']} ({img['scene_p']:.2f})")
            lines.append(f"| {i} | {name} | {answer} | {probs} | "
                         f"{'; '.join(str(w['content']) for w in r['written']) or '-'} |")
    lines += ["", "## What this run does not show", "",
              "- Real photographs: the pictures are rendered, sharp and straight.",
              "- The gates' own reading of the released pictures: each was let through by the owner after a gate held "
              "it as unsure.",
              "- A trained image gate or scene decision: the checkpoint's image answers come from its base model's "
              "vision tower, untrained on images.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run", action="store_true", help="run the evaluation (both servers up) and write the export")
    parser.add_argument("--check", action="store_true", help="compare the report from the export with the committed one")
    parser.add_argument("--decider-pid", type=int, default=None, help="the text server's pid, for its memory")
    parser.add_argument("--vision-pid", type=int, default=None, help="the vision server's pid, for its memory")
    parser.add_argument("--conditions", default="", help="what else was (not) running on the machine")
    parser.add_argument("--second", action="store_true",
                        help="the second run (ruling 50): labels_v2.json, the scene decision, the 2026-10-21_scene files")
    parser.add_argument("--released", action="store_true",
                        help="the released pictures (ruling 52): the owner's taps, the 2026-10-22_released files")
    parser.add_argument("--released2", action="store_true",
                        help="the second addendum (ruling 53): no word floor for photos, the _released2 files")
    parser.add_argument("--final", action="store_true",
                        help="the final run (ruling 54): all twenty-four, the two-way scene, the _final files")
    args = parser.parse_args(argv)
    if args.check:  # every report whose export exists, each against its own renderer
        failed = 0
        for export, report, draw in ((EXPORT, REPORT, render), (EXPORT2, REPORT2, render2),
                                     (EXPORT3, REPORT3, render3), (EXPORT4, REPORT4, render3),
                                     (EXPORT5, REPORT5, render4)):
            if not export.exists():
                continue
            same = draw(json.loads(export.read_text(encoding="utf-8"))) == report.read_text(encoding="utf-8")
            failed += not same
            print(f"{report.relative_to(REPO)}: regenerated from {export.relative_to(REPO)}; "
                  + ("identical, byte for byte" if same else "DIFFERS"))
        return 1 if failed else 0
    export, report, draw = ((EXPORT5, REPORT5, render4) if args.final else
                            (EXPORT4, REPORT4, render3) if args.released2 else
                            (EXPORT3, REPORT3, render3) if args.released else
                            (EXPORT2, REPORT2, render2) if args.second else (EXPORT, REPORT, render))
    if args.run:
        data = run_released(args) if args.released or args.released2 or args.final else run(args)
        export.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        print(f"wrote {export.relative_to(REPO)}")
    data = json.loads(export.read_text(encoding="utf-8"))
    report.write_bytes(draw(data).encode("utf-8"))
    print(f"wrote {report.relative_to(REPO)}" + (f"; STOPPED: {data['stopped']}" if data["stopped"] else ""))
    return 1 if data["stopped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
