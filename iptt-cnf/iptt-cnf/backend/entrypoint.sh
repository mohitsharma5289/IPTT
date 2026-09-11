#!/bin/sh
# Migrations run as a separate Job/initContainer in OpenShift, never on every
# worker start. The legacy app called create_all() at import time, so four
# workers raced to issue DDL against production on every rollout (audit B3).
set -e
case "$1" in
  migrate) exec alembic upgrade head ;;
  serve|"") exec gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker \
              --workers "${WEB_CONCURRENCY:-4}" --bind 0.0.0.0:8000 --access-logfile - ;;
  *) exec "$@" ;;
esac
