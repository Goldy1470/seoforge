"""The tenant directory offline: SQLite is the suite's backend, so every
test here exercises the store, the kernel's gate and meter rules, and
the shown-once key discipline. Postgres answers are checked as
translation + honest-offline-error, never a live connection."""
import sys

import pytest

from seoforge import store, tenants

DDL_TABLES = {"tenants", "api_keys", "usage"}


def test_directory_ddl_is_idempotent():
    store.directory().migrate()
    store.directory().migrate()  # re-run changes nothing
    with store.Directory.open() as d:
        got = {r["name"] for r in d.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert DDL_TABLES <= got


def test_ensure_owner_is_one_stable_row():
    first = tenants.ensure_owner()
    assert first["id"] == tenants.DEFAULT_TENANT_ID
    assert first["plan"] == "owner" and first["status"] == "active"
    assert tenants.ensure_owner() == first


def test_create_get_list_round_trip():
    owner = tenants.ensure_owner()
    made = tenants.create_tenant("Acme Agency", plan="customer", notes="pilot")
    assert made["id"] and made["id"] != owner["id"]
    assert made["name"] == "Acme Agency" and made["plan"] == "customer"
    assert made["status"] == "active"

    assert tenants.get_tenant(made["id"]) == made
    listed = tenants.list_tenants()
    assert [t["id"] for t in listed] == [tenants.DEFAULT_TENANT_ID, made["id"]]


def test_status_and_plan_vocabulary_reject_unknowns():
    made = tenants.create_tenant("Vocab Tenant")
    with pytest.raises(ValueError):
        tenants.set_status(made["id"], "frozen")
    with pytest.raises(ValueError):
        tenants.set_plan(made["id"], "gold")
    with pytest.raises(ValueError):
        tenants.create_tenant("Bad", plan="gold")


def test_issue_key_shows_the_secret_exactly_once():
    made = tenants.create_tenant("Keyed Tenant")
    issued = tenants.issue_key(made["id"])

    assert issued["secret"].startswith(tenants.KEY_PREFIX)
    assert issued["prefix"] == issued["secret"][:9]

    # the list carries prefixes only: the secret and its hash never list
    listed = tenants.list_keys(made["id"])
    assert len(listed) == 1
    assert set(listed[0]) == {"id", "tenant_id", "prefix", "status", "created_at", "revoked_at"}
    assert issued["secret"] not in str(listed)
    assert issued["prefix"] == listed[0]["prefix"]

    # the shown secret authenticates, flattening tenant identity in
    who = tenants.authenticate(issued["secret"])
    assert who["tenant_id"] == made["id"] and who["plan"] == "customer"
    assert who["key_id"] == issued["id"]


def test_authenticate_answers_none_for_the_honest_no_paths():
    made = tenants.create_tenant("No-K Tenant")
    issued = tenants.issue_key(made["id"])
    assert tenants.authenticate("") is None
    assert tenants.authenticate("sfk_garbage") is None
    assert tenants.authenticate(f"sfk_wrong_but_reallooking_{made['id']}") is None

    tenants.revoke_key(issued["id"])
    assert tenants.authenticate(issued["secret"]) is None


def test_revoke_is_an_honest_none_when_there_is_nothing_to_revoke():
    assert tenants.revoke_key("k_missing") is None
    made = tenants.create_tenant("Revoke Tenant")
    issued = tenants.issue_key(made["id"])
    assert tenants.revoke_key(issued["id"])["status"] == "revoked"
    assert tenants.revoke_key(issued["id"]) is None  # already revoked


def _table_counts(d):
    got = {}
    for table in sorted(DDL_TABLES):
        got[table] = d.query_one(f"SELECT COUNT(*) AS c FROM {table}")["c"]
    return got


def test_gate_open_only_while_active_or_trialing():
    made = tenants.create_tenant("Gate Tenant")
    assert tenants.gate(tenants.get_tenant(made["id"])) == {"gate": "open", "status": "active"}
    tenants.set_status(made["id"], "trialing")
    assert tenants.gate(tenants.get_tenant(made["id"]))["gate"] == "open"
    for status in ("past_due", "churned", "suspended"):
        tenants.set_status(made["id"], status)
        gated = tenants.gate(tenants.get_tenant(made["id"]))
        assert gated["gate"] == "closed" and gated["reason"] == status
    assert tenants.gate(None) == {"gate": "closed", "reason": "unknown", "status": ""}


def test_meter_upserts_one_row_per_metric_and_month():
    made = tenants.create_tenant("Metered Tenant")
    month = "2026-10"

    after = tenants.meter(made["id"], "requests", 1, month=month)
    tenants.meter(made["id"], "requests", 2, month=month)
    after = tenants.meter(made["id"], "requests", 4, month=month)
    assert after == {"month": month, "requests": 7}

    tenants.meter(made["id"], "pages", 3, month=month)
    assert tenants.usage(made["id"], month) == {"month": month, "requests": 7, "pages": 3}

    with store.directory() as d:
        assert _table_counts(d)["usage"] == 2  # upserts, never a new row


def test_the_customer_quota_gate_counts_the_month():
    made = tenants.create_tenant("Quota Tenant", plan="customer")
    limits = {
        "owner": {"monthly_requests": None},
        "customer": {"monthly_requests": 2},
    }
    real_limits = tenants.PLAN_LIMITS
    tenants.PLAN_LIMITS = limits
    try:
        assert tenants.over_quota(tenants.get_tenant(made["id"])) is False
        tenants.meter(made["id"], "requests", 2)
        row = tenants.get_tenant(made["id"])
        assert tenants.remaining(row) == 0
        assert tenants.over_quota(row) is True
    finally:
        tenants.PLAN_LIMITS = real_limits


def test_the_owner_seat_is_unmetered():
    owner = tenants.ensure_owner()
    assert tenants.remaining(owner) is None
    assert tenants.over_quota(owner) is False
    assert tenants.plan_limits("owner") == {"monthly_requests": None}


def test_placeholder_translation_counts_and_maps_every_slot():
    d = store.Directory("postgresql://exampleuser@examplehost/exampledb")
    assert d.backend == "postgres"
    sql = d._translate("SELECT * FROM tenants WHERE id = ? AND plan = ?")
    assert sql == "SELECT * FROM tenants WHERE id = %s AND plan = %s"
    plain = store.Directory("C:/no/place/tests.db")
    assert plain.backend == "sqlite" and plain._translate("a = ?") == "a = ?"


def test_a_missing_postgres_driver_answers_honestly(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    with pytest.raises(RuntimeError) as err:
        store.Directory.open("postgresql://u:p@examplehost/d")
    assert "postgres driver" in str(err.value)
