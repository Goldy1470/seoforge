from __future__ import annotations

import os

from .config import settings

HOSTED_MCP = "https://app.openseo.so/mcp"
HOSTED_APP = "https://app.openseo.so"
SELFHOST_GUIDE = "https://github.com/every-app/open-seo/blob/main/docs/SELF_HOSTING_CLOUDFLARE.md"
DOCKER_GUIDE = "https://github.com/every-app/open-seo/blob/main/docs/SELF_HOSTING_DOCKER.md"
AI_VIS_GUIDE = "https://openseo.so/docs/ai-visibility"

SKILLS = [
    ("seo-project-setup", "save goals, positioning, competitors, key pages into the project context"),
    ("seo-coach", "asks what you need before proposing a workflow - run first"),
    ("seo-audit", "their crawler audit - complements seoforge.crawl_site with their own rules"),
    ("ai-visibility-audit", "why competitors appear in LLM answers and what to change"),
    ("ai-prompt-research", "questions asked of AI models + which sources get cited"),
    ("keyword-research", "volume/difficulty/CPC keywords - feeds seoforge PAA build list"),
    ("keyword-clustering", "keywords -> page groups (use as input to question_page_plan)"),
    ("competitive-landscape", "map a market before competitors are chosen"),
    ("competitor-analysis", "single competitor deep dive"),
    ("local-seo", "Google Business Profile, Maps rank grid, reviews"),
    ("link-prospecting", "outreach prospects for a linkable asset"),
    ("seo-report", "report templates saved onto the project"),
]


def mode() -> dict:
    api_key = settings.openseo_api_key or os.getenv("OPENSEO_API_KEY", "")
    selfhost = settings.openseo_url
    if selfhost:
        return {"mode": "self-hosted", "mcp_url": selfhost.rstrip("/") + "/mcp", "key_set": bool(api_key)}
    if api_key:
        return {"mode": "hosted", "mcp_url": HOSTED_MCP, "key_set": True}
    return {"mode": "not-configured", "mcp_url": HOSTED_MCP, "key_set": False}


def status() -> dict:
    m = mode()
    m["app"] = HOSTED_APP
    m["note"] = {
        "not-configured": "run openseo_setup() - codex attaches their plugin in ~2 min, or set OPENSEO_API_KEY for headless use",
        "hosted": "MCP URL + Bearer key configured; Codex auto-loads their tools next session",
        "self-hosted": "your own URL overrides hosted; per docs deploy is Cloudflare Workers (D1/KV/R2) or Docker",
    }[m["mode"]]
    m["credits"] = (
        "hosted adds 28% over raw DataForSEO; self-host pays DataForSEO directly; "
        "seoforge's own DataForSEO calls avoid that fee entirely"
    )
    return m


def setup_codex(include_skills: bool = True) -> dict:
    """Paste-ready commands, per openseo.so/docs/codex-plugin and /docs/mcp."""
    m = mode()
    if m["mode"] == "hosted-with-key" or m["key_set"]:
        base = [
            f'set OPENSEO_API_KEY={settings.openseo_api_key or "oseo_YOUR_KEY"}',
            f"codex mcp add openseo --url {HOSTED_MCP} --bearer-token-env-var OPENSEO_API_KEY",
        ]
    else:
        base = [
            "codex plugin marketplace add every-app/open-seo",
            "codex plugin add openseo@openseo",
            "codex mcp login openseo",
        ]
    out = {"codex_cli": base, "desktop_app": [
        "Codex -> Settings -> MCP servers -> Add server",
        f'name=openseo, transport=Streamable HTTP, url={HOSTED_MCP}',
        "Save -> Restart -> Authenticate -> approve OpenSEO sign-in (Codex >= 0.147.0 for OAuth)",
    ]}
    if include_skills:
        out["skills"] = [
            f"run in Codex: '{n}' - {d}" for n, d in SKILLS
            if n in ("seo-project-setup", "seo-coach", "ai-visibility-audit", "keyword-research")
        ]
    out["verify_in_codex"] = 'ask Codex: "Use OpenSEO to check my connection and list my projects."'
    return out


def other_clients() -> dict:
    return {
        "claude_code": f'claude mcp add --transport http --scope user openseo {HOSTED_MCP}',
        "claude_with_key": f'claude mcp add --transport http --scope user openseo {HOSTED_MCP} '
        ' --header "Authorization: Bearer oseo_YOUR_KEY"',
        "cursor": {"mcpServers": {"openseo": {"url": HOSTED_MCP, "headers": {"Authorization": "Bearer oseo_YOUR_KEY"}}}},
        "plugin_installs": {
            "codex": "codex plugin marketplace add every-app/open-seo -> codex plugin add openseo@openseo",
            "chatgpt": "ChatGPT Plugin directory: OpenSEO (adds MCP + skills)",
        },
    }


def skills_map() -> list[dict]:
    """The 12 official skills and how seoforge consumes their outputs."""
    bridge = {
        "keyword-research": "saved keywords -> seoforge watchlist; volumes enrich opportunity scores",
        "keyword-clustering": "clusters -> seoforge paa.build_list gets sibling questions added",
        "ai-visibility-audit": "cited/recommended stage evidence for seoforge aeo.funnel_audit",
        "competitive-landscape": "pick competitors -> seoforge watch_add + competitor_gap",
        "competitor-analysis": "same, single competitor",
        "seo-audit": "cross-check seoforge crawl findings; merge disagreements into the queue",
        "link-prospecting": "targets -> seoforge backlinks tracker + browser agent outreach",
        "seo-report": "publishes to project; seoforge dashboard links the share URL",
        "local-seo": "GBP audits feed seoforge opportunities as local_seo items",
    }
    return [
        {"skill": n, "purpose": d, "feeds_seoforge": bridge.get(n, "")} for n, d in SKILLS
    ]


def selfhost_guide() -> dict:
    return {
        "recommended": "Cloudflare Workers (free plan) - one command deploy via alchemy: provisions D1, KV, R2, Access gate",
        "steps": [
            "fork every-app/open-seo, clone, corepack enable, pnpm install (Node 22.6+)",
            "pnpm alchemy login  (+ pnpm alchemy cloudflare bootstrap once)",
            "cp deploy/.env.selfhost.example .env.selfhost  (set ACCESS_ALLOWED_EMAILS + DataForSEO key)",
            "pnpm deploy:selfhost --yes",
            "open the printed worker URL, sign in with Cloudflare Access",
            "point clients at https://YOUR-WORKER.workers.dev/mcp  (set OPENSEO_URL to it)",
        ],
        "requirements": "R2 must be active on the Cloudflare account (payment method on file even on free tier), DataForSEO account for data",
        "docker": f"simplest for local: {DOCKER_GUIDE}",
        "vercel": "not a documented path - the stack is Workers/D1/KV/R2, not Next-on-Vercel; use Cloudflare (also free)",
        "then": "seoforge selfhost: set OPENSEO_URL=https://your-worker.workers.dev - seoforge and codex pick it up",
        "full_doc": SELFHOST_GUIDE,
        "ai_visibility_doc": AI_VIS_GUIDE,
    }


async def ping() -> dict:
    """Live MCP handshake against the configured server (no SEO credits used)."""
    m = mode()
    url = m["mcp_url"]
    headers: dict = {}
    key = settings.openseo_api_key or os.getenv("OPENSEO_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        from fastmcp import Client
        from fastmcp.client.auth import BearerAuth

        auth = BearerAuth(key) if key else None
        async with Client(url, auth=auth) as c:
            tools = sorted(t.name for t in await c.list_tools())
        return {"ok": True, "url": url, "tools_count": len(tools), "tools": tools[:60]}
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {str(exc)[:220]}", "next": "run openseo_setup() for the OAuth path, or create an API key at app.openseo.so/settings"}
