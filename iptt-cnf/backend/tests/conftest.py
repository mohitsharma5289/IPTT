"""Shared fixtures. The suite seeds everything it needs.

Previously most of these tests ran against the POC dataset that was migrated
from SQLite: they hardcoded `PROJECT_ID = 12`, the legacy `admin/admin123`
credentials, and magic numbers like "57 nodes" and "50 activities". That made a
clean checkout unable to run its own tests, and CI impossible without shipping a
copy of the POC database.

Now the only external requirement is an empty PostgreSQL database with the
migrations applied. Everything else - the admin account, a programme, a project,
its scope, its task template and some recorded execution - is built here and
torn down afterwards. Tests assert against the fixture's own numbers rather than
against constants copied out of someone's spreadsheet.

    createdb iptt_test
    DATABASE_URL=postgresql+psycopg://...  alembic upgrade head
    IPTT_TEST_DB_READY=1 pytest

The reference data the migrations seed - the stage ladder, the task-to-stage
map, capacity rules, holidays - is still required, because the planner and the
stage resolver are meaningless without it. That is schema-level seed data, not
POC data.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta

import pytest

DB_READY = os.environ.get("IPTT_TEST_DB_READY") == "1"

# Credentials this suite creates and uses. Nothing here depends on an account
# that arrived with a data migration.
TEST_ADMIN = "pytest_admin"
TEST_PASSWORD = "Pytest!Passw0rd"

#: Accounts cannot be deleted - only deactivated, so the audit trail survives -
#: so any user a test creates would collide with itself on the next run. Names
#: are suffixed with this instead.
RUN_ID = f"{os.getpid()}{int(date.today().strftime('%j'))}"


def unique(name: str) -> str:
    """A username that will not collide with a previous run."""
    return f"{name}_{RUN_ID}"

# Deliberately small but structurally realistic: several circles so the
# governance matrix and circle roll-ups have something to group by, a
# dependency chain so the planner has ordering to resolve, and a prerequisite
# gate so zero-duration handling is exercised.
CIRCLES = ("MH", "TN", "KA")
NODES_PER_CIRCLE = 2
ACTIVITIES = (
    # (number, name, duration, predecessor, is_prerequisite)
    (1, "Site readiness", 0, None, True),
    (2, "HW delivery", 5, 1, False),
    (3, "OS Installation", 3, 2, False),
    (4, "Integration", 4, 3, False),
    (5, "ATP Acceptance", 2, 4, False),
)
KICKOFF = date(2026, 3, 16)  # a Monday


@dataclass
class Seed:
    """What the seeded project actually contains, so tests assert against this
    rather than against numbers that only held for the POC extract."""

    programme_id: int
    project_id: int
    node_count: int
    activity_count: int
    circles: tuple[str, ...]
    scope_ids: list[int] = field(default_factory=list)


def _ensure_admin() -> None:
    """Create the test administrator directly, bypassing the API.

    Chicken-and-egg: creating a user requires an admin session, and a fresh
    database has no users at all. This is the same gap `app.bootstrap` exists
    to close in a real deployment.
    """
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import AppUser
    from app.models.enums import Role
    from app.security import hash_password

    with SessionLocal() as db:
        existing = db.scalar(select(AppUser).where(AppUser.username == TEST_ADMIN))
        if existing is None:
            db.add(
                AppUser(
                    username=TEST_ADMIN,
                    password_hash=hash_password(TEST_PASSWORD),
                    role=Role.ADMIN,
                    is_active=True,
                    must_change_password=False,
                )
            )
        else:
            # Re-assert the password: a previous run may have reset it.
            existing.password_hash = hash_password(TEST_PASSWORD)
            existing.role = Role.ADMIN
            existing.is_active = True
        db.commit()


@pytest.fixture(scope="session")
def app():
    if not DB_READY:
        pytest.skip("requires a migrated PostgreSQL database")
    from app.main import create_app

    return create_app()


@pytest.fixture(scope="session")
def anon(app):
    """A client with no session, for authentication tests."""
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture(scope="session")
def admin(app):
    """A signed-in administrator, with the CSRF token already on the client."""
    from fastapi.testclient import TestClient

    _ensure_admin()
    client = TestClient(app)
    response = client.post(
        "/api/auth/login", json={"username": TEST_ADMIN, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    return client


@pytest.fixture(scope="session")
def seeded(admin) -> Seed:
    """A complete, planned project: programme, scope, template, plan, actuals.

    Built through the public API, so the fixture also proves the Day-0 flow
    works end to end on every run.
    """
    name = f"pytest seed {os.getpid()}"

    # Clear anything a previous interrupted run left behind.
    for programme in admin.get("/api/programmes").json():
        if programme["name"].startswith("pytest seed"):
            for project in admin.get(
                f"/api/projects?programme_id={programme['id']}"
            ).json():
                admin.delete(f"/api/projects/{project['id']}?force=true")
            admin.delete(f"/api/programmes/{programme['id']}")

    programme = admin.post("/api/programmes", json={"name": name, "status": "Active"})
    assert programme.status_code == 201, programme.text
    programme_id = programme.json()["id"]

    project = admin.post(
        "/api/projects",
        json={
            "programme_id": programme_id,
            "name": "Seeded Project",
            "status": "In Progress",
            "project_start_date": KICKOFF.isoformat(),
        },
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    scope_ids = []
    for circle in CIRCLES:
        for n in range(1, NODES_PER_CIRCLE + 1):
            created = admin.post(
                f"/api/projects/{project_id}/scope",
                json={
                    "node_id": f"{circle}{n}PYTEST01",
                    "circle": circle,
                    "facility_name": f"{circle} facility {n}",
                    "num_servers": 4,
                },
            )
            assert created.status_code == 201, created.text
            scope_ids.append(created.json()["id"])

    for number, activity, duration, predecessor, prerequisite in ACTIVITIES:
        added = admin.post(
            f"/api/projects/{project_id}/template",
            json={
                "template_task_number": number,
                "name": activity,
                "duration_days": duration,
                "predecessor_template_number": predecessor,
                "is_prerequisite": prerequisite,
            },
        )
        assert added.status_code == 201, added.text

    # The plan generates itself once all three preconditions are met.
    readiness = admin.get(f"/api/projects/{project_id}/baseline-readiness").json()
    assert readiness["planned"] is True, readiness
    assert readiness["ready"] is True, readiness

    _record_execution(admin, project_id)

    seed = Seed(
        programme_id=programme_id,
        project_id=project_id,
        node_count=len(CIRCLES) * NODES_PER_CIRCLE,
        activity_count=len(ACTIVITIES),
        circles=CIRCLES,
        scope_ids=scope_ids,
    )
    yield seed

    admin.delete(f"/api/projects/{project_id}?force=true")
    admin.delete(f"/api/programmes/{programme_id}")


def _record_execution(admin, project_id: int) -> None:
    """Give the project a realistic execution state.

    Several tests need a project that is genuinely *in flight*: some nodes
    complete, at least one finishing late enough to be at risk (more than 7
    working days past its planned finish), and some still open. Without that,
    the delay, health and heat-map assertions would all be vacuously true.
    """
    grid = admin.get(
        f"/api/execution/projects/{project_id}/grid", params={"page_size": 200}
    ).json()

    updates = []
    for index, node in enumerate(grid["nodes"]):
        for position, task in enumerate(node["tasks"]):
            planned = task["planned_finish"]
            if planned is None or position > index % 3:
                continue  # leave later activities open, staggered per node
            finish = date.fromisoformat(planned)
            # Every third node runs badly late, so "at risk" is non-empty.
            if index % 3 == 0:
                finish += timedelta(days=20)
            start = date.fromisoformat(task["planned_start"] or planned)
            updates.append(
                {
                    "scope_id": node["scope_id"],
                    "task_id": task["task_id"],
                    "actual_start": start.isoformat(),
                    "actual_finish": finish.isoformat(),
                    "status": "Completed",
                }
            )

    if updates:
        applied = admin.put("/api/execution/bulk-update", json={"updates": updates})
        assert applied.status_code == 200, applied.text


@pytest.fixture
def db():
    """A plain session for tests that exercise services directly."""
    if not DB_READY:
        pytest.skip("requires a migrated PostgreSQL database")
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
