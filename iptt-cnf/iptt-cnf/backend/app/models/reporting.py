from __future__ import annotations

from datetime import date

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
from app.models.enums import ConstraintGroup


class ReportingStage(Base, TimestampMixin):
    """Governance stages, ordered.

    Was two Python dicts plus a hand-written list that disagreed with each other:
    `STAGE_SEQUENCE` omitted "Labeling WIP" entirely, so any node at that stage
    vanished from the governance matrix (audit H5). Moving this into the database
    means the ordering and the weights can be corrected by an admin without a
    redeploy, and a stage can never be missing from the sequence.

    `sequence_position` is what resolves a node's stage (decision 3: furthest
    position reached, not highest weight). `weight` drives the health score.
    """

    __tablename__ = "reporting_stage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    sequence_position: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    is_terminal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    task_mappings: Mapped[list["TaskStageMap"]] = relationship(
        back_populates="stage", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_reporting_stage_name"),
        UniqueConstraint("sequence_position", name="uq_reporting_stage_sequence_position"),
        CheckConstraint("weight BETWEEN 0 AND 100", name="weight_is_percentage"),
    )


class TaskStageMap(Base, TimestampMixin):
    """Maps a template task to the stage its completion signifies.

    The legacy map was keyed on the task's *display name* and matched literally,
    silently skipping anything it did not recognise. It only achieved full
    coverage because the keys reproduced the source typos exactly — "IRM
    disptatched", "Lebeling", "Cabaling", "TOR  HW I & C" with a double space
    (audit M17). Correcting a spelling anywhere removed that task from reporting
    with no error. Keying on `template_task_number` removes that fragility.
    """

    __tablename__ = "task_stage_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_task_number: Mapped[int] = mapped_column(Integer, nullable=False)
    task_name: Mapped[str] = mapped_column(String(200), nullable=False)
    stage_id: Mapped[int] = mapped_column(
        ForeignKey("reporting_stage.id", ondelete="CASCADE"), nullable=False
    )

    stage: Mapped[ReportingStage] = relationship(back_populates="task_mappings")

    __table_args__ = (
        UniqueConstraint("template_task_number", name="uq_task_stage_map_template_task_number"),
        Index("ix_task_stage_map_stage_id", "stage_id"),
    )


class Holiday(Base, TimestampMixin):
    """Non-working days, per circle.

    Replaces a hardcoded set of eight Maharashtra 2026 dates that was applied to
    all 20 circles and was empty for every date outside 2026 (audit M16).
    A NULL circle means the date is a national holiday applying everywhere.
    """

    __tablename__ = "holiday"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    circle: Mapped[str | None] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        UniqueConstraint("holiday_date", "circle", name="uq_holiday_holiday_date_circle"),
        Index("ix_holiday_holiday_date", "holiday_date"),
    )


class SchedulingConstraint(Base, TimestampMixin):
    """One resource rule. Decision 8: the two legacy definitions merged here.

    The legacy code carried two contradictory sets:
      * `planner.py` TASK_CAPACITY_RULES - per-day start caps for tasks
        13/16/20/40, plus three hardcoded `if template_id == ...` branches for
        tasks 25, 32/33 and 41/42.
      * `constraints.py` TASK_CONSTRAINTS - a different set, never imported by
        anything, disagreeing with the above on tasks 32 and 41.

    A task can carry more than one rule (on-site installation is capped both
    globally and per circle), so this is one row per rule, not per task.

      rule_type            starts_per_day | concurrent | distinct_in_window
      partition_by         the bucket the cap applies within (global/circle/facility)
      count_distinct_by    what to count - NULL counts occupancies, "facility"
                           counts distinct facilities
      window_working_days  >1 turns a per-day cap into a rolling window
      pool_key             rows sharing a key are counted against one pool, so
                           tasks 32 and 33 compete for the same installation crew
    """

    __tablename__ = "scheduling_constraint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_task_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)

    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)
    partition_by: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ConstraintGroup.GLOBAL, server_default="global"
    )
    count_distinct_by: Mapped[str | None] = mapped_column(String(20))
    max_count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_working_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    pool_key: Mapped[str | None] = mapped_column(String(60))

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    notes: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint(
            "template_task_number",
            "rule_type",
            "partition_by",
            name="uq_scheduling_constraint_task_rule_partition",
        ),
        CheckConstraint("window_working_days >= 1", name="window_at_least_one_day"),
        CheckConstraint("max_count >= 1", name="max_count_positive"),
        CheckConstraint(
            "rule_type IN ('starts_per_day','concurrent','distinct_in_window')",
            name="rule_type_known",
        ),
        Index("ix_scheduling_constraint_template_task_number", "template_task_number"),
    )
