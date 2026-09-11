from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.execution import TaskExecution
    from app.models.portfolio import Project
    from app.models.scope import Scope


class Task(Base, TimestampMixin):
    """One checklist activity for one node.

    Tasks are materialised per (scope x template task): 111 scopes x 50 template
    tasks = 5,550 rows in the legacy dataset. Because a task row is already
    node-specific, `planned_start` / `planned_finish` on this row are the
    authoritative Day-0 baseline for that node's activity (decision 2).
    """

    __tablename__ = "task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope.id", ondelete="CASCADE"), nullable=False
    )

    # Position in the 50-step deployment template. Stable across re-uploads and
    # the correct join key for Excel round trips (audit M18).
    template_task_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # --- THE B1 FIX --------------------------------------------------------
    # The legacy column was `predecessor_task_id Integer FK -> task.id`, but the
    # Excel importer wrote `Predecessor_Task_Number` (a value in 1..50) into it.
    # All 4,551 populated values violated that foreign key; SQLite never checked.
    # The column is renamed to state what it actually holds and the false FK is
    # gone. Dependencies are resolved within a scope, by template number.
    predecessor_template_number: Mapped[int | None] = mapped_column(Integer)

    is_prerequisite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    duration_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    assigned_to: Mapped[str | None] = mapped_column(String(200))
    owner_role: Mapped[str | None] = mapped_column(String(100))

    # Authoritative baseline (decision 2). Rewritten only by a re-baseline,
    # which archives the superseded execution rows first (decision 6).
    planned_start: Mapped[date | None] = mapped_column(Date)
    planned_finish: Mapped[date | None] = mapped_column(Date)

    execution: Mapped["TaskExecution | None"] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
    project: Mapped["Project"] = relationship(back_populates="tasks")
    scope: Mapped["Scope"] = relationship(back_populates="tasks")

    __table_args__ = (
        UniqueConstraint(
            "scope_id", "template_task_number", name="uq_task_scope_id_template_task_number"
        ),
        CheckConstraint("duration_days >= 0", name="duration_non_negative"),
        CheckConstraint(
            "planned_finish IS NULL OR planned_start IS NULL OR planned_finish >= planned_start",
            name="planned_finish_after_start",
        ),
        Index("ix_task_project_id", "project_id"),
        Index("ix_task_scope_id", "scope_id"),
        Index("ix_task_project_id_template_task_number", "project_id", "template_task_number"),
    )
