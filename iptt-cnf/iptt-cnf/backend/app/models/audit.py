from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import ActionPriority, ActionStatus


class AuditLog(Base):
    """Append-only change log.

    Three changes from the legacy table. It now records the acting user's id and
    username (the override endpoint wrote the caller-supplied *role* string, and
    required no authentication at all, so entries could be forged and identified
    nobody - audit M7). It carries denormalised node and task labels so entries
    stay readable after the referenced rows are deleted - 193 of 226 legacy rows
    pointed at scope and task ids that no longer existed (audit B6). And the
    Excel upload paths write here too, which they never used to (audit H12).
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    actor_username: Mapped[str] = mapped_column(String(150), nullable=False)
    actor_role: Mapped[str | None] = mapped_column(String(20))

    action: Mapped[str] = mapped_column(String(60), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="api")

    project_id: Mapped[int | None] = mapped_column(Integer)
    scope_id: Mapped[int | None] = mapped_column(Integer)
    task_id: Mapped[int | None] = mapped_column(Integer)
    node_id: Mapped[str | None] = mapped_column(String(100))
    task_name: Mapped[str | None] = mapped_column(String(200))

    field: Mapped[str | None] = mapped_column(String(80))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)

    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # The legacy viewer applied a bare LIMIT 200 with no pagination, so older
        # entries were unreachable (audit M5). These support keyset pagination.
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_project_id_created_at", "project_id", "created_at"),
        Index("ix_audit_log_scope_id", "scope_id"),
    )


class LeadershipAction(Base, TimestampMixin):
    """Escalation tracked against a project."""

    __tablename__ = "leadership_action"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )

    # The legacy create path hardcoded these three to "-" and discarded the
    # caller's value, leaving the columns permanently unused (audit H13).
    circle: Mapped[str | None] = mapped_column(String(20))
    node_id: Mapped[str | None] = mapped_column(String(100))
    risk_area: Mapped[str | None] = mapped_column(String(120))

    action_required: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(150))
    target_date: Mapped[date | None] = mapped_column(Date)

    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ActionPriority.MEDIUM, server_default="Medium"
    )
    # Explicit ordering column: sorting the text `priority` descending put High
    # last, below Medium and Low (audit H8).
    priority_sort: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ActionStatus.OPEN, server_default="Open"
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_leadership_action_project_id", "project_id"),
        Index("ix_leadership_action_project_id_priority_sort", "project_id", "priority_sort"),
    )
