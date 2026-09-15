# IPTT — CNF rebuild

Infrastructure Project Tracking Tool, rebuilt for OpenShift. Replaces the
FastAPI + Jinja2 + SQLite application audited in September 2026.

Target architecture, as agreed:

| Layer | Choice |
|---|---|
| Frontend | Next.js 15, App Router, TypeScript, Tailwind |
| Backend | FastAPI, modular monolith, JSON API only |
| Database | PostgreSQL 16 on a StatefulSet with a PVC |
| Platform | OpenShift, non-root containers, migrations as a Job |

---

## Status

**Done and verified against the real 10,106-row dataset:**

- Corrected data model, 14 tables, real foreign keys and cascades
- Alembic baseline, reference-data seed, and the confirmed Security Clearance change
- The eight agreed business rules, implemented and unit-tested
- SQLite → PostgreSQL ETL with quarantine reporting and a verification pass
- 63 API routes: auth with lockout and CSRF, health probes, programme and
  project lifecycle, reporting, execution grid, baseline/re-baseline, scope
  management, task-template management, user administration, leadership
  actions, audit log
- **Next.js frontend**, 9 routes: sign-in, portfolio with pipeline buckets,
  programme roll-up, project executive dashboard, the execution grid with
  auto-saving inline edits, scope editor, task-template editor, user
  administration, audit log
- **Excel round trip** on all three sheets — execution, scope and task template:
  export, validated import with a dry-run preview, fully audited — replacing the
  legacy importer that matched columns by position
- **PDF executive packs** at project, programme and circle level: KPI band,
  vector bar charts, circle and node tables, generated server-side with ReportLab
- **OpenShift manifests**: PostgreSQL StatefulSet + PVC, migration Job, API and
  web Deployments, edge-TLS Route, NetworkPolicies, PDBs, nightly backup CronJob,
  Kustomize dev/prod overlays
- **Project and programme lifecycle**: create, rename, restatus and delete, with
  the plan generated automatically once a project has a kickoff date, scope and
  a task template
- **Task template editing** in place — activities, durations, predecessors —
  with dependency cycles and dangling predecessors rejected before any write
- Dockerfiles for both services, docker-compose, 136 passing tests

Every screen in the legacy GUI now has a replacement, and the Day-0 flow —
create a project, scope it, template it, get a plan — has been driven end to end
in a browser against an empty database.

---

## Quick start

```bash
cp backend/.env.example backend/.env      # then set SESSION_SECRET
docker compose up --build                 # postgres, migrations, first admin, api, web
docker compose logs bootstrap             # the generated admin password, printed once
open http://localhost:3000                # the application
open http://localhost:8000/api/docs       # the API
```

**Signing in the first time.** The migrations seed reference data only — stages,
the task-stage map, capacity rules, holidays — never identities. A freshly
migrated database has an empty `app_user` table, and since creating a user needs
an existing admin and self-registration is disabled, there would otherwise be no
way in at all. The `bootstrap` step closes that: it creates one administrator,
generates a password if you did not supply one, and prints it to its log exactly
once. Set `BOOTSTRAP_ADMIN_PASSWORD` to choose your own. Either way the account
must change its password at first sign-in, and running it again does nothing
once an active admin exists.

If instead you migrate the legacy dataset, its four accounts come with it — all
flagged `must_change_password`, because the legacy repository committed their
credentials.

The browser only ever talks to the frontend origin. Next proxies `/api/*` to the
backend, so the session cookie stays first-party and CORS does not apply in the
deployed topology.

**The proxy destination is a build-time value, not a runtime one.** Next
evaluates `rewrites()` during `next build` and serialises the result into
`.next/routes-manifest.json`; the standalone server reads that file at startup.
Setting `API_ORIGIN` as a container environment variable therefore does nothing
— the symptom is the web container proxying to the default and failing every
request with `ECONNREFUSED`. The image is built against `http://iptt-api:8000`,
which resolves in both environments: `Service/iptt-api` on OpenShift, and the
compose service deliberately named `iptt-api`. Change it only at build time:

```bash
docker build --build-arg API_ORIGIN=http://elsewhere:8000 frontend
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
| 5 | Security Clearance weighted 76, late position kept | `alembic/versions/0003_*` |
| 6 | Re-baselining **preserves PM actuals**, in history and live | `app/services/baseline.py` |
| 7 | The planner **ignores `Scope.priority`** | `app/domain/planner.py` |
| 8 | The two constraint sets **merged into one table** | `app/domain/seed_data.py` |

### Planning is automatic until fieldwork starts

A project is planned the moment it has all three of a kickoff date, scope and a
task template — there is no separate "generate" button. Adding an activity later
replans, so it gets dates too.

That stops the instant the first actual start is recorded anywhere in the
project: `baseline_locked` flips, the plan freezes, and replanning becomes a
**re-baseline** — explicit, reason-bearing, and archiving the current state
first (decision 6).

Note `baseline_version` is *not* a "has been planned" flag. It starts at 1 on
every new project and counts re-baselines only; ask
`GET /api/projects/{id}/baseline-readiness` instead.

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
| Nodes quarantined | — | 8 | Legacy rows failing the new date-integrity checks |
| Project 12 progress | 3.51% | 3.51% | Unchanged — two nodes are live either way |
| Rows migrated | 10,106 | 10,102 | Four leadership actions pointed at deleted projects |

The health figure moving is worth understanding: it is the **weight scale** that
changed, not the underlying progress. Under decisions 3 and 4 the resolution is
strictly *tighter* — in-progress work now earns nothing — but the re-derived
weights place mid-pipeline stages higher than the hand-assigned originals did.

---

## Repository layout

```
frontend/
  src/
    app/              App Router pages
      login/          sign-in
      page.tsx        portfolio, bucketed into Ongoing / Setup / Completed
      programmes/[id]/           programme roll-up, weakest circle first
      projects/[id]/             executive dashboard
      projects/[id]/execution/   the PM working grid
      projects/[id]/scope/       node list, add/remove, sheet round trip
      projects/[id]/tasks/       task template, dependencies, sheet round trip
      admin/users/               accounts, roles, assignments, resets
      admin/audit/               keyset-paginated audit log
    components/       session context, shell, shared primitives,
                      workbook grid, sheet import dialog, actions panel
    lib/api.ts        the only place fetch, credentials and CSRF are handled
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
    api/               HTTP routers
    services/          orchestration that touches the database
      baseline.py      Day-0 planning and re-baselining
      excel.py         the workbook round trip
      rollup.py        circle and programme aggregates, narrative
      pdf.py           the executive packs
    bootstrap.py       creates the first administrator
  alembic/versions/    0001 schema, 0002 reference data, 0003 stage correction
  etl/                 migration and verification
  tests/               136 tests
deploy/
  base/                every Kubernetes resource
  overlays/dev|prod/   Kustomize overlays
  validate.py          structural checks for CI
  README.md            the deployment runbook
```

`app/domain/` holds no imports from SQLAlchemy or FastAPI. That is what makes
the rules testable without a database — the legacy business logic was spread
across a 2,700-line `main.py`, a 1,300-line route module and eleven reporting
modules that each opened their own sessions.

---

## Two decisions, and what they cost

**Security Clearance — confirmed, with one consequence to watch.** The weight is
now 76 rather than 98 and the stage keeps its late position just below RFS, as
requested on 11 Sep 2026.

Because a node's stage is its furthest *position* (decision 3), and template task
39 places the clearance activity mid-pipeline, two things follow while both hold:
a node that completes task 39 jumps past testing, ATP and handover and then
cannot be advanced by any of them until RFS; and a node that has passed ATP
Acceptance without clearance scores 93 while one that has passed both scores 76.

Migration `0003` documents this and `test_stages.py` pins the behaviour so it
cannot change silently. It resolves itself when the task template is renumbered
so the clearance activity genuinely is late — at which point position and weight
agree again.

**Two capacity rules — confirmed as seeded.** Resolved in favour of `planner.py`,
the set that produced the live schedule, and recorded in the `notes` column of
`scheduling_constraint`:

- Task 32 (OS installation) — `planner.py` capped 3 globally and 1 per circle;
  `constraints.py` said 4 per circle. Seeded as 3 global + 1 per circle.
- Task 41 (testing offered) — `planner.py` capped 1 per circle;
  `constraints.py` said unlimited, grouped by facility. Seeded as 1 per circle.

Both remain a single `UPDATE` away from changing, no redeploy required.

---

## The Excel round trip

Download the workbook, edit the Actual Start and Actual Finish columns, upload it
back. The upload is **always a dry run first**: the user sees every cell that
would change and must press Apply.

Every activity column heading carries its template task number —
`32 · OS Installation — Start` — and the importer binds to that number. Column
order is irrelevant: the sheet can be reordered, filtered, or have columns and
rows deleted, and the import is still correct. A heading whose number has been
stripped is a hard error naming the offending column, not a silent mis-write.

This is a direct replacement for `upload_project_execution`, which located the
"Facility" column and then walked forward in pairs, zipping columns against
execution rows by position. Inserting or moving one column wrote dates onto the
wrong activities across all 57 nodes, with no error, no audit entry and no
authentication (audit C3, B5, H12). `tests/test_excel.py` shuffles the columns
and asserts nothing changes.

---

## Executive packs

`GET /api/reporting/projects/{id}/pack.pdf`, `…/programmes/{id}/pack.pdf` and
`…/projects/{id}/circles/{circle}/pack.pdf` render a landscape A4 pack: a KPI
band, the stage distribution and circle health as vector bar charts, then the
node table. Charts are drawn as styled tables rather than rasterised through
Matplotlib, so the pack is a few hundred kilobytes and stays sharp when printed,
and the API image needs no font or graphics stack beyond ReportLab.

Every figure in the pack comes from the same `rollup.py` functions the dashboard
calls, so a pack and the screen it was generated from can never disagree.

---

## Administration

- **Users** — create, change role, deactivate, reset password, assign projects.
  The API refuses to let you demote or deactivate yourself, or to remove the last
  active admin. A reset returns a one-time password; hashes are never readable.
- **Scope** — add and remove nodes, or upload the scope sheet. Removing a node
  that has recorded dates returns 409 and names them; `force=true` is deliberate.
- **Task template** — edit activities, durations and predecessors. Changes are
  reconciled by template task number, never by delete-and-reinsert, so execution
  history survives. Dangling predecessors and dependency cycles are rejected
  before anything is written.
- **Audit log** — every write, with actor, source, field, before and after.
  Keyset-paginated and filterable; nothing ages out of reach.
- **Leadership actions** — the escalation list, ordered by priority then age,
  attachable to a circle, node or risk area.

---

## Deploying

See [`deploy/README.md`](deploy/README.md) for the runbook. In short:

```bash
oc apply -k deploy/overlays/prod
oc wait --for=condition=complete job/iptt-migrate --timeout=300s
```

Only the web service is exposed. The API is reachable only from the web pods and
the database only from the API, enforced by NetworkPolicy.

---

## IPv6

The stack runs dual-stack. Both containers bind `::`, which on Linux serves IPv4
clients too as v4-mapped addresses, so one listener covers both families. All
three OpenShift Services declare `ipFamilyPolicy: PreferDualStack` — a Service
without it is SingleStack in the cluster's primary family only, and the other
family fails silently.

Under compose the containers attach to the runtime's **existing default
network** rather than one compose creates, so `podman network ls` stays clean
and the containers sit on whatever the host already routes. That means the
address families — and container DNS — come from that network, not from this
repository. Two things to confirm on a new host:

```bash
podman network inspect podman --format '{{.DNSEnabled}}'    # must be true
podman network inspect podman --format '{{.IPv6Enabled}}'   # true for v6
```

Podman's default network ships with DNS **disabled**, unlike user-defined
networks. With DNS off, the `db` and `api` hostnames do not resolve and the API
cannot reach the database — and it fails at connection time, so the symptom
looks like a database fault rather than a network one. If that first command
prints `false`, three routes, in order of least disruption:

1. Enable DNS on the default network by writing its config explicitly —
   `/etc/containers/networks/podman.json` for rootful podman,
   `~/.local/share/containers/storage/networks/podman.json` for rootless — with
   `"dns_enabled": true`, then `podman network reload --all`. A host change, and
   it affects every project on that VM.
2. Point `default:` in `docker-compose.yml` at another existing network that has
   DNS enabled.
3. Use the project-owned bridge kept commented at the foot of
   `docker-compose.yml`. This reintroduces the extra entry in
   `podman network ls`, which is what moving to the default network avoided.

Two escape hatches, for a host or CI runner where IPv6 is disabled:

```bash
BIND_ADDRESS=0.0.0.0:8000   # API
HOSTNAME=0.0.0.0            # web
```

Without them, binding `::` on such a host fails with `[Errno 97] Address family
not supported by protocol`. `deploy/README.md` covers pinning a cluster to IPv6
only.

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
