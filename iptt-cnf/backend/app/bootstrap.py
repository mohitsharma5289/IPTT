"""Create the first administrator.

Nothing else can. `POST /api/users` requires an existing admin, self-service
registration is disabled by default, and the Alembic migrations deliberately
seed only *reference* data - stages, the task-stage map, capacity rules,
holidays - never identities. A database that has been migrated but never had
the legacy ETL run against it therefore has an empty `app_user` table and no
way in: the login form is correct, the credentials simply do not exist.

That gap only shows up on a fresh deployment, which is exactly when it is most
expensive to discover.

Run it as:

    python -m app.bootstrap                    # generates a password, prints it
    BOOTSTRAP_ADMIN_PASSWORD=... python -m app.bootstrap
    ./entrypoint.sh bootstrap                  # the same thing, in the image

Safe to run on every boot: if an active administrator already exists it changes
nothing and exits 0. It never resets an existing account's password - use the
admin UI, or `POST /api/users/{id}/reset-password`, for that.

The account it creates is always flagged `must_change_password`, so a generated
or shared password cannot quietly become the permanent one.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AppUser, AuditLog
from app.models.enums import Role
from app.security import hash_password, validate_password_strength

GENERATED_PASSWORD_BYTES = 12


def _generate_password() -> str:
    """A generated password must itself satisfy the strength policy, or the
    account would be created and then be unable to change its own password."""
    while True:
        candidate = secrets.token_urlsafe(GENERATED_PASSWORD_BYTES) + "aA1!"
        if not validate_password_strength(candidate):
            return candidate


def bootstrap(username: str, password: str | None, role: str = Role.ADMIN) -> int:
    with SessionLocal() as db:
        existing_admin = db.scalar(
            select(AppUser.username).where(
                AppUser.role == Role.ADMIN, AppUser.is_active.is_(True)
            )
        )
        if existing_admin:
            print(
                f"An active administrator already exists ('{existing_admin}'). "
                "Nothing to do."
            )
            return 0

        if db.scalar(select(AppUser.id).where(AppUser.username == username)):
            print(
                f"A user named '{username}' already exists but is not an active "
                "administrator. Refusing to touch it - pick another --username, "
                "or reactivate that account directly in the database.",
                file=sys.stderr,
            )
            return 1

        generated = password is None
        if generated:
            password = _generate_password()
        else:
            problems = validate_password_strength(password)
            if problems:
                print("Password " + "; ".join(problems), file=sys.stderr)
                return 1

        user = AppUser(
            username=username,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        db.add(
            AuditLog(
                actor_user_id=user.id,
                actor_username=username,
                actor_role=role,
                action="USER_CREATE",
                source="bootstrap",
                field="username",
                old_value=None,
                new_value=username,
                task_name=username,
            )
        )
        db.commit()

        print("-" * 62)
        print(f"  Created the first administrator: {username}")
        if generated:
            print(f"  Password (shown once, not stored anywhere): {password}")
        else:
            print("  Password: as supplied in BOOTSTRAP_ADMIN_PASSWORD")
        print("  It must be changed at first sign-in.")
        print("-" * 62)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--username",
        default=os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin"),
        help="administrator username (default: admin, or BOOTSTRAP_ADMIN_USERNAME)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("BOOTSTRAP_ADMIN_PASSWORD"),
        help="password; generated and printed once if omitted",
    )
    args = parser.parse_args()
    return bootstrap(args.username.strip(), args.password)


if __name__ == "__main__":
    raise SystemExit(main())
