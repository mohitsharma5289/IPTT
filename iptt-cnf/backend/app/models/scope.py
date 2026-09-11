from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ExecutionStatus

if TYPE_CHECKING:
    from app.models.execution import TaskExecution
    from app.models.portfolio import Project
    from app.models.task import Task


class Scope(Base, TimestampMixin):
    """One deployable network node within a project."""

    __tablename__ = "scope"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )

    # Sequencing hint carried over from the legacy schema. Per decision 7 the
    # planner deliberately ignores it; it is retained for display and sorting.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    circle: Mapped[str] = mapped_column(String(20), nullable=False)
    facility_name: Mapped[str] = mapped_column(String(200), nullable=False)
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    num_servers: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ExecutionStatus.NOT_STARTED, server_default="Not Started"
    )

    project: Mapped["Project"] = relationship(back_populates="scopes")
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="scope", cascade="all, delete-orphan", passive_deletes=True
    )
    executions: Mapped[list["TaskExecution"]] = relationship(
        back_populates="scope", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("project_id", "node_id", name="uq_scope_project_id_node_id"),
        CheckConstraint("num_servers >= 0", name="num_servers_non_negative"),
        Index("ix_scope_project_id", "project_id"),
        Index("ix_scope_circle", "circle"),
        # Circle dashboards filter on both together.
        Index("ix_scope_project_id_circle", "project_id", "circle"),
    )
