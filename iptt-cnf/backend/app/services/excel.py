"""Excel round trip for the execution grid.

This replaces the single most dangerous piece of the legacy application.

`upload_project_execution` located the "Facility" column, then walked forward in
pairs - `columns[i]`, `columns[i+1]` - zipping them against execution rows ordered
by task id. It never compared a single column header against a task name. Insert,
remove or reorder one column in the spreadsheet and dates were written to the
wrong activities across every node in the project, silently, with no audit entry
and no authentication (audit C3, B5, H12).

Three things make that impossible here:

  * Every activity column header carries its template task number, and the
    importer matches on that number. Column *order is irrelevant* - the sheet can
    be reordered, filtered or have columns deleted and the import is still
    correct, or it refuses.
  * Unknown or unparseable headers are a hard error listing exactly what was
    wrong, rather than a silent mis-write.
  * Nothing is written until the whole file validates. The caller can ask for a
    dry run and see precisely which cells would change before committing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from typing import Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.calendar import WorkingCalendar
from app.domain.delay import compute_delay_days
from app.models import Project, Scope, Task, TaskExecution
from app.models.enums import ExecutionStatus

SHEET_EXECUTION = "Execution"
SHEET_DETAIL = "Detail"
SHEET_README = "How to use"

# Identity columns on the matrix sheet. Matched by name, case-insensitively.
COL_NODE = "Node"
COL_CIRCLE = "Circle"
COL_FACILITY = "Facility"
COL_SERVERS = "Servers"
IDENTITY_COLUMNS = (COL_NODE, COL_CIRCLE, COL_FACILITY, COL_SERVERS)

#: "13 · DMTO/SO1 Creation for HW — Start"
#: The leading integer is what binds. Everything after it is for the reader.
HEADER_PATTERN = re.compile(
    r"^\s*(\d{1,3})\s*[\u00b7.:]\s*(.*?)\s*[\u2014\u2013-]\s*(plan\s+finish|plan\s+start|start|finish)\s*$",
    re.I,
)

HEADER_FILL = PatternFill("solid", fgColor="1F2933")
IDENTITY_FILL = PatternFill("solid", fgColor="0E6D7D")
LOCKED_FILL = PatternFill("solid", fgColor="EEF1F4")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=9)
THIN = Side(style="thin", color="D3DAE1")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def activity_header(template_task_number: int, task_name: str, kind: str) -> str:
    return f"{template_task_number} · {task_name} — {kind}"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _template_catalogue(db: Session, project_id: int) -> list[tuple[int, str]]:
    """Distinct (template number, name) for the project, in template order."""
    rows = db.execute(
        select(Task.template_task_number, Task.name)
        .where(Task.project_id == project_id)
        .distinct()
        .order_by(Task.template_task_number)
    ).all()
    seen: dict[int, str] = {}
    for number, name in rows:
        seen.setdefault(number, name)
    return sorted(seen.items())


def build_workbook(db: Session, project_id: int, circle: str | None = None) -> bytes:
    """Export the execution grid.

    Two sheets carry data. `Execution` is the wide matrix PMs are used to filling
    in; `Detail` is one row per activity, which is easier to pivot and to diff.
    Planned dates appear on both but are shaded as read-only - they are the
    baseline, and the importer ignores them if edited.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

    catalogue = _template_catalogue(db, project_id)
    if not catalogue:
        raise ValueError("Project has no task template loaded")

    scope_query = select(Scope).where(Scope.project_id == project_id)
    if circle:
        scope_query = scope_query.where(Scope.circle == circle)
    scopes = db.scalars(scope_query.order_by(Scope.circle, Scope.node_id)).all()
    if not scopes:
        raise ValueError("Project has no scope defined")

    rows = db.execute(
        select(TaskExecution, Task, Scope)
        .join(Task, TaskExecution.task_id == Task.id)
        .join(Scope, TaskExecution.scope_id == Scope.id)
        .where(TaskExecution.scope_id.in_([s.id for s in scopes]))
        .order_by(Scope.circle, Scope.node_id, Task.template_task_number)
    ).all()

    by_scope: dict[int, dict[int, tuple[TaskExecution, Task]]] = {}
    for execution, task, scope in rows:
        by_scope.setdefault(scope.id, {})[task.template_task_number] = (execution, task)

    wb = Workbook()

    # --- readme ------------------------------------------------------------
    readme = wb.active
    readme.title = SHEET_README
    guidance = [
        (f"{project.name} — execution export", True),
        ("", False),
        (f"Generated {datetime.now():%Y-%m-%d %H:%M} · baseline v{project.baseline_version}", False),
        ("", False),
        ("Filling this in", True),
        ("1. Edit only the Actual Start, Actual Finish and Reason columns.", False),
        ("2. Dates may be typed as YYYY-MM-DD or entered as real Excel dates.", False),
        ("3. A finish date requires a start date, and cannot precede it.", False),
        ("4. Leave a cell blank to clear it. Status is derived from the dates.", False),
        ("", False),
        ("What you may safely change", True),
        ("Columns can be reordered, hidden or deleted, and rows can be filtered", False),
        ("or removed. Activities are matched by the number at the front of each", False),
        ("column heading, not by position, so only the rows and columns you keep", False),
        ("are considered. Do not edit the heading text itself.", False),
        ("", False),
        ("Shaded columns are read-only", True),
        ("Planned dates are the Day-0 baseline. Edits to them are ignored.", False),
    ]
    for index, (text, bold) in enumerate(guidance, start=1):
        cell = readme.cell(row=index, column=1, value=text)
        if bold:
            cell.font = Font(bold=True, size=11 if index == 1 else 10)
    readme.column_dimensions["A"].width = 82

    # --- matrix ------------------------------------------------------------
    ws = wb.create_sheet(SHEET_EXECUTION)
    header = [*IDENTITY_COLUMNS]
    for number, name in catalogue:
        header.append(activity_header(number, name, "Plan Finish"))
        header.append(activity_header(number, name, "Start"))
        header.append(activity_header(number, name, "Finish"))
    ws.append(header)

    for column_index, title in enumerate(header, start=1):
        cell = ws.cell(row=1, column=column_index)
        cell.font = HEADER_FONT
        cell.fill = IDENTITY_FILL if column_index <= len(IDENTITY_COLUMNS) else HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(column_index)].width = (
            14 if column_index <= len(IDENTITY_COLUMNS) else 20
        )
    ws.freeze_panes = "E2"
    ws.row_dimensions[1].height = 46

    for scope in scopes:
        line: list = [scope.node_id, scope.circle, scope.facility_name, scope.num_servers]
        entries = by_scope.get(scope.id, {})
        for number, _name in catalogue:
            pair = entries.get(number)
            if pair is None:
                line += [None, None, None]
                continue
            execution, task = pair
            line += [task.planned_finish, execution.actual_start, execution.actual_finish]
        ws.append(line)

    # Shade the read-only plan columns.
    for row_index in range(2, ws.max_row + 1):
        for offset in range(len(catalogue)):
            column_index = len(IDENTITY_COLUMNS) + offset * 3 + 1
            ws.cell(row=row_index, column=column_index).fill = LOCKED_FILL

    # --- detail ------------------------------------------------------------
    detail = wb.create_sheet(SHEET_DETAIL)
    detail.append(
        [
            "Node", "Circle", "Facility", "Task #", "Activity",
            "Plan Start", "Plan Finish", "Actual Start", "Actual Finish",
            "Status", "Delay (working days)", "Reason",
        ]
    )
    for cell in detail[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = BORDER
    for execution, task, scope in rows:
        detail.append(
            [
                scope.node_id, scope.circle, scope.facility_name,
                task.template_task_number, task.name,
                task.planned_start, task.planned_finish,
                execution.actual_start, execution.actual_finish,
                execution.status, execution.delay_days, execution.delay_reason,
            ]
        )
    for column_index, width in enumerate([16, 9, 26, 8, 32, 13, 13, 13, 13, 13, 10, 34], start=1):
        detail.column_dimensions[get_column_letter(column_index)].width = width
    detail.freeze_panes = "A2"

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def build_blank_template(db: Session, project_id: int) -> bytes:
    """The same workbook with no actuals filled in."""
    return build_workbook(db, project_id)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CellChange:
    node_id: str
    template_task_number: int
    task_name: str
    field: str
    old_value: str | None
    new_value: str | None


@dataclass
class ImportReport:
    dry_run: bool
    rows_read: int = 0
    nodes_matched: int = 0
    activities_considered: int = 0
    changes: list[CellChange] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "ok": self.ok,
            "rows_read": self.rows_read,
            "nodes_matched": self.nodes_matched,
            "activities_considered": self.activities_considered,
            "change_count": len(self.changes),
            "changes": [
                {
                    "node_id": c.node_id,
                    "template_task_number": c.template_task_number,
                    "task_name": c.task_name,
                    "field": c.field,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                }
                for c in self.changes[:500]
            ],
            "changes_truncated": max(0, len(self.changes) - 500),
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _coerce_date(value, label: str, errors: list[str]) -> date | None | object:
    """Returns a date, None for blank, or the sentinel `_INVALID`."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    errors.append(f"{label}: '{text}' is not a date the importer recognises")
    return _INVALID


class _Invalid:
    __slots__ = ()


_INVALID = _Invalid()


def parse_workbook(
    db: Session,
    project_id: int,
    content: bytes,
    *,
    dry_run: bool,
    actor_username: str,
    actor_user_id: int | None,
    actor_role: str | None,
) -> ImportReport:
    """Validate an uploaded workbook and, unless `dry_run`, apply it.

    Nothing is written unless the entire file is valid. The caller commits.
    """
    report = ImportReport(dry_run=dry_run)

    project = db.get(Project, project_id)
    if project is None:
        report.errors.append("Project not found")
        return report

    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception:  # noqa: BLE001 - openpyxl raises a wide range here
        report.errors.append("That file could not be opened as an .xlsx workbook")
        return report

    if SHEET_EXECUTION not in wb.sheetnames:
        report.errors.append(
            f"The workbook has no '{SHEET_EXECUTION}' sheet. "
            f"Found: {', '.join(wb.sheetnames)}"
        )
        return report

    ws = wb[SHEET_EXECUTION]
    grid = list(ws.iter_rows(values_only=True))
    if not grid:
        report.errors.append(f"The '{SHEET_EXECUTION}' sheet is empty")
        return report

    header_row = [("" if v is None else str(v).strip()) for v in grid[0]]

    # --- identity columns --------------------------------------------------
    lowered = {h.lower(): i for i, h in enumerate(header_row) if h}
    node_index = lowered.get(COL_NODE.lower())
    if node_index is None:
        report.errors.append(
            f"No '{COL_NODE}' column found. The first row must be the exported header row."
        )
        return report

    # --- activity columns, matched by template number ----------------------
    known = {number for number, _ in _template_catalogue(db, project_id)}
    start_columns: dict[int, int] = {}
    finish_columns: dict[int, int] = {}
    unknown_headers: list[str] = []

    for index, title in enumerate(header_row):
        if not title or index == node_index or title.lower() in {
            c.lower() for c in IDENTITY_COLUMNS
        }:
            continue
        match = HEADER_PATTERN.match(title)
        if not match:
            unknown_headers.append(title)
            continue
        number = int(match.group(1))
        kind = " ".join(match.group(3).lower().split())
        if number not in known:
            report.warnings.append(
                f"Column '{title}' refers to activity {number}, which is not in this "
                "project's template. Ignored."
            )
            continue
        # Plan columns are the Day-0 baseline: read-only, never collected.
        if kind.startswith("plan"):
            continue
        (start_columns if kind == "start" else finish_columns)[number] = index

    if unknown_headers:
        report.errors.append(
            "These column headings could not be understood. Each activity column "
            "must keep its leading number, for example "
            "'13 · DMTO/SO1 Creation for HW — Start'. Offending headings: "
            + "; ".join(f"'{h}'" for h in unknown_headers[:8])
            + ("…" if len(unknown_headers) > 8 else "")
        )
        return report

    if not start_columns and not finish_columns:
        report.errors.append("No editable activity columns were found in the sheet")
        return report

    # --- database side -----------------------------------------------------
    scopes = {
        s.node_id.strip().lower(): s
        for s in db.scalars(select(Scope).where(Scope.project_id == project_id)).all()
    }
    executions: dict[tuple[int, int], tuple[TaskExecution, Task]] = {}
    for execution, task in db.execute(
        select(TaskExecution, Task)
        .join(Task, TaskExecution.task_id == Task.id)
        .where(TaskExecution.project_id == project_id)
    ).all():
        executions[(execution.scope_id, task.template_task_number)] = (execution, task)

    calendar = WorkingCalendar.from_session(db)

    # --- pass one: validate ------------------------------------------------
    pending: list[tuple[TaskExecution, Task, Scope, date | None, date | None]] = []
    unmatched_nodes: list[str] = []

    for row_number, row in enumerate(grid[1:], start=2):
        if row is None or node_index >= len(row):
            continue
        raw_node = row[node_index]
        if raw_node is None or not str(raw_node).strip():
            continue
        report.rows_read += 1

        node_key = str(raw_node).strip().lower()
        scope = scopes.get(node_key)
        if scope is None:
            unmatched_nodes.append(str(raw_node).strip())
            continue
        report.nodes_matched += 1

        for number in sorted(set(start_columns) | set(finish_columns)):
            pair = executions.get((scope.id, number))
            if pair is None:
                continue
            execution, task = pair
            report.activities_considered += 1

            label = f"Row {row_number}, node {scope.node_id}, activity {number}"
            start_index = start_columns.get(number)
            finish_index = finish_columns.get(number)

            start_value = (
                _coerce_date(row[start_index], f"{label} start", report.errors)
                if start_index is not None and start_index < len(row)
                else execution.actual_start
            )
            finish_value = (
                _coerce_date(row[finish_index], f"{label} finish", report.errors)
                if finish_index is not None and finish_index < len(row)
                else execution.actual_finish
            )
            if start_value is _INVALID or finish_value is _INVALID:
                continue

            if finish_value and not start_value:
                report.errors.append(
                    f"{label}: a finish date needs a start date"
                )
                continue
            if start_value and finish_value and finish_value < start_value:
                report.errors.append(
                    f"{label}: finish {finish_value} precedes start {start_value}"
                )
                continue

            if (
                start_value == execution.actual_start
                and finish_value == execution.actual_finish
            ):
                continue

            if execution.actual_start is not None or execution.actual_finish is not None:
                if start_value is None and finish_value is None:
                    report.warnings.append(
                        f"{label}: clearing previously recorded dates"
                    )

            for field_name, old, new in (
                ("actual_start", execution.actual_start, start_value),
                ("actual_finish", execution.actual_finish, finish_value),
            ):
                if old != new:
                    report.changes.append(
                        CellChange(
                            node_id=scope.node_id,
                            template_task_number=number,
                            task_name=task.name,
                            field=field_name,
                            old_value=old.isoformat() if old else None,
                            new_value=new.isoformat() if new else None,
                        )
                    )
            pending.append((execution, task, scope, start_value, finish_value))

    if unmatched_nodes:
        unique = sorted(set(unmatched_nodes))
        report.warnings.append(
            f"{len(unique)} node id(s) in the sheet are not in this project and were "
            f"skipped: {', '.join(unique[:10])}" + ("…" if len(unique) > 10 else "")
        )

    if report.errors:
        return report
    if dry_run:
        return report

    # --- pass two: apply ---------------------------------------------------
    from app.models import AuditLog

    for execution, task, scope, start_value, finish_value in pending:
        for field_name, old, new in (
            ("actual_start", execution.actual_start, start_value),
            ("actual_finish", execution.actual_finish, finish_value),
        ):
            if old != new:
                db.add(
                    AuditLog(
                        actor_user_id=actor_user_id,
                        actor_username=actor_username,
                        actor_role=actor_role,
                        action="EXECUTION_IMPORT",
                        # The legacy Excel paths wrote no audit rows at all (H12).
                        source="excel-import",
                        project_id=project_id,
                        scope_id=scope.id,
                        task_id=task.id,
                        node_id=scope.node_id,
                        task_name=task.name,
                        field=field_name,
                        old_value=old.isoformat() if old else None,
                        new_value=new.isoformat() if new else None,
                    )
                )

        execution.actual_start = start_value
        execution.actual_finish = finish_value
        execution.status = (
            ExecutionStatus.COMPLETED
            if finish_value
            else ExecutionStatus.IN_PROGRESS
            if start_value
            else ExecutionStatus.NOT_STARTED
        )
        execution.delay_days = compute_delay_days(
            calendar, task.planned_finish, finish_value, scope.circle
        )
        if start_value and not project.baseline_locked:
            project.baseline_locked = True

    return report
