from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ProgrammeStatus, ProjectStatus

if TYPE_CHECKING:
    from app.models.scope import Scope
    from app.models.task import Task
    from app.models.user import AppUser


class Programme(Base, TimestampMixin):
    __tablename__ = "programme"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ProgrammeStatus.ACTIVE, server_default="Active"
    )

    projects: Mapped[list["Project"]] = relationship(
        back_populates="programme", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (UniqueConstraint("name", name="uq_programme_name"),)


class Project(Base, TimestampMixin):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    programme_id: Mapped[int] = mapped_column(
        ForeignKey("programme.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ProjectStatus.NOT_STARTED, server_default="Not Started"
    )

    # Kickoff for the planner. The legacy CPM engine ignored this in favour of a
    # hardcoded date(2026, 5, 1) (audit C2); nothing may hardcode a start date now.
    project_start_date: Mapped[date | None] = mapped_column(Date)

    # Flips true on the first actual_start recorded anywhere in the project.
    baseline_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Incremented by each re-baseline. Execution rows are archived under the
    # version they were captured against (decision 6).
    baseline_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    programme: Mapped[Programme] = relationship(back_populates="projects")
    scopes: Mapped[list["Scope"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    assignments: Mapped[list["ProjectAssignment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("programme_id", "name", name="uq_project_programme_id_name"),
        Index("ix_project_programme_id", "programme_id"),
    )


class ProjectAssignment(Base, TimestampMixin):
    """Grants a PM write access to one project. The legacy table carried no
    foreign keys at all (audit B6/M19)."""

    __tablename__ = "project_assignment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="assignments")
    user: Mapped["AppUser"] = relationship(back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_assignment_project_id_user_id"),
        Index("ix_project_assignment_user_id", "user_id"),
    )
