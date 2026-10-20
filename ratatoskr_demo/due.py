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

"""Due strings to dates, for the priority rule's 30-day test (Dan's ruling 7, 17 Oct 2026).

Todoist parses a task's due string itself; this module answers one question for
priority_rule(): is a stated deadline within 30 days of the recording? Code
only, standard library. "Today" is always the recording's own date, from its
envelope, never the wall clock, so a bench run scores the same whenever it is
run.

    resolve(due, today) -> date | None    the earliest date the string names; None if none
    near(due, today)    -> bool           within 30 days; no due string, or an
                                          unparseable one, counts as near (the ruling)

Recognised anywhere in the string, any case (the earliest date named wins):

    today, tonight, this morning / afternoon / evening, end of (the) day, asap
    tomorrow, the day after tomorrow
    a weekday (fri, friday ...): the next one on or after today; "next friday" a week later
    this week (its Sunday), next week (+7 days), (this) weekend (its Saturday), next weekend
    end of (the) month / this month (its last day), next month (its 1st), end of next month
    in N days / weeks / months / years ("in two weeks", "in a couple of months")
    next year, end of the year
    a month, with or without a day and a year (March, 15 March, March 15th, Mar 15, 2027):
        without a year, the next occurrence on or after today; a bare month is its 1st,
        or today in the current month. "may" counts only beside a day, a year or "in" /
        "by" / "early" / "mid" / "late" / "end of", since it is also a verb
    a day of the month as an ordinal (the 18th, week of the 20th): the next occurrence,
        so "the 18th" heard on the 20th is next month's
    an ISO date (2026-10-20)
    a season (spring, summer, autumn, fall, winter; starting 1 Mar, 1 Jun, 1 Sep, 1 Dec):
        today within it, else its next start; "next spring" the one after; "fall" counts
        only after "in" / "by" / "this" / "next" / "the" / "early" / "late"

Times of day ("7pm", "noon") are ignored. "Before the next call" names no date:
unparseable, so near.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

WINDOW_DAYS = 30

_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
             "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5,
             "sat": 5, "sunday": 6, "sun": 6}
_MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
           "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
           "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
_SEASONS = {"spring": 3, "summer": 6, "autumn": 9, "fall": 9, "winter": 12}
_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a couple of": 2, "a few": 3}

_MONTH = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(\d{4}))?"
_DAY_MONTH = re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}\b{_YEAR}")
_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+{_DAY}\b{_YEAR}")
_MONTH_ALONE = re.compile(rf"(?:\b(in|by|early|mid|late|of|until|before)\s+)?\b{_MONTH}\b(?:\s+(\d{{4}}))?")
_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_WEEKDAY = re.compile(r"\b(next\s+)?(" + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + r")\b")
_IN_N = re.compile(r"\b(?:in|within)\s+(a couple of|a few|an?|\d+|" + "|".join(k for k in _NUMBERS if " " not in k)
                   + r")\s+(day|week|month|year)s?\b")
_SEASON = re.compile(r"(?:\b(in|by|this|next|the|early|late|mid)\s+)?\b(spring|summer|autumn|fall|winter)\b")
_TODAY = re.compile(r"\b(today|tonight|this (?:morning|afternoon|evening)|end of (?:the )?day|eod|asap)\b")


def recording_date(timestamp: str | None) -> date | None:
    """The date of an ISO 8601 timestamp ("2026-10-09T15:30:00Z" -> 2026-10-09), as written; None if unreadable."""
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


def near(due: str | None, today: date | None, days: int = WINDOW_DAYS) -> bool:
    """True when the due string names a date within `days` of today, or names none we can read."""
    if not due or today is None:
        return True
    when = resolve(due, today)
    return when is None or (when - today).days <= days


def resolve(due: str, today: date) -> date | None:
    """The earliest date `due` names, counted from `today`; None if it names none we can read."""
    text = " ".join(due.lower().split())
    found: list[date] = []
    taken: list[tuple[int, int]] = []  # spans already read as part of a month date

    def add(when: date | None, span: tuple[int, int] | None = None) -> None:
        if when is not None:
            found.append(when)
            if span is not None:
                taken.append(span)

    for m in _ISO.finditer(text):
        add(_date(int(m[1]), int(m[2]), int(m[3])), m.span())
    for m in _DAY_MONTH.finditer(text):
        add(_month_day(_MONTHS[m[2]], int(m[1]), m[3], today), m.span())
    for m in _MONTH_DAY.finditer(text):
        add(_month_day(_MONTHS[m[1]], int(m[2]), m[3], today), m.span())
    for m in _MONTH_ALONE.finditer(text):
        if _inside(m.span(2), taken) or (m[2] == "may" and not m[1] and not m[3]):
            continue
        add(_bare_month(_MONTHS[m[2]], m[3], today))
    for m in _ORDINAL.finditer(text):
        if not _inside(m.span(), taken):
            add(_next_day_of_month(int(m[1]), today))
    for m in _WEEKDAY.finditer(text):
        ahead = (_WEEKDAYS[m[2]] - today.weekday()) % 7
        add(today + timedelta(days=ahead + (7 if m[1] else 0)))
    for m in _IN_N.finditer(text):
        add(_plus(today, _NUMBERS.get(m[1]) or int(m[1]), m[2]))
    for m in _SEASON.finditer(text):
        if m[2] == "fall" and not m[1]:
            continue
        add(_season(_SEASONS[m[2]], today, after=m[1] == "next"))
    if _TODAY.search(text):
        add(today)
    if re.search(r"\bday after tomorrow\b", text):
        add(today + timedelta(days=2))
    elif re.search(r"\btomorrow\b", text):
        add(today + timedelta(days=1))
    if re.search(r"\bthis week\b", text):
        add(today + timedelta(days=6 - today.weekday()))
    if re.search(r"\bnext week\b", text):
        add(today + timedelta(days=7))
    if re.search(r"\bnext weekend\b", text):
        add(today + timedelta(days=(5 - today.weekday()) % 7 + 7))
    elif re.search(r"\bweekend\b", text):
        add(today if today.weekday() == 6 else today + timedelta(days=(5 - today.weekday()) % 7))
    if re.search(r"\bend of next month\b", text):
        add(_last_day(_plus(today.replace(day=1), 1, "month")))
    elif re.search(r"\bnext month\b", text):
        add(_plus(today.replace(day=1), 1, "month"))
    if re.search(r"\b(?:end of (?:the )?month|month end|this month)\b", text):
        add(_last_day(today))
    if re.search(r"\bnext year\b", text):
        add(_plus(today, 1, "year"))
    if re.search(r"\bend of (?:the )?year\b", text):
        add(date(today.year, 12, 31))
    return min(found) if found else None


def _inside(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(start <= span[0] and span[1] <= end for start, end in spans)


def _date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_day(month: int, day: int, year: str | None, today: date) -> date | None:
    if year:
        return _date(int(year), month, day)
    for y in (today.year, today.year + 1, today.year + 2):  # 29 February may be up to four years out
        when = _date(y, month, day)
        if when is not None and when >= today:
            return when
    return None


def _bare_month(month: int, year: str | None, today: date) -> date:
    if year:
        return date(int(year), month, 1)
    if month == today.month:
        return today
    return date(today.year + (month < today.month), month, 1)


def _next_day_of_month(day: int, today: date) -> date | None:
    if not 1 <= day <= 31:
        return None
    start = today.replace(day=1)
    for k in range(13):
        first = _plus(start, k, "month")
        when = _date(first.year, first.month, day)
        if when is not None and when >= today:
            return when
    return None


def _season(month: int, today: date, *, after: bool) -> date:
    start = date(today.year, month, 1)
    if start > today:
        previous = date(today.year - 1, month, 1)
        start_now = previous if _plus(previous, 3, "month") > today else None
    else:
        start_now = start if _plus(start, 3, "month") > today else None
        start = date(today.year + 1, month, 1)
    if start_now is not None:  # we are in this season now
        return date(start_now.year + 1, month, 1) if after else today
    return start


def _plus(when: date, n: int, unit: str) -> date:
    if unit == "day":
        return when + timedelta(days=n)
    if unit == "week":
        return when + timedelta(weeks=n)
    months = n * (12 if unit == "year" else 1)
    year, month = divmod(when.month - 1 + months, 12)
    year, month = when.year + year, month + 1
    return date(year, month, min(when.day, calendar.monthrange(year, month)[1]))


def _last_day(when: date) -> date:
    return when.replace(day=calendar.monthrange(when.year, when.month)[1])
