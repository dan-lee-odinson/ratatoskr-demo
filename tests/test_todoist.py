"""§7 Todoist writer over a mocked /sync endpoint."""

import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from ratatoskr_demo.errors import ConfigurationError, TerminalError, TransientError
from ratatoskr_demo.extract import Contact, Task
from ratatoskr_demo.route import RoutedTask
from ratatoskr_demo.todoist import (
    SYNC_URL, TodoistClient, build_description, build_task_args, task_command_uuid, task_temp_id,
)
from tests.conftest import FakeTodoist

CONTACT = Contact(name="Priya Venkataraman", organisation="Aldrin Microsystems", role=None,
                  context="Met at a meetup.")


def client():
    return TodoistClient("tok", http=httpx.Client())


def test_command_uuids_are_deterministic_per_recording_and_ordinal():
    assert task_command_uuid("rec_001", 1) == task_command_uuid("rec_001", 1)
    assert task_command_uuid("rec_001", 1) != task_command_uuid("rec_001", 2)
    assert task_command_uuid("rec_001", 1) != task_temp_id("rec_001", 1)


def test_the_description_carries_notes_contact_and_pocket_id():
    text = build_description("Spring hiring.", CONTACT, "rec_001", 2)
    assert text == ("Spring hiring.\n\nFrom: Priya Venkataraman, Aldrin Microsystems — Met at a meetup.\n"
                    "Pocket id: rec_001 #2")


def test_the_description_without_notes_starts_with_from():
    assert build_description(None, CONTACT, "rec_001", 1).startswith("From: Priya")


def test_task_args_use_a_due_object_and_the_pocket_label():
    task = RoutedTask(task=Task(content="Email the deck", due_string="Friday", notes=None))
    args = build_task_args(task, recording_id="rec_001", ordinal=1, contact=CONTACT, project_id="p9", label="pocket")
    assert args == {
        "content": "Email the deck",
        "description": build_description(None, CONTACT, "rec_001", 1),
        "project_id": "p9",
        "labels": ["pocket"],
        "due": {"string": "Friday"},
    }


@respx.mock
def test_an_existing_inbox_project_is_found_by_name():
    respx.post(SYNC_URL).mock(return_value=httpx.Response(200, json={"projects": [
        {"id": "p1", "name": "Other", "is_deleted": False, "is_archived": False},
        {"id": "p2", "name": "Ratatoskr Demo", "is_deleted": False, "is_archived": False},
    ]}))
    assert client().ensure_project("Ratatoskr Demo") == "p2"
    form = parse_qs(respx.calls.last.request.content.decode())
    assert form["sync_token"] == ["*"] and json.loads(form["resource_types"][0]) == ["projects"]
    assert respx.calls.last.request.headers["Authorization"] == "Bearer tok"


def test_a_missing_inbox_project_is_created_once():
    fake = FakeTodoist()
    with respx.mock:
        respx.post(SYNC_URL).mock(side_effect=fake.handler)
        c = client()
        pid = c.ensure_project("Ratatoskr Demo")
        assert c.ensure_project("Ratatoskr Demo") == pid
    assert fake.projects == {"Ratatoskr Demo": pid}


def test_create_task_sends_item_add_with_the_deterministic_uuid():
    fake = FakeTodoist()
    with respx.mock:
        respx.post(SYNC_URL).mock(side_effect=fake.handler)
        task_id = client().create_task({"content": "x", "project_id": "p"}, recording_id="rec_001", ordinal=3)
    command = json.loads(fake.payloads[-1]["commands"])[0]
    assert command["type"] == "item_add"
    assert command["uuid"] == task_command_uuid("rec_001", 3)
    assert fake.tasks[task_id] == {"content": "x", "project_id": "p"}


def test_resending_the_same_command_creates_nothing_new():
    fake = FakeTodoist()
    with respx.mock:
        respx.post(SYNC_URL).mock(side_effect=fake.handler)
        c = client()
        c.create_task({"content": "x"}, recording_id="rec_001", ordinal=1)
        c.create_task({"content": "x"}, recording_id="rec_001", ordinal=1)
    assert len(fake.tasks) == 1


@pytest.mark.parametrize("response, expected", [
    (httpx.Response(429), TransientError),
    (httpx.Response(503), TransientError),
    (httpx.Response(401), ConfigurationError),
    (httpx.Response(400), TerminalError),
])
@respx.mock
def test_http_errors_are_sorted_by_kind(response, expected):
    respx.post(SYNC_URL).mock(return_value=response)
    with pytest.raises(expected):
        client().create_task({"content": "x"}, recording_id="r", ordinal=1)


@respx.mock
def test_a_network_failure_is_transient():
    respx.post(SYNC_URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(TransientError):
        client().ensure_project("Ratatoskr Demo")


@respx.mock
def test_a_rejected_command_is_terminal_and_names_keys_only():
    uuid = task_command_uuid("r", 1)
    respx.post(SYNC_URL).mock(return_value=httpx.Response(200, json={"sync_status": {uuid: {
        "error_tag": "INVALID_ARGUMENT_VALUE", "error_code": 20, "error": "Invalid argument value", "http_code": 400}}}))
    with pytest.raises(TerminalError) as info:
        client().create_task({"content": "private words"}, recording_id="r", ordinal=1)
    assert "private words" not in str(info.value)
    assert info.value.keys == ("content",)
