from __future__ import annotations

import asyncio
import json
import sys

from fastmcp import FastMCP

from .config import settings

mcp = FastMCP(
    "SEOForge",
    instructions=(
        "End-to-end SEO SaaS: technical crawl, Google Search Console, PAA question-pages, "
        "backlinks (73 directories + digital PR), internal linking, faceted navigation, "
        "review/product/FAQ schema, Merchant Center feed, AI-visibility (GEO), browser agent."
    ),
)


@mcp.tool()
def crawl_site(url: str = "") -> dict:
    """Crawl a site like Screaming Frog: titles, metas, canonicals, status, links, JSON-LD, speed."""
    from . import crawler
    from .safe_fetch import FetchError

    try:
        return asyncio.run(crawler.crawl_site(url or settings.site_url))
    except FetchError as e:
        return {"error": e.code, "message": str(e)}


@mcp.tool()
def seo_audit(url: str = "", with_gsc: bool = True) -> dict:
    """Full technical onboarding audit: crawl + facets + internal links + AI readiness + GSC."""
    from . import orchestrator

    return orchestrator.seo_audit(url or None, with_gsc)


@mcp.tool()
def gsc_auth() -> str:
    """One-time Google Search Console OAuth login (opens browser)."""
    from . import gsc

    return gsc.run_oauth_flow()


@mcp.tool()
def gsc_striking_distance(min_pos: int = 5, max_pos: int = 20, min_impressions: int = 50) -> list:
    """Queries ranking 5-20 with impressions - the shortlist for PAA/content work."""
    from . import gsc

    return gsc.striking_distance(min_pos, max_pos, min_impressions)


@mcp.tool()
def gsc_cannibalization() -> list:
    """Multiple URLs ranking for one query."""
    from . import gsc

    return gsc.cannibalization()


@mcp.tool()
def gsc_low_ctr(min_impressions: int = 500, max_ctr: float = 0.02) -> list:
    """High impression + low CTR = fix title/meta description."""
    from . import gsc

    return gsc.low_ctr(min_impressions, max_ctr)


@mcp.tool()
def gsc_inspect(url: str) -> dict:
    """Index status for a URL (urlInspection API)."""
    from . import gsc

    return gsc.inspect_url(url)


@mcp.tool()
def gsc_pages(days: int = 90) -> list:
    """Per-page GSC table: clicks, impressions, CTR, position."""
    from . import gsc

    return gsc.pages(days)


@mcp.tool()
def competitor_gap(keyword: str, competitor_domain: str = "") -> dict:
    """SERP + keyword ideas for a keyword, framed against one competitor domain."""
    from . import keywords

    return keywords.competitor_gap(keyword, competitor_domain)


@mcp.tool()
def rank_history(keyword: str = "", domain: str = "") -> list:
    """Historical rank snapshots (google_serp saves a snapshot on every run)."""
    from . import keywords

    return keywords.rank_history(keyword or None, domain or None)


@mcp.tool()
def core_web_vitals(url: str = "", desktop: bool = False) -> dict:
    """PageSpeed Insights mobile (+desktop) run: PSI score, LCP/TBT/CLS, CrUX field data, fix hints."""
    from . import perf

    return perf.cwv(url or settings.site_url, desktop)


@mcp.tool()
def indexing_push(urls: list[str], indexnow_key: str = "") -> dict:
    """Submit new/updated URLs to search engines: IndexNow (+ Bing/Yandex/Seznam) with sitemap ping."""
    from . import indexing

    out = {"google_sitemap_ping": indexing.ping_sitemap()}
    if urls:
        out["indexnow"] = indexing.indexnow_submit(urls, indexnow_key or None)
    return out


@mcp.tool()
def keyword_ideas(seed: str) -> list:
    """Keyword ideas + volume from DataForSEO."""
    from . import keywords

    return asyncio.run(keywords.keyword_suggestions(seed))


@mcp.tool()
def google_serp(keyword: str) -> dict:
    """Google organic SERP + People Also Ask for a keyword."""
    from . import keywords

    return asyncio.run(keywords.serp(keyword))


@mcp.tool()
def backlink_gap(domain: str = "") -> dict:
    """Your live backlinks + domain metrics (DataForSEO)."""
    from . import backlinks

    return backlinks.gap(domain or None)


@mcp.tool()
def openseo_status() -> dict:
    """Live OpenSEO integration status: hosted MCP / API key / self-host mode + costs."""
    from . import openseo

    return openseo.status()


@mcp.tool()
def openseo_setup(include_skills: bool = True) -> dict:
    """Paste-ready commands to attach OpenSEO MCP+skills to Codex (or Cursor/Claude variants)."""
    from . import openseo

    return openseo.setup_codex(include_skills)


@mcp.tool()
def openseo_ping() -> dict:
    """Live MCP handshake against OpenSEO (uses no SEO credits)."""
    from . import openseo

    return asyncio.run(openseo.ping())


@mcp.tool()
def openseo_skills() -> list:
    """The 12 official OpenSEO skills and what each feeds back into seoforge."""
    from . import openseo

    return openseo.skills_map()


@mcp.tool()
def openseo_selfhost() -> dict:
    """Self-host OpenSEO for $0: Cloudflare Workers path (the documented one; not Vercel) or Docker."""
    from . import openseo

    return openseo.selfhost_guide()


@mcp.tool()
def aeo_funnel(category_id: str = "") -> dict:
    """GEO/AEO funnel per category: be found -> answered -> cited -> recommended -> chosen. Scores + fixes."""
    from . import aeo

    return aeo.funnel_audit(category_id or None)


@mcp.tool()
def aeo_matrix() -> list:
    """Funnel scores for every category in data/aeo_categories.json."""
    from . import aeo

    return aeo.funnel_matrix()


@mcp.tool()
def aeo_llms_full() -> str:
    """Generate llms-full.txt (index + corpus) from the crawl DB - llms.txt alone is outdated."""
    from . import aeo

    return aeo.llms_full_txt_from_crawl()


@mcp.tool()
def ai_bots_list() -> dict:
    """AI-crawler registry: every bot we track (operator, type training/search/user, UA token), which publish IP ranges, and the count per type."""
    from . import ai_bots

    return ai_bots.registry_summary()


@mcp.tool()
def ai_bot_verify(user_agent: str = "", ip: str = "") -> dict:
    """Verify one hit (user agent + IP) as an AI bot: UA match on token boundaries + IP inside the operator's published ranges (fetched live, 24 h cached). Returns the bot, or a reason: unknown_agent / no_ranges / unverified_ip."""
    from . import ai_bots

    return asyncio.run(ai_bots.verify_bot_request(user_agent, ip))


@mcp.tool()
def bot_connections_list() -> dict:
    """AI-bot log ingest connections + this month's use: stored rows against the event cap, log senders' records against the raw cap."""
    from . import bot_ingest, db

    with db._conn() as conn:
        connections = bot_ingest.list_connections(conn)
        month = db.now()[:7] + "-01"
        usage_row = conn.execute(
            "SELECT stored_events, counted_events, capped_at, raw_records, raw_capped_at"
            " FROM bot_usage WHERE month=?",
            (month,),
        ).fetchone()
    caps = bot_ingest.current_caps()
    return {
        "connections": connections,
        "usage": {
            "month": month,
            "stored": usage_row["stored_events"] if usage_row else 0,
            "counted": usage_row["counted_events"] if usage_row else 0,
            "capped_at": usage_row["capped_at"] if usage_row else None,
            "event_cap": caps["event_cap"],
            "raw": usage_row["raw_records"] if usage_row else 0,
            "raw_capped_at": usage_row["raw_capped_at"] if usage_row else None,
            "raw_cap": caps["raw_cap"],
        },
    }


@mcp.tool()
def bot_connection_create(kind: str, domains: list = []) -> dict:
    """Make a bot-ingest connection (cloudflare_worker | cloudflare_logpush | vercel_drain): returns its secret once, the public endpoint and what to copy - the Worker source, a Cloudflare Logpush job config or a Vercel drain config."""
    from . import bot_ingest, bot_sender_setup, bot_worker_script

    endpoint = bot_ingest.public_ingest_endpoint()
    if not endpoint:
        return {
            "error": "not_set_up",
            "message": "SEOFORGE_PUBLIC_SERVER_URL is missing or not https, so senders have nowhere to post.",
        }
    if bot_ingest.current_caps()["event_cap"] == 0:
        return {"error": "locked", "message": "the bot report is off on this server (SEOFORGE_BOT_EVENT_CAP=0)"}
    row = bot_ingest.create_connection(kind, [d for d in domains if d])
    return {
        "connection": {"id": row["id"], "kind": row["kind"], "domains": row["domains"], "created_at": row["created_at"]},
        "secret": row["secret"],  # shown once; only its SHA-256 is stored
        "endpoint": endpoint,
        "worker_script": bot_worker_script.build_worker_script(endpoint) if row["kind"] == "cloudflare_worker" else None,
        "worker_secret_name": bot_worker_script.WORKER_SECRET_NAME if row["kind"] == "cloudflare_worker" else None,
        "logpush_job": bot_sender_setup.logpush_job(endpoint, row["secret"]) if row["kind"] == "cloudflare_logpush" else None,
        "vercel_drain": bot_sender_setup.vercel_drain(endpoint, row["secret"]) if row["kind"] == "vercel_drain" else None,
    }


@mcp.tool()
def bot_connection_revoke(connection_id: int) -> dict:
    """Revoke a bot-ingest connection (its past events stay readable)."""
    from . import bot_ingest

    return {"revoked": bot_ingest.revoke_connection(connection_id)}


@mcp.tool()
def bot_ingest_requests(connection_id: int, requests: list) -> dict:
    """Verify and store one batch of bot requests (userAgent/ip/url/status/at/country) for a connection: page on the connection's domains, UA on the bot registry, IP in the operator's published ranges; written within the month's event cap (overflow folds onto '(other)' daily counters)."""
    from . import bot_ingest, db

    if not isinstance(requests, list):
        return {"error": "invalid_request", "message": "requests must be a list"}
    with db._conn() as conn:
        connection = bot_ingest.connection_by_id(conn, connection_id)
    if connection is None:
        return {"error": "unknown_connection", "message": f"no live connection {connection_id}"}
    return asyncio.run(bot_ingest.ingest_bot_requests(connection, requests))


@mcp.tool()
def bot_report(since: str = "", limit: int = 20) -> dict:
    """AI bots reading the site: per type (training/search/user), per bot and top pages - read from bot x page x day counters that keep counting past the cap; alert waits until 30 days of data, then signals staleness (no live connection in 2 days, or search reads folding onto '(other)')."""
    from . import bot_ingest, db

    with db._conn() as conn:
        return bot_ingest.bot_report(conn, since=since or None, limit=limit)


@mcp.tool()
def bot_prune() -> dict:
    """Delete bot rows past their retention (events 90 days, daily counters 400, file hashes 7) in batches; run it daily."""
    from . import bot_ingest

    return bot_ingest.prune_bot_events()


@mcp.tool()
def outcomes_list(limit: int = 25) -> list:
    """Measured outcome verdicts for closed work, newest first: per item the outcome word (improved/declined/no_meaningful_change/not_measurable) + the validation JSON (both 7-day windows, every metric pair, position verdicts). Movement measured after the work closed — never causality."""
    from . import outcomes

    return outcomes.list_outcomes(limit)


@mcp.tool()
def outcome_validate(opportunity_id: int) -> dict:
    """Measure one closed work item once its 7-day after-window has finished: compare the before/after reads (GSC windows, rank snapshots, bot reads), record the verdict ONCE into History. Returns {"verdict": null, "note": ...} while the item is open or its window has not finished; a verdict already recorded stays answered."""
    from . import outcomes

    verdict = outcomes.validate_opportunity(opportunity_id)
    if verdict is None:
        return {
            "verdict": None,
            "note": "not ready: the work is not closed yet, or its 7-day after-window has not finished (nothing was written)",
        }
    return {"verdict": verdict}


@mcp.tool()
def prompt_create(text: str, category: str = "") -> dict:
    """Track one buying question verbatim for prompt-positions tracking: pasted AI answers are parsed against it (mentions, mention position, own-domain citations, visibility). Idempotent - the same text never creates a second row; an empty text answers {"error": "invalid"}."""
    from . import prompts

    try:
        return {"id": prompts.create_prompt(text, category)}
    except ValueError as e:
        return {"error": "invalid", "message": str(e)}


@mcp.tool()
def prompt_record_answer(
    prompt_id: int,
    platform: str,
    response: str,
    citations: list | None = None,
    sentiment: str = "neutral",
    competitors: list | None = None,
    at: str = "",
) -> dict:
    """Record one manually pasted AI answer for a prompt (live answer fetching stays deferred - the paste-in is the substrate): parsed deterministically at write time (mention count, first-mention position, own-domain citation count, visibility score, sentiment, competitor mentions) and stored append-only - a recorded answer never changes. citations are {"url": ...} dicts (bare strings are read as urls); an unknown prompt id or platform answers {"error": "invalid"}."""
    from . import prompts

    cites = [c if isinstance(c, dict) else {"url": str(c)} for c in (citations or [])]
    try:
        return prompts.record_answer(
            prompt_id,
            platform,
            response,
            citations=cites,
            sentiment=sentiment,
            competitors=competitors or [],
            at=at or None,
        )
    except ValueError as e:
        return {"error": "invalid", "message": str(e)}


@mcp.tool()
def prompt_states(prompt_id: int, since: str = "", until: str = "") -> list:
    """One prompt's state per tracked platform over the last 7 tracked days (or since/until day strings): state, run count and how they were judged - the same rows and rules the Positions panel reads; every tracked platform answers, `no_runs` while nothing was pasted on it."""
    from . import prompts

    return prompts.platform_states(
        prompt_id, since=since or None, until=until or None
    )


@mcp.tool()
def prompt_gap() -> list:
    """One improve_ai_visibility work item per tracked prompt whose 7-day state is weak (not_mentioned, mentioned_late or not_cited) - the fix is prompt-shaped work the prompt-positions sweep then measures; `no_runs` platforms raise nothing (there is only pasting to catch up on)."""
    from . import integrations

    return integrations._ai_visibility_gap()


@mcp.tool()
def ai_sources_list() -> dict:
    """AI-assistant source registry: every source we count traffic from (hosts, aliases, surfaces like ChatGPT Shopping), with the Bing /ck/a referrer gate flagged."""
    from . import ai_sources

    return ai_sources.sources_summary()


@mcp.tool()
def classify_ai_traffic(referrer: str = "", utm_source: str = "", utm_medium: str = "", landing_url: str = "") -> dict:
    """Classify one visit: which AI assistant it came from and through which surface. Referrer first (Bing counts only via its /ck/a redirect), utm_source second; medium feed = ChatGPT Shopping."""
    from . import ai_sources

    result = ai_sources.classify(
        referrer or None, utm_source or None, utm_medium or None, landing_url or None
    )
    if result is None:
        return {"source": None, "note": "not AI traffic (normal outcome for most visits)"}
    return {"source": result.source, "surface": result.surface, "origin": result.origin}


@mcp.tool()
def rubric_audit(url: str, query: str = "") -> dict:
    """48-signal AI-citation rubric audit of one page: 34 deterministic signals across 5 weighted categories (structure / authority / content / trust / E-E-A-T), scored with partial-coverage re-normalization; the 13 LLM-judged + brand-entity signals are listed as deferred. Persists a rubric_audit report."""
    from . import rubric_signals

    try:
        return rubric_signals.audit_url(url, query=query or None)
    except (FetchError, rubric_signals.AuditPageError) as e:
        return {"error": getattr(e, "code", "audit_page_error"), "status": getattr(e, "status", None), "message": str(e)}


@mcp.tool()
def preferred_sources_audit(site: str = "") -> dict:
    """Google Preferred Sources audit: eligibility (fresh content, domain property, SGEA-in-GSC), official button snippet, checklist."""
    from . import preferred

    return preferred.audit(site or None)


@mcp.tool()
def preferred_sources_button(theme: str = "light", lang: str = "", mode: str = "standard") -> dict:
    """Copy-paste 'Add to Preferred Sources' button code (Google's official publisher.js embed). mode: standard|manual|esm."""
    from . import preferred

    return preferred.embed_snippet(theme, lang or None, mode)


@mcp.tool()
def technical_audit(max_clicks: int = 3) -> dict:
    """Six technical rules: 3-click structure, JS-shell crawlability, GSC-indexed/skipped batch, render-blocker speed, viewport mobile, sitemap policy + submissions."""
    from . import technical

    return technical.report()


@mcp.tool()
def site_structure(max_clicks: int = 3) -> dict:
    """Rule: BFS click-distance of important (GSC-topped) pages from the homepage - flag anything beyond ~3 clicks."""
    from . import technical

    return technical.structure(max_clicks)


@mcp.tool()
def crawlability_report() -> dict:
    """JS shells vs raw-HTML words per page: what GPTBot/ClaudeBot actually see without executing JavaScript."""
    from . import technical

    return technical.crawlability()


@mcp.tool()
def index_status(urls: list = [], limit: int = 25) -> dict:
    """Batch GSC urlInspection: indexed vs skipped + Google coverage reasons per URL."""
    from . import technical

    return technical.index_status(list(urls) or None, limit)


@mcp.tool()
def speed_report() -> dict:
    """Render-blocking head resources + TTFB/size per page: main-content-first verdict."""
    from . import technical

    return technical.speed_rules()


@mcp.tool()
def mobile_report() -> dict:
    """Viewport meta per page (the phone-usability floor) + pointer to CWV mobile data."""
    from . import technical

    return technical.mobile_rules()


@mcp.tool()
def sitemap_policy() -> dict:
    """What belongs in sitemap.xml (keep/remove, thin+noindex+404s) and how it reaches Google & Bing."""
    from . import technical

    return technical.sitemap_policy()


@mcp.tool()
def value_vs_semrush(openseo_hosted: bool = False, markdown: bool = False) -> dict | str:
    """Semrush $139.95/mo vs the SEOForge stack: module coverage matrix + cost model."""
    from . import value

    report = value.value_report(openseo_hosted)
    return value.render_markdown(report) if markdown else report


@mcp.tool()
def research_provider() -> dict:
    """Rule 3 - use ONE keyword tool: which one seoforge is wired to, and why never to mix difficulty scores."""
    from . import research

    return research.provider()


@mcp.tool()
def audience_problems(products: list = [], limit: int = 40) -> dict:
    """Rule 1 - problems your customers search about, in their words: real GSC queries + PAA + category drills."""
    from . import research

    return research.audience_problems(list(products), limit)


@mcp.tool()
def seed_ideas(products: list = []) -> dict:
    """Rule 2 - broad seed keywords built from how a customer would describe what you sell."""
    from . import research

    return research.seed_ideas(list(products))


@mcp.tool()
def intent_check(keyword: str) -> dict:
    """Rule 4 - live SERP intent: do the ranking results teach, compare or sell -> which page type to build."""
    from . import research

    return research.check_intent(keyword)


@mcp.tool()
def keyword_worth(keywords: list) -> dict:
    """Rule 5 - volume/competition/trend per keyword: page, needs-edge, skip or unknown."""
    from . import research

    return research.worth_a_page(list(keywords))


@mcp.tool()
def keyword_groups(keywords: list = []) -> dict:
    """Rule 6 - cluster similar keywords so one page targets one cluster (never compete with yourself)."""
    from . import research

    return research.group_keywords(list(keywords) or None)


@mcp.tool()
def keyword_research_workflow(products: list = []) -> dict:
    """Full 6-rule keyword research pass (audience/seeds/one-tool/intent/numbers/groups), saved as a report."""
    from . import research

    return research.workflow(list(products) or None)


@mcp.tool()
def onpage_audit(url: str = "") -> dict:
    """Six-rule on-page audit per crawled page: title phrase+length, meta why-beats, question headings, first-sentence answer, alt text, title-matched anchors."""
    from . import onpage

    return onpage.audit(url or None)


@mcp.tool()
def onpage_pack(query: str, usp: str = "") -> dict:
    """One query -> six-rule-compliant on-page block: title (<=60), meta (<=155), question headings, first line, image alt, internal links."""
    from . import onpage

    return onpage.fix_pack(query, usp)


@mcp.tool()
def paa_tree(seeds: list[str], max_depth: int = 2) -> dict:
    """Expand People Also Ask boxes into a question tree (how/what/best/vs)."""
    from . import paa

    return asyncio.run(paa.build_paa_tree(seeds, max_depth))


@mcp.tool()
def question_page_plan(min_questions: int = 2) -> dict:
    """Turn the PAA tree into question-pages to build (path, title, meta, schema, interlinks)."""
    from . import paa

    plan = paa.generate_build_list(limit=500)
    pages = [p for p in plan["pages"] if p["questions"] >= min_questions]
    return {"count": len(pages), "pages": pages}


@mcp.tool()
def paa_export() -> str:
    """Export the PAA build-list CSV."""
    from . import paa

    return paa.export_paa_csv()


@mcp.tool()
def page_scaffolds(min_questions: int = 3, limit: int = 15) -> dict:
    """Write one markdown brief + FAQPage JSON-LD template per question page (Codex fills copy)."""
    from . import paa

    return paa.page_scaffold(min_questions, limit)


@mcp.tool()
def sitemap_crosscheck(site: str = "") -> dict:
    """Sitemap vs crawl DB: 404/noindex/thin/canonical drift, pages missing from sitemap."""
    from . import indexing

    return indexing.crosscheck(site or None)


@mcp.tool()
def ai_citation_check(questions: list[str]) -> dict:
    """Ask a frontier LLM buying questions; count how often it cites your brand/domain (GEO)."""
    from . import geo

    return geo.ai_citation_check(questions)


@mcp.tool()
def internal_links_report() -> dict:
    """Orphans, dead links, PageRank top pages, TF-IDF link suggestions."""
    from . import internal_links

    return internal_links.build_report()


@mcp.tool()
def faceted_navigation_report() -> dict:
    """Classify crawl params: index / noindex-follow / ignore + robots.txt snippet."""
    from . import facets

    return facets.analyze()


@mcp.tool()
def backlink_directories(status: str = "", min_dr: int = 0) -> list:
    """The 73 startup backlink places with DR + submission status."""
    from . import backlinks

    return backlinks.list_directories(status or None, min_dr)


@mcp.tool()
def backlink_update(name: str, status: str, notes: str = "") -> str:
    """Update a directory row: todo|drafting|submitted|live|rejected|skipped."""
    from . import backlinks

    return backlinks.update_status(name, status, notes or None)


@mcp.tool()
def digital_pr_plan() -> list:
    """Digital PR tactics: unlinked mentions, journalists, data assets, podcast circuit."""
    from . import backlinks

    return backlinks.digital_pr_plan()


@mcp.tool()
def merchant_center_feed(csv_path: str = "") -> str:
    """Build/refresh the Google Merchant Center XML feed (import CSV first if given)."""
    from . import merchant

    if csv_path:
        merchant.load_products_csv(csv_path)
    return merchant.build_feed()


@mcp.tool()
def add_product(product_json: str) -> str:
    """Add one product for merchant feed + product schema. e.g. {"id","title","price","link",...}."""
    from . import merchant

    return merchant.upsert_product_json(product_json)


@mcp.tool()
def faq_schema(qa_json: str) -> str:
    """FAQPage JSON-LD from [{"question","answer"}]. Use for PAA pages + product FAQs."""
    from . import struct

    return struct.render(struct.faq(json.loads(qa_json)))


@mcp.tool()
def product_schema(product_json: str) -> str:
    """Product JSON-LD with offers, AggregateRating, Reviews. Add "faqs" to embed FAQ block."""
    from . import struct

    d = json.loads(product_json)
    return struct.render(
        struct.product(
            d["id"], d["title"], d["description"], d["link"], d["image"],
            d["price"], d.get("currency", "INR"), d.get("availability", "InStock"),
            d.get("brand"), d.get("gtin"), d.get("reviews"), d.get("faqs"),
        )
    )


def _extract(d: dict, keys: list, defaults: list):
    return [d.get(k, deft) for k, deft in zip(keys, defaults)]


@mcp.tool()
def validate_schema(schema_json: str) -> list:
    """Check required fields for Google rich results."""
    from . import struct

    return struct.validate(json.loads(schema_json))


@mcp.tool()
def ai_readiness(site: str = "") -> dict:
    """GEO test: llms.txt, AI robots (GPTBot...), sitemap, FAQ 'ten-second' test, score."""
    from . import geo

    return asyncio.run(geo.check_ai_readiness(site or None))


@mcp.tool()
def llms_txt() -> str:
    """Generate an llms.txt from your crawl DB."""
    from . import geo

    return geo.llms_txt_from_crawl()


@mcp.tool()
def opportunity_queue(status: str = "open", kind: str = "", limit: int = 25) -> list:
    """THE work queue: GSC + PAA + crawl + CWV + backlinks + rank data merged and scored (impact/effort)."""
    from . import integrations

    return integrations.queue(status, kind, limit)


@mcp.tool()
def opportunities_refresh() -> dict:
    """Re-blend all sources into the opportunity queue (calls gsc cache; refreshes if stale)."""
    from . import integrations

    return integrations.recompute()


@mcp.tool()
def opportunity_done(type_target: str) -> str:
    """Mark an opportunity done; format '<type>|<target>'."""
    from . import integrations

    return integrations.mark_done(type_target)


@mcp.tool()
def gsc_delta() -> dict:
    """Nightly-style delta now: last 2 complete days GSC vs prior 7-day baseline + refreshed queue."""
    from . import scheduler

    return scheduler.nightly_delta()


@mcp.tool()
def watch_add(keyword: str, competitor: str = "") -> str:
    """Track a keyword in the rank-watch list (serp_watch job snapshots it daily)."""
    from . import scheduler

    return scheduler.watch_add(keyword, competitor)


@mcp.tool()
def watch_list() -> list:
    """Current keyword watchlist."""
    from . import scheduler

    return scheduler.watch_list()


@mcp.tool()
def watch_run() -> dict:
    """Rank-snapshot all watchlist keywords now."""
    from . import scheduler

    return scheduler.serp_watch()


@mcp.tool()
def scheduler_status() -> list:
    """Scheduled jobs, intervals, last run/status."""
    from . import scheduler

    return scheduler.list_schedule()


@mcp.tool()
def scheduler_run(only_due: bool = True) -> list:
    """Run due scheduled jobs now (nightly_delta, opps_refresh, sitemap check, serp watch)."""
    from . import scheduler

    return scheduler.tick(only_due)


@mcp.tool()
def browser_task(task: str) -> str:
    """Run any browser automation with the browser-use agent (logins, submissions, scraping)."""
    from . import browser_agent

    return browser_agent.run(task)


@mcp.tool()
def submit_to_directory(name: str, product_info: str = "{}") -> str:
    """Use the browser agent to submit your product to one of the 73 directories."""
    from . import browser_agent

    info = json.dumps({**json.loads(product_info), "brand": settings.brand_name})
    return asyncio.run(browser_agent.submit_directory(name, json.loads(info)))


@mcp.tool()
def verify_listing(url: str) -> dict:
    """Verify a submitted listing/mention is live."""
    from . import browser_agent

    return asyncio.run(browser_agent.verify_listing(url, settings.brand_name))


@mcp.tool()
def autopilot_run(site_url: str = "", skip_browser: bool = True) -> dict:
    """Run the complete end-to-end autonomous pipeline (discover, diagnose 4-engines, fix bundle, distribute, index, monitor)."""
    from . import autopilot

    return autopilot.run(site_url or None, skip_browser=skip_browser)


@mcp.tool()
def fix_bundle_generate(output_dir: str = "") -> dict:
    """Package all generated fixes (llms.txt, schemas, robots.txt, preferred button, titles CSV) into a downloadable ZIP."""
    from . import fix_bundle

    path = fix_bundle.generate(output_dir or None)
    return {"bundle_zip": path, "status": "generated"}


@mcp.tool()
def directory_batch_submit(max_per_session: int = 5, pause_seconds: int = 10) -> dict:
    """Batch-submit brand profile to backlink directories via automated browser agent."""
    from . import auto_submit

    return auto_submit.batch_submit(max_per_session=max_per_session, pause_seconds=pause_seconds)


@mcp.tool()
def directory_submit_status() -> dict:
    """Current status counts across all 75 authority backlink directories."""
    from . import auto_submit

    return auto_submit.submit_status()


@mcp.tool()
def generate_article(keyword: str, questions: list = []) -> dict:
    """Generate a complete 800-1200 word publication-ready guide targeting SEO and AI answer engines."""
    from . import content_gen

    return content_gen.generate_article(keyword, questions=list(questions) or None)


@mcp.tool()
def deploy_discovery_files(repo_public_dir: str = "") -> dict:
    """Deploy llms.txt, llms-full.txt, and suggested robots.txt directly into a Next.js / Astro public folder."""
    from . import publisher

    return publisher.deploy_local_repo(repo_public_dir or None)


def main():
    args = [a for a in sys.argv[1:] if a]
    if args and args[0] == "http":
        mcp.run(transport="http", host="127.0.0.1", port=8811)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
