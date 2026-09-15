"""Programme and circle rollups.

The legacy programme report averaged each project's health unweighted, so a
2-node project counted the same as a 57-node one, and it counted *projects* per
circle while labelling the result "nodes" (audit H9, H10). It also raised
NameError for any programme with no projects (C12).

Everything here aggregates over nodes, which is the unit the business actually
counts, and every function tolerates an empty set.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.delay import AT_RISK_THRESHOLD_DAYS
from app.domain.stages import NodeStage, StageModel, health_status, resolve_many, summarise
from app.models import Programme, Project, Scope, Task, TaskExecution


@dataclass(slots=True)
class CircleRollup:
    circle: str
    nodes: int
    live_nodes: int
    health: float
    progress: float
    at_risk_nodes: int
    total_delay_days: int
    status: str


@dataclass(slots=True)
class ProjectRollup:
    project_id: int
    project_name: str
    nodes: int
    health: float
    progress: float
    live_nodes: int
    at_risk_nodes: int
    total_delay_days: int
    status: str
    dominant_stage: str


@dataclass(slots=True)
class ProgrammeRollup:
    programme_id: int
    programme_name: str
    total_projects: int
    total_nodes: int
    health: float
    progress: float
    live_nodes: int
    at_risk_nodes: int
    total_delay_days: int
    status: str
    dominant_stage: str
    projects: list[ProjectRollup] = field(default_factory=list)
    circles: list[CircleRollup] = field(default_factory=list)
    stage_mix: dict[str, int] = field(default_factory=dict)
    narrative: str = ""


def _stage_rows(db: Session, project_ids: list[int]):
    if not project_ids:
        return []
    return db.execute(
        select(Scope.id, Task.template_task_number, TaskExecution.status)
        .join(TaskExecution, TaskExecution.scope_id == Scope.id)
        .join(Task, Task.id == TaskExecution.task_id)
        .where(Scope.project_id.in_(project_ids))
    ).all()


def _delay_by_scope(db: Session, project_ids: list[int]) -> dict[int, tuple[int, int]]:
    """scope_id -> (total delay days, worst delay days)."""
    if not project_ids:
        return {}
    rows = db.execute(
        select(
            TaskExecution.scope_id,
            func.coalesce(func.sum(TaskExecution.delay_days), 0),
            func.coalesce(func.max(TaskExecution.delay_days), 0),
        )
        .where(TaskExecution.project_id.in_(project_ids))
        .group_by(TaskExecution.scope_id)
    ).all()
    return {scope_id: (int(total), int(worst)) for scope_id, total, worst in rows}


def _summarise_group(
    node_stages: list[NodeStage], delays: dict[int, tuple[int, int]]
) -> tuple[float, float, int, int, int, str]:
    summary = summarise(node_stages)
    at_risk = sum(
        1
        for n in node_stages
        if delays.get(n.scope_id, (0, 0))[1] > AT_RISK_THRESHOLD_DAYS
    )
    total_delay = sum(delays.get(n.scope_id, (0, 0))[0] for n in node_stages)
    dominant = (
        Counter(n.stage_name for n in node_stages).most_common(1)[0][0]
        if node_stages
        else "-"
    )
    return (
        summary.health,
        summary.progress,
        summary.live_nodes,
        at_risk,
        total_delay,
        dominant,
    )


def circle_rollups(
    db: Session, project_id: int, model: StageModel | None = None
) -> list[CircleRollup]:
    """Per-circle aggregate for one project, weakest first."""
    model = model or StageModel.from_session(db)
    node_stages = resolve_many(model, _stage_rows(db, [project_id]))
    delays = _delay_by_scope(db, [project_id])

    circle_of = {
        sid: circle
        for sid, circle in db.execute(
            select(Scope.id, Scope.circle).where(Scope.project_id == project_id)
        ).all()
    }

    grouped: dict[str, list[NodeStage]] = defaultdict(list)
    for scope_id, stage in node_stages.items():
        grouped[circle_of.get(scope_id, "Unknown")].append(stage)

    out = []
    for circle, stages in grouped.items():
        health, progress, live, at_risk, delay, _ = _summarise_group(stages, delays)
        out.append(
            CircleRollup(
                circle=circle,
                nodes=len(stages),
                live_nodes=live,
                health=health,
                progress=progress,
                at_risk_nodes=at_risk,
                total_delay_days=delay,
                status=health_status(health),
            )
        )
    return sorted(out, key=lambda c: (c.health, -c.total_delay_days))


def programme_rollup(db: Session, programme_id: int) -> ProgrammeRollup | None:
    """Aggregate a programme over its nodes, not over its projects."""
    programme = db.get(Programme, programme_id)
    if programme is None:
        return None

    projects = db.scalars(
        select(Project).where(Project.programme_id == programme_id).order_by(Project.name)
    ).all()
    project_ids = [p.id for p in projects]

    model = StageModel.from_session(db)
    node_stages = resolve_many(model, _stage_rows(db, project_ids))
    delays = _delay_by_scope(db, project_ids)

    project_of = {
        sid: pid
        for sid, pid in db.execute(
            select(Scope.id, Scope.project_id).where(Scope.project_id.in_(project_ids))
        ).all()
    } if project_ids else {}
    circle_of = {
        sid: circle
        for sid, circle in db.execute(
            select(Scope.id, Scope.circle).where(Scope.project_id.in_(project_ids))
        ).all()
    } if project_ids else {}

    # --- per project -------------------------------------------------------
    by_project: dict[int, list[NodeStage]] = defaultdict(list)
    for scope_id, stage in node_stages.items():
        by_project[project_of[scope_id]].append(stage)

    project_rollups = []
    for project in projects:
        stages = by_project.get(project.id, [])
        health, progress, live, at_risk, delay, dominant = _summarise_group(stages, delays)
        project_rollups.append(
            ProjectRollup(
                project_id=project.id,
                project_name=project.name,
                nodes=len(stages),
                health=health,
                progress=progress,
                live_nodes=live,
                at_risk_nodes=at_risk,
                total_delay_days=delay,
                status=health_status(health),
                dominant_stage=dominant,
            )
        )

    # --- per circle, counting nodes ---------------------------------------
    by_circle: dict[str, list[NodeStage]] = defaultdict(list)
    for scope_id, stage in node_stages.items():
        by_circle[circle_of.get(scope_id, "Unknown")].append(stage)

    circles = []
    for circle, stages in by_circle.items():
        health, progress, live, at_risk, delay, _ = _summarise_group(stages, delays)
        circles.append(
            CircleRollup(
                circle=circle,
                nodes=len(stages),
                live_nodes=live,
                health=health,
                progress=progress,
                at_risk_nodes=at_risk,
                total_delay_days=delay,
                status=health_status(health),
            )
        )
    circles.sort(key=lambda c: (c.health, -c.total_delay_days))

    # --- programme totals, over every node --------------------------------
    all_stages = list(node_stages.values())
    health, progress, live, at_risk, delay, dominant = _summarise_group(all_stages, delays)
    stage_mix = dict(Counter(n.stage_name for n in all_stages))

    if not projects:
        narrative = f"Programme '{programme.name}' has no projects yet."
    else:
        worst = min(project_rollups, key=lambda p: p.health) if project_rollups else None
        parts = [
            f"'{programme.name}' spans {len(projects)} project"
            f"{'' if len(projects) == 1 else 's'} and {len(all_stages)} node"
            f"{'' if len(all_stages) == 1 else 's'}.",
            f"Programme health is {health} and {live} node"
            f"{'' if live == 1 else 's'} are live ({progress}%).",
        ]
        if all_stages:
            parts.append(f"Most nodes are at '{dominant}'.")
        if at_risk:
            parts.append(
                f"{at_risk} node{'' if at_risk == 1 else 's'} are more than "
                f"{AT_RISK_THRESHOLD_DAYS} working days late, "
                f"{delay} working days of slip in total."
            )
        else:
            parts.append("No node is more than a week late.")
        if worst and len(project_rollups) > 1:
            parts.append(
                f"'{worst.project_name}' is the weakest project at {worst.health}."
            )
        narrative = " ".join(parts)

    return ProgrammeRollup(
        programme_id=programme.id,
        programme_name=programme.name,
        total_projects=len(projects),
        total_nodes=len(all_stages),
        health=health,
        progress=progress,
        live_nodes=live,
        at_risk_nodes=at_risk,
        total_delay_days=delay,
        status=health_status(health),
        dominant_stage=dominant,
        projects=project_rollups,
        circles=circles,
        stage_mix=stage_mix,
        narrative=narrative,
    )


def project_narrative(
    db: Session, project_id: int, model: StageModel | None = None
) -> str:
    """One honest paragraph.

    The legacy narrative printed a *task* name under the label "Most delayed
    stage", and computed a `most_delayed_stage` that was really the least mature
    stage present and was never used (audit H11).
    """
    project = db.get(Project, project_id)
    if project is None:
        return ""
    model = model or StageModel.from_session(db)
    node_stages = list(resolve_many(model, _stage_rows(db, [project_id])).values())
    delays = _delay_by_scope(db, [project_id])
    health, progress, live, at_risk, delay, dominant = _summarise_group(node_stages, delays)

    if not node_stages:
        return f"'{project.name}' has no scope loaded yet."

    worst_task = db.execute(
        select(Task.name, func.count(TaskExecution.id))
        .join(TaskExecution, TaskExecution.task_id == Task.id)
        .where(
            TaskExecution.project_id == project_id,
            TaskExecution.delay_days > AT_RISK_THRESHOLD_DAYS,
        )
        .group_by(Task.name)
        .order_by(func.count(TaskExecution.id).desc())
        .limit(1)
    ).first()

    facilities = db.scalar(
        select(func.count(func.distinct(Scope.facility_name))).where(
            Scope.project_id == project_id
        )
    ) or 0
    parts = [
        f"'{project.name}' covers {len(node_stages)} node"
        f"{'' if len(node_stages) == 1 else 's'} across "
        f"{facilities} facilit{'y' if facilities == 1 else 'ies'}.",
        f"Health is {health}; {live} node{'' if live == 1 else 's'} live ({progress}%).",
        f"Most nodes sit at '{dominant}'.",
    ]
    if at_risk:
        parts.append(
            f"{at_risk} node{'' if at_risk == 1 else 's'} carry an activity more than "
            f"{AT_RISK_THRESHOLD_DAYS} working days late, {delay} working days in total."
        )
        if worst_task:
            parts.append(
                f"The activity most often late is '{worst_task[0]}' "
                f"({worst_task[1]} occurrences)."
            )
    else:
        parts.append("No activity is more than a week late.")
    return " ".join(parts)
