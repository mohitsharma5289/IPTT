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


# ---------------------------------------------------------------------------
# Portfolio-wide aggregates
# ---------------------------------------------------------------------------
#
# These answer questions the per-project endpoints cannot: "how are my
# programmes doing" and "how is each circle doing across everything we are
# building". Neither can be derived client-side by summing project responses,
# because health is weighted by node and a circle spans projects.


@dataclass(slots=True)
class GovernanceRow:
    """One programme, as the governance dashboard shows it."""

    programme_id: int
    programme_name: str
    status: str
    total_projects: int
    planned_projects: int      # projects that have a generated plan
    planning_completion: float  # planned / total, as a percentage
    total_nodes: int
    health: float
    progress: float
    at_risk_nodes: int
    total_delay_days: int
    health_status: str


@dataclass(slots=True)
class CircleIntelligenceRow:
    """One circle, across every project in the portfolio."""

    circle: str
    facilities: int
    programmes: int
    projects: int
    total_nodes: int
    completed_nodes: int
    wip_nodes: int
    not_started_nodes: int
    progress: float
    health: float
    at_risk_nodes: int
    total_delay_days: int
    health_status: str


def _visible_project_ids(db: Session, project_ids: list[int] | None) -> list[int]:
    """Restrict to what the caller may see; None means the whole portfolio."""
    if project_ids is not None:
        return project_ids
    return list(db.scalars(select(Project.id)).all())


def governance_rows(
    db: Session, project_ids: list[int] | None = None
) -> list[GovernanceRow]:
    """Programme-level governance, weakest first.

    "Planned" means the project has generated planned dates - not that
    `baseline_version` is above zero, which is true of every project from the
    moment it is created and says nothing about whether planning has happened.
    """
    visible = _visible_project_ids(db, project_ids)
    model = StageModel.from_session(db)

    rows = db.execute(
        select(
            Programme.id,
            Programme.name,
            Programme.status,
            Project.id,
            func.count(func.distinct(Task.id)).filter(
                Task.planned_finish.is_not(None)
            ),
        )
        .join(Project, Project.programme_id == Programme.id, isouter=True)
        .join(Task, Task.project_id == Project.id, isouter=True)
        .where(Project.id.in_(visible) if visible else Project.id.is_(None))
        .group_by(Programme.id, Programme.name, Programme.status, Project.id)
    ).all()

    # Programmes with no projects at all still belong on the dashboard.
    all_programmes = {
        pid: (name, status)
        for pid, name, status in db.execute(
            select(Programme.id, Programme.name, Programme.status)
        ).all()
    }

    per_programme: dict[int, list[tuple[int, int]]] = {pid: [] for pid in all_programmes}
    for programme_id, _name, _status, project_id, planned_tasks in rows:
        if project_id is not None:
            per_programme.setdefault(programme_id, []).append(
                (project_id, planned_tasks or 0)
            )

    node_stages = resolve_many(model, _stage_rows(db, visible))
    delays = _delay_by_scope(db, visible)
    scope_to_project = dict(
        db.execute(
            select(Scope.id, Scope.project_id).where(Scope.project_id.in_(visible))
        ).all()
        if visible
        else []
    )

    out: list[GovernanceRow] = []
    for programme_id, (name, status) in all_programmes.items():
        projects = per_programme.get(programme_id, [])
        project_id_set = {pid for pid, _ in projects}
        planned = sum(1 for _pid, planned_tasks in projects if planned_tasks > 0)

        members = [
            stage
            for scope_id, stage in node_stages.items()
            if scope_to_project.get(scope_id) in project_id_set
        ]
        health, progress, _live, at_risk, delay_days, _dominant = _summarise_group(
            members, delays
        )
        out.append(
            GovernanceRow(
                programme_id=programme_id,
                programme_name=name,
                status=status,
                total_projects=len(projects),
                planned_projects=planned,
                planning_completion=(
                    round(planned / len(projects) * 100, 1) if projects else 0.0
                ),
                total_nodes=len(members),
                health=health,
                progress=progress,
                at_risk_nodes=at_risk,
                total_delay_days=delay_days,
                health_status=health_status(health),
            )
        )

    out.sort(key=lambda r: (r.health, -r.total_delay_days))
    return out


def circle_intelligence(
    db: Session, project_ids: list[int] | None = None
) -> list[CircleIntelligenceRow]:
    """Every circle across the portfolio, weakest first.

    A circle spans projects and programmes, which is exactly why this cannot be
    assembled from the per-project circle roll-up: the same circle appears in
    several of them and the node counts have to be unioned, not added.
    """
    visible = _visible_project_ids(db, project_ids)
    if not visible:
        return []

    model = StageModel.from_session(db)
    node_stages = resolve_many(model, _stage_rows(db, visible))
    delays = _delay_by_scope(db, visible)

    rows = db.execute(
        select(
            Scope.id,
            Scope.circle,
            Scope.facility_name,
            Project.id,
            Project.programme_id,
        )
        .join(Project, Project.id == Scope.project_id)
        .where(Scope.project_id.in_(visible))
    ).all()

    grouped: dict[str, dict] = {}
    for scope_id, circle, facility, project_id, programme_id in rows:
        bucket = grouped.setdefault(
            circle,
            {
                "scope_ids": [],
                "facilities": set(),
                "projects": set(),
                "programmes": set(),
            },
        )
        bucket["scope_ids"].append(scope_id)
        bucket["facilities"].add(facility)
        bucket["projects"].add(project_id)
        bucket["programmes"].add(programme_id)

    out: list[CircleIntelligenceRow] = []
    for circle, bucket in grouped.items():
        members = [
            node_stages[sid] for sid in bucket["scope_ids"] if sid in node_stages
        ]
        health, progress, live, at_risk, delay_days, _dominant = _summarise_group(
            members, delays
        )
        # "Complete" means the node is live - it has reached the terminal stage,
        # which is what weight 100 encodes. "WIP" is started but not there yet.
        completed = sum(1 for n in members if n.is_live)
        started = sum(1 for n in members if n.completed_tasks > 0)
        out.append(
            CircleIntelligenceRow(
                circle=circle,
                facilities=len(bucket["facilities"]),
                programmes=len(bucket["programmes"]),
                projects=len(bucket["projects"]),
                total_nodes=len(bucket["scope_ids"]),
                completed_nodes=completed,
                wip_nodes=max(0, started - completed),
                not_started_nodes=len(bucket["scope_ids"]) - started,
                progress=progress,
                health=health,
                at_risk_nodes=at_risk,
                total_delay_days=delay_days,
                health_status=health_status(health),
            )
        )

    out.sort(key=lambda r: (r.health, -r.total_delay_days))
    return out
