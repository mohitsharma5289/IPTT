"""Execution grid: read and update.

Every write here is typed, authenticated, authorised against the project
assignment, validated server-side and audited. The legacy equivalent accepted an
untyped `list[dict]`, enforced date sanity only in JavaScript, and had three
sibling Excel importers that wrote the same columns with no authentication and
no audit entry at all (audit B5, H12, M6, M23).
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.domain import delay as delay_rules
from app.domain.calendar import WorkingCalendar
from app.models import AuditLog, Project, Scope, Task, TaskExecution
from app.services import excel as excel_service
from app.models.enums import ExecutionStatus
from app.security import (
    CurrentUser,
    assert_can_write_project,
    get_current_user,
    require_csrf,
    require_pm_or_admin,
)

router = APIRouter()


class ExecutionUpdate(BaseModel):
    scope_id: int
    task_id: int
    actual_start: date | None = None
    actual_finish: date | None = None
    status: ExecutionStatus | None = None
    delay_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def check_dates(self) -> "ExecutionUpdate":
        """Server-side, not browser-side. The legacy API accepted a finish before
        a start because the rule lived only in `execution.js`; the migration
        found four such rows in live data."""
        if self.actual_finish and not self.actual_start:
            raise ValueError("Actual finish requires an actual start")
        if self.actual_start and self.actual_finish and self.actual_finish < self.actual_start:
            raise ValueError("Actual finish cannot precede actual start")
        return self

    def derived_status(self) -> ExecutionStatus:
        if self.actual_finish:
            return ExecutionStatus.COMPLETED
        if self.actual_start:
            return ExecutionStatus.IN_PROGRESS
        return ExecutionStatus.NOT_STARTED


class BulkUpdateRequest(BaseModel):
    updates: list[ExecutionUpdate] = Field(min_length=1, max_length=500)


@router.get("/projects/{project_id}/grid")
def execution_grid(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    circle: str | None = None,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Paginated execution grid. Two queries total, not one per node."""
    if db.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    scope_query = select(Scope).where(Scope.project_id == project_id)
    if circle:
        scope_query = scope_query.where(Scope.circle == circle)

    total = db.scalar(
        select(func.count()).select_from(scope_query.subquery())
    )
    scopes = db.scalars(
        scope_query.order_by(Scope.circle, Scope.node_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    scope_ids = [s.id for s in scopes]

    rows = (
        db.execute(
            select(TaskExecution, Task)
            .join(Task, TaskExecution.task_id == Task.id)
            .where(TaskExecution.scope_id.in_(scope_ids))
            .order_by(TaskExecution.scope_id, Task.template_task_number)
        ).all()
        if scope_ids
        else []
    )

    by_scope: dict[int, list[dict]] = {sid: [] for sid in scope_ids}
    for execution, task in rows:
        by_scope[execution.scope_id].append(
            {
                "execution_id": execution.id,
                "task_id": task.id,
                "template_task_number": task.template_task_number,
                "task_name": task.name,
                # Decision 2: the task row is the baseline. There is no second copy.
                "planned_start": task.planned_start,
                "planned_finish": task.planned_finish,
                "actual_start": execution.actual_start,
                "actual_finish": execution.actual_finish,
                "status": execution.status,
                "delay_days": execution.delay_days,
                "delay_reason": execution.delay_reason,
            }
        )

    return {
        "page": page,
        "page_size": page_size,
        "total_nodes": total,
        "nodes": [
            {
                "scope_id": s.id,
                "node_id": s.node_id,
                "circle": s.circle,
                "facility_name": s.facility_name,
                "num_servers": s.num_servers,
                "tasks": by_scope[s.id],
            }
            for s in scopes
        ],
    }


@router.put("/bulk-update")
def bulk_update(
    payload: BulkUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_pm_or_admin),
    _: None = Depends(require_csrf),
):
    keys = [(u.scope_id, u.task_id) for u in payload.updates]
    scope_ids = {s for s, _t in keys}

    executions = {
        (e.scope_id, e.task_id): e
        for e in db.scalars(
            select(TaskExecution).where(TaskExecution.scope_id.in_(scope_ids))
        ).all()
    }
    scopes = {
        s.id: s for s in db.scalars(select(Scope).where(Scope.id.in_(scope_ids))).all()
    }
    tasks = {
        t.id: t
        for t in db.scalars(
            select(Task).where(Task.id.in_({t for _s, t in keys}))
        ).all()
    }

    project_ids = {s.project_id for s in scopes.values()}
    for pid in project_ids:
        assert_can_write_project(db, user, pid)

    calendar = WorkingCalendar.from_session(db)
    applied = 0

    for update in payload.updates:
        execution = executions.get((update.scope_id, update.task_id))
        if execution is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No execution row for scope {update.scope_id} task {update.task_id}",
            )
        scope = scopes[update.scope_id]
        task = tasks[update.task_id]

        new_status = update.status or update.derived_status()
        if new_status == ExecutionStatus.NOT_STARTED:
            update.actual_start = None
            update.actual_finish = None
        elif new_status == ExecutionStatus.COMPLETED and not update.actual_finish:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"'{task.name}': completing requires an actual finish date",
            )
        elif new_status == ExecutionStatus.IN_PROGRESS and not update.actual_start:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"'{task.name}': in progress requires an actual start date",
            )

        for field, old, new in (
            ("actual_start", execution.actual_start, update.actual_start),
            ("actual_finish", execution.actual_finish, update.actual_finish),
            ("status", execution.status, new_status),
            ("delay_reason", execution.delay_reason, update.delay_reason),
        ):
            if old != new:
                db.add(
                    AuditLog(
                        actor_user_id=user.id,
                        actor_username=user.username,
                        actor_role=user.role,
                        action="EXECUTION_UPDATE",
                        source="api",
                        project_id=scope.project_id,
                        scope_id=scope.id,
                        task_id=task.id,
                        node_id=scope.node_id,
                        task_name=task.name,
                        field=field,
                        old_value=str(old) if old is not None else None,
                        new_value=str(new) if new is not None else None,
                    )
                )

        execution.actual_start = update.actual_start
        execution.actual_finish = update.actual_finish
        execution.status = new_status
        if update.delay_reason is not None:
            execution.delay_reason = update.delay_reason

        # Decisions 1 + 2: working days, against task.planned_finish.
        execution.delay_days = delay_rules.compute_delay_days(
            calendar, task.planned_finish, execution.actual_finish, scope.circle
        )

        if execution.actual_start:
            project = db.get(Project, scope.project_id)
            if project and not project.baseline_locked:
                project.baseline_locked = True

        applied += 1

    return {"applied": applied}


# ---------------------------------------------------------------------------
# Excel round trip
# ---------------------------------------------------------------------------

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _require_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


async def _read_upload(file: UploadFile) -> bytes:
    """Read an upload under a hard size cap.

    The legacy importers passed the file straight to pandas with no bound at
    all, so one oversized workbook was a memory exhaustion away from an
    OOMKill under a container limit (audit M12).
    """
    settings = get_settings()
    if file.filename and not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Upload an .xlsx workbook"
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"That file is larger than the {settings.max_upload_bytes // (1024 * 1024)} MB limit",
            )
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty")
    return b"".join(chunks)


@router.get("/projects/{project_id}/export")
def export_execution(
    project_id: int,
    circle: str | None = None,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Download the execution grid as a workbook."""
    project = _require_project(db, project_id)
    try:
        content = excel_service.build_workbook(db, project_id, circle)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    safe_name = "".join(
        ch if ch.isalnum() or ch in "-_" else "_" for ch in project.name
    )[:60]
    suffix = f"_{circle}" if circle else ""
    filename = f"IPTT_{safe_name}{suffix}_{date.today():%Y%m%d}.xlsx"

    return StreamingResponse(
        iter([content]),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/projects/{project_id}/import")
async def import_execution(
    project_id: int,
    file: UploadFile = File(...),
    dry_run: bool = Query(True, description="Validate and report without writing"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_pm_or_admin),
    _: None = Depends(require_csrf),
):
    """Apply an edited workbook.

    Defaults to a dry run: the caller sees exactly which cells would change and
    must opt in with `dry_run=false` to write. Nothing is applied unless the
    whole file validates, and every change is audited.
    """
    _require_project(db, project_id)
    assert_can_write_project(db, user, project_id)

    content = await _read_upload(file)

    report = excel_service.parse_workbook(
        db,
        project_id,
        content,
        dry_run=dry_run,
        actor_username=user.username,
        actor_user_id=user.id,
        actor_role=user.role,
    )

    if not report.ok:
        # A failed import must not leave partial writes behind.
        db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": "The workbook was not applied", **report.summary()},
        )

    return report.summary()
