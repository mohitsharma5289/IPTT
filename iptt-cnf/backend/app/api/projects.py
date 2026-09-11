from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ExecutionArchive, Programme, Project, Scope, Task
from app.security import (
    CurrentUser,
    assert_can_write_project,
    get_current_user,
    require_admin,
    require_csrf,
)
from app.services.baseline import BaselineResult, run_baseline

router = APIRouter()


class ProjectSummary(BaseModel):
    id: int
    programme_id: int
    programme_name: str
    name: str
    status: str
    project_start_date: date | None
    baseline_locked: bool
    baseline_version: int
    node_count: int
    task_count: int


class BaselineRequest(BaseModel):
    kickoff_date: date | None = None
    reason: str = "baseline"


@router.get("", response_model=list[ProjectSummary])
def list_projects(
    programme_id: int | None = None,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    query = (
        select(
            Project,
            Programme.name,
            func.count(func.distinct(Scope.id)),
            func.count(func.distinct(Task.id)),
        )
        .join(Programme, Programme.id == Project.programme_id)
        .join(Scope, Scope.project_id == Project.id, isouter=True)
        .join(Task, Task.project_id == Project.id, isouter=True)
        .group_by(Project.id, Programme.name)
        .order_by(Programme.name, Project.name)
    )
    if programme_id is not None:
        query = query.where(Project.programme_id == programme_id)

    return [
        ProjectSummary(
            id=p.id,
            programme_id=p.programme_id,
            programme_name=programme_name,
            name=p.name,
            status=p.status,
            project_start_date=p.project_start_date,
            baseline_locked=p.baseline_locked,
            baseline_version=p.baseline_version,
            node_count=nodes,
            task_count=tasks,
        )
        for p, programme_name, nodes, tasks in db.execute(query).all()
    ]


@router.get("/{project_id}", response_model=ProjectSummary)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    row = db.execute(
        select(
            Project,
            Programme.name,
            func.count(func.distinct(Scope.id)),
            func.count(func.distinct(Task.id)),
        )
        .join(Programme, Programme.id == Project.programme_id)
        .join(Scope, Scope.project_id == Project.id, isouter=True)
        .join(Task, Task.project_id == Project.id, isouter=True)
        .where(Project.id == project_id)
        .group_by(Project.id, Programme.name)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    p, programme_name, nodes, tasks = row
    return ProjectSummary(
        id=p.id,
        programme_id=p.programme_id,
        programme_name=programme_name,
        name=p.name,
        status=p.status,
        project_start_date=p.project_start_date,
        baseline_locked=p.baseline_locked,
        baseline_version=p.baseline_version,
        node_count=nodes,
        task_count=tasks,
    )


@router.get("/{project_id}/circles")
def project_circles(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Distinct circles with node counts, for filter controls."""
    rows = db.execute(
        select(Scope.circle, func.count(Scope.id))
        .where(Scope.project_id == project_id)
        .group_by(Scope.circle)
        .order_by(Scope.circle)
    ).all()
    return {"circles": [{"circle": c, "node_count": n} for c, n in rows]}


@router.post("/{project_id}/baseline")
def create_baseline(
    project_id: int,
    payload: BaselineRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
) -> BaselineResult:
    """Generate the Day-0 plan. Refused once execution has started."""
    try:
        return run_baseline(
            db,
            project_id,
            kickoff_date=payload.kickoff_date,
            actor=user.username,
            reason=payload.reason,
            is_rebaseline=False,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/{project_id}/rebaseline")
def rebaseline(
    project_id: int,
    payload: BaselineRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
) -> BaselineResult:
    """Re-plan a project that is already under way.

    Decision 6: the current execution state is archived first and the live
    actuals are kept. The legacy flow deleted every execution row across four
    unguarded transactions, losing actual dates, delay reasons and status with
    no rollback (audit C4).
    """
    try:
        return run_baseline(
            db,
            project_id,
            kickoff_date=payload.kickoff_date,
            actor=user.username,
            reason=payload.reason,
            is_rebaseline=True,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{project_id}/baseline-history")
def baseline_history(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Archived execution state, newest baseline first."""
    rows = db.execute(
        select(
            ExecutionArchive.baseline_version,
            func.count(ExecutionArchive.id),
            func.min(ExecutionArchive.archived_at),
            func.max(ExecutionArchive.archived_by),
            func.max(ExecutionArchive.reason),
        )
        .where(ExecutionArchive.project_id == project_id)
        .group_by(ExecutionArchive.baseline_version)
        .order_by(ExecutionArchive.baseline_version.desc())
    ).all()
    return {
        "project_id": project_id,
        "baselines": [
            {
                "baseline_version": version,
                "rows_archived": count,
                "archived_at": archived_at,
                "archived_by": archived_by,
                "reason": reason,
            }
            for version, count, archived_at, archived_by, reason in rows
        ],
    }
