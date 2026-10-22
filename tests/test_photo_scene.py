"""Dan's ruling 59: patterns/photo_scene, the photo scene decision as a second recipe.

The floor from the TOML and its band; each outcome of the handoff and of the owner's answer; the wiring rule's three
branches; the client failing closed; the example's output against a local stand-in for the vision server; and the
recipe's question kept identical to the courier's scene question, as the first recipe's questions are kept to
route.toml.
"""

import base64
import io
import json
import runpy
import sys
import tomllib
from contextlib import redirect_stdout
from importlib import resources
from pathlib import Path

import pytest
from PIL import Image

from ratatoskr_demo import photos
from tests.test_patterns import LocalDecider

RECIPE = Path(__file__).resolve().parents[1] / "patterns" / "photo_scene"
sys.path.insert(0, str(RECIPE))

import handoff as ho  # noqa: E402
import scene as sc  # noqa: E402


def scene_reply(choice, confidence, probabilities=None):
    return 200, {"model": "strands-decider-2B-hobson-v21", "answers": {"scene": {
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": probabilities or {choice: confidence}}}}


def decide(choice, confidence, picture=None):
    with LocalDecider(lambda body: scene_reply(choice, confidence)) as server:
        return sc.SceneClient(server.url).decide(picture or RECIPE / "pictures" / "o4_sticky_note.png", "text"), server


# --- the question and its floor ---

def test_the_recipes_question_is_the_couriers_scene_question_word_for_word():
    recipe = tomllib.loads((RECIPE / "questions.toml").read_text(encoding="utf-8"))["scene"]
    route = tomllib.loads(resources.files("ratatoskr_demo").joinpath("prompts", "route.toml").read_text("utf-8"))
    assert (recipe["type"], recipe["instructions"], recipe["criteria"]) == ("choice", photos.SCENE_QUESTION,
                                                                            photos.SCENE_CRITERIA)
    assert recipe["min_confidence"] == route["project"]["min_confidence"]  # the routing read's choice floor


@pytest.mark.parametrize("confidence, answer", [(0.70, "commitment"), (0.95, "commitment"), (0.6999, "unsure"),
                                                (0.40, "unsure")])
def test_the_floor_and_its_band_come_from_the_toml(confidence, answer):
    assert sc.load_question().floor == 0.70
    found, _ = decide("commitment", confidence)
    assert (found.answer, found.choice, found.confidence, found.floor) == (answer, "commitment", confidence, 0.70)


def test_one_read_carries_the_picture_its_text_and_the_scene_question():
    with LocalDecider(lambda body: scene_reply("reference", 0.96, {"commitment": 0.02, "reference": 0.98})) as server:
        found = sc.SceneClient(server.url).decide(RECIPE / "pictures" / "n4_reference_menu.png", "Juniper Lane Café")
    [body] = server.bodies
    assert body["state"] == "Juniper Lane Café" and list(body["questions"]) == ["scene"]
    assert body["questions"]["scene"] == {"type": "choice", "instructions": photos.SCENE_QUESTION,
                                          "criteria": photos.SCENE_CRITERIA}
    assert base64.b64decode(body["images"][0]) == (RECIPE / "pictures" / "n4_reference_menu.png").read_bytes()
    assert found.probabilities == {"commitment": 0.02, "reference": 0.98} and found.options == ("commitment",
                                                                                                 "reference")


def test_the_client_fails_closed():
    with pytest.raises(sc.SceneUnavailable):
        sc.SceneClient("http://127.0.0.1:9", timeout=2).decide(RECIPE / "pictures" / "o4_sticky_note.png", "")
    with pytest.raises(sc.SceneUnavailable, match="one of its options"):
        decide("owner_owes", 0.9)  # not an option of the two-way question
    with pytest.raises(sc.SceneUnavailable):
        decide("commitment", 1.7)


def test_a_rotated_or_large_picture_is_sent_upright_and_within_the_long_side_and_a_bmp_is_refused(tmp_path):
    exif = Image.Exif()
    exif[sc.ORIENTATION] = 6
    Image.linear_gradient("L").resize((3000, 2000)).convert("RGB").save(tmp_path / "side.jpg", exif=exif.tobytes())
    sent = Image.open(io.BytesIO(sc.picture_bytes(tmp_path / "side.jpg")))
    assert sent.size == (1333, 2000)  # upright (portrait) and the long side at 2000
    Image.new("RGB", (20, 20)).save(tmp_path / "x.bmp")
    with pytest.raises(ValueError, match="not a PNG"):
        sc.picture_bytes(tmp_path / "x.bmp")


# --- the handoff, the owner's answer, the wiring rule ---

def scene(answer, choice=None, confidence=0.9):
    return sc.Scene(answer, choice or answer, confidence, 0.70, ("commitment", "reference"), {})


@pytest.mark.parametrize("found, action", [(scene("commitment"), ho.EXTRACT), (scene("reference"), ho.KEEP)])
def test_an_answer_at_or_above_the_floor_acts(found, action):
    decision = ho.handoff(found)
    assert (decision.answer, decision.action, decision.by, decision.choices) == (found.answer, action, "decider", ())


def test_an_answer_under_the_floor_asks_the_owner_with_the_two_choices():
    decision = ho.handoff(scene("unsure", "reference", 0.45))
    assert (decision.answer, decision.action, decision.by) == ("unsure", ho.ASK, "decider")
    assert decision.choices == ("commitment", "reference") and decision.confidence == 0.45


@pytest.mark.parametrize("answer, action", [("commitment", ho.EXTRACT), ("reference", ho.KEEP)])
def test_the_owners_recorded_answer_acts_and_is_the_owners(answer, action):
    asked = ho.handoff(scene("unsure", "reference", 0.45))
    decision = ho.apply_owner_answer(asked, answer)
    assert (decision.answer, decision.action, decision.by, decision.confidence) == (answer, action, "owner", 0.45)
    with pytest.raises(ValueError, match="does not ask"):
        ho.apply_owner_answer(decision, "reference" if answer == "commitment" else "commitment")  # the first stands


def test_the_owner_cannot_answer_a_decision_that_did_not_ask_or_with_another_option():
    with pytest.raises(ValueError, match="does not ask"):
        ho.apply_owner_answer(ho.handoff(scene("reference")), "commitment")
    with pytest.raises(ValueError, match="must be one of"):
        ho.apply_owner_answer(ho.handoff(scene("unsure", "reference", 0.45)), "owner_owes")


class Calls:
    def __init__(self):
        self.extract = self.keep = 0

    def extracted(self):
        self.extract += 1
        return "extracted"

    def kept(self):
        self.keep += 1
        return "kept"


@pytest.mark.parametrize("found, result, extract, keep", [
    (scene("commitment"), "extracted", 1, 0),
    (scene("reference"), "kept", 0, 1),
    (scene("unsure", "reference", 0.45), None, 0, 0),
])
def test_the_wiring_rule(found, result, extract, keep):
    calls = Calls()
    assert ho.route(ho.handoff(found), calls.extracted, calls.kept) == result
    assert (calls.extract, calls.keep) == (extract, keep)


def test_unsure_calls_nothing_until_the_owner_answers_then_his_answer_runs():
    calls, asked = Calls(), ho.handoff(scene("unsure", "commitment", 0.55))
    assert ho.route(asked, calls.extracted, calls.kept) is None and (calls.extract, calls.keep) == (0, 0)
    assert ho.route(ho.apply_owner_answer(asked, "commitment"), calls.extracted, calls.kept) == "extracted"
    assert (calls.extract, calls.keep) == (1, 0)


def test_reference_never_calls_the_extractor_even_without_a_keep():
    calls = Calls()
    assert ho.route(ho.handoff(scene("reference")), calls.extracted) is None and calls.extract == 0


# --- the example ---

def by_picture(body):
    if "Juniper" in body["state"]:
        return scene_reply("reference", 0.96, {"commitment": 0.04, "reference": 0.96})
    return scene_reply("commitment", 0.74, {"commitment": 0.87, "reference": 0.13})


def test_the_example_prints_the_decision_the_confidence_the_action_and_the_cloud_calls(monkeypatch):
    with LocalDecider(by_picture) as server:
        monkeypatch.setattr(sc.SceneClient.__init__, "__defaults__", (server.url,))
        out = io.StringIO()
        with redirect_stdout(out):
            runpy.run_path(str(RECIPE / "example.py"), run_name="__main__")
    assert out.getvalue().splitlines() == [
        "n4_reference_menu.png: reference, confidence 0.96 (floor 0.70) -> keep: 0 cloud calls",
        "o4_sticky_note.png: commitment, confidence 0.74 (floor 0.70) -> extract: would call the extractor "
        "(1 cloud call)"]
    assert len(server.bodies) == 2 and all(len(b["images"]) == 1 for b in server.bodies)


def test_the_example_says_an_unsure_picture_waits_for_the_owner(monkeypatch):
    with LocalDecider(lambda body: scene_reply("reference", 0.45)) as server:
        monkeypatch.setattr(sc.SceneClient.__init__, "__defaults__", (server.url,))
        out = io.StringIO()
        with redirect_stdout(out):
            runpy.run_path(str(RECIPE / "example.py"), run_name="__main__")
    assert out.getvalue().splitlines()[0] == ("n4_reference_menu.png: unsure, confidence 0.45 (floor 0.70) -> ask the "
                                             "owner: 0 cloud calls until the owner answers commitment or reference")


def test_the_bundled_pictures_are_the_evaluation_sets_and_the_recipe_needs_only_the_stdlib_and_pillow():
    bench = RECIPE.parents[1] / "bench" / "images"
    for picture in (RECIPE / "pictures").iterdir():
        assert picture.read_bytes() == (bench / picture.name).read_bytes()
    allowed = {"__future__", "base64", "io", "json", "tomllib", "urllib", "dataclasses", "pathlib", "typing", "PIL",
               "scene", "handoff"}
    for name in ("scene.py", "handoff.py", "example.py"):
        lines = (RECIPE / name).read_text(encoding="utf-8").splitlines()
        imported = {line.split()[1].split(".")[0] for line in lines if line.strip().startswith(("import ", "from "))}
        assert imported <= allowed, (name, imported - allowed)
    assert "IMAGE_EVAL_2026-10-22_final.md" in (RECIPE / "README.md").read_text(encoding="utf-8")
