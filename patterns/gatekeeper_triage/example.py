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

"""Gatekeeper triage in twenty lines: one task, one Strands Decider read, six answers."""

from decider_client import DeciderClient, load_questions

questions = load_questions()  # questions.toml, next to this file
state = {
    "task": {"content": "Email Priya the orbital thermal bounds preprint", "due_string": "Friday",
             "notes": "She will pass it to her team lead."},
    "contact": {"name": "Priya Venkataraman", "organisation": "Aldrin Microsystems", "role": "thermal lead",
                "context": "Met at a space industry meetup; she runs thermal for small-sat radiators."},
}
triage = DeciderClient().triage(state, questions)  # tripwire, then one read of all six questions

print(f"verdict: {triage.verdict}{f' ({triage.reason})' if triage.reason else ''}")
for name, answer in triage.answers.items():
    print(f"  {name:<16} {str(answer.value):<9} confidence {answer.confidence:.2f}  "
          f"floor {answer.floor:.2f}  {'sure' if answer.sure else 'unsure'}")
print(f"file under: {triage.project or 'unsorted'}, priority {triage.priority or '-'} "
      f"(set by {triage.priority_source or 'nothing'}){', marked [maybe]' if triage.maybe else ''}")
