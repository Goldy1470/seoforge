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
| Google Search Console | `gsc.py` | striking-distance queries (pos 5–20), cannibalization, low CTR, URL inspection |
| SERP + keyword data | `keywords.py` | DataForSEO SERP/PAA/keyword-ideas/backlinks, OpenSEO passthrough |
| **PAA → question pages** (the highprimeco method — PAA box as a build list) | `paa.py` | `paa_tree`, `question_page_plan`, CSV export to build pages like `/free-guides` |
| Backlinks: the 73 startup places | `backlinks.py` + `data/directories_73.csv` | tracker (todo→submitted→live), DR filter, digital-PR plan |
| Browser work | `browser_agent.py` | browser-use: directory submissions, verify listings, SERP/PAA click-through, screenshots |
| Internal linking | `internal_links.py` | graph + PageRank + orphans + TF-IDF anchor suggestions |
| Faceted navigation | `facets.py` | classify params (facet/pagination/tracking) → noindex,follow / canonical / robots rules |
| Review & FAQ schema on products | `struct.py` | Product + AggregateRating + Review + FAQPage JSON-LD generators & validator |
| Merchant Center | `merchant.py` | products DB → `g:` XML feed |
| Rank tracking | `keywords.py` | every SERP run snapshots positions into `rank_snapshots`; `rank-history` diffs over time |
| Core Web Vitals | `perf.py` | PageSpeed Insights mobile+desktop: PSI score, LCP/TBT/CLS, CrUX, fix hints |
| Indexing push | `indexing.py` | IndexNow (Bing/Yandex/Seznam), Google sitemap ping, IndexNow key generator |
| Sitemap hygiene | `indexing.py` | sitemap↔crawl crosscheck: 404s/noindex/thin/canonical drift, pages missing from sitemap |
| Cross-source value layer | `integrations.py` | gsc cache + PAA + crawl + CWV + tracker + rank history → one scored opportunity queue |
| Scheduler | `scheduler.py` | nightly GSC delta, keyword watch, due-job tick + daemon |
| Digests | `notify.py` | Slack webhook or saved report briefs |
| Live OpenSEO | `openseo.py` | hosted-MCP / API-key / self-host modes, setup commands, 12-skill bridge |
| AEO/GEO funnel | `aeo.py` | found → answered → cited → recommended → chosen, category-wise scores + fixes, llms-full.txt |
| Value vs Semrush | `value.py` | 16-module coverage matrix + $139.95 vs stack cost counter |
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
73-directory tracker         │   internal_link_orphan · cwv_speed ·
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
python -m seoforge.cli dirs --min-dr 70           # highest-DR places from the 73 list
python -m seoforge.cli dirs-update ProductHunt submitted
python -m seoforge.cli pr                         # digital PR plan
python -m seoforge.cli watch-add "best crm for d2c" --competitor hubspot.com
python -m seoforge.cli watch-run                  # rank snapshot of watchlist (you vs competitor)
python -m seoforge.cli opps                       # the blended work queue
python -m seoforge.cli opps-refresh
python -m seoforge.cli delta                      # GSC movers now (also refreshes queue + digest)
python -m seoforge.cli schedule-status
python -m seoforge.cli schedule --all             # run every job now; --daemon runs the loop
python -m seoforge.cli geo                        # AI-readiness score + GPTBot FAQ test
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

## Roadmap

- [x] v0.1 — all modules as a working scaffold (29 MCP tools)
- [x] v0.2 — cross-source opportunity engine, scheduler + nightly GSC delta, keyword watch, digests (46 tools)
- [x] v0.3 — live OpenSEO (hosted/key/self-host), AEO funnel per category, llms-full.txt, Semrush value counter (54 tools)
- [ ] JS-rendered crawl mode for SPA/Next sites via playwright
- [ ] content generator: draft the question pages (title/H2/FAQ JSON-LD) with your LLM of choice
- [ ] OpenAI-keyed AI-citation daily trend (citation position tracking in the funnel)
