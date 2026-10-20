"""bedrock.py over a mocked bedrock-mantle: identity, record, failures, spend, the key."""

import json
import random

import httpx
import pytest
import respx

from ratatoskr_demo.bedrock import (
    BedrockClient, ModelIdentityError, ModelRecordChanged, NetworkLost, canonical, make_spend_ledger,
)
from ratatoskr_demo.errors import ConfigurationError, TransientError
from ratatoskr_demo.spend import SpendStop, cost_nano
from tests.conftest import extraction_json, make_settings

BASE = "https://bedrock-mantle.us-east-1.api.aws/v1"
RECORD = {
    "created": 1764460800, "id": "openai.gpt-oss-20b", "object": "model", "owned_by": "system",
    "status": "available",
    "data_retention": {"allowed_modes": ["aws_review", "default", "none", "provider_data_share"],
                       "mode": "default", "source": "model_default"},
}


class FakeMantle:
    """bedrock-mantle, simulated: records every chat body and its headers, replies as told."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.headers: list[httpx.Headers] = []
        self.model_name = "openai.gpt-oss-20b"
        self.content: str | None = extraction_json()
        self.finish_reason = "stop"
        self.usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        self.fail = None  # an exception to raise, or an int status to return
        self.record = json.loads(json.dumps(RECORD))
        self.model_calls = 0

    def models(self, request):
        self.model_calls += 1
        record = json.loads(json.dumps(self.record))
        random.shuffle(record["data_retention"]["allowed_modes"])  # as mantle does, on every call
        other = {"id": "openai.gpt-oss-120b", "object": "model"}
        return httpx.Response(200, json={"object": "list", "data": [other, record]})

    def chat(self, request):
        self.bodies.append(json.loads(request.content))
        self.headers.append(request.headers)
        if isinstance(self.fail, Exception):
            raise self.fail
        if isinstance(self.fail, int):
            return httpx.Response(self.fail, json={"error": {"message": "simulated"}})
        return httpx.Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion", "model": self.model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": self.content},
                         "finish_reason": self.finish_reason}],
            "usage": self.usage,
        })


def write_baseline(tmp_path, record=RECORD):
    path = tmp_path / "model_baseline.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def make_client(tmp_path, *, hard_stop=5.0, baseline=True):
    if baseline:
        write_baseline(tmp_path)
    settings = make_settings(tmp_path, {"spend": {"hard_stop_usd": hard_stop},
                                        "paths": {"model_baseline": str(tmp_path / "model_baseline.json")}})
    return BedrockClient(settings, make_spend_ledger(settings), http=httpx.Client(), sleep=lambda s: None), settings


@pytest.fixture
def mantle():
    fake = FakeMantle()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/models").mock(side_effect=fake.models)
        router.post(f"{BASE}/chat/completions").mock(side_effect=fake.chat)
        yield fake


@pytest.fixture
def bedrock(tmp_path, mantle):
    client, _ = make_client(tmp_path)
    return client, mantle


MESSAGES = [{"role": "user", "content": "hello"}]


# --- the model record ---

def test_the_record_matches_its_baseline_whatever_order_mantle_lists_the_modes_in(tmp_path, mantle):
    client, _ = make_client(tmp_path)
    for _ in range(5):
        client.check_model_record()  # shuffled every call, never a difference


def test_a_changed_record_stops_before_any_call(tmp_path, mantle):
    client, _ = make_client(tmp_path)
    mantle.record["status"] = "deprecated"
    with pytest.raises(ModelRecordChanged, match="status"):
        client.chat(MESSAGES, purpose="test")
    assert mantle.bodies == []


def test_a_new_retention_mode_is_a_difference_too(tmp_path, mantle):
    client, _ = make_client(tmp_path)
    mantle.record["data_retention"]["allowed_modes"].append("provider_training")
    with pytest.raises(ModelRecordChanged, match="data_retention"):
        client.check_model_record()


def test_a_missing_baseline_refuses_to_call_bedrock(tmp_path, mantle):
    client, _ = make_client(tmp_path, baseline=False)
    with pytest.raises(ConfigurationError, match="baseline"):
        client.chat(MESSAGES, purpose="test")
    assert mantle.bodies == []


def test_the_model_not_being_listed_stops(tmp_path, mantle):
    client, _ = make_client(tmp_path)
    mantle.record["id"] = "openai.gpt-oss-20b-v2"
    with pytest.raises(ModelRecordChanged, match="not listed"):
        client.check_model_record()


def test_the_record_is_checked_once_before_the_first_call(tmp_path, mantle):
    client, _ = make_client(tmp_path)
    client.chat(MESSAGES, purpose="test")
    client.chat(MESSAGES, purpose="test")
    assert mantle.model_calls == 1


def test_canonical_sorts_lists_of_plain_values_only():
    assert canonical({"a": ["b", "a"], "n": [{"x": 2}, {"x": 1}]}) == {"a": ["a", "b"], "n": [{"x": 2}, {"x": 1}]}


# --- model identity ---

def test_a_response_naming_another_model_stops_and_its_cost_is_still_counted(tmp_path, mantle):
    client, settings = make_client(tmp_path)
    mantle.model_name = "openai.gpt-oss-120b"
    with pytest.raises(ModelIdentityError):
        client.chat(MESSAGES, purpose="test")
    assert client.spend.total_nano() == cost_nano(100, 50, 0.07, 0.30)
    assert isinstance(ModelIdentityError("x"), ConfigurationError)


def test_the_request_names_only_the_configured_model(tmp_path, mantle):
    client, _ = make_client(tmp_path)
    client.chat(MESSAGES, purpose="test")
    assert mantle.bodies[0]["model"] == "openai.gpt-oss-20b"


# --- spend: reserve, settle, release, unanswered ---

def test_a_answered_call_is_settled_at_its_reported_usage(tmp_path, mantle):
    client, _ = make_client(tmp_path)
    result = client.chat(MESSAGES, purpose="test")
    assert (result.input_tokens, result.output_tokens) == (100, 50)
    assert client.spend.total_nano() == cost_nano(100, 50, 0.07, 0.30)
    assert client.spend.summary()["settled"] == 1


@pytest.mark.parametrize("failure", [httpx.ConnectError("dns"), httpx.ConnectTimeout("connect"),
                                     httpx.PoolTimeout("pool"), httpx.WriteError("write")],
                         ids=["dns-or-refused", "connect-timeout", "pool-timeout", "write-failed"])
def test_a_lost_network_is_released_and_never_counted(tmp_path, mantle, failure):
    client, _ = make_client(tmp_path)
    client.check_model_record()
    mantle.fail = failure
    with pytest.raises(NetworkLost) as info:
        client.chat(MESSAGES, purpose="test")
    assert info.value.kind == "network" and isinstance(info.value, TransientError)
    assert client.spend.total_nano() == 0 and client.spend.summary()["calls"] == 0


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("waiting"), httpx.ReadError("reset after send"),
                                     httpx.RemoteProtocolError("server disconnected")],
                         ids=["timeout-waiting", "reset-after-send", "disconnected"])
def test_a_request_that_went_out_and_got_no_answer_stays_at_worst_case(tmp_path, mantle, failure):
    client, _ = make_client(tmp_path)
    client.check_model_record()
    mantle.fail = failure
    with pytest.raises(TransientError) as info:
        client.chat(MESSAGES, purpose="test")
    assert not isinstance(info.value, NetworkLost)
    summary = client.spend.summary()
    assert summary["calls"] == 1 and summary["at_worst_case"] == 1 and client.spend.total_nano() > 0


@pytest.mark.parametrize("status, kind", [(429, TransientError), (500, TransientError), (503, TransientError),
                                          (401, ConfigurationError), (403, ConfigurationError),
                                          (400, ConfigurationError), (404, ConfigurationError)])
def test_http_errors_are_sorted_and_kept_at_worst_case(tmp_path, mantle, status, kind):
    client, _ = make_client(tmp_path)
    client.check_model_record()
    mantle.fail = status
    with pytest.raises(kind):
        client.chat(MESSAGES, purpose="test")
    assert client.spend.summary()["at_worst_case"] == 1


def test_the_hard_stop_refuses_before_anything_is_sent(tmp_path, mantle):
    client, _ = make_client(tmp_path, hard_stop=0.0001)  # one call's worst case is ~$0.0006
    client.check_model_record()
    with pytest.raises(SpendStop):
        client.chat(MESSAGES, purpose="test")
    assert mantle.bodies == []
    assert client.spend.summary()["refused"] == 1 and client.spend.total_nano() == 0


# --- the key ---

def test_the_key_travels_only_in_the_authorization_header(tmp_path, mantle, caplog):
    caplog.set_level("DEBUG")
    client, settings = make_client(tmp_path)
    client.chat(MESSAGES, purpose="test")
    assert mantle.headers[0]["authorization"] == "Bearer test-bedrock-key"
    assert "test-bedrock-key" not in json.dumps(mantle.bodies)
    assert "test-bedrock-key" not in caplog.text
    assert "test-bedrock-key" not in settings.spend_path.read_text(encoding="utf-8")
    assert "test-bedrock-key" not in repr(settings)


def test_the_network_recheck_waits_about_a_minute_between_tries(tmp_path, monkeypatch):
    import ratatoskr_demo.bedrock as bedrock_module

    waits, tries = [], {"n": 0}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def connect(address, timeout):
        tries["n"] += 1
        if tries["n"] < 3:
            raise OSError("still down")
        return Conn()

    monkeypatch.setattr(bedrock_module.socket, "create_connection", connect)
    client, _ = make_client(tmp_path)
    client._sleep = waits.append
    client.wait_for_network()
    assert waits == [60.0, 60.0, 60.0] and tries["n"] == 3
