#!/bin/sh
# Migrations run as a separate Job/initContainer in OpenShift, never on every
# worker start. The legacy app called create_all() at import time, so four
# workers raced to issue DDL against production on every rollout (audit B3).
#
# BIND_ADDRESS defaults to [::]:8000 — a dual-stack wildcard. On Linux with the
# default net.ipv6.bindv6only=0 a socket bound to :: also accepts IPv4 clients
# as v4-mapped addresses, so one bind serves both families and no IPv4-specific
# listener is needed. Override it (BIND_ADDRESS=0.0.0.0:8000) on a host where
# IPv6 is compiled out or disabled, otherwise the bind fails with EAFNOSUPPORT.
set -e
case "$1" in
  migrate) exec alembic upgrade head ;;
  serve|"") exec gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker \
              --workers "${WEB_CONCURRENCY:-4}" \
              --bind "${BIND_ADDRESS:-[::]:8000}" --access-logfile - ;;
  *) exec "$@" ;;
esac
