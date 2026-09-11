# Deploying IPTT on OpenShift

Kustomize, two overlays. `oc apply -k` is all that is needed once the images are
built and the Secrets exist.

```
deploy/
  base/            every resource, environment-independent
  overlays/dev/    1 replica each, 5Gi database, no backups, no PDBs
  overlays/prod/   3 API + 2 web replicas, 50Gi database, nightly backups
  validate.py      structural checks; run in CI
```

---

## What gets created

| Resource | Purpose |
|---|---|
| `StatefulSet/iptt-postgres` + PVC | The database. One replica, persistent volume, no operator. |
| `Deployment/iptt-api` | FastAPI, stateless, horizontally scalable. |
| `Deployment/iptt-web` | Next.js, stateless. Proxies `/api/*` to the API service. |
| `Job/iptt-migrate` | Alembic. Runs to completion **before** the API rolls. |
| `Route/iptt` | Edge TLS, HTTP redirected. Exposes the web service only. |
| `CronJob/iptt-backup` + PVC | Nightly `pg_dump`, verified, 14-day retention. Prod only. |
| 4 × `NetworkPolicy` | Default deny, then router→web, web→api, api→database. |
| 2 × `PodDisruptionBudget` | Keeps one replica up during node drains. Prod only. |

The API is never exposed outside the namespace. The browser talks only to the
web origin, which proxies to the API internally — so the session cookie stays
first-party and CORS never enters the picture.

---

## First deployment

**1. Namespace and images**

```bash
oc new-project iptt

oc new-build --binary --strategy=docker --name=iptt-api
oc start-build iptt-api --from-dir=backend --follow

oc new-build --binary --strategy=docker --name=iptt-web
oc start-build iptt-web --from-dir=frontend --follow
```

**2. Secrets** — created out of band, never committed. See
`base/secrets.example.yaml` for the full commands.

```bash
DB_PASSWORD="$(openssl rand -base64 32)"

oc create secret generic iptt-db \
  --from-literal=POSTGRES_USER=iptt \
  --from-literal=POSTGRES_PASSWORD="$DB_PASSWORD"

oc create secret generic iptt-app \
  --from-literal=SESSION_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  --from-literal=DATABASE_URL="postgresql+psycopg://iptt:${DB_PASSWORD}@iptt-postgres:5432/iptt"
```

The API **refuses to start** outside `local` if `SESSION_SECRET` is the
development default or shorter than 32 characters. The legacy application
shipped `iptt-secret-key-change-later` hardcoded in `main.py`, which meant
anyone with the repository could forge an admin session cookie.

**3. Apply, wait for the database, then migrate**

```bash
oc apply -k deploy/overlays/prod
oc rollout status statefulset/iptt-postgres
oc wait --for=condition=complete job/iptt-migrate --timeout=300s
oc rollout status deployment/iptt-api
oc rollout status deployment/iptt-web
oc get route iptt -o jsonpath='{.spec.host}{"\n"}'
```

**4. Load the legacy data** (once)

```bash
oc cp iptt.db "$(oc get pod -l app.kubernetes.io/component=api -o name | head -1 | cut -d/ -f2)":/tmp/iptt.db
oc exec deploy/iptt-api -- python -m etl.migrate_from_sqlite --sqlite /tmp/iptt.db --force
oc exec deploy/iptt-api -- python -m etl.verify_migration    --sqlite /tmp/iptt.db
```

Read the verification output before declaring the cutover good. Several figures
are *supposed* to move — see the root README.

**5. Rotate the migrated passwords.** Every account imported from the legacy
database is flagged `must_change_password`, because all four used credentials
that were committed to the old repository.

---

## Subsequent releases

```bash
oc start-build iptt-api --from-dir=backend --follow
oc start-build iptt-web --from-dir=frontend --follow
oc delete job iptt-migrate --ignore-not-found
oc apply -k deploy/overlays/prod
oc wait --for=condition=complete job/iptt-migrate --timeout=300s
oc rollout restart deployment/iptt-api deployment/iptt-web
```

Migrations run in the Job and nowhere else. The legacy application called
`Base.metadata.create_all()` at module import, so four Uvicorn workers raced to
issue DDL against production on every restart — and `create_all` cannot `ALTER`
an existing table, so schema changes silently did nothing.

Under ArgoCD the Job is already annotated as a `PreSync` hook and this sequencing
happens on its own.

---

## Notes that will save you an afternoon

**The database image.** `quay.io/sclorg/postgresql-16-c9s` is used rather than
`docker.io/postgres` because OpenShift's default `restricted-v2` SCC runs
containers as an arbitrary UID in group 0. The upstream image assumes UID 999
and will not start without granting an elevated SCC. Both application images are
built the same way — group-writable, numeric `USER`, no pinned UID.

**Read-only root filesystems.** All three workloads run with
`readOnlyRootFilesystem: true`. The API and the migration Job get an `emptyDir`
at `/tmp`; the web pod also needs one at `/app/.next/cache`.

**Liveness does not touch the database.** `/healthz` answers from the process
alone; only `/readyz` opens a connection and reports the applied schema
revision. A database blip therefore takes pods out of the load balancer without
restarting them.

**Restoring a backup.**

```bash
oc exec -it statefulset/iptt-postgres -- bash
pg_restore --clean --if-exists --no-owner -d iptt /backups/iptt-<stamp>.dump
```

The backup PVC is `ReadWriteOnce`, so mount it in a debug pod if you need to
copy a dump off-cluster.

**Scaling.** The API is stateless and safe to scale; sessions are signed cookies,
not server-side state. The database is a single replica by design — the agreed
architecture is a StatefulSet with a PVC, not an HA cluster. If you later need
HA, that is an operator decision, not a manifest change.

---

## CI

```bash
kustomize build deploy/overlays/prod | kubeconform -strict -summary
python3 deploy/validate.py
```

`validate.py` checks what a schema validator cannot: that Service selectors
match real pod labels, that every referenced ConfigMap key exists, that no
container is missing probes or resource limits, that nothing pins `runAsUser`,
that the Route exposes the web service and not the API, and that a
PodDisruptionBudget can never deadlock a node drain.
