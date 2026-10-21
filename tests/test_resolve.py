"""Dan's ruling 43 (21 Oct 2026): the owner files an unsorted task from its Telegram buttons.

One test per branch (file, leave, a repeat tap, an unknown task, another chat, Todoist refusing), the keyboard's
order against fixed probabilities, the 64-byte bound, and the courier's cycle sending the keyboard.
"""

import json
import logging

import httpx
import pytest
import respx

from ratatoskr_demo import resolve
from ratatoskr_demo.commands import TelegramReceiver, telegram_get_updates
from ratatoskr_demo.errors import TerminalError, TransientError
from ratatoskr_demo.ledger import Ledger, LedgerEntry
from ratatoskr_demo.notify import Notifier
from ratatoskr_demo.state import State
from ratatoskr_demo.todoist import SYNC_URL, TodoistClient
from tests.conftest import ScriptedModel, make_settings, runtime_for, wrist_messages
from tests.test_decider import attach, decider_answers

OWNER = 4242
PROJECTS = ["research", "school", "work", "writing", "community", "personal", "other"]
RID = "c0ffee00-1111-2222-3333-444455556666"
SID = resolve.short_id(RID, 1)
MESSAGE_TEXT = "unsorted · high · 0.55\nEmail the reviewer the revised figure\ncommit yes 0.91 · sens no 0.80 · prio waiting"


class FakeTelegram:
    def __init__(self):
        self.edits, self.answers = [], []

    def edit_message(self, message_id, text, reply_markup=None):
        self.edits.append((message_id, text, reply_markup))

    def answer_callback(self, callback_id, text=None):
        self.answers.append((callback_id, text))


class FakeTodoistLabels:
    def __init__(self, fail=None):
        self.updates, self.fail = [], fail

    def update_labels(self, task_id, labels, *, command_uuid):
        if self.fail is not None:
            raise self.fail
        self.updates.append((task_id, labels, command_uuid))


def ledger_with_pick(tmp_path):
    ledger = Ledger(tmp_path / "processed.json")
    entry = LedgerEntry("done", "title", "2026-10-21T10:00:00Z", "2026-10-21T10:01:00Z", todoist_task_ids=["t1"],
                        picks={SID: resolve.pick_record(ordinal=1, task_id="t1", labels=["pocket"], priority="high",
                                                        p="0.55")})
    ledger.put(RID, entry)
    return State(ledger)


def tap(key, *, chat=OWNER, sid=SID, keyboard=True):
    message = {"message_id": 77, "chat": {"id": chat}, "text": MESSAGE_TEXT}
    if keyboard:
        message["reply_markup"] = resolve.keyboard(sid, PROJECTS)
    return {"id": "cb-1", "data": resolve.callback_data(sid, key), "message": message}


def make(tmp_path, todoist=None):
    state = ledger_with_pick(tmp_path)
    telegram, todoist = FakeTelegram(), todoist or FakeTodoistLabels()
    resolver = resolve.Resolver(state, telegram, todoist, OWNER, PROJECTS, clock=lambda: 1792600000.0)
    return state, telegram, todoist, resolver


def pick(state):
    return state.pick_find(SID)[1]


# --- the branches ---

def test_a_tap_files_the_existing_task_records_the_owner_and_rewrites_the_first_line(tmp_path):
    state, telegram, todoist, resolver = make(tmp_path)
    resolver.handle(tap("research"))
    [(task_id, labels, uuid)] = todoist.updates
    assert (task_id, labels) == ("t1", ["pocket", "proj:research"])  # the label the sorted path gives; nothing else
    assert pick(state)["project_by"] == "owner" and pick(state)["project"] == "research"
    assert pick(state)["at"].startswith("2026-")
    [(message_id, text, markup)] = telegram.edits
    assert message_id == 77 and markup is None  # the keyboard goes
    assert text.split("\n")[0] == "research · high · 0.55 → filed by owner"
    assert text.split("\n")[1:] == MESSAGE_TEXT.split("\n")[1:]  # nothing else in the message changes
    assert telegram.answers == [("cb-1", None)]  # the spinner stops


def test_leave_unsorted_changes_nothing_in_todoist_and_says_so(tmp_path):
    state, telegram, todoist, resolver = make(tmp_path)
    resolver.handle(tap(resolve.LEAVE))
    assert todoist.updates == []
    assert (pick(state)["project_by"], pick(state)["project"]) == ("owner", "unsorted")
    [(_, text, markup)] = telegram.edits
    assert text.split("\n")[0] == "unsorted · high · 0.55 → left by owner" and markup is None
    assert telegram.answers == [("cb-1", None)]


@pytest.mark.parametrize("first, said", [("research", "already filed as research"), ("unsorted", "left unsorted")])
def test_a_repeat_tap_answers_and_touches_neither_todoist_nor_the_ledger(tmp_path, first, said):
    state, telegram, todoist, resolver = make(tmp_path)
    resolver.handle(tap(first))
    calls, ledger_bytes = len(todoist.updates), (tmp_path / "processed.json").read_bytes()
    for key in ("work", resolve.LEAVE, "research"):
        resolver.handle(tap(key))
    assert len(todoist.updates) == calls and (tmp_path / "processed.json").read_bytes() == ledger_bytes
    assert telegram.answers[1:] == [("cb-1", said)] * 3 and len(telegram.edits) == 1


@pytest.mark.parametrize("data", ["pick:0123456789:research", "pick:" + SID + ":astrology", "garbage", "pick::work"])
def test_an_unknown_task_or_project_answers_unknown_task_and_does_nothing(tmp_path, data):
    state, telegram, todoist, resolver = make(tmp_path)
    before = (tmp_path / "processed.json").read_bytes()
    resolver.handle({**tap("research"), "data": data})
    assert telegram.answers == [("cb-1", "unknown task")] and telegram.edits == [] and todoist.updates == []
    assert (tmp_path / "processed.json").read_bytes() == before


def test_a_tap_from_another_chat_is_answered_with_nothing_and_logged(tmp_path, caplog):
    state, telegram, todoist, resolver = make(tmp_path)
    with caplog.at_level(logging.WARNING, logger="ratatoskr_demo"):
        resolver.handle(tap("research", chat=999))
    assert telegram.answers == [("cb-1", None)] and telegram.edits == [] and todoist.updates == []
    assert "project_by" not in pick(state)
    assert any(r.levelno == logging.WARNING and "another chat" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("error", [TransientError("todoist down", kind="network"), TerminalError("rejected")])
def test_todoist_refusing_keeps_the_buttons_writes_no_ledger_and_a_second_tap_works(tmp_path, error):
    state, telegram, todoist, resolver = make(tmp_path, FakeTodoistLabels(fail=error))
    resolver.handle(tap("work"))
    [(_, text, markup)] = telegram.edits
    assert text.split("\n")[0] == "work · high · 0.55 → not filed (Todoist error), tap again"
    assert markup == resolve.keyboard(SID, PROJECTS)  # the buttons stay, so the owner can tap again
    assert "project_by" not in pick(state)
    todoist.fail = None
    resolver.handle(tap("work"))
    assert todoist.updates and pick(state)["project"] == "work"
    assert telegram.edits[-1][1].split("\n")[0] == "work · high · 0.55 → filed by owner"


# --- the keyboard ---

def test_the_buttons_follow_deciders_probabilities_best_first_then_leave_unsorted():
    probabilities = {"work": 0.41, "research": 0.33, "community": 0.17, "other": 0.06, "school": 0.03}
    board = resolve.keyboard(SID, PROJECTS, probabilities)
    texts = [b["text"] for row in board["inline_keyboard"] for b in row]
    assert texts == ["work", "research", "community", "other", "school", "writing", "personal", "Leave unsorted"]
    assert [len(row) for row in board["inline_keyboard"]] == [4, 4]
    assert board["inline_keyboard"][1][-1]["callback_data"] == f"pick:{SID}:unsorted"
    # without the probabilities, Decider's top choice alone goes first; the rest keep the config order
    assert [b["text"] for b in resolve.keyboard(SID, PROJECTS, None, top="writing")["inline_keyboard"][0]] == [
        "writing", "research", "school", "work"]


def test_callback_data_stays_within_telegrams_64_bytes():
    sid = resolve.short_id("f" * 200, 9999)  # however long the recording id, the short id is ten characters
    assert len(sid) == 10
    board = resolve.keyboard(sid, PROJECTS)
    assert all(len(b["callback_data"].encode("utf-8")) <= 64 for row in board["inline_keyboard"] for b in row)
    long_key = "x" * 60
    texts = [b["text"] for row in resolve.keyboard(sid, PROJECTS + [long_key])["inline_keyboard"] for b in row]
    assert long_key not in texts and len(texts) == len(PROJECTS) + 1  # an over-long key is left off, not sent


# --- the wiring: the receiver, the Todoist client, the cycle ---

def test_the_receiver_asks_for_taps_and_hands_them_to_the_resolver(tmp_path):
    seen = []
    receiver = TelegramReceiver(lambda offset: [], None, OWNER, ledger_with_pick(tmp_path), None,
                                resolver=type("R", (), {"handle": lambda self, q: seen.append(q)})())
    receiver.handle({"update_id": 5, "callback_query": tap("research")})
    assert seen and seen[0]["data"] == f"pick:{SID}:research"
    with respx.mock() as router:
        route = router.get(url__regex=r".*/getUpdates.*").mock(return_value=httpx.Response(200, json={"result": []}))
        telegram_get_updates("TEST-TOKEN", httpx.Client())(None)
    assert "callback_query" in route.calls[0].request.url.params["allowed_updates"]


def test_the_todoist_update_changes_only_the_labels_of_the_existing_task(tmp_path, services):
    todoist, _, _ = services
    client = TodoistClient("test-todoist-token", http=httpx.Client())
    project = client.ensure_project("Ratatoskr Demo")
    task_id = client.create_task({"content": "Email the reviewer", "description": "d", "project_id": project,
                                  "labels": ["pocket"], "priority": 4}, recording_id=RID, ordinal=1)
    before = dict(todoist.tasks[task_id])
    client.update_labels(task_id, ["pocket", "proj:research"], command_uuid="u-1")
    after = todoist.tasks[task_id]
    assert after["labels"] == ["pocket", "proj:research"] and len(todoist.tasks) == 1
    assert {k: v for k, v in after.items() if k != "labels"} == {k: v for k, v in before.items() if k != "labels"}


def test_the_notifier_edits_and_answers_in_its_own_chat_only():
    with respx.mock() as router:
        edit = router.post("https://api.telegram.org/botT/editMessageText").mock(return_value=httpx.Response(200))
        answer = router.post("https://api.telegram.org/botT/answerCallbackQuery").mock(
            return_value=httpx.Response(200))
        send = router.post("https://api.telegram.org/botT/sendMessage").mock(return_value=httpx.Response(200))
        notifier = Notifier("T", OWNER, http=httpx.Client())
        notifier.edit_message(77, "research · high · 0.55 → filed by owner")
        notifier.answer_callback("cb-1", "already filed as research")
        notifier.send_with_keyboard("unsorted · high · 0.55", resolve.keyboard(SID, PROJECTS))
    assert json.loads(edit.calls[0].request.content) == {"chat_id": OWNER, "message_id": 77, "parse_mode": "HTML",
                                                         "text": "research · high · 0.55 → filed by owner"}
    assert json.loads(answer.calls[0].request.content) == {"callback_query_id": "cb-1",
                                                           "text": "already filed as research"}
    sent = json.loads(send.calls[0].request.content)
    assert sent["chat_id"] == OWNER and sent["reply_markup"]["inline_keyboard"]


def unsure_project(body):
    answers = decider_answers()
    answers["answers"]["project"] = {"type": "choice", "choice": "work", "confidence": 0.41,
                                     "probabilities": {"work": 0.41, "research": 0.33, "community": 0.17,
                                                       "other": 0.06, "school": 0.03}}
    return httpx.Response(200, json=answers)


def test_a_once_cycle_sends_the_keyboard_with_an_unsorted_task_and_records_the_pick(tmp_path, clock, services):
    todoist, telegram, router = services
    fake = attach(router)
    fake.reply = unsure_project
    settings = make_settings(tmp_path, {"route": {"backend": "decider"}})
    runtime_for(settings, ScriptedModel(), clock, router=fake.router()).cycle.run()
    keyed = [m for m in telegram.messages if "reply_markup" in m]
    # every task Decider was asked about and unsure of; the "Met ... no follow-ups" contact note is never asked
    asked = [t for t in wrist_messages(telegram) if not t.startswith("unsorted · - · -")]
    assert keyed and [m["text"] for m in keyed] == asked
    assert any(t.startswith("unsorted · - · -") for t in wrist_messages(telegram))  # sent, without buttons
    first_row = [b["text"] for b in keyed[0]["reply_markup"]["inline_keyboard"][0]]
    assert first_row == ["work", "research", "community", "other"]
    assert keyed[0]["text"].startswith("unsorted · ")
    entries = json.loads(settings.ledger_path.read_text(encoding="utf-8"))
    picks = {sid: p for e in entries.values() for sid, p in e.get("picks", {}).items()}
    assert len(picks) == len(keyed) and all(p["task_id"] in todoist.tasks and "project_by" not in p
                                            for p in picks.values())
    assert not any("content" in p or "text" in p for p in picks.values())  # no task text in the ledger


def test_a_sorted_task_gets_no_keyboard_and_no_pick(tmp_path, clock, services):
    todoist, telegram, router = services
    fake = attach(router)  # Decider sure of the project (research 0.91)
    settings = make_settings(tmp_path, {"route": {"backend": "decider"}})
    runtime_for(settings, ScriptedModel(), clock, router=fake.router()).cycle.run()
    assert wrist_messages(telegram) and not any("reply_markup" in m for m in telegram.messages)
    entries = json.loads(settings.ledger_path.read_text(encoding="utf-8"))
    assert not any("picks" in e for e in entries.values())  # entries are written exactly as before ruling 43
