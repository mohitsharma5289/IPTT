"""Task template management.

A project's template is the ordered list of activities each node must go
through. Uploading it materialises one Task row per (node × template activity) —
2,850 rows for a 57-node project.

The legacy uploader (`routes/task.py`) was unauthenticated, deleted every task
in the project on each upload, and left the corresponding TaskExecution rows
behind pointing at task ids that no longer existed. That is the mechanism that
produced the 193 orphan audit rows found during migration (audit B5, B6, M19).

Here the template is reconciled rather than replaced: activities are matched by
template number, existing rows are updated in place, and any activity carrying
recorded dates cannot be removed without an explicit force.
"""
from __future__ import annotations

from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from openpyxl import Workbook, load_workbook
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import read_upload, require_project, xlsx_response
from app.db import get_db
from app.models import AuditLog, Scope, Task, TaskExecution
from app.security import CurrentUser, get_current_user, require_admin, require_csrf

router = APIRouter()

REQUIRED_COLUMNS = ["Task_Number", "Task_Name", "Duration_Days", "Predecessor_Task_Number"]
OPTIONAL_COLUMNS = ["Is_Prerequisite", "Owner_Role"]


class TemplateRow(BaseModel):
    template_task_number: int
    name: str
    duration_days: int
    predecessor_template_number: int | None
    is_prerequisite: bool
    owner_role: str | None
    node_count: int
    recorded_dates: int


@router.get("/projects/{project_id}/template", response_model=list[TemplateRow])
def list_template(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    require_project(db, project_id)
    rows = db.execute(
        select(
            Task.template_task_number,
            func.min(Task.name),
            func.min(Task.duration_days),
            func.min(Task.predecessor_template_number),
            func.bool_or(Task.is_prerequisite),
            func.min(Task.owner_role),
            func.count(func.distinct(Task.scope_id)),
            func.count(TaskExecution.id).filter(TaskExecution.actual_start.is_not(None)),
        )
        .join(TaskExecution, TaskExecution.task_id == Task.id, isouter=True)
        .where(Task.project_id == project_id)
        .group_by(Task.template_task_number)
        .order_by(Task.template_task_number)
    ).all()
    return [
        TemplateRow(
            template_task_number=number,
            name=name,
            duration_days=duration,
            predecessor_template_number=predecessor,
            is_prerequisite=bool(prerequisite),
            owner_role=owner,
            node_count=nodes,
            recorded_dates=recorded,
        )
        for number, name, duration, predecessor, prerequisite, owner, nodes, recorded in rows
    ]


@router.get("/projects/{project_id}/template/export")
def export_template(
    project_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Download the current template, or a starter sheet if none is loaded."""
    require_project(db, project_id)
    rows = list_template(project_id, db, _)  # type: ignore[arg-type]

    wb = Workbook()
    ws = wb.active
    ws.title = "Template"
    ws.append(REQUIRED_COLUMNS + OPTIONAL_COLUMNS)
    if rows:
        for row in rows:
            ws.append([
                row.template_task_number, row.name, row.duration_days,
                row.predecessor_template_number, "Y" if row.is_prerequisite else "",
                row.owner_role or "",
            ])
    else:
        ws.append([1, "Scope defined and available", 0, None, "Y", "Planning"])
        ws.append([2, "Project Initiation", 1, 1, "", "Planning"])
    for index, width in enumerate([14, 42, 15, 24, 16, 16], start=1):
        ws.column_dimensions[chr(64 + index)].width = width
    ws.freeze_panes = "A2"

    guide = wb.create_sheet("How to use")
    for line in [
        "Task_Number  the activity's position in the template, 1..N. This is the",
        "             identifier everything else binds to — reporting, the",
        "             execution workbook and scheduling constraints. Do not",
        "             renumber an activity that already has recorded dates.",
        "Task_Name    what appears on screen and in exports.",
        "Duration_Days  working days. 0 marks a gate that consumes no time.",
        "Predecessor_Task_Number  another Task_Number in this sheet, or blank.",
        "Is_Prerequisite  Y for an activity satisfied at kickoff.",
        "Owner_Role   optional, free text.",
        "",
        "Uploading reconciles: activities are matched by Task_Number, existing",
        "rows are updated in place, and an activity carrying recorded dates",
        "cannot be removed without an explicit force.",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 78

    buffer = BytesIO()
    wb.save(buffer)
    return xlsx_response(buffer.getvalue(), f"IPTT_template_{project_id}.xlsx")


@router.post("/projects/{project_id}/template/import")
async def import_template(
    project_id: int,
    file: UploadFile = File(...),
    dry_run: bool = Query(True),
    force_remove: bool = Query(False, description="Remove activities that carry recorded dates"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    __: None = Depends(require_csrf),
):
    require_project(db, project_id)
    content = await read_upload(file)

    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not a readable .xlsx workbook")

    ws = wb["Template"] if "Template" in wb.sheetnames else wb[wb.sheetnames[0]]
    grid = list(ws.iter_rows(values_only=True))
    if not grid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The sheet is empty")

    header = [("" if v is None else str(v).strip()) for v in grid[0]]
    index_of = {h.lower(): i for i, h in enumerate(header) if h}
    missing = [c for c in REQUIRED_COLUMNS if c.lower() not in index_of]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Missing column(s): {', '.join(missing)}",
        )

    errors: list[str] = []
    parsed: dict[int, dict] = {}

    for row_number, row in enumerate(grid[1:], start=2):
        def cell(name: str):
            i = index_of.get(name.lower())
            return row[i] if i is not None and i < len(row) else None

        raw_number = cell("Task_Number")
        if raw_number is None or str(raw_number).strip() == "":
            continue
        try:
            number = int(raw_number)
        except (TypeError, ValueError):
            errors.append(f"Row {row_number}: Task_Number '{raw_number}' is not a whole number")
            continue
        if not 1 <= number <= 999:
            errors.append(f"Row {row_number}: Task_Number {number} is outside 1..999")
            continue
        if number in parsed:
            errors.append(f"Row {row_number}: Task_Number {number} appears more than once")
            continue

        name = str(cell("Task_Name") or "").strip()
        if not name:
            errors.append(f"Row {row_number}: Task_Name is required")
            continue

        try:
            duration = int(cell("Duration_Days") or 0)
        except (TypeError, ValueError):
            errors.append(f"Row {row_number}: Duration_Days must be a whole number")
            continue
        if duration < 0:
            errors.append(f"Row {row_number}: Duration_Days cannot be negative")
            continue

        raw_predecessor = cell("Predecessor_Task_Number")
        predecessor: int | None = None
        if raw_predecessor not in (None, "", 0, "0"):
            try:
                predecessor = int(raw_predecessor)
            except (TypeError, ValueError):
                errors.append(
                    f"Row {row_number}: Predecessor_Task_Number '{raw_predecessor}' "
                    "is not a whole number"
                )
                continue

        prerequisite = str(cell("Is_Prerequisite") or "").strip().lower() in {"y", "yes", "true", "1"}
        owner = str(cell("Owner_Role") or "").strip() or None

        parsed[number] = {
            "template_task_number": number,
            "name": name,
            "duration_days": duration,
            "predecessor_template_number": predecessor,
            "is_prerequisite": prerequisite,
            "owner_role": owner,
        }

    if not parsed and not errors:
        errors.append("No activities found in the sheet")

    # Dependencies must resolve inside the template, and must not cycle.
    for number, values in parsed.items():
        predecessor = values["predecessor_template_number"]
        if predecessor is None:
            continue
        if predecessor not in parsed:
            errors.append(
                f"Activity {number} lists predecessor {predecessor}, which is not "
                "in the sheet"
            )
        elif predecessor == number:
            errors.append(f"Activity {number} is its own predecessor")

    if not errors:
        for number in parsed:
            seen: set[int] = set()
            cursor: int | None = number
            while cursor is not None:
                if cursor in seen:
                    errors.append(
                        f"Activities {sorted(seen)} form a dependency cycle"
                    )
                    break
                seen.add(cursor)
                cursor = parsed[cursor]["predecessor_template_number"]
            if errors:
                break

    # --- compare with what exists ------------------------------------------
    current = db.execute(
        select(
            Task.template_task_number,
            func.min(Task.name),
            func.count(TaskExecution.id).filter(TaskExecution.actual_start.is_not(None)),
        )
        .join(TaskExecution, TaskExecution.task_id == Task.id, isouter=True)
        .where(Task.project_id == project_id)
        .group_by(Task.template_task_number)
    ).all()
    existing = {number: (name, recorded) for number, name, recorded in current}

    removals = sorted(set(existing) - set(parsed))
    blocked = [
        f"{n} '{existing[n][0]}' ({existing[n][1]} recorded dates)"
        for n in removals
        if existing[n][1]
    ]
    if blocked and not force_remove:
        errors.append(
            "These activities are absent from the sheet but carry recorded dates: "
            + "; ".join(blocked[:10])
            + ". Re-send with force_remove=true to delete them and their history."
        )

    scope_count = db.scalar(
        select(func.count()).select_from(Scope).where(Scope.project_id == project_id)
    ) or 0

    summary = {
        "dry_run": dry_run,
        "ok": not errors,
        "activities_in_sheet": len(parsed),
        "nodes": scope_count,
        "to_create": sorted(set(parsed) - set(existing)),
        "to_update": sorted(set(parsed) & set(existing)),
        "to_remove": removals,
        "task_rows_affected": len(parsed) * scope_count,
        "errors": errors,
    }

    if errors:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, summary)
    if dry_run:
        return summary
    if scope_count == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Load the project scope before the task template — tasks are created "
            "per node.",
        )

    scopes = db.scalars(select(Scope).where(Scope.project_id == project_id)).all()
    tasks = db.scalars(select(Task).where(Task.project_id == project_id)).all()
    by_key = {(t.scope_id, t.template_task_number): t for t in tasks}

    for scope in scopes:
        for number, values in parsed.items():
            task = by_key.get((scope.id, number))
            if task is None:
                db.add(Task(project_id=project_id, scope_id=scope.id, **values))
            else:
                for key, value in values.items():
                    setattr(task, key, value)

    for number in removals:
        for task in [t for t in tasks if t.template_task_number == number]:
            db.delete(task)  # executions cascade

    db.add(
        AuditLog(
            actor_user_id=user.id, actor_username=user.username, actor_role=user.role,
            action="TEMPLATE_IMPORT", source="excel-import", project_id=project_id,
            field="task_template",
            new_value=(
                f"{len(parsed)} activities x {scope_count} nodes; "
                f"removed {len(removals)}"
            ),
        )
    )
    return summary
