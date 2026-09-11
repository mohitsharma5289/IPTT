"""Day-0 plan generation.

One scheduler, not two. The legacy codebase had `runbook_scheduler/planner.py`
(which produced the live data) and `services/schedule_service.py` (a CPM pass
that crashed on every real project and, had it run, would have overwritten every
baseline from a hardcoded 2026-05-01). Both wrote `task.planned_start` and
`task.planned_finish` (audit C1, C2). This module replaces both.

Three behavioural fixes carried over from the audit:

C7 - `get_edp()` returned `add_working_days(base, duration - 1)`, an *end* date,
and the caller assigned it to `candidate_start`, then added `duration - 1` again
to get the finish. Every activity's start was pushed out by its own duration and
its finish by twice it. Separately, a successor started *on* the day its
predecessor finished rather than the next working day. Both are corrected here.

C8 - the rolling-window check is real now; see `domain.calendar`.

M15 - capacity was charged only against the start date, so a five-day activity
occupied a single day in the ledger and "at most three concurrent" actually meant
"at most three starts per day". Occupancy is now charged across the full span.

M14 - `build_scope_df()` issued no ORDER BY and the planner consumed capacity
greedily, so the same inputs could produce different plans on PostgreSQL. Nodes
are now processed in a deterministic, explicitly documented order.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from app.domain.calendar import WorkingCalendar
from app.models.enums import ConstraintGroup

MAX_DEFERRAL_DAYS = 2000  # guards against an unsatisfiable constraint looping forever


# --- inputs -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopeInput:
    scope_id: int
    node_id: str
    circle: str
    facility_name: str
    priority: int = 1  # decision 7: carried for display, never used for ordering


@dataclass(frozen=True, slots=True)
class TaskInput:
    task_id: int
    scope_id: int
    template_task_number: int
    name: str
    duration_days: int
    predecessor_template_number: int | None
    is_prerequisite: bool = False


@dataclass(frozen=True, slots=True)
class ConstraintRule:
    template_task_number: int
    rule_type: str  # starts_per_day | concurrent | distinct_in_window
    partition_by: str  # global | circle | facility
    max_count: int
    count_distinct_by: str | None = None
    window_working_days: int = 1
    pool_key: str | None = None

    @property
    def pool(self) -> str:
        return self.pool_key or f"task:{self.template_task_number}"


@dataclass(frozen=True, slots=True)
class PlannedTask:
    task_id: int
    scope_id: int
    template_task_number: int
    name: str
    planned_start: date
    planned_finish: date
    deferred_working_days: int


# --- capacity ledger --------------------------------------------------------


@dataclass
class _Ledger:
    """Tracks what each resource pool is doing on each working day."""

    starts: dict[tuple[str, str, date], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    occupancy: dict[tuple[str, str, date], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    distinct: dict[tuple[str, str, date], set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )

    @staticmethod
    def partition_key(rule: ConstraintRule, scope: ScopeInput) -> str:
        if rule.partition_by == ConstraintGroup.CIRCLE:
            return f"circle:{scope.circle}"
        if rule.partition_by == ConstraintGroup.FACILITY:
            return f"facility:{scope.facility_name}"
        return "global"

    @staticmethod
    def distinct_value(rule: ConstraintRule, scope: ScopeInput) -> str:
        if rule.count_distinct_by == "facility":
            return scope.facility_name
        if rule.count_distinct_by == "circle":
            return scope.circle
        return scope.node_id

    def permits(
        self,
        rule: ConstraintRule,
        scope: ScopeInput,
        span: list[date],
        calendar: WorkingCalendar,
    ) -> bool:
        pool = rule.pool
        part = self.partition_key(rule, scope)
        start = span[0]

        if rule.rule_type == "starts_per_day":
            return self.starts[(pool, part, start)] < rule.max_count

        if rule.rule_type == "concurrent":
            return all(self.occupancy[(pool, part, d)] < rule.max_count for d in span)

        if rule.rule_type == "distinct_in_window":
            value = self.distinct_value(rule, scope)
            window = calendar.last_n_working_days(start, rule.window_working_days)
            seen: set[str] = set()
            for d in window:
                seen |= self.distinct[(pool, part, d)]
            # Already inside the window? Then it costs nothing extra.
            return value in seen or len(seen) < rule.max_count

        return True

    def charge(
        self,
        rule: ConstraintRule,
        scope: ScopeInput,
        span: list[date],
        calendar: WorkingCalendar,
    ) -> None:
        pool = rule.pool
        part = self.partition_key(rule, scope)
        if rule.rule_type == "starts_per_day":
            self.starts[(pool, part, span[0])] += 1
        elif rule.rule_type == "concurrent":
            for d in span:
                self.occupancy[(pool, part, d)] += 1
        elif rule.rule_type == "distinct_in_window":
            value = self.distinct_value(rule, scope)
            for d in span:
                self.distinct[(pool, part, d)].add(value)


# --- planner ----------------------------------------------------------------


def _working_span(
    calendar: WorkingCalendar, start: date, duration_days: int, circle: str
) -> list[date]:
    """Every working day the activity occupies, inclusive of both ends."""
    if duration_days <= 1:
        return [start]
    span = [start]
    cur = start
    for _ in range(duration_days - 1):
        cur = calendar.next_working_day(cur, circle)
        span.append(cur)
    return span


def generate_plan(
    *,
    kickoff_date: date,
    scopes: list[ScopeInput],
    tasks: list[TaskInput],
    constraints: list[ConstraintRule],
    calendar: WorkingCalendar,
) -> list[PlannedTask]:
    """Produce Day-0 planned dates for every task.

    Node order is `(circle, node_id)` - stable, human-meaningful, and independent
    of database row order. Per decision 7, `Scope.priority` is deliberately not
    consulted: in the live dataset all 57 nodes of project 12 share priority 1,
    so it carries no ordering information anyway.
    """
    rules_by_task: dict[int, list[ConstraintRule]] = defaultdict(list)
    for rule in constraints:
        rules_by_task[rule.template_task_number].append(rule)

    tasks_by_scope: dict[int, list[TaskInput]] = defaultdict(list)
    for t in tasks:
        tasks_by_scope[t.scope_id].append(t)

    ledger = _Ledger()
    results: list[PlannedTask] = []

    ordered_scopes = sorted(scopes, key=lambda s: (s.circle, s.node_id, s.scope_id))

    for scope in ordered_scopes:
        scope_kickoff = calendar.snap_to_working_day(kickoff_date, scope.circle)
        # template number -> (finish date, was a zero-duration milestone)
        finish_by_template: dict[int, tuple[date, bool]] = {}

        for task in sorted(
            tasks_by_scope.get(scope.scope_id, []), key=lambda t: t.template_task_number
        ):
            duration = max(task.duration_days, 0)

            # Prerequisite gates are satisfied at kickoff and consume no capacity.
            if task.is_prerequisite or duration == 0:
                start = finish = scope_kickoff
                prior = finish_by_template.get(task.predecessor_template_number)
                if prior is not None:
                    start = finish = prior[0]
                finish_by_template[task.template_task_number] = (finish, True)
                results.append(
                    PlannedTask(
                        task_id=task.task_id,
                        scope_id=scope.scope_id,
                        template_task_number=task.template_task_number,
                        name=task.name,
                        planned_start=start,
                        planned_finish=finish,
                        deferred_working_days=0,
                    )
                )
                continue

            # --- earliest feasible start (the C7 fix) -----------------------
            prior = finish_by_template.get(task.predecessor_template_number)
            if prior is None:
                candidate = scope_kickoff
            else:
                predecessor_finish, predecessor_was_milestone = prior
                # A real activity hands over the working day *after* it ends. A
                # zero-duration gate consumes no time, so its successor may start
                # the same day.
                candidate = (
                    predecessor_finish
                    if predecessor_was_milestone
                    else calendar.next_working_day(predecessor_finish, scope.circle)
                )

            # --- defer until every rule is satisfied ------------------------
            rules = rules_by_task.get(task.template_task_number, ())
            deferrals = 0
            if rules:
                while deferrals < MAX_DEFERRAL_DAYS:
                    span = _working_span(calendar, candidate, duration, scope.circle)
                    if all(
                        ledger.permits(rule, scope, span, calendar) for rule in rules
                    ):
                        break
                    candidate = calendar.next_working_day(candidate, scope.circle)
                    deferrals += 1
                else:
                    raise RuntimeError(
                        f"Could not place task {task.template_task_number} for node "
                        f"{scope.node_id} within {MAX_DEFERRAL_DAYS} working days; "
                        "a scheduling constraint is unsatisfiable."
                    )

            span = _working_span(calendar, candidate, duration, scope.circle)
            for rule in rules:
                ledger.charge(rule, scope, span, calendar)

            planned_start = span[0]
            planned_finish = span[-1]
            finish_by_template[task.template_task_number] = (planned_finish, False)

            results.append(
                PlannedTask(
                    task_id=task.task_id,
                    scope_id=scope.scope_id,
                    template_task_number=task.template_task_number,
                    name=task.name,
                    planned_start=planned_start,
                    planned_finish=planned_finish,
                    deferred_working_days=deferrals,
                )
            )

    return results


def critical_path_length(tasks: list[TaskInput]) -> int:
    """Longest dependency chain in working days, ignoring resource contention.

    Useful as a floor for the plan and for deriving stage weights from the
    template rather than assigning them by hand.
    """
    by_number = {t.template_task_number: t for t in tasks}
    memo: dict[int, int] = {}

    def finish(n: int, seen: frozenset[int] = frozenset()) -> int:
        if n in memo:
            return memo[n]
        if n in seen:
            raise ValueError(f"Cyclic dependency at template task {n}")
        task = by_number[n]
        pred = task.predecessor_template_number
        base = finish(pred, seen | {n}) if pred in by_number else 0
        memo[n] = base + max(task.duration_days, 0)
        return memo[n]

    return max((finish(n) for n in by_number), default=0)
