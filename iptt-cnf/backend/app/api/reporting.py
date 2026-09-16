"""Reporting endpoints.

The whole point of this module is that every figure comes from **one** query per
dashboard. The legacy `get_project_executive_summary` resolved each node's stage
four separate times, and each resolution lazily loaded `scope.executions` and
then `e.task` per row - roughly 10,000 round trips for a 57-node project
(audit M1). That was tolerable against a local SQLite file and would be
unusable across a network to a PostgreSQL pod.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dataclasses import asdict

from app.api.common import pdf_response, safe_filename
from app.db import get_db
from app.domain.delay import AT_RISK_THRESHOLD_DAYS
from app.domain.stages import StageModel, governance_matrix, resolve_many, summarise
from app.models import (
    Programme,
    Project,
    ProjectAssignment,
    Scope,
    Task,
    TaskExecution,
)
from app.models.enums import Role
from app.security import CurrentUser, get_current_user
from app.services import forecast as forecast_service
from app.services import pdf as pdf_service
from app.services.rollup import (
    circle_intelligence,
    circle_rollups,
    governance_rows,
    programme_rollup,
    project_narrative,
)

router = APIRouter()


class KpiResponse(BaseModel):
    project_id: int
    project_name: str
    total_nodes: int
    health: float
    trend: str
    live_nodes: int
    progress: float
    at_risk_nodes: int
    total_delay_days: int
    stage_mix: dict[str, int]


def _load_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def _node_stage_rows(db: Session, project_id: int):
    """One query. Returns (scope_id, template_task_number, status)."""
    return db.execute(
        select(Scope.id, Task.template_task_number, TaskExecution.status)
        .join(TaskExecution, TaskExecution.scope_id == Scope.id)
        .join(Task, Task.id == TaskExecution.task_id)
        .where(Scope.project_id == project_id)
    ).all()


@router.get("/projects/{project_id}/kpis", response_model=KpiResponse)
def project_kpis(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    project = _load_project(db, project_id)
    model = StageModel.from_session(db)
    node_stages = resolve_many(model, _node_stage_rows(db, project_id))
    summary = summarise(node_stages.values())

    # Second query: delay aggregates.
    at_risk, total_delay = db.execute(
        select(
            func.count(func.distinct(TaskExecution.scope_id)).filter(
                TaskExecution.delay_days > AT_RISK_THRESHOLD_DAYS
            ),
            func.coalesce(func.sum(TaskExecution.delay_days), 0),
        ).where(TaskExecution.project_id == project_id)
    ).one()

    return KpiResponse(
        project_id=project.id,
        project_name=project.name,
        total_nodes=summary.total_nodes,
        health=summary.health,
        trend=summary.trend,
        live_nodes=summary.live_nodes,
        # Already a percentage. The legacy dashboard script multiplied the
        # equivalent value by 100 again and rendered 3.5% as 350% (audit H1).
        progress=summary.progress,
        at_risk_nodes=at_risk or 0,
        total_delay_days=int(total_delay or 0),
        stage_mix=summary.stage_mix,
    )


@router.get("/projects/{project_id}/governance-matrix")
def project_governance_matrix(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    _load_project(db, project_id)
    model = StageModel.from_session(db)
    node_stages = resolve_many(model, _node_stage_rows(db, project_id))
    circles = {
        sid: circle
        for sid, circle in db.execute(
            select(Scope.id, Scope.circle).where(Scope.project_id == project_id)
        ).all()
    }
    return governance_matrix(model, node_stages.values(), circles)


@router.get("/projects/{project_id}/nodes")
def project_nodes(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Per-node stage and delay. Two queries regardless of node count."""
    _load_project(db, project_id)
    model = StageModel.from_session(db)
    node_stages = resolve_many(model, _node_stage_rows(db, project_id))

    rows = db.execute(
        select(
            Scope.id,
            Scope.node_id,
            Scope.circle,
            Scope.facility_name,
            Scope.num_servers,
            func.coalesce(func.sum(TaskExecution.delay_days), 0).label("delay_days"),
            func.max(TaskExecution.delay_days).label("worst_delay"),
        )
        .join(TaskExecution, TaskExecution.scope_id == Scope.id, isouter=True)
        .where(Scope.project_id == project_id)
        .group_by(Scope.id)
        .order_by(Scope.circle, Scope.node_id)
    ).all()

    out = []
    for sid, node_id, circle, facility, servers, delay_days, worst in rows:
        stage = node_stages.get(sid)
        out.append(
            {
                "scope_id": sid,
                "node_id": node_id,
                "circle": circle,
                "facility_name": facility,
                "num_servers": servers,
                "stage": stage.stage_name if stage else "Not Started",
                "stage_position": stage.sequence_position if stage else 0,
                "weight": stage.weight if stage else 0,
                "completed_tasks": stage.completed_tasks if stage else 0,
                "total_tasks": stage.total_tasks if stage else 0,
                "total_delay_days": int(delay_days or 0),
                "worst_delay_days": int(worst or 0),
                "at_risk": int(worst or 0) > AT_RISK_THRESHOLD_DAYS,
            }
        )
    return {"project_id": project_id, "nodes": out}


@router.get("/projects/{project_id}/delay-heatmap")
def delay_heatmap(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Delay aggregated by circle and by facility. One grouped query each."""
    _load_project(db, project_id)

    def aggregate(column):
        return [
            {
                "key": key,
                "delayed_tasks": int(count),
                "total_delay_days": int(total),
            }
            for key, count, total in db.execute(
                select(
                    column,
                    func.count(TaskExecution.id),
                    func.coalesce(func.sum(TaskExecution.delay_days), 0),
                )
                .join(Scope, TaskExecution.scope_id == Scope.id)
                .where(Scope.project_id == project_id, TaskExecution.delay_days > 0)
                .group_by(column)
                .order_by(func.sum(TaskExecution.delay_days).desc())
            ).all()
        ]

    return {
        "by_circle": aggregate(Scope.circle),
        "by_facility": aggregate(Scope.facility_name),
    }


@router.get("/stages")
def stage_ladder(
    db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)
):
    """The stage ladder, in order. Exposed so the UI never hardcodes it."""
    model = StageModel.from_session(db)
    return {
        "stages": [
            {
                "name": s.name,
                "position": s.sequence_position,
                "weight": s.weight,
                "is_terminal": s.is_terminal,
            }
            for s in model.ordered
        ]
    }


# ---------------------------------------------------------------------------
# Narrative, circle rollup and programme rollup
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/narrative")
def narrative(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """One paragraph, generated from the same figures as the KPIs.

    The legacy narrative printed a *task* name under the label "Most delayed
    stage", and separately computed a `most_delayed_stage` that was really the
    least mature stage present and was never used (audit H11).
    """
    _load_project(db, project_id)
    return {"project_id": project_id, "narrative": project_narrative(db, project_id)}


@router.get("/projects/{project_id}/circles")
def project_circle_rollup(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Per-circle health, weakest first."""
    _load_project(db, project_id)
    return {
        "project_id": project_id,
        "circles": [asdict(c) for c in circle_rollups(db, project_id)],
    }


@router.get("/programmes/{programme_id}/rollup")
def programme_summary(
    programme_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Programme aggregate, weighted by node.

    The legacy version averaged project healths unweighted, counted projects per
    circle while calling them nodes, and raised NameError for a programme with
    no projects (audit H9, H10, C12).
    """
    rollup = programme_rollup(db, programme_id)
    if rollup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Programme not found")
    return asdict(rollup)


# ---------------------------------------------------------------------------
# PDF packs
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/pack.pdf")
def project_pdf(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    project = _load_project(db, project_id)
    try:
        content = pdf_service.project_pack(db, project_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return pdf_response(
        content, f"IPTT_{safe_filename(project.name)}_{date.today():%Y%m%d}.pdf"
    )


@router.get("/programmes/{programme_id}/pack.pdf")
def programme_pdf(
    programme_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    programme = db.get(Programme, programme_id)
    if programme is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Programme not found")
    try:
        content = pdf_service.programme_pack(db, programme_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return pdf_response(
        content, f"IPTT_{safe_filename(programme.name)}_{date.today():%Y%m%d}.pdf"
    )


@router.get("/projects/{project_id}/circles/{circle}/pack.pdf")
def circle_pdf(
    project_id: int,
    circle: str,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    project = _load_project(db, project_id)
    try:
        content = pdf_service.circle_pack(db, project_id, circle)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return pdf_response(
        content,
        f"IPTT_{safe_filename(project.name)}_{safe_filename(circle)}_{date.today():%Y%m%d}.pdf",
    )


# ---------------------------------------------------------------------------
# Portfolio-wide reports ("Quick Reports" in the legacy home page)
# ---------------------------------------------------------------------------


def _visible_projects(db: Session, user: CurrentUser) -> list[int] | None:
    """Which projects this caller may see. None means all of them.

    A PM sees only assigned projects, so the governance and circle dashboards
    must be scoped the same way the legacy screens were - otherwise they become
    a way to read the whole portfolio without an assignment.
    """
    if user.role == Role.ADMIN:
        return None
    return list(
        db.scalars(
            select(ProjectAssignment.project_id).where(
                ProjectAssignment.user_id == user.id
            )
        ).all()
    )


@router.get("/governance")
def governance_dashboard(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Programme-level governance, weakest first."""
    rows = governance_rows(db, _visible_projects(db, user))
    return {
        "programmes": [asdict(r) for r in rows],
        "totals": {
            "programmes": len(rows),
            "projects": sum(r.total_projects for r in rows),
            "planned_projects": sum(r.planned_projects for r in rows),
            "nodes": sum(r.total_nodes for r in rows),
            "at_risk_nodes": sum(r.at_risk_nodes for r in rows),
            "total_delay_days": sum(r.total_delay_days for r in rows),
        },
    }


@router.get("/circles")
def circle_intelligence_dashboard(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Every circle across the portfolio, weakest first."""
    rows = circle_intelligence(db, _visible_projects(db, user))
    return {
        "circles": [asdict(r) for r in rows],
        "totals": {
            "circles": len(rows),
            "facilities": sum(r.facilities for r in rows),
            "nodes": sum(r.total_nodes for r in rows),
            "completed_nodes": sum(r.completed_nodes for r in rows),
            "wip_nodes": sum(r.wip_nodes for r in rows),
            "at_risk_nodes": sum(r.at_risk_nodes for r in rows),
            "total_delay_days": sum(r.total_delay_days for r in rows),
        },
    }


# ---------------------------------------------------------------------------
# Forecast and drill-downs
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/forecast")
def project_forecast_view(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Where each node lands if it carries on as it has been.

    Working days throughout - the legacy version projected over calendar days,
    which inflated every forecast by roughly 40%.
    """
    forecast = forecast_service.project_forecast(db, project_id)
    if forecast is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return asdict(forecast)


@router.get("/projects/{project_id}/circles/{circle}")
def circle_detail(
    project_id: int,
    circle: str,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """One circle inside one project: its nodes, stages and delay."""
    _load_project(db, project_id)
    model = StageModel.from_session(db)
    node_stages = resolve_many(model, _node_stage_rows(db, project_id))

    rows = db.execute(
        select(
            Scope.id,
            Scope.node_id,
            Scope.facility_name,
            Scope.num_servers,
            func.coalesce(func.sum(TaskExecution.delay_days), 0),
            func.max(TaskExecution.delay_days),
        )
        .join(TaskExecution, TaskExecution.scope_id == Scope.id, isouter=True)
        .where(Scope.project_id == project_id, Scope.circle == circle)
        .group_by(Scope.id)
        .order_by(Scope.node_id)
    ).all()
    if not rows:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No nodes in circle '{circle}' on this project"
        )

    nodes = []
    for sid, node_id, facility, servers, delay, worst in rows:
        stage = node_stages.get(sid)
        nodes.append(
            {
                "scope_id": sid,
                "node_id": node_id,
                "facility_name": facility,
                "num_servers": servers,
                "stage": stage.stage_name if stage else "Not Started",
                "weight": stage.weight if stage else 0,
                "completed_tasks": stage.completed_tasks if stage else 0,
                "total_tasks": stage.total_tasks if stage else 0,
                "total_delay_days": int(delay or 0),
                "worst_delay_days": int(worst or 0),
                "at_risk": int(worst or 0) > AT_RISK_THRESHOLD_DAYS,
            }
        )

    members = [node_stages[n["scope_id"]] for n in nodes if n["scope_id"] in node_stages]
    summary = summarise(members)
    return {
        "project_id": project_id,
        "circle": circle,
        "node_count": len(nodes),
        "health": summary.health,
        "progress": summary.progress,
        "live_nodes": summary.live_nodes,
        "stage_mix": summary.stage_mix,
        "facilities": sorted({n["facility_name"] for n in nodes}),
        "nodes": nodes,
    }


@router.get("/projects/{project_id}/facilities/{facility}")
def facility_detail(
    project_id: int,
    facility: str,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """One facility: often several nodes in the same building."""
    _load_project(db, project_id)
    model = StageModel.from_session(db)
    node_stages = resolve_many(model, _node_stage_rows(db, project_id))

    rows = db.execute(
        select(
            Scope.id,
            Scope.node_id,
            Scope.circle,
            Scope.num_servers,
            func.coalesce(func.sum(TaskExecution.delay_days), 0),
        )
        .join(TaskExecution, TaskExecution.scope_id == Scope.id, isouter=True)
        .where(Scope.project_id == project_id, Scope.facility_name == facility)
        .group_by(Scope.id)
        .order_by(Scope.node_id)
    ).all()
    if not rows:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No nodes at facility '{facility}'"
        )

    nodes = [
        {
            "scope_id": sid,
            "node_id": node_id,
            "circle": circle,
            "num_servers": servers,
            "stage": (node_stages.get(sid).stage_name if sid in node_stages else "Not Started"),
            "weight": (node_stages.get(sid).weight if sid in node_stages else 0),
            "total_delay_days": int(delay or 0),
        }
        for sid, node_id, circle, servers, delay in rows
    ]
    members = [node_stages[n["scope_id"]] for n in nodes if n["scope_id"] in node_stages]
    summary = summarise(members)
    return {
        "project_id": project_id,
        "facility_name": facility,
        "circles": sorted({n["circle"] for n in nodes}),
        "node_count": len(nodes),
        "health": summary.health,
        "progress": summary.progress,
        "total_servers": sum(n["num_servers"] or 0 for n in nodes),
        "nodes": nodes,
    }


@router.get("/nodes/{scope_id}")
def node_detail(
    scope_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Every activity for one node, in template order.

    The execution grid shows all nodes at once; this is the single-node view
    the legacy app reached from a node name.
    """
    scope = db.get(Scope, scope_id)
    if scope is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")

    rows = db.execute(
        select(
            Task.template_task_number,
            Task.name,
            Task.duration_days,
            Task.planned_start,
            Task.planned_finish,
            TaskExecution.id,
            TaskExecution.actual_start,
            TaskExecution.actual_finish,
            TaskExecution.status,
            TaskExecution.delay_days,
            TaskExecution.delay_reason,
        )
        .join(TaskExecution, TaskExecution.task_id == Task.id)
        .where(TaskExecution.scope_id == scope_id)
        .order_by(Task.template_task_number)
    ).all()

    model = StageModel.from_session(db)
    # resolve_many wants (scope_id, template_task_number, status) triples.
    stage = resolve_many(
        model, [(scope_id, r.template_task_number, r.status) for r in rows]
    ).get(scope_id)

    return {
        "scope_id": scope_id,
        "node_id": scope.node_id,
        "circle": scope.circle,
        "facility_name": scope.facility_name,
        "num_servers": scope.num_servers,
        "project_id": scope.project_id,
        "stage": stage.stage_name if stage else "Not Started",
        "weight": stage.weight if stage else 0,
        "activities": [
            {
                "template_task_number": number,
                "name": name,
                "duration_days": duration,
                "planned_start": planned_start,
                "planned_finish": planned_finish,
                "execution_id": execution_id,
                "actual_start": actual_start,
                "actual_finish": actual_finish,
                "status": status_value,
                "delay_days": delay_days,
                "delay_reason": reason,
            }
            for (
                number, name, duration, planned_start, planned_finish,
                execution_id, actual_start, actual_finish, status_value,
                delay_days, reason,
            ) in rows
        ],
    }
