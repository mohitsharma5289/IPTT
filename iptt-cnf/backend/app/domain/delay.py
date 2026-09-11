"""Delay measurement - the single definition.

Decision 1: delay is counted in **working days**.
Decision 2: the baseline is **task.planned_finish**.

The legacy code had three different formulas. Every write path
(`bulk-update`, all three Excel importers, the legacy form handler) computed
`(actual_finish - planned_finish).days` against `task_execution.planned_finish`
- calendar days against the execution snapshot. Meanwhile
`task_execution_service` computed `working_days_between(...)` against
`task.planned_finish` - working days against the task row. A ten-day slip
spanning two weekends therefore read as 10 on one screen and 6 on another
(audit C5, C6).

This module is the only place delay is computed. Nothing else may subtract dates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.calendar import WorkingCalendar
from app.models.enums import ExecutionStatus

#: A node is "at risk" once any of its activities exceeds this many working days
#: late. Matches the legacy threshold (> 7) and the design document's §3.1.1.
AT_RISK_THRESHOLD_DAYS = 7

#: Severity bands for a single activity.
DELAY_ATTENTION_DAYS = 7
DELAY_CRITICAL_DAYS = 15


@dataclass(frozen=True, slots=True)
class DelayResult:
    delay_days: int
    is_late: bool
    severity: str  # "on-time" | "attention" | "critical"


def classify(delay_days: int) -> str:
    if delay_days >= DELAY_CRITICAL_DAYS:
        return "critical"
    if delay_days >= DELAY_ATTENTION_DAYS:
        return "attention"
    return "on-time"


def compute_delay_days(
    calendar: WorkingCalendar,
    planned_finish: date | None,
    actual_finish: date | None,
    circle: str | None = None,
) -> int:
    """Working days between the baseline finish and the actual finish.

    Zero when the activity is not finished, when there is no baseline, or when
    it finished early or on time.
    """
    return calendar.working_days_between(planned_finish, actual_finish, circle)


def evaluate(
    calendar: WorkingCalendar,
    planned_finish: date | None,
    actual_finish: date | None,
    circle: str | None = None,
) -> DelayResult:
    days = compute_delay_days(calendar, planned_finish, actual_finish, circle)
    return DelayResult(delay_days=days, is_late=days > 0, severity=classify(days))


def forecast_delay_days(
    calendar: WorkingCalendar,
    planned_finish: date | None,
    today: date,
    status: str,
    circle: str | None = None,
) -> int:
    """Slip accrued so far by an activity that is running late but not finished.

    The legacy dashboards could not show this at all: `delay_days` was only ever
    written when a finish date was recorded, so every delayed row had status
    "Completed". Two panels then filtered on `status != "Completed"` and were
    therefore permanently empty - the Stage Aging table and the circle
    dashboard's per-node delay column (audit H3, H4). Open work now has a number.
    """
    if status == ExecutionStatus.COMPLETED or planned_finish is None:
        return 0
    if today <= planned_finish:
        return 0
    return calendar.working_days_between(planned_finish, today, circle)
