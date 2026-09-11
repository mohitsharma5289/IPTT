"""Application factory.

Differences from the legacy `main.py` worth naming, all from the audit:

  * One app object. The legacy module created `FastAPI()` twice and discarded
    the first (M22).
  * No DDL at import. `Base.metadata.create_all()` ran on every worker start
    against the production database and could never ALTER anything (B3).
    Schema changes go through Alembic.
  * The exception handler no longer re-raises. Re-raising inside a handler
    turned every 400 and 404 into a 500, so "Baseline is locked" and "Project
    not found" both reached users as Internal Server Error (C11).
  * Configuration is validated at startup and the process refuses to boot with
    an insecure session secret outside local (B4).
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.api import auth, execution, health, programmes, projects, reporting
from app.config import get_settings
from app.db import engine
from app.logging_config import configure_logging, request_id_var

log = logging.getLogger("iptt")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_for_environment()
    log.info("starting", extra={"path": "-", "method": "-"})
    yield
    engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title="IPTT API",
        version="2.0",
        description="Infrastructure Project Tracking Tool",
        docs_url="/api/docs" if settings.environment != "prod" else None,
        openapi_url="/api/openapi.json" if settings.environment != "prod" else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age_seconds,
        https_only=settings.session_https_only,
        same_site="lax",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "x-csrf-token"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        duration = round((time.perf_counter() - started) * 1000, 2)
        response.headers["x-request-id"] = rid
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration,
            },
        )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Returns the status the route asked for. It does not re-raise.
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "request_id": request_id_var.get()},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Pydantic puts the original exception object in `ctx`, which is not JSON
        # serialisable. Flatten each error to the field path and the message.
        details = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())),
                "message": err.get("msg", "invalid value"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": details, "request_id": request_id_var.get()},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # The legacy handlers returned `str(e)` to the client, leaking SQL
        # fragments and file paths (audit M11). The detail goes to the log.
        log.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "request_id": request_id_var.get(),
            },
        )

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(programmes.router, prefix="/api/programmes", tags=["programmes"])
    app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
    app.include_router(execution.router, prefix="/api/execution", tags=["execution"])
    app.include_router(reporting.router, prefix="/api/reporting", tags=["reporting"])
    return app


app = create_app()
