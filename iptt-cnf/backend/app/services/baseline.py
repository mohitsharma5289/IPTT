"""Day-0 planning and re-baselining.

Decision 6: PM actuals are preserved when a project is re-baselined.

The legacy flow (`persist_project_plan`) opened its own session and committed
four separate times: clear the plan, write the new plan, **delete every
TaskExecution row for the project**, then recreate them empty. No enclosing
transaction. A failure between steps three and four destroyed every actual date,
delay reason and status in the project with no rollback and no backup (audit C4).

Here, re-baselining:
  1. archives the current execution state together with the baseline it was
     measured against, into `execution_archive`;
  2. recomputes the plan and writes it to `task.planned_start` / `planned_finish`;
  3. **keeps** the live actuals in place and recomputes `delay_days` against the
     new baseline.

All of it in one transaction. Nothing a PM entered is ever discarded - the
archive holds the history and the live rows keep the work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import delay as delay_rules
from app.domain.calendar import WorkingCalendar
from app.domain.planner import (
    ConstraintRule,
    PlannedTask,
    ScopeInput,
    TaskInput,
    generate_plan,
)
from app.models import (
    ExecutionArchive,
    Project,
    Scope,
    SchedulingConstraint,
    Task,
    TaskExecution,
)
from app.models.enums import ExecutionStatus


@dataclass(frozen=True, slots=True)
class BaselineResult:
    project_id: int
    baseline_version: int
    tasks_planned: int
    executions_created: int
    executions_preserved: int
    rows_archived: int
    plan_start: date | None
    plan_finish: date | None


def _load_planner_inputs(db: Session, project_id: int):
    scopes = [
        ScopeInput(
            scope_id=s.id,
            node_id=s.node_id,
            circle=s.circle,
            facility_name=s.facility_name,
            priority=s.priority,
        )
        for s in db.scalars(select(Scope).where(Scope.project_id == project_id)).all()
    ]
    tasks = [
        TaskInput(
            task_id=t.id,
            scope_id=t.scope_id,
            template_task_number=t.template_task_number,
            name=t.name,
            duration_days=t.duration_days,
            predecessor_template_number=t.predecessor_template_number,
            is_prerequisite=t.is_prerequisite,
        )
        for t in db.scalars(select(Task).where(Task.project_id == project_id)).all()
    ]
    constraints = [
        ConstraintRule(
            template_task_number=c.template_task_number,
            rule_type=c.rule_type,
            partition_by=c.partition_by,
            max_count=c.max_count,
            count_distinct_by=c.count_distinct_by,
            window_working_days=c.window_working_days,
            pool_key=c.pool_key,
        )
        for c in db.scalars(
            select(SchedulingConstraint).where(SchedulingConstraint.is_active.is_(True))
        ).all()
    ]
    return scopes, tasks, constraints


def archive_current_state(
    db: Session, project_id: int, *, actor: str | None, reason: str
) -> int:
    """Snapshot every execution row with the baseline it was measured against."""
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    rows = db.execute(
        select(TaskExecution, Task, Scope)
        .join(Task, TaskExecution.task_id == Task.id)
        .join(Scope, TaskExecution.scope_id == Scope.id)
        .where(TaskExecution.project_id == project_id)
    ).all()

    archived = 0
    for execution, task, scope in rows:
        # Nothing recorded yet - no history worth keeping.
        if (
            execution.actual_start is None
            and execution.actual_finish is None
            and execution.status == ExecutionStatus.NOT_STARTED
            and not execution.delay_reason
        ):
            continue
        db.add(
            ExecutionArchive(
                project_id=project_id,
                scope_id=scope.id,
                task_id=task.id,
                node_id=scope.node_id,
                circle=scope.circle,
                facility_name=scope.facility_name,
                template_task_number=task.template_task_number,
                task_name=task.name,
                baseline_version=project.baseline_version,
                baseline_planned_start=task.planned_start,
                baseline_planned_finish=task.planned_finish,
                actual_start=execution.actual_start,
                actual_finish=execution.actual_finish,
                status=execution.status,
                delay_reason=execution.delay_reason,
                delay_days=execution.delay_days,
                archived_by=actor,
                reason=reason,
            )
        )
        archived += 1
    return archived


def run_baseline(
    db: Session,
    project_id: int,
    *,
    kickoff_date: date | None = None,
    actor: str | None = None,
    reason: str = "baseline",
    is_rebaseline: bool = False,
) -> BaselineResult:
    """Generate and persist the Day-0 plan. One transaction, caller commits."""
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    if kickoff_date is not None:
        project.project_start_date = kickoff_date
    if project.project_start_date is None:
        raise ValueError("Project start date is not set")

    if project.baseline_locked and not is_rebaseline:
        raise ValueError(
            "Baseline is locked because execution has started. Use re-baseline instead."
        )

    rows_archived = 0
    if is_rebaseline:
        rows_archived = archive_current_state(db, project_id, actor=actor, reason=reason)
        project.baseline_version += 1

    scopes, tasks, constraints = _load_planner_inputs(db, project_id)
    if not scopes:
        raise ValueError("Project has no scope defined")
    if not tasks:
        raise ValueError("Project has no tasks defined")

    circles = sorted({s.circle for s in scopes})
    calendar = WorkingCalendar.from_session(db, circles)

    plan: list[PlannedTask] = generate_plan(
        kickoff_date=project.project_start_date,
        scopes=scopes,
        tasks=tasks,
        constraints=constraints,
        calendar=calendar,
    )

    task_rows = {
        t.id: t for t in db.scalars(select(Task).where(Task.project_id == project_id)).all()
    }
    for planned in plan:
        task = task_rows.get(planned.task_id)
        if task is not None:
            task.planned_start = planned.planned_start
            task.planned_finish = planned.planned_finish

    db.flush()

    created, preserved = _sync_execution_rows(db, project_id, calendar)

    starts = [p.planned_start for p in plan]
    finishes = [p.planned_finish for p in plan]
    return BaselineResult(
        project_id=project_id,
        baseline_version=project.baseline_version,
        tasks_planned=len(plan),
        executions_created=created,
        executions_preserved=preserved,
        rows_archived=rows_archived,
        plan_start=min(starts) if starts else None,
        plan_finish=max(finishes) if finishes else None,
    )


def _sync_execution_rows(
    db: Session, project_id: int, calendar: WorkingCalendar
) -> tuple[int, int]:
    """Ensure one execution row per task, preserving anything already recorded."""
    tasks = db.execute(
        select(Task, Scope).join(Scope, Task.scope_id == Scope.id).where(
            Task.project_id == project_id
        )
    ).all()
    existing = {
        e.task_id: e
        for e in db.scalars(
            select(TaskExecution).where(TaskExecution.project_id == project_id)
        ).all()
    }

    created = 0
    preserved = 0
    for task, scope in tasks:
        execution = existing.get(task.id)
        if execution is None:
            db.add(
                TaskExecution(
                    project_id=project_id,
                    scope_id=task.scope_id,
                    task_id=task.id,
                    status=ExecutionStatus.NOT_STARTED,
                    delay_days=0,
                )
            )
            created += 1
            continue

        preserved += 1
        # Re-measure the surviving actuals against the new baseline.
        execution.delay_days = delay_rules.compute_delay_days(
            calendar, task.planned_finish, execution.actual_finish, scope.circle
        )

    # Any execution row whose task no longer exists is removed by the FK cascade.
    return created, preserved


def recompute_project_delays(db: Session, project_id: int) -> int:
    """Recompute `delay_days` for a whole project. Idempotent."""
    circles = [
        c for (c,) in db.execute(
            select(Scope.circle).where(Scope.project_id == project_id).distinct()
        ).all()
    ]
    calendar = WorkingCalendar.from_session(db, circles)
    rows = db.execute(
        select(TaskExecution, Task, Scope)
        .join(Task, TaskExecution.task_id == Task.id)
        .join(Scope, TaskExecution.scope_id == Scope.id)
        .where(TaskExecution.project_id == project_id)
    ).all()
    for execution, task, scope in rows:
        execution.delay_days = delay_rules.compute_delay_days(
            calendar, task.planned_finish, execution.actual_finish, scope.circle
        )
    return len(rows)
