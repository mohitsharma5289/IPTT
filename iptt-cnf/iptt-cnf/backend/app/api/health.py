"""Liveness and readiness. The legacy app had neither, so OpenShift would have
had nothing to probe (audit B7)."""
from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config import get_settings
from app.db import engine

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness")
def healthz() -> dict:
    """Process is up. Deliberately does not touch the database: a database blip
    should not cause OpenShift to restart healthy pods."""
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness")
def readyz(response: Response) -> dict:
    """Ready to serve traffic, which requires a working database connection."""
    settings = get_settings()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            revision = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "unreachable", "error": type(exc).__name__}

    return {
        "status": "ok",
        "database": "ok",
        "schema_revision": revision,
        "environment": settings.environment,
    }
