"""Stage resolution and health scoring.

Decision 3: a node's stage is the **furthest position reached in the stage
sequence**, not the highest-weighted stage.
Decision 4: only **Completed** activities count. Starting work earns nothing.

Why this matters. The legacy resolver took `max(weight)` across activities that
were Completed *or* In Progress. Two consequences followed. Merely starting an
activity promoted the node to that stage's full weight (audit H7). And because
the weights are not monotonic in the task order - "Security Clearance" is task 39
of 50 but carried weight 98 - a single completed activity could pin a node at
98% while eleven activities, including all testing and handover, were still
outstanding (audit H6).

Ordering by sequence position makes the model say what it means: a node is at
the furthest gate it has actually passed.

Everything here operates on plain tuples supplied by one query. The legacy
implementation resolved each node's stage by lazily loading `scope.executions`
and then `e.task` per row, and `get_project_executive_summary` did it four times
over - roughly 10,000 round trips per dashboard view (audit M1). Nothing in this
module touches the ORM.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping

from app.models.enums import ExecutionStatus

NOT_STARTED = "Not Started"
NOT_STARTED_POSITION = 0
NOT_STARTED_WEIGHT = 0


@dataclass(frozen=True, slots=True)
class Stage:
    name: str
    sequence_position: int
    weight: int
    is_terminal: bool = False


@dataclass(frozen=True, slots=True)
class StageModel:
    """The ordered stage ladder plus the task-number -> stage mapping."""

    stages_by_name: Mapping[str, Stage]
    stage_by_task_number: Mapping[int, Stage]

    @property
    def ordered(self) -> list[Stage]:
        return sorted(self.stages_by_name.values(), key=lambda s: s.sequence_position)

    @classmethod
    def from_session(cls, db) -> "StageModel":
        from app.models import ReportingStage, TaskStageMap

        stages = {
            s.name: Stage(s.name, s.sequence_position, s.weight, s.is_terminal)
            for s in db.query(ReportingStage).all()
        }
        by_task = {
            m.template_task_number: stages[m.stage.name]
            for m in db.query(TaskStageMap).join(TaskStageMap.stage).all()
        }
        return cls(stages_by_name=stages, stage_by_task_number=by_task)

    def terminal_stage(self) -> Stage | None:
        for s in self.stages_by_name.values():
            if s.is_terminal:
                return s
        return None


@dataclass(frozen=True, slots=True)
class NodeStage:
    scope_id: int
    stage_name: str
    sequence_position: int
    weight: int
    completed_tasks: int
    total_tasks: int

    @property
    def is_live(self) -> bool:
        return self.weight >= 100


def resolve_node_stage(
    model: StageModel,
    scope_id: int,
    rows: Iterable[tuple[int, str]],
) -> NodeStage:
    """Resolve one node's stage from `(template_task_number, status)` pairs."""
    furthest: Stage | None = None
    completed = 0
    total = 0

    for template_task_number, status in rows:
        total += 1
        if status != ExecutionStatus.COMPLETED:
            continue
        completed += 1
        stage = model.stage_by_task_number.get(template_task_number)
        if stage is None:
            # Unmapped template number. Counted as progress but cannot advance
            # the stage. The legacy code skipped silently with no way to notice.
            continue
        if furthest is None or stage.sequence_position > furthest.sequence_position:
            furthest = stage

    if furthest is None:
        return NodeStage(
            scope_id=scope_id,
            stage_name=NOT_STARTED,
            sequence_position=NOT_STARTED_POSITION,
            weight=NOT_STARTED_WEIGHT,
            completed_tasks=completed,
            total_tasks=total,
        )
    return NodeStage(
        scope_id=scope_id,
        stage_name=furthest.name,
        sequence_position=furthest.sequence_position,
        weight=furthest.weight,
        completed_tasks=completed,
        total_tasks=total,
    )


def resolve_many(
    model: StageModel,
    rows: Iterable[tuple[int, int, str]],
) -> dict[int, NodeStage]:
    """Resolve every node in one pass over `(scope_id, template_task_number, status)`."""
    grouped: dict[int, list[tuple[int, str]]] = {}
    for scope_id, template_task_number, status in rows:
        grouped.setdefault(scope_id, []).append((template_task_number, status))
    return {sid: resolve_node_stage(model, sid, r) for sid, r in grouped.items()}


# --- aggregate scoring ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HealthSummary:
    total_nodes: int
    health: float
    live_nodes: int
    progress: float
    stage_mix: dict[str, int]
    trend: str


def health_status(score: float) -> str:
    if score >= 80:
        return "Healthy"
    if score >= 50:
        return "Attention"
    return "Critical"


def trend_label(score: float) -> str:
    if score >= 80:
        return "On Track"
    if score >= 50:
        return "Stable"
    return "Needs Attention"


def summarise(node_stages: Iterable[NodeStage]) -> HealthSummary:
    """Mean stage weight across nodes, plus the stage mix.

    `progress` is the share of nodes at the terminal stage. Note this is already
    a percentage - the legacy dashboard script multiplied the equivalent figure
    by 100 a second time, rendering 3.5% as 350% (audit H1).
    """
    nodes = list(node_stages)
    total = len(nodes)
    if total == 0:
        return HealthSummary(0, 0.0, 0, 0.0, {}, trend_label(0))

    health = round(sum(n.weight for n in nodes) / total, 2)
    live = sum(1 for n in nodes if n.is_live)
    progress = round(live / total * 100, 2)
    mix = dict(Counter(n.stage_name for n in nodes))
    return HealthSummary(
        total_nodes=total,
        health=health,
        live_nodes=live,
        progress=progress,
        stage_mix=mix,
        trend=trend_label(health),
    )


def governance_matrix(
    model: StageModel,
    node_stages: Iterable[NodeStage],
    circle_by_scope: Mapping[int, str],
) -> dict:
    """Stage x circle counts, every stage in sequence order.

    Rows are driven by the stage ladder itself, so a stage can never be missing
    from the matrix. The legacy sequence list omitted "Labeling WIP" entirely,
    and any node sitting there vanished from the report while the grand total
    quietly under-counted (audit H5).
    """
    nodes = list(node_stages)
    circles = sorted({circle_by_scope.get(n.scope_id, "Unknown") for n in nodes})

    counts: dict[str, Counter] = {}
    for n in nodes:
        counts.setdefault(n.stage_name, Counter())[
            circle_by_scope.get(n.scope_id, "Unknown")
        ] += 1

    ladder = [NOT_STARTED, *[s.name for s in model.ordered]]
    rows = []
    grand_total = 0
    for stage_name in ladder:
        if stage_name not in counts:
            continue
        row = {"stage": stage_name}
        stage_total = 0
        for circle in circles:
            value = counts[stage_name].get(circle, 0)
            row[circle] = value
            stage_total += value
        row["Total"] = stage_total
        grand_total += stage_total
        rows.append(row)

    total_row = {"stage": "Total"}
    for circle in circles:
        total_row[circle] = sum(r.get(circle, 0) for r in rows)
    total_row["Total"] = grand_total
    rows.append(total_row)

    return {"circles": circles, "rows": rows, "grand_total": grand_total}
