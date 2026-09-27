"""The operator CLI provisions and recovers platform accounts without ever
exposing a password in arguments or logs."""

import pytest
from sqlalchemy import select

from app.cli import platform_admin as cli
from app.models import PlatformAdmin
from app.security import verify_password
from conftest import TestingSession


def _passwords(monkeypatch, *values):
    remaining = list(values)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": remaining.pop(0))


def test_create_builds_one_admin_and_refuses_a_duplicate(client, monkeypatch):
    _passwords(monkeypatch, "secret-one", "secret-one")
    cli.create("Platform owner", "owner@platform.test")
    with TestingSession() as db:
        admin = db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == "owner@platform.test"))
        assert admin is not None and verify_password("secret-one", admin.password_hash)
    with pytest.raises(SystemExit):
        cli.create("Again", "owner@platform.test")


def test_create_rejects_mismatched_passwords(client, monkeypatch):
    _passwords(monkeypatch, "first", "second")
    with pytest.raises(SystemExit):
        cli.create("Platform owner", "owner@platform.test")
    with TestingSession() as db:
        assert db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == "owner@platform.test")) is None


def test_disable_and_reset_password_revoke_sessions(client, monkeypatch):
    _passwords(monkeypatch, "secret-one", "secret-one")
    cli.create("Platform owner", "owner@platform.test")
    with TestingSession() as db:
        admin = db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == "owner@platform.test"))
        first_version = admin.session_version

    cli.disable("owner@platform.test")
    with TestingSession() as db:
        admin = db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == "owner@platform.test"))
        assert admin.is_active is False
        assert admin.session_version == first_version + 1

    _passwords(monkeypatch, "new-secret", "new-secret")
    cli.reset_password("owner@platform.test")
    with TestingSession() as db:
        admin = db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == "owner@platform.test"))
        assert verify_password("new-secret", admin.password_hash)
        assert admin.session_version == first_version + 2

    with pytest.raises(SystemExit):
        cli.disable("nobody@platform.test")
