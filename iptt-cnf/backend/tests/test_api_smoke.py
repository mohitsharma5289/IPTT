"""End-to-end smoke tests against the migrated PostgreSQL database.

Skipped automatically unless IPTT_TEST_DB_READY=1, so the unit suite stays
runnable without a database.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    os.environ.get("IPTT_TEST_DB_READY") != "1",
    reason="requires a migrated PostgreSQL database",
)

PROJECT_ID = 12


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="module")
def admin(client: TestClient) -> TestClient:
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    return client


def test_liveness_does_not_touch_the_database(client: TestClient):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readiness_reports_the_applied_schema_revision(client: TestClient):
    """Asserts against the Alembic head rather than a literal, so adding a
    migration does not break the test."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    body = client.get("/readyz").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["schema_revision"] == head


def test_every_data_route_rejects_an_anonymous_caller(client: TestClient):
    """Audit B5: 31 of 74 legacy routes had no session check."""
    for method, path in [
        ("get", "/api/projects"),
        ("get", f"/api/reporting/projects/{PROJECT_ID}/kpis"),
        ("get", f"/api/reporting/projects/{PROJECT_ID}/nodes"),
        ("get", f"/api/execution/projects/{PROJECT_ID}/grid"),
        ("get", "/api/reporting/stages"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401, f"{method} {path} returned {response.status_code}"


def test_writes_reject_an_anonymous_caller(client: TestClient):
    response = client.put(
        "/api/execution/bulk-update",
        json={"updates": [{"scope_id": 1, "task_id": 1, "status": "Not Started"}]},
    )
    assert response.status_code in (401, 403)


def test_migrated_admin_must_change_password(admin: TestClient):
    """Every legacy account used a credential committed to git (audit M8)."""
    body = admin.get("/api/auth/me").json()
    assert body["role"] == "admin"
    assert body["must_change_password"] is True


def test_project_list_reports_node_and_task_counts(admin: TestClient):
    projects = admin.get("/api/projects").json()
    target = next(p for p in projects if p["id"] == PROJECT_ID)
    assert target["node_count"] == 57
    assert target["task_count"] == 2850
    assert target["baseline_locked"] is True


def test_kpis_progress_is_a_percentage(admin: TestClient):
    """Audit H1: the legacy dashboard multiplied this by 100 a second time."""
    body = admin.get(f"/api/reporting/projects/{PROJECT_ID}/kpis").json()
    assert body["total_nodes"] == 57
    assert 0 <= body["progress"] <= 100
    assert 0 <= body["health"] <= 100
    assert sum(body["stage_mix"].values()) == body["total_nodes"]


def test_governance_matrix_reconciles_with_the_node_count(admin: TestClient):
    """Audit H5: the legacy matrix dropped nodes at any stage missing from its
    hand-written sequence, and the grand total under-counted silently."""
    matrix = admin.get(
        f"/api/reporting/projects/{PROJECT_ID}/governance-matrix"
    ).json()
    kpis = admin.get(f"/api/reporting/projects/{PROJECT_ID}/kpis").json()
    assert matrix["grand_total"] == kpis["total_nodes"]
    total_row = matrix["rows"][-1]
    assert total_row["stage"] == "Total"
    assert sum(total_row[c] for c in matrix["circles"]) == kpis["total_nodes"]


def test_node_list_returns_every_node_once(admin: TestClient):
    nodes = admin.get(f"/api/reporting/projects/{PROJECT_ID}/nodes").json()["nodes"]
    assert len(nodes) == 57
    assert len({n["scope_id"] for n in nodes}) == 57


def test_open_nodes_are_not_all_reported_as_zero_delay(admin: TestClient):
    """Audit H4: the legacy circle dashboard showed 0 delay for every node
    because it summed delay only where status != 'Completed'."""
    nodes = admin.get(f"/api/reporting/projects/{PROJECT_ID}/nodes").json()["nodes"]
    assert any(n["total_delay_days"] > 0 for n in nodes)
    assert any(n["at_risk"] for n in nodes)


def test_execution_grid_is_paginated_and_carries_the_baseline(admin: TestClient):
    body = admin.get(
        f"/api/execution/projects/{PROJECT_ID}/grid", params={"page": 1, "page_size": 3}
    ).json()
    assert body["total_nodes"] == 57
    assert len(body["nodes"]) == 3
    tasks = body["nodes"][0]["tasks"]
    assert len(tasks) == 50
    assert any(t["planned_finish"] for t in tasks)


def test_bulk_update_rejects_a_finish_before_a_start(admin: TestClient):
    """Audit M6: the legacy rule lived only in JavaScript, and the migration
    found four rows in live data where finish preceded start."""
    grid = admin.get(
        f"/api/execution/projects/{PROJECT_ID}/grid", params={"page_size": 1}
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


def test_bulk_update_rejects_a_finish_with_no_start(admin: TestClient):
    grid = admin.get(
        f"/api/execution/projects/{PROJECT_ID}/grid", params={"page_size": 1}
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


def test_bulk_update_requires_a_csrf_token(client: TestClient):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
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
