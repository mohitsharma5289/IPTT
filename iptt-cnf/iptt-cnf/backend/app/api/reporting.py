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

from app.db import get_db
from app.domain.delay import AT_RISK_THRESHOLD_DAYS
from app.domain.stages import StageModel, governance_matrix, resolve_many, summarise
from app.models import Project, Scope, Task, TaskExecution
from app.security import CurrentUser, get_current_user

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
