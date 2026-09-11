"""Post-migration verification.

Compares the PostgreSQL target against the legacy SQLite source and reports
every place the numbers move, with the decision that caused the move. Run this
after `migrate_from_sqlite` and read the output before declaring the cutover
good - some figures are *supposed* to change.

    python -m etl.verify_migration --sqlite /path/iptt.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

from sqlalchemy import func, select, text

from app.db import session_scope
from app.domain.stages import StageModel, governance_matrix, resolve_many, summarise
from app.models import Project, Scope, Task, TaskExecution

TABLE_PAIRS = [
    ("programme", "programme"),
    ("project", "project"),
    ('"user"', "app_user"),
    ("project_assignment", "project_assignment"),
    ("scope", "scope"),
    ("task", "task"),
    ("task_execution", "task_execution"),
    ("leadership_actions", "leadership_action"),
    ("execution_audit_log", "audit_log"),
]


def banner(title: str) -> None:
    print(f"\n{title}")
    print("-" * 72)


def check_row_counts(src: sqlite3.Connection, db) -> bool:
    banner("Row counts")
    ok = True
    print(f"  {'table':26}{'source':>10}{'target':>10}   status")
    for legacy, target in TABLE_PAIRS:
        s = src.execute(f"SELECT count(*) FROM {legacy}").fetchone()[0]
        t = db.execute(text(f"SELECT count(*) FROM {target}")).scalar_one()
        if s == t:
            status = "match"
        elif target == "leadership_action":
            status = "expected - orphans dropped"
        else:
            status = "MISMATCH"
            ok = False
        print(f"  {target:26}{s:>10}{t:>10}   {status}")
    return ok


def check_integrity(db) -> bool:
    banner("Referential integrity (enforced for the first time)")
    checks = {
        "tasks whose predecessor is absent from their scope": """
            SELECT count(*) FROM task t WHERE t.predecessor_template_number IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM task p WHERE p.scope_id = t.scope_id
                            AND p.template_task_number = t.predecessor_template_number)""",
        "executions without a task": """
            SELECT count(*) FROM task_execution te
            WHERE NOT EXISTS (SELECT 1 FROM task t WHERE t.id = te.task_id)""",
        "tasks whose project disagrees with their scope": """
            SELECT count(*) FROM task t JOIN scope s ON s.id = t.scope_id
            WHERE t.project_id <> s.project_id""",
        "executions violating finish-requires-start": """
            SELECT count(*) FROM task_execution
            WHERE actual_finish IS NOT NULL AND actual_start IS NULL""",
        "executions violating finish-after-start": """
            SELECT count(*) FROM task_execution
            WHERE actual_finish IS NOT NULL AND actual_finish < actual_start""",
        "executions whose status disagrees with its dates": """
            SELECT count(*) FROM task_execution
            WHERE (status = 'Completed' AND actual_finish IS NULL)
               OR (status = 'In Progress' AND actual_start IS NULL)
               OR (status = 'Not Started' AND actual_start IS NOT NULL)""",
    }
    ok = True
    for label, sql in checks.items():
        n = db.execute(text(sql)).scalar_one()
        if n:
            ok = False
        print(f"  {'FAIL' if n else ' ok '}  {label:58} {n:>5}")
    return ok


def check_sequences(db) -> bool:
    banner("Identity sequences")
    ok = True
    for table in [t for _, t in TABLE_PAIRS]:
        max_id = db.execute(text(f"SELECT COALESCE(MAX(id),0) FROM {table}")).scalar_one()
        last = db.execute(
            text(
                "SELECT last_value FROM pg_sequences "
                "WHERE schemaname || '.' || sequencename = "
                "      pg_get_serial_sequence(:t, 'id')"
            ),
            {"t": table},
        ).scalar_one()
        good = last is not None and last >= max_id
        ok = ok and good
        print(f"  {' ok ' if good else 'FAIL'}  {table:26} max id {max_id:>6}   sequence {last:>6}")
    return ok


def compare_delays(src: sqlite3.Connection, db) -> None:
    banner("Delay recomputation: calendar days -> working days (decisions 1 + 2)")
    legacy_total = src.execute("SELECT COALESCE(SUM(delay_days),0) FROM task_execution").fetchone()[0]
    legacy_rows = src.execute("SELECT count(*) FROM task_execution WHERE delay_days > 0").fetchone()[0]
    new_total = db.execute(text("SELECT COALESCE(SUM(delay_days),0) FROM task_execution")).scalar_one()
    new_rows = db.execute(text("SELECT count(*) FROM task_execution WHERE delay_days > 0")).scalar_one()

    print(f"  {'':32}{'legacy':>10}{'new':>10}{'delta':>10}")
    print(f"  {'total delay days':32}{legacy_total:>10}{new_total:>10}{new_total-legacy_total:>+10}")
    print(f"  {'rows recording a delay':32}{legacy_rows:>10}{new_rows:>10}{new_rows-legacy_rows:>+10}")
    at_risk_legacy = src.execute(
        "SELECT count(DISTINCT scope_id) FROM task_execution WHERE delay_days > 7"
    ).fetchone()[0]
    at_risk_new = db.execute(
        text("SELECT count(DISTINCT scope_id) FROM task_execution WHERE delay_days > 7")
    ).scalar_one()
    print(f"  {'nodes at risk (>7 days late)':32}{at_risk_legacy:>10}{at_risk_new:>10}"
          f"{at_risk_new-at_risk_legacy:>+10}")
    print("\n  Calendar days count weekends and holidays as slip; working days do not,")
    print("  so the new figures are lower. This is the intended effect of decision 1.")


def compare_reporting(db, project_id: int) -> None:
    banner(f"Reporting model, project {project_id} (decisions 3 + 4)")
    model = StageModel.from_session(db)
    rows = db.execute(
        select(Scope.id, Task.template_task_number, TaskExecution.status)
        .join(TaskExecution, TaskExecution.scope_id == Scope.id)
        .join(Task, Task.id == TaskExecution.task_id)
        .where(Scope.project_id == project_id)
    ).all()
    node_stages = resolve_many(model, rows)
    summary = summarise(node_stages.values())

    print(f"  nodes                {summary.total_nodes}")
    print(f"  health (mean weight) {summary.health}")
    print(f"  live nodes           {summary.live_nodes}")
    print(f"  progress             {summary.progress}%")
    print(f"  trend                {summary.trend}")
    print("\n  Stage mix")
    for name, count in sorted(
        summary.stage_mix.items(),
        key=lambda kv: model.stages_by_name[kv[0]].sequence_position
        if kv[0] in model.stages_by_name
        else 0,
    ):
        weight = model.stages_by_name[name].weight if name in model.stages_by_name else 0
        print(f"    {name:44} {count:>4}   (weight {weight})")

    circles = {sid: c for sid, c in db.execute(select(Scope.id, Scope.circle)).all()}
    matrix = governance_matrix(model, node_stages.values(), circles)
    total_row = matrix["rows"][-1]
    reconciles = total_row["Total"] == summary.total_nodes
    print(f"\n  Governance matrix grand total {matrix['grand_total']} vs "
          f"{summary.total_nodes} nodes: {'reconciles' if reconciles else 'MISMATCH'}")
    print("  (the legacy matrix silently dropped any node at a stage missing from")
    print("   its hand-written sequence - audit H5)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--project-id", type=int, default=12)
    args = parser.parse_args()

    src = sqlite3.connect(args.sqlite)
    ok = True
    with session_scope() as db:
        ok &= check_row_counts(src, db)
        ok &= check_integrity(db)
        ok &= check_sequences(db)
        compare_delays(src, db)
        compare_reporting(db, args.project_id)
    src.close()

    banner("Result")
    print("  All structural checks passed." if ok else "  STRUCTURAL CHECKS FAILED - see above.")
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
