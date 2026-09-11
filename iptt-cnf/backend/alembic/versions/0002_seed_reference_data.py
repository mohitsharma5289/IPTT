"""Seed reference data: stages, task-stage map, constraints, holidays.

Revision ID: 0002_seed
Revises: 0001_baseline
"""
from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa
from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.domain.seed_data import (  # noqa: E402
    HOLIDAYS,
    REPORTING_STAGES,
    SCHEDULING_CONSTRAINTS,
    TASK_STAGE_MAP,
)

revision: str = "0002_seed"
down_revision: str | None = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            "INSERT INTO reporting_stage (name, sequence_position, weight, is_terminal) "
            "VALUES (:name, :pos, :weight, :terminal)"
        ),
        [
            {"name": name, "pos": pos, "weight": weight, "terminal": terminal}
            for pos, name, weight, terminal in REPORTING_STAGES
        ],
    )

    stage_ids = {
        name: sid
        for sid, name in bind.execute(sa.text("SELECT id, name FROM reporting_stage")).all()
    }

    bind.execute(
        sa.text(
            "INSERT INTO task_stage_map (template_task_number, task_name, stage_id) "
            "VALUES (:num, :task_name, :stage_id)"
        ),
        [
            {"num": num, "task_name": task_name, "stage_id": stage_ids[stage_name]}
            for num, task_name, stage_name in TASK_STAGE_MAP
        ],
    )

    bind.execute(
        sa.text(
            "INSERT INTO scheduling_constraint "
            "(template_task_number, label, rule_type, partition_by, count_distinct_by, "
            " max_count, window_working_days, pool_key, is_active, notes) "
            "VALUES (:template_task_number, :label, :rule_type, :partition_by, "
            "        :count_distinct_by, :max_count, :window_working_days, :pool_key, "
            "        true, :notes)"
        ),
        [
            {
                "template_task_number": c["template_task_number"],
                "label": c["label"],
                "rule_type": c["rule_type"],
                "partition_by": c.get("partition_by", "global"),
                "count_distinct_by": c.get("count_distinct_by"),
                "max_count": c["max_count"],
                "window_working_days": c.get("window_working_days", 1),
                "pool_key": c.get("pool_key"),
                "notes": c.get("notes"),
            }
            for c in SCHEDULING_CONSTRAINTS
        ],
    )

    bind.execute(
        sa.text(
            "INSERT INTO holiday (holiday_date, circle, name) VALUES (:d, :circle, :name)"
        ),
        [{"d": d, "circle": circle, "name": name} for d, circle, name in HOLIDAYS],
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("holiday", "scheduling_constraint", "task_stage_map", "reporting_stage"):
        bind.execute(sa.text(f"DELETE FROM {table}"))
