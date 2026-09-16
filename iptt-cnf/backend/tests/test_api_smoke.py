"""End-to-end smoke tests against a project this suite seeds itself.

Skipped automatically unless IPTT_TEST_DB_READY=1, so the unit suite stays
runnable without a database. See conftest for what `seeded` contains - the
assertions here derive their numbers from it rather than hardcoding the shape
of the POC extract.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

def test_liveness_does_not_touch_the_database(anon: TestClient):
    assert anon.get("/healthz").json() == {"status": "ok"}


def test_readiness_reports_the_applied_schema_revision(anon: TestClient):
    """Asserts against the Alembic head rather than a literal, so adding a
    migration does not break the test."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    body = anon.get("/readyz").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["schema_revision"] == head


def test_every_data_route_rejects_an_anonymous_caller(anon: TestClient, seeded):
    """Audit B5: 31 of 74 legacy routes had no session check."""
    for method, path in [
        ("get", "/api/projects"),
        ("get", f"/api/reporting/projects/{seeded.project_id}/kpis"),
        ("get", f"/api/reporting/projects/{seeded.project_id}/nodes"),
        ("get", f"/api/execution/projects/{seeded.project_id}/grid"),
        ("get", "/api/reporting/stages"),
    ]:
        response = getattr(anon, method)(path)
        assert response.status_code == 401, f"{method} {path} returned {response.status_code}"


def test_writes_reject_an_anonymous_caller(anon: TestClient):
    response = anon.put(
        "/api/execution/bulk-update",
        json={"updates": [{"scope_id": 1, "task_id": 1, "status": "Not Started"}]},
    )
    assert response.status_code in (401, 403)


def test_the_session_reports_the_signed_in_role(admin: TestClient):
    body = admin.get("/api/auth/me").json()
    assert body["role"] == "admin"


def test_project_list_reports_node_and_task_counts(admin: TestClient, seeded):
    projects = admin.get("/api/projects").json()
    target = next(p for p in projects if p["id"] == seeded.project_id)
    assert target["node_count"] == seeded.node_count
    assert target["task_count"] == seeded.node_count * seeded.activity_count
    assert target["baseline_locked"] is True


def test_kpis_progress_is_a_percentage(admin: TestClient, seeded):
    """Audit H1: the legacy dashboard multiplied this by 100 a second time."""
    body = admin.get(f"/api/reporting/projects/{seeded.project_id}/kpis").json()
    assert body["total_nodes"] == seeded.node_count
    assert 0 <= body["progress"] <= 100
    assert 0 <= body["health"] <= 100
    assert sum(body["stage_mix"].values()) == body["total_nodes"]


def test_governance_matrix_reconciles_with_the_node_count(admin: TestClient, seeded):
    """Audit H5: the legacy matrix dropped nodes at any stage missing from its
    hand-written sequence, and the grand total under-counted silently."""
    matrix = admin.get(
        f"/api/reporting/projects/{seeded.project_id}/governance-matrix"
    ).json()
    kpis = admin.get(f"/api/reporting/projects/{seeded.project_id}/kpis").json()
    assert matrix["grand_total"] == kpis["total_nodes"]
    total_row = matrix["rows"][-1]
    assert total_row["stage"] == "Total"
    assert sum(total_row[c] for c in matrix["circles"]) == kpis["total_nodes"]


def test_node_list_returns_every_node_once(admin: TestClient, seeded):
    nodes = admin.get(f"/api/reporting/projects/{seeded.project_id}/nodes").json()["nodes"]
    assert len(nodes) == seeded.node_count
    assert len({n["scope_id"] for n in nodes}) == seeded.node_count


def test_open_nodes_are_not_all_reported_as_zero_delay(admin: TestClient, seeded):
    """Audit H4: the legacy circle dashboard showed 0 delay for every node
    because it summed delay only where status != 'Completed'."""
    nodes = admin.get(f"/api/reporting/projects/{seeded.project_id}/nodes").json()["nodes"]
    assert any(n["total_delay_days"] > 0 for n in nodes)
    assert any(n["at_risk"] for n in nodes)


def test_execution_grid_is_paginated_and_carries_the_baseline(admin: TestClient, seeded):
    body = admin.get(
        f"/api/execution/projects/{seeded.project_id}/grid", params={"page": 1, "page_size": 3}
    ).json()
    assert body["total_nodes"] == seeded.node_count
    assert len(body["nodes"]) == 3
    tasks = body["nodes"][0]["tasks"]
    assert len(tasks) == seeded.activity_count
    assert any(t["planned_finish"] for t in tasks)


def test_bulk_update_rejects_a_finish_before_a_start(admin: TestClient, seeded):
    """Audit M6: the legacy rule lived only in JavaScript, and the migration
    found four rows in live data where finish preceded start."""
    grid = admin.get(
        f"/api/execution/projects/{seeded.project_id}/grid", params={"page_size": 1}
    ).json()
    node = grid["nodes"][0]
    task = node["tasks"][0]
    response = admin.put(
        "/api/execution/bulk-update",
        json={
            "updates": [
                {
                    "scope_id": node["scope_id"],
                    "task_id": task["task_id"],
                    "actual_start": "2026-05-15",
                    "actual_finish": "2026-03-20",
                    "status": "Completed",
                }
            ]
        },
    )
    assert response.status_code == 422


def test_bulk_update_rejects_a_finish_with_no_start(admin: TestClient, seeded):
    grid = admin.get(
        f"/api/execution/projects/{seeded.project_id}/grid", params={"page_size": 1}
    ).json()
    node = grid["nodes"][0]
    task = node["tasks"][0]
    response = admin.put(
        "/api/execution/bulk-update",
        json={
            "updates": [
                {
                    "scope_id": node["scope_id"],
                    "task_id": task["task_id"],
                    "actual_finish": "2026-03-20",
                    "status": "Completed",
                }
            ]
        },
    )
    assert response.status_code == 422


def test_bulk_update_requires_a_csrf_token(app):
    """A valid session with a wrong CSRF token must still be refused."""
    from fastapi.testclient import TestClient

    from tests.conftest import TEST_ADMIN, TEST_PASSWORD

    client = TestClient(app)
    client.post("/api/auth/login", json={"username": TEST_ADMIN, "password": TEST_PASSWORD})
    response = client.put(
        "/api/execution/bulk-update",
        json={"updates": [{"scope_id": 1, "task_id": 1, "status": "Not Started"}]},
        headers={"x-csrf-token": "wrong"},
    )
    assert response.status_code == 403


def test_stage_ladder_is_ordered_and_complete(admin: TestClient):
    stages = admin.get("/api/reporting/stages").json()["stages"]
    assert len(stages) == 26
    positions = [s["position"] for s in stages]
    assert positions == sorted(positions)
    names = {s["name"] for s in stages}
    # The stage the legacy sequence omitted (audit H5).
    assert "Labeling WIP" in names

    by_name = {s["name"]: s for s in stages}
    clearance = by_name["Security Clearance"]

    # Confirmed 11 Sep 2026: weight lowered to 76, late position retained.
    assert clearance["weight"] == 76
    assert clearance["position"] > by_name["IDC & NOC Handover Completed"]["position"]
    assert clearance["position"] < by_name["RFS"]["position"]

    # The ladder is therefore deliberately non-monotonic in weight at exactly one
    # point. Anywhere else would be a mistake, so pin it: only Security Clearance
    # may score lower than the stage before it.
    dips = [
        stages[i]["name"]
        for i in range(1, len(stages))
        if stages[i]["weight"] < stages[i - 1]["weight"]
    ]
    assert dips == ["Security Clearance"], dips
