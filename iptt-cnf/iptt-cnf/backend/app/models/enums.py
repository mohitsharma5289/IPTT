"""Controlled vocabularies.

Values match the legacy strings exactly so migrated rows stay valid. The legacy
app allowed any string (and the Excel upload path could write NULL into status,
audit finding on `upload_execution`), so these are newly enforced.
"""
from __future__ import annotations

from enum import StrEnum


class ExecutionStatus(StrEnum):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"


class ProjectStatus(StrEnum):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    ON_HOLD = "On Hold"


class ProgrammeStatus(StrEnum):
    ACTIVE = "Active"
    CLOSED = "Closed"


class Role(StrEnum):
    ADMIN = "admin"
    PM = "pm"
    VIEWER = "viewer"


class ActionPriority(StrEnum):
    """Ordered low -> high. The legacy code sorted this column as text and so
    surfaced High last (audit H8); `sort_order` on the model fixes that.
    """

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


PRIORITY_SORT = {ActionPriority.HIGH: 0, ActionPriority.MEDIUM: 1, ActionPriority.LOW: 2}


class ActionStatus(StrEnum):
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    CLOSED = "Closed"


class ConstraintGroup(StrEnum):
    """Resource dimension a concurrency cap applies across."""

    GLOBAL = "global"
    CIRCLE = "circle"
    FACILITY = "facility"
