"""due.py: due strings to dates, for the priority rule's 30-day test (Dan's ruling 7, 17 Oct 2026)."""

from datetime import date

import pytest

from ratatoskr_demo.due import near, recording_date, resolve

FRI_2026_10_09 = date(2026, 10, 9)


def test_today_is_the_recordings_own_date_never_the_wall_clock():
    assert recording_date("2026-09-12T23:10:00Z") == date(2026, 9, 12)
    assert recording_date("2026-10-09T15:30:00+00:00") == date(2026, 10, 9)
    assert recording_date("") is None and recording_date(None) is None and recording_date("yesterday") is None


@pytest.mark.parametrize("due, today, expected", [
    ("March", date(2026, 10, 9), date(2027, 3, 1)),           # Dan: March heard in October is next March
    ("the 18th", date(2026, 10, 20), date(2026, 11, 18)),     # Dan: the 18th heard on the 20th is next month's
    ("the 18th", date(2026, 10, 6), date(2026, 10, 18)),
    ("week of the 20th", date(2026, 10, 3), date(2026, 10, 20)),
    ("tonight", FRI_2026_10_09, FRI_2026_10_09),
    ("tomorrow", FRI_2026_10_09, date(2026, 10, 10)),
    ("Tuesday", FRI_2026_10_09, date(2026, 10, 13)),
    ("Friday noon", FRI_2026_10_09, FRI_2026_10_09),
    ("next Friday", FRI_2026_10_09, date(2026, 10, 16)),
    ("Wednesday 7pm", FRI_2026_10_09, date(2026, 10, 14)),
    ("this week", FRI_2026_10_09, date(2026, 10, 11)),
    ("next week", FRI_2026_10_09, date(2026, 10, 16)),
    ("this weekend", FRI_2026_10_09, date(2026, 10, 10)),
    ("end of the month", FRI_2026_10_09, date(2026, 10, 31)),
    ("next month", FRI_2026_10_09, date(2026, 11, 1)),
    ("in two weeks", FRI_2026_10_09, date(2026, 10, 23)),
    ("in a couple of months", FRI_2026_10_09, date(2026, 12, 9)),
    ("15 January", FRI_2026_10_09, date(2027, 1, 15)),
    ("Jan 15th, 2028", FRI_2026_10_09, date(2028, 1, 15)),
    ("2026-12-25", FRI_2026_10_09, date(2026, 12, 25)),
    ("by May", FRI_2026_10_09, date(2027, 5, 1)),
    ("next spring", FRI_2026_10_09, date(2027, 3, 1)),
    ("Friday or next month", FRI_2026_10_09, FRI_2026_10_09),  # the earliest date named wins
])
def test_resolve(due, today, expected):
    assert resolve(due, today) == expected


@pytest.mark.parametrize("due", ["before the next call", "I may send it", "soon", "when she's back", "fall behind"])
def test_a_string_naming_no_date_is_unparseable(due):
    assert resolve(due, FRI_2026_10_09) is None


@pytest.mark.parametrize("due, today, is_near", [
    ("March", date(2026, 9, 12), False),          # rec_001 #2: about six months out, so it does not count
    ("March", date(2027, 2, 20), True),
    ("in 30 days", FRI_2026_10_09, True),         # 30 days is within
    ("in 31 days", FRI_2026_10_09, False),
    ("before the next call", FRI_2026_10_09, True),  # unparseable counts as near (the ruling)
    (None, FRI_2026_10_09, True),                 # no due string: near
    ("March", None, True),                        # no recording date to count from: as unparseable
])
def test_near(due, today, is_near):
    assert near(due, today) is is_near


def test_every_fixture_due_string_resolves_as_its_label_needs():
    """Only rec_001's "March" is more than 30 days out; every other stated date is within them."""
    from ratatoskr_demo.expected import load_expected

    far = [(r.recording_id, t.task.due_string) for r in load_expected() for t in r.tasks
           if not near(t.task.due_string, r.recorded_on)]
    assert far == [("rec_001", "March")]
