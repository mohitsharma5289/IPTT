"""Audit log viewer.

The legacy endpoint required no authentication, so the full change history —
including who changed what — was readable by anyone (audit M7). It also applied
a bare `LIMIT 200` with no pagination, which made every entry beyond the most
recent 200 permanently unreachable (M5).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuditLog
from app.security import CurrentUser, require_admin

router = APIRouter()


class AuditEntry(BaseModel):
    id: int
    created_at: datetime
    actor_username: str
    actor_role: str | None
    action: str
    source: str
    project_id: int | None
    node_id: str | None
    task_name: str | None
    field: str | None
    old_value: str | None
    new_value: str | None


class AuditPage(BaseModel):
    entries: list[AuditEntry]
    next_cursor: int | None
    has_more: bool


@router.get("", response_model=AuditPage)
def read_audit(
    project_id: int | None = None,
    scope_id: int | None = None,
    action: str | None = None,
    source: str | None = None,
    actor: str | None = None,
    cursor: int | None = Query(None, description="Return entries older than this id"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
):
    """Keyset pagination on descending id, so the whole history is reachable."""
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit + 1)
    if project_id is not None:
        query = query.where(AuditLog.project_id == project_id)
    if scope_id is not None:
        query = query.where(AuditLog.scope_id == scope_id)
    if action:
        query = query.where(AuditLog.action == action)
    if source:
        query = query.where(AuditLog.source == source)
    if actor:
        query = query.where(AuditLog.actor_username.ilike(f"%{actor}%"))
    if cursor is not None:
        query = query.where(AuditLog.id < cursor)

    rows = db.scalars(query).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    return AuditPage(
        entries=[
            AuditEntry(
                id=r.id, created_at=r.created_at, actor_username=r.actor_username,
                actor_role=r.actor_role, action=r.action, source=r.source,
                project_id=r.project_id, node_id=r.node_id, task_name=r.task_name,
                field=r.field, old_value=r.old_value, new_value=r.new_value,
            )
            for r in rows
        ],
        next_cursor=rows[-1].id if rows and has_more else None,
        has_more=has_more,
    )


@router.get("/actions", response_model=list[str])
def distinct_actions(
    db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)
):
    """Populates the filter dropdown."""
    return [a for (a,) in db.execute(select(AuditLog.action).distinct().order_by(AuditLog.action)).all()]
