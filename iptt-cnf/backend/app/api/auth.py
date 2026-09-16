from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AppUser, AuditLog
from app.models.enums import Role
from app.security import (
    CurrentUser,
    authenticate,
    get_current_user,
    hash_password,
    login_user,
    logout_user,
    require_csrf,
    validate_password_strength,
    verify_password,
)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


class SessionResponse(BaseModel):
    id: int
    username: str
    role: str
    must_change_password: bool
    csrf_token: str


@router.post("/login", response_model=SessionResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = authenticate(db, payload.username, payload.password)
    login_user(request, user)
    return SessionResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        must_change_password=user.must_change_password,
        csrf_token=request.session["csrf"],
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(request: Request) -> Response:
    logout_user(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=SessionResponse)
def me(request: Request, user: CurrentUser = Depends(get_current_user)):
    return SessionResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        must_change_password=user.must_change_password,
        csrf_token=request.session.get("csrf", ""),
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_csrf),
) -> Response:
    from app.models import AppUser

    record = db.get(AppUser, user.id)
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")

    if not verify_password(payload.current_password, record.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "New password must differ from the current one"
        )

    problems = validate_password_strength(payload.new_password)
    if problems:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Password " + "; ".join(problems)
        )

    record.password_hash = hash_password(payload.new_password)
    record.must_change_password = False
    # Force a fresh sign-in everywhere. The legacy endpoint left the old session
    # valid and applied no complexity rules at all.
    logout_user(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Self-service registration. The account is created *inactive*.

    The legacy endpoint was public, unauthenticated, and created **active**
    accounts with no approval and no password rules, so anyone who could reach
    the app could mint themselves a working login (audit M9). Here the account
    lands pending: an administrator activates it from the Users screen, and
    until then `authenticate` refuses it because `is_active` is false.

    Two deliberate properties:

      * The response is identical whether or not the username was already
        taken. Returning 409 here would turn the form into a username oracle
        for an unauthenticated caller - it would confirm who has an account.
        An admin sees the collision in the pending queue instead.
      * Password rules apply at the door, not at first sign-in.
    """
    settings = get_settings()
    if not settings.allow_self_registration:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Self-service registration is disabled. Ask an administrator for an account.",
        )

    username = payload.username.strip()
    problems = validate_password_strength(payload.password)
    if problems:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Password " + "; ".join(problems)
        )

    accepted = {
        "status": "pending",
        "detail": (
            "Your request has been recorded. An administrator must approve the "
            "account before you can sign in."
        ),
    }

    if db.scalar(select(AppUser.id).where(AppUser.username == username)):
        # Same response as success, deliberately - see the docstring.
        return accepted

    user = AppUser(
        username=username,
        password_hash=hash_password(payload.password),
        role=Role.VIEWER,
        is_active=False,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_username=username,
            actor_role=Role.VIEWER,
            action="USER_REGISTER",
            source="self-registration",
            field="username",
            old_value=None,
            new_value=username,
            task_name=username,
        )
    )
    return accepted
