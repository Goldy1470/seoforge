# SEOForge — end-to-end SEO SaaS + MCP plugin for Codex / ChatGPT

One agent-connectable engine that runs the whole SEO loop:

```
            ┌─────────────────────────────────────────────────────────────┐
            │                     Codex / ChatGPT (MCP)                    │
            │        /seo-audit  /content-plan  /link-plan  ...           │
            └──────────────┬──────────────────────────┬──────────────────┘
                           ▼                          ▼
        ┌────────────┐  ┌───────────┐  ┌──────────┐  ▼            ┌───────────┐
        │ GSC        │  │ Crawler   │  │ PAA tree │  │ DataForSEO / │ │ browser-  │
        │ striking   │  │ (Frog-    │  │ question │  │ OpenSEO       │ │ use agent │
        │ position   │  │ like)     │  │ pages    │  │ keywords/BL   │ │ (browser) │
        └────────────┘  └───────────┘  └──────────┘  └──────────────  └───────────┘
             + internal links + facets + review/FAQ schema + Merchant
               Center feed + AI-visibility (llms.txt, GPTBot, FAQ test)
```

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
| AI visibility / GEO (from the ChatGPT-rec guide) | `geo.py` | llms.txt, GPTBot/OAI-SearchBot/ClaudeBot robots, FAQ "ten-second test", readiness score |
| Orchestration | `orchestrator.py` | `seo_audit`, `content_plan`, `full_pipeline` |
| Dashboard | `dashboard.py` | FastAPI at :8808 — run jobs, view reports, download exports |

## Quickstart commands

```bash
python -m seoforge.cli crawl                      # full technical sweep of SEOFORGE_SITE_URL
python -m seoforge.cli audit                      # crawl + facets + links + GEO (+GSC if configured)
python -m seoforge.cli gsc-auth                   # one-time OAuth (needs GSC_CREDENTIALS_PATH)
python -m seoforge.cli paa "best crm for d2c" "how to track amazon sales" --depth 2
python -m seoforge.cli build-list --min-q 2       # question pages to build, like /free-guides
python -m seoforge.cli links                      # internal linking report + suggestions
python -m seoforge.cli facets                     # faceted navigation policy report
python -m seoforge.cli add-product '{"id":"hp-1","title":"...","price":"2999","link":"...","image":"..."}'
python -m seoforge.cli feed                       # merchant_center.xml
python -m seoforge.cli dirs --min-dr 70           # highest-DR places from the 73 list
python -m seoforge.cli dirs-update ProductHunt submitted
python -m seoforge.cli pr                         # digital PR plan
python -m seoforge.cli geo                        # AI-readiness score + GPTBot FAQ test
python -m seoforge.cli serve                      # dashboard http://127.0.0.1:8808
python -m seoforge.cli mcp                        # run MCP server for Codex/ChatGPT (stdio)
```

## API keys you need

- **Google Search Console**: OAuth desktop client secret → `gsc-auth` once.
- **DataForSEO** ($3 free credit): keyword volumes, SERP/PAA, backlinks.
- **OpenSEO**: optional; free tier at openseo.so, attach its MCP for `/seo-audit` skills.
- **browser-use**: only for agent submissions; optional.

## Roadmap

- [x] v0.1 — all modules above as a working scaffold
- [ ] scheduler: nightly GSC pull → weekly delta report (email/Slack)
- [ ] content generator: draft the question pages (title/H2/FAQ JSON-LD) with your LLM of choice
- [ ] rank tracking history + competitor gap graphs in dashboard
- [ ] multi-model agent mode: Codex/Cursor/Claude adapters
