from __future__ import annotations

import asyncio
from typing import Any

from .config import settings

_browser_use: Any = None
_pw: Any = None


def available() -> bool:
    global _browser_use
    if _browser_use is None:
        try:
            import browser_use as bu

            _browser_use = bu
        except Exception:
            _browser_use = False
    return bool(_browser_use)


def require_browser():
    if not available():
        raise SystemExit(
            "browser-use not installed. Run: pip install browser-use && playwright install chromium"
        )


async def _agent(task: str):
    require_browser()
    from browser_use import Agent

    agent = Agent(task=task, headless=settings.headless_browser)
    result = await agent.run()
    return result


def run(task: str) -> str:
    result = asyncio.run(_agent(task))
    return _to_text(result)


def _to_text(result) -> str:
    try:
        history = result.history
        out = []
        for h in history[-6:]:
            m = h.extracted_content()
            if m:
                out.append(str(m)[:400])
        return "\n".join(out) or "no extraction"
    except Exception:
        return str(result)


async def paa_expand(keyword: str, extra_clicks: int = 6) -> list[str]:
    """Open Google, read PAA box, click to expand more questions."""
    from playwright.async_api import async_playwright

    questions: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.headless_browser)
        page = await browser.new_page(
            user_agent=settings.user_agent.replace("SEOForgeBot", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        )
        await page.goto(
            f"https://www.google.com/search?q={keyword}&hl={settings.default_language}",
            timeout=45000,
        )
        try:
            await page.wait_for_selector(
                "jsname:yKCt9D, div.related-question-pair, [data-initq]", timeout=15000
            )
        except Exception:
            await browser.close()
            return questions
        pair_sel = "div.related-question-pair"
        for _ in range(extra_clicks):
            pairs = await page.locator(pair_sel).all()
            n = len(pairs)
            if not n:
                break
            try:
                await page.locator(f"{pair_sel} >> nth={n - 1}").click(timeout=3000)
                await page.wait_for_timeout(700)
            except Exception:
                break
        for loc in await page.locator(pair_sel).all():
            try:
                q = (await loc.inner_text()).strip().split("\n")[0]
                if q and q not in questions:
                    questions.append(q)
            except Exception:
                continue
        await browser.close()
    return questions


FIELD_MAP = {
    "name": ["Brand / product name"],
    "url": ["Website", "URL", "Product link"],
    "description": ["Description", "About your product"],
    "categories": ["Categories", "Primary category"],
    "email": ["Email"],
}


async def submit_directory(
    directory_name: str,
    product_info: dict,
    confirm_resolve=True,
) -> str:
    """Use browser-use to submit the product to a directory from the tracker."""
    require_browser()
    task = f"""Submit our product to the "{directory_name}" startup directory / marketplace.
Product info: {product_info}
Rules:
 - Find the submission form or "Add/Submit/Suggest" page.
 - Fill fields using product_info.
 - If a human verification or login is required, STOP and report.
 - Report the final URL where the submission can be verified, status, and anything left blank.
"""
    result = run(task)
    if settings.confirm_submissions:
        note = "[confirm mode] review this output and hit submit again manually if unsure"
    else:
        note = "submission executed automatically"
    return f"{note}\n{result}"


async def verify_listing(url: str, brand: str) -> dict:
    require_browser()
    task = f"""Open {url}. Check whether a short/OA listing or article mentioning "{brand}" is live.
Return: present=yes/no, dofollow guess, visible text excerpt."""
    res = run(task)
    return {"url": url, "result": res}


def screenshot(url: str, path: str | None = None) -> str:
    from pathlib import Path

    path = path or str(_ss_path(url))
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=settings.headless_browser)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url, timeout=45000)
        page.screenshot(path=path, full_page=True)
        browser.close()
    return path


def _ss_path(url: str):
    from .config import ROOT

    slug = url.split("//")[1].replace("/", "_")[:80]
    return ROOT / "exports" / f"screenshot_{slug}.png"
