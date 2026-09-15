"""The first-administrator bootstrap.

These pin the property that actually matters operationally: a freshly migrated
database has no identities at all, so without this path a correct deployment is
still unusable - the login form works, there is simply nothing to log in as.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("IPTT_TEST_DB_READY") != "1",
    reason="requires a migrated PostgreSQL database",
)


@pytest.fixture()
def empty_db():
    """A transaction on the real database that is always rolled back, with the
    identity tables emptied inside it. Nothing escapes to the migrated data."""
    from app.db import SessionLocal
    from app.models import AppUser, AuditLog

    db = SessionLocal()
    db.begin_nested() if db.in_transaction() else None
    try:
        db.query(AuditLog).delete(synchronize_session=False)
        db.query(AppUser).delete(synchronize_session=False)
        db.flush()
        yield db
    finally:
        db.rollback()
        db.close()


def _bootstrap_against(db, monkeypatch, **kwargs):
    """Point bootstrap's session factory at the test transaction, and stop it
    committing, so the rollback above still cleans up."""
    import app.bootstrap as mod

    monkeypatch.setattr(mod, "SessionLocal", lambda: _NoCommit(db))
    return mod.bootstrap(**kwargs)


class _NoCommit:
    """Wraps the test session so `with SessionLocal() as db` neither commits
    nor closes the outer transaction."""

    def __init__(self, db):
        self._db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        self._db.flush()

    def __getattr__(self, name):
        return getattr(self._db, name)


def test_a_migrated_database_has_no_users_to_log_in_as(empty_db):
    from app.models import AppUser

    assert empty_db.scalar(select(AppUser.id)) is None


def test_bootstrap_creates_an_admin_that_must_rotate_its_password(
    empty_db, monkeypatch, capsys
):
    from app.models import AppUser
    from app.models.enums import Role
    from app.security import verify_password

    rc = _bootstrap_against(
        empty_db, monkeypatch, username="firstadmin", password="Str0ng!Passw0rd"
    )
    assert rc == 0

    user = empty_db.scalar(select(AppUser).where(AppUser.username == "firstadmin"))
    assert user is not None
    assert user.role == Role.ADMIN
    assert user.is_active is True
    # A shared or generated password must never become the permanent one.
    assert user.must_change_password is True
    assert verify_password("Str0ng!Passw0rd", user.password_hash)
    # The plaintext is never echoed when the caller supplied it.
    assert "Str0ng!Passw0rd" not in capsys.readouterr().out


def test_a_generated_password_satisfies_the_strength_policy(empty_db, monkeypatch):
    """Otherwise the account is created and then cannot change its own
    password - the one thing it is required to do."""
    from app.security import validate_password_strength
    import app.bootstrap as mod

    for _ in range(25):
        assert validate_password_strength(mod._generate_password()) == []


def test_generated_password_is_printed_once_and_works(empty_db, monkeypatch, capsys):
    from app.models import AppUser
    from app.security import verify_password

    assert _bootstrap_against(empty_db, monkeypatch, username="gen", password=None) == 0
    printed = capsys.readouterr().out
    secret = [
        line.split(": ", 1)[1].strip()
        for line in printed.splitlines()
        if "Password (shown once" in line
    ]
    assert len(secret) == 1, printed
    user = empty_db.scalar(select(AppUser).where(AppUser.username == "gen"))
    assert verify_password(secret[0], user.password_hash)


def test_running_twice_changes_nothing(empty_db, monkeypatch):
    from app.models import AppUser

    _bootstrap_against(empty_db, monkeypatch, username="first", password="Str0ng!Pass1")
    before = empty_db.scalar(
        select(AppUser.password_hash).where(AppUser.username == "first")
    )

    rc = _bootstrap_against(empty_db, monkeypatch, username="second", password="Str0ng!Pass2")
    assert rc == 0
    # No second admin, and the first one's credentials are untouched.
    assert empty_db.scalar(select(AppUser.id).where(AppUser.username == "second")) is None
    assert (
        empty_db.scalar(select(AppUser.password_hash).where(AppUser.username == "first"))
        == before
    )


def test_a_weak_supplied_password_is_refused_and_creates_nothing(
    empty_db, monkeypatch, capsys
):
    from app.models import AppUser

    rc = _bootstrap_against(empty_db, monkeypatch, username="weak", password="short")
    assert rc == 1
    assert empty_db.scalar(select(AppUser.id).where(AppUser.username == "weak")) is None


def test_it_refuses_to_adopt_an_existing_non_admin_account(empty_db, monkeypatch):
    """Silently promoting an existing username would be a privilege escalation
    dressed up as convenience."""
    from app.models import AppUser
    from app.models.enums import Role
    from app.security import hash_password

    empty_db.add(
        AppUser(
            username="taken",
            password_hash=hash_password("Str0ng!Existing1"),
            role=Role.VIEWER,
            is_active=True,
        )
    )
    empty_db.flush()

    rc = _bootstrap_against(empty_db, monkeypatch, username="taken", password="Str0ng!New1")
    assert rc == 1
    user = empty_db.scalar(select(AppUser).where(AppUser.username == "taken"))
    assert user.role == Role.VIEWER
