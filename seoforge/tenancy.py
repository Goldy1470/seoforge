"""Engine-side tenancy: the ambient tenant the current work runs as.

The default context (no tenant) is the operator's own engine - identical
behaviour to before Phase 7, on the configured db_path. When a tenant is
scoped in (from the served API), every `db._conn()` lands in that
tenant's own SQLite file beside the operator's: no tenant ever shares
rows, WAL files, caps or history with the operator seat or another
tenant.

Threads and scopes: `scoped` works through contextvars, so the served
app can wrap one engine call in a tenant scope without leaking into the
next request."""
from __future__ import annotations

import contextlib
import contextvars
import re
from pathlib import Path

_current: contextvars.ContextVar = contextvars.ContextVar("seoforge_tenant", default=None)

# A tenant id never names a path outside the tenants directory.
_TENANT_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def current() -> str | None:
    return _current.get()


@contextlib.contextmanager
def scoped(tenant_id: str):
    """Run a block as one tenant; whatever was ambient before is restored.
    The tenant id is flattened exactly as it will be used on disk, so
    callers can read it from `current()`."""
    safe = flattened(tenant_id)
    token = _current.set(safe)
    try:
        yield safe
    finally:
        _current.reset(token)


def flattened(tenant_id: str) -> str:
    safe = _TENANT_SAFE.sub("_", str(tenant_id or "")).strip("_")
    return safe or "tenant"


def engine_db_path() -> str:
    """The engine file the ambient context uses: the configured path for
    the operator seat, one file per tenant otherwise."""
    from .config import settings

    base = Path(settings.db_path)
    tid = _current.get()
    if not tid:
        return str(base)
    return str(base.parent / "tenants" / tid / base.name)
