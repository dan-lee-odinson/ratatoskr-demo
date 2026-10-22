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

"""The final image evaluation's labels (Dan's ruling 54), derived from labels_v2.json by code.

The scene question became two-way: commitment (a promise in either direction) or reference. Every scene label of
labels_v2.json maps as MAPPING says; every other field is copied as it stands. labels.json and labels_v2.json are not
touched. Written before any model is asked about the pictures under the two-way question:

    python bench/images/labels_v3.py      writes bench/images/labels_v3.json
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAPPING = {"owner_owes": "commitment", "owed_to_owner": "commitment", "reference": "reference", "unsure": "unsure"}


def main() -> None:
    v2 = json.loads((HERE / "labels_v2.json").read_text(encoding="utf-8"))
    images = [{**x, "scene": MAPPING[x["scene"]]} for x in v2["images"]]
    labels = {
        "about": ("Ruling 54's final image evaluation: the twenty-four pictures of labels_v2.json, their scene labels "
                  "mapped to the two-way question, written by bench/images/labels_v3.py before any model was asked "
                  "under it (21 Oct 2026). labels.json and labels_v2.json are not edited. Not edited after the run: a "
                  "label found wrong afterwards is reported as a disagreement."),
        "mapping": ("scene: owner_owes -> commitment, owed_to_owner -> commitment, reference -> reference, unsure -> "
                    "unsure. Every other field is labels_v2.json's, as it stands (expected_waiting_on included, kept "
                    "as a record: the photo path no longer writes a waiting-on label)."),
        "conventions": v2["conventions"],
        "mirrored": v2["mirrored"],
        "images": images,
    }
    (HERE / "labels_v3.json").write_bytes((json.dumps(labels, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"wrote labels_v3.json ({len(images)} pictures)")


if __name__ == "__main__":
    main()
