from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
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


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: LoginRequest, db: Session = Depends(get_db)):
    """Self-service registration, off by default.

    The legacy endpoint was public, unauthenticated, and created active accounts
    with no approval and no password rules (audit M9).
    """
    settings = get_settings()
    if not settings.allow_self_registration:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Self-service registration is disabled. Ask an administrator for an account.",
        )
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Not enabled in this deployment")
