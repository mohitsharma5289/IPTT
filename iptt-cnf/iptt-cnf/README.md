# IPTT — CNF rebuild

Infrastructure Project Tracking Tool, rebuilt for OpenShift. Replaces the
FastAPI + Jinja2 + SQLite application audited in September 2026.

Target architecture, as agreed:

| Layer | Choice |
|---|---|
| Frontend | Next.js (not yet built — see *Status*) |
| Backend | FastAPI, modular monolith, JSON API only |
| Database | PostgreSQL 16 on a StatefulSet with a PVC |
| Platform | OpenShift, non-root containers, migrations as a Job |

---

## Status

**Done and verified against the real 10,106-row dataset:**

- Corrected data model, 14 tables, real foreign keys and cascades
- Alembic baseline + reference-data seed migration
- The eight agreed business rules, implemented and unit-tested
- SQLite → PostgreSQL ETL with quarantine reporting and a verification pass
- API foundation: auth with lockout and CSRF, health probes, reporting, execution grid, baseline/re-baseline
- Dockerfile, docker-compose, 62 passing tests

**Not yet built:** Next.js frontend, Excel import/export, PDF export, remaining
CRUD routes (programmes, scope, tasks, users, leadership actions), OpenShift
manifests.

---

## Quick start

```bash
cp backend/.env.example backend/.env      # then set SESSION_SECRET
docker compose up --build                 # postgres + migrations + api
open http://localhost:8000/api/docs
```

Migrating the legacy data:

```bash
cd backend
python -m etl.migrate_from_sqlite --sqlite /path/to/iptt.db --force
python -m etl.verify_migration    --sqlite /path/to/iptt.db
```

Tests:

```bash
make test                                  # unit suite, no database needed
IPTT_TEST_DB_READY=1 make test-all         # adds API smoke tests
```

---

## The eight business rules

These were ambiguous or contradictory in the legacy code. Each is now
implemented in exactly one place and covered by tests.

| # | Decision | Where it lives |
|---|---|---|
| 1 | Delay is counted in **working days** | `app/domain/delay.py` |
| 2 | The baseline is **`task.planned_finish`** | `app/domain/delay.py` |
| 3 | Node stage is the **furthest position reached** in the ladder | `app/domain/stages.py` |
| 4 | **Only Completed** activities earn stage credit | `app/domain/stages.py` |
| 5 | Stage weights re-derived from the task template | `app/domain/seed_data.py` |
| 6 | Re-baselining **preserves PM actuals**, in history and live | `app/services/baseline.py` |
| 7 | The planner **ignores `Scope.priority`** | `app/domain/planner.py` |
| 8 | The two constraint sets **merged into one table** | `app/domain/seed_data.py` |

Reference data that used to be Python constants — the stage ladder, the
task-to-stage map, capacity rules and the holiday calendar — now lives in the
database, so an admin can correct it without a redeploy.

---

## What changed in the numbers

Running the ETL against the live dataset moves several figures. All of it is
intended; none of it is a regression.

| Measure | Legacy | New | Why |
|---|---:|---:|---|
| Total delay days | 2,475 | 1,808 | Decision 1 — weekends and holidays no longer count as slip |
| Nodes at risk (>7 days) | 28 | 26 | Same |
| Project 12 health | 35.47 | 50.54 | Decision 5 — weights re-derived from the template |
| Project 12 progress | 3.51% | 3.51% | Unchanged — two nodes are live either way |
| Rows migrated | 10,106 | 10,102 | Four leadership actions pointed at deleted projects |

The health figure moving is worth understanding: it is the **weight scale** that
changed, not the underlying progress. Under decisions 3 and 4 the resolution is
strictly *tighter* — in-progress work now earns nothing — but the re-derived
weights place mid-pipeline stages higher than the hand-assigned originals did.

---

## Repository layout

```
backend/
  app/
    config.py          environment-driven settings, validated at startup
    db.py              engine and request-scoped sessions
    security.py        passwords, sessions, CSRF, project authorisation
    main.py            application factory
    models/            SQLAlchemy models, one module per aggregate
    domain/            pure business rules, no ORM, no I/O
      calendar.py      working days, circle-aware holidays
      delay.py         the single delay definition
      stages.py        stage resolution and health scoring
      planner.py       Day-0 plan generation
      seed_data.py     the corrected reference data
    services/          orchestration that touches the database
    api/               HTTP routers
  alembic/versions/    0001 schema, 0002 reference data
  etl/                 migration and verification
  tests/               62 tests
deploy/                OpenShift manifests (next increment)
```

`app/domain/` holds no imports from SQLAlchemy or FastAPI. That is what makes
the rules testable without a database — the legacy business logic was spread
across a 2,700-line `main.py`, a 1,300-line route module and eleven reporting
modules that each opened their own sessions.

---

## Two things that need your confirmation

**Security Clearance is template task 39 of 50, not last.** The legacy weight of
98 placed it just below RFS, which meant one completed activity could report a
node as ~98% done with eleven still outstanding — all testing, ATP and IDC/NOC
handover among them. It is seeded at position 17, weight 76, derived from where
it actually falls on the critical path. If the process has genuinely changed so
that clearance now comes last, the task template needs reordering, not the
weight.

**Two capacity rules conflicted between the legacy files.** Both are resolved in
favour of `planner.py`, the set that produced the live schedule, and flagged in
the `notes` column of `scheduling_constraint`:

- Task 32 (OS installation) — `planner.py` capped 3 globally and 1 per circle;
  `constraints.py` said 4 per circle. Seeded as 3 global + 1 per circle.
- Task 41 (testing offered) — `planner.py` capped 1 per circle;
  `constraints.py` said unlimited, grouped by facility. Seeded as 1 per circle.

Both are a single `UPDATE` away from changing, no redeploy required.

---

## Operational notes

- **Migrations never run on worker start.** `entrypoint.sh migrate` is a separate
  step, run as an OpenShift Job or initContainer.
- **The process refuses to boot** with the default session secret, with `DEBUG`
  on, or with a localhost database URL, in any environment other than `local`.
- **`/healthz` does not touch the database** — a database blip should not cause
  OpenShift to restart otherwise-healthy pods. `/readyz` does, and reports the
  applied schema revision.
- **Every migrated user is flagged `must_change_password`.** All four legacy
  accounts used credentials committed to the repository.
