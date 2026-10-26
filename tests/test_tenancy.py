"""Engine-side tenancy offline: the ambient context, the per-tenant
engine file landing every `db._conn()` write in its own store, and the
CLI round trips for the directory commands."""
import json
import sqlite3
import sys

import pytest

from seoforge import db, tenancy, tenants
from seoforge.config import settings


def test_the_default_context_is_the_operator_seat():
    assert tenancy.current() is None
    assert tenancy.engine_db_path() == settings.db_path


def test_a_scoped_tenant_runs_in_its_own_file():
    owner_path = settings.db_path
    with tenancy.scoped("Acme!Site") as tid:
        assert tid == "Acme_Site" and tenancy.current() == "Acme_Site"
        tenant_path = tenancy.engine_db_path()
        assert tenant_path == str(
            __import__("pathlib").Path(owner_path).parent
            / "tenants"
            / "Acme_Site"
            / __import__("pathlib").Path(owner_path).name
        )

        with db._conn() as conn:
            conn.execute(
                "INSERT INTO jobs(id, kind, params, status, started_at)"
                " VALUES ('tj1','crawl','{}','running','2026-10-26T00:00:00+00:00')"
            )

        # the write really landed in the tenant's own file
        with db._conn() as conn:
            assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1
        assert sqlite3.connect(tenant_path).execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    # outside the scope: the operator context is restored untouched
    assert tenancy.current() is None
    assert tenancy.engine_db_path() == owner_path


def test_nested_scopes_restore_exactly():
    with tenancy.scoped("Outer"):
        with tenancy.scoped("Inner"):
            assert tenancy.current() == "Inner"
        assert tenancy.current() == "Outer"
    assert tenancy.current() is None


def test_a_hostile_id_flattens_never_walks():
    # dots and separators are erased outright: the id cannot climb or
    # walk - the result is one flat name inside the tenants directory
    assert "/" not in tenancy.flattened("../../etc/passwd")
    assert tenancy.flattened("../../etc") == "etc"
    with tenancy.scoped("../../etc") as tid:
        assert tid == "etc"
        assert "tenants/etc/" in tenancy.engine_db_path().replace("\\", "/")


def run_cli(*args):
    from seoforge import cli

    old_argv = sys.argv
    sys.argv = ["seoforge", *args]
    try:
        cli.main()
    finally:
        sys.argv = old_argv


def test_cli_tenant_round_trip(capsys):
    run_cli("tenants")
    assert json.loads(capsys.readouterr().out) == []  # nothing exists yet

    run_cli("tenant-add", "Acme", "--notes", "pilot")
    made = json.loads(capsys.readouterr().out)
    assert made["plan"] == "customer" and made["notes"] == "pilot"

    # tenant-add seeded the operator seat too; both list oldest-first
    run_cli("tenants")
    assert [t["id"] for t in json.loads(capsys.readouterr().out)] == [
        tenants.DEFAULT_TENANT_ID,
        made["id"],
    ]

    run_cli("tenant-status", made["id"], "past_due")
    assert json.loads(capsys.readouterr().out)["status"] == "past_due"

    run_cli("tenant-plan", made["id"], "gold")
    error = json.loads(capsys.readouterr().out)
    assert error["error"] == "invalid" and "unknown plan" in error["message"]


def test_cli_key_round_trip(capsys):
    run_cli("tenant-add", "Keyed")
    made = json.loads(capsys.readouterr().out)

    run_cli("key-issue", made["id"])
    issued = json.loads(capsys.readouterr().out)
    assert issued["secret"].startswith(tenants.KEY_PREFIX)

    run_cli("keys", made["id"])
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["prefix"] == issued["prefix"]
    assert issued["secret"] not in str(listed)

    run_cli("key-revoke", issued["id"])
    assert json.loads(capsys.readouterr().out)["status"] == "revoked"

    run_cli("key-revoke", issued["id"])
    assert "nothing to revoke" in json.loads(capsys.readouterr().out)["note"]

    # an unknown tenant is answered plainly, never raised through the CLI
    run_cli("key-issue", "t_missing")
    error = json.loads(capsys.readouterr().out)
    assert error["error"] == "invalid" and "unknown tenant" in error["message"]
