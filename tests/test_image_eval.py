"""Dan's ruling 47: the image evaluation's runner. Its rules (the OCR's key words, an OCR miss told apart from a
model miss), a report drawn from a synthetic export, and, once the run is committed, the report regenerated from
bench/image_eval_2026-10-22.json byte for byte with no network, no model and no key."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_images", REPO / "bench" / "run_images.py")
run_images = importlib.util.module_from_spec(spec)
sys.modules["run_images"] = run_images
spec.loader.exec_module(run_images)
LABELS = json.loads((REPO / "bench" / "images" / "labels.json").read_text(encoding="utf-8"))


def test_the_set_is_sixteen_pictures_eight_and_eight_with_labels_for_every_one():
    images = LABELS["images"]
    assert len(images) == 16 and sum(x["set"] == "sensitive" for x in images) == 8
    assert all((REPO / "bench" / "images" / x["file"]).is_file() for x in images)
    assert all(x["expected_gate"] == ("hold" if x["set"] == "sensitive" else "clear") for x in images)
    assert all(x["ocr_keywords"] for x in images) and all(not x["expected_tasks"] for x in images
                                                         if x["set"] == "sensitive")


def test_the_ocrs_key_words_are_found_ignoring_case_and_spaces():
    assert run_images.recovered("byThursday29Oct\nPay the COACH deposit", ["Thursday", "coach", "test plan"]) == [
        "Thursday", "coach"]
    assert run_images.recovered("circulate thetestplan", ["test plan"]) == ["test plan"]


def row(file, status="done", reason=None, written=(), found=None, gate_p=0.2):
    label = next(x for x in LABELS["images"] if x["file"] == file)
    return {"file": file, "set": label["set"], "id": "photo-x", "status": status, "reason": reason,
            "image": {"gate_p": gate_p, "gate_seconds": 3.0, "ocr_seconds": 1.5, "extraction_calls": 1,
                      "ocr_confidence": [0.98]},
            "extraction_calls_spend_ledger": 1, "written": list(written),
            "ocr_keywords_found": label["ocr_keywords"] if found is None else found,
            "text_gate_p": [0.1], "text_gate_seconds": [2.0], "routing_seconds": [11.0], "seconds": 20.0}


def test_an_ocr_miss_is_told_apart_from_a_model_miss():
    label = next(x for x in LABELS["images"] if x["file"] == "o2_whiteboard_deposit.png")
    written = [{"content": "Book twelve coach seats", "labels": ["pocket"], "priority": 3}]
    assert run_images._judged(row(label["file"], written=written, found=["coach"]), label)[0] == "OCR miss"
    assert run_images._judged(row(label["file"], written=written), label)[0] == "missed 1 of 1"
    hit = [{"content": "Pay the coach deposit to Fenella", "labels": ["pocket", "proj:community"], "priority": 4}]
    assert run_images._judged(row(label["file"], written=hit), label) == ("right", ["community / community"], ["4"])


def test_a_report_is_drawn_from_an_export_alone():
    rows = [row(x["file"]) if x["set"] == "ordinary" else
            row(x["file"], status="hold", reason="image-sensitive", gate_p=0.8) for x in LABELS["images"]]
    for r in rows:
        if r["status"] == "hold":
            r["image"] = {"gate_p": 0.8, "gate_seconds": 3.0, "extraction_calls": 0}
            r.update(extraction_calls_spend_ledger=0, text_gate_p=[], text_gate_seconds=[], routing_seconds=[])
    data = {"labels_json": json.dumps(LABELS), "labels_sha256": "0" * 16, "code": "abc1234", "runner_blob": "b" * 16,
            "started": "2026-10-22 00:00 UTC", "finished": "2026-10-22 00:10 UTC", "stopped": None,
            "versions": {"hardware": "a laptop"}, "memory_end": {"text server": None, "vision server": None},
            "spend_before": {"total_usd": 0.1, "calls": 1}, "spend_after": {"total_usd": 0.11, "calls": 9,
                                                                          "hard_stop_usd": 5.0},
            "images": rows}
    text = run_images.render(data)
    assert "| sensitive pictures wrongly cleared (written to Todoist) | 0 of 8 |" in text
    assert "| Bedrock extraction calls for held pictures (the courier's ledger / the spend ledger) | 0 / 0" in text
    assert "untrained" in text and "Desktop-fed" in text and "three checks on the laptop before any cloud call" in text
    assert text.count("\n| o") + text.count("\n| s") >= 16


@pytest.mark.skipif(not run_images.EXPORT.exists(), reason="the image evaluation has not been run yet")
def test_the_committed_report_regenerates_from_its_export_byte_for_byte():
    data = json.loads(run_images.EXPORT.read_text(encoding="utf-8"))
    assert run_images.render(data) == run_images.REPORT.read_text(encoding="utf-8")


# --- the second run (Dan's ruling 50) ---

LABELS_V2 = json.loads((REPO / "bench" / "images" / "labels_v2.json").read_text(encoding="utf-8"))


def row2(label, *, scene=None, p=0.8):
    """A second-run row as the runner records it: the scene from the label, or `scene` where given."""
    base = {"file": label["file"], "set": label["set"], "id": "photo-x", "extraction_calls_spend_ledger": 0,
            "written": [], "ocr_keywords_found": label["ocr_keywords"], "text_gate_p": [0.1], "text_gate_seconds": [2.0],
            "routing_seconds": [], "seconds": 20.0, "telegram": [], "routing_waiting_p": [], "reference_note": False}
    if label["set"] == "sensitive":
        return base | {"status": "hold", "reason": "image-sensitive", "text_gate_p": [], "text_gate_seconds": [],
                       "image": {"gate_p": 0.8, "gate_seconds": 3.0, "extraction_calls": 0}}
    scene = scene or label["scene"]
    image = {"gate_p": 0.2, "gate_seconds": 3.0, "ocr_seconds": 1.5, "ocr_confidence": [0.98], "extraction_calls": 0,
             "scene": scene, "scene_p": p if scene != "unsure" else 0.55, "scene_seconds": 4.0, "scene_by": "decider",
             "scene_options": {"owner_owes": 0.1, "owed_to_owner": 0.1, "reference": 0.8},
             "scene_action": {"owner_owes": "extracted", "owed_to_owner": "extracted, waiting-on",
                              "reference": "kept on the laptop", "unsure": "asked the owner"}[scene]}
    if scene == "reference":
        return base | {"status": "done", "reason": "reference", "image": image, "reference_note": True,
                       "telegram": [{"first_line": "reference · kept on the laptop", "buttons": []}]}
    if scene == "unsure":
        return base | {"status": "hold", "reason": "scene-unsure", "image": image,
                       "telegram": [{"first_line": "scene unsure · 0.55", "buttons": ["I owe", "They owe", "Reference"]}]}
    labels = ["pocket"] + (["waiting-on"] if scene == "owed_to_owner" else [])
    written = [{"content": " ".join(t["match"][0][0] for _ in range(1)) + " task", "labels": labels, "priority": None}
               for t in label["expected_tasks"]] or [{"content": "Met an unnamed contact — no follow-ups",
                                                       "labels": labels, "priority": None}]
    return base | {"status": "done", "reason": None, "image": image | {"extraction_calls": 1},
                   "extraction_calls_spend_ledger": 1, "written": written, "routing_seconds": [11.0],
                   "routing_waiting_p": [0.2] if scene == "owed_to_owner" else [],
                   "telegram": [{"first_line": ("waiting-on · - · 0.80" if scene == "owed_to_owner" else
                                                "unsorted · - · 0.40"), "buttons": []}]}


def test_the_second_report_is_drawn_from_an_export_alone():
    rows = [row2(x) for x in LABELS_V2["images"]]
    data = {"labels_json": json.dumps(LABELS_V2), "labels_sha256": "0" * 16, "code": "abc1234", "runner_blob": "b" * 16,
            "started": "2026-10-22 00:00 UTC", "finished": "2026-10-22 00:10 UTC", "stopped": None,
            "versions": {"hardware": "a laptop", "scene": "the scene question"},
            "memory_end": {"text server": None, "vision server": None},
            "spend_before": {"total_usd": 0.1, "calls": 1},
            "spend_after": {"total_usd": 0.11, "calls": 9, "hard_stop_usd": 5.0}, "images": rows}
    text = run_images.render2(data)
    assert text.startswith("# Image evaluation, second run") and "nothing in it is restated" in text
    assert "| sensitive pictures wrongly cleared (sent to Bedrock or written to Todoist) | 0 of 8 |" in text
    assert "| label \\ answer | owner_owes | owed_to_owner | reference | unsure |" in text
    assert "| reference | 6 | 0 / 0 | 0 |" in text and "| unsure | 1 | 0 / 0 | 0 |" in text  # no cloud call
    assert "### The mirrored pairs" in text and "n2_owed_whiteboard.png" in text and "n7_owes_whiteboard.png" in text
    assert "| scene read, median / p90 | 4.00 s / 4.00 s (16) |" in text
    assert "Answer matches the label on 16 of 16." in text
    assert text.count("\n| o") + text.count("\n| s") + text.count("\n| n") >= 24


@pytest.mark.skipif(not run_images.EXPORT2.exists(), reason="the second image evaluation has not been run yet")
def test_the_second_committed_report_regenerates_from_its_export_byte_for_byte():
    data = json.loads(run_images.EXPORT2.read_text(encoding="utf-8"))
    assert run_images.render2(data) == run_images.REPORT2.read_text(encoding="utf-8")
