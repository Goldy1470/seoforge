# SEOForge — end-to-end SEO SaaS + MCP plugin for Codex / ChatGPT

One agent-connectable engine that runs the whole SEO loop — and, unlike Semrush ($139.95/mo),
it *executes*, not just reports:

**be found → be answered → be cited → be recommended → be chosen** (AEO/GEO funnel per category)

## Live OpenSEO (the YC open-source Semrush alternative) — 3 paths

| Path | Cost | Setup |
|---|---|---|
| **Plugin (best)** | $10/mo hosted | `codex plugin marketplace add every-app/open-seo` → `codex plugin add openseo@openseo` → `codex mcp login openseo` — bundles MCP + all 12 skills (audit, keyword research, **AI-visibility audit**, rank tracking, competitor analysis…) |
| **API key** | $10/mo hosted | create key at app.openseo.so/settings → `codex mcp add openseo --url https://app.openseo.so/mcp --bearer-token-env-var OPENSEO_API_KEY` |
| **Self-host** | $0 | Cloudflare Workers (the documented path; **not Vercel** — stack is D1/KV/R2/Workers) or Docker local; bring your own DataForSEO, no 28% hosted markup |

Seoforge-side helper tools: `openseo_status`, `openseo_setup` (paste-ready commands),
`openseo_ping` (live handshake, no credits), `openseo_skills` (their 12 skills → what each
feeds back into seoforge's queue), `openseo_selfhost` (full guide). CLI: `openseo-setup` etc.

```
Codex/ChatGPT
 ├─ seoforge MCP (46+ tools): crawl/GSC/PAA/queue/schema/browser/scheduler   ← owns the work queue
 └─ openseo MCP (hosted):    keywords/competitors/AI-visibility/rank data   ← feeds the queue
        skills bridge: keyword-research→watchlist, ai-visibility-audit→funnel cited+recommended,
        competitor-analysis→watch_add, link-prospecting→73-dir tracker, keyword-clustering→PAA tree
```

## The GEO/AEO funnel — `aeo.py`, category-wise (`data/aeo_categories.json`)

Per commercial category (amazon-agency, ai-seo, website-development, performance-marketing,
local-mumbai — edit for your site):

| Stage | What it measures | Fixed by |
|---|---|---|
| **found** (0-100) | per-agent robots (GPTBot, ClaudeBot, PerplexityBot…), llms.txt **+ llms-full.txt** (llms.txt alone is outdated), sitemap, server-rendered FAQ (GPTBot reads only raw HTML) | robots + `llms-full` tool + pre-render |
| **answered** (0-100) | the category's real buying questions match page headings, a 40-70 word literal answer block exists in first 1000 chars, FAQPage JSON-LD present | `struct.faq`, page scaffolds |
| **cited** (0-100) | does an LLM actually cite you? (ai_citation_check; OpenSEO's multi-model citation tool as bridge) | first-party data, sameAs entity, methodology |
| **recommended** (0-100) | 'best X' shortlist presence: rank snapshots + AI-directory listings + the "how to choose X" comparison page agents love to cite | build the comparison page, get listed (73-list) |
| **chosen** (0-100) | assistive-agent actionability: Offer/Review schema, visible price, clear CTA, tel:/mailto:/wa.me machine-readable contact | `struct.product`, pricing block |

Commands: `python -m seoforge.cli aeo ai-seo` · `aeo-matrix` · `llms-full` (write results to
`/llms-full.txt`) · MCP: `aeo_funnel`, `aeo_matrix`, `value_vs_semrush`.

## Google Preferred Sources — why this lever (and how the module runs it)

People pick you once in `Search personalization → Source preferences`, and your links get a
**Preferred** badge in *Top Stories, AI Overviews and AI Mode* for that user — Google says those
readers click ~2× more. Any site that publishes fresh content is eligible; only domain-level
/subdomain properties show up in the tool; and you must be included in **Search generative AI
features** in Search Console. `python -m seoforge.cli preferred` audits all three, checks
whether the official button is already on your home page, and hands you the exact embeds
(`preferred_sources_button`) — Google's `publisher.js` + `google-add-preferred-source-btn`
div, light/dark, localized. Put it in article footers / the homepage.

## Why it beats a $140/mo suite (run `value_vs_semrush` / `value --markdown`)

16-module coverage matrix + cost model: Semrush Pro $139.95/mo → SEOForge stack
≈ $0-25/mo (self-host, or +$10 OpenSEO hosted) for equal data depth — plus the four
"beyond rankings" stages Semrush doesn't touch (question pages, execution agent, IndexNow,
nightly scored queue). Dashboard shows the counter on the front page.

## Install

```bash
cd seoforge
pip install -e .            # core
pip install -e ".[browser]" # + browser-use & playwright (one Windows line: playwright install chromium)
copy .env.example .env      # then edit
```

Give it a 2-minute smoke run:

```bash
python -m seoforge.cli crawl https://example.com
python -m seoforge.cli build-list
```

## Connect to Codex / ChatGPT (the "plugin")

Add `%USERPROFILE%\.codex\config.json` (Codex CLI) or the connector list in ChatGPT:

```json
{ "mcp_servers": { "seoforge": { "command": "python", "args": ["-m", "seoforge.mcp_server"] } } }
```

(see `codex-mcp-config.example.json` — includes pairing with **OpenSEO**, the YC-backed
open-source Ahrefs/Semrush alternative that also ships an MCP server; attach both.)

## What it does — module map

| Area | Module | Key tools |
|---|---|---|
| Technical crawl (Screaming Frog-style) | `crawler.py` | `crawl_site`, titles/metas/canonicals/dups/speed/JSON-LD, sitemap seeding |
| SSRF-safe fetching (all user URLs) | `safe_fetch.py` | every page/robots/sitemap fetch goes through public-address checks + DNS pinning + redirect re-validation + size/time caps (`SEOFORGE_ALLOW_PRIVATE_FETCH` for local dev only) |
| Google Search Console | `gsc.py` | striking-distance queries (pos 5–20), cannibalization, low CTR, URL inspection |
| SERP + keyword data | `keywords.py` | DataForSEO SERP/PAA/keyword-ideas/backlinks, OpenSEO passthrough |
| **PAA → question pages** (the highprimeco method — PAA box as a build list) | `paa.py` | `paa_tree`, `question_page_plan`, CSV export to build pages like `/free-guides` |
| Backlinks: the 75 startup places | `backlinks.py` + `data/directories_73.csv` | tracker (todo→submitted→live), DR filter, digital-PR plan |
| Browser work | `browser_agent.py` | browser-use: directory submissions, verify listings, SERP/PAA click-through, screenshots |
| Internal linking | `internal_links.py` | graph + PageRank + orphans + TF-IDF anchor suggestions |
| Faceted navigation | `facets.py` | classify params (facet/pagination/tracking) → noindex,follow / canonical / robots rules |
| Review & FAQ schema on products | `struct.py` | Product + AggregateRating + Review + FAQPage JSON-LD generators & validator |
| Merchant Center | `merchant.py` | products DB → `g:` XML feed |
| Rank tracking | `keywords.py` | every SERP run snapshots positions into `rank_snapshots`; `rank-history` diffs over time |
| Core Web Vitals | `perf.py` | PageSpeed Insights mobile+desktop: PSI score, LCP/TBT/CLS, CrUX, fix hints |
| Indexing push | `indexing.py` | IndexNow (Bing/Yandex/Seznam), Google sitemap ping, IndexNow key generator, Brave submit lane (browser-filled `search.brave.com/submit-url` or manual steps; Brave documents no user-agent token of its own — Googlebot-crawlability gates crawling, `noindex` delists, nothing is attributable in the bot report) |
| Sitemap hygiene | `indexing.py` | sitemap↔crawl crosscheck: 404s/noindex/thin/canonical drift, pages missing from sitemap |
| Cross-source value layer | `integrations.py` | gsc cache + PAA + crawl + CWV + tracker + rank history → one scored opportunity queue |
| **Outcome capture** (did the work help?) | `outcomes.py` | every closed opportunity gets measured once: two equal 7-day windows (one ending before the work was raised, one after it closed) read through the same data the other reports use — GSC clicks/impressions/position for its page and query, the rank-snapshot best position for its keyword, the AI-bot daily reads for its page; count metrics derive with a 5% noise floor (mixed movement reads improved), positions with the lower-is-better 0.5 rule; **unmeasured is never zero**; the verdict is recorded exactly once (`improved` / `declined` / `no_meaningful_change` / `not_measurable`) and reports movement, never causality; `mark_done` stamps the close + arms it, the daily `outcome_validation` job measures after the settling week; `outcomes` / `outcomes-validate` CLI, `outcomes_list` + `outcome_validate` MCP tools |
| Scheduler | `scheduler.py` | nightly GSC delta, keyword watch, due-job tick + daemon |
| **Tenant shell** (the billing gate) | `store.py` · `tenants.py` · `tenancy.py` | one cross-dialect directory (SQLite offline/dev, `postgresql://` production with lazy psycopg — identical DDL, TEXT uuid ids, `?` placeholders translated) holding tenants, `sfk_` API keys (shown exactly once, only SHA-256 stored — the bot-connection secret discipline), a plan gate (active/trialing open; past_due/churned/suspended closed with the reason) and monthly metering; per-tenant engine files via the ambient tenancy context — every `db._conn()` lands in the tenant's own store, the operator seat stays where it always was; `/api/v1/whoami` + `/api/v1/run/{kind}` (Bearer auth at the wire: 401 unknown key, 402 plan gate, 429 spent quota) with `tenant-add` / `tenants` / `tenant-status` / `tenant-plan` / `key-issue` / `keys` / `key-revoke` CLI |
| Digests | `notify.py` | Slack webhook or saved report briefs |
| Live OpenSEO | `openseo.py` | hosted-MCP / API-key / self-host modes, setup commands, 12-skill bridge |
| AEO/GEO funnel | `aeo.py` | found → answered → cited → recommended → chosen, category-wise scores + fixes, llms-full.txt |
| **AI-bot registry** (who is really crawling you) | `ai_bots.py` | 17 tracked agents — 8 verified against the operators' published IP ranges (OpenAI, Anthropic, Perplexity), token-boundary UA matching, 24 h SQLite-cached range fetch through the SSRF-safe fetcher; `ai-bots` + `ai-bot-verify` CLI, `ai_bots_list` + `ai_bot_verify` MCP tools; per-type robots access (training vs search vs user) feeds the found-stage |
| **AI-source classifier** (which assistant sent the visit) | `ai_sources.py` | 13 sources; host-or-subdomain or exact-alias matching (never substring - a random host with "claude" in it isn't AI traffic); referrer → utm_source order with Bing gated to its `/ck/a` redirect; surfaces by exact medium (ChatGPT Shopping = `utm_medium=feed`); RE2 GA4 pattern; `ai-sources` + `ai-classify` CLI, `ai_sources_list` + `classify_ai_traffic` MCP; the classifier the Phase-4 log ingest runs per line |
| **AI-bot log ingest** (the "bots reading your site" report) | `bot_ingest.py` · `bot_log_formats.py` · `bot_worker_script.py` · `bot_sender_setup.py` | senders post request lines from the site's own edge — a Cloudflare Worker (source generated on connection; secret read from the `SEOFORGE_BOT_SECRET` Worker Secret, never inlined), a Cloudflare Logpush job on the http_requests dataset (gzipped NDJSON, edge-filtered to the bot list) or a Vercel drain (JSON logs); every batch verified page-by-page (registry UA + operator IP ranges) before anything is written; monthly event/raw caps (`SEOFORGE_BOT_EVENT_CAP` / `SEOFORGE_BOT_RAW_CAP`, -1 unlimited / 0 locks the report) where overflow keeps counting on the bot's `(other)` daily row so totals stay honest; a resent log file answers as a duplicate by body hash; public `POST /bots/ingest` + `/bots/connections` CRUD, `bot-connections` / `bot-connection-add` / `bot-connection-revoke` / `bot-ingest-file` / `bot-report` / `bot-prune` CLI, `bot_connections_list` / `bot_connection_create` / `bot_connection_revoke` / `bot_ingest_requests` / `bot_report` / `bot_prune` MCP tools |
| **Prompt-positions tracking** (am I cited for what buyers paste?) | `prompts.py` | buying questions tracked verbatim (idempotent by text, seeded from the PAA tree + watchlist); answers pasted in by hand (live fetching deferred) parsed deterministically at write time — mention count, first-mention position, own-domain citation count (exact host or subdomain, never substring), visibility score (half-up rounding), sentiment, competitor mentions with per-competitor citation counts; per-platform 7-day states from the Positions-panel rules (`no_runs` until something is pasted); the outcome sweep keeps the baseline immutable (the window ending the day tracking was raised never re-read), refreshes the current read while the work is open and inside its 30 tracking days, and writes the day-30 verdict exactly once into History (`position_outcome_recorded` trailed in the event log); weak states raise one `improve_ai_visibility` item per prompt, and the bot report's uncrawled alert reads the same citations; `prompts-add` / `prompts-list` / `answer-record` / `prompt-states` / `prompt-sweep` CLI, `prompt_create` / `prompt_record_answer` / `prompt_states` / `prompt_gap` MCP tools, daily `prompt_positions` job |
| **Position tracker** (the full rank engine) | `serp_tracking.py` + `serp_providers.py` + `serp_countries.py` + `serp_scraper.py` + `serp_digest.py` | 11 scraping providers behind one contract (scrapingant, scrapingrobot, serpapi, searchapi, serply, spaceSerp, valueserp, serper, hasdata, crazyserp + the `proxy` HTML fallback), each keeping its reference URL/header shape, with a 251-entry country codebook; basic / custom / smart strategies renumber positions per page and answer "scraper too unreliable" when half the pages fail rather than guess; per-domain refresh with parallel keyword lanes on scrapingant/serpapi/searchapi; failed keywords parked in a SQL retry queue drained hourly; daily YYYY-M-D history rows stamped on success and error-logged on failure, and `position_change` at reference parity (re-entering the results counts as a gain) feeding `serp-insights` distributions, the keywords CSV and the digest arrows; `serp-domains` / `serp-add` / `serp-keywords` / `serp-refresh` / `serp-retries` / `serp-insights` / `serp-digest` / `serp-csv` CLI, 9 `serp_*` MCP tools, `serp_refresh` / `serp_retries` / `serp_digest` scheduler lanes |
| **48-signal AI-citation rubric** (what AI engines look for) | `rubric.py` + `rubric_signals.py` + `product_description.py` | 5 weighted categories — structure · authority · content · trust · E-E-A-T — 34 deterministic signals scored with partial-coverage re-normalization (the 13 LLM-judged + brand-entity signals are listed deferred, never drag the score); product-description opening gate (ChatGPT reads ~40 words / 250 chars — type + 2 key attributes or it warns); `rubric-audit <url>` CLI + `rubric_audit` MCP; every audit persists a `rubric_audit` report |
| **Execution chain** (one URL in, work done out) | `autopilot.py` · `fix_bundle.py` · `auto_submit.py` · `content_gen.py` · `publisher.py` | 6-phase autopilot (discover → diagnose 4-engine scores → fixes → distribute → index → monitor); downloadable fix-bundle ZIP (suggested robots.txt, llms.txt+llms-full.txt, FAQ/Org schemas, Preferred Sources button, titles/metas CSV, PAA briefs, REPORT.md); batch directory submission with status tracking; PAA-scaffold → publication-ready articles (OpenAI/Gemini/Anthropic keys, offline structural fallback); WordPress/webhook/local-repo publishing |
| Value vs Semrush | `value.py` | 16-module coverage matrix + $139.95 vs stack cost counter |
| **Google Preferred Sources** (the 2026 sleeper) | `preferred.py` | eligibility audit (fresh content, domain property, SGEA-in-GSC) + official `Add to Preferred Sources` button embeds — badges in Top Stories **and** AI Overviews/AI Mode, ~2× CTR |
| **Keyword-research playbook** (the 6 rules) | `research.py` | 1 audience problems (their words: GSC+PAA) → 2 broad seeds → 3 one-tool policy (never mix difficulty scores) → 4 intent (teach/compare/sell → page type) → 5 numbers (page / needs-edge / skip) → 6 clusters (one page per group, never compete with yourself) |
| **On-page SEO** (the 6 rules) | `onpage.py` | per-page audit + one-query build pack: 1 title in searcher's words ≤60 · 2 meta says why you beat the results above · 3 headings phrased as the questions · 4 first words answer the heading · 5 alt text on real photos · 6 anchors matching destination titles — violations feed the work queue as `fix_onpage` |
| **Technical SEO** (the 6 rules) | `technical.py` | 1 three-click structure (BFS over the link graph vs GSC-top pages) · 2 JS-shell vs raw-HTML words (GPTBot reality) · 3 batch urlInspection: indexed vs *Google skipped* + reasons · 4 render-blockers/TTFB main-content-first · 5 viewport/mobile floor + CWV · 6 sitemap keep/remove policy + Google ping & Bing IndexNow submissions — violations feed the queue (`fix_crawlability`, `fix_mobile`, `fix_speed`) |
| AI-citation test | `geo.py` | ask a frontier LLM your buying questions, count brand/domain citations (the guide's experiment) |
| Page scaffolds | `paa.py` | one markdown brief + FAQPage JSON-LD per question page for your agent to fill |
| Orchestration | `orchestrator.py` | `seo_audit`, `content_plan`, `link_building_plan`, `full_pipeline` |
| Dashboard | `dashboard.py` | FastAPI at :8808 — run jobs, view reports, download exports |

## Why the integrations pay out (source → value chain)

```
GSC cache (pulled once/24h) ──┐
PAA question tree            ├─► integrations.py ──► ONE scored work queue
crawl DB (+sitemap xcheck)   │   impact ÷ effort    (opportunities table)
DataForSEO volume            │   per opportunity:
rank snapshots + watchlist    │   fix_title_meta · build_question_page ·
CWV (PageSpeed)              │   promote_to_top5 · repair_cannibalization ·
73-directory tracker (75)    │   internal_link_orphan · cwv_speed ·
AI citation reports          ┘   backlink_submission (DR≥70 first) · improve_ai_visibility
```

Live walk-through: GSC shows " amazon marketing agency " at position 8 with 900 impressions,
the crawler confirms that page has 180 words and 0 internal links pointing to it, the PAA tree
has 4 matching questions → **one** `promote_to_top5` opportunity (score ~4.2 impact/effort) with
the refresh-plan attached, instead of four disconnected reports you have to reconcile by hand.

## Why the schedule keeps it compounding

| Job | Every | What it does |
|---|---|---|
| `nightly_delta` | 24h | GSC 2-day vs prior 7-day mover report + refreshed work queue → digest (Slack webhook or saved report) |
| `opps_refresh` | 12h | re-blend all sources into the queue |
| `weekly_sitemap_check` | 7d | sitemap↔crawl reconciliation |
| `serp_watch` | 24h | rank snapshots of watchlist keywords, you vs your named competitor |
| `outcome_validation` | 24h | measure every closed opportunity whose 7-day after-window has finished, record the verdict once |

`python -m seoforge.cli schedule --daemon` runs the loop in a terminal (or hook it into
`cron`/`schtasks`/`systemd`). Every digest lands in Slack if `SLACK_WEBHOOK_URL` is set.

## Quickstart commands

```bash
python -m seoforge.cli crawl                      # full technical sweep of SEOFORGE_SITE_URL
python -m seoforge.cli audit                      # crawl + facets + links + GEO (+GSC if configured)
python -m seoforge.cli gsc-auth                   # one-time OAuth (needs GSC_CREDENTIALS_PATH)
python -m seoforge.cli gsc-opportunities pages    # per-page GSC table (or striking/cannibal/lowctr)
python -m seoforge.cli paa "best crm for d2c" "how to track amazon sales" --depth 2
python -m seoforge.cli build-list --min-q 2       # question pages to build, like /free-guides
python -m seoforge.cli scaffold --min-q 3         # markdown brief + FAQPage JSON-LD per page
python -m seoforge.cli links                      # internal linking report + suggestions
python -m seoforge.cli facets                     # faceted navigation policy report
python -m seoforge.cli sitemap-check              # sitemap vs crawl crosscheck
python -m seoforge.cli cwv --desktop              # Core Web Vitals (PageSpeed Insights)
python -m seoforge.cli serp "best tools for seo"  # SERP + PAA + auto rank snapshot
python -m seoforge.cli rank-history --keyword "..." | --domain "..."
python -m seoforge.cli indexnow <url1> <url2> --key <key>   # push new pages to engines
python -m seoforge.cli ai-cite "best amazon agency for D2C brands"   # ChatGPT citation test
python -m seoforge.cli add-product '{"id":"hp-1","title":"...","price":"2999","link":"...","image":"..."}'
python -m seoforge.cli feed                       # merchant_center.xml
python -m seoforge.cli dirs --min-dr 70           # highest-DR places from the 75-dir list
python -m seoforge.cli dirs-update ProductHunt submitted
python -m seoforge.cli pr                         # digital PR plan
python -m seoforge.cli watch-add "best crm for d2c" --competitor hubspot.com
python -m seoforge.cli watch-run                  # rank snapshot of watchlist (you vs competitor)
python -m seoforge.cli opps                       # the blended work queue
python -m seoforge.cli opps-refresh
python -m seoforge.cli delta                      # GSC movers now (also refreshes queue + digest)
python -m seoforge.cli schedule-status
python -m seoforge.cli schedule --all             # run every job now; --daemon runs the loop
python -m seoforge.cli value                        # $139.95 Semrush vs your stack
python -m seoforge.cli preferred                    # Google Preferred Sources audit + checklist
python -m seoforge.cli preferred-btn --theme dark   # official "Add to Preferred Sources" button code
python -m seoforge.cli research                     # full 6-rule keyword-research playbook, saved as report
python -m seoforge.cli problems                     # what customers search, in their words (GSC+PAA+drills)
python -m seoforge.cli seeds --products "amazon marketing agency"
python -m seoforge.cli intent "best crm for d2c"    # do the SERP results teach, compare or sell?
python -m seoforge.cli worth "crm for d2c" "email marketing pricing"   # page / needs-edge / skip
python -m seoforge.cli groups                       # cluster keywords so nobody competes with you
python -m seoforge.cli onpage                       # six-rule on-page audit over the crawl DB
python -m seoforge.cli onpage-pack "best crm for d2c"   # title/meta/headings/first-line/alt/links, pre-complied
python -m seoforge.cli technical                   # six technical rules, one report
python -m seoforge.cli structure                   # important pages beyond 3 clicks from home
python -m seoforge.cli crawlability                # JS shells vs raw-HTML words (AI robots)
python -m seoforge.cli index-status                # batch GSC: indexed vs skipped + reasons
python -m seoforge.cli speed && python -m seoforge.cli mobile   # blockers + viewport sweeps
python -m seoforge.cli sitemap-policy              # keep/remove lists + Google/Bing submission
python -m seoforge.cli geo                        # AI-readiness score + GPTBot FAQ test
python -m seoforge.cli bot-connection-add cloudflare_logpush --domain example.com   # secret shown once + what to copy
python -m seoforge.cli bot-connections             # connections + this month's event/raw usage vs caps
python -m seoforge.cli bot-ingest-file log.ndjson.gz --secret sfb_...   # read a log sender's file now (or `-` for stdin)
python -m seoforge.cli bot-report --days 30        # who is reading your site: types/bots/top pages
python -m seoforge.cli bot-prune                   # delete bot rows past their retention
python -m seoforge.cli outcomes                    # measured verdicts for closed work (History's Result column)
python -m seoforge.cli outcomes-validate           # measure every settled closed opportunity now (or one: <id>)
python -m seoforge.cli serve                      # dashboard http://127.0.0.1:8808
python -m seoforge.cli mcp                        # run MCP server for Codex/ChatGPT (stdio)
```

## API keys you need

- **Google Search Console**: OAuth desktop client secret → `gsc-auth` once.
- **DataForSEO** ($3 free credit): keyword volumes, SERP/PAA, backlinks, rank snapshots.
- **OpenSEO**: optional; free tier at openseo.so, attach its MCP for `/seo-audit` skills.
- **PAGESPEED_API_KEY** (optional): free Google API key for reliable quota on `cwv`.
- **OPENAI_API_KEY** (optional): enables scripted `ai-cite` runs; otherwise Codex tests by hand.
- **browser-use**: only for agent submissions; optional.
- **SEOFORGE_DIRECTORY_DB** (SaaS mode): the tenant directory store — defaults to `data/directory.db` (SQLite); point it at `postgresql://user:pass@host/db` in production (psycopg imported lazily; the DDL is identical for both backends).
- **SEOFORGE_SERP_TRACKER_** (optional): the position tracker's scrape setup — `SEOFORGE_SERP_TRACKER_PROVIDER` (default `never`), `SEOFORGE_SERP_TRACKER_API_KEY`, `SEOFORGE_SERP_TRACKER_RETRY` (failed keywords requeue for the hourly lane), `SEOFORGE_SERP_TRACKER_DELAY` (seconds between requests), `SEOFORGE_SERP_TRACKER_STRATEGY` (`basic` / `custom` / `smart`), `SEOFORGE_SERP_TRACKER_PAGES` (default 5), `SEOFORGE_SERP_TRACKER_SMART_FALLBACK` (fall back to the full-HTML read when a subset of pages fails).
- **SEOFORGE_SMTP_** (optional): the position-tracker digest — `SEOFORGE_SMTP_HOST` / `SEOFORGE_SMTP_PORT` / `SEOFORGE_SMTP_USER` / `SEOFORGE_SMTP_PASSWORD`, recipients (`SEOFORGE_SMTP_NOTIFY_EMAIL` is the global fallback; a domain's own `notification_emails` overrides), `SEOFORGE_SMTP_FROM_EMAIL` + `SEOFORGE_SMTP_FROM_NAME` header.

## Roadmap

- [x] v0.1 — all modules as a working scaffold (29 MCP tools)
- [x] v0.2 — cross-source opportunity engine, scheduler + nightly GSC delta, keyword watch, digests (46 tools)
- [x] v0.3 — live OpenSEO (hosted/key/self-host), AEO funnel per category, llms-full.txt, Semrush value counter (54 tools)
- [x] v0.4 — Google Preferred Sources: eligibility audit + official button embed code (56 tools)
- [x] v0.5 — keyword-research playbook: audience problems, seeds, one-tool policy, SERP intent (teach/compare/sell), volume+competition+trend verdicts, keyword clustering (63 tools)
- [x] v0.6 — on-page SEO six rules: crawl now captures h2/h3 + first sentence; per-page audit (title phrase+length, meta why-beats, question headings, immediate answers, alt text, title-matched anchors) + onpage_pack generator; fix_onpage in the work queue (65 tools)
- [x] v0.7 — technical SEO six rules: crawler captures viewport/render-blockers/JS-shell flags; 3-click structure BFS, js-shell crawlability, batch GSC index status, speed+mobile sweeps, sitemap keep/remove policy with Google+Bing submission; technical_audit MCP tool + work-queue wiring (72 tools)
- [x] v0.8 — 48-signal AI-citation rubric: structure · authority · content · trust · E-E-A-T scored from 34 deterministic signals (13 LLM-judged listed deferred, never drag the score), `rubric-audit` CLI + `rubric_audit` MCP tool, product-description opening gate (83 tools)
- [x] v0.9 — AI-bot log ingest: connections with once-shown secrets + generated Worker/Logpush/drain setup, offline verification against IP ranges, monthly event/raw caps with `(other)` fold-over, resend-proof file claims, wait/stale/on bot report; public `/bots/ingest` + connection CRUD, 6 CLI commands + 6 MCP tools (89 tools)
- [x] v0.10 — outcome capture: every closed opportunity measured once with before/after 7-day windows (GSC + rank snapshots + bot reads), 5% count noise floor, lower-is-better position rule, unmeasured-never-zero, verdicts recorded once into History; `mark_done` arms + `outcome_validation` daily job, `outcomes`/`outcomes-validate` CLI + 2 MCP tools (91 tools)
- [x] v0.11 — prompt-positions tracking: buying questions tracked verbatim, AI answers pasted in manually (live fetch deferred) parsed at write time (mentions, first-mention position, own-domain citations, visibility, sentiment, per-competitor citations), per-platform 7-day states, outcome sweep (immutable baseline, interim refresh, day-30 final verdict recorded once), uncrawled-citations alert in the bot report, per-prompt `improve_ai_visibility` queue, daily `prompt_positions` job; `prompts-add`/`prompts-list`/`answer-record`/`prompt-states`/`prompt-sweep` CLI + 4 MCP tools; Brave submit lane (`brave-submit` CLI + `brave_submit` MCP — browser agent fills `search.brave.com/submit-url`, manual steps otherwise; no Bravebot registry entry, since Brave documents no user-agent token of its own) (96 tools)
- [x] v0.12 — tenant shell (the billing gate): cross-dialect directory store (SQLite offline/dev, `postgresql://` production with lazy psycopg — same DDL, TEXT uuid ids, shown-once hashed keys), tenants + `sfk_` API keys (shown exactly once, SHA-256 only), plan gate (active/trialing open; past_due/churned/suspended closed), monthly metering + per-plan quotas, per-tenant engine files via the ambient tenancy context (every `db._conn()` lands in the tenant's own store), `/api/v1/whoami` + `/api/v1/run/{kind}` with Bearer auth at the wire (401/402/429); `tenant-add`/`tenants`/`tenant-status`/`tenant-plan`/`key-issue`/`keys`/`key-revoke` CLI; live Postgres + served key flow verified on the hosted origin (pending seat)
- [x] v0.13 — the position tracker, fully engine-side: migration 7 stores (domains with per-domain device/country/city/scrape-strategy/notification settings, keywords with daily YYYY-M-D history, a retry queue), 11 scraping providers behind one contract (scrapingant, scrapingrobot, serpapi, searchapi, serply, spaceSerp, valueserp, serper, hasdata, crazyserp, plus the proxy HTML fallback) and a 251-entry country codebook; basic / custom / smart scrape strategies with per-page position renumbering, a smart fallback to full-HTML reading when a subset of pages fails, and an unusable-if-half-failed answer instead of a guessed rank; per-domain refresh (parallel on scrapingant/serpapi/searchapi), SQL retry queue with an hourly lane, reference-parity `position_change` (re-entering the results counts as a gain); position insights (avg, best, distribution) + keywords CSV; digest email over SMTP (`SEOFORGE_SMTP_*`, per-domain recipients); `serp-domains` / `serp-add` / `serp-keywords` / `serp-refresh` / `serp-retries` / `serp-insights` / `serp-digest` / `serp-csv` CLI, 9 `serp_*` MCP tools, `serp_refresh` / `serp_retries` / `serp_digest` scheduler jobs (105 tools)
- [ ] JS-rendered crawl mode for SPA/Next sites via playwright
- [ ] content generator: draft the question pages (title/H2/FAQ JSON-LD) with your LLM of choice
- [ ] OpenAI-keyed AI-citation daily trend (citation position tracking in the funnel)
