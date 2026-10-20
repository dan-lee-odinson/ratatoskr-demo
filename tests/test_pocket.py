"""§4.1 Pocket client with HTTP mocked. Never hits the network."""

from datetime import date

import httpx
import pytest
import respx

from ratatoskr_demo.errors import ConfigurationError, TransientError
from ratatoskr_demo.pocket import BASE_URL, PocketClient, PocketContractError
from tests.conftest import FIXTURES

LIST = f"{BASE_URL}/public/recordings"


def client():
    return PocketClient("key-123", http=httpx.Client())


@respx.mock
def test_list_follows_pagination_and_sends_the_documented_parameters():
    route = respx.get(LIST).mock(side_effect=[
        httpx.Response(200, json={"data": [{"id": "a"}], "pagination": {"has_more": True}, "success": True}),
        httpx.Response(200, json={"data": [{"id": "b"}], "pagination": {"has_more": False}, "success": True}),
    ])
    items = client().list_recordings(date(2026, 9, 10), date(2026, 9, 13))
    assert [i["id"] for i in items] == ["a", "b"]
    first = route.calls[0].request
    assert first.headers["Authorization"] == "Bearer key-123"
    assert dict(first.url.params) == {"start_date": "2026-09-10", "end_date": "2026-09-13", "page": "1", "limit": "100"}
    assert route.calls[1].request.url.params["page"] == "2"


@respx.mock
def test_detail_asks_for_the_transcript():
    route = respx.get(f"{LIST}/rec_001").mock(
        return_value=httpx.Response(200, json={"data": {"id": "rec_001", "transcript": "hi"}, "success": True}))
    assert client().get_recording("rec_001")["transcript"] == "hi"
    assert route.calls.last.request.url.params["include_transcript"] == "true"


@pytest.mark.parametrize("response, expected", [
    (httpx.Response(429), TransientError),
    (httpx.Response(502), TransientError),
    (httpx.Response(401), ConfigurationError),
])
@respx.mock
def test_http_errors_are_sorted_by_kind(response, expected):
    respx.get(LIST).mock(return_value=response)
    with pytest.raises(expected):
        client().list_recordings(date(2026, 9, 1), date(2026, 9, 2))


@pytest.mark.parametrize("error", [httpx.ConnectError("dns"), httpx.ReadTimeout("slow")])
@respx.mock
def test_network_failures_are_transient(error):
    respx.get(LIST).mock(side_effect=error)
    with pytest.raises(TransientError):
        client().list_recordings(date(2026, 9, 1), date(2026, 9, 2))


@respx.mock
def test_an_undocumented_list_shape_fails_loud_with_keys():
    respx.get(LIST).mock(return_value=httpx.Response(200, json={"recordings": [], "ok": True}))
    with pytest.raises(PocketContractError) as info:
        client().list_recordings(date(2026, 9, 1), date(2026, 9, 2))
    assert info.value.keys == ("ok", "recordings")


def test_fixtures_mode_reads_files_instead_of_the_network():
    pocket = PocketClient(None, fixtures_dir=FIXTURES)
    assert [r["id"] for r in pocket.list_recordings(date.today(), date.today())] == [
        "rec_001", "rec_002", "rec_003", "rec_004", "rec_005"]
    assert pocket.get_recording("rec_005")["title"] == "Hallway chat"


def test_the_client_has_no_way_to_delete_or_change_anything():
    public = {name for name in dir(PocketClient) if not name.startswith("_")}
    assert public == {"list_recordings", "get_recording"}
