"""Planner behaviour, covering the audit's C7, C8, M14 and M15 and decision 8."""
from __future__ import annotations

from datetime import date

import pytest

from app.domain.calendar import WorkingCalendar
from app.domain.planner import (
    ConstraintRule,
    ScopeInput,
    TaskInput,
    critical_path_length,
    generate_plan,
)

KICKOFF = date(2026, 3, 16)  # a Monday


@pytest.fixture
def cal() -> WorkingCalendar:
    return WorkingCalendar()


def scope(n: int, circle="TN", facility=None) -> ScopeInput:
    return ScopeInput(
        scope_id=n,
        node_id=f"NODE{n:02d}",
        circle=circle,
        facility_name=facility or f"FAC{n:02d}",
    )


def task(scope_id, number, duration, pred=None, task_id=None, prereq=False) -> TaskInput:
    return TaskInput(
        task_id=task_id if task_id is not None else scope_id * 100 + number,
        scope_id=scope_id,
        template_task_number=number,
        name=f"T{number}",
        duration_days=duration,
        predecessor_template_number=pred,
        is_prerequisite=prereq,
    )


def by_number(plan):
    return {p.template_task_number: p for p in plan}


# --- C7: start dates were pushed out by the task's own duration --------------


def test_first_task_starts_on_the_kickoff_date(cal):
    plan = generate_plan(
        kickoff_date=KICKOFF,
        scopes=[scope(1)],
        tasks=[task(1, 1, 3)],
        constraints=[],
        calendar=cal,
    )
    p = by_number(plan)[1]
    assert p.planned_start == KICKOFF
    # A three-day activity starting Monday finishes Wednesday, not Friday.
    assert p.planned_finish == date(2026, 3, 18)


def test_successor_starts_the_working_day_after_its_predecessor_finishes(cal):
    """Two legacy defects in one line: `get_edp` returned an end date that was
    used as a start, and a successor began *on* its predecessor's finish date."""
    plan = generate_plan(
        kickoff_date=KICKOFF,
        scopes=[scope(1)],
        tasks=[task(1, 1, 3), task(1, 2, 2, pred=1)],
        constraints=[],
        calendar=cal,
    )
    p = by_number(plan)
    assert p[1].planned_finish == date(2026, 3, 18)
    assert p[2].planned_start == date(2026, 3, 19)
    assert p[2].planned_finish == date(2026, 3, 20)


def test_chain_length_matches_the_critical_path(cal):
    tasks = [task(1, 1, 2), task(1, 2, 3, pred=1), task(1, 3, 1, pred=2)]
    plan = generate_plan(
        kickoff_date=KICKOFF, scopes=[scope(1)], tasks=tasks, constraints=[], calendar=cal
    )
    p = by_number(plan)
    # 2 + 3 + 1 = 6 working days from Monday 16 Mar -> Monday 23 Mar
    assert p[1].planned_start == date(2026, 3, 16)
    assert p[3].planned_finish == date(2026, 3, 23)
    assert critical_path_length(tasks) == 6


def test_spans_skip_weekends(cal):
    plan = generate_plan(
        kickoff_date=date(2026, 3, 19),  # Thursday
        scopes=[scope(1)],
        tasks=[task(1, 1, 3)],
        constraints=[],
        calendar=cal,
    )
    # Thu, Fri, Mon
    assert by_number(plan)[1].planned_finish == date(2026, 3, 23)


def test_kickoff_on_a_weekend_snaps_forward(cal):
    plan = generate_plan(
        kickoff_date=date(2026, 3, 15),  # Sunday - project 12's real start date
        scopes=[scope(1)],
        tasks=[task(1, 1, 1)],
        constraints=[],
        calendar=cal,
    )
    assert by_number(plan)[1].planned_start == date(2026, 3, 16)


def test_prerequisite_gates_sit_at_kickoff_and_consume_nothing(cal):
    plan = generate_plan(
        kickoff_date=KICKOFF,
        scopes=[scope(1)],
        tasks=[task(1, 1, 0, prereq=True), task(1, 2, 2, pred=1)],
        constraints=[],
        calendar=cal,
    )
    p = by_number(plan)
    assert p[1].planned_start == p[1].planned_finish == KICKOFF
    assert p[2].planned_start == KICKOFF


# --- M15: caps counted starts, not occupancy --------------------------------


def test_concurrent_cap_counts_occupancy_across_the_whole_span(cal):
    """A five-day activity used to occupy one day in the legacy ledger, so
    "at most one concurrent" really meant "at most one start per day"."""
    rule = ConstraintRule(
        template_task_number=1, rule_type="concurrent", partition_by="global", max_count=1
    )
    plan = generate_plan(
        kickoff_date=KICKOFF,
        scopes=[scope(1), scope(2)],
        tasks=[task(1, 1, 5), task(2, 1, 5)],
        constraints=[rule],
        calendar=cal,
    )
    first, second = sorted(plan, key=lambda p: p.planned_start)
    assert first.planned_finish < second.planned_start


def test_per_circle_cap_allows_parallel_work_in_different_circles(cal):
    rule = ConstraintRule(
        template_task_number=1, rule_type="concurrent", partition_by="circle", max_count=1
    )
    plan = generate_plan(
        kickoff_date=KICKOFF,
        scopes=[scope(1, circle="TN"), scope(2, circle="KL")],
        tasks=[task(1, 1, 4), task(2, 1, 4)],
        constraints=[rule],
        calendar=cal,
    )
    assert {p.planned_start for p in plan} == {KICKOFF}


def test_tasks_sharing_a_pool_compete_with_each_other(cal):
    """Decision 8: tasks 32 and 33 are the same on-site crew."""
    rules = [
        ConstraintRule(32, "concurrent", "global", 1, pool_key="crew"),
        ConstraintRule(33, "concurrent", "global", 1, pool_key="crew"),
    ]
    plan = generate_plan(
        kickoff_date=KICKOFF,
        scopes=[scope(1), scope(2)],
        tasks=[task(1, 32, 3), task(2, 33, 3)],
        constraints=rules,
        calendar=cal,
    )
    a, b = sorted(plan, key=lambda p: p.planned_start)
    assert a.planned_finish < b.planned_start


def test_starts_per_day_cap_defers_the_overflow(cal):
    rule = ConstraintRule(13, "starts_per_day", "global", max_count=2)
    plan = generate_plan(
        kickoff_date=KICKOFF,
        scopes=[scope(1), scope(2), scope(3)],
        tasks=[task(1, 13, 1), task(2, 13, 1), task(3, 13, 1)],
        constraints=[rule],
        calendar=cal,
    )
    starts = sorted(p.planned_start for p in plan)
    assert starts[:2] == [KICKOFF, KICKOFF]
    assert starts[2] == date(2026, 3, 17)


# --- C8: the rolling window ------------------------------------------------


def test_rolling_window_limits_distinct_facilities_across_two_days(cal):
    """Task 25: at most two distinct facilities inside any rolling two-working-day
    window. The legacy `get_last_n_working_days` returned the same date twice, so
    this reduced to a same-day check and was never really enforced."""
    rule = ConstraintRule(
        25,
        "distinct_in_window",
        "global",
        max_count=2,
        count_distinct_by="facility",
        window_working_days=2,
    )
    scopes = [
        scope(1, facility="FAC_A"),
        scope(2, facility="FAC_B"),
        scope(3, facility="FAC_C"),
    ]
    tasks = [task(1, 25, 1), task(2, 25, 1), task(3, 25, 1)]
    plan = generate_plan(
        kickoff_date=KICKOFF, scopes=scopes, tasks=tasks, constraints=[rule], calendar=cal
    )
    starts = sorted(p.planned_start for p in plan)
    # The third facility cannot join until the window has rolled past the first.
    assert starts[2] > starts[0]
    assert (starts[2] - starts[0]).days >= 1


def test_same_facility_inside_the_window_costs_nothing_extra(cal):
    rule = ConstraintRule(
        25, "distinct_in_window", "global", 2, count_distinct_by="facility", window_working_days=2
    )
    scopes = [scope(1, facility="SAME"), scope(2, facility="SAME")]
    plan = generate_plan(
        kickoff_date=KICKOFF,
        scopes=scopes,
        tasks=[task(1, 25, 1), task(2, 25, 1)],
        constraints=[rule],
        calendar=cal,
    )
    assert {p.planned_start for p in plan} == {KICKOFF}


# --- M14: determinism -------------------------------------------------------


def test_plan_is_independent_of_input_ordering(cal):
    """`build_scope_df` issued no ORDER BY, so the greedy planner could produce a
    different schedule on PostgreSQL than on SQLite."""
    rule = ConstraintRule(1, "concurrent", "global", max_count=1)
    scopes = [scope(1), scope(2), scope(3)]
    tasks = [task(1, 1, 2), task(2, 1, 2), task(3, 1, 2)]

    forward = generate_plan(
        kickoff_date=KICKOFF, scopes=scopes, tasks=tasks, constraints=[rule], calendar=cal
    )
    reverse = generate_plan(
        kickoff_date=KICKOFF,
        scopes=list(reversed(scopes)),
        tasks=list(reversed(tasks)),
        constraints=[rule],
        calendar=cal,
    )
    assert {(p.task_id, p.planned_start) for p in forward} == {
        (p.task_id, p.planned_start) for p in reverse
    }


def test_scope_priority_does_not_influence_the_plan(cal):
    """Decision 7: priority is carried for display only."""
    rule = ConstraintRule(1, "concurrent", "global", max_count=1)
    a = [
        ScopeInput(1, "NODE01", "TN", "F1", priority=1),
        ScopeInput(2, "NODE02", "TN", "F2", priority=9),
    ]
    b = [
        ScopeInput(1, "NODE01", "TN", "F1", priority=9),
        ScopeInput(2, "NODE02", "TN", "F2", priority=1),
    ]
    tasks = [task(1, 1, 2), task(2, 1, 2)]
    pa = generate_plan(kickoff_date=KICKOFF, scopes=a, tasks=tasks, constraints=[rule], calendar=cal)
    pb = generate_plan(kickoff_date=KICKOFF, scopes=b, tasks=tasks, constraints=[rule], calendar=cal)
    assert {(p.task_id, p.planned_start) for p in pa} == {
        (p.task_id, p.planned_start) for p in pb
    }


def test_cyclic_dependency_is_rejected():
    tasks = [task(1, 1, 2, pred=2), task(1, 2, 2, pred=1)]
    with pytest.raises(ValueError, match="Cyclic"):
        critical_path_length(tasks)
