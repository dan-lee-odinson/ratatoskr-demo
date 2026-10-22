"""Dan's ruling 52: the owner's Release for a photo held as unsure by the image gate or the text gate.

The unsure hold's notice carries one button; a sensitive hold's carries none. A tap (owner's chat only; the first
tap stands) records the release and sends nothing anywhere; on the next cycle the photo continues from the check after
the one that held it: from the image gate to the OCR, the tripwire, the text gate and the scene; from the text gate
to the scene. Every later check runs as usual.
"""

import json
from pathlib import Path

import httpx

from ratatoskr_demo import photos, resolve
from ratatoskr_demo.devices.glasses_adapter import photo_id
from tests.conftest import ScriptedModel, extraction_json, make_settings, runtime_for
from tests.test_decider import attach, decider_answers
from tests.test_photos import TEXT, VISION, FakeOcr, Vision, picture

OWNER = 4242
NAME = "whiteboard.png"
RID = photo_id(Path(NAME))


class Run:
    """One photo in the watched folder and the courier around it; cycle() runs it once, as `--once` does."""

    def __init__(self, tmp_path, services, clock, *, p=0.2, text_gate=None, scene="commitment", scene_p=0.9):
        self.todoist, self.telegram, router = services
        self.fake = attach(router)
        if text_gate is not None:
            self.fake.gate_reply = lambda body: httpx.Response(200, json=decider_answers(sensitive=text_gate))
        self.vision = Vision(p, scene=scene, scene_p=scene_p)
        router.post(f"{VISION}/v1/systemone").mock(side_effect=self.vision.reply)
        self.settings = make_settings(tmp_path, {"route": {"backend": "decider"},
                                                 "devices": {"inputs": ["glasses"], "outputs": ["todoist", "telegram"]}},
                                      fixtures=None)
        folder = self.settings.paths.state_dir / "inbox" / "photos"
        folder.mkdir(parents=True)
        picture(folder / NAME)
        self.ocr = FakeOcr(TEXT)
        self.model = ScriptedModel(replies={}, default=extraction_json(tasks=[
            {"content": "Send Ilona the antenna drawings", "due_string": "Thursday", "notes": None}]))
        self.runtime = runtime_for(self.settings, self.model, clock, router=self.fake.router())
        self.runtime.cycle.deps.inputs["glasses"].ocr = photos.Ocr(self.ocr)

    def cycle(self):
        self.runtime.cycle.run()
        return json.loads(self.settings.ledger_path.read_text(encoding="utf-8"))[RID]

    def reads(self, question):
        return [b for b in self.vision.bodies if question in b["questions"]]

    def tap(self, gate, *, chat=OWNER):
        telegram = TapTelegram()
        resolver = resolve.Resolver(self.runtime.state, telegram, None, OWNER, ["research"], clock=lambda: 1792600000.0)
        resolver.handle({"id": "cb-7", "data": f"release:{photos.release_sid(RID, gate)}",
                         "message": {"message_id": 33, "chat": {"id": chat}, "text": f"Held {RID}: unsure"}})
        return telegram


class TapTelegram:
    def __init__(self):
        self.edits, self.answers = [], []

    def edit_message(self, message_id, text, reply_markup=None):
        self.edits.append((message_id, text, reply_markup))

    def answer_callback(self, callback_id, text=None):
        self.answers.append((callback_id, text))


def release_button(message):
    return [(b["text"], b["callback_data"]) for row in (message.get("reply_markup") or {}).get("inline_keyboard", [])
            for b in row]


def test_an_image_gate_unsure_photo_is_released_and_continues_to_the_ocr_tripwire_text_gate_and_scene(
        tmp_path, services, clock):
    run = Run(tmp_path, services, clock, p=0.5)  # unsure: between 0.40 and 0.60
    entry = run.cycle()
    assert (entry["status"], entry["reason"]) == ("hold", "image-unsure") and run.ocr.calls == []
    [message] = run.telegram.messages
    assert release_button(message) == [("Release", f"release:{photos.release_sid(RID, 'image gate')}")]
    telegram = run.tap("image gate")
    [release] = run.runtime.state.ledger_get(RID).image["releases"]
    assert (release["held_by"], release["by"]) == ("image gate", "owner") and release["at"].startswith("2026-")
    assert telegram.edits[0][1].endswith("→ released by owner; continues on the next cycle")
    assert run.model.calls == [] and len(run.vision.bodies) == 1  # the tap itself sends nothing anywhere
    entry = run.cycle()
    assert len(run.reads("picture")) == 1  # the image gate is not asked again
    assert len(run.ocr.calls) == 1 and len(run.fake.gate_bodies) >= 1 and len(run.reads("scene")) == 1
    assert entry["status"] == "done" and len(run.model.calls) == 1 and len(run.todoist.tasks) == 1
    assert entry["image"]["gate_p"] == 0.5 and entry["image"]["releases"][0]["by"] == "owner"


def test_a_text_gate_unsure_photo_is_released_and_continues_to_the_scene(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, p=0.2, text_gate=0.5)
    entry = run.cycle()
    assert (entry["status"], entry["reason"]) == ("hold", "source-unsure")
    [message] = run.telegram.messages
    assert release_button(message) == [("Release", f"release:{photos.release_sid(RID, 'text gate')}")]
    gate_reads = len(run.fake.gate_bodies)
    run.tap("text gate")
    entry = run.cycle()
    assert len(run.reads("picture")) == 1 and len(run.fake.gate_bodies) == gate_reads  # neither gate asked again
    assert len(run.reads("scene")) == 1 and entry["status"] == "done" and len(run.model.calls) == 1


def test_a_photo_held_as_sensitive_has_no_release_button(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, p=0.8)  # a yes from the image gate
    assert run.cycle()["reason"] == "image-sensitive"
    [message] = run.telegram.messages
    assert release_button(message) == [] and "releases" not in run.runtime.state.ledger_get(RID).image


def test_a_photo_the_text_gate_holds_as_sensitive_has_no_release_button(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, p=0.2, text_gate=0.95)
    assert run.cycle()["reason"] == "source-sensitive"
    [message] = run.telegram.messages
    assert release_button(message) == []


def test_a_second_release_tap_changes_nothing(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, p=0.5)
    run.cycle()
    run.tap("image gate")
    before = json.dumps(run.runtime.state.ledger_get(RID).image, sort_keys=True)
    telegram = run.tap("image gate")
    assert json.dumps(run.runtime.state.ledger_get(RID).image, sort_keys=True) == before
    assert telegram.edits == [] and telegram.answers == [("cb-7", "already released")]


def test_a_release_tap_from_another_chat_is_ignored(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, p=0.5)
    run.cycle()
    telegram = run.tap("image gate", chat=999)
    assert run.runtime.state.ledger_get(RID).image["releases"][0]["by"] is None
    assert telegram.edits == [] and telegram.answers == [("cb-7", None)]
    entry = run.cycle()
    assert entry["reason"] == "image-unsure" and run.ocr.calls == []  # still held: the gate is asked again


def test_an_unreleased_unsure_photo_stays_held_and_is_announced_once(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, p=0.5)
    run.cycle()
    run.cycle()
    assert len(run.telegram.messages) == 1 and run.model.calls == [] and run.ocr.calls == []


def test_a_photo_released_from_the_image_gate_and_held_by_the_text_gate_is_offered_a_second_release(
        tmp_path, services, clock):
    run = Run(tmp_path, services, clock, p=0.5, text_gate=0.5)
    run.cycle()
    run.tap("image gate")
    entry = run.cycle()  # continues, and the text gate holds it as unsure: a new hold, announced with its own button
    assert (entry["status"], entry["reason"]) == ("hold", "source-unsure")
    assert release_button(run.telegram.messages[-1]) == [("Release", f"release:{photos.release_sid(RID, 'text gate')}")]
    assert [r["held_by"] for r in entry["image"]["releases"]] == ["image gate", "text gate"]
    run.tap("text gate")
    entry = run.cycle()
    assert entry["status"] == "done" and len(run.reads("picture")) == 1 and len(run.model.calls) == 1
