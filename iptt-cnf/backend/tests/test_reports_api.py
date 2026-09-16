"""Portfolio-wide reports, the forecast, drill-downs and self-registration.

These are the screens the legacy app reached from its "Quick Reports" block,
plus the drill-downs and the register page. The rebuild had none of them; the
per-project endpoints cannot be summed into them because health is weighted by
node and a circle spans projects.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_ADMIN, unique


# --- governance -------------------------------------------------------------


def test_governance_lists_every_programme(admin: TestClient, seeded):
    body = admin.get("/api/reporting/governance").json()
    names = [p["programme_name"] for p in body["programmes"]]
    assert any("pytest seed" in n for n in names), names
    assert body["totals"]["programmes"] == len(body["programmes"])


def test_governance_counts_a_planned_project_as_planned(admin: TestClient, seeded):
    """`planned_projects` must reflect real planned dates, not baseline_version,
    which is 1 on every project from the moment it is created."""
    row = next(
        p
        for p in admin.get("/api/reporting/governance").json()["programmes"]
        if p["programme_id"] == seeded.programme_id
    )
    assert row["total_projects"] == 1
    assert row["planned_projects"] == 1
    assert row["planning_completion"] == 100.0
    assert row["total_nodes"] == seeded.node_count


def test_an_unplanned_project_drags_planning_completion_down(
    admin: TestClient, seeded
):
    created = admin.post(
        "/api/projects",
        json={"programme_id": seeded.programme_id, "name": "Unplanned"},
    )
    assert created.status_code == 201, created.text
    try:
        row = next(
            p
            for p in admin.get("/api/reporting/governance").json()["programmes"]
            if p["programme_id"] == seeded.programme_id
        )
        assert row["total_projects"] == 2
        assert row["planned_projects"] == 1
        assert row["planning_completion"] == 50.0
    finally:
        admin.delete(f"/api/projects/{created.json()['id']}?force=true")


def test_governance_is_ordered_weakest_first(admin: TestClient, seeded):
    healths = [p["health"] for p in admin.get("/api/reporting/governance").json()["programmes"]]
    assert healths == sorted(healths)


# --- circle intelligence ----------------------------------------------------


def test_circle_intelligence_covers_every_seeded_circle(admin: TestClient, seeded):
    rows = admin.get("/api/reporting/circles").json()["circles"]
    found = {r["circle"] for r in rows}
    assert set(seeded.circles) <= found, found


def test_a_circle_unions_nodes_rather_than_summing_projects(
    admin: TestClient, seeded
):
    """The same circle appears in several projects. Its node count must be the
    union, which is what makes this different from the per-project roll-up."""
    circle = seeded.circles[0]
    before = next(
        r for r in admin.get("/api/reporting/circles").json()["circles"]
        if r["circle"] == circle
    )

    second = admin.post(
        "/api/projects",
        json={
            "programme_id": seeded.programme_id,
            "name": "Second in circle",
            "project_start_date": "2026-04-01",
        },
    ).json()
    try:
        admin.post(
            f"/api/projects/{second['id']}/scope",
            json={
                "node_id": "EXTRA01",
                "circle": circle,
                "facility_name": "Another facility",
                "num_servers": 1,
            },
        )
        after = next(
            r for r in admin.get("/api/reporting/circles").json()["circles"]
            if r["circle"] == circle
        )
        assert after["total_nodes"] == before["total_nodes"] + 1
        assert after["projects"] == before["projects"] + 1
        assert after["facilities"] == before["facilities"] + 1
    finally:
        admin.delete(f"/api/projects/{second['id']}?force=true")


def test_circle_node_counts_partition_cleanly(admin: TestClient, seeded):
    """Live, in-flight and not-started must add up to the total - otherwise the
    dashboard is quietly losing nodes."""
    for row in admin.get("/api/reporting/circles").json()["circles"]:
        assert (
            row["completed_nodes"] + row["wip_nodes"] + row["not_started_nodes"]
            == row["total_nodes"]
        ), row


# --- forecast ---------------------------------------------------------------


def test_forecast_covers_every_node_including_unstarted(admin: TestClient, seeded):
    """The legacy loop skipped nodes with no execution rows, so a project where
    nothing had begun produced an empty dashboard."""
    body = admin.get(f"/api/reporting/projects/{seeded.project_id}/forecast").json()
    assert body["total_nodes"] == seeded.node_count
    assert len(body["nodes"]) == seeded.node_count


def test_forecast_risk_bands_partition(admin: TestClient, seeded):
    body = admin.get(f"/api/reporting/projects/{seeded.project_id}/forecast").json()
    assert body["high_risk"] + body["medium_risk"] + body["low_risk"] == body["total_nodes"]


def test_performance_factor_is_clamped(admin: TestClient, seeded):
    body = admin.get(f"/api/reporting/projects/{seeded.project_id}/forecast").json()
    for node in body["nodes"]:
        assert 1.0 <= node["performance_factor"] <= 3.0, node


def test_forecast_projects_over_working_days(admin: TestClient, seeded):
    """A forecast go-live must never land on a weekend. The legacy version added
    calendar days to today, so roughly two in seven did."""
    from datetime import date

    body = admin.get(f"/api/reporting/projects/{seeded.project_id}/forecast").json()
    landed = [
        date.fromisoformat(n["forecast_go_live"])
        for n in body["nodes"]
        if n["forecast_go_live"]
    ]
    assert landed, "no node produced a forecast date"
    assert all(d.weekday() < 5 for d in landed), [d for d in landed if d.weekday() >= 5]


def test_forecast_always_says_something(admin: TestClient, seeded):
    body = admin.get(f"/api/reporting/projects/{seeded.project_id}/forecast").json()
    assert body["insights"], "the forecast produced no narrative at all"


def test_forecast_404s_for_an_unknown_project(admin: TestClient):
    assert admin.get("/api/reporting/projects/9999999/forecast").status_code == 404


# --- drill-downs ------------------------------------------------------------


def test_circle_detail_lists_that_circle_only(admin: TestClient, seeded):
    circle = seeded.circles[0]
    body = admin.get(
        f"/api/reporting/projects/{seeded.project_id}/circles/{circle}"
    ).json()
    assert body["circle"] == circle
    assert body["node_count"] == len(body["nodes"])
    assert body["node_count"] > 0


def test_circle_detail_404s_for_a_circle_not_in_the_project(admin: TestClient, seeded):
    response = admin.get(f"/api/reporting/projects/{seeded.project_id}/circles/ZZ")
    assert response.status_code == 404


def test_facility_detail_lists_its_nodes(admin: TestClient, seeded):
    circle = seeded.circles[0]
    detail = admin.get(
        f"/api/reporting/projects/{seeded.project_id}/circles/{circle}"
    ).json()
    facility = detail["facilities"][0]
    body = admin.get(
        f"/api/reporting/projects/{seeded.project_id}/facilities/{facility}"
    ).json()
    assert body["facility_name"] == facility
    assert body["node_count"] == len(body["nodes"]) > 0


def test_node_detail_returns_the_whole_template_in_order(admin: TestClient, seeded):
    scope_id = seeded.scope_ids[0]
    body = admin.get(f"/api/reporting/nodes/{scope_id}").json()
    assert body["scope_id"] == scope_id
    numbers = [a["template_task_number"] for a in body["activities"]]
    assert numbers == sorted(numbers)
    assert len(numbers) == seeded.activity_count


def test_node_detail_404s_for_an_unknown_node(admin: TestClient):
    assert admin.get("/api/reporting/nodes/9999999").status_code == 404


def test_the_new_reports_reject_anonymous_callers(anon: TestClient, seeded):
    for path in [
        "/api/reporting/governance",
        "/api/reporting/circles",
        f"/api/reporting/projects/{seeded.project_id}/forecast",
        f"/api/reporting/projects/{seeded.project_id}/circles/{seeded.circles[0]}",
        f"/api/reporting/nodes/{seeded.scope_ids[0]}",
        "/api/users/pending",
    ]:
        assert anon.get(path).status_code in (401, 403), path


# --- self-registration ------------------------------------------------------


@pytest.fixture()
def registration_on(app):
    """Registration is off by default; turn it on for these tests only."""
    from app.config import get_settings

    settings = get_settings()
    before = settings.allow_self_registration
    settings.allow_self_registration = True
    yield
    settings.allow_self_registration = before


def test_registration_is_refused_when_disabled(anon: TestClient):
    from app.config import get_settings

    settings = get_settings()
    before = settings.allow_self_registration
    settings.allow_self_registration = False
    try:
        response = anon.post(
            "/api/auth/register",
            json={"username": unique("off"), "password": "Str0ng!Passw0rd"},
        )
        assert response.status_code == 403
    finally:
        settings.allow_self_registration = before


def test_a_registered_account_cannot_sign_in_until_approved(
    app, admin: TestClient, registration_on
):
    from fastapi.testclient import TestClient as Client

    name = unique("pending_user")
    password = "Str0ng!Passw0rd"
    visitor = Client(app)

    assert visitor.post(
        "/api/auth/register", json={"username": name, "password": password}
    ).status_code == 202

    # Pending: authenticate must refuse it.
    assert visitor.post(
        "/api/auth/login", json={"username": name, "password": password}
    ).status_code == 401

    queued = [u for u in admin.get("/api/users/pending").json() if u["username"] == name]
    assert queued, "the request never reached the approval queue"

    approved = admin.post(f"/api/users/{queued[0]['id']}/approve", json={"role": "viewer"})
    assert approved.status_code == 200, approved.text

    assert visitor.post(
        "/api/auth/login", json={"username": name, "password": password}
    ).status_code == 200


def test_registration_does_not_reveal_whether_a_username_is_taken(
    app, registration_on
):
    """Otherwise the form is a way for an unauthenticated caller to enumerate
    who has an account."""
    from fastapi.testclient import TestClient as Client

    visitor = Client(app)
    name = unique("oracle")
    first = visitor.post(
        "/api/auth/register", json={"username": name, "password": "Str0ng!Passw0rd"}
    )
    second = visitor.post(
        "/api/auth/register", json={"username": name, "password": "An0ther!Passw0rd"}
    )
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()

    # And the existing account's password was not overwritten by the second try.
    taken = visitor.post(
        "/api/auth/login", json={"username": name, "password": "An0ther!Passw0rd"}
    )
    assert taken.status_code == 401


def test_a_weak_password_is_refused_at_registration(app, registration_on):
    from fastapi.testclient import TestClient as Client

    response = Client(app).post(
        "/api/auth/register", json={"username": unique("weak"), "password": "short"}
    )
    assert response.status_code == 400
    assert "Password" in response.json()["detail"]


def test_rejecting_a_request_frees_the_username(app, admin: TestClient, registration_on):
    from fastapi.testclient import TestClient as Client

    visitor = Client(app)
    name = unique("rejected")
    visitor.post("/api/auth/register", json={"username": name, "password": "Str0ng!Passw0rd"})
    queued = next(u for u in admin.get("/api/users/pending").json() if u["username"] == name)

    assert admin.delete(f"/api/users/{queued['id']}/approve").status_code == 204
    assert not [u for u in admin.get("/api/users/pending").json() if u["username"] == name]

    # The name is free again, which it would not be if the row were merely
    # left inactive.
    assert visitor.post(
        "/api/auth/register", json={"username": name, "password": "Str0ng!Passw0rd"}
    ).status_code == 202
    again = next(u for u in admin.get("/api/users/pending").json() if u["username"] == name)
    admin.delete(f"/api/users/{again['id']}/approve")


def test_the_pending_queue_excludes_deactivated_staff(admin: TestClient):
    """A deactivated former employee is inactive too, and must never appear in a
    queue whose buttons say Approve."""
    name = unique("deactivated")
    created = admin.post(
        "/api/users", json={"username": name, "password": "Str0ng!Passw0rd", "role": "viewer"}
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    admin.patch(f"/api/users/{user_id}/active", json={"is_active": False})

    pending = [u["username"] for u in admin.get("/api/users/pending").json()]
    assert name not in pending, pending


def test_only_an_admin_sees_or_acts_on_the_queue(app, admin: TestClient):
    from fastapi.testclient import TestClient as Client

    name = unique("viewer_probe")
    password = "Str0ng!Passw0rd"
    created = admin.post(
        "/api/users", json={"username": name, "password": password, "role": "viewer"}
    )
    assert created.status_code == 201, created.text

    viewer = Client(app)
    login = viewer.post("/api/auth/login", json={"username": name, "password": password})
    assert login.status_code == 200
    viewer.headers["x-csrf-token"] = login.json()["csrf_token"]

    assert viewer.get("/api/users/pending").status_code == 403
    assert viewer.post(f"/api/users/{created.json()['id']}/approve", json={}).status_code == 403
