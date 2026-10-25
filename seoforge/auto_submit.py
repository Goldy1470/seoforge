"""Batch directory submission engine.

Takes a brand profile (name, URL, descriptions, logo, category, email)
and loops through directories in the tracker, submitting via
the browser agent. Handles failures gracefully, pauses between submissions,
and tracks status in the database.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import backlinks, browser_agent, db
from .config import settings


@dataclass
class BrandProfile:
    name: str
    url: str
    tagline: str = ""
    short_description: str = ""   # ~50 words
    long_description: str = ""    # ~200 words
    category: str = "SaaS"        # SaaS, Agency, E-commerce, AI Tool, etc.
    logo_url: str = ""
    email: str = ""
    twitter: str = ""

    @classmethod
    def from_settings(cls) -> "BrandProfile":
        """Build a standard profile from site settings."""
        site = settings.site_url
        brand = settings.brand_name
        tagline = settings.brand_tagline or f"Next-generation solutions by {brand}"
        short_desc = f"{brand} delivers {tagline}. Designed for performance, reliability, and growth."
        long_desc = (
            f"{brand} is an end-to-end platform specializing in {tagline}. "
            f"Built for modern businesses and founders looking to accelerate growth and streamline operations. "
            f"Learn more and get started at {site}."
        )
        return cls(
            name=brand,
            url=site,
            tagline=tagline,
            short_description=short_desc,
            long_description=long_desc,
        )


def submission_plan(profile: BrandProfile | None = None, min_dr: int = 0, status: str = "todo") -> list[dict]:
    """Returns directory targets sorted by DR descending."""
    backlinks.load_directories()
    return backlinks.list_directories(status=status, min_dr=min_dr)


def submit_one(profile: BrandProfile, directory: dict) -> dict:
    """Submit profile to a single directory."""
    name = directory["name"]
    url = directory.get("url") or ""

    if not browser_agent.available():
        return {
            "name": name,
            "status": "skipped",
            "error": "browser-use / playwright not installed. Run: pip install browser-use && playwright install chromium",
            "url": url,
        }

    product_info = {
        "brand": profile.name,
        "name": profile.name,
        "url": profile.url,
        "tagline": profile.tagline,
        "short_description": profile.short_description,
        "description": profile.long_description,
        "category": profile.category,
        "logo_url": profile.logo_url,
        "email": profile.email,
        "twitter": profile.twitter,
    }

    try:
        output = asyncio.run(browser_agent.submit_directory(name, product_info))
        backlinks.update_status(name, "submitted", f"automated submission: {output[:150]}")
        return {
            "name": name,
            "status": "submitted",
            "output": output[:300],
            "url": url,
        }
    except Exception as exc:
        err_msg = str(exc)[:200]
        backlinks.update_status(name, "todo", f"submission error: {err_msg}")
        return {
            "name": name,
            "status": "failed",
            "error": err_msg,
            "url": url,
        }


def batch_submit(
    profile: BrandProfile | None = None,
    min_dr: int = 0,
    max_per_session: int = 10,
    pause_seconds: int = 15,
) -> dict:
    """Run batch directory submission across todo entries."""
    profile = profile or BrandProfile.from_settings()
    plan = submission_plan(profile, min_dr=min_dr, status="todo")
    to_attempt = plan[:max_per_session]

    results = []
    submitted_count = 0
    failed_count = 0
    skipped_count = 0

    print(f"[auto_submit] Starting batch: {len(to_attempt)} of {len(plan)} eligible directories", flush=True)

    for i, d in enumerate(to_attempt):
        print(f"[auto_submit] ({i+1}/{len(to_attempt)}) Submitting to {d['name']} (DR {d['dr']})...", flush=True)
        r = submit_one(profile, d)
        results.append(r)

        if r["status"] == "submitted":
            submitted_count += 1
        elif r["status"] == "skipped":
            skipped_count += 1
        else:
            failed_count += 1

        if i < len(to_attempt) - 1 and pause_seconds > 0 and r["status"] != "skipped":
            time.sleep(pause_seconds)

    report = {
        "total_eligible_todo": len(plan),
        "attempted": len(to_attempt),
        "submitted": submitted_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "results": results,
    }
    db.save_report("batch_submission", report)
    return report


def submit_status() -> dict:
    """Returns overview of directory status counts and remaining queue."""
    backlinks.load_directories()
    dirs = backlinks.list_directories()
    by_status: dict[str, int] = {}
    for d in dirs:
        s = d.get("status") or "todo"
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "total_directories": len(dirs),
        "by_status": by_status,
        "high_dr_todo": len([d for d in dirs if d.get("status") == "todo" and (d.get("dr") or 0) >= 70]),
        "browser_agent_ready": browser_agent.available(),
    }


def verify_submissions(max_checks: int = 10) -> dict:
    """Verify live status for submitted directories."""
    if not browser_agent.available():
        return {
            "error": "browser-use not installed. Verification requires playwright.",
            "checked": 0,
        }

    submitted = backlinks.list_directories(status="submitted")[:max_checks]
    verified = []
    for d in submitted:
        target_url = d.get("url")
        if not target_url:
            continue
        try:
            res = asyncio.run(browser_agent.verify_listing(target_url, settings.brand_name))
            if "yes" in str(res.get("result", "")).lower():
                backlinks.update_status(d["name"], "live", "auto-verified presence")
                verified.append({"name": d["name"], "status": "live", "url": target_url})
            else:
                verified.append({"name": d["name"], "status": "pending", "url": target_url})
        except Exception as exc:
            verified.append({"name": d["name"], "status": "check_failed", "error": str(exc)[:120]})

    return {"checked": len(submitted), "verified_live": sum(1 for v in verified if v["status"] == "live"), "details": verified}
