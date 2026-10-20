"""The spend ledger: worst case before the call, the exact $5.00 boundary, settle / release / unanswered."""

import json

import pytest

from ratatoskr_demo.errors import ConfigurationError
from ratatoskr_demo.spend import (
    NANO, SpendLedger, SpendStop, cost_nano, input_token_bound, nano_to_usd, usd_to_nano,
)

PRICES = dict(price_in_per_m=0.07, price_out_per_m=0.30)


def ledger(tmp_path, hard_stop=5.0):
    return SpendLedger(tmp_path / "ledger" / "spend.json", hard_stop_usd=hard_stop, **PRICES)


def test_cost_is_exact_in_nanodollars_and_rounds_up():
    assert cost_nano(1_000_000, 0, 0.07, 0.30) == 70_000_000  # $0.07
    assert cost_nano(0, 1_000_000, 0.07, 0.30) == 300_000_000  # $0.30
    assert cost_nano(1, 1, 0.07, 0.30) == 70 + 300  # 7e-8 + 3e-7 dollars
    assert cost_nano(1, 0, 0.0721, 0.309) == 73  # 72.1 nanodollars rounds UP
    assert usd_to_nano(5.00) == 5 * NANO and nano_to_usd(5 * NANO) == 5.0


def test_a_call_bringing_the_total_to_exactly_the_limit_is_allowed_and_one_nanodollar_more_is_refused(tmp_path):
    # $5.00 at these prices: 2000 output tokens cost 600,000 nanodollars per call.
    book = ledger(tmp_path, hard_stop=nano_to_usd(3 * 600_000))
    for _ in range(3):
        book.reserve(purpose="t", model="m", input_tokens_bound=0, max_tokens=2000)
    assert book.total_nano() == 3 * 600_000  # exactly at the limit: allowed
    with pytest.raises(SpendStop):
        book.reserve(purpose="t", model="m", input_tokens_bound=1, max_tokens=0)  # 70 nanodollars over: refused
    assert book.summary()["refused"] == 1 and book.total_nano() == 3 * 600_000


def test_the_five_dollar_boundary_itself(tmp_path):
    book = ledger(tmp_path)  # the shipped limit, $5.00
    # 71,428,550 input tokens at $0.07/M plus 5 output tokens at $0.30/M is exactly $5.00.
    assert cost_nano(71_428_550, 5, 0.07, 0.30) == 5 * NANO
    book.reserve(purpose="t", model="m", input_tokens_bound=71_428_550, max_tokens=5)
    assert book.total_nano() == 5 * NANO  # exactly at the limit: allowed
    with pytest.raises(SpendStop, match="nothing was sent"):
        book.reserve(purpose="t", model="m", input_tokens_bound=1, max_tokens=0)  # $0.00000007 over: refused


def test_settle_replaces_the_worst_case_with_the_reported_usage(tmp_path):
    book = ledger(tmp_path)
    r = book.reserve(purpose="extract", model="m", input_tokens_bound=5000, max_tokens=2000)
    assert book.total_nano() == r.worst_nano == cost_nano(5000, 2000, 0.07, 0.30)
    book.settle(r, input_tokens=900, output_tokens=150)
    assert book.total_nano() == cost_nano(900, 150, 0.07, 0.30)
    assert book.summary()["by_purpose"] == {"extract": nano_to_usd(cost_nano(900, 150, 0.07, 0.30))}


def test_release_removes_a_call_that_never_went_out(tmp_path):
    book = ledger(tmp_path)
    r = book.reserve(purpose="t", model="m", input_tokens_bound=100, max_tokens=100)
    book.release(r)
    assert book.total_nano() == 0 and book.summary()["calls"] == 0


def test_a_call_with_no_answer_stays_at_worst_case(tmp_path):
    book = ledger(tmp_path)
    r = book.reserve(purpose="t", model="m", input_tokens_bound=100, max_tokens=2000)
    book.unanswered(r, "ReadTimeout")
    assert book.total_nano() == r.worst_nano and book.summary()["at_worst_case"] == 1


def test_a_crash_between_reserve_and_settle_leaves_the_worst_case_on_file(tmp_path):
    book = ledger(tmp_path)
    r = book.reserve(purpose="t", model="m", input_tokens_bound=100, max_tokens=2000)
    reopened = ledger(tmp_path)  # a new process reads the file
    assert reopened.total_nano() == r.worst_nano


def test_an_unreadable_ledger_refuses_every_call_rather_than_starting_from_zero(tmp_path):
    book = ledger(tmp_path)
    book.path.parent.mkdir(parents=True)
    book.path.write_text("{ half a file", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="unreadable"):
        book.reserve(purpose="t", model="m", input_tokens_bound=1, max_tokens=1)


def test_the_file_holds_counts_and_purposes_never_text(tmp_path):
    book = ledger(tmp_path)
    r = book.reserve(purpose="extract", model="openai.gpt-oss-20b", input_tokens_bound=10, max_tokens=10)
    book.settle(r, input_tokens=5, output_tokens=5)
    data = json.loads(book.path.read_text(encoding="utf-8"))
    assert set(data) == {"calls", "refused", "total_usd", "hard_stop_usd"}
    assert set(data["calls"][0]) == {"n", "at", "purpose", "model", "status", "worst_nano", "nano",
                                     "input_tokens", "output_tokens"}


def test_the_input_bound_never_undercounts_a_byte_level_tokenizer():
    payload = {"messages": [{"role": "user", "content": "naïve café — ünïcode"}]}
    assert input_token_bound(payload) >= len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def test_a_stale_lock_left_by_a_crash_is_cleared(tmp_path):
    import os
    import time

    book = ledger(tmp_path)
    lock = book.path.with_name(book.path.name + ".lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("99999", encoding="utf-8")
    old = time.time() - 600
    os.utime(lock, (old, old))
    book.reserve(purpose="t", model="m", input_tokens_bound=1, max_tokens=1)
    assert not lock.exists()
