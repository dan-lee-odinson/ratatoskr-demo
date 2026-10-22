"""Dan's ruling 50: the scene of a photo, decided on the laptop after the text gate and before anything is extracted.

One test per answer (owner_owes, owed_to_owner, reference, unsure), the owner's tap and what runs on the next cycle,
the repeat tap, a tap from another chat, the reference note's contents and place, the waiting-on label and line,
no cloud call on reference or unsure, and the mirrored pairs of bench/images/labels_v2.json.
"""

import hashlib
import json
from pathlib import Path

from ratatoskr_demo import photos, resolve
from ratatoskr_demo.devices.glasses_adapter import photo_id
from tests.conftest import ScriptedModel, extraction_json, make_settings, runtime_for
from tests.test_decider import attach
from tests.test_photos import TEXT, VISION, FakeOcr, Vision, picture

REPO = Path(__file__).resolve().parents[1]
OWNER = 4242
NAME = "whiteboard.png"
RID = photo_id(Path(NAME))


class Run:
    """One photo in the watched folder and the courier around it; cycle() runs it once, as `--once` does."""

    def __init__(self, tmp_path, services, clock, *, scene, scene_p):
        self.todoist, self.telegram, router = services
        self.fake = attach(router)
        self.vision = Vision(0.2, scene=scene, scene_p=scene_p)
        router.post(f"{VISION}/v1/systemone").mock(side_effect=self.vision.reply)
        self.settings = make_settings(tmp_path, {"route": {"backend": "decider"},
                                                 "devices": {"inputs": ["glasses"], "outputs": ["todoist", "telegram"]}},
                                      fixtures=None)
        folder = self.settings.paths.state_dir / "inbox" / "photos"
        folder.mkdir(parents=True)
        picture(folder / NAME)
        self.model = ScriptedModel(replies={}, default=extraction_json(tasks=[
            {"content": "Send Ilona the antenna drawings", "due_string": "Thursday", "notes": None}]))
        self.runtime = runtime_for(self.settings, self.model, clock, router=self.fake.router())
        self.runtime.cycle.deps.inputs["glasses"].ocr = photos.Ocr(FakeOcr(TEXT))

    def cycle(self):
        self.runtime.cycle.run()
        return json.loads(self.settings.ledger_path.read_text(encoding="utf-8"))[RID]

    def scene_reads(self):
        return [b for b in self.vision.bodies if "scene" in b["questions"]]

    def tap(self, answer, *, chat=OWNER, message_id=91):
        telegram = TapTelegram()
        resolver = resolve.Resolver(self.runtime.state, telegram, None, OWNER, ["research"], clock=lambda: 1792600000.0)
        sid = photos.scene_sid(RID)
        resolver.handle({"id": "cb-9", "data": f"scene:{sid}:{answer}",
                         "message": {"message_id": message_id, "chat": {"id": chat}, "text": "scene unsure · 0.50"}})
        return telegram


class TapTelegram:
    def __init__(self):
        self.edits, self.answers = [], []

    def edit_message(self, message_id, text, reply_markup=None):
        self.edits.append((message_id, text, reply_markup))

    def answer_callback(self, callback_id, text=None):
        self.answers.append((callback_id, text))


def test_owner_owes_runs_the_existing_path(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="owner_owes", scene_p=0.9)
    entry = run.cycle()
    assert entry["status"] == "done" and len(run.model.calls) == 1 and len(run.todoist.tasks) == 1
    [task] = run.todoist.tasks.values()
    assert photos.WAITING_ON not in task["labels"]
    assert {k: entry["image"][k] for k in ("scene", "scene_p", "scene_by", "scene_action")} == {
        "scene": "owner_owes", "scene_p": 0.9, "scene_by": "decider", "scene_action": "extracted"}
    # the scene read carries the picture and its OCR text as the state, one choice question
    [read] = run.scene_reads()
    assert read["state"] == "\n".join(TEXT) and len(read["images"]) == 1
    assert read["questions"]["scene"]["type"] == "choice" and set(read["questions"]["scene"]["criteria"]) == {
        "owner_owes", "owed_to_owner", "reference"}


def test_owed_to_owner_is_written_with_the_waiting_on_label_and_line(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="owed_to_owner", scene_p=0.85)
    entry = run.cycle()
    assert entry["status"] == "done" and len(run.model.calls) == 1
    [task] = run.todoist.tasks.values()
    assert photos.WAITING_ON in task["labels"]
    [line] = [t.split("\n")[0] for t in run.telegram.texts if t.startswith("waiting-on")]
    assert line.startswith("waiting-on · ") and line.endswith(" · 0.85")
    assert entry["image"]["scene_action"] == "extracted, waiting-on"


def test_reference_is_kept_on_the_laptop_and_reaches_no_cloud_service(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="reference", scene_p=0.92)
    entry = run.cycle()
    assert run.model.calls == [] and run.fake.bodies == [] and run.todoist.tasks == {}  # no extraction, no routing
    assert (entry["status"], entry["reason"], entry["image"]["scene_action"]) == ("done", "reference",
                                                                                 "kept on the laptop")
    note = run.settings.paths.state_dir / "reference" / f"{RID}.txt"
    lines = note.read_text(encoding="utf-8").split("\n")
    assert lines[0] == f"id: {RID}" and lines[1].startswith("date: 20") and lines[3:6] == TEXT
    assert run.telegram.texts == ["reference · kept on the laptop"]
    assert not any(line in json.dumps(entry) for line in TEXT)  # the ledger keeps numbers, not the text


def test_unsure_sends_nothing_anywhere_but_the_question_with_three_buttons(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="owner_owes", scene_p=0.5)  # under the 0.70 floor
    entry = run.cycle()
    assert run.model.calls == [] and run.fake.bodies == [] and run.todoist.tasks == {}
    assert (entry["status"], entry["reason"], entry["image"]["scene"]) == ("hold", "scene-unsure", "unsure")
    [message] = run.telegram.messages
    assert message["text"] == "scene unsure · 0.50"
    [buttons] = message["reply_markup"]["inline_keyboard"]
    sid = photos.scene_sid(RID)
    assert [(b["text"], b["callback_data"]) for b in buttons] == [
        ("I owe", f"scene:{sid}:owner_owes"), ("They owe", f"scene:{sid}:owed_to_owner"),
        ("Reference", f"scene:{sid}:reference")]
    assert not (run.settings.paths.state_dir / "reference").exists()
    run.cycle()  # still unanswered: nothing more is sent, nothing is asked again of the scene
    assert len(run.telegram.messages) == 1 and len(run.scene_reads()) == 1 and run.model.calls == []


def test_the_owners_tap_is_recorded_and_the_chosen_path_runs_on_the_next_cycle(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="owner_owes", scene_p=0.5)
    run.cycle()
    telegram = run.tap("owed_to_owner")
    image = run.runtime.state.ledger_get(RID).image
    assert (image["scene"], image["scene_by"]) == ("owed_to_owner", "owner") and image["scene_at"].startswith("2026-")
    [(message_id, text, markup)] = telegram.edits
    assert message_id == 91 and markup is None and text.startswith("scene unsure · 0.50 → They owe, by owner")
    assert telegram.answers == [("cb-9", None)] and run.model.calls == []  # nothing ran on the tap itself
    entry = run.cycle()
    assert entry["status"] == "done" and len(run.model.calls) == 1 and len(run.scene_reads()) == 1  # not re-asked
    [task] = run.todoist.tasks.values()
    assert photos.WAITING_ON in task["labels"]
    assert (entry["image"]["scene"], entry["image"]["scene_by"]) == ("owed_to_owner", "owner")


def test_a_second_tap_on_the_same_message_changes_nothing(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="owner_owes", scene_p=0.5)
    run.cycle()
    run.tap("reference")
    before = dict(run.runtime.state.ledger_get(RID).image)
    telegram = run.tap("owner_owes")
    assert run.runtime.state.ledger_get(RID).image == before  # the first answer stands
    assert telegram.edits == [] and telegram.answers == [("cb-9", "already answered: Reference")]
    entry = run.cycle()
    assert (entry["status"], entry["reason"]) == ("done", "reference") and run.model.calls == []


def test_a_scene_tap_from_another_chat_is_ignored(tmp_path, services, clock):
    run = Run(tmp_path, services, clock, scene="owner_owes", scene_p=0.5)
    run.cycle()
    telegram = run.tap("owner_owes", chat=999)
    assert run.runtime.state.ledger_get(RID).image["scene_by"] == "decider"  # the owner's answer was not recorded
    assert telegram.edits == [] and telegram.answers == [("cb-9", None)]


def test_the_scene_floor_is_the_routing_reads_floor_for_a_choice_question(tmp_path):
    from ratatoskr_demo.config import Settings
    from ratatoskr_demo.route import load_route_questions

    questions = load_route_questions(Settings())
    decision = photos.make_scene_decision(Settings(), questions)
    assert decision.floor == questions["project"].min_confidence == 0.7


# --- the second picture set (bench/images/labels_v2.json) ---

def test_labels_v2_keeps_the_first_labels_and_mirrors_two_pairs():
    v1_raw = (REPO / "bench" / "images" / "labels.json").read_bytes()
    assert hashlib.sha256(v1_raw).hexdigest().startswith("7ae6ef6bc5d041ba")  # the first labels, not edited
    v1 = json.loads(v1_raw)["images"]
    v2 = json.loads((REPO / "bench" / "images" / "labels_v2.json").read_text(encoding="utf-8"))
    images = {x["file"]: x for x in v2["images"]}
    assert len(images) == 24 and [{k: v for k, v in x.items() if k != "scene"} for x in v2["images"][:16]] == v1
    assert all(x["scene"] in ("owner_owes", "owed_to_owner", "reference", "unsure") for x in v2["images"])
    assert all((REPO / "bench" / "images" / name).is_file() for name in images)
    assert sum(x["scene"] == "owed_to_owner" for x in v2["images"][16:]) == 3
    assert all(x.get("expected_waiting_on") for x in v2["images"] if x["scene"] == "owed_to_owner")
    for owed, owes in v2["mirrored"]:  # the wording matches, the direction differs
        assert (images[owed]["scene"], images[owes]["scene"]) == ("owed_to_owner", "owner_owes")
        assert {w.lower() for w in images[owed]["ocr_keywords"]} == {w.lower() for w in images[owes]["ocr_keywords"]}
