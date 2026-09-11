"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""
from app.models.audit import AuditLog, LeadershipAction
from app.models.base import Base
from app.models.enums import (
    ActionPriority,
    ActionStatus,
    ConstraintGroup,
    ExecutionStatus,
    ProgrammeStatus,
    ProjectStatus,
    Role,
)
from app.models.execution import ExecutionArchive, TaskExecution
from app.models.portfolio import Programme, Project, ProjectAssignment
from app.models.reporting import Holiday, ReportingStage, SchedulingConstraint, TaskStageMap
from app.models.scope import Scope
from app.models.task import Task
from app.models.user import AppUser

__all__ = [
    "Base",
    "Programme",
    "Project",
    "ProjectAssignment",
    "Scope",
    "Task",
    "TaskExecution",
    "ExecutionArchive",
    "AppUser",
    "AuditLog",
    "LeadershipAction",
    "ReportingStage",
    "TaskStageMap",
    "Holiday",
    "SchedulingConstraint",
    "ExecutionStatus",
    "ProjectStatus",
    "ProgrammeStatus",
    "Role",
    "ActionPriority",
    "ActionStatus",
    "ConstraintGroup",
]
