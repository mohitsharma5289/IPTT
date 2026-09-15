"""Scope, template, users, leadership actions, audit and PDF.

Every one of these replaces a legacy endpoint that had no authentication at all,
so the first thing each group checks is that an anonymous caller is refused.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    os.environ.get("IPTT_TEST_DB_READY") != "1",
    reason="requires a migrated PostgreSQL database",
)

PROJECT_ID = 12
PROGRAMME_ID = 6


@pytest.fixture(scope="module")
def anon() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="module")
def admin() -> TestClient:
    from app.main import create_app

    client = TestClient(create_app())
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    return client


# --- authentication ---------------------------------------------------------


def test_every_new_read_endpoint_rejects_anonymous(anon: TestClient):
    for path in [
        f"/api/projects/{PROJECT_ID}/scope",
        f"/api/projects/{PROJECT_ID}/template",
        f"/api/projects/{PROJECT_ID}/actions",
        "/api/users",
        "/api/audit",
        f"/api/reporting/programmes/{PROGRAMME_ID}/rollup",
        f"/api/reporting/projects/{PROJECT_ID}/pack.pdf",
    ]:
        assert anon.get(path).status_code == 401, path


def test_every_new_write_endpoint_rejects_anonymous(anon: TestClient):
    """The legacy scope, task and leadership routes accepted these from anyone."""
    cases = [
        ("post", f"/api/projects/{PROJECT_ID}/scope", {"node_id": "X", "circle": "MH",
                                                       "facility_name": "F", "num_servers": 1}),
        ("delete", f"/api/projects/{PROJECT_ID}/scope/1", None),
        ("post", f"/api/projects/{PROJECT_ID}/actions", {"action_required": "x"}),
        ("post", "/api/users", {"username": "x", "password": "Abcdefgh1!xyz"}),
    ]
    for method, path, body in cases:
        response = getattr(anon, method)(path, json=body) if body else getattr(anon, method)(path)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


def test_a_viewer_cannot_administer_users(admin: TestClient):
    from app.main import create_app

    viewer = TestClient(create_app())
    login = viewer.post(
        "/api/auth/login", json={"username": "Manoj_Viewer", "password": "viewer123"}
    )
    assert login.status_code == 200
    viewer.headers["x-csrf-token"] = login.json()["csrf_token"]
    assert viewer.get("/api/users").status_code == 403
    assert viewer.get("/api/audit").status_code == 403


# --- scope ------------------------------------------------------------------


def test_scope_lists_every_node_with_its_task_count(admin: TestClient):
    rows = admin.get(f"/api/projects/{PROJECT_ID}/scope").json()
    assert len(rows) == 57
    assert all(r["task_count"] == 50 for r in rows)
    assert any(r["has_execution_data"] for r in rows)


def test_deleting_a_node_with_recorded_dates_is_refused(admin: TestClient):
    """The legacy replace-mode upload destroyed these without asking."""
    rows = admin.get(f"/api/projects/{PROJECT_ID}/scope").json()
    target = next(r for r in rows if r["has_execution_data"])
    response = admin.delete(f"/api/projects/{PROJECT_ID}/scope/{target['id']}")
    assert response.status_code == 409
    assert "recorded activity date" in response.json()["detail"]


def test_scope_template_is_a_workbook(admin: TestClient):
    response = admin.get(f"/api/projects/{PROJECT_ID}/scope/template")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"


def test_scope_import_dry_run_of_current_state_is_a_no_op(admin: TestClient):
    from io import BytesIO

    from openpyxl import Workbook

    rows = admin.get(f"/api/projects/{PROJECT_ID}/scope").json()
    wb = Workbook()
    ws = wb.active
    ws.title = "Scope"
    ws.append(["Node", "Circle", "Facility", "Servers", "Priority"])
    for r in rows:
        ws.append([r["node_id"], r["circle"], r["facility_name"],
                   r["num_servers"], r["priority"]])
    buffer = BytesIO()
    wb.save(buffer)

    response = admin.post(
        f"/api/projects/{PROJECT_ID}/scope/import?dry_run=true",
        files={"file": ("scope.xlsx", buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] and body["to_create"] == 0 and body["to_update"] == 0


def test_scope_import_rejects_a_missing_column(admin: TestClient):
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Node", "Circle"])
    buffer = BytesIO()
    wb.save(buffer)
    response = admin.post(
        f"/api/projects/{PROJECT_ID}/scope/import",
        files={"file": ("scope.xlsx", buffer.getvalue(), "application/vnd.ms-excel")},
    )
    assert response.status_code == 400
    assert "Missing column" in response.json()["detail"]


# --- task template ----------------------------------------------------------


def test_template_lists_all_fifty_activities(admin: TestClient):
    rows = admin.get(f"/api/projects/{PROJECT_ID}/template").json()
    assert len(rows) == 50
    assert [r["template_task_number"] for r in rows] == list(range(1, 51))
    assert all(r["node_count"] == 57 for r in rows)


def test_template_export_round_trips_as_a_no_op(admin: TestClient):
    export = admin.get(f"/api/projects/{PROJECT_ID}/template/export")
    assert export.status_code == 200
    response = admin.post(
        f"/api/projects/{PROJECT_ID}/template/import?dry_run=true",
        files={"file": ("t.xlsx", export.content, "application/vnd.ms-excel")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"]
    assert body["to_create"] == [] and body["to_remove"] == []
    assert len(body["to_update"]) == 50


def test_template_import_rejects_a_dangling_predecessor(admin: TestClient):
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Template"
    ws.append(["Task_Number", "Task_Name", "Duration_Days", "Predecessor_Task_Number"])
    ws.append([1, "First", 1, None])
    ws.append([2, "Second", 1, 99])
    buffer = BytesIO()
    wb.save(buffer)

    response = admin.post(
        f"/api/projects/{PROJECT_ID}/template/import",
        files={"file": ("t.xlsx", buffer.getvalue(), "application/vnd.ms-excel")},
    )
    assert response.status_code == 422
    assert any("not\nin the sheet" in e or "not in the sheet" in e
               for e in response.json()["detail"]["errors"])


def test_template_import_rejects_a_dependency_cycle(admin: TestClient):
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Template"
    ws.append(["Task_Number", "Task_Name", "Duration_Days", "Predecessor_Task_Number"])
    ws.append([1, "A", 1, 2])
    ws.append([2, "B", 1, 1])
    buffer = BytesIO()
    wb.save(buffer)

    response = admin.post(
        f"/api/projects/{PROJECT_ID}/template/import",
        files={"file": ("t.xlsx", buffer.getvalue(), "application/vnd.ms-excel")},
    )
    assert response.status_code == 422
    assert any("cycle" in e for e in response.json()["detail"]["errors"])


def test_template_import_will_not_silently_drop_recorded_activities(admin: TestClient):
    """The legacy uploader deleted every task on each upload, orphaning the
    executions that pointed at them (audit B6, M19)."""
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Template"
    ws.append(["Task_Number", "Task_Name", "Duration_Days", "Predecessor_Task_Number"])
    ws.append([1, "Only this one", 1, None])
    buffer = BytesIO()
    wb.save(buffer)

    response = admin.post(
        f"/api/projects/{PROJECT_ID}/template/import",
        files={"file": ("t.xlsx", buffer.getvalue(), "application/vnd.ms-excel")},
    )
    assert response.status_code == 422
    assert any("carry recorded dates" in e for e in response.json()["detail"]["errors"])


# --- leadership actions -----------------------------------------------------


def test_action_lifecycle_and_priority_ordering(admin: TestClient):
    """Audit H8: ordering by the text column put High last."""
    created = []
    for priority, offset in (("Low", 30), ("High", -20), ("Medium", 5)):
        response = admin.post(
            f"/api/projects/{PROJECT_ID}/actions",
            json={
                "action_required": f"Test {priority}",
                "owner": "tester",
                "priority": priority,
                "status": "Open",
                "target_date": (date.today() + timedelta(days=offset)).isoformat(),
                "circle": "MH",
                "risk_area": "Testing",
            },
        )
        assert response.status_code == 201, response.text
        created.append(response.json())

    listing = admin.get(f"/api/projects/{PROJECT_ID}/actions").json()
    ours = [a for a in listing if a["action_required"].startswith("Test ")]
    assert [a["priority"] for a in ours] == ["High", "Medium", "Low"]

    high = next(a for a in ours if a["priority"] == "High")
    assert high["overdue_days"] == 20
    assert high["escalation"] == "Critical"
    # The legacy create path hardcoded these to "-" and discarded the caller's
    # status (audit H13).
    assert high["circle"] == "MH"
    assert high["risk_area"] == "Testing"

    updated = admin.put(
        f"/api/actions/{high['id']}",
        json={
            "action_required": high["action_required"],
            "owner": high["owner"],
            "priority": "Low",
            "status": "Closed",
            "target_date": high["target_date"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "Closed"
    assert updated.json()["overdue_days"] == 0  # closed actions are not overdue

    for action in created:
        assert admin.delete(f"/api/actions/{action['id']}").status_code == 204


def test_creating_an_action_honours_the_supplied_status(admin: TestClient):
    response = admin.post(
        f"/api/projects/{PROJECT_ID}/actions",
        json={"action_required": "Already handled", "status": "Closed", "priority": "Low"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "Closed"
    admin.delete(f"/api/actions/{response.json()['id']}")


# --- users ------------------------------------------------------------------


def test_user_listing_flags_the_migrated_defaults(admin: TestClient):
    users = admin.get("/api/users").json()
    assert len(users) >= 4
    assert all(u["must_change_password"] for u in users), (
        "every migrated account used a credential committed to the legacy repo"
    )


def test_cannot_demote_the_last_administrator(admin: TestClient):
    me = admin.get("/api/auth/me").json()
    response = admin.patch(f"/api/users/{me['id']}/role", json={"role": "viewer"})
    assert response.status_code == 400
    assert "your own administrator role" in response.json()["detail"]


def test_cannot_deactivate_yourself(admin: TestClient):
    me = admin.get("/api/auth/me").json()
    response = admin.patch(f"/api/users/{me['id']}/active", json={"is_active": False})
    assert response.status_code == 400


def test_create_user_enforces_password_strength(admin: TestClient):
    response = admin.post(
        "/api/users", json={"username": "weakling", "password": "password"}
    )
    assert response.status_code == 400
    assert "Password" in response.json()["detail"]


def test_user_creation_reset_and_assignment(admin: TestClient):
    created = admin.post(
        "/api/users",
        json={"username": "pytest_pm", "password": "Str0ng!Passphrase", "role": "pm"},
    )
    assert created.status_code == 201, created.text
    user = created.json()
    assert user["must_change_password"] is True

    assigned = admin.put(
        f"/api/users/{user['id']}/assignments", json={"project_ids": [PROJECT_ID]}
    )
    assert assigned.status_code == 200
    assert assigned.json()["assigned_project_ids"] == [PROJECT_ID]

    reset = admin.post(f"/api/users/{user['id']}/reset-password")
    assert reset.status_code == 200
    assert len(reset.json()["temporary_password"]) > 12

    deactivated = admin.patch(f"/api/users/{user['id']}/active", json={"is_active": False})
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    # Clean up.
    from sqlalchemy import delete

    from app.db import session_scope
    from app.models import AppUser, ProjectAssignment

    with session_scope() as db:
        db.execute(delete(ProjectAssignment).where(ProjectAssignment.user_id == user["id"]))
        db.execute(delete(AppUser).where(AppUser.id == user["id"]))


def test_assignment_rejects_an_unknown_project(admin: TestClient):
    users = admin.get("/api/users").json()
    pm = next(u for u in users if u["role"] == "pm")
    response = admin.put(
        f"/api/users/{pm['id']}/assignments", json={"project_ids": [999999]}
    )
    assert response.status_code == 400
    assert "Unknown project" in response.json()["detail"]


# --- audit ------------------------------------------------------------------


def test_audit_paginates_beyond_the_legacy_two_hundred_row_ceiling(admin: TestClient):
    """Audit M5: the legacy viewer applied a bare LIMIT 200 with no pagination,
    so older entries were permanently unreachable."""
    first = admin.get("/api/audit?limit=50").json()
    assert len(first["entries"]) <= 50
    if first["has_more"]:
        second = admin.get(f"/api/audit?limit=50&cursor={first['next_cursor']}").json()
        assert second["entries"]
        first_ids = {e["id"] for e in first["entries"]}
        second_ids = {e["id"] for e in second["entries"]}
        assert not (first_ids & second_ids), "pages must not overlap"
        assert max(second_ids) < min(first_ids), "keyset order is descending"


def test_audit_records_the_real_actor_not_a_role(admin: TestClient):
    """Audit M7: the legacy override endpoint wrote the caller-supplied *role*
    string and required no authentication, so entries identified nobody."""
    created = admin.post(
        f"/api/projects/{PROJECT_ID}/actions",
        json={"action_required": "audit actor check", "priority": "Low"},
    )
    action_id = created.json()["id"]
    entries = admin.get("/api/audit?limit=5").json()["entries"]
    latest = entries[0]
    assert latest["actor_username"] == "admin"
    assert latest["actor_role"] == "admin"
    assert latest["source"] == "api"
    admin.delete(f"/api/actions/{action_id}")


def test_audit_filters_by_source(admin: TestClient):
    page = admin.get("/api/audit?source=legacy-import&limit=5").json()
    assert all(e["source"] == "legacy-import" for e in page["entries"])


# --- reporting and PDF ------------------------------------------------------


def test_programme_rollup_counts_nodes_not_projects(admin: TestClient):
    """Audit H9/H10: the legacy version incremented circles once per project and
    averaged project healths without weighting."""
    rollup = admin.get(f"/api/reporting/programmes/{PROGRAMME_ID}/rollup").json()
    assert rollup["total_nodes"] == sum(p["nodes"] for p in rollup["projects"])
    assert rollup["total_nodes"] == sum(c["nodes"] for c in rollup["circles"])
    assert 0 <= rollup["health"] <= 100


def test_programme_rollup_survives_an_empty_programme(admin: TestClient):
    """Audit C12: the legacy report raised NameError in exactly this case."""
    created = admin.post("/api/programmes", json={"name": "pytest empty programme"})
    assert created.status_code == 201, created.text
    programme_id = created.json()["id"]
    try:
        rollup = admin.get(f"/api/reporting/programmes/{programme_id}/rollup").json()
        assert rollup["total_projects"] == 0
        assert rollup["total_nodes"] == 0
        assert rollup["health"] == 0
        assert "no projects" in rollup["narrative"]

        pack = admin.get(f"/api/reporting/programmes/{programme_id}/pack.pdf")
        assert pack.status_code == 200
        assert pack.content[:4] == b"%PDF"
    finally:
        admin.delete(f"/api/programmes/{programme_id}")


def test_narrative_does_not_label_a_task_as_a_stage(admin: TestClient):
    """Audit H11: the legacy sentence read 'Most delayed stage observed is
    <task name>'."""
    text = admin.get(f"/api/reporting/projects/{PROJECT_ID}/narrative").json()["narrative"]
    assert text
    assert "Most delayed stage" not in text
    if "most often late" in text:
        assert "activity most often late" in text


def test_all_three_pdf_packs_render(admin: TestClient):
    for path in [
        f"/api/reporting/projects/{PROJECT_ID}/pack.pdf",
        f"/api/reporting/programmes/{PROGRAMME_ID}/pack.pdf",
        f"/api/reporting/projects/{PROJECT_ID}/circles/BH/pack.pdf",
    ]:
        response = admin.get(path)
        assert response.status_code == 200, path
        assert response.content[:4] == b"%PDF", path
        assert len(response.content) > 2000, path
        assert "attachment" in response.headers["content-disposition"]


def test_circle_pack_404s_for_a_circle_with_no_nodes(admin: TestClient):
    response = admin.get(f"/api/reporting/projects/{PROJECT_ID}/circles/ZZ/pack.pdf")
    assert response.status_code == 404


def test_circle_rollup_matches_the_node_total(admin: TestClient):
    circles = admin.get(f"/api/reporting/projects/{PROJECT_ID}/circles").json()["circles"]
    kpis = admin.get(f"/api/reporting/projects/{PROJECT_ID}/kpis").json()
    assert sum(c["nodes"] for c in circles) == kpis["total_nodes"]
    healths = [c["health"] for c in circles]
    assert healths == sorted(healths), "weakest circle first"
