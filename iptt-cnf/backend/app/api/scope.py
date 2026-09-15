"""Scope (network node) management.

The legacy `routes/scope.py` had **no authentication at all** and ran in replace
mode: a `POST` from anyone deleted every node in the project, orphaning its tasks
and executions (audit B5, M19). Both endpoints here require an admin, run inside
one transaction, and refuse to destroy execution data by accident.
"""
from __future__ import annotations

from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from openpyxl import Workbook, load_workbook
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import XLSX_MEDIA_TYPE, read_upload, require_project, xlsx_response
from app.db import get_db
from app.services.baseline import maybe_autobaseline
from app.models import AuditLog, Scope, Task, TaskExecution
from app.security import CurrentUser, get_current_user, require_admin, require_csrf

router = APIRouter()


def _autobaseline_note(db, project_id: int, actor: str) -> dict | None:
    """Plan the project if this save was the one that made it plannable.

    Returned to the caller so the UI can say a plan was generated, rather than
    the dates simply appearing with no explanation.
    """
    result = maybe_autobaseline(db, project_id, actor=actor)
    if result is None:
        return None
    return {
        "generated": True,
        "baseline_version": result.baseline_version,
        "tasks_planned": result.tasks_planned,
        "plan_start": result.plan_start.isoformat() if result.plan_start else None,
        "plan_finish": result.plan_finish.isoformat() if result.plan_finish else None,
    }

REQUIRED_COLUMNS = ["Node", "Circle", "Facility", "Servers", "Priority"]


class ScopeIn(BaseModel):
    node_id: str = Field(min_length=1, max_length=100)
    circle: str = Field(min_length=1, max_length=20)
    facility_name: str = Field(min_length=1, max_length=200)
    num_servers: int = Field(ge=0, le=10_000)
    priority: int = Field(default=1, ge=1, le=999)


class ScopeOut(BaseModel):
    id: int
    node_id: str
    circle: str
    facility_name: str
    num_servers: int
    priority: int
    status: str
    task_count: int
    has_execution_data: bool


def _serialise(db: Session, project_id: int) -> list[ScopeOut]:
    rows = db.execute(
        select(
            Scope,
            func.count(func.distinct(Task.id)),
            func.count(TaskExecution.id).filter(TaskExecution.actual_start.is_not(None)),
        )
        .join(Task, Task.scope_id == Scope.id, isouter=True)
        .join(TaskExecution, TaskExecution.scope_id == Scope.id, isouter=True)
        .where(Scope.project_id == project_id)
        .group_by(Scope.id)
        .order_by(Scope.circle, Scope.node_id)
    ).all()
    return [
        ScopeOut(
            id=s.id,
            node_id=s.node_id,
            circle=s.circle,
            facility_name=s.facility_name,
            num_servers=s.num_servers,
            priority=s.priority,
            status=s.status,
            task_count=tasks,
            has_execution_data=bool(started),
        )
        for s, tasks, started in rows
    ]


@router.get("/projects/{project_id}/scope", response_model=list[ScopeOut])
def list_scope(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    require_project(db, project_id)
    return _serialise(db, project_id)


@router.post("/projects/{project_id}/scope", response_model=ScopeOut, status_code=201)
def add_scope(
    project_id: int,
    payload: ScopeIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    __: None = Depends(require_csrf),
):
    require_project(db, project_id)
    node_id = payload.node_id.strip().replace(" ", "")
    if db.scalar(
        select(Scope.id).where(Scope.project_id == project_id, Scope.node_id == node_id)
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Node '{node_id}' already exists in this project"
        )

    scope = Scope(
        project_id=project_id,
        node_id=node_id,
        circle=payload.circle.strip(),
        facility_name=payload.facility_name.strip(),
        num_servers=payload.num_servers,
        priority=payload.priority,
    )
    db.add(scope)
    db.flush()
    _autobaseline_note(db, project_id, user.username)
    db.add(
        AuditLog(
            actor_user_id=user.id, actor_username=user.username, actor_role=user.role,
            action="SCOPE_CREATE", source="api", project_id=project_id,
            scope_id=scope.id, node_id=scope.node_id, field="node_id",
            new_value=scope.node_id,
        )
    )
    db.flush()
    return next(r for r in _serialise(db, project_id) if r.id == scope.id)


@router.delete("/projects/{project_id}/scope/{scope_id}", status_code=204)
def delete_scope(
    project_id: int,
    scope_id: int,
    force: bool = Query(False, description="Delete even though execution data exists"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    __: None = Depends(require_csrf),
):
    """Refuses by default once a PM has recorded anything against the node."""
    from fastapi import Response

    scope = db.get(Scope, scope_id)
    if scope is None or scope.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found in this project")

    recorded = db.scalar(
        select(func.count())
        .select_from(TaskExecution)
        .where(TaskExecution.scope_id == scope_id, TaskExecution.actual_start.is_not(None))
    )
    if recorded and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{scope.node_id}' has {recorded} recorded activity date(s). "
            "Deleting it destroys that history. Re-send with force=true to proceed.",
        )

    db.add(
        AuditLog(
            actor_user_id=user.id, actor_username=user.username, actor_role=user.role,
            action="SCOPE_DELETE", source="api", project_id=project_id,
            scope_id=scope.id, node_id=scope.node_id, field="node_id",
            old_value=scope.node_id,
            new_value=f"forced, {recorded} recorded dates lost" if recorded else None,
        )
    )
    db.delete(scope)  # tasks and executions cascade
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/scope/template")
def scope_template(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """A blank upload template, with one example row."""
    require_project(db, project_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Scope"
    ws.append(REQUIRED_COLUMNS)
    ws.append(["MH1JPSBC01", "MH", "Mumbai GDC", 4, 1])
    for index, width in enumerate([18, 10, 32, 10, 10], start=1):
        ws.column_dimensions[chr(64 + index)].width = width
    buffer = BytesIO()
    wb.save(buffer)
    return xlsx_response(buffer.getvalue(), f"IPTT_scope_template_{project_id}.xlsx")


@router.post("/projects/{project_id}/scope/import")
async def import_scope(
    project_id: int,
    file: UploadFile = File(...),
    dry_run: bool = Query(True),
    replace: bool = Query(False, description="Remove nodes absent from the sheet"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    __: None = Depends(require_csrf),
):
    """Add and update nodes from a workbook.

    Additive by default. `replace=true` also removes nodes missing from the
    sheet, but never silently: a node carrying recorded dates blocks the import
    and is named in the error. The legacy endpoint deleted every node first,
    unconditionally and unauthenticated.
    """
    require_project(db, project_id)
    content = await read_upload(file)

    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not a readable .xlsx workbook")

    ws = wb["Scope"] if "Scope" in wb.sheetnames else wb[wb.sheetnames[0]]
    grid = list(ws.iter_rows(values_only=True))
    if not grid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The sheet is empty")

    header = [("" if v is None else str(v).strip()) for v in grid[0]]
    index_of = {h.lower(): i for i, h in enumerate(header) if h}
    missing = [c for c in REQUIRED_COLUMNS if c.lower() not in index_of]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Missing column(s): {', '.join(missing)}. Expected: "
            f"{', '.join(REQUIRED_COLUMNS)}",
        )

    existing = {s.node_id: s for s in db.scalars(
        select(Scope).where(Scope.project_id == project_id)
    ).all()}

    errors: list[str] = []
    seen: set[str] = set()
    creates: list[dict] = []
    updates: list[tuple[Scope, dict]] = []

    for row_number, row in enumerate(grid[1:], start=2):
        def cell(name: str):
            i = index_of[name.lower()]
            return row[i] if i < len(row) else None

        raw_node = cell("Node")
        if raw_node is None or not str(raw_node).strip():
            continue
        node_id = str(raw_node).strip().replace(" ", "")
        if node_id in seen:
            errors.append(f"Row {row_number}: '{node_id}' appears more than once")
            continue
        seen.add(node_id)

        try:
            values = {
                "node_id": node_id,
                "circle": str(cell("Circle") or "").strip(),
                "facility_name": str(cell("Facility") or "").strip(),
                "num_servers": int(cell("Servers") or 0),
                "priority": int(cell("Priority") or 1),
            }
        except (TypeError, ValueError):
            errors.append(f"Row {row_number} ({node_id}): Servers and Priority must be whole numbers")
            continue

        if not values["circle"] or not values["facility_name"]:
            errors.append(f"Row {row_number} ({node_id}): Circle and Facility are required")
            continue

        if node_id in existing:
            current = existing[node_id]
            changed = {
                k: v for k, v in values.items()
                if k != "node_id" and getattr(current, k) != v
            }
            if changed:
                updates.append((current, changed))
        else:
            creates.append(values)

    removals = [s for node_id, s in existing.items() if node_id not in seen] if replace else []
    blocked = []
    for scope in removals:
        recorded = db.scalar(
            select(func.count()).select_from(TaskExecution).where(
                TaskExecution.scope_id == scope.id,
                TaskExecution.actual_start.is_not(None),
            )
        )
        if recorded:
            blocked.append(f"{scope.node_id} ({recorded} recorded dates)")

    if blocked:
        errors.append(
            "These nodes are absent from the sheet but carry recorded execution "
            "dates, so replace mode will not remove them: " + ", ".join(blocked[:10])
        )

    summary = {
        "dry_run": dry_run,
        "ok": not errors,
        "rows_read": len(seen),
        "to_create": len(creates),
        "to_update": len(updates),
        "to_remove": len(removals),
        "creates": [c["node_id"] for c in creates[:100]],
        "updates": [
            {"node_id": s.node_id, "changes": changes} for s, changes in updates[:100]
        ],
        "removes": [s.node_id for s in removals[:100]],
        "errors": errors,
    }

    if errors:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, summary)
    if dry_run:
        return summary

    for values in creates:
        db.add(Scope(project_id=project_id, **values))
    for scope, changes in updates:
        for key, value in changes.items():
            setattr(scope, key, value)
    for scope in removals:
        db.delete(scope)

    db.add(
        AuditLog(
            actor_user_id=user.id, actor_username=user.username, actor_role=user.role,
            action="SCOPE_IMPORT", source="excel-import", project_id=project_id,
            field="scope",
            new_value=(
                f"created {len(creates)}, updated {len(updates)}, "
                f"removed {len(removals)}"
            ),
        )
    )
    db.flush()
    summary["autobaseline"] = _autobaseline_note(db, project_id, user.username)
    return summary
