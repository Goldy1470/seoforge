"""The tenant data plane offline: Bearer-key auth against the directory,
the plan gate and the monthly quota answered at the wire, and one engine
run scoped into the tenant's own file. The operator dashboard routes
stay open exactly as they were (same-process, local tool)."""
import pytest
from fastapi.testclient import TestClient

from seoforge import dashboard, tenants
from seoforge import db as seoforge_db


@pytest.fixture
def client():
    return TestClient(dashboard.app)


def _issue(name: str):
    tenant = tenants.create_tenant(name)
    key = tenants.issue_key(tenant["id"])
    return tenant, key


def test_the_data_plane_requires_a_bearer_key(client):
    assert client.get("/api/v1/whoami").status_code == 401
    assert (
        client.get("/api/v1/whoami", headers={"Authorization": "Bearer sfk_wrong"}).status_code
        == 401
    )
    assert (
        client.get("/api/v1/whoami", headers={"Authorization": "Basic abc"}).status_code == 401
    )


def test_whoami_answers_identity_plan_and_quota(client):
    tenant, key = _issue("Acme")
    tenants.meter(tenant["id"], "requests", 3)

    r = client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {key['secret']}"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == tenant["id"]
    assert body["name"] == "Acme"
    assert body["plan"] == "customer" and body["status"] == "active"
    assert body["limits"] == {"monthly_requests": 10000}
    assert body["usage"]["requests"] == 3
    assert body["remaining"] == 10000 - 3

    # whoami is the health check: it never meters itself
    r = client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {key['secret']}"})
    assert r.json()["usage"]["requests"] == 3


def test_a_closed_plan_gates_the_whole_data_plane(client):
    tenant, key = _issue("Gated")
    tenants.set_status(tenant["id"], "churned")
    r = client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {key['secret']}"})
    assert r.status_code == 402
    body = r.json()
    assert body["reason"] == "churned" and "gates engine work" in body["message"]


def test_a_spent_quota_answers_429_at_the_wire(client, monkeypatch):
    tenant, key = _issue("Quota")
    monkeypatch.setattr(
        tenants,
        "PLAN_LIMITS",
        {"owner": {"monthly_requests": None}, "customer": {"monthly_requests": 0}},
    )
    r = client.post(
        "/api/v1/run/opps", headers={"Authorization": f"Bearer {key['secret']}"}
    )
    assert r.status_code == 429
    assert r.json()["error"] == "quota_exhausted"


def test_run_is_allow_listed_and_meters_success(client, monkeypatch):
    from seoforge import integrations

    tenant, key = _issue("Runner")

    r = client.post(
        "/api/v1/run/nope", headers={"Authorization": f"Bearer {key['secret']}"}
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unknown"

    seen = {}

    def fake_recompute(persist=False):
        seen["persist"] = persist
        return {"made": 1}

    monkeypatch.setattr(integrations, "recompute", fake_recompute)
    r = client.post(
        "/api/v1/run/opps", headers={"Authorization": f"Bearer {key['secret']}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["tenant_id"] == tenant["id"]
    assert body["result"] == {"made": 1}
    assert seen["persist"] is True
    assert tenants.usage(tenant["id"])["requests"] == 1


def test_the_engine_run_happens_in_the_tenants_own_file(client, monkeypatch):
    from seoforge import integrations
    from seoforge.config import settings

    tenant, key = _issue("Scanner")
    landed = {}

    def fake_recompute(persist=False):
        from seoforge import db, tenancy

        landed["path"] = tenancy.engine_db_path()
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO jobs(id,kind,params,status,started_at)"
                " VALUES ('v1j','opps','{}','done','2026-10-26T00:00:00+00:00')"
            )
        return {"inside": True}

    monkeypatch.setattr(integrations, "recompute", fake_recompute)
    r = client.post(
        "/api/v1/run/opps", headers={"Authorization": f"Bearer {key['secret']}"}
    )
    assert r.status_code == 200
    assert landed["path"] != settings.db_path
    assert "tenants" in landed["path"]

    # the job row lives in the tenant's store only
    with seoforge_db._conn() as conn:  # operator scope: the contextvar is empty here
        assert conn.execute("SELECT COUNT(*) c FROM jobs WHERE id='v1j'").fetchone()["c"] == 0


def test_the_operator_dashboard_stays_open(client):
    r = client.get("/")
    assert r.status_code == 200  # local tool: no key required
