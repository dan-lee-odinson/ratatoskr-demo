"""patterns/gatekeeper_triage: the stdlib Decider client, the pattern's rules, the Strands hook, the example.

The client uses urllib, so a real local HTTP server on 127.0.0.1 stands in for
Strands Decider here (loopback only; the test guard still blocks the network).
"""

import io
import json
import runpy
import sys
import threading
import tomllib
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from pathlib import Path

import pytest

PATTERN = Path(__file__).resolve().parents[1] / "patterns" / "gatekeeper_triage"
sys.path.insert(0, str(PATTERN))

import decider_client as dc  # noqa: E402

from tests.test_decider import decider_answers  # noqa: E402


class LocalDecider:
    """A tiny HTTP server with Decider's POST /v1/systemone, replying as told."""

    def __init__(self, reply=None):
        self.reply = reply or (lambda body: (200, decider_answers()))
        self.bodies = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.bodies.append(body)
                code, payload = outer.reply(body)
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


STATE = {"task": {"content": "Email Priya the preprint", "notes": None, "due_string": "Friday"},
         "contact": {"name": "Priya", "organisation": "Aldrin Microsystems", "role": None, "context": "Meetup."}}


def test_the_pattern_questions_are_the_couriers_route_toml_word_for_word():
    pattern = tomllib.loads((PATTERN / "questions.toml").read_text(encoding="utf-8"))
    courier = tomllib.loads(resources.files("ratatoskr_demo").joinpath("prompts", "route.toml").read_text("utf-8"))
    pattern.pop("gatekeeper")
    assert pattern == courier


def test_one_read_carries_the_six_typed_questions():
    with LocalDecider() as server:
        triage = dc.DeciderClient(server.url).triage(STATE, dc.load_questions())
    [body] = server.bodies
    assert body["state"] == STATE
    assert {n: q["type"] for n, q in body["questions"].items()} == {
        "project": "choice", "someone_waiting": "noul", "hard_deadline": "noul", "worth_researching_soon": "noul",
        "real_commitment": "noul", "sensitive": "noul"}
    assert all("min_confidence" not in q and "defaults" not in q for q in body["questions"].values())
    assert (triage.verdict, triage.project, triage.priority, triage.maybe) == ("proceed", "research", "high", False)
    assert triage.priority_source == "someone_waiting"


def test_the_tripwire_holds_before_decider_is_asked():
    with LocalDecider() as server:
        triage = dc.DeciderClient(server.url).triage({"note": "Lunch with VARGRHEIM"}, dc.load_questions())
    assert triage.verdict == "hold" and triage.reason == "tripwire:vargrheim" and server.bodies == []


@pytest.mark.parametrize("p, verdict, reason", [(0.95, "hold", "sensitive"), (0.55, "hold", "sensitive-unsure"),
                                                (0.45, "hold", "sensitive-unsure"), (0.05, "proceed", None)])
def test_sensitive_yes_or_unsure_holds(p, verdict, reason):
    triage = dc.judge(decider_answers(sensitive=p), dc.load_questions())
    assert (triage.verdict, triage.reason) == (verdict, reason)


def test_floors_make_an_unsure_project_unsorted_and_a_unsure_priority_fall_back():
    q = dc.load_questions()
    assert dc.judge(decider_answers(project=("work", 0.5)), q).project is None
    unsure = dc.judge(decider_answers(project=("school", 0.9), waiting=0.55, soon=0.5), q)
    assert (unsure.priority, unsure.priority_source) == ("high", "default:waiting")
    assert dc.judge(decider_answers(waiting=0.1, deadline=0.1, soon=0.9), q).priority == "medium"
    assert dc.judge(decider_answers(waiting=0.1, deadline=0.1, soon=0.1), q).priority == "low"
    soon_unsure = dc.judge(decider_answers(project=("writing", 0.9), waiting=0.1, deadline=0.1, soon=0.5), q)
    assert (soon_unsure.priority, soon_unsure.priority_source) == ("low", "default:soon")
    assert dc.judge(decider_answers(commit=0.1), q).maybe is True  # sure it is a pleasantry
    assert dc.judge(decider_answers(commit=0.45), q).maybe is False  # unsure: not marked


def test_the_pattern_counts_a_deadline_only_when_the_caller_says_it_is_near():
    """The client reads no dates (standard library only): the caller passes deadline_near (Dan's ruling 7)."""
    q = dc.load_questions()
    answers = decider_answers(waiting=0.1, deadline=0.9, soon=0.1)
    assert (dc.judge(answers, q).priority, dc.judge(answers, q).priority_source) == ("high", "hard_deadline")
    far = dc.judge(answers, q, deadline_near=False)
    assert (far.priority, far.priority_source) == ("low", "neither")
    with LocalDecider(lambda body: (200, answers)) as server:
        assert dc.DeciderClient(server.url).triage(STATE, q, deadline_near=False).priority == "low"


@pytest.mark.parametrize("args, expected", [
    ((True, None, True, None, None), ("high", "someone_waiting")),
    ((None, True, True, None, "writing"), ("high", "hard_deadline")),
    ((False, True, False, True, "research"), ("medium", "worth_researching_soon")),
    ((None, False, True, True, "research"), ("medium", "default:waiting")),
    ((False, None, True, True, "school"), ("high", "default:deadline")),
    ((False, False, True, None, "writing"), ("low", "default:soon")),
    ((False, False, True, False, "school"), ("low", "neither")),
    ((None, None, True, None, None), (None, "default:waiting")),
])
def test_the_pattern_priority_rule_is_the_couriers(args, expected):
    from ratatoskr_demo.route import TODOIST_PRIORITY, priority_rule

    defaults = {"research": "medium", "school": "high", "writing": "low"}
    assert dc.priority_rule(*args, defaults) == expected
    level, source = priority_rule(*args, defaults)
    assert (level, source) == ((TODOIST_PRIORITY[expected[0]] if expected[0] else None), expected[1])


def test_an_unreachable_decider_fails_closed_by_default(tmp_path):
    q = dc.load_questions()
    assert dc.DeciderClient("http://127.0.0.1:9", timeout=2).triage(STATE, q).verdict == "hold"
    custom = tmp_path / "q.toml"
    custom.write_text((PATTERN / "questions.toml").read_text(encoding="utf-8").replace(
        'on_unavailable = "hold"', 'on_unavailable = "proceed"'), encoding="utf-8")
    assert dc.DeciderClient("http://127.0.0.1:9", timeout=2).triage(STATE, dc.load_questions(custom)).verdict == "proceed"


@pytest.mark.parametrize("heard", ["Vargrime", "Vargrhyme", "var grime", "VARGRHEIM"])
def test_the_pattern_tripwire_holds_a_misspelled_or_split_name(heard):
    assert dc.tripwire_hit(f"Speaker 1: I work with {heard} on that.", (dc.TripwireTerm("vargrheim"),)) == "vargrheim"


@pytest.mark.parametrize("word", ["Varga", "Vargas", "Vargo"])
def test_the_pattern_tripwire_lets_a_dissimilar_prefix_word_through(word):
    assert dc.tripwire_hit(f"Speaker 1: {word} sent the checklist.", (dc.TripwireTerm("vargrheim"),)) is None


def test_the_pattern_tripwire_trips_on_no_fixture_recording_but_the_one_naming_the_client():
    tripped = []
    for directory in (PATTERN.parents[1] / "tests" / "fixtures", PATTERN.parents[1] / "bench" / "fixtures"):
        for path in sorted(directory.glob("rec_*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))["data"]
            if dc.tripwire_hit(f"{data.get('title')}\n{data.get('transcript') or ''}", (dc.TripwireTerm("vargrheim"),)):
                tripped.append(data["id"])
    assert tripped == ["rec_002"]


def test_the_pattern_tripwire_reads_spellings_from_questions_toml():
    q = dc.load_questions()
    assert q.tripwire == (dc.TripwireTerm("vargrheim", ("vargrime",)),)
    assert dc.tripwire_hit("notes: VGH", (dc.TripwireTerm("vargrheim", ("vgh",)),)) == "vargrheim"


@pytest.mark.parametrize("word, code", [("Vargrheim", "V626"), ("Robert", "R163"), ("Rupert", "R163"),
                                        ("Ashcraft", "A261"), ("Tymczak", "T522"), ("Pfister", "P236"), ("", "")])
def test_soundex_matches_the_published_examples(word, code):
    assert dc.soundex(word) == code


def test_the_client_needs_nothing_but_the_standard_library():
    imports = [line for line in (PATTERN / "decider_client.py").read_text(encoding="utf-8").splitlines()
               if line.startswith(("import ", "from "))]
    third_party = [line for line in imports if not line.split()[1].split(".")[0] in
                   {"__future__", "json", "math", "re", "tomllib", "urllib", "dataclasses", "pathlib", "typing"}]
    assert third_party == []


# --- the Strands hook ---

def event_for(tool, arguments):
    from strands.hooks import BeforeToolCallEvent

    return BeforeToolCallEvent(agent=None, selected_tool=None, invocation_state={},
                               tool_use={"name": tool, "input": arguments, "toolUseId": "t1"})


def registry_with(hook):
    from strands.hooks import HookRegistry

    registry = HookRegistry()
    registry.add_hook(hook)
    return registry


def test_the_hook_lets_a_clear_tool_call_run_and_records_the_decision():
    import strands_hook

    with LocalDecider() as server:
        hook = strands_hook.GatekeeperHook(dc.DeciderClient(server.url), tools={"add_task"})
        event = event_for("add_task", {"content": "Email Priya the preprint"})
        registry_with(hook).invoke_callbacks(event)
    assert event.cancel_tool is False
    assert server.bodies[0]["state"] == {"tool": "add_task", "arguments": {"content": "Email Priya the preprint"}}
    assert hook.decisions[0][1].verdict == "proceed"


def test_the_hook_cancels_a_sensitive_tool_call():
    import strands_hook

    with LocalDecider(lambda body: (200, decider_answers(sensitive=0.97))) as server:
        hook = strands_hook.GatekeeperHook(dc.DeciderClient(server.url), tools={"add_task"})
        event = event_for("add_task", {"content": "Cover Mara's talk; she starts medical leave"})
        registry_with(hook).invoke_callbacks(event)
    assert isinstance(event.cancel_tool, str) and "HELD" in event.cancel_tool and "sensitive" in event.cancel_tool


def test_the_hook_trips_on_the_client_name_without_asking_decider():
    import strands_hook

    with LocalDecider() as server:
        hook = strands_hook.GatekeeperHook(dc.DeciderClient(server.url))
        event = event_for("add_task", {"content": "Send Vargrheim the numbers"})
        registry_with(hook).invoke_callbacks(event)
    assert "tripwire" in event.cancel_tool and server.bodies == []


def test_the_hook_ignores_tools_it_does_not_guard():
    import strands_hook

    hook = strands_hook.GatekeeperHook(dc.DeciderClient("http://127.0.0.1:9"), tools={"add_task"})
    event = event_for("calculator", {"expression": "2+2"})
    registry_with(hook).invoke_callbacks(event)
    assert event.cancel_tool is False and hook.decisions == []


def test_the_demo_agent_really_offers_add_task_as_a_tool_and_registers_the_hook():
    """A plain function is not a Strands tool: the agent must list add_task, or the model never sees it."""
    import strands_hook

    hook = strands_hook.GatekeeperHook(dc.DeciderClient("http://127.0.0.1:9"), tools={"add_task"})
    agent = strands_hook.make_agent(strands_hook.bedrock_model("test-key"), hook)
    assert agent.tool_names == ["add_task"]
    spec = agent.tool_registry.get_all_tool_specs()[0]
    assert spec["name"] == "add_task" and set(spec["inputSchema"]["json"]["properties"]) == {"content", "notes"}
    assert strands_hook.add_task(content="x") == "added: x" and strands_hook.ADDED[-1]["content"] == "x"


def test_the_demo_model_is_the_openai_compatible_provider_on_bedrock_mantle():
    import strands_hook

    model = strands_hook.bedrock_model("test-key")
    assert type(model).__name__ == "OpenAIModel"
    assert model.get_config()["model_id"] == "openai.gpt-oss-20b"
    assert model.client_args["base_url"] == "https://bedrock-mantle.us-east-1.api.aws/v1"


def test_the_pinned_strands_version_is_the_one_the_hook_was_written_against():
    from importlib.metadata import version

    pyproject = tomllib.loads((PATTERN.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "strands-agents[openai]==1.61.1" in pyproject["project"]["optional-dependencies"]["pattern"]
    assert version("strands-agents") == "1.61.1"


# --- the example ---

def test_the_example_prints_the_verdict_and_the_six_answers(monkeypatch):
    with LocalDecider() as server:
        monkeypatch.setattr(dc.DeciderClient.__init__, "__defaults__", (server.url,))
        out = io.StringIO()
        with redirect_stdout(out):
            runpy.run_path(str(PATTERN / "example.py"), run_name="__main__")
    text = out.getvalue()
    assert text.splitlines()[0] == "verdict: proceed"
    for name in ("project", "priority", "real_commitment", "sensitive"):
        assert name in text
    assert "file under: research, priority high (set by someone_waiting)" in text
    lines = (PATTERN / "example.py").read_text(encoding="utf-8").splitlines()
    code = lines[lines.index("# limitations under the License.") + 1:]  # below the license header (ruling 27)
    assert len(code) <= 22  # the README's "twenty-line example"


# --- gate_source_text: the source first, the arguments only as the fallback ---

MEDICAL = ("Speaker 1: Keep this between us, but I'm starting medical leave next week; my surgery got moved up. "
           "Speaker 2: I'll give your Tuesday talk for you.")


def by_text(sensitive_when: str):
    def reply(body):
        state = body["state"] if isinstance(body["state"], str) else json.dumps(body["state"])
        return 200, decider_answers(sensitive=0.94 if sensitive_when in state else 0.05)
    return reply


def test_gate_source_text_holds_a_sensitive_source_and_asks_only_the_sensitive_question():
    with LocalDecider(by_text("medical leave")) as server:
        verdict = dc.DeciderClient(server.url).gate_source_text(MEDICAL, dc.load_questions())
    assert (verdict.verdict, verdict.reason, verdict.windows_asked) == ("hold", "source-sensitive", 1)
    [body] = server.bodies
    assert body["state"].startswith("Speaker 1:") and list(body["questions"]) == ["sensitive"]
    assert body["questions"]["sensitive"]["instructions"].startswith("Does this part of the conversation")


@pytest.mark.parametrize("p, verdict, reason", [(0.05, "proceed", None), (0.5, "hold", "source-unsure")])
def test_gate_source_text_proceeds_on_clear_and_holds_on_unsure(p, verdict, reason):
    with LocalDecider(lambda body: (200, decider_answers(sensitive=p))) as server:
        result = dc.gate_source_text("Speaker 1: send the deck", client=dc.DeciderClient(server.url))
    assert (result.verdict, result.reason) == (verdict, reason)


def test_gate_source_text_trips_on_the_client_name_and_holds_when_decider_is_down():
    tripped = dc.gate_source_text("Vargrheim called", client=dc.DeciderClient("http://127.0.0.1:9"))
    assert tripped.reason == "tripwire:vargrheim"
    down = dc.gate_source_text("Speaker 1: hi", client=dc.DeciderClient("http://127.0.0.1:9", timeout=2))
    assert (down.verdict, down.reason) == ("hold", "decider-unavailable")


def test_long_source_text_is_split_into_windows_and_any_sensitive_window_holds():
    text = "\n".join(f"Speaker {i % 2 + 1}: " + "routine words " * 60 for i in range(6)) + "\n" + MEDICAL
    with LocalDecider(by_text("medical leave")) as server:
        verdict = dc.DeciderClient(server.url).gate_source_text(text, dc.load_questions())
    assert verdict.verdict == "hold" and verdict.windows_total >= 3 and len(server.bodies) == verdict.windows_asked


def test_the_hook_holds_on_the_source_text_even_when_the_tool_arguments_look_harmless():
    import strands_hook

    with LocalDecider(by_text("medical leave")) as server:
        hook = strands_hook.GatekeeperHook(dc.DeciderClient(server.url), tools={"add_task"}, source_text=MEDICAL)
        event = event_for("add_task", {"content": "Give Mara's Tuesday talk"})  # the detail dropped
        registry_with(hook).invoke_callbacks(event)
    assert "source text (source-sensitive)" in event.cancel_tool
    assert len(server.bodies) == 1 and isinstance(server.bodies[0]["state"], str)  # the arguments were never asked


class FakeAgent:
    """Stands in for the Strands agent: records each call, then asks for add_task as a model would."""

    def __init__(self, hook, built):
        self.hook, self.calls, self.events = hook, [], []
        built.append(self)

    def __call__(self, note):
        self.calls.append(note)
        event = event_for("add_task", {"content": "Email Priya the preprint by Friday"})
        registry_with(self.hook).invoke_callbacks(event)
        self.events.append(event)


def run_demo(note, reply):
    import strands_hook

    built, out = [], io.StringIO()
    with LocalDecider(reply) as server, redirect_stdout(out):
        strands_hook.main([note], client=dc.DeciderClient(server.url),
                          build_agent=lambda hook: FakeAgent(hook, built))
    return built, server.bodies, out.getvalue()


def test_the_hook_demo_holds_a_sensitive_note_before_the_agent_is_ever_called():
    built, bodies, out = run_demo(MEDICAL, by_text("medical leave"))
    assert built == []  # no agent built, none called: the note never reached the model
    assert len(bodies) == 1 and isinstance(bodies[0]["state"], str)
    assert "source: hold (source-sensitive)" in out and "held before the agent ran" in out


def test_the_hook_demo_runs_the_agent_on_a_clear_note_and_the_hook_still_gates_add_task():
    def reply(body):  # the note is clear; the add_task arguments are what Decider holds
        return 200, decider_answers(sensitive=0.05 if isinstance(body["state"], str) else 0.97)

    built, bodies, out = run_demo("Met Priya at the meetup. I promised to email her the preprint.", reply)
    [agent] = built
    assert len(agent.calls) == 1
    assert [type(body["state"]) for body in bodies] == [str, dict]  # the note first, then the tool call
    assert bodies[1]["state"]["tool"] == "add_task"
    assert "HELD by gatekeeper triage (sensitive)" in agent.events[0].cancel_tool
    assert "source: proceed" in out and "add_task: hold" in out


def test_without_source_text_the_hook_falls_back_to_the_arguments():
    import strands_hook

    with LocalDecider(by_text("medical leave")) as server:
        hook = strands_hook.GatekeeperHook(dc.DeciderClient(server.url), tools={"add_task"})
        event = event_for("add_task", {"content": "Give Mara's Tuesday talk"})
        registry_with(hook).invoke_callbacks(event)
    assert event.cancel_tool is False  # the arguments alone carry nothing sensitive: the known limit
