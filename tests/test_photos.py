"""Dan's ruling 47: the photo path. A photo is read, gated on the vision server and OCR'd on the laptop before any
cloud call, and held, with an id-only notice, when it cannot be read or the image gate says yes or is unsure.

One test per adapter branch (a new file, unreadable, blank, gate yes, gate unsure, gate no, the reader failing),
the gate's floor and band from route.toml, the folder watcher, and the courier's cycle end to end. Ruling 49: a
photo held after the image gate (the tripwire, the text gate, the check after extraction, the leak guard) is told
it stays in the inbox folder, one test per notice site.
"""

import json
import os
import time
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image, ImageDraw, ImageFont

from ratatoskr_demo import photos
from ratatoskr_demo.config import Settings
from ratatoskr_demo.decider import DeciderClient
from ratatoskr_demo.devices.envelope import Envelope, ImageRef, schema
from ratatoskr_demo.devices.glasses_adapter import PhotoFolder, photo_id
from ratatoskr_demo.errors import TerminalError, TransientError
from ratatoskr_demo.route import load_route_questions
from tests.conftest import ScriptedModel, extraction_json, make_settings, runtime_for
from tests.test_decider import attach, decider_answers

VISION = "http://127.0.0.1:8001"
SENSITIVE = load_route_questions(Settings())["sensitive"]
TEXT = ["Send Ilona the revised antenna drawings", "by Thursday 29 Oct", "Book the clean room for the fit check"]


def picture(path, lines=TEXT):
    img = Image.new("RGB", (900, 300), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((20, 20 + 60 * i), line, fill="black", font=ImageFont.load_default(size=36))
    img.save(path)
    return path


class FakeOcr:
    def __init__(self, lines=None, fail=None):
        self.lines, self.fail, self.calls = lines if lines is not None else TEXT, fail, []

    def __call__(self, path):
        self.calls.append(path)
        if self.fail:
            raise self.fail
        return [[[[0, 0]], line, 0.97] for line in self.lines], [0.1]


class Vision:
    """The vision server's /v1/systemone: the picture question answered with p, the scene question (ruling 50)
    with `scene` at `scene_p`."""

    def __init__(self, p=0.2, status=200, scene="commitment", scene_p=0.9):
        self.p, self.status, self.bodies = p, status, []
        self.scene, self.scene_p = scene, scene_p

    def reply(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "simulated"})
        answers = {}
        if "picture" in body["questions"]:
            answers["picture"] = {"type": "noul", "noul": self.p}
        if "scene" in body["questions"]:
            rest = (1 - self.scene_p) / 2
            answers["scene"] = {"type": "choice", "choice": self.scene, "confidence": self.scene_p,
                                "probabilities": {k: (self.scene_p if k == self.scene else rest)
                                                  for k in photos.SCENE_CRITERIA}}
        return httpx.Response(200, json={"model": "strands-decider-2B-hobson-v21", "answers": answers,
                                         "usage": {"input_tokens": 260, "output_tokens": 1}, "latency_ms": 3500.0})


def folder_with(tmp_path, *names):
    folder = tmp_path / "inbox" / "photos"
    folder.mkdir(parents=True)
    for name in names:
        picture(folder / name)
    return folder


def adapter(folder, vision, ocr=None):
    gate = photos.ImageGate(DeciderClient(VISION, http=httpx.Client()), SENSITIVE)
    return PhotoFolder(folder, gate, photos.Ocr(ocr or FakeOcr()))


def run_one(tmp_path, folder, vision, ocr=None):
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{VISION}/v1/systemone").mock(side_effect=vision.reply)
        a = adapter(folder, vision, ocr)
        [envelope] = a.list_new()
        rid = a.source(envelope).item_id
        return a, rid, a.fetch_text(envelope)


# --- the adapter's branches ---

def test_a_new_photo_is_listed_as_a_glasses_envelope_with_an_image_ref_and_a_neutral_title(tmp_path):
    folder = folder_with(tmp_path, "whiteboard.png")
    (folder / "notes.txt").write_text("not a photo", encoding="utf-8")  # not an image name: never listed
    a = adapter(folder, Vision())
    [envelope] = a.list_new()
    assert envelope.kind == "glasses" and isinstance(envelope.payload, ImageRef)
    assert envelope.payload.uri.startswith("file:///") and schema_valid(envelope)
    source = a.source(envelope)
    assert source.item_id == photo_id(folder / "whiteboard.png") and source.title == "photo"
    assert "whiteboard" not in source.item_id  # the id never carries the file's name


def schema_valid(envelope):
    import jsonschema

    jsonschema.validate(envelope.to_json(), schema())
    return Envelope.from_json(envelope.to_json()) == envelope


@pytest.mark.parametrize("make, why", [
    (lambda folder: (folder / "gone.png").unlink(), "missing"),
    (lambda folder: (folder / "gone.png").write_bytes(b"plain text, not an image\n" * 40), "not an image"),
    (lambda folder: Image.new("RGB", (640, 480), "white").save(folder / "gone.png"), "blank"),
    (lambda folder: Image.open(folder / "gone.png").save(folder / "gone.png", format="BMP"), "unsupported format"),
])
def test_an_unreadable_photo_holds_before_any_model(tmp_path, make, why):
    folder = folder_with(tmp_path, "gone.png")
    vision, ocr = Vision(), FakeOcr()
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{VISION}/v1/systemone").mock(side_effect=vision.reply)
        a = adapter(folder, vision, ocr)
        [envelope] = a.list_new()
        make(folder)
        held = a.fetch_text(envelope)
    assert held == photos.PhotoHold("unreadable")
    assert vision.bodies == [] and ocr.calls == []  # no model asked, nothing read
    assert a.records[photo_id(folder / "gone.png")] == {"extraction_calls": 0, "unreadable": why}


@pytest.mark.parametrize("p, reason", [(0.80, "image-sensitive"), (0.55, "image-unsure"), (0.45, "image-unsure")])
def test_the_image_gate_holds_on_yes_and_on_unsure_and_the_ocr_never_runs(tmp_path, p, reason):
    ocr = FakeOcr()
    a, rid, held = run_one(tmp_path, folder_with(tmp_path, "card.png"), Vision(p), ocr)
    assert held == photos.PhotoHold(reason, p) and ocr.calls == []
    assert a.records[rid]["gate_p"] == p


def test_a_cleared_photo_is_read_on_the_laptop_and_its_text_becomes_the_transcript(tmp_path):
    vision, ocr = Vision(0.2), FakeOcr()
    a, rid, text = run_one(tmp_path, folder_with(tmp_path, "card.png"), vision, ocr)
    assert text == "\n".join(TEXT) and len(ocr.calls) == 1
    [body] = vision.bodies  # the picture question, the text gate's criteria, one image, no state
    assert body["state"] == "" and len(body["images"]) == 1
    assert body["questions"]["picture"]["instructions"] == photos.PICTURE_QUESTION
    assert body["questions"]["picture"]["criteria"] == SENSITIVE.criteria
    record = a.records[rid]
    assert record["gate_p"] == 0.2 and record["ocr_confidence"] == [0.97, 0.97, 0.97] and "ocr_seconds" in record


def test_the_reader_failing_is_terminal_and_the_vision_server_down_waits(tmp_path):
    with pytest.raises(TerminalError, match="OCR could not read it"):
        run_one(tmp_path / "a", folder_with(tmp_path / "a", "card.png"), Vision(0.2), FakeOcr(fail=RuntimeError()))
    with pytest.raises(TransientError, match="image gate"):
        run_one(tmp_path / "b", folder_with(tmp_path / "b", "card.png"), Vision(status=503))


@pytest.mark.parametrize("p, status", [(0.60, "sensitive"), (0.59, "unsure"), (0.41, "unsure"), (0.40, "clear"),
                                       (0.05, "clear"), (0.95, "sensitive")])
def test_the_gate_uses_the_text_gates_floor_and_band_from_route_toml(p, status):
    assert SENSITIVE.min_confidence == 0.60  # route.toml [sensitive]: the text gate's floor, a starting point
    gate = photos.ImageGate(DeciderClient(VISION, http=httpx.Client()), SENSITIVE)
    assert gate.floor == SENSITIVE.min_confidence and photos.judge(p, gate.floor) == status


def test_the_watcher_lists_photos_as_they_arrive_oldest_first(tmp_path):
    folder = folder_with(tmp_path, "b.png")
    a = adapter(folder, Vision())
    assert len(a.list_new()) == 1
    picture(folder / "a.jpg")
    os.utime(folder / "a.jpg", (time.time() + 60, time.time() + 60))
    listed = [a.source(e).item_id for e in a.list_new()]
    assert listed == [photo_id(folder / "b.png"), photo_id(folder / "a.jpg")]
    assert adapter(tmp_path / "no-such-folder", Vision()).list_new() == []


def test_the_vision_url_must_be_on_this_machine(tmp_path):
    from ratatoskr_demo.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="vision_url"):
        make_settings(tmp_path, {"route": {"vision_url": "http://vision.example.com:8001"}})


# --- the courier's cycle, end to end ---

def photo_cycle(tmp_path, services, clock, *, p, ocr_lines=TEXT, name="whiteboard.png", content=None, text_gate=None,
                routing=None, model=None):
    todoist, telegram, router = services
    fake = attach(router)
    if text_gate is not None:  # the text gate's answer on the OCR'd text
        fake.gate_reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=text_gate))
    if routing is not None:  # the routing read's answer on the extracted tasks
        fake.reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=routing))
    vision = Vision(p)
    router.post(f"{VISION}/v1/systemone").mock(side_effect=vision.reply)
    settings = make_settings(tmp_path, {"route": {"backend": "decider"},
                                        "devices": {"inputs": ["glasses"], "outputs": ["todoist", "telegram"]}},
                             fixtures=None)
    folder = settings.paths.state_dir / "inbox" / "photos"
    folder.mkdir(parents=True)
    picture(folder / name) if content is None else content(folder / name)
    model = model or ScriptedModel(replies={}, default=extraction_json(tasks=[
        {"content": "Send Ilona the antenna drawings", "due_string": "Thursday", "notes": None}]))
    runtime = runtime_for(settings, model, clock, router=fake.router())
    runtime.cycle.deps.inputs["glasses"].ocr = photos.Ocr(FakeOcr(ocr_lines))
    runtime.cycle.run()
    entry = json.loads(settings.ledger_path.read_text(encoding="utf-8"))[photo_id(folder / name)]
    return entry, model, fake, vision, todoist, telegram


def test_a_cleared_photo_passes_three_checks_then_is_written_and_its_numbers_are_in_the_ledger(tmp_path, services, clock):
    entry, model, fake, vision, todoist, telegram = photo_cycle(tmp_path, services, clock, p=0.2)
    assert entry["status"] == "done" and len(todoist.tasks) == 1
    assert len(vision.bodies) == 2 and len(fake.gate_bodies) >= 1  # the image gate, the text gate, the scene
    assert "picture" in vision.bodies[0]["questions"] and "scene" in vision.bodies[1]["questions"]
    assert len(model.calls) == 1  # then the one cloud call
    image = entry["image"]
    assert image["gate_p"] == 0.2 and image["extraction_calls"] == 1 and image["ocr_confidence"] == [0.97] * 3
    assert not any(line in json.dumps(entry) for line in TEXT)  # the picture's text is never in the ledger


def test_a_photo_the_image_gate_holds_reaches_no_model_and_no_list(tmp_path, services, clock):
    entry, model, fake, vision, todoist, telegram = photo_cycle(tmp_path, services, clock, p=0.8)
    assert (entry["status"], entry["reason"]) == ("hold", "image-sensitive")
    assert model.calls == [] and fake.gate_bodies == [] and todoist.tasks == {}
    assert entry["image"] == {"extraction_calls": 0, "gate_p": 0.8, "gate_seconds": entry["image"]["gate_seconds"],
                              "sha256": entry["image"]["sha256"]}  # the file's hash (ruling 57); numbers, no text
    assert len(entry["image"]["sha256"]) == 64
    assert telegram.texts == [f"Held {photo_id(Path('whiteboard.png'))}: image-sensitive. Nothing was written."]


def test_an_unreadable_photo_holds_with_no_model_asked_at_all(tmp_path, services, clock):
    entry, model, fake, vision, todoist, telegram = photo_cycle(
        tmp_path, services, clock, p=0.2, content=lambda path: Image.new("RGB", (400, 300), "white").save(path))
    assert (entry["status"], entry["reason"], entry["image"]["unreadable"]) == ("hold", "unreadable", "blank")
    assert vision.bodies == [] and model.calls == [] and fake.gate_bodies == [] and todoist.tasks == {}


def test_the_tripwire_reads_the_photos_text_before_the_text_gate_and_the_cloud(tmp_path, services, clock):
    entry, model, fake, vision, todoist, telegram = photo_cycle(
        tmp_path, services, clock, p=0.2, ocr_lines=["Call Vargrheim about the contract", "before Friday at noon please"])
    assert (entry["status"], entry["reason"]) == ("hold", "stoplist:vargrheim")
    assert len(vision.bodies) == 1 and fake.gate_bodies == [] and model.calls == []


# --- a held photo's notice names the folder, not Pocket (ruling 49): one test per notice site ---

def photo_notice(telegram, reason, name="whiteboard.png"):
    rid = photo_id(Path(name))
    assert [t for t in telegram.texts if t.startswith(f"Held {rid}:")] == [
        f"Held {rid}: {reason} (photo). It stays in the inbox folder."]
    assert not any("Pocket" in t for t in telegram.texts)


def test_a_photo_the_tripwire_holds_is_told_it_stays_in_the_folder(tmp_path, services, clock):
    entry, model, fake, vision, todoist, telegram = photo_cycle(
        tmp_path, services, clock, p=0.2, ocr_lines=["Call Vargrheim about the contract", "before Friday at noon please"])
    assert (entry["status"], entry["reason"]) == ("hold", "stoplist:vargrheim")
    photo_notice(telegram, "stop-list")  # the kind of hold, never the term (ruling 13)


def test_a_photo_the_text_gate_holds_is_told_it_stays_in_the_folder(tmp_path, services, clock):
    entry, model, fake, vision, todoist, telegram = photo_cycle(tmp_path, services, clock, p=0.2, text_gate=0.95)
    assert entry["status"] == "hold" and model.calls == [] and len(fake.gate_bodies) >= 1
    photo_notice(telegram, entry["reason"])


def test_a_photo_held_after_extraction_is_told_it_stays_in_the_folder(tmp_path, services, clock):
    entry, model, fake, vision, todoist, telegram = photo_cycle(tmp_path, services, clock, p=0.2, routing=0.95)
    assert (entry["status"], entry["reason"]) == ("hold", "sensitive") and len(model.calls) == 1
    assert todoist.tasks == {}
    photo_notice(telegram, "sensitive")


def test_a_photo_the_leak_guard_holds_is_told_it_stays_in_the_folder(tmp_path, services, clock):
    quoting = ScriptedModel(replies={}, default=extraction_json(tasks=[
        {"content": "Book the clean room", "due_string": None, "notes": "She said Book the clean room for the fit check"}]))
    entry, model, fake, vision, todoist, telegram = photo_cycle(tmp_path, services, clock, p=0.2, model=quoting)
    assert (entry["status"], entry["reason"]) == ("hold", "leak-guard") and len(model.calls) == 2  # and the retry
    assert todoist.tasks == {}
    photo_notice(telegram, "leak-guard")


def test_the_vision_launcher_serves_with_vision_on_this_machine_capped_like_the_published_one(tmp_path, monkeypatch):
    import importlib.util
    import sys
    import types
    from pathlib import Path as P

    spec = importlib.util.spec_from_file_location("serve_vision", P(__file__).resolve().parents[1] / "tools" /
                                                  "serve_vision.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    calls, threads = {}, {}
    torch = types.SimpleNamespace(__version__="fake", set_num_threads=lambda n: threads.update(intra=n),
                                  set_num_interop_threads=lambda n: threads.update(inter=n),
                                  get_num_threads=lambda: threads["intra"])
    server = types.ModuleType("strands_decider.server")
    server.serve = lambda checkpoint, **kw: calls.update(checkpoint=checkpoint, **kw)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "strands_decider", types.ModuleType("strands_decider"))
    monkeypatch.setitem(sys.modules, "strands_decider.server", server)
    monkeypatch.setattr(launcher.serve_decider, "lower_priority", lambda: calls.update(lowered=True))
    assert launcher.main(["--pid-file", str(tmp_path / "vision.pid")]) == 0
    assert calls["vision"] is True and calls["host"] == "127.0.0.1" and calls["port"] == 8001
    assert calls["device"] == "cpu" and calls["lowered"] and threads == {"intra": 4, "inter": 1}
    assert calls["checkpoint"] == "StrandsAgents/strands-decider-2B-hobson-v21"
    assert (tmp_path / "vision.pid").read_text(encoding="ascii").isdigit()
