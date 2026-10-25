"""CMS and Webhook Auto-Publisher for SEOForge.

Enables 1-click automated deployment of generated articles, schemas, and AI discovery files:
  - WordPress REST API: Posts articles, sets SEO titles & meta descriptions.
  - GitHub / Local Repo: Commits llms.txt, llms-full.txt, and robots.txt for Next.js/Jamstack auto-deploys.
  - Webhooks (Zapier, Make, n8n): Pushes full article payloads and fix bundles to external workflows.
  - Shopify Admin API: Updates product descriptions and SEO tags.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx

from . import db
from .config import ROOT, settings


def publish_to_webhook(url: str, payload: dict) -> dict:
    """Send payload to a custom webhook (Zapier, Make, n8n, Slack)."""
    try:
        r = httpx.post(url, json=payload, timeout=30)
        return {
            "status": "success" if r.status_code in (200, 201, 202) else "http_error",
            "status_code": r.status_code,
            "response": r.text[:200],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200]}


def publish_to_wordpress(
    title: str,
    content: str,
    status: str = "draft",  # 'draft' or 'publish'
    wp_url: str | None = None,
    username: str | None = None,
    app_password: str | None = None,
) -> dict:
    """Publish an article to a WordPress site using WordPress REST API application passwords."""
    base_url = (wp_url or os.getenv("WP_URL", "")).rstrip("/")
    user = username or os.getenv("WP_USER", "")
    pw = app_password or os.getenv("WP_APP_PASSWORD", "")

    if not (base_url and user and pw):
        return {
            "status": "config_missing",
            "error": "WP_URL, WP_USER, and WP_APP_PASSWORD must be configured in environment or arguments.",
        }

    token = base64.b64encode(f"{user}:{pw}".encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }

    endpoint = f"{base_url}/wp-json/wp/v2/posts"
    body = {
        "title": title,
        "content": content,
        "status": status,
    }

    try:
        r = httpx.post(endpoint, headers=headers, json=body, timeout=45)
        if r.status_code in (200, 201):
            data = r.json()
            return {
                "status": "success",
                "post_id": data.get("id"),
                "post_link": data.get("link"),
                "post_status": data.get("status"),
            }
        return {"status": "error", "status_code": r.status_code, "response": r.text[:300]}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200]}


def deploy_local_repo(repo_path: str | None = None) -> dict:
    """Copies generated llms.txt, llms-full.txt, and robots.txt to a local Next.js / Astro public folder."""
    dest = Path(repo_path or os.getenv("LOCAL_REPO_PUBLIC_DIR", ""))
    if not dest.exists():
        return {
            "status": "skipped",
            "message": "LOCAL_REPO_PUBLIC_DIR not configured or path does not exist.",
        }

    from . import aeo, fix_bundle, geo

    copied = []
    # 1. llms.txt
    llms = geo.llms_txt_from_crawl()
    (dest / "llms.txt").write_text(llms, encoding="utf-8")
    copied.append("llms.txt")

    # 2. llms-full.txt
    llms_full = aeo.llms_full_txt_from_crawl()
    (dest / "llms-full.txt").write_text(llms_full, encoding="utf-8")
    copied.append("llms-full.txt")

    # 3. robots.txt
    robots = fix_bundle.robots_suggested()
    (dest / "robots.txt").write_text(robots, encoding="utf-8")
    copied.append("robots.txt")

    return {
        "status": "success",
        "destination": str(dest),
        "files_deployed": copied,
    }
