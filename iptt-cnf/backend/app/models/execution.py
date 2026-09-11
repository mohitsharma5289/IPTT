from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ExecutionStatus

if TYPE_CHECKING:
    from app.models.scope import Scope
    from app.models.task import Task


class TaskExecution(Base, TimestampMixin):
    """Live execution state for one task on one node.

    Note what is *absent*: the legacy table carried `planned_start` /
    `planned_finish` as a second copy of the baseline, and half the read paths
    measured delay against it while the other half used `task.planned_finish`
    (audit C6). Per decision 2 the task row is authoritative, so the duplicate
    is gone. Superseded baselines live in `execution_archive`.
    """

    __tablename__ = "task_execution"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"), nullable=False
    )

    actual_start: Mapped[date | None] = mapped_column(Date)
    actual_finish: Mapped[date | None] = mapped_column(Date)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ExecutionStatus.NOT_STARTED, server_default="Not Started"
    )
    delay_reason: Mapped[str | None] = mapped_column(Text)

    # Working days late against task.planned_finish (decisions 1 + 2).
    # Derived — recomputed on every write; never authored directly.
    delay_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    task: Mapped["Task"] = relationship(back_populates="execution")
    scope: Mapped["Scope"] = relationship(back_populates="executions")

    __table_args__ = (
        UniqueConstraint("scope_id", "task_id", name="uq_task_execution_scope_id_task_id"),
        CheckConstraint("delay_days >= 0", name="delay_days_non_negative"),
        CheckConstraint(
            "actual_finish IS NULL OR actual_start IS NOT NULL",
            name="finish_requires_start",
        ),
        CheckConstraint(
            "actual_finish IS NULL OR actual_finish >= actual_start",
            name="finish_after_start",
        ),
        # The legacy app enforced both of the above in JavaScript only, so the
        # API accepted a finish before a start (audit M6). Now the database does.
        CheckConstraint(
            "(status <> 'Completed') OR (actual_start IS NOT NULL AND actual_finish IS NOT NULL)",
            name="completed_requires_both_dates",
        ),
        CheckConstraint(
            "(status <> 'In Progress') OR (actual_start IS NOT NULL)",
            name="in_progress_requires_start",
        ),
        CheckConstraint(
            "(status <> 'Not Started') OR (actual_start IS NULL AND actual_finish IS NULL)",
            name="not_started_has_no_dates",
        ),
        Index("ix_task_execution_project_id", "project_id"),
        Index("ix_task_execution_scope_id", "scope_id"),
        Index("ix_task_execution_task_id", "task_id"),
        Index("ix_task_execution_project_id_status", "project_id", "status"),
    )


class ExecutionArchive(Base):
    """Immutable snapshot of execution state at the moment of a re-baseline.

    Decision 6: PM actuals must survive re-baselining. The legacy flow deleted
    every execution row for the project across four unguarded transactions
    (audit C4) — actual dates, delay reasons and status were simply lost.

    Each row records both the actuals *and* the baseline they were measured
    against, so historical delay stays reconstructible after the plan moves.
    """

    __tablename__ = "execution_archive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised deliberately: the archive must outlive scope/task deletion,
    # so these are plain columns, not foreign keys.
    scope_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    circle: Mapped[str] = mapped_column(String(20), nullable=False)
    facility_name: Mapped[str] = mapped_column(String(200), nullable=False)
    template_task_number: Mapped[int] = mapped_column(Integer, nullable=False)
    task_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # The baseline in force when this snapshot was taken.
    baseline_version: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_planned_start: Mapped[date | None] = mapped_column(Date)
    baseline_planned_finish: Mapped[date | None] = mapped_column(Date)

    # The actuals being preserved.
    actual_start: Mapped[date | None] = mapped_column(Date)
    actual_finish: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    delay_reason: Mapped[str | None] = mapped_column(Text)
    delay_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    archived_by: Mapped[str | None] = mapped_column(String(150))
    reason: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index("ix_execution_archive_project_id_baseline_version", "project_id", "baseline_version"),
        Index("ix_execution_archive_scope_id", "scope_id"),
    )
