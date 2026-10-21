"""Dan's ruling 39 Part 3 (rulings 40 and 41): the fresh evaluation's runner, with Bedrock and Decider mocked.

The 24 fresh conversations go through the courier's own cycle; a held conversation's extraction calls are read
from the spend ledger and its task writes from the Todoist adapter's record, never inferred.
"""

import json
import shutil

import httpx
import pytest
import respx

from ratatoskr_demo.bedrock import BedrockClient, make_spend_ledger
from ratatoskr_demo.config import Settings
from ratatoskr_demo.decider import DeciderClient
from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.route import load_route_questions
from ratatoskr_demo.source_gate import SourceGate
from tests.conftest import extraction_json, make_settings
from tests.test_bedrock import BASE, FakeMantle, write_baseline
from tests.test_decider import URL, attach, decider_answers
from tests.test_bench import bench

FRESH = bench.FRESH_DIR
# words only the sensitive conversations use: the fake gate holds a window that has one
PRIVATE = ("IVF", "cutting the telemetry team", "custody", "arrested", "panic attacks", "runway", "chemotherapy",
           "harassment", "bankruptcy", "without status", "pregnant", "sober")


class FreshMantle(FakeMantle):
    """Bedrock for the fresh set: each transcript's extraction is its labelled tasks."""

    def __init__(self, directory):
        super().__init__()
        labels = json.loads((directory / "labels.json").read_text(encoding="utf-8"))
        self.by_transcript = {}
        for c in labels["conversations"]:
            body = json.loads((directory / c["file"]).read_text(encoding="utf-8"))
            tasks = [{k: t[k] for k in ("content", "due_string", "notes")} for t in c["tasks"]]
            self.by_transcript[body["data"]["transcript"]] = extraction_json(c["contact"], tasks)
        self.extractions = 0

    def chat(self, request):
        user = json.loads(request.content)["messages"][1]["content"]
        self.extractions += 1
        self.content = next((reply for transcript, reply in self.by_transcript.items() if transcript in user),
                            extraction_json(tasks=[]))
        return super().chat(request)


def fresh_run(tmp_path, *, directory=FRESH, gate_holds=PRIVATE, task_reply=None):
    write_baseline(tmp_path)
    settings = make_settings(tmp_path, {"route": {"backend": "decider"},
                                        "paths": {"model_baseline": str(tmp_path / "model_baseline.json")}})
    mantle = FreshMantle(directory)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/models").mock(side_effect=mantle.models)
        router.post(f"{BASE}/chat/completions").mock(side_effect=mantle.chat)
        decider = attach(router)
        decider.gate_reply = lambda body: httpx.Response(200, json=decider_answers(
            sensitive=0.92 if any(word in body["state"] for word in gate_holds) else 0.05))
        if task_reply is not None:
            decider.reply = task_reply
        gate = SourceGate(DeciderClient(URL, http=httpx.Client()), load_route_questions(Settings())["sensitive"])
        client = BedrockClient(settings, make_spend_ledger(settings), http=httpx.Client())
        run = bench.run_fresh(settings, bedrock=client, router=decider.router(), gate=gate, directory=directory,
                              state_dir=tmp_path / "fresh-state", out=lambda line: None)
    run.versions = {"test": "mocked"}
    return run, settings, mantle, decider


def test_the_fresh_set_is_24_conversations_12_and_12_with_every_task_labelled():
    labels, sha = bench.load_fresh_labels()
    kinds = [c["set"] for c in labels["conversations"]]
    assert (len(kinds), kinds.count("sensitive"), kinds.count("ordinary")) == (24, 12, 12)
    ordinary = {t["content"] for c in labels["conversations"] if c["set"] == "ordinary" for t in c["tasks"]}
    assert len(ordinary) >= 20  # ruling 39: at least 20 distinct tasks for the project sort
    assert len(sha) == 64 and not (bench.WALKTHROUGH & {c["id"] for c in labels["conversations"]})


def test_held_conversations_have_no_extraction_and_no_writes_read_from_their_records(tmp_path):
    run, settings, mantle, decider = fresh_run(tmp_path)
    assert run.stopped is None and len(run.conversations) == 24
    held = [c for c in run.conversations if c.status == "hold"]
    written = [c for c in run.conversations if c.status == "done"]
    assert {c.kind for c in held} == {"sensitive"} and len(held) == 12
    assert all(c.reason == "source-sensitive" and c.extraction_calls == 0 and c.written == [] for c in held)
    assert {c.kind for c in written} == {"ordinary"} and len(written) == 12
    labels = {c["id"]: c for c in bench.load_fresh_labels()[0]["conversations"]}
    for c in written:  # the counts come from the ledger and the Todoist record, and they match what happened
        assert c.extraction_calls >= 1 and len(c.written) == len(labels[c.recording_id]["tasks"])
        assert all(f"Pocket id: {c.recording_id} #" in w["description"] for w in c.written)
    assert sum(c.extraction_calls for c in run.conversations) == mantle.extractions == bench.extract_calls(
        settings.spend_path)
    assert len(run.tasks) == len(run.asked) == sum(len(c["tasks"]) for c in labels.values())
    assert run.reads["gate"] and run.reads["task"]
    text = bench.render_fresh(run, settings=settings)
    for line in ("| sensitive conversations wrongly cleared (written to Todoist) | 0 of 12 |",
                 "| ordinary conversations unnecessarily held: source gate | 0 of 12 |",
                 "| Bedrock extraction calls for held conversations | 0 (over 12 held) |",
                 "| task writes for held conversations | 0 (over 12 held) |",
                 f"sha256 `{bench._grouped(run.labels_sha256)}`", "RESULTS.md's \"Label corrections\" section",
                 "the five recordings made on 21 Oct for the walkthrough video are in no count"):
        assert line in text, line
    assert "**Finding:**" not in text and "## Label disagreements\n\nNone recorded." in text


def test_a_sensitive_conversation_the_gate_misses_shows_its_extraction_as_a_finding(tmp_path):
    misses = tuple(w for w in PRIVATE if w != "IVF")  # the gate clears the grant report check-in
    held_at_task = lambda body: httpx.Response(200, json=decider_answers(  # noqa: E731
        sensitive=0.95 if "Rosalind" in json.dumps(body["state"]) else 0.05))
    run, settings, _, _ = fresh_run(tmp_path, gate_holds=misses, task_reply=held_at_task)
    [missed] = [c for c in run.conversations if c.recording_id == "fresh_13"]
    assert (missed.status, missed.reason, missed.written) == ("hold", "sensitive", [])
    assert missed.extraction_calls >= 1  # read from the ledger: it was extracted before the task check held it
    text = bench.render_fresh(run, settings=settings)
    assert "| sensitive conversations cleared by the source gate and held after extraction | 1 of 12 |" in text
    assert f"| Bedrock extraction calls for held conversations | {missed.extraction_calls} (over 12 held) |" in text
    assert "**Finding:** held conversations with extraction calls or task writes: fresh_13" in text


def test_a_walkthrough_recording_in_the_labels_is_refused_before_any_call(tmp_path):
    copy = tmp_path / "fresh"
    shutil.copytree(FRESH, copy)
    labels = json.loads((copy / "labels.json").read_text(encoding="utf-8"))
    labels["conversations"][0]["id"] = sorted(bench.WALKTHROUGH)[0]
    (copy / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="walkthrough"):
        fresh_run(tmp_path, directory=copy)


def test_the_todoist_record_answers_as_todoist_and_counts_by_pocket_id():
    record = bench.TodoistRecord()
    client = httpx.Client(transport=httpx.MockTransport(record.handler))
    from ratatoskr_demo.todoist import TodoistClient

    todoist = TodoistClient("local-record-only", http=client)
    project = todoist.ensure_project("Ratatoskr Demo")
    for n in (1, 2):
        todoist.create_task({"content": f"task {n}", "description": f"From: x\nPocket id: fresh_01 #{n}",
                             "project_id": project, "labels": ["pocket"]}, recording_id="fresh_01", ordinal=n)
    assert [a["content"] for a in record.items_for("fresh_01")] == ["task 1", "task 2"]
    assert record.items_for("fresh_02") == [] and record.items_for("fresh_0") == []
