"""Leadership action tracker.

Three legacy defects are fixed here. The routes had no authentication at all, so
anyone could create, edit or delete an escalation (audit B5). The create path
accepted a `status` argument and then hardcoded `status="Open"`, and hardcoded
`circle`, `node` and `risk_area` to "-", leaving three columns permanently unused
(H13). And the list was ordered by `priority.desc()` on a text column, which
sorted Medium, Low, High — putting the most urgent escalations last (H8).
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import require_project
from app.db import get_db
from app.models import AuditLog, LeadershipAction
from app.models.enums import PRIORITY_SORT, ActionPriority, ActionStatus
from app.security import (
    CurrentUser,
    assert_can_write_project,
    get_current_user,
    require_csrf,
    require_pm_or_admin,
)

router = APIRouter()

ESCALATION_CRITICAL_DAYS = 15
ESCALATION_HIGH_DAYS = 7


class ActionIn(BaseModel):
    action_required: str = Field(min_length=1, max_length=4000)
    owner: str | None = Field(default=None, max_length=150)
    target_date: date | None = None
    priority: ActionPriority = ActionPriority.MEDIUM
    status: ActionStatus = ActionStatus.OPEN
    circle: str | None = Field(default=None, max_length=20)
    node_id: str | None = Field(default=None, max_length=100)
    risk_area: str | None = Field(default=None, max_length=120)
    remarks: str | None = Field(default=None, max_length=4000)


class ActionOut(BaseModel):
    id: int
    project_id: int
    action_required: str
    owner: str | None
    target_date: date | None
    priority: str
    status: str
    circle: str | None
    node_id: str | None
    risk_area: str | None
    remarks: str | None
    overdue_days: int
    escalation: str


def _escalation(overdue: int) -> str:
    if overdue >= ESCALATION_CRITICAL_DAYS:
        return "Critical"
    if overdue >= ESCALATION_HIGH_DAYS:
        return "High"
    if overdue > 0:
        return "Medium"
    return "Normal"


def _out(action: LeadershipAction) -> ActionOut:
    overdue = 0
    if (
        action.target_date
        and action.status != ActionStatus.CLOSED
        and action.target_date < date.today()
    ):
        overdue = (date.today() - action.target_date).days
    return ActionOut(
        id=action.id,
        project_id=action.project_id,
        action_required=action.action_required,
        owner=action.owner,
        target_date=action.target_date,
        priority=action.priority,
        status=action.status,
        circle=action.circle,
        node_id=action.node_id,
        risk_area=action.risk_area,
        remarks=action.remarks,
        overdue_days=overdue,
        escalation=_escalation(overdue),
    )


@router.get("/projects/{project_id}/actions", response_model=list[ActionOut])
def list_actions(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Highest priority first, then soonest target date."""
    require_project(db, project_id)
    actions = db.scalars(
        select(LeadershipAction)
        .where(LeadershipAction.project_id == project_id)
        .order_by(
            LeadershipAction.priority_sort,
            LeadershipAction.target_date.nulls_last(),
            LeadershipAction.id,
        )
    ).all()
    return [_out(a) for a in actions]


@router.post("/projects/{project_id}/actions", response_model=ActionOut, status_code=201)
def create_action(
    project_id: int,
    payload: ActionIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_pm_or_admin),
    _: None = Depends(require_csrf),
):
    require_project(db, project_id)
    assert_can_write_project(db, user, project_id)

    action = LeadershipAction(
        project_id=project_id,
        action_required=payload.action_required.strip(),
        owner=payload.owner,
        target_date=payload.target_date,
        priority=payload.priority,
        priority_sort=PRIORITY_SORT[payload.priority],
        # The caller's status is honoured, unlike the legacy path.
        status=payload.status,
        circle=payload.circle,
        node_id=payload.node_id,
        risk_area=payload.risk_area,
        remarks=payload.remarks,
    )
    db.add(action)
    db.flush()
    db.add(
        AuditLog(
            actor_user_id=user.id, actor_username=user.username, actor_role=user.role,
            action="ACTION_CREATE", source="api", project_id=project_id,
            field="action_required", new_value=action.action_required[:500],
        )
    )
    return _out(action)


@router.put("/actions/{action_id}", response_model=ActionOut)
def update_action(
    action_id: int,
    payload: ActionIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_pm_or_admin),
    _: None = Depends(require_csrf),
):
    action = db.get(LeadershipAction, action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    assert_can_write_project(db, user, action.project_id)

    for field, new in (
        ("action_required", payload.action_required.strip()),
        ("owner", payload.owner),
        ("target_date", payload.target_date),
        ("priority", payload.priority),
        ("status", payload.status),
        ("circle", payload.circle),
        ("node_id", payload.node_id),
        ("risk_area", payload.risk_area),
        ("remarks", payload.remarks),
    ):
        old = getattr(action, field)
        if old != new:
            db.add(
                AuditLog(
                    actor_user_id=user.id, actor_username=user.username,
                    actor_role=user.role, action="ACTION_UPDATE", source="api",
                    project_id=action.project_id, field=field,
                    old_value=str(old) if old is not None else None,
                    new_value=str(new) if new is not None else None,
                )
            )
            setattr(action, field, new)
    action.priority_sort = PRIORITY_SORT[payload.priority]
    db.flush()
    return _out(action)


@router.delete("/actions/{action_id}", status_code=204)
def delete_action(
    action_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_pm_or_admin),
    _: None = Depends(require_csrf),
):
    action = db.get(LeadershipAction, action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    assert_can_write_project(db, user, action.project_id)

    db.add(
        AuditLog(
            actor_user_id=user.id, actor_username=user.username, actor_role=user.role,
            action="ACTION_DELETE", source="api", project_id=action.project_id,
            field="action_required", old_value=action.action_required[:500],
        )
    )
    db.delete(action)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
