"""Authentication and authorisation.

Every route is protected unless it is explicitly listed as public. The legacy app
had 31 of 74 routes with no session check at all, thirteen of them accepting
writes and three of those destroying data (audit B5). Here, `require_*` is a
dependency the router must declare, and the default posture is closed.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AppUser, Project, ProjectAssignment
from app.models.enums import Role

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: int
    username: str
    role: str
    must_change_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_pm(self) -> bool:
        return self.role == Role.PM


# --- passwords --------------------------------------------------------------


def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(raw, hashed)
    except ValueError:
        return False


def validate_password_strength(raw: str) -> list[str]:
    """The legacy changelog claimed complexity validation; the register endpoint
    had none (audit M9)."""
    settings = get_settings()
    problems = []
    if len(raw) < settings.password_min_length:
        problems.append(f"must be at least {settings.password_min_length} characters")
    if not re.search(r"[a-z]", raw):
        problems.append("must contain a lowercase letter")
    if not re.search(r"[A-Z]", raw):
        problems.append("must contain an uppercase letter")
    if not re.search(r"\d", raw):
        problems.append("must contain a digit")
    if not re.search(r"[^A-Za-z0-9]", raw):
        problems.append("must contain a symbol")
    return problems


# --- sessions ---------------------------------------------------------------


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def login_user(request: Request, user: AppUser) -> None:
    request.session["user"] = {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "must_change_password": user.must_change_password,
    }
    request.session["csrf"] = issue_csrf_token()


def logout_user(request: Request) -> None:
    request.session.clear()


def authenticate(db: Session, username: str, password: str) -> AppUser:
    """Constant-ish time, with lockout. The legacy endpoint had no rate limiting,
    no lockout and no failed-attempt logging."""
    user = db.scalar(select(AppUser).where(AppUser.username == username))
    now = datetime.now(timezone.utc)

    if user is None:
        # Burn a hash to keep the timing similar for unknown usernames.
        pwd_context.hash("no-such-user")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    if user.locked_until and user.locked_until > now:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Account temporarily locked after repeated failed sign-ins",
        )

    if not user.is_active or not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_count = 0
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    return user


# --- dependencies -----------------------------------------------------------


def get_current_user(request: Request) -> CurrentUser:
    data = request.session.get("user")
    if not data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return CurrentUser(
        id=data["id"],
        username=data["username"],
        role=data["role"],
        must_change_password=data.get("must_change_password", False),
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return user


def require_pm_or_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role not in (Role.ADMIN, Role.PM):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Write access required")
    return user


def require_csrf(request: Request) -> None:
    """The legacy app had no CSRF protection on any state-changing form
    (audit M10)."""
    expected = request.session.get("csrf")
    supplied = request.headers.get("x-csrf-token")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or missing CSRF token")


def can_write_project(db: Session, user: CurrentUser, project_id: int) -> bool:
    """Admins write anywhere; PMs only where assigned; viewers never.

    The legacy `validate_project_access` returned True for anything that was not
    admin or pm - the viewer default, but also the default for an unknown or
    corrupted role value, and it was used to gate writes as well as reads
    (audit M13). This function is write-only and fails closed.
    """
    if user.is_admin:
        return True
    if not user.is_pm:
        return False
    return (
        db.scalar(
            select(ProjectAssignment.id).where(
                ProjectAssignment.project_id == project_id,
                ProjectAssignment.user_id == user.id,
            )
        )
        is not None
    )


def can_read_project(db: Session, user: CurrentUser, project_id: int) -> bool:
    """All signed-in roles may read. Kept as its own function so a future
    tightening has one place to change."""
    return db.scalar(select(Project.id).where(Project.id == project_id)) is not None


def assert_can_write_project(db: Session, user: CurrentUser, project_id: int) -> None:
    if not can_write_project(db, user, project_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You are not assigned to this project"
        )


def get_db_session(db: Session = Depends(get_db)) -> Session:
    return db
