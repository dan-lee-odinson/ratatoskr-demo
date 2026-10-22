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

"""The second image evaluation's eight new pictures and the labels for all twenty-four (Dan's ruling 50).

The sixteen pictures of ruling 47B stay as committed (render.py, labels.json: neither is touched). Eight more are
drawn with render.py's own render(), the same type and spacing: three where someone owes the owner something,
three reference pictures with no promise in either direction, and two where the owner owes, written to mirror two
of the first three (the wording matches, the direction differs). Fictional throughout; every capitalised name on
the new pictures is checked here against every fixture, the first labels and the stop list before anything is
written. The labels are written before any model is asked about the pictures:

    python bench/images/render_v2.py     writes the eight new bench/images/*.png and bench/images/labels_v2.json
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("render_v1", HERE / "render.py")
v1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v1)

# Scene labels for the first sixteen, by the ruling's three classes (unsure where the label-writer would not commit).
# The eight that must not leave the machine are held by the image gate before any scene is asked; their labels say
# what each would be if it were cleared.
SCENES_V1 = {
    "o1_business_card": "reference",  # contact details, nothing owed
    "o2_whiteboard_deposit": "owner_owes",  # pay the deposit to Fenella by Friday
    "o3_conference_slide": "unsure",  # a call for abstracts: an invitation, or an action the owner means to take
    "o4_sticky_note": "owner_owes",  # promised Wilhelmina the tent, Sunday
    "o5_meeting_agenda": "owner_owes",  # action for me: circulate the test plan
    "o6_bookshop_receipt": "reference",
    "o7_flyer": "reference",  # an event poster with a date and no promise
    "o8_todo_list": "owner_owes",
    "s1_medical_intake": "reference", "s2_prescription_label": "reference", "s3_pay_stub": "reference",
    "s4_bank_statement": "reference", "s5_confidential_letter": "reference", "s6_counselling_card": "reference",
    "s7_id_card": "reference", "s8_hr_memo": "reference",
}

# (name, lines, OCR key words, expected tasks, scene, expected waiting-on text)
NEW = [
    ("n1_owed_note", ["Ines: dataset to Dan by Tuesday"],
     ["Ines", "dataset", "Tuesday"], [v1.task([["dataset"]], "research")], "owed_to_owner",
     "the dataset, from Ines, by Tuesday"),
    ("n2_owed_whiteboard", ["Teodor → Dan: thermal data, next week"],
     ["Teodor", "thermal data", "next week"], [v1.task([["thermal"]], "research")], "owed_to_owner",
     "the thermal data, from Teodor, next week"),
    ("n3_owed_message", ["I'll get you the invoice Friday.", "Sam"],
     ["invoice", "Friday", "Sam"], [v1.task([["invoice"]], "work")], "owed_to_owner",
     "the invoice, from Sam, on Friday"),
    ("n4_reference_menu", ["Juniper Lane Café", "Flat white  3.20", "Cardamom bun  2.80",
                           "Soup of the day  5.50", "Open 8 am to 4 pm"],
     ["Juniper Lane", "Flat white", "Soup"], [], "reference", None),
    ("n5_reference_poster", ["Orchard Hill Lantern Walk", "Sunday 15 November, 5 pm",
                             "Meet at the Corn Exchange steps", "Free, all welcome"],
     ["Lantern Walk", "November", "Corn Exchange"], [], "reference", None),
    ("n6_reference_catalog", ["Brightmoor Outdoor Catalogue  page 14", "Trail stove  89.00", "Titanium mug  24.00",
                              "Dry bag 20 L  16.50"],
     ["Brightmoor", "Trail stove", "Titanium"], [], "reference", None),
    ("n7_owes_whiteboard", ["Dan → Teodor: thermal data, next week"],  # mirrors n2
     ["Teodor", "thermal data", "next week"], [v1.task([["thermal"]], "research")], "owner_owes", None),
    ("n8_owes_note", ["Invoice to Sam by Friday"],  # mirrors n3
     ["Invoice", "Sam", "Friday"], [v1.task([["invoice"]], "work")], "owner_owes", None),
]
MIRRORED = [["n2_owed_whiteboard.png", "n7_owes_whiteboard.png"], ["n3_owed_message.png", "n8_owes_note.png"]]
OWNER = "Dan"  # the owner, named on the pictures as the extraction prompt names him; not a fictional third party


def fixture_text() -> str:
    """Every fixture, the first labels and the stop list, as one lower-case text to check new names against."""
    parts = [p.read_text(encoding="utf-8") for d in (REPO / "tests" / "fixtures", REPO / "bench" / "fixtures")
             for p in d.rglob("*.json")]
    parts.append((HERE / "labels.json").read_text(encoding="utf-8"))
    parts.append((REPO / "config.toml").read_text(encoding="utf-8"))
    return "\n".join(parts).lower()


# The fictional people and places on the new pictures (the owner aside), each checked as a whole word.
FICTIONAL = ["Ines", "Teodor", "Sam", "Juniper Lane", "Cardamom", "Orchard Hill", "Lantern Walk", "Corn Exchange",
             "Brightmoor"]


def main() -> None:
    known = fixture_text()
    clashes = sorted(n for n in FICTIONAL if re.search(rf"\b{re.escape(n.lower())}\b", known))
    if clashes:
        raise SystemExit(f"render_v2: names already in a fixture, the first labels or the stop list: {clashes}")
    images = [{"file": f"{name}.png", "set": kind, "expected_gate": "hold" if kind == "sensitive" else "clear",
               "ocr_keywords": keywords, "expected_tasks": tasks, "scene": SCENES_V1[name]}
              for name, kind, _, keywords, tasks in v1.IMAGES]
    for name, lines, keywords, tasks, scene, waiting in NEW:
        v1.render(name, lines)
        images.append({"file": f"{name}.png", "set": "ordinary", "expected_gate": "clear", "ocr_keywords": keywords,
                       "expected_tasks": tasks, "scene": scene,
                       **({"expected_waiting_on": waiting} if waiting else {})})
    labels = {
        "about": ("Ruling 50's image evaluation: the expected outcome of every picture, the sixteen of ruling 47B and "
                  "eight new ones, written before any model was asked about the new ones (21 Oct 2026). Fictional, "
                  "rendered by bench/images/render.py and render_v2.py. labels.json is not edited: its fields are "
                  "copied here as they stand. Not edited after the run: a label found wrong afterwards is reported "
                  "as a disagreement."),
        "conventions": ("expected_gate, ocr_keywords, expected_tasks: as in labels.json. scene: what the picture "
                        "represents, one of owner_owes (the owner promised something to a specific person, or owes "
                        "an action by a date), owed_to_owner (a specific person promised the owner something, or the "
                        "owner is waiting on them), reference (information worth keeping, no promise in either "
                        "direction), or unsure where the label-writer would not commit. expected_tasks are judged "
                        "only where the scene answer sends the picture to extraction. expected_waiting_on "
                        "(owed_to_owner only): what the owner is waiting for, from whom, by when. mirrored: two pairs "
                        "with matching wording and the opposite direction. Two names in the ruling's examples were "
                        "already in fixtures (Priya, Marcus) and are replaced by Ines and Teodor; the wording is "
                        "otherwise the ruling's."),
        "mirrored": MIRRORED,
        "images": images,
    }
    (HERE / "labels_v2.json").write_bytes((json.dumps(labels, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"wrote {len(NEW)} new pictures and labels_v2.json ({len(images)} pictures)")


if __name__ == "__main__":
    main()
