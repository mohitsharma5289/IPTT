"""Working-day arithmetic.

Decision 1: every duration and every delay in IPTT is measured in working days.

Two defects in the legacy implementation are fixed here.

`add_working_days(d, n)` looped `while added < n`, so a negative `n` fell
through immediately and returned the input date unchanged. `get_last_n_working_days`
relied on stepping backwards with `add_working_days(current, -1)`, so it returned
the same date n times. That silently reduced the task-25 "no more than two
facilities in a rolling two-working-day window" rule to a single-day check
(audit C8) - the constraint the design document describes had never been enforced.

The legacy holiday set was also eight hardcoded Maharashtra 2026 dates applied to
all 20 circles, and empty for any date outside 2026 (audit M16). The calendar is
now built from the `holiday` table and is circle-aware.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta

SATURDAY = 5


class WorkingCalendar:
    """Weekends plus configured holidays. Immutable once built."""

    __slots__ = ("_national", "_by_circle")

    def __init__(self, holidays: Iterable[tuple[date, str | None]] = ()) -> None:
        national: set[date] = set()
        by_circle: dict[str, set[date]] = {}
        for holiday_date, circle in holidays:
            if circle is None:
                national.add(holiday_date)
            else:
                by_circle.setdefault(circle, set()).add(holiday_date)
        self._national = frozenset(national)
        self._by_circle = {k: frozenset(v) for k, v in by_circle.items()}

    @classmethod
    def from_session(cls, db, circles: Sequence[str] | None = None) -> "WorkingCalendar":
        from app.models import Holiday

        rows = db.query(Holiday.holiday_date, Holiday.circle).all()
        if circles is not None:
            allowed = set(circles)
            rows = [r for r in rows if r[1] is None or r[1] in allowed]
        return cls(rows)

    # -- predicates ---------------------------------------------------------

    def is_weekend(self, d: date) -> bool:
        return d.weekday() >= SATURDAY

    def is_holiday(self, d: date, circle: str | None = None) -> bool:
        if d in self._national:
            return True
        if circle is not None:
            return d in self._by_circle.get(circle, frozenset())
        return False

    def is_working_day(self, d: date, circle: str | None = None) -> bool:
        return not self.is_weekend(d) and not self.is_holiday(d, circle)

    # -- arithmetic ---------------------------------------------------------

    def next_working_day(self, d: date, circle: str | None = None) -> date:
        """The first working day strictly after `d`."""
        cur = d + timedelta(days=1)
        while not self.is_working_day(cur, circle):
            cur += timedelta(days=1)
        return cur

    def previous_working_day(self, d: date, circle: str | None = None) -> date:
        """The last working day strictly before `d`."""
        cur = d - timedelta(days=1)
        while not self.is_working_day(cur, circle):
            cur -= timedelta(days=1)
        return cur

    def snap_to_working_day(self, d: date, circle: str | None = None) -> date:
        """`d` itself if it is a working day, else the next one.

        The legacy planner never did this, so a kickoff date landing on a weekend
        or a holiday produced tasks scheduled on a non-working day. Project 12's
        own start date (2026-03-15) is a Sunday.
        """
        cur = d
        while not self.is_working_day(cur, circle):
            cur += timedelta(days=1)
        return cur

    def add_working_days(self, start: date, days: int, circle: str | None = None) -> date:
        """Move `days` working days from `start`. Negative moves backwards.

        `days == 0` returns `start` snapped forward to a working day.
        """
        if days == 0:
            return self.snap_to_working_day(start, circle)
        step = 1 if days > 0 else -1
        remaining = abs(days)
        cur = start
        while remaining:
            cur += timedelta(days=step)
            if self.is_working_day(cur, circle):
                remaining -= 1
        return cur

    def working_days_between(
        self, start: date | None, end: date | None, circle: str | None = None
    ) -> int:
        """Count working days in the half-open interval (start, end].

        Returns 0 when either bound is missing or `end` is not after `start`, so
        finishing early never reports as negative delay.
        """
        if start is None or end is None or end <= start:
            return 0
        days = 0
        cur = start
        while cur < end:
            cur += timedelta(days=1)
            if self.is_working_day(cur, circle):
                days += 1
        return days

    def last_n_working_days(
        self, end: date, n: int, circle: str | None = None
    ) -> list[date]:
        """The `n` working days ending at (and including) `end`, oldest first.

        This is the function the rolling-window constraint depends on. The legacy
        version returned `[end] * n`.
        """
        if n <= 0:
            return []
        days: list[date] = []
        cur = self.snap_to_working_day(end, circle) if not self.is_working_day(end, circle) else end
        while len(days) < n:
            days.append(cur)
            cur = self.previous_working_day(cur, circle)
        return list(reversed(days))


#: Weekends only. Useful for tests and as a safe default before holidays load.
WEEKENDS_ONLY = WorkingCalendar()
