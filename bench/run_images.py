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
    python bench/run_images.py --check                               the same, compared with the committed report

The report is drawn from the export alone, so the second and third need nothing the run needed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import statistics
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / "bench" / "images"
EXPORT = REPO / "bench" / "image_eval_2026-10-22.json"
REPORT = REPO / "bench" / "IMAGE_EVAL_2026-10-22.md"
COMMAND = "python bench/run_images.py --check"
GATE_REASONS = ("image-sensitive", "image-unsure")


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
            return response

        client.systemone = timed


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

    labels_raw = (IMAGES / "labels.json").read_bytes()
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
    record = bench.TodoistRecord()
    courier = replace(settings, fixtures_dir=None, paths=replace(settings.paths, state_dir=state_dir),
                      devices=replace(settings.devices, inputs=("glasses",), outputs=("todoist",)),
                      secrets=replace(settings.secrets, todoist_api_token="local-record-only"))
    runtime = build_runtime(courier, dry_run=False, bedrock=bedrock, router=router, source_gate=gate,
                            http=httpx.Client(transport=httpx.MockTransport(record.handler)))
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
        print(f"  {label['file']}: {rows[-1]['status']} ({rows[-1]['reason']}); image gate p "
              f"{rows[-1]['image'].get('gate_p')}; extraction calls {rows[-1]['extraction_calls_spend_ledger']}; "
              f"writes {len(rows[-1]['written'])}", flush=True)
        if entry is None or entry.status not in ("done", "hold"):
            stopped = f"{label['file']}: the courier left no final ledger entry; the run stops here (ruling 47)"
            break
    from importlib.metadata import version

    return {
        "about": ("Ruling 47's image evaluation, as the run recorded it. bench/run_images.py draws "
                  "bench/IMAGE_EVAL_2026-10-22.md from this file alone. Fictional pictures only."),
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
        return "too short" if reason == "too-short" else "written"  # too short: the OCR found under 8 words
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
        "<!-- Generated by bench/run_images.py from bench/image_eval_2026-10-22.json. Do not edit by hand. -->",
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
        f"- **Replay:** `{COMMAND}` draws this report again from `bench/image_eval_2026-10-22.json`, with no "
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run", action="store_true", help="run the evaluation (both servers up) and write the export")
    parser.add_argument("--check", action="store_true", help="compare the report from the export with the committed one")
    parser.add_argument("--decider-pid", type=int, default=None, help="the text server's pid, for its memory")
    parser.add_argument("--vision-pid", type=int, default=None, help="the vision server's pid, for its memory")
    parser.add_argument("--conditions", default="", help="what else was (not) running on the machine")
    args = parser.parse_args(argv)
    if args.run:
        data = run(args)
        EXPORT.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        print(f"wrote {EXPORT.relative_to(REPO)}")
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    text = render(data)
    if args.check:
        same = text == REPORT.read_text(encoding="utf-8")
        print(f"{REPORT.relative_to(REPO)}: regenerated from {EXPORT.relative_to(REPO)}; "
              + ("identical, byte for byte" if same else "DIFFERS"))
        return 0 if same else 1
    REPORT.write_bytes(text.encode("utf-8"))
    print(f"wrote {REPORT.relative_to(REPO)}" + (f"; STOPPED: {data['stopped']}" if data["stopped"] else ""))
    return 1 if data["stopped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
