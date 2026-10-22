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

"""Photo scene decision end to end: two bundled pictures, one read each, the handoff, the wiring rule. No keys, no
cloud: the extractor here only says it would call."""

from pathlib import Path

from handoff import handoff, route
from scene import SceneClient

PICTURES = {  # fictional, rendered; each with its words as an OCR on the laptop would hand them on
    "n4_reference_menu.png": "Juniper Lane Café\nFlat white 3.20\nCardamom bun 2.80\nSoup of the day 5.50\nOpen 8 am to 4 pm",
    "o4_sticky_note.png": "Promised Wilhelmina the spare tent\nfor her camping trip\ndrop it round on Sunday",
}
client = SceneClient()  # a Strands Decider vision server on 127.0.0.1:8001
for name, text in PICTURES.items():
    decision = handoff(client.decide(Path(__file__).with_name("pictures") / name, text))
    calls = route(decision, extract=lambda: "would call the extractor (1 cloud call)", keep=lambda: "0 cloud calls")
    print(f"{name}: {decision.answer}, confidence {decision.confidence:.2f} (floor {decision.floor:.2f}) -> "
          f"{decision.action}: {calls or '0 cloud calls until the owner answers ' + ' or '.join(decision.choices)}")
