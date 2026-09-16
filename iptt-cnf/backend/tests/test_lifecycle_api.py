"""Project and programme lifecycle, and the automatic first baseline.

These cover the path a brand-new deployment takes: create a programme, create a
project, give it scope and a template, and watch it become plannable. Until this
module existed the API had no way to create a project at all.

Everything runs inside one programme that is deleted at the end.
"""
from __future__ import annotations

import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

STAMP = date.today().isoformat()
PROGRAMME = f"pytest lifecycle {STAMP}"


@pytest.fixture(scope="module")
def programme_id(admin: TestClient):
    # Clear any leftover from an interrupted run.
    for p in admin.get("/api/programmes").json():
        if p["name"] == PROGRAMME:
            for proj in admin.get(f"/api/projects?programme_id={p['id']}").json():
                admin.delete(f"/api/projects/{proj['id']}?force=true")
            admin.delete(f"/api/programmes/{p['id']}")

    r = admin.post("/api/programmes", json={"name": PROGRAMME, "status": "Active"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    yield pid
    for proj in admin.get(f"/api/projects?programme_id={pid}").json():
        admin.delete(f"/api/projects/{proj['id']}?force=true")
    admin.delete(f"/api/programmes/{pid}")


def test_a_project_can_be_created_at_all(admin: TestClient, programme_id: int):
    r = admin.post(
        "/api/projects",
        json={"programme_id": programme_id, "name": "Alpha", "status": "Not Started"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Alpha"
    assert body["programme_id"] == programme_id
    assert body["node_count"] == 0 and body["task_count"] == 0
    assert body["baseline_locked"] is False
    admin.delete(f"/api/projects/{body['id']}?force=true")


def test_duplicate_name_within_a_programme_is_refused(admin: TestClient, programme_id: int):
    first = admin.post(
        "/api/projects", json={"programme_id": programme_id, "name": "Dup"}
    )
    assert first.status_code == 201
    again = admin.post(
        "/api/projects", json={"programme_id": programme_id, "name": "Dup"}
    )
    assert again.status_code == 409
    admin.delete(f"/api/projects/{first.json()['id']}?force=true")


def test_creating_under_a_missing_programme_is_404(admin: TestClient):
    r = admin.post("/api/projects", json={"programme_id": 10**7, "name": "Orphan"})
    assert r.status_code == 404


def test_project_can_be_renamed_and_restatused(admin: TestClient, programme_id: int):
    pid = admin.post(
        "/api/projects", json={"programme_id": programme_id, "name": "Before"}
    ).json()["id"]

    r = admin.patch(f"/api/projects/{pid}", json={"name": "After", "status": "In Progress"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "After"
    assert r.json()["status"] == "In Progress"
    admin.delete(f"/api/projects/{pid}?force=true")


def test_the_full_day_zero_flow_plans_itself(admin: TestClient, programme_id: int):
    """The point of the whole exercise: an empty project becomes a planned one
    as soon as it has a kickoff date, scope and a template - no separate
    'generate' step, as agreed."""
    pid = admin.post(
        "/api/projects",
        json={
            "programme_id": programme_id,
            "name": "Day Zero",
            "project_start_date": "2026-10-01",
        },
    ).json()["id"]

    # A kickoff date alone plans nothing.
    ready = admin.get(f"/api/projects/{pid}/baseline-readiness").json()
    assert ready["planned"] is False
    assert "at least one node in scope" in ready["missing"]
    assert "a task template" in ready["missing"]

    # Scope, still no template.
    node = admin.post(
        f"/api/projects/{pid}/scope",
        json={"node_id": "PYT1", "circle": "MH", "facility_name": "Pytest DC", "num_servers": 2},
    )
    assert node.status_code == 201, node.text
    ready = admin.get(f"/api/projects/{pid}/baseline-readiness").json()
    assert ready["missing"] == ["a task template"]

    # The template completes the set, so this save plans the project.
    first = admin.post(
        f"/api/projects/{pid}/template",
        json={"template_task_number": 1, "name": "Kickoff", "duration_days": 2},
    )
    assert first.status_code == 201, first.text

    ready = admin.get(f"/api/projects/{pid}/baseline-readiness").json()
    assert ready["ready"] is True and ready["missing"] == []

    # And it produced real planned dates, not empty rows.
    grid = admin.get(f"/api/execution/projects/{pid}/grid").json()
    assert grid["nodes"], "no execution rows were created"
    assert grid["nodes"][0]["tasks"], "the node has no activities"

    admin.delete(f"/api/projects/{pid}?force=true")


def test_replanning_tracks_the_template_until_fieldwork_starts(
    admin: TestClient, programme_id: int
):
    """An activity added before anyone is on site must get planned dates too -
    otherwise it sits in the grid with none and looks broken. Once an actual
    start exists the plan is frozen and only a re-baseline may move it."""
    pid = admin.post(
        "/api/projects",
        json={
            "programme_id": programme_id,
            "name": "Tracks",
            "project_start_date": "2026-10-01",
        },
    ).json()["id"]
    admin.post(
        f"/api/projects/{pid}/scope",
        json={"node_id": "PYT2", "circle": "MH", "facility_name": "Pytest DC2", "num_servers": 1},
    )
    admin.post(
        f"/api/projects/{pid}/template",
        json={"template_task_number": 1, "name": "One", "duration_days": 1},
    )

    # A second activity, still no fieldwork: it should come back planned.
    admin.post(
        f"/api/projects/{pid}/template",
        json={"template_task_number": 2, "name": "Two", "duration_days": 1,
              "predecessor_template_number": 1},
    )
    grid = admin.get(f"/api/execution/projects/{pid}/grid").json()
    planned = [t["planned_finish"] for n in grid["nodes"] for t in n["tasks"]]
    assert len(planned) == 2, f"expected both activities, got {len(planned)}"
    assert all(planned), f"an activity was left without planned dates: {planned}"

    # Record an actual start; the project locks.
    node = grid["nodes"][0]
    saved = admin.put(
        "/api/execution/bulk-update",
        json={
            "updates": [
                {
                    "scope_id": node["scope_id"],
                    "task_id": node["tasks"][0]["task_id"],
                    "actual_start": "2026-10-02",
                }
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["applied"] == 1, saved.text
    assert admin.get(f"/api/projects/{pid}").json()["baseline_locked"] is True

    # A further template change must NOT silently re-plan now.
    before = admin.get(f"/api/execution/projects/{pid}/grid").json()
    admin.post(
        f"/api/projects/{pid}/template",
        json={"template_task_number": 3, "name": "Three", "duration_days": 1,
              "predecessor_template_number": 2},
    )
    after = admin.get(f"/api/execution/projects/{pid}/grid").json()
    first_before = before["nodes"][0]["tasks"][0]
    first_after = next(
        t for t in after["nodes"][0]["tasks"]
        if t["execution_id"] == first_before["execution_id"]
    )
    assert first_after["planned_finish"] == first_before["planned_finish"], (
        "an existing planned date moved without an explicit re-baseline"
    )

    admin.delete(f"/api/projects/{pid}?force=true")


def test_kickoff_cannot_be_moved_under_a_live_baseline(admin: TestClient, programme_id: int):
    pid = admin.post(
        "/api/projects",
        json={
            "programme_id": programme_id,
            "name": "Locked",
            "project_start_date": "2026-10-01",
        },
    ).json()["id"]
    admin.post(
        f"/api/projects/{pid}/scope",
        json={"node_id": "PYT3", "circle": "MH", "facility_name": "Pytest DC3", "num_servers": 1},
    )
    admin.post(
        f"/api/projects/{pid}/template",
        json={"template_task_number": 1, "name": "One", "duration_days": 1},
    )
    grid = admin.get(f"/api/execution/projects/{pid}/grid").json()
    node = grid["nodes"][0]
    admin.put(
        "/api/execution/bulk-update",
        json={
            "updates": [
                {
                    "scope_id": node["scope_id"],
                    "task_id": node["tasks"][0]["task_id"],
                    "actual_start": "2026-10-02",
                }
            ]
        },
    )

    r = admin.patch(f"/api/projects/{pid}", json={"project_start_date": "2026-11-01"})
    assert r.status_code == 409
    assert "re-baselin" in r.json()["detail"].lower()
    # Renaming is still fine.
    assert admin.patch(f"/api/projects/{pid}", json={"name": "Locked II"}).status_code == 200

    admin.delete(f"/api/projects/{pid}?force=true")


# --- template row editing --------------------------------------------------


@pytest.fixture()
def templated(admin: TestClient, programme_id: int):
    pid = admin.post(
        "/api/projects",
        json={
            "programme_id": programme_id,
            "name": f"Tmpl {os.urandom(3).hex()}",
            "project_start_date": "2026-10-01",
        },
    ).json()["id"]
    admin.post(
        f"/api/projects/{pid}/scope",
        json={"node_id": "TMP1", "circle": "MH", "facility_name": "T", "num_servers": 1},
    )
    for n in (1, 2):
        admin.post(
            f"/api/projects/{pid}/template",
            json={
                "template_task_number": n,
                "name": f"Activity {n}",
                "duration_days": 3,
                "predecessor_template_number": n - 1 or None,
            },
        )
    yield pid
    admin.delete(f"/api/projects/{pid}?force=true")


def test_template_row_edits_apply_to_every_node(admin: TestClient, templated: int):
    r = admin.patch(
        f"/api/projects/{templated}/template/2",
        json={"name": "Renamed", "duration_days": 9},
    )
    assert r.status_code == 200, r.text
    row = next(
        x for x in admin.get(f"/api/projects/{templated}/template").json()
        if x["template_task_number"] == 2
    )
    assert row["name"] == "Renamed" and row["duration_days"] == 9


def test_a_cycle_is_refused(admin: TestClient, templated: int):
    """1 <- 2 already; pointing 1 at 2 closes the loop and would make the
    planner spin."""
    r = admin.patch(
        f"/api/projects/{templated}/template/1",
        json={"predecessor_template_number": 2},
    )
    assert r.status_code == 400
    assert "cycle" in r.json()["detail"].lower()


def test_a_dangling_predecessor_is_refused(admin: TestClient, templated: int):
    r = admin.patch(
        f"/api/projects/{templated}/template/2",
        json={"predecessor_template_number": 99},
    )
    assert r.status_code == 400
    assert "not in the template" in r.json()["detail"]


def test_self_reference_is_refused(admin: TestClient, templated: int):
    r = admin.patch(
        f"/api/projects/{templated}/template/2",
        json={"predecessor_template_number": 2},
    )
    assert r.status_code == 400


def test_duplicate_activity_number_is_refused(admin: TestClient, templated: int):
    r = admin.post(
        f"/api/projects/{templated}/template",
        json={"template_task_number": 1, "name": "Clash", "duration_days": 1},
    )
    assert r.status_code == 409


def test_deleting_an_activity_others_depend_on_is_refused(admin: TestClient, templated: int):
    r = admin.delete(f"/api/projects/{templated}/template/1")
    assert r.status_code == 409
    assert "predecessor" in r.json()["detail"].lower()


def test_a_leaf_activity_deletes_cleanly(admin: TestClient, templated: int):
    assert admin.delete(f"/api/projects/{templated}/template/2").status_code == 204
    numbers = [
        x["template_task_number"]
        for x in admin.get(f"/api/projects/{templated}/template").json()
    ]
    assert numbers == [1]


def test_deleting_a_project_with_recorded_dates_needs_force(
    admin: TestClient, programme_id: int
):
    pid = admin.post(
        "/api/projects",
        json={
            "programme_id": programme_id,
            "name": "HasData",
            "project_start_date": "2026-10-01",
        },
    ).json()["id"]
    admin.post(
        f"/api/projects/{pid}/scope",
        json={"node_id": "REC1", "circle": "MH", "facility_name": "R", "num_servers": 1},
    )
    admin.post(
        f"/api/projects/{pid}/template",
        json={"template_task_number": 1, "name": "One", "duration_days": 1},
    )
    grid = admin.get(f"/api/execution/projects/{pid}/grid").json()
    node = grid["nodes"][0]
    saved = admin.put(
        "/api/execution/bulk-update",
        json={
            "updates": [
                {
                    "scope_id": node["scope_id"],
                    "task_id": node["tasks"][0]["task_id"],
                    "actual_start": "2026-10-02",
                }
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["applied"] == 1, saved.text

    refused = admin.delete(f"/api/projects/{pid}")
    assert refused.status_code == 409
    assert "recorded dates" in refused.json()["detail"]

    assert admin.delete(f"/api/projects/{pid}?force=true").status_code == 204
    assert admin.get(f"/api/projects/{pid}").status_code == 404
