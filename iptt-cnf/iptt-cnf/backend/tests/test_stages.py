"""Stage resolution under decisions 3 (furthest position) and 4 (Completed only)."""
from __future__ import annotations

import pytest

from app.domain.stages import Stage, StageModel, governance_matrix, resolve_node_stage, summarise
from app.models.enums import ExecutionStatus

COMPLETED = ExecutionStatus.COMPLETED
IN_PROGRESS = ExecutionStatus.IN_PROGRESS
NOT_STARTED = ExecutionStatus.NOT_STARTED


@pytest.fixture
def model() -> StageModel:
    """A miniature ladder with the same shape as the real one: an early task
    carrying a high weight, which is what made the legacy max-weight resolver
    misreport (audit H6)."""
    stages = {
        "Material at Site": Stage("Material at Site", 1, 10),
        "HW Deployment Completed": Stage("HW Deployment Completed", 2, 33),
        "Security Clearance": Stage("Security Clearance", 3, 76),
        "Under Testing": Stage("Under Testing", 4, 80),
        "Live": Stage("Live", 5, 100, is_terminal=True),
    }
    by_task = {
        15: stages["Material at Site"],
        19: stages["HW Deployment Completed"],
        39: stages["Security Clearance"],
        41: stages["Under Testing"],
        50: stages["Live"],
    }
    return StageModel(stages_by_name=stages, stage_by_task_number=by_task)


def test_furthest_completed_stage_wins(model):
    node = resolve_node_stage(model, 1, [(15, COMPLETED), (19, COMPLETED), (39, NOT_STARTED)])
    assert node.stage_name == "HW Deployment Completed"
    assert node.sequence_position == 2


def test_in_progress_earns_no_stage_credit(model):
    """Decision 4. The legacy resolver accepted Completed *or* In Progress, so
    merely starting an activity promoted the node to that stage (audit H7)."""
    node = resolve_node_stage(model, 1, [(15, COMPLETED), (41, IN_PROGRESS)])
    assert node.stage_name == "Material at Site"


def test_a_node_with_nothing_completed_is_not_started(model):
    node = resolve_node_stage(model, 1, [(15, IN_PROGRESS), (19, NOT_STARTED)])
    assert node.stage_name == "Not Started"
    assert node.weight == 0


def test_completing_a_later_task_out_of_order_still_advances(model):
    """Position, not weight, decides - but a genuinely later gate does count."""
    node = resolve_node_stage(model, 1, [(15, COMPLETED), (41, COMPLETED)])
    assert node.stage_name == "Under Testing"
    assert node.sequence_position == 4


def test_security_clearance_does_not_leapfrog_testing(model):
    """The legacy resolver took max(weight). Security Clearance carried 98 while
    being template task 39 of 50, so one completed activity pinned the node at
    98% with eleven still outstanding (audit H6)."""
    node = resolve_node_stage(model, 1, [(39, COMPLETED)])
    assert node.stage_name == "Security Clearance"
    assert node.weight == 76
    assert node.weight < model.stages_by_name["Live"].weight


def test_unmapped_task_counts_as_progress_but_cannot_advance_stage(model):
    node = resolve_node_stage(model, 1, [(15, COMPLETED), (99, COMPLETED)])
    assert node.stage_name == "Material at Site"
    assert node.completed_tasks == 2


def test_summarise_returns_progress_already_as_a_percentage(model):
    """Audit H1: the dashboard script multiplied this by 100 a second time."""
    nodes = [
        resolve_node_stage(model, 1, [(50, COMPLETED)]),
        resolve_node_stage(model, 2, [(15, COMPLETED)]),
        resolve_node_stage(model, 3, [(15, NOT_STARTED)]),
    ]
    summary = summarise(nodes)
    assert summary.total_nodes == 3
    assert summary.live_nodes == 1
    assert summary.progress == pytest.approx(33.33, abs=0.01)
    assert 0 <= summary.progress <= 100
    assert summary.health == pytest.approx(round((100 + 10 + 0) / 3, 2))


def test_summarise_handles_an_empty_project(model):
    summary = summarise([])
    assert summary.total_nodes == 0
    assert summary.health == 0.0


def test_governance_matrix_totals_reconcile(model):
    """Audit H5: the legacy matrix was built by iterating a hand-written sequence
    that omitted a stage, so nodes at that stage vanished and the grand total
    silently under-counted."""
    nodes = [
        resolve_node_stage(model, 1, [(50, COMPLETED)]),
        resolve_node_stage(model, 2, [(15, COMPLETED)]),
        resolve_node_stage(model, 3, [(19, COMPLETED)]),
        resolve_node_stage(model, 4, [(15, NOT_STARTED)]),
    ]
    circles = {1: "TN", 2: "TN", 3: "KL", 4: "KL"}
    matrix = governance_matrix(model, nodes, circles)

    assert matrix["grand_total"] == len(nodes)
    total_row = matrix["rows"][-1]
    assert total_row["stage"] == "Total"
    assert sum(total_row[c] for c in matrix["circles"]) == len(nodes)


def test_governance_matrix_rows_follow_sequence_order(model):
    nodes = [
        resolve_node_stage(model, 1, [(50, COMPLETED)]),
        resolve_node_stage(model, 2, [(15, COMPLETED)]),
    ]
    matrix = governance_matrix(model, nodes, {1: "TN", 2: "KL"})
    stages = [r["stage"] for r in matrix["rows"][:-1]]
    assert stages == ["Material at Site", "Live"]
