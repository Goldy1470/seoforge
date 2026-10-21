from __future__ import annotations

import base64
import datetime as _dt
import json
from pathlib import Path

import httpx

from .config import settings

API = "https://searchconsole.googleapis.com"
_TOKEN_PATH = Path(settings.db_path).with_name("gsc_token.json")


def _date_range(days: int) -> dict:
    end = _dt.date.today() - _dt.timedelta(days=3)
    start = end - _dt.timedelta(days=days)
    return {"startDate": start.isoformat(), "endDate": end.isoformat()}


def run_oauth_flow() -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [
        "https://www.googleapis.com/auth/webmasters.readonly",
        "https://www.googleapis.com/auth/webmasters",
    ]
    flow = InstalledAppFlow.from_client_secrets_file(settings.gsc_credentials, scopes)
    creds = flow.run_local_server(port=0)
    _TOKEN_PATH.write_text(creds.to_json())
    return "token saved: " + str(_TOKEN_PATH)


def _access_token() -> str:
    if not _TOKEN_PATH.exists():
        raise SystemExit(
            "No GSC token. Put your OAuth client secret in "
            f"{settings.gsc_credentials} then run: python -m seoforge.cli gsc-auth"
        )
    info = json.loads(_TOKEN_PATH.read_text())
    if info.get("expiry", "") >= _dt.datetime.utcnow().isoformat():
        return info["token"]
    import google.auth.transport.requests as _rq

    creds = Credentials_from_json(info)
    creds.refresh(_rq.Request())
    _TOKEN_PATH.write_text(creds.to_json())
    return creds.token


def Credentials_from_json(info: dict):
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=info.get("token"),
        refresh_token=info.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=info.get("client_id"),
        client_secret=info.get("client_secret"),
        scopes=info.get("scopes"),
    )


def query(
    dims: list[str],
    days: int | None = None,
    row_limit: int = 25000,
    data_state: str = "all",
    extra: dict | None = None,
    property_url: str | None = None,
) -> list[dict]:
    prop = property_url or settings.gsc_property
    if not prop:
        raise SystemExit("Set GSC_PROPERTY (e.g. https://highprimeco.com/ or sc-domain:highprimeco.com)")
    body = {
        **_date_range(days or settings.gsc_days),
        "dimensions": dims,
        "rowLimit": row_limit,
        "dataState": data_state,
        **(extra or {}),
    }
    r = httpx.post(
        f"{API}/webmasters/v3/sites/{_quote(prop)}/searchAnalytics/query",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("rows", [])


def _quote(prop: str) -> str:
    return base64.urlsafe_b64encode(prop.encode()).decode()


def striking_distance(lo: int = 5, hi: int = 20, min_impressions: int = 50) -> list[dict]:
    rows = query(["query", "page"])
    return sorted(
        [
            {
                "query": r["keys"][0],
                "page": r["keys"][1],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"] * 100, 2),
                "position": round(r["position"], 1),
            }
            for r in rows
            if lo <= r["position"] <= hi and r["impressions"] >= min_impressions
        ],
        key=lambda x: (x["impressions"] * x["position"]),
        reverse=True,
    )


def cannibalization() -> list[dict]:
    rows = query(["query", "page"])
    by_query: dict[str, list] = {}
    for r in rows:
        by_query.setdefault(r["keys"][0], []).append(
            {"page": r["keys"][1], "clicks": r["clicks"], "position": round(r["position"], 1)}
        )
    out = []
    for q, pages in by_query.items():
        pages = [p for p in pages if p["clicks"] > 0]
        if len(pages) > 1:
            out.append({"query": q, "pages": sorted(pages, key=lambda p: -p["clicks"])})
    return sorted(out, key=lambda x: sum(p["clicks"] for p in x["pages"]), reverse=True)


def low_ctr(min_impressions: int = 500, max_ctr: float = 0.02) -> list[dict]:
    rows = query(["query", "page"])
    return [
        {
            "query": r["keys"][0],
            "page": r["keys"][1],
            "impressions": r["impressions"],
            "ctr_pct": round(r["ctr"] * 100, 2),
            "position": round(r["position"], 1),
        }
        for r in rows
        if r["impressions"] >= min_impressions and r["ctr"] < max_ctr
    ]


def inspect_url(url: str) -> dict:
    """URL Indexing API (urlInspection) - indexing state, coverage, sitemaps."""
    prop = settings.gsc_property or "sc-domain:" + url.split("/")[2]
    prop = prop if "sc-domain" in settings.gsc_property else urljoin_prop(prop)
    r = httpx.post(
        f"{API}/webmasters/v3/urlInspection/index:inspect",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={
            "inspectionUrl": url,
            "siteUrl": prop,
            "languageCode": settings.default_language,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def urljoin_prop(prop: str) -> str:
    from urllib.parse import urlsplit

    p = urlsplit(prop)
    return f"sc-domain:{p.netloc.lower().removeprefix('www.')}"
