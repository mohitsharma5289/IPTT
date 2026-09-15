from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AuditLog,
    ExecutionArchive,
    Programme,
    Project,
    Scope,
    Task,
    TaskExecution,
)
from app.models.enums import ProjectStatus
from app.security import (
    CurrentUser,
    assert_can_write_project,
    get_current_user,
    require_admin,
    require_csrf,
)
from app.services.baseline import (
    BaselineResult,
    baseline_readiness,
    maybe_autobaseline,
    run_baseline,
)

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


class ProjectCreate(BaseModel):
    programme_id: int
    name: str = Field(min_length=1, max_length=200)
    status: ProjectStatus = ProjectStatus.NOT_STARTED
    # Optional: a project is routinely created before its kickoff is agreed.
    # Supplying it here does not plan anything on its own - scope and a task
    # template are also required. See maybe_autobaseline.
    project_start_date: date | None = None


class ProjectUpdate(BaseModel):
    """Every field optional; only what is sent is changed."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: ProjectStatus | None = None
    project_start_date: date | None = None
    programme_id: int | None = None


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


# ---------------------------------------------------------------------------
# Project lifecycle
# ---------------------------------------------------------------------------


def _audit(
    db: Session,
    user: CurrentUser,
    action: str,
    project: Project,
    field: str,
    old: str | None,
    new: str | None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_username=user.username,
            actor_role=user.role,
            action=action,
            source="api",
            project_id=project.id,
            field=field,
            old_value=old,
            new_value=new,
            task_name=project.name,
        )
    )


@router.post("", response_model=ProjectSummary, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
):
    programme = db.get(Programme, payload.programme_id)
    if programme is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Programme not found")

    name = payload.name.strip()
    # Unique per programme, not globally: two programmes may each run a project
    # of the same name, and the legacy data does exactly that.
    if db.scalar(
        select(Project.id).where(
            Project.programme_id == payload.programme_id, Project.name == name
        )
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{programme.name}' already has a project named '{name}'",
        )

    project = Project(
        programme_id=payload.programme_id,
        name=name,
        status=payload.status,
        project_start_date=payload.project_start_date,
    )
    db.add(project)
    db.flush()
    _audit(db, user, "PROJECT_CREATE", project, "name", None, name)

    # Cannot fire yet - a new project has no scope and no template - but the
    # call keeps the rule in one place rather than assuming.
    maybe_autobaseline(db, project.id, actor=user.username)
    db.flush()
    return get_project(project.id, db, user)


@router.patch("/{project_id}", response_model=ProjectSummary)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_csrf),
):
    assert_can_write_project(db, user, project_id)
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    changes = payload.model_dump(exclude_unset=True)

    if "programme_id" in changes and db.get(Programme, changes["programme_id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Programme not found")

    if "project_start_date" in changes and project.baseline_locked:
        # Moving the kickoff after planning would silently invalidate every
        # planned date. Re-baselining is the supported way to do that, and it
        # archives the current state first (decision 6).
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This project is baselined; change the kickoff date by re-baselining "
            "so the current plan is archived first.",
        )

    for field, new in changes.items():
        old = getattr(project, field)
        if old == new:
            continue
        setattr(project, field, new)
        _audit(
            db,
            user,
            "PROJECT_UPDATE",
            project,
            field,
            None if old is None else str(old),
            None if new is None else str(new),
        )

    db.flush()
    maybe_autobaseline(db, project_id, actor=user.username)
    db.flush()
    return get_project(project_id, db, user)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_project(
    project_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
):
    """Refuses while recorded execution data exists, unless forced.

    The cascade would remove the scope, the task template, every execution row
    and the archived baselines with it. Recorded actual dates are field data
    that cannot be recovered from anywhere else, so deleting them is made
    deliberate rather than incidental.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    if not force:
        recorded = db.scalar(
            select(func.count())
            .select_from(TaskExecution)
            .where(
                TaskExecution.project_id == project_id,
                (TaskExecution.actual_start.is_not(None))
                | (TaskExecution.actual_finish.is_not(None)),
            )
        )
        if recorded:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"'{project.name}' has {recorded} activities with recorded dates. "
                "Deleting it destroys that field data. Pass force=true to proceed.",
            )

    _audit(db, user, "PROJECT_DELETE", project, "name", project.name, None)
    db.delete(project)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/baseline-readiness")
def project_baseline_readiness(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """What the project still needs before it can be planned.

    The UI uses this to tell a PM why a new project has no dates yet, instead
    of showing an empty dashboard with no explanation.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    missing = baseline_readiness(db, project_id)
    # "Planned" means planned dates actually exist. baseline_version is no use
    # for this: it starts at 1 on every new project and only counts
    # re-baselines, so it is 1 for a project that has never been planned.
    planned = bool(
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.project_id == project_id, Task.planned_finish.is_not(None))
        )
    )
    return {
        "project_id": project_id,
        "planned": planned,
        "locked": project.baseline_locked,
        "baseline_version": project.baseline_version,
        "ready": not missing,
        "missing": missing,
    }
