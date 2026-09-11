"""Working-day arithmetic, including the two defects the audit found (C8)."""
from __future__ import annotations

from datetime import date

import pytest

from app.domain.calendar import WorkingCalendar

# 2026-03-13 Fri, 03-14 Sat, 03-15 Sun, 03-16 Mon
FRI = date(2026, 3, 13)
SAT = date(2026, 3, 14)
SUN = date(2026, 3, 15)
MON = date(2026, 3, 16)


@pytest.fixture
def cal() -> WorkingCalendar:
    return WorkingCalendar([(date(2026, 8, 15), None), (date(2026, 5, 1), "MH")])


def test_weekends_are_not_working_days(cal):
    assert cal.is_working_day(FRI)
    assert not cal.is_working_day(SAT)
    assert not cal.is_working_day(SUN)
    assert cal.is_working_day(MON)


def test_national_holiday_applies_to_every_circle(cal):
    assert not cal.is_working_day(date(2026, 8, 15), "TN")
    assert not cal.is_working_day(date(2026, 8, 15), None)


def test_circle_holiday_applies_only_to_that_circle(cal):
    """The legacy calendar applied Maharashtra's holidays to all 20 circles (M16)."""
    assert not cal.is_working_day(date(2026, 5, 1), "MH")
    assert cal.is_working_day(date(2026, 5, 1), "TN")


def test_add_working_days_skips_the_weekend(cal):
    assert cal.add_working_days(FRI, 1) == MON


def test_add_zero_snaps_forward_to_a_working_day(cal):
    """Project 12's own start date, 2026-03-15, is a Sunday."""
    assert cal.add_working_days(SUN, 0) == MON
    assert cal.add_working_days(FRI, 0) == FRI


def test_add_negative_working_days_moves_backwards(cal):
    """The legacy implementation looped `while added < days`, so a negative
    argument returned the input date unchanged."""
    assert cal.add_working_days(MON, -1) == FRI
    assert cal.add_working_days(MON, -3) == date(2026, 3, 11)


def test_last_n_working_days_returns_distinct_days(cal):
    """The legacy version returned [end] * n, which silently reduced the task-25
    rolling two-day window to a single-day check (audit C8)."""
    window = cal.last_n_working_days(MON, 2)
    assert window == [FRI, MON]
    assert len(set(window)) == 2


def test_last_n_working_days_spans_a_holiday(cal):
    # 2026-08-15 is a Saturday and a holiday; 08-14 Fri, 08-13 Thu
    window = cal.last_n_working_days(date(2026, 8, 17), 3)
    assert window == [date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 17)]


def test_working_days_between_excludes_weekends(cal):
    assert cal.working_days_between(FRI, MON) == 1
    assert cal.working_days_between(FRI, date(2026, 3, 20)) == 5


def test_working_days_between_is_never_negative(cal):
    assert cal.working_days_between(MON, FRI) == 0
    assert cal.working_days_between(MON, MON) == 0
    assert cal.working_days_between(None, MON) == 0
    assert cal.working_days_between(MON, None) == 0


def test_calendar_days_and_working_days_differ_across_a_weekend(cal):
    """The heart of decision 1. A ten-calendar-day slip spanning two weekends is
    six working days; the legacy code reported both numbers on different screens."""
    start, end = date(2026, 3, 13), date(2026, 3, 23)
    assert (end - start).days == 10
    assert cal.working_days_between(start, end) == 6
