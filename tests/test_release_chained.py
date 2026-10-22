"""Dan's ruling 57: the chained path, an owner's Release and then the owner's scene answer, for the same photo.

As the review reproduced it: a photo held as unsure by the image gate or the text gate is released; the scene comes
back unsure; the owner taps Commitment or Reference. The next cycle must run that answer, not stop at the gate the
owner released the photo from. A release is recorded with the file's sha256 and carried forward for the unchanged
file; a file whose hash differs is a new picture. Every later check still runs, the ledger on disk is the only state
across a restart, and attribution stays the owner's (the release's `by`, `at` and `sha256`; `scene_by`).
"""

import hashlib

import pytest

from ratatoskr_demo import photos, resolve
from tests.conftest import runtime_for
from tests.test_photos import FakeOcr, TEXT, picture
from tests.test_release import NAME, OWNER, RID, Run, TapTelegram

GATES = {"image": dict(p=0.5), "text": dict(p=0.2, text_gate=0.5)}  # which gate holds the photo as unsure


def scene_tap(run, answer):
    telegram = TapTelegram()
    resolve.Resolver(run.runtime.state, telegram, None, OWNER, ["research"], clock=lambda: 1792600000.0).handle(
        {"id": "cb-scene", "data": f"scene:{photos.scene_sid(RID)}:{answer}",
         "message": {"message_id": 34, "chat": {"id": OWNER}, "text": "scene unsure · 0.50"}})
    return telegram


def held_then_released(tmp_path, services, clock, gate):
    """The photo held as unsure by `gate`, released by the owner, then held again as scene unsure."""
    run = Run(tmp_path, services, clock, scene="commitment", scene_p=0.5, **GATES[gate])
    first = run.cycle()
    assert (first["status"], first["reason"]) == ("hold", "image-unsure" if gate == "image" else "source-unsure")
    run.tap(f"{gate} gate")
    second = run.cycle()
    assert (second["status"], second["reason"]) == ("hold", "scene-unsure")
    return run


def file_hash(run):
    return hashlib.sha256((run.settings.paths.state_dir / "inbox" / "photos" / NAME).read_bytes()).hexdigest()


@pytest.mark.parametrize("gate", ["image", "text"])
def test_released_then_the_owner_answers_commitment_and_it_is_extracted_and_written(tmp_path, services, clock, gate):
    run = held_then_released(tmp_path, services, clock, gate)
    scene_tap(run, "commitment")
    pictures, text_reads = len(run.reads("picture")), len(run.fake.gate_bodies)
    entry = run.cycle()
    assert (entry["status"], entry.get("reason")) == ("done", None)
    assert len(run.model.calls) == 1 and len(run.todoist.tasks) == 1  # extracted and written
    assert len(run.reads("picture")) == pictures and len(run.fake.gate_bodies) == text_reads  # no gate asked again
    assert len(run.reads("scene")) == 1  # nor the scene: the owner's answer runs
    [release] = entry["image"]["releases"]
    assert (release["held_by"], release["by"], release["sha256"]) == (f"{gate} gate", "owner", file_hash(run))
    assert release["at"].startswith("2026-") and (entry["image"]["scene"], entry["image"]["scene_by"]) == (
        "commitment", "owner")


@pytest.mark.parametrize("gate", ["image", "text"])
def test_released_then_the_owner_answers_reference_and_the_note_is_kept_with_no_cloud_call(
        tmp_path, services, clock, gate):
    run = held_then_released(tmp_path, services, clock, gate)
    scene_tap(run, "reference")
    entry = run.cycle()
    assert (entry["status"], entry["reason"]) == ("done", "reference")
    assert run.model.calls == [] and run.fake.bodies == [] and run.todoist.tasks == {}  # no extraction, no routing
    assert (run.settings.paths.state_dir / "reference" / f"{RID}.txt").is_file()
    assert entry["image"]["releases"][0]["by"] == "owner" and entry["image"]["scene_by"] == "owner"


def test_with_both_gates_clear_the_owners_scene_answer_completes(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="commitment", scene_p=0.5)  # the review's control
    assert run.cycle()["reason"] == "scene-unsure"
    scene_tap(run, "commitment")
    pictures, text_reads = len(run.reads("picture")), len(run.fake.gate_bodies)
    entry = run.cycle()
    assert (entry["status"], entry.get("reason")) == ("done", None)
    assert len(run.model.calls) == 1 and len(run.todoist.tasks) == 1
    assert len(run.reads("picture")) == pictures and len(run.fake.gate_bodies) == text_reads


def test_a_duplicate_tap_at_each_step_changes_nothing(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="commitment", scene_p=0.5, p=0.5)
    run.cycle()
    run.tap("image gate")
    assert run.tap("image gate").answers == [("cb-7", "already released")]  # the second Release
    run.cycle()
    scene_tap(run, "commitment")
    before = dict(run.runtime.state.ledger_get(RID).image)
    assert scene_tap(run, "reference").answers == [("cb-scene", "already answered: Commitment")]
    assert run.runtime.state.ledger_get(RID).image == before
    entry = run.cycle()
    assert entry["status"] == "done" and entry["image"]["scene"] == "commitment" and len(run.todoist.tasks) == 1


def test_a_file_replaced_between_cycles_inherits_no_release_and_is_held_again(tmp_path, services, clock):
    run = held_then_released(tmp_path, services, clock, "image")
    scene_tap(run, "commitment")
    old = file_hash(run)
    picture(run.settings.paths.state_dir / "inbox" / "photos" / NAME, lines=["A different picture", "same name"])
    assert file_hash(run) != old
    pictures, notices = len(run.reads("picture")), len(run.telegram.messages)
    entry = run.cycle()
    assert (entry["status"], entry["reason"]) == ("hold", "image-unsure")  # the gate is asked again, and holds
    assert len(run.reads("picture")) == pictures + 1 and run.model.calls == [] and run.todoist.tasks == {}
    assert len(run.telegram.messages) == notices + 1  # a new hold, announced with its own Release
    assert "scene_by" not in entry["image"]  # the owner's answer was for the other file
    [release] = entry["image"]["releases"]
    assert (release["by"], release["sha256"]) == (None, file_hash(run))


def test_a_file_replaced_after_the_owners_scene_answer_is_asked_its_scene_again(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="commitment", scene_p=0.5)  # both gates clear
    assert run.cycle()["reason"] == "scene-unsure"
    scene_tap(run, "commitment")
    picture(run.settings.paths.state_dir / "inbox" / "photos" / NAME, lines=["A different picture", "same name"])
    notices = len(run.telegram.messages)
    entry = run.cycle()
    assert (entry["status"], entry["reason"]) == ("hold", "scene-unsure")  # the answer was for the other file
    assert len(run.reads("scene")) == 2 and run.model.calls == [] and run.todoist.tasks == {}
    assert len(run.telegram.messages) == notices + 1 and entry["image"]["scene_by"] == "decider"


def test_a_restart_between_the_tap_and_the_next_cycle_resumes_from_the_ledger(tmp_path, services, clock):
    run = held_then_released(tmp_path, services, clock, "text")
    scene_tap(run, "commitment")
    # a new courier: nothing carried over but the ledger on disk
    run.runtime = runtime_for(run.settings, run.model, clock, router=run.fake.router())
    run.runtime.cycle.deps.inputs["glasses"].ocr = photos.Ocr(FakeOcr(TEXT))
    pictures, text_reads = len(run.reads("picture")), len(run.fake.gate_bodies)
    entry = run.cycle()
    assert (entry["status"], entry.get("reason")) == ("done", None) and len(run.todoist.tasks) == 1
    assert len(run.reads("picture")) == pictures and len(run.fake.gate_bodies) == text_reads


def test_a_release_holds_only_for_the_file_it_was_recorded_against():
    earlier = {"sha256": "a" * 64, "releases": [{"held_by": "image gate", "by": "owner", "sha256": "a" * 64}]}
    assert photos.passed_gates(earlier, "a" * 64) == {"image gate"}
    assert photos.passed_gates(earlier, "b" * 64) == frozenset()  # another file
    assert photos.passed_gates({**earlier, "releases": [{**earlier["releases"][0], "by": None}]}, "a" * 64) == set()
    other = {**earlier, "releases": [{**earlier["releases"][0], "sha256": "b" * 64}]}  # recorded against another file
    assert photos.passed_gates(other, "a" * 64) == frozenset()
    text ={"sha256": "a" * 64, "releases": [{"held_by": "text gate", "by": "owner", "sha256": "a" * 64}]}
    assert photos.passed_gates(text, "a" * 64) == {"image gate", "text gate"}  # it had passed the image gate first
    assert photos.passed_gates({"sha256": "a" * 64, "scene_by": "owner"}, "a" * 64) == {"image gate", "text gate"}
