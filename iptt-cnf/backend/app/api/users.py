"""User administration and project assignment.

The legacy screens let an admin change a role, and nothing else. There was no
way to deactivate an account, reset a forgotten password, or see who was
assigned to what — and the four live accounts all used credentials committed to
the repository (audit M8).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser, AuditLog, Project, ProjectAssignment
from app.models.enums import Role
from app.security import (
    CurrentUser,
    hash_password,
    require_admin,
    require_csrf,
    validate_password_strength,
)

router = APIRouter()


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    is_locked: bool
    assigned_project_ids: list[int]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=8, max_length=256)
    role: Role = Role.VIEWER


class RoleUpdate(BaseModel):
    role: Role


class ActiveUpdate(BaseModel):
    is_active: bool


class AssignmentUpdate(BaseModel):
    project_ids: list[int]


def _serialise(db: Session) -> list[UserOut]:
    users = db.scalars(select(AppUser).order_by(AppUser.username)).all()
    assignments: dict[int, list[int]] = {}
    for user_id, project_id in db.execute(
        select(ProjectAssignment.user_id, ProjectAssignment.project_id)
    ).all():
        assignments.setdefault(user_id, []).append(project_id)

    now = datetime.now(timezone.utc)
    return [
        UserOut(
            id=u.id,
            username=u.username,
            role=u.role,
            is_active=u.is_active,
            must_change_password=u.must_change_password,
            last_login_at=u.last_login_at,
            is_locked=bool(u.locked_until and u.locked_until > now),
            assigned_project_ids=sorted(assignments.get(u.id, [])),
        )
        for u in users
    ]


def _audit(db: Session, actor: CurrentUser, action: str, target: AppUser,
           field: str, old: str | None, new: str | None) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor.id, actor_username=actor.username, actor_role=actor.role,
            action=action, source="api", field=field,
            old_value=old, new_value=new,
            task_name=target.username,  # denormalised label so it survives deletion
        )
    )


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return _serialise(db)


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    actor: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
):
    username = payload.username.strip()
    if db.scalar(select(AppUser.id).where(AppUser.username == username)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"'{username}' already exists")

    problems = validate_password_strength(payload.password)
    if problems:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Password " + "; ".join(problems)
        )

    user = AppUser(
        username=username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    _audit(db, actor, "USER_CREATE", user, "username", None, username)
    return next(u for u in _serialise(db) if u.id == user.id)


@router.patch("/{user_id}/role", response_model=UserOut)
def update_role(
    user_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    actor: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
):
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    if user.id == actor.id and payload.role != Role.ADMIN:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You cannot remove your own administrator role.",
        )
    if user.role == Role.ADMIN and payload.role != Role.ADMIN:
        remaining = db.scalar(
            select(func.count()).select_from(AppUser).where(
                AppUser.role == Role.ADMIN, AppUser.is_active.is_(True), AppUser.id != user_id
            )
        )
        if not remaining:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "That is the last active administrator. Promote someone else first.",
            )

    old, user.role = user.role, payload.role
    _audit(db, actor, "USER_ROLE_CHANGE", user, "role", old, payload.role)
    db.flush()
    return next(u for u in _serialise(db) if u.id == user_id)


@router.patch("/{user_id}/active", response_model=UserOut)
def set_active(
    user_id: int,
    payload: ActiveUpdate,
    db: Session = Depends(get_db),
    actor: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
):
    """Deactivating is preferred over deleting: it keeps the audit trail intact."""
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == actor.id and not payload.is_active:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account."
        )
    if user.role == Role.ADMIN and not payload.is_active:
        remaining = db.scalar(
            select(func.count()).select_from(AppUser).where(
                AppUser.role == Role.ADMIN, AppUser.is_active.is_(True), AppUser.id != user_id
            )
        )
        if not remaining:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "That is the last active administrator."
            )

    old, user.is_active = user.is_active, payload.is_active
    if payload.is_active:
        user.failed_login_count = 0
        user.locked_until = None
    _audit(db, actor, "USER_ACTIVE_CHANGE", user, "is_active", str(old), str(payload.is_active))
    db.flush()
    return next(u for u in _serialise(db) if u.id == user_id)


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    actor: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
):
    """Issues a one-time password the admin passes on out of band.

    The generated value is returned exactly once and never stored in clear.
    """
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    temporary = secrets.token_urlsafe(12) + "aA1!"
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    _audit(db, actor, "USER_PASSWORD_RESET", user, "password_hash", None, "reset")
    return {
        "username": user.username,
        "temporary_password": temporary,
        "note": "Shown once. The user must change it at next sign-in.",
    }


@router.put("/{user_id}/assignments", response_model=UserOut)
def set_assignments(
    user_id: int,
    payload: AssignmentUpdate,
    db: Session = Depends(get_db),
    actor: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
):
    """Which projects a PM may write to. Admins write everywhere regardless."""
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    wanted = set(payload.project_ids)
    known = {
        pid for (pid,) in db.execute(select(Project.id).where(Project.id.in_(wanted))).all()
    } if wanted else set()
    unknown = wanted - known
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown project id(s): {sorted(unknown)}"
        )

    current = {
        a.project_id: a
        for a in db.scalars(
            select(ProjectAssignment).where(ProjectAssignment.user_id == user_id)
        ).all()
    }
    for project_id in wanted - set(current):
        db.add(ProjectAssignment(user_id=user_id, project_id=project_id))
    for project_id in set(current) - wanted:
        db.delete(current[project_id])

    _audit(
        db, actor, "USER_ASSIGNMENTS", user, "assignments",
        ",".join(map(str, sorted(current))) or None,
        ",".join(map(str, sorted(wanted))) or None,
    )
    db.flush()
    return next(u for u in _serialise(db) if u.id == user_id)
