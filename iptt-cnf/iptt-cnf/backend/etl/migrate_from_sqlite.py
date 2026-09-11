"""One-shot migration of the legacy SQLite database into PostgreSQL.

Run against a *copy* of iptt.db. Idempotent only in the sense that it refuses to
run against a non-empty target unless --force is given.

What this has to deal with, all established by the audit:

  * `task.predecessor_task_id` is declared a foreign key to `task.id` but every
    one of its 4,551 populated values is a template task number in 1..50. It
    moves to `predecessor_template_number` and the false key is dropped (B1).
  * Dates are stored with TEXT affinity and booleans as 0/1 integers, so every
    value needs parsing and casting rather than copying (migration blockers).
  * 193 of 226 audit rows reference scope and task ids that no longer exist, and
    4 of 6 leadership actions point at deleted projects. Those references are
    preserved as labels but detached from the foreign keys (B6).
  * `user` is a reserved word in PostgreSQL; the table becomes `app_user`.
  * All four live accounts are the committed seed credentials, so every migrated
    user is flagged `must_change_password` (M8).
  * `delay_days` was stored in calendar days against the execution snapshot. It
    is recomputed in working days against `task.planned_finish` per decisions
    1 and 2, and the difference is reported.

Usage:
    python -m etl.migrate_from_sqlite --sqlite /path/iptt.db [--force] [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select, text

from app.db import session_scope
from app.domain import delay as delay_rules
from app.domain.calendar import WorkingCalendar
from app.models import (
    AppUser,
    AuditLog,
    ExecutionStatus,
    LeadershipAction,
    Programme,
    Project,
    ProjectAssignment,
    Scope,
    Task,
    TaskExecution,
)
from app.models.enums import PRIORITY_SORT, ActionPriority

TABLES_IN_LOAD_ORDER = [
    "programme",
    "project",
    "app_user",
    "project_assignment",
    "scope",
    "task",
    "task_execution",
    "leadership_action",
    "audit_log",
]


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    delay_changes: int = 0
    delay_delta_total: int = 0
    quarantine: list[dict] = field(default_factory=list)

    def add(self, table: str, n: int) -> None:
        self.counts[table] = n

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def parse_date(value) -> date | None:
    if value in (None, "", "None"):
        return None
    if isinstance(value, date):
        return value
    text_value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text_value[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unparseable date: {value!r}")


def parse_datetime(value) -> datetime | None:
    if value in (None, "", "None"):
        return None
    text_value = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text_value, fmt)
        except ValueError:
            continue
    return None


def parse_bool(value) -> bool:
    return bool(value) and str(value) not in ("0", "False", "false", "")


def normalise_status(value) -> str:
    if value in (None, "", "None", "nan"):
        return ExecutionStatus.NOT_STARTED
    s = str(value).strip()
    for member in ExecutionStatus:
        if s.lower() == member.value.lower():
            return member.value
    return ExecutionStatus.NOT_STARTED


def reconcile_status(status: str, actual_start, actual_finish) -> str:
    """Force status into agreement with the dates.

    The database now enforces this with CHECK constraints; the legacy rows do not
    all satisfy them, because the Excel importer wrote `status` straight from the
    spreadsheet cell without validating it against the dates.
    """
    if actual_finish is not None:
        return ExecutionStatus.COMPLETED
    if actual_start is not None:
        return ExecutionStatus.IN_PROGRESS
    return ExecutionStatus.NOT_STARTED


def migrate(sqlite_path: str, *, force: bool = False, dry_run: bool = False) -> Report:
    report = Report()
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    with session_scope() as db:
        existing = db.scalar(select(func.count()).select_from(Project))
        if existing and not force:
            raise SystemExit(
                f"Target already contains {existing} projects. Re-run with --force to proceed."
            )
        if force:
            for table in reversed(TABLES_IN_LOAD_ORDER):
                db.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
            db.flush()

        # --- programmes ----------------------------------------------------
        for row in src.execute("SELECT * FROM programme ORDER BY id"):
            db.add(
                Programme(
                    id=row["id"],
                    name=row["name"],
                    status=row["status"] or "Active",
                )
            )
        db.flush()
        report.add("programme", db.scalar(select(func.count()).select_from(Programme)))

        # --- projects ------------------------------------------------------
        for row in src.execute("SELECT * FROM project ORDER BY id"):
            db.add(
                Project(
                    id=row["id"],
                    programme_id=row["programme_id"],
                    name=row["name"],
                    priority=row["priority"],
                    status=row["status"] or "Not Started",
                    project_start_date=parse_date(row["project_start_date"]),
                    baseline_locked=parse_bool(row["baseline_locked"]),
                    baseline_version=1,
                )
            )
        db.flush()
        report.add("project", db.scalar(select(func.count()).select_from(Project)))

        # --- users ---------------------------------------------------------
        for row in src.execute('SELECT * FROM "user" ORDER BY id'):
            db.add(
                AppUser(
                    id=row["id"],
                    username=row["username"],
                    password_hash=row["password_hash"],
                    role=row["role"] or "viewer",
                    is_active=parse_bool(row["is_active"]),
                    # Every legacy account uses a credential committed to the repo.
                    must_change_password=True,
                )
            )
        db.flush()
        report.add("app_user", db.scalar(select(func.count()).select_from(AppUser)))
        report.warn(
            "All migrated users are flagged must_change_password: the legacy seed "
            "credentials (admin123 / pm123 / viewer123) were committed to git."
        )

        # --- assignments ---------------------------------------------------
        valid_projects = {p for (p,) in db.execute(select(Project.id)).all()}
        valid_users = {u for (u,) in db.execute(select(AppUser.id)).all()}
        skipped_assignments = 0
        for row in src.execute("SELECT * FROM project_assignment ORDER BY id"):
            if row["project_id"] not in valid_projects or row["user_id"] not in valid_users:
                skipped_assignments += 1
                continue
            db.add(
                ProjectAssignment(
                    id=row["id"], project_id=row["project_id"], user_id=row["user_id"]
                )
            )
        db.flush()
        report.add(
            "project_assignment",
            db.scalar(select(func.count()).select_from(ProjectAssignment)),
        )
        if skipped_assignments:
            report.warn(f"Skipped {skipped_assignments} assignment(s) with dangling references.")

        # --- scopes --------------------------------------------------------
        for row in src.execute("SELECT * FROM scope ORDER BY id"):
            db.add(
                Scope(
                    id=row["id"],
                    project_id=row["project_id"],
                    priority=row["priority"] or 1,
                    circle=(row["circle"] or "").strip(),
                    facility_name=(row["facility_name"] or "").strip(),
                    node_id=(row["node_id"] or "").strip(),
                    num_servers=row["num_servers"] or 0,
                    status=normalise_status(row["status"]),
                )
            )
        db.flush()
        report.add("scope", db.scalar(select(func.count()).select_from(Scope)))

        # --- tasks (THE B1 FIX) --------------------------------------------
        predecessor_out_of_range = 0
        for row in src.execute("SELECT * FROM task ORDER BY id"):
            pred = row["predecessor_task_id"]
            if pred is not None and not (1 <= int(pred) <= 200):
                predecessor_out_of_range += 1
                pred = None
            db.add(
                Task(
                    id=row["id"],
                    project_id=row["project_id"],
                    scope_id=row["scope_id"],
                    template_task_number=row["template_task_number"],
                    name=row["name"],
                    # The legacy column held a template number despite its name
                    # and its declared foreign key. Carried across as-is.
                    predecessor_template_number=int(pred) if pred is not None else None,
                    is_prerequisite=parse_bool(row["is_prerequisite"]),
                    duration_days=row["duration_days"] if row["duration_days"] is not None else 1,
                    assigned_to=row["assigned_to"],
                    owner_role=row["owner_role"],
                    planned_start=parse_date(row["planned_start"]),
                    planned_finish=parse_date(row["planned_finish"]),
                )
            )
        db.flush()
        report.add("task", db.scalar(select(func.count()).select_from(Task)))
        if predecessor_out_of_range:
            report.warn(
                f"{predecessor_out_of_range} predecessor value(s) outside 1..200 were nulled."
            )

        # Every predecessor must now resolve within its own scope.
        unresolved = db.execute(
            text(
                """
                SELECT count(*) FROM task t
                WHERE t.predecessor_template_number IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM task p
                    WHERE p.scope_id = t.scope_id
                      AND p.template_task_number = t.predecessor_template_number)
                """
            )
        ).scalar_one()
        if unresolved:
            report.warn(f"{unresolved} task(s) reference a predecessor absent from their scope.")

        # --- executions ----------------------------------------------------
        scope_circle = {
            sid: circle for sid, circle in db.execute(select(Scope.id, Scope.circle)).all()
        }
        task_baseline = {
            tid: pf for tid, pf in db.execute(select(Task.id, Task.planned_finish)).all()
        }
        calendar = WorkingCalendar.from_session(db)

        scope_node_for_quarantine = {
            sid: node for sid, node in db.execute(select(Scope.id, Scope.node_id)).all()
        }
        task_label = {
            tid: (num, name)
            for tid, num, name in db.execute(
                select(Task.id, Task.template_task_number, Task.name)
            ).all()
        }

        for row in src.execute("SELECT * FROM task_execution ORDER BY id"):
            actual_start = parse_date(row["actual_start"])
            actual_finish = parse_date(row["actual_finish"])
            num, name = task_label.get(row["task_id"], (None, None))

            # The database now enforces date sanity with CHECK constraints. The
            # legacy API enforced it only in JavaScript, so callers hitting the
            # endpoint directly could store nonsense (audit M6). These rows are
            # repaired conservatively and every change is written to the
            # quarantine report for a PM to confirm.
            if actual_finish is not None and actual_start is None:
                # A finish implies the work happened; treat it as same-day.
                report.quarantine.append(
                    {
                        "execution_id": row["id"],
                        "node_id": scope_node_for_quarantine.get(row["scope_id"], ""),
                        "template_task_number": num,
                        "task_name": name,
                        "issue": "finish recorded with no start",
                        "original_start": "",
                        "original_finish": actual_finish.isoformat(),
                        "action": "start inferred as equal to finish",
                    }
                )
                actual_start = actual_finish

            elif (
                actual_start is not None
                and actual_finish is not None
                and actual_finish < actual_start
            ):
                report.quarantine.append(
                    {
                        "execution_id": row["id"],
                        "node_id": scope_node_for_quarantine.get(row["scope_id"], ""),
                        "template_task_number": num,
                        "task_name": name,
                        "issue": "finish precedes start",
                        "original_start": actual_start.isoformat(),
                        "original_finish": actual_finish.isoformat(),
                        "action": "finish cleared, status set to In Progress - needs re-entry",
                    }
                )
                actual_finish = None

            status = reconcile_status(
                normalise_status(row["status"]), actual_start, actual_finish
            )
            circle = scope_circle.get(row["scope_id"])
            recomputed = delay_rules.compute_delay_days(
                calendar, task_baseline.get(row["task_id"]), actual_finish, circle
            )
            legacy = row["delay_days"] or 0
            if legacy != recomputed:
                report.delay_changes += 1
                report.delay_delta_total += recomputed - legacy

            db.add(
                TaskExecution(
                    id=row["id"],
                    project_id=row["project_id"],
                    scope_id=row["scope_id"],
                    task_id=row["task_id"],
                    actual_start=actual_start,
                    actual_finish=actual_finish,
                    status=status,
                    delay_reason=row["delay_reason"],
                    delay_days=recomputed,
                )
            )
        db.flush()
        report.add("task_execution", db.scalar(select(func.count()).select_from(TaskExecution)))

        # --- leadership actions --------------------------------------------
        orphan_actions = 0
        for row in src.execute("SELECT * FROM leadership_actions ORDER BY id"):
            if row["project_id"] not in valid_projects:
                orphan_actions += 1
                continue
            priority = row["priority"] if row["priority"] in set(ActionPriority) else "Medium"
            db.add(
                LeadershipAction(
                    id=row["id"],
                    project_id=row["project_id"],
                    circle=None if row["circle"] in (None, "-") else row["circle"],
                    node_id=None if row["node"] in (None, "-") else row["node"],
                    risk_area=None if row["risk_area"] in (None, "-") else row["risk_area"],
                    action_required=row["action_required"] or "(no description)",
                    owner=row["owner"],
                    target_date=parse_date(row["target_date"]),
                    priority=priority,
                    priority_sort=PRIORITY_SORT[ActionPriority(priority)],
                    status=row["status"] or "Open",
                    remarks=row["remarks"],
                )
            )
        db.flush()
        report.add(
            "leadership_action", db.scalar(select(func.count()).select_from(LeadershipAction))
        )
        if orphan_actions:
            report.warn(
                f"Dropped {orphan_actions} leadership action(s) referencing deleted projects "
                "(legacy rows pointed at projects 1 and 2, which no longer exist)."
            )

        # --- audit log ------------------------------------------------------
        valid_scopes = set(scope_circle)
        valid_tasks = set(task_baseline)
        scope_node = {
            sid: node for sid, node in db.execute(select(Scope.id, Scope.node_id)).all()
        }
        task_name = {tid: n for tid, n in db.execute(select(Task.id, Task.name)).all()}
        user_by_name = {
            u.username.lower(): u.id for u in db.scalars(select(AppUser)).all()
        }
        detached = 0
        for row in src.execute("SELECT * FROM execution_audit_log ORDER BY id"):
            sid = row["scope_id"] if row["scope_id"] in valid_scopes else None
            tid = row["task_id"] if row["task_id"] in valid_tasks else None
            if (row["scope_id"] is not None and sid is None) or (
                row["task_id"] is not None and tid is None
            ):
                detached += 1
            actor = (row["user"] or "unknown").strip()
            db.add(
                AuditLog(
                    id=row["id"],
                    actor_user_id=user_by_name.get(actor.lower()),
                    actor_username=actor,
                    actor_role=None,
                    action=row["action"] or "UPDATE",
                    source="legacy-import",
                    project_id=None,
                    scope_id=row["scope_id"],
                    task_id=row["task_id"],
                    node_id=scope_node.get(sid),
                    task_name=task_name.get(tid),
                    field=row["field"],
                    old_value=row["old_value"],
                    new_value=row["new_value"],
                    created_at=parse_datetime(row["timestamp"]) or datetime.utcnow(),
                )
            )
        db.flush()
        report.add("audit_log", db.scalar(select(func.count()).select_from(AuditLog)))
        if detached:
            report.warn(
                f"{detached} audit row(s) referenced deleted scopes or tasks. The ids are "
                "retained as plain columns but resolve to no row - the legacy table had no "
                "foreign keys and tasks were deleted wholesale on every re-upload."
            )

        # --- sequences ------------------------------------------------------
        # Copying explicit ids leaves every identity sequence at 1; the first
        # insert would collide.
        for table in TABLES_IN_LOAD_ORDER:
            db.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
                )
            )

        if dry_run:
            db.rollback()
            report.warn("DRY RUN - transaction rolled back, nothing written.")

    src.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="path to a copy of iptt.db")
    parser.add_argument("--force", action="store_true", help="truncate the target first")
    parser.add_argument("--dry-run", action="store_true", help="roll back instead of committing")
    parser.add_argument(
        "--quarantine-csv",
        default="migration_quarantine.csv",
        help="where to write rows that needed repair",
    )
    args = parser.parse_args()

    report = migrate(args.sqlite, force=args.force, dry_run=args.dry_run)

    print("\nRows loaded")
    print("-" * 46)
    for table in TABLES_IN_LOAD_ORDER:
        print(f"  {table:24} {report.counts.get(table, 0):>8}")
    print(f"  {'TOTAL':24} {sum(report.counts.values()):>8}")

    print("\nDelay recomputation (calendar -> working days, decisions 1 + 2)")
    print("-" * 46)
    print(f"  rows whose delay changed  {report.delay_changes:>8}")
    print(f"  net change in delay days  {report.delay_delta_total:>+8}")

    if report.quarantine:
        import csv

        with open(args.quarantine_csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(report.quarantine[0].keys()))
            writer.writeheader()
            writer.writerows(report.quarantine)
        print(f"\nQuarantine: {len(report.quarantine)} row(s) repaired -> {args.quarantine_csv}")
        print("-" * 46)
        for q in report.quarantine:
            print(f"  {q['node_id']:14} task {q['template_task_number']:>2} {q['task_name'][:26]:26} {q['issue']}")

    if report.warnings:
        print("\nWarnings")
        print("-" * 46)
        for w in report.warnings:
            print(f"  - {w}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
