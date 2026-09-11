"""Delay measurement under decisions 1 (working days) and 2 (task baseline)."""
from __future__ import annotations

from datetime import date

import pytest

from app.domain import delay as delay_rules
from app.domain.calendar import WorkingCalendar
from app.models.enums import ExecutionStatus


@pytest.fixture
def cal() -> WorkingCalendar:
    return WorkingCalendar([(date(2026, 8, 15), None)])


def test_finishing_on_the_baseline_is_not_a_delay(cal):
    d = date(2026, 3, 16)
    assert delay_rules.compute_delay_days(cal, d, d) == 0


def test_finishing_early_is_not_a_negative_delay(cal):
    assert delay_rules.compute_delay_days(cal, date(2026, 3, 20), date(2026, 3, 16)) == 0


def test_delay_is_counted_in_working_days_not_calendar_days(cal):
    """Decision 1. Every legacy write path used `(actual - planned).days`."""
    planned, actual = date(2026, 3, 13), date(2026, 3, 23)
    assert (actual - planned).days == 10
    assert delay_rules.compute_delay_days(cal, planned, actual) == 6


def test_missing_baseline_yields_no_delay(cal):
    assert delay_rules.compute_delay_days(cal, None, date(2026, 3, 20)) == 0


def test_unfinished_work_yields_no_recorded_delay(cal):
    assert delay_rules.compute_delay_days(cal, date(2026, 3, 20), None) == 0


def test_severity_bands(cal):
    assert delay_rules.classify(0) == "on-time"
    assert delay_rules.classify(6) == "on-time"
    assert delay_rules.classify(7) == "attention"
    assert delay_rules.classify(14) == "attention"
    assert delay_rules.classify(15) == "critical"


def test_evaluate_reports_lateness(cal):
    result = delay_rules.evaluate(cal, date(2026, 3, 2), date(2026, 3, 23))
    assert result.is_late
    assert result.delay_days == 15
    assert result.severity == "critical"


def test_open_work_accrues_forecast_delay(cal):
    """Audit H3/H4: `delay_days` was only ever written when a finish date was
    recorded, so every delayed row had status Completed - and the two panels that
    filtered on `status != 'Completed'` were permanently empty."""
    accrued = delay_rules.forecast_delay_days(
        cal,
        planned_finish=date(2026, 3, 13),
        today=date(2026, 3, 23),
        status=ExecutionStatus.IN_PROGRESS,
    )
    assert accrued == 6


def test_completed_work_has_no_forecast_delay(cal):
    assert (
        delay_rules.forecast_delay_days(
            cal,
            planned_finish=date(2026, 3, 13),
            today=date(2026, 3, 23),
            status=ExecutionStatus.COMPLETED,
        )
        == 0
    )


def test_forecast_delay_before_the_baseline_is_zero(cal):
    assert (
        delay_rules.forecast_delay_days(
            cal,
            planned_finish=date(2026, 3, 30),
            today=date(2026, 3, 23),
            status=ExecutionStatus.IN_PROGRESS,
        )
        == 0
    )


def test_holiday_inside_the_slip_is_not_counted(cal):
    # 2026-08-15 is a Saturday and a national holiday.
    planned, actual = date(2026, 8, 12), date(2026, 8, 19)
    assert (actual - planned).days == 7
    assert delay_rules.compute_delay_days(cal, planned, actual) == 5
