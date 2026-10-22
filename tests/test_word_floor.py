"""Dan's ruling 53: the 8-word floor of ruling 14 is for recordings (an accidental press), not for photos.

A photo's guard is the readability check: OCR text with no words at all is held as unreadable, with the id-only
notice and nothing further asked; one word or more goes on to the tripwire, the text gate and the scene. A short
recording is still closed as an accidental press.
"""

import json
from pathlib import Path

from ratatoskr_demo.devices.glasses_adapter import photo_id
from tests.conftest import ScriptedModel, make_settings, runtime_for
from tests.test_decider import attach
from tests.test_notices_and_short import fixtures_with, ledger
from tests.test_photos import photo_cycle

RID = photo_id(Path("whiteboard.png"))


def test_a_four_word_photo_goes_on_to_the_tripwire_the_text_gate_and_the_scene(tmp_path, services, clock):
    entry, model, fake, vision, todoist, telegram = photo_cycle(tmp_path, services, clock, p=0.2,
                                                                ocr_lines=["Invoice to Sam Friday"])
    assert entry["status"] == "done" and entry.get("reason") != "too-short"
    assert len(fake.gate_bodies) >= 1 and any("scene" in b["questions"] for b in vision.bodies)
    assert len(model.calls) == 1


def test_a_photo_with_no_words_is_held_unreadable_and_nothing_further_is_asked(tmp_path, services, clock):
    entry, model, fake, vision, todoist, telegram = photo_cycle(tmp_path, services, clock, p=0.2, ocr_lines=[])
    assert (entry["status"], entry["reason"], entry["image"]["unreadable"]) == ("hold", "unreadable", "no words")
    assert fake.gate_bodies == [] and model.calls == [] and todoist.tasks == {}
    assert not any("scene" in b["questions"] for b in vision.bodies)
    assert telegram.texts == [f"Held {RID}: unreadable. Nothing was written."]


def test_a_four_word_recording_is_still_closed_as_an_accidental_press(tmp_path, clock, services):
    _, telegram, router = services
    fake = attach(router)
    settings = make_settings(tmp_path, {"route": {"backend": "decider"}}, fixtures=fixtures_with(
        tmp_path, {"rec_001": {"transcript": "Speaker 1: Invoice to Sam Friday"}}))
    runtime_for(settings, ScriptedModel(), clock, router=fake.router()).cycle.run()
    entry = ledger(settings)["rec_001"]
    assert (entry["status"], entry["reason"]) == ("done", "too-short")
    assert not any("Invoice to Sam" in json.dumps(body) for body in fake.all_bodies)
    assert not any("rec_001" in t for t in telegram.texts)
