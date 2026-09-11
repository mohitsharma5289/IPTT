from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuditLog, Programme, Project, Scope
from app.models.enums import ProgrammeStatus
from app.security import CurrentUser, get_current_user, require_admin, require_csrf

router = APIRouter()


class ProgrammeSummary(BaseModel):
    id: int
    name: str
    status: str
    project_count: int
    node_count: int


class ProgrammeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    status: ProgrammeStatus = ProgrammeStatus.ACTIVE


@router.get("", response_model=list[ProgrammeSummary])
def list_programmes(
    db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)
):
    rows = db.execute(
        select(
            Programme,
            func.count(func.distinct(Project.id)),
            func.count(func.distinct(Scope.id)),
        )
        .join(Project, Project.programme_id == Programme.id, isouter=True)
        .join(Scope, Scope.project_id == Project.id, isouter=True)
        .group_by(Programme.id)
        .order_by(Programme.name)
    ).all()
    return [
        ProgrammeSummary(
            id=p.id,
            name=p.name,
            status=p.status,
            project_count=projects,
            node_count=nodes,
        )
        for p, projects, nodes in rows
    ]


@router.post("", response_model=ProgrammeSummary, status_code=status.HTTP_201_CREATED)
def create_programme(
    payload: ProgrammeCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
):
    if db.scalar(select(Programme.id).where(Programme.name == payload.name)):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A programme named '{payload.name}' already exists"
        )
    programme = Programme(name=payload.name, status=payload.status)
    db.add(programme)
    db.flush()
    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_username=user.username,
            actor_role=user.role,
            action="PROGRAMME_CREATE",
            source="api",
            field="name",
            new_value=payload.name,
        )
    )
    return ProgrammeSummary(
        id=programme.id,
        name=programme.name,
        status=programme.status,
        project_count=0,
        node_count=0,
    )


@router.delete("/{programme_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_programme(
    programme_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
):
    """Refuses while child projects exist.

    The cascade would happily delete them; requiring the caller to clear the
    projects first makes an irreversible action deliberate.
    """
    programme = db.get(Programme, programme_id)
    if programme is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Programme not found")

    children = db.scalar(
        select(func.count())
        .select_from(Project)
        .where(Project.programme_id == programme_id)
    )
    if children:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Programme still has {children} project(s). Delete or move them first.",
        )

    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_username=user.username,
            actor_role=user.role,
            action="PROGRAMME_DELETE",
            source="api",
            field="name",
            old_value=programme.name,
        )
    )
    db.delete(programme)
    from fastapi import Response

    return Response(status_code=status.HTTP_204_NO_CONTENT)
