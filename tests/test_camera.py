"""Dan's ruling 58, Part A: phone photographs, and the camera evaluation's harness, before the photographs exist.

The input: a JPEG is read upright (its EXIF orientation honoured) with its long side at most 2000 px, from a copy in
memory, the file untouched; a file over 12 MB is held as too large, and a HEIC file as an unsupported format (this
machine's Pillow, 13.0.0, has no HEIF plugin), both before any model with the id-only notice. The harness: the
`--camera` mode's owner loop (a Release, then the scene's answer, until the photograph settles), its Telegram (only an
ordinary photograph's Release and scene notices reach the owner), the report drawn from an export alone, the files
named by the run's UTC start date, the replay check over the sixth report, and every sha256 an export stores written
in four groups. Ruling 57's carry-over: the owner-release qualification beside the unsure invariant.
"""

import base64
import importlib.util
import io
import json
import re
import sys
import threading
from pathlib import Path

import pytest
import respx
from PIL import Image

from ratatoskr_demo import photos
from ratatoskr_demo.devices.glasses_adapter import IMAGE_SUFFIXES, photo_id
from tests.test_photos import VISION, FakeOcr, Vision, adapter, photo_cycle
from tests.test_release import OWNER, Run
from tests.test_release_chained import scene_tap

REPO = Path(__file__).resolve().parents[1]
if "run_images" not in sys.modules:
    spec = importlib.util.spec_from_file_location("run_images", REPO / "bench" / "run_images.py")
    sys.modules["run_images"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sys.modules["run_images"])
run_images = sys.modules["run_images"]
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 200


def phone_photo(path, size=(640, 480), orientation=None):
    """A JPEG as a phone stores it: its pixels as the sensor read them, a red block at the stored top left, and the
    EXIF orientation that turns it upright."""
    img = Image.linear_gradient("L").resize(size).convert("RGB")
    img.paste((255, 0, 0), (0, 0, size[0] // 5, size[1] // 5))
    exif = Image.Exif()
    if orientation:
        exif[photos.ORIENTATION] = orientation
    img.save(path, "JPEG", quality=90, exif=exif.tobytes())
    return path


def decoded(data):
    return Image.open(io.BytesIO(data))


def read_one(folder, vision, ocr):
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{VISION}/v1/systemone").mock(side_effect=vision.reply)
        a = adapter(folder, vision, ocr)
        [envelope] = a.list_new()
        return a, a.source(envelope).item_id, a.fetch_text(envelope)


def photo_folder(tmp_path):
    folder = tmp_path / "inbox" / "photos"
    folder.mkdir(parents=True)
    return folder


# --- the input (Part A, item 2) ---

def test_a_rotated_phone_photo_is_read_upright_from_a_copy_and_the_file_is_kept(tmp_path):
    folder = photo_folder(tmp_path)
    path = phone_photo(folder / "c90.jpg", size=(640, 480), orientation=6)  # stored sideways; upright it is 480x640
    stored = path.read_bytes()
    vision, ocr = Vision(0.2), FakeOcr()
    a, rid, text = read_one(folder, vision, ocr)
    gate = decoded(base64.b64decode(vision.bodies[0]["images"][0]))
    assert gate.size == (480, 640)  # the image gate read it upright
    assert gate.convert("RGB").getpixel((470, 10))[0] > 200  # the stored top left is now the top right
    assert isinstance(ocr.calls[0], bytes) and decoded(ocr.calls[0]).size == (480, 640)  # and so did the OCR
    assert path.read_bytes() == stored  # the file in the folder is not touched
    record = a.records[rid]
    assert (record["pixels"], record["orientation"], record["read_at"]) == ("640x480", 6, "480x640")
    assert record["sha256"] == photos.file_sha256(path)  # a release is recorded against the file, not the copy


def test_an_oversized_phone_photo_is_read_at_the_long_side_and_the_file_is_kept(tmp_path):
    folder = photo_folder(tmp_path)
    path = phone_photo(folder / "c91.jpg", size=(4080, 3072))
    stored = path.read_bytes()
    assert len(stored) <= photos.MAX_BYTES
    vision, ocr = Vision(0.2), FakeOcr()
    a, rid, text = read_one(folder, vision, ocr)
    gate = decoded(base64.b64decode(vision.bodies[0]["images"][0]))
    assert max(gate.size) == photos.LONG_SIDE == 2000 and abs(gate.size[0] / gate.size[1] - 4080 / 3072) < 0.01
    assert decoded(ocr.calls[0]).size == gate.size
    assert a.records[rid]["read_at"] == "x".join(map(str, gate.size)) and a.records[rid]["pixels"] == "4080x3072"
    assert path.read_bytes() == stored and text == "\n".join(FakeOcr().lines)


def test_a_picture_already_upright_and_within_the_long_side_is_read_as_its_own_bytes(tmp_path):
    folder = photo_folder(tmp_path)
    path = phone_photo(folder / "c92.jpg", size=(900, 300))
    vision, ocr = Vision(0.2), FakeOcr()
    read_one(folder, vision, ocr)
    assert base64.b64decode(vision.bodies[0]["images"][0]) == path.read_bytes()  # what earlier runs were sent
    assert ocr.calls == [str(path)]


def test_a_file_up_to_12_mb_is_accepted_and_one_over_it_is_held_as_too_large_before_any_model(tmp_path):
    folder = photo_folder(tmp_path)
    path = phone_photo(folder / "c93.jpg")
    with path.open("ab") as fh:  # bytes after the picture's end: a decoder reads the picture and stops
        fh.write(b"\0" * (photos.MAX_BYTES - path.stat().st_size))
    assert path.stat().st_size == photos.MAX_BYTES == 12 * 1024 * 1024
    assert photos.unreadable(path) is None
    with path.open("ab") as fh:
        fh.write(b"\0")
    vision, ocr = Vision(0.2), FakeOcr()
    a, rid, held = read_one(folder, vision, ocr)
    assert held == photos.PhotoHold("unreadable") and a.records[rid] == {"extraction_calls": 0, "unreadable": "too large"}
    assert vision.bodies == [] and ocr.calls == []


def test_a_heic_file_is_listed_and_held_as_an_unsupported_format_before_any_model(tmp_path):
    assert ".heic" not in Image.registered_extensions()  # this machine's Pillow cannot open HEIC: refused, as unsupported
    folder = photo_folder(tmp_path)
    (folder / "c94.heic").write_bytes(HEIC)
    assert ".heic" in IMAGE_SUFFIXES  # listed, so it is held with its notice rather than skipped unseen
    vision, ocr = Vision(0.2), FakeOcr()
    a, rid, held = read_one(folder, vision, ocr)
    assert held == photos.PhotoHold("unreadable") and a.records[rid]["unreadable"] == "unsupported format"
    assert vision.bodies == [] and ocr.calls == []


@pytest.mark.parametrize("name, content, why", [
    ("c94.heic", lambda path: path.write_bytes(HEIC), "unsupported format"),
    ("c93.jpg", lambda path: path.write_bytes(b"\xff\xd8" + b"\0" * (photos.MAX_BYTES + 1)), "too large"),
])
def test_a_refused_photo_gets_the_id_only_notice_through_the_cycle(tmp_path, services, clock, name, content, why):
    entry, model, fake, vision, todoist, telegram = photo_cycle(tmp_path, services, clock, p=0.2, name=name,
                                                                content=content)
    assert (entry["status"], entry["reason"], entry["image"]["unreadable"]) == ("hold", "unreadable", why)
    assert vision.bodies == [] and model.calls == [] and fake.gate_bodies == [] and todoist.tasks == {}
    assert telegram.texts == [f"Held {photo_id(Path(name))}: unreadable. Nothing was written."]  # the id, no name


# --- the harness (Part A, items 1, 3, 4, 5) ---

def test_the_labels_template_has_one_entry_per_expected_photograph():
    template = json.loads((REPO / "bench" / "camera" / "labels_template.json").read_text(encoding="utf-8"))
    assert [x["file"] for x in template["images"]] == [f"{i}.jpg" for i in run_images.CAMERA_IDS]
    assert run_images.CAMERA_IDS == tuple(f"c{i:02d}" for i in range(1, 13))
    fields = {"file", "original", "shows", "set", "handwritten", "angled", "expected_gate", "ocr_keywords",
              "expected_tasks", "scene", "scene_unsure_accepted"}
    assert all(set(x) == fields for x in template["images"])
    assert (REPO / "bench" / "camera" / "README.md").is_file()


def test_every_sha256_an_export_stores_is_written_in_four_groups_of_sixteen():
    sha = "ab" * 32
    image = {"sha256": sha, "gate_p": 0.5,
             "releases": [{"held_by": "image gate", "sid": "0123456789", "by": "owner", "sha256": sha}]}
    out = run_images.as_exported(image)
    grouped = " ".join(sha[i:i + 16] for i in range(0, 64, 16))
    assert out["sha256"] == grouped and out["releases"][0]["sha256"] == grouped
    assert image["sha256"] == sha  # the ledger's record is not changed
    assert run_images.as_exported(out) == out  # already grouped: left as it is
    assert not re.search(r"[0-9a-f]{40,}", json.dumps(out))  # nothing the secret scan reads as a key


def owner_telegram(forward):
    import httpx

    out = []

    class Real:
        def post(self, url, content, headers):
            out.append(json.loads(content))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    telegram = run_images._OwnerTelegram(Real())
    telegram.kinds, telegram.forward = ("release:", "scene:"), forward
    url = "https://api.telegram.org/botTOKEN/sendMessage"
    for message in ({"chat_id": 1, "text": "Held photo-x: image-unsure. Nothing was written.",
                     "reply_markup": photos.release_keyboard("abc")},
                    {"chat_id": 1, "text": "scene unsure · 0.55", "reply_markup": photos.scene_keyboard("def")},
                    {"chat_id": 1, "text": "reference · kept on the laptop"}):
        telegram.handler(httpx.Request("POST", url, json=message))
    return telegram, out


def test_the_camera_run_offers_the_owner_only_an_ordinary_photographs_release_and_scene_notices():
    telegram, out = owner_telegram(forward=True)
    assert [m["text"] for m in out] == ["Held photo-x: image-unsure. Nothing was written.", "scene unsure · 0.55"]
    assert [m["to_owner"] for m in telegram.sent] == [True, True, False]
    telegram, out = owner_telegram(forward=False)  # a photograph the labels call sensitive: all of it stays local
    assert out == [] and [m["to_owner"] for m in telegram.sent] == [False, False, False]


def test_the_camera_runs_poller_hands_release_and_scene_taps_and_nothing_else_to_the_resolver():
    stop, handled = threading.Event(), []
    batches = [[{"update_id": 1, "message": {"text": "/status"}},
                {"update_id": 2, "callback_query": {"data": "pick:abc:research"}},
                {"update_id": 3, "callback_query": {"data": "release:abc"}},
                {"update_id": 4, "callback_query": {"data": "scene:def:commitment"}}]]

    def get_updates(offset):
        if batches:
            return batches.pop()
        stop.set()
        return []

    class Resolver:
        def handle(self, query):
            handled.append(query["data"])

    run_images._poll_releases(get_updates, Resolver(), stop,
                              lambda data: photos.is_release_tap(data) or photos.is_scene_tap(data))
    assert handled == ["release:abc", "scene:def:commitment"]


def settle(run, *, answer="commitment", taps=None):
    """The camera run's loop over the courier of tests/test_release.py: the owner's taps as the loop asks for them."""
    asked = [] if taps is None else taps

    def visit():
        entry = run.cycle()
        return {"status": entry["status"], "reason": entry.get("reason")}

    def wait(rid, step):
        asked.append(step)
        if step == "release":
            run.tap("image gate" if run.runtime.state.ledger_get(rid).reason == "image-unsure" else "text gate")
        else:
            scene_tap(run, answer)
        return True

    from tests.test_release import RID

    return run_images._settle(visit, run.runtime.state.ledger_get, RID, wait=wait)


@pytest.mark.parametrize("held, kwargs", [("image-unsure", dict(p=0.5)), ("source-unsure", dict(p=0.2, text_gate=0.5))])
def test_a_photograph_settles_through_the_owners_release_and_then_his_scene_answer(tmp_path, services, clock, held,
                                                                                  kwargs):
    run, taps = Run(tmp_path, services, clock, scene_p=0.5, **kwargs), []
    visits, offers, stopped = settle(run, taps=taps)
    assert stopped is None and taps == ["release", "scene"]
    assert offers == [{"step": "release", "on": held, "taken": True}, {"step": "scene", "on": "scene-unsure", "taken": True}]
    assert [(v["status"], v["reason"]) for v in visits] == [("hold", held), ("hold", "scene-unsure"), ("done", None)]
    assert len(run.model.calls) == 1 and len(run.todoist.tasks) == 1  # the chained path of ruling 57, written
    image = run.runtime.state.ledger_get(photo_id(Path("whiteboard.png"))).image
    assert (image["scene_by"], image["releases"][0]["by"]) == ("owner", "owner")
    assert image["scene_at"].startswith("2026-") and image["releases"][0]["at"].startswith("2026-")  # with their times


def test_every_unsure_hold_is_offered_and_one_the_owner_leaves_settles_held_while_a_missing_tap_stops_the_run(
        tmp_path, services, clock):
    rid = photo_id(Path("whiteboard.png"))
    run = Run(tmp_path / "a", services, clock, p=0.5)  # an unsure hold: offered, whatever the label (ruling 58C)
    visits, offers, stopped = run_images._settle(lambda: {"status": run.cycle()["status"]}, run.runtime.state.ledger_get,
                                                 rid, wait=lambda item, step: None)  # the owner leaves it held
    assert len(visits) == 1 and stopped is None
    assert offers == [{"step": "release", "on": "image-unsure", "taken": False}]
    run = Run(tmp_path / "b", services, clock, p=0.5)
    visits, offers, stopped = run_images._settle(lambda: {"status": run.cycle()["status"]}, run.runtime.state.ledger_get,
                                                 rid, wait=lambda item, step: False)  # no tap on an ordinary photograph
    assert len(visits) == 1 and stopped == "no release tap within 45 minutes" and offers[0]["taken"] is False
    run = Run(tmp_path / "c", services, clock, p=0.8)  # a yes verdict: no button, so nothing is offered
    visits, offers, stopped = run_images._settle(lambda: {"status": run.cycle()["status"]}, run.runtime.state.ledger_get,
                                                 rid, wait=lambda item, step: True)
    assert len(visits) == 1 and offers == [] and stopped is None


def test_the_label_sets_only_how_long_the_harness_waits_on_an_offer():
    assert run_images._patience({"set": "ordinary"}) == (45 * 60, False)  # the owner answers; no tap stops the run
    assert run_images._patience({"set": "sensitive"}) == (run_images.DECLINE_SECONDS, None) == (30, None)


def test_the_camera_files_are_named_by_the_utc_date_the_run_started(tmp_path):
    export, report = run_images.camera_files("2026-10-24 01:12 UTC", tmp_path)
    assert (export.name, report.name) == ("image_eval_2026-10-24_camera.json", "IMAGE_EVAL_2026-10-24_camera.md")
    (tmp_path / "image_eval_2026-10-23_camera.json").write_text(json.dumps({"started": "2026-10-24 01:12 UTC"}),
                                                                encoding="utf-8")
    with pytest.raises(SystemExit, match="rename it"):  # an export named for another day is refused, not rendered
        run_images.camera_pairs(tmp_path)


LABELS = {"about": "test", "images": [
    {"file": "c01.jpg", "set": "ordinary", "scene": "commitment", "handwritten": True, "angled": "yes",
     "expected_gate": "clear", "ocr_keywords": ["English", "Tues"], "expected_tasks": [
         {"match": [["english"]], "project": "school"}], "scene_unsure_accepted": False},
    {"file": "c03.jpg", "set": "ordinary", "scene": "commitment", "handwritten": True, "angled": "slight",
     "expected_gate": "clear", "ocr_keywords": ["Brittany", "Practice"], "expected_tasks": [
         {"match": [["brittany"]], "project": "personal"}], "scene_unsure_accepted": True},
    {"file": "c05.jpg", "set": "ordinary", "scene": "reference", "handwritten": True, "angled": "no",
     "expected_gate": "clear", "ocr_keywords": ["GARAGE", "SALE"], "expected_tasks": [], "scene_unsure_accepted": False},
    {"file": "c04.jpg", "set": "sensitive", "scene": None, "handwritten": True, "angled": "no",
     "expected_gate": "hold", "ocr_keywords": ["Therapy"], "expected_tasks": [], "scene_unsure_accepted": False},
]}


def visit(status, reason=None, *, picture_p=(), ocr_ran=False, found=(), text_p=(), scene=None):
    out = {"status": status, "reason": reason, "picture_p": list(picture_p), "ocr_ran": ocr_ran,
           "ocr_keywords_found": list(found), "text_gate_p": list(text_p), "text_gate_seconds": [1.5] * len(text_p),
           "scene_reads": 1 if scene else 0, "routing_seconds": [], "routing_waiting_p": []}
    if scene:
        out.update(scene=scene[0], scene_choice=scene[1], scene_p=scene[2], scene_options={}, scene_seconds=2.5,
                   scene_by="decider")
    return out


def camera_export():
    image = {"pixels": "3072x4080", "orientation": 1, "read_at": "1506x2000", "gate_seconds": 2.7, "ocr_seconds": 1.1}
    release = {"held_by": "image gate", "sid": "x", "by": "owner", "at": "2026-10-24T01:20:00+00:00",
               "sha256": "0123456789abcdef " * 3 + "0123456789abcdef"}
    rows = [
        {"file": "c01.jpg", "set": "ordinary", "id": "photo-1", "status": "done", "reason": None,
         "image": image | {"gate_p": 0.45, "releases": [release], "scene": "commitment", "scene_by": "owner",
                           "scene_at": "2026-10-24T01:22:00+00:00", "scene_action": "extracted", "extraction_calls": 1},
         "visits": [visit("hold", "image-unsure", picture_p=[0.45]),
                    visit("hold", "scene-unsure", ocr_ran=True, found=["English", "Tues"], text_p=[0.2],
                          scene=("unsure", "commitment", 0.55)),
                    visit("done")],
         "ocr_ran": True, "ocr_keywords_found": ["English", "Tues"], "extraction_calls_spend_ledger": 1,
         "written": [{"content": "Finish the English comp essay", "labels": ["pocket", "proj:school"], "priority": 4}],
         "telegram": [{"first_line": "Held photo-1: image-unsure.", "buttons": ["Release"], "to_owner": True},
                      {"first_line": "scene unsure · 0.55", "buttons": ["Commitment", "Reference"], "to_owner": True}],
         "offers": [{"step": "release", "on": "image-unsure", "taken": True},
                    {"step": "scene", "on": "scene-unsure", "taken": True}],
         "reference_note": False},
        {"file": "c03.jpg", "set": "ordinary", "id": "photo-3", "status": "hold", "reason": "scene-unsure",
         "image": image | {"gate_p": 0.2, "scene": "unsure", "scene_by": "decider", "scene_p": 0.6,
                           "scene_action": "asked the owner", "extraction_calls": 0},
         "visits": [visit("hold", "scene-unsure", picture_p=[0.2], ocr_ran=True, found=["Practice"], text_p=[0.3],
                          scene=("unsure", "reference", 0.6))],
         "ocr_ran": True, "ocr_keywords_found": ["Practice"], "extraction_calls_spend_ledger": 0, "written": [],
         "telegram": [], "offers": [], "reference_note": False},
        {"file": "c05.jpg", "set": "ordinary", "id": "photo-5", "status": "done", "reason": None,
         "image": image | {"gate_p": 0.1, "scene": "commitment", "scene_by": "decider", "scene_p": 0.8,
                           "scene_action": "extracted", "extraction_calls": 1},
         "visits": [visit("done", picture_p=[0.1], ocr_ran=True, found=["GARAGE", "SALE"], text_p=[0.1],
                          scene=("commitment", "commitment", 0.8))],
         "ocr_ran": True, "ocr_keywords_found": ["GARAGE", "SALE"], "extraction_calls_spend_ledger": 1,
         "written": [{"content": "Go to the garage sale", "labels": ["pocket"], "priority": 1}],
         "telegram": [], "offers": [], "reference_note": False},
        {"file": "c04.jpg", "set": "sensitive", "id": "photo-4", "status": "hold", "reason": "image-unsure",
         "image": image | {"gate_p": 0.55, "extraction_calls": 0, "releases": [
             {"held_by": "image gate", "sid": "y", "by": None, "at": None, "sha256": release["sha256"]}]},
         "visits": [visit("hold", "image-unsure", picture_p=[0.55])], "ocr_ran": False, "ocr_keywords_found": [],
         "extraction_calls_spend_ledger": 0, "written": [],
         "telegram": [{"first_line": "Held photo-4: image-unsure.", "buttons": ["Release"], "to_owner": True}],
         "offers": [{"step": "release", "on": "image-unsure", "taken": False}],  # offered; the owner left it held
         "reference_note": False},
    ]
    return {"camera": True, "labels_file": "bench/camera/labels.json", "labels_json": json.dumps(LABELS),
            "labels_sha256": "0" * 16, "code": "abc1234", "runner_blob": "b" * 16, "started": "2026-10-24 01:10 UTC",
            "finished": "2026-10-24 01:40 UTC", "stopped": None,
            "floors": {"image gate": 0.6, "text gate": 0.6, "scene": 0.7}, "input": {"long_side": 2000,
                                                                                    "max_bytes": photos.MAX_BYTES},
            "owner_wait": {"ordinary_seconds": 2700, "sensitive_seconds": 30},
            "versions": {"input": "phone photographs"}, "memory_end": {},
            "spend_before": {"total_usd": 0.1, "calls": 1},
            "spend_after": {"total_usd": 0.11, "calls": 3, "hard_stop_usd": 5.0}, "images": rows}


def test_the_camera_report_is_drawn_from_an_export_alone():
    text = run_images.render5(camera_export())
    assert text.startswith("# Image evaluation, camera photographs")
    assert "from bench/image_eval_2026-10-24_camera.json. Do not edit by hand." in text
    for words in ("photographs taken by the owner on a phone", "content no earlier run saw",  # ruling 58C, 3
                  "handwritten, some angled, in ordinary room light", "generalization to new content",
                  "the same frozen gates, floors, question, and checkpoint as `bench/IMAGE_EVAL_2026-10-22_final.md`",
                  "Every unsure hold offered", "whatever the label", "whether to release followed the labels",  # 58C, 2
                  "The first run's results are the results", "Simulated integrations",
                  "Ruling 57's chain, live",
                  "A successful tap resolves the uncertainty; it does not make the first model answer correct"):  # 58C, 1
        assert words in text, words
    assert "| automatic completions (finished with no owner action: written, or kept as reference) | 1 of 4 |" in text
    assert "| owner-assisted completions (finished after the owner's release or scene answer) | 1 of 4 |" in text
    assert "| held at the end: with no offer / offered, not taken by the owner | 1 / 1 of 4 |" in text
    assert "| commitment photographs written as their task (the written task matches the label's) | 1 of 2 |" in text
    assert "| reference photographs kept on the laptop | 0 of 1 |" in text  # c05 was extracted: an extra task
    assert ("The owner took 1 of the 1 Release offers on photographs the labels call ordinary and left 1 of the 1 offers "
            "on photographs the labels call sensitive") in text
    assert "The owner's scene answers matched the labels on 1 of 1." in text
    assert "| owner actions: releases taken / scene answers / offers left | 1 / 1 / 1 |" in text
    assert "| confident disagreements with the label at the first model answer (photographs / answers) | 1 / 1 |" in text
    assert "| OCR misses, of the photographs the OCR read: a key word missed / no words | 1 / 0 of 3 |" in text
    assert "| Telegram lines to the owner: commitment / reference / sensitive photographs | 2 / 0 / 1 |" in text
    assert "| ruling 57's chain: occurrences / with no gate or scene asked again and no duplicate write | 1 / 1 |" in text
    assert ("| c01.jpg | commitment: a task (school) | unsure 0.45 | clear 0.20 | unsure (top commitment, 0.55); - / - "
            "| released (image gate); scene: Commitment | extracted, 1 task(s) written | 1 / 1 | 1 | no gate asked "
            "again, the scene asked once, no duplicate write |") in text
    assert ("| c04.jpg | held (sensitive or unsure) | unsure 0.55 | - | not asked | offered Release (image gate), not "
            "taken by the owner | held (image-unsure) | 0 / 0 | 0 | - |") in text
    assert "| c05.jpg | reference | extra task | scene commitment (0.80) |" in text
    assert "| c01.jpg | commitment | Release | image gate (0.45) | released | 2026-10-24T01:20:00 |" in text
    assert "| c04.jpg | sensitive | Release | image gate (0.55) | not taken | - |" in text
    assert "| c01.jpg | commitment | Commitment or Reference | scene (top commitment, 0.55) | chose Commitment |" in text
    assert "of which the label accepts unsure on c03.jpg" in text
    assert "| c03.jpg | commitment | yes | slight | 3072x4080 | 1 | 1506x2000 | 1 of 2 | missed Brittany |" in text


def test_the_chain_is_marked_when_a_gate_was_asked_twice_or_a_write_repeated():
    row = camera_export()["images"][0]
    assert run_images._chain(row) == "no gate asked again, the scene asked once, no duplicate write"
    twice = dict(row, visits=[*row["visits"][:2], dict(row["visits"][2], picture_p=[0.45])])  # the gate asked again
    assert run_images._chain(twice).startswith("a gate asked again")
    repeated = dict(row, written=row["written"] * 2)
    assert run_images._chain(repeated).endswith("a duplicate write")
    assert run_images._chain(camera_export()["images"][2]) is None  # no owner action: not the chain


def test_the_replay_check_covers_the_sixth_report(tmp_path, monkeypatch, capsys):
    data = camera_export()
    export, report = run_images.camera_files(data["started"], tmp_path)
    export.write_text(json.dumps(data, indent=2), encoding="utf-8")
    report.write_bytes(run_images.render5(data).encode("utf-8"))
    assert run_images.camera_pairs(tmp_path) == [(export, report)]
    monkeypatch.setattr(run_images, "camera_pairs", lambda folder=None: [(export, report)])
    assert run_images.main(["--check"]) == 0
    assert "IMAGE_EVAL_2026-10-24_camera.md: regenerated from" in capsys.readouterr().out
    report.write_text(report.read_text(encoding="utf-8") + "edited\n", encoding="utf-8")
    assert run_images.main(["--check"]) == 1 and "DIFFERS" in capsys.readouterr().out


def test_every_committed_camera_report_regenerates_from_its_export_byte_for_byte():
    for export, report in run_images.camera_pairs():
        assert run_images.render5(json.loads(export.read_text(encoding="utf-8"))) == report.read_text(encoding="utf-8")


# --- the photographs and their labels (Part B) ---

CAMERA_LABELS = REPO / "bench" / "camera" / "labels.json"


@pytest.mark.skipif(not CAMERA_LABELS.exists(), reason="the camera labels are not written yet")
def test_the_camera_labels_cover_the_twelve_photographs_four_of_each_class_bound_to_their_files():
    import hashlib

    from ratatoskr_demo.config import load_settings
    from ratatoskr_demo.triage import stop_list_hit

    labels = json.loads(CAMERA_LABELS.read_text(encoding="utf-8"))["images"]
    assert [x["file"] for x in labels] == [f"{i}.jpg" for i in run_images.CAMERA_IDS]
    kinds = [run_images._kind(x) for x in labels]
    assert {k: kinds.count(k) for k in set(kinds)} == {"commitment": 4, "reference": 4, "sensitive": 4}
    for x in labels:
        path = REPO / "bench" / "camera" / x["file"]
        data = path.read_bytes()
        assert " ".join(re.findall(".{16}", hashlib.sha256(data).hexdigest())) == x["sha256"], x["file"]
        with Image.open(path) as image:  # the copy: no location, nothing after the picture, upright as stored
            exif = image.getexif()
            assert 0x8825 not in exif and not exif.get_ifd(0x8825) and exif.get(photos.ORIENTATION, 1) == 1
            assert image.format == "JPEG" and "x".join(map(str, image.size)) == x["pixels"]
        assert data.endswith(b"\xff\xd9") and b"ftyp" not in data and len(data) <= photos.MAX_BYTES
        sensitive = x["set"] == "sensitive"
        assert x["expected_gate"] == ("hold" if sensitive else "clear") and x["ocr_keywords"]
        assert (x["scene"] is None) == sensitive and bool(x["expected_tasks"]) == (x["scene"] == "commitment")
        assert stop_list_hit("photo", x["shows"] + "\n" + " ".join(x["ocr_keywords"]),
                             load_settings().triage.stop_list) is None  # the names are on no stop list
    assert [x["file"] for x in labels if x["scene_unsure_accepted"]] == ["c03.jpg"]


# --- ruling 57's carry-over (Part A, item 4) ---

def test_the_owner_release_qualification_stands_beside_the_unsure_invariant_wherever_it_is_stated():
    notebook = "".join("".join(c["source"]) for c in json.loads(
        (REPO / "notebooks" / "gatekeeper_triage.ipynb").read_text(encoding="utf-8"))["cells"])
    for name, text in (("notebook", notebook), ("CONTRIBUTING.md", (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")),
                       ("README.md", (REPO / "README.md").read_text(encoding="utf-8"))):
        flat = " ".join(text.split())
        assert "An explicit, recorded owner release (the Release button on a photo held as unsure) authorizes" in flat, name
        assert "a model clear and an owner release are never confused" in flat, name


def test_a_note_the_owner_records_after_the_run_is_shown_and_changes_no_result():
    data = camera_export()
    plain = run_images.render5(data)
    data["owner_notes"] = [{"file": "c01.jpg", "recorded_at": "2026-10-24 02:00 UTC",
                            "note": "The owner's answer was a mis-tap."}]
    noted = run_images.render5(data)
    assert "## Notes recorded after the run" in noted and "- **c01.jpg** (2026-10-24 02:00 UTC): The owner's answer was a mis-tap." in noted
    assert "scene: Commitment (see the note)" in noted and "## Notes recorded after the run" not in plain
    summary = lambda text: text[text.index("## Summary"):text.index("## Per photograph")]  # noqa: E731
    assert summary(noted) == summary(plain)  # the counts are the run's as recorded
