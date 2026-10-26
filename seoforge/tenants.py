"""The tenant kernel: who gets in, what the plan allows, what was used
this month. This is the billing gate - the engine's data plane stays in
each tenant's own store (seoforge/tenancy.py); the directory only knows
identity, plans and metering.

API keys follow the bot connections' secret discipline: the full secret
is shown exactly once at issue time, and only its SHA-256 is stored."""
from __future__ import annotations

import hashlib
import secrets
import uuid

from .store import directory, now

KEY_PREFIX = "sfk_"
DEFAULT_TENANT_ID = "owner"

STATUSES = ("active", "trialing", "past_due", "churned", "suspended")
GATE_OPEN = {"active", "trialing"}

# The metering vocabulary: the operator seat is unmetered; 'customer'
# carries one monthly request budget. Real tier names and budgets are a
# pricing decision (the strategy doc), not a data-model one - this dict
# is the single place to edit.
PLAN_LIMITS = {
    "owner": {"monthly_requests": None},
    "customer": {"monthly_requests": 10000},
}

_TENANT_COLS = "id, name, plan, status, created_at, notes"


def _hash(secret: str) -> str:
    """Same discipline as the bot connections' once-shown secrets."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def ensure_owner() -> dict:
    """The operator seat: one stable row, 'owner' plan, unmetered."""
    row = directory().query_one(
        f"SELECT {_TENANT_COLS} FROM tenants WHERE id = ?", (DEFAULT_TENANT_ID,)
    )
    if row is not None:
        return row
    directory().execute(
        "INSERT INTO tenants(id, name, plan, status, created_at, notes)"
        " VALUES (?,?,?,?,?,?)",
        (DEFAULT_TENANT_ID, "Operator", "owner", "active", now(), ""),
    )
    return get_tenant(DEFAULT_TENANT_ID)


def create_tenant(
    name: str, plan: str = "customer", status: str = "active", notes: str = ""
) -> dict:
    if plan not in PLAN_LIMITS:
        raise ValueError(f"unknown plan {plan!r}: expect one of {sorted(PLAN_LIMITS)}")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}: expect one of {STATUSES}")
    tenant_id = f"t_{uuid.uuid4().hex[:12]}"
    directory().execute(
        "INSERT INTO tenants(id, name, plan, status, created_at, notes)"
        " VALUES (?,?,?,?,?,?)",
        (tenant_id, str(name or "").strip() or "tenant", plan, status, now(), str(notes)),
    )
    return get_tenant(tenant_id)


def get_tenant(tenant_id: str) -> dict | None:
    return directory().query_one(
        f"SELECT {_TENANT_COLS} FROM tenants WHERE id = ?", (str(tenant_id),)
    )


def list_tenants() -> list[dict]:
    return directory().query(f"SELECT {_TENANT_COLS} FROM tenants ORDER BY created_at, id")


def set_status(tenant_id: str, status: str) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}: expect one of {STATUSES}")
    directory().execute(
        "UPDATE tenants SET status = ? WHERE id = ?", (status, str(tenant_id))
    )
    return get_tenant(tenant_id)


def set_plan(tenant_id: str, plan: str) -> dict | None:
    if plan not in PLAN_LIMITS:
        raise ValueError(f"unknown plan {plan!r}: expect one of {sorted(PLAN_LIMITS)}")
    directory().execute(
        "UPDATE tenants SET plan = ? WHERE id = ?", (plan, str(tenant_id))
    )
    return get_tenant(tenant_id)


# ---------------------------------------------------------------- keys


def issue_key(tenant_id: str) -> dict:
    """Issue one API key: the `secret` in the answer is shown exactly
    once - after this call only its SHA-256 exists in the directory."""
    tenant = get_tenant(tenant_id)
    if tenant is None:
        raise ValueError(f"unknown tenant {tenant_id!r}")
    secret = KEY_PREFIX + secrets.token_urlsafe(32)
    key_id = f"k_{uuid.uuid4().hex[:12]}"
    directory().execute(
        "INSERT INTO api_keys(id, tenant_id, prefix, key_hash, status, created_at)"
        " VALUES (?,?,?,?, 'active', ?)",
        (key_id, tenant_id, secret[:9], _hash(secret), now()),
    )
    return {
        "id": key_id,
        "tenant_id": tenant_id,
        "prefix": secret[:9],
        "status": "active",
        "created_at": now(),
        "secret": secret,
    }


def list_keys(tenant_id: str) -> list[dict]:
    """A tenant's keys with prefixes only - secrets and hashes never list."""
    return directory().query(
        "SELECT id, tenant_id, prefix, status, created_at, revoked_at"
        " FROM api_keys WHERE tenant_id = ? ORDER BY created_at, id",
        (str(tenant_id),),
    )


def revoke_key(key_id: str) -> dict | None:
    key_id = str(key_id)
    row = directory().query_one(
        "SELECT id, tenant_id, prefix, status FROM api_keys WHERE id = ?", (key_id,)
    )
    if row is None or row["status"] != "active":
        return None
    directory().execute(
        "UPDATE api_keys SET status = 'revoked', revoked_at = ? WHERE id = ?",
        (now(), key_id),
    )
    return {**row, "status": "revoked", "revoked_at": now()}


def authenticate(secret: str) -> dict | None:
    """The active key's tenant, flattened with its key facts; None is an
    honest unknown/wrong/revoked - never an invented tenant."""
    if not secret:
        return None
    return directory().query_one(
        "SELECT k.id AS key_id, k.prefix AS key_prefix, t.id AS tenant_id,"
        " t.name AS tenant_name, t.plan AS plan, t.status AS status"
        " FROM api_keys k JOIN tenants t ON t.id = k.tenant_id"
        " WHERE k.key_hash = ? AND k.status = 'active'",
        (_hash(str(secret)),),
    )


# ---------------------------------------------------------------- gate


def plan_limits(plan: str) -> dict:
    return dict(PLAN_LIMITS.get(plan, {}))


def gate(tenant: dict | None) -> dict:
    """The plan gate: active and trialing open the door; past_due,
    churned and suspended answer closed with the reason."""
    status = str((tenant or {}).get("status") or "")
    if status in GATE_OPEN:
        return {"gate": "open", "status": status}
    return {"gate": "closed", "reason": status or "unknown", "status": status}


# --------------------------------------------------------------- meter


def meter(tenant_id: str, metric: str = "requests", value: int = 1, month: str | None = None) -> dict:
    """Add to the month's counter (UPDATE-then-INSERT, so the same SQL
    answers on both dialects and keys up cleanly)."""
    month = month or now()[:7]
    d = directory()
    updated = d.execute(
        "UPDATE usage SET value = value + ? WHERE tenant_id = ? AND month = ? AND metric = ?",
        (int(value), tenant_id, month, metric),
    )
    if not updated:
        d.execute(
            "INSERT INTO usage(tenant_id, month, metric, value) VALUES (?,?,?,?)",
            (tenant_id, month, metric, int(value)),
        )
    return usage(tenant_id, month)


def usage(tenant_id: str, month: str | None = None) -> dict:
    month = month or now()[:7]
    rows = directory().query(
        "SELECT metric, value FROM usage WHERE tenant_id = ? AND month = ?",
        (tenant_id, month),
    )
    return {"month": month, **{r["metric"]: r["value"] for r in rows}}


def remaining(tenant: dict) -> int | None:
    """How many request-units the plan still has this month; None is the
    unmetered operator seat."""
    limit = plan_limits(tenant.get("plan", "")).get("monthly_requests")
    if limit is None:
        return None
    used = usage(tenant["id"]).get("requests", 0)
    return max(int(limit) - int(used), 0)


def over_quota(tenant: dict) -> bool:
    r = remaining(tenant)
    return r is not None and r <= 0
