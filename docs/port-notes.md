# Port notes — Ansvisor → SEOForge

Working log for the port plan (`docs/port-notes.md` per ground rules).
Reference: https://github.com/ansvisor/ansvisor @ `17f1924f`, MIT (Empler AI Inc.),
cloned read-only to `../ansvisor`. Nothing there is edited.

## Phase 0 (foundation) — COMPLETE

### Tasks
- **0.1 test isolation** — `tests/conftest.py` autouse fixture patches
  `settings.db_path` / `settings.exports_dir` onto the settings
  instance; every test runs on its own empty SQLite DB + exports dir.
  `data/seoforge.db` is never touched by the suite. Two tests that silently
  depended on prior DB state were made self-seeding:
  `test_internal_links_on_crawl` (fixture graph in `tests/conftest.py`) and
  `test_recompute_persists_and_queue` (loads directories first). Suite green
  twice in a row from clean state.
- **0.2 versioned migrations** — `seoforge/migrations.py` with
  `schema_migrations(version, name, applied_at)` and an ordered `MIGRATIONS`
  list; `db.get_db()` applies pending migrations. `db.py` rewritten thin;
  schema SQL lives only in `migrations.py`.
- **0.3 crawler correctness** — `asyncio.Queue` + `task_done`/`join`
  (workers park on `queue.get()` instead of terminating when the queue is
  momentarily empty; `max_pages`/`max_depth` preserved); tldextract-based
  `registrable()` with `suffix_list_urls=(), cache_dir=None` (fully offline);
  robots fetched through the async client and parsed via `rp.parse(lines)`
  — `RobotFileParser.read()` (blocking, unguarded HTTP) removed.
- **0.4 packaging/CI** — `__version__` via `importlib.metadata`, pyproject
  0.8.0, browser extras split to `requirements-browser.txt`, GitHub Actions
  CI on 3.11/3.12, `.env.example` personal data removed (SAME_AS / GSC_PROPERTY
  / brand tagging genericized).

### Discrepancies & decisions (code over plan)
- **Migration ordering quirk (keep!)**: for a legacy DB, missing columns must
  be ALTERed onto existing tables *before* running the v1 `executescript`,
  because the script creates indexes that reference columns a legacy table
  may not have yet. Migration 1 therefore: upgrades existing tables column
  by column (by parsing `_SCHEMA`), then runs the script. Verified against a
  **copy** of the real production-shape DB (rows preserved, all columns +
  indexes applied).
- **`SEOFORGE_RESPECT_ROBOTS` was dead config** — the pre-port crawler always
  consulted robots.txt regardless. It is now wired to the robots loader
  (`false` ⇒ robots gating disabled). Default unchanged (`true`).
- **Worker-count == concurrency**: the old semaphore (equal to worker count)
  was redundant and removed; workers now number exactly `max_concurrency`.
  Crawl order start-first (FIFO) instead of the old reversed-sitemap-first
  deque order — deterministic and marginally saner; no behavior asserted
  elsewhere depends on order.
- **Honest note on the race regression test**: the old code would, in many
  single-item-seed scenarios, still complete the crawl (one surviving worker
  processed everything serially); the real historical hazard was the pool
  dying mid-queue with children pending. The new `join()` semantics guarantee
  completion under any scheduling; `test_crawl_discovers_links_after_slow_home`
  locks the plan's acceptance scenario (links only discoverable after a slow
  page completes ⇒ all pages crawled).
- **tldextract IP/fallback behavior**: hosts without a valid suffix (IPs,
  `localhost`) fall back to the full host, matching the prior helper's spirit
  for the ≤2-label case; `urlsplit().hostname` used so ports never leak into
  registrable comparisons.
- **MockTransport content-type**: `httpx.Response(200, text=...)` has no
  `content-type` header, so the crawler (correctly) did not HTML-parse it in
  tests — mock handlers must set the header like real servers do.

### Toolchain / environment record
- pytest was not installed initially; installed `pytest 9.1.1` (local Python
  3.14.6). CI pins 3.11/3.12. `tldextract 5.4.0` added as a runtime dependency.
- Windows / PowerShell; the suite command is `python -m pytest -q` from the
  repo root; commits via
  `git -c user.email=you@example.com -c user.name=seoforge`.
- pyproject had remained at `version = 0.1.0` while the README product is
  v0.7 (72 tools) — bumped to 0.8.0 at the start of the port series.

## Phase 1 (SSRF-safe fetching) — COMPLETE

### What landed
- `seoforge/safe_fetch.py`: blocklist mirrors the reference (IPv4 RFC 6890 set
  + IPv6 transition ranges; IPv4-mapped IPv6 evaluated as its IPv4 value);
  `is_public_address` = blocklist ∧ `ipaddress.is_global`; `check_url`
  (http/https, no credentials, standard ports, public IP literals,
  localhost refused); `fetch()` validates every resolved address and pins the
  request to a validated one (URL host swapped to the IP, real `Host` header,
  `sni_hostname` extension so TLS/SNI + certificate verification use the real
  name), follows ≤5 redirects by hand re-checking every hop, streams the body
  with a 3 MiB cap (truncate mode optional), overall deadline via
  `asyncio.timeout`; typed `FetchError` codes exactly the plan's eight.
- `SEOFORGE_ALLOW_PRIVATE_FETCH` (env-only, default false): bypasses the
  public-address checks for local dev; documented in `.env.example` (empty).
  No MCP tool or dashboard route touches the environment.
- Routed through safe fetch: crawler (robots, sitemaps, every page),
  `geo.check_ai_readiness` (robots/llms/sitemap/FAQ pages), `aeo._fetch`
  (GPTBot-UA fetch + llms-full probe), `indexing` (sitemap walker + robots
  probe), `preferred` (sitemap probe + homepage). CLI `crawl` exits 2 with a
  friendly message; MCP `crawl_site` returns `{"error": code, "message": …}`.

### Discrepancies / deviations (code over plan)
- **Plan assumed geo.py/aeo.py/perf.py/technical.py/indexing.py/onpage.py/
  preferred.py all fetch user URLs.** Grepping the real code:
  `technical.py`/`onpage.py` are DB-rule modules with no direct fetches;
  `perf.py` (PageSpeed), `gsc.py` (Google endpoints), `keywords.py`
  (DataForSEO), `indexnow`/google-ping (fixed hosts), `notify.py`
  (operator-configured Slack webhook) are **fixed vendor endpoints** and stay
  direct httpx — per plan section 4's carve-out.
- **Carrier clients remain**: crawler/geo still construct their own
  `AsyncClient` (also the transport-injection seam for tests); every request
  on those clients is issued by `safe_fetch.fetch`.
- **DNS pinning needs a resolver injection in tests**: the crawler's mock
  transport cannot satisfy real `getaddrinfo` for fake hosts, so
  `crawl_site(..., transport=…, resolver=…)` threads a resolver into
  `safe_fetch.fetch` (mirrors the JS `lookup` test hook). Production keeps
  real pinning.
- **Content-type gate NOT applied to crawler/sitemap/robots fetches**
  (`accept=None`): those legitimately come in different/absent content types
  (the HTML decision is made by the crawl logic, same as before); `safe_fetch`
  still applies SSRF + size/time caps. The gate is active for
  `preferred.audit` (HTML required).
- **Crawler trade-offs recorded**: one DNS resolution per page (could be
  cached later); page bodies over 3 MiB are truncated into the stored row
  instead of persisted whole (bounded memory ≥ correctness trade, noted);
  `http_error` pages are stored with their HTTP status (parity with the old
  `follow_redirects=True` behaviour); blocked/unreachable pages store -1.
- **Verification caveat to watch**: `sni_hostname` support by httpcore is the
  load-bearing assumption for HTTPS pinning (docs say it drives SNI *and*
  certificate verification). The offline mock tests can't prove TLS; the
  first live Phase 2 fetch (AI-bot IP-range JSON) should be treated as the
  smoke test — record a failure here if cert verification breaks.
- `fetch_sync` added for the sync call paths in aeo/preferred (must not be
  called inside a running loop).

### Toolchain
- Suite: 102 tests passing offline (45 new in `tests/test_safe_fetch.py`,
  8 in `tests/test_crawler.py` including the two refusal acceptance tests).

## Phase 2 (AI-bot + AI-source registries) — COMPLETE

### Task 2.1 — AI-bot registry + range verification (`data/ai_bots.json`, `seoforge/ai_bots.py`)
- Registry: 17 agents. 8 verified core bots mirroring the reference list
  (GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, Claude-SearchBot,
  Claude-User, PerplexityBot, Perplexity-User) with the operators' published
  range URLs (all six checked live), plus the 9 agents SEOForge already
  tracked in its extended list (anthropic-ai, google-extended,
  gemini-deep-research, ccbot, applebot-extended, meta-externalagent,
  mistralai-user, youbot, cohere-ai) as `ranges: null, verified: false`.
  Registry order == match order.
- Tolerant range parser: today all three operators' files share the
  `{creationTime, prefixes: [{ipv4Prefix|ipv6Prefix}]}` shape (verified live);
  the parser still handles bare arrays, `{ranges|ips|addresses|ipv4|ipv6}`,
  `prefix|cidr|ip|range` object keys, and skips malformed entries — never
  assuming one format, per plan.
- `ai_bot_ranges` SQLite cache (migration 2; schema versions now `[1, 2]`):
  fresh ≤ 24 h; a failed refresh keeps the last stored list (possibly stale);
  with nothing ever stored, a negative-cache row (empty prefixes_json)
  suppresses live retries for `RANGES_RETRY` (5 min) so bursts don't hammer a
  dead endpoint.
- Verification reasons mirror the ingest design: `unknown_agent` /
  `no_ranges` / `unverified_ip` (spoofed UA = UA matched but IP outside the
  operator's ranges).
- Wiring: `geo.check_ai_readiness` now iterates the registry (17 tokens),
  per-bot entries carry `type`/`verified`, and a new `checks.robots_by_type`
  aggregates `explicitly_named`/`site_blocked` per training|search|user.
  `aeo.stage_found` scores named/total over the registry, reports
  `bots_by_type`, the robots fix bullet names the actual unnamed agents (and
  disappears when all are named), and a note fires when training crawlers are
  blocked while search/user agents pass (common deliberate choice).
  `aeo.AI_AGENTS_EXTENDED` and `geo.AI_BOTS` became derived aliases — the
  registry file is the single source of truth.
- CLI: `ai-bots` (registry view), `ai-bot-verify "<ua>" <ip>`; MCP tools:
  `ai_bots_list`, `ai_bot_verify`.

### Task 2.2 — AI-source registry + classifier (`data/ai_sources.json`, `seoforge/ai_sources.py`)
- Registry: 13 sources, version-3 state — chatgpt.com (hosts incl.
  chat.openai.com; aliases chatgpt/openai/openai.com; Shopping surface
  medium=feed), perplexity.ai, claude.ai, gemini.google.com (bard.google.com),
  copilot.microsoft.com (copilot.com), grok.com, deepseek.com, meta.ai,
  you.com, phind.com, poe.com, labs.google, bing.com (referrerPath /ck/a,
  analytics: false, utm: false). Change history rewritten in SEOForge's own
  words.
- Semantics ported from `ai-sources.js` + the tracking snippet:
  `normalize_source` (lower/trim/one leading `www.`); match = exact alias OR
  host-or-subdomain, never substring; origin-aware counting (`utm: false` /
  `analytics: false` opt-outs); referrer-first → utm_source fallback order;
  the Bing referrer gate; `landing_medium` = first `utm_medium` param, name
  in any case; surface = exact medium, any case ("feed"→shopping,
  "rss-feed"→not); `ai_source_pattern` RE2 helper for GA4.
- `classify(referrer, utm_source, utm_medium, landing_url)` returns
  source + surface + origin, or None (normal outcome).
- CLI `ai-sources` + `ai-classify`; MCP `ai_sources_list` +
  `classify_ai_traffic`.

### Discrepancies / decisions (code over plan)

Task 2.1:
- **geo._score's all-explicitly-named gate became stricter**: populations are
  now all 17 registry bots (old hard-coded list: 9). The score formula is
  untouched; the gate's meaning ("every tracked agent has an explicit
  robots.txt section") must cover everything we track. Recorded here since it
  changes reported `ai_readiness_score` values.
- **Stale-keep window simplified**: the JS kept a stale list usable for one
  further hour after a failed refresh; the port returns the stale list on
  every failed refresh, gated by the 24 h freshness + 5 min retry window.
  Strictly more available, simpler to reason about.
- **Phase 1 TLS caveat resolved**: the first live Phase 2 fetch (manual CLI
  run `ai-bot-verify "GPTBot/1.2" 203.0.113.9`) pulled OpenAI's real range
  file through safe_fetch — DNS pinning, SNI + certificate verification and
  the JSON parse all worked; UA matched, documentation IP correctly outside
  OpenAI's ranges → `unverified_ip`. The manual run legitimately wrote a
  production cache row (it is the CLI, not the test suite; `data/*.db` is
  gitignored).
- The JS module's in-memory pending-read coordination was not ported: the
  SQLite cache is the coordination point (idempotent GET + upsert); no
  consumer double-fetches the same URL within the TTL.
- Not ported from the reference module: `botUsage`-style aggregation maps —
  Phase 4 (log ingest) is their real consumer; they will be designed there,
  over this registry.

Task 2.2:
- **The Bing gate is a per-source skip at the referrer origin, judged on the
  RAW referrer string** — the tracking snippet checks
  `referrer.indexOf(referrerPath) === -1` (substring), not parsed-path
  equality; ported exactly that way, so `www.bing.com/ck/a?…` counts and
  `www.bing.com/search?q=…` does not. (An earlier plan sketch suggested
  path equality — code wins, recorded.)
- **Alias matching is array membership (exact), not contains-on-string** —
  the snippet's `e.a.indexOf(x)` reads the array; `not-perplexity` etc. must
  never match. Locked by tests.
- **The GA4 ("analytics") origin is ported but intentionally unused by
  `classify()`** — the on-site classifier is referrer+utm only, exactly like
  the snippet; the analytics origin (and `ai_source_pattern`) exists for the
  GA4-sync work in a later phase, where the same table decides what counts.
- `classify()` accepts the medium as an explicit `utm_medium` argument with
  the landing URL's own `utm_medium` as fallback — log lines often carry
  both separately; the snippet itself never sees landing URLs, so this is a
  Phase-4-shaped extension of the same rule, not a new matching rule.

### Toolchain
- Suite: 148 tests passing offline — 35 new in `tests/test_ai_bots.py`
  (fixtures use RFC 5737 / 2001:db8 documentation ranges only) + 14 new in
  `tests/test_ai_sources.py` (table-driven over every source in the file).

## Open questions for the human (plan §12 — still pending)
- (Phase 6) scraper-backed provider for consumer AI surfaces — cost + ToS review.
- (Phase 7) Postgres before first paying customers vs SQLite closed beta.
- (Phase 5) outcome verdict wording shown to end users vs internal only.


## Phase 3 (48-signal rubric) — COMPLETE

### Tasks
- **3.1 rubric data + scorer + engine** — `data/rubric.json` (48 signals, 5
  weighted categories, all descriptor strings debranded + rewritten) and
  `seoforge/rubric.py` (loader; `score_audit` partial-coverage +
  re-normalization; `run_signals` engine with throw -> `na`). Committed
  `a97a27c`.
- **3.2 signals + context + product-description gate** —
  `seoforge/rubric_signals.py` (`AuditContext` + `build_audit_context`
  via the SSRF-safe fetcher + 34 `@Signal` evaluators + `DEFERRED_SIGNAL_KEYS`)
  and `seoforge/product_description.py` (opening checker). Committed
  `e08cf57` after the offline smoke pass, which caught one real bug
  (`_STATISTIC.findall` returning tuples into `re.search` -> TypeError;
  fixed with `finditer`).
- **3.3 surfaces + docs** — `audit_url()` orchestration
  (context -> signals -> score -> `db.save_report('rubric_audit', ...)`),
  CLI `rubric-audit <url> [--query]` (`AuditPageError` / `FetchError`
  -> stderr + exit 2), MCP `rubric_audit(url, query)`
  (errors returned as `{error, status, message}`, like the crawl tool),
  README + notices rows, this section.

### Discrepancies & decisions (code over plan)
- **Where the audit context fetch happens**: reference `context.js` uses
  Scrape.do with `render: true` (page JS executes in a headless browser for
  every audit). SEOForge's `build_audit_context` fetches raw HTML through
  `safe_fetch` and renders nothing - the GPTBot view of the page, which is
  the point of the audit. Recorded as a deliberate deviation; if render-quality
  audits are ever needed, that is a browser-agent screenshot-style question
  first (same ToS/cost bucket as the Phase-6 stop-and-ask).
- **`ai-bot-access` matches SEOForge's registry, not the reference's
  7-token list** - the evaluators read `ai_bots.all_bots()` (17 agents), so
  the registry stays the single source of truth for who is tracked. The
  reference test where `CCBot` is absent from a tracked group therefore
  differs: here both agents in a shared group are named when blocked.
- **`normalize_url` mirrors the JS URL parser's quirks, including the
  lenient ones** - `ftp://host` prefix-free input becomes
  `https://ftp/{rest}` (one regex case test); whitespace-in-host and
  no-host inputs raise 400, exactly what `new URL` would reject.
- **Deferred 14**: 13 LLM-judged signals + `brand-entity` are defined in
  `DEFERRED_SIGNAL_KEYS` and returned as deferred lists by `audit_url`;
  they never drag a score. This is the standing stop-and-ask (LLM keys /
  provider ToS / cost per audit) - unblocking that decision lands Phase 3.5+
  (`llm-signals.js` ports to one structured-output call).
- **Upstream untested surface**: the user's execution modules
  (`autopilot`/`auto_submit`/`fix_bundle`/`content_gen`/
  `publisher`) were committed unmodified in five separate commits with the
  suite kept green (their code ships no tests yet); `content_gen`'s env keys
  and `publisher`'s targets are documented in `.env.example` (never
  touching `.env`). Migration 3 (`leads`) + dashboard `/scan` lead
  capture landed as their own commits with the test asserts updated.

### Toolchain
- Suite: 224 tests passing offline - +13 task 3.1 (`tests/test_rubric.py`),
  +17 `tests/test_product_description.py` (reference fixture tables incl.
  brand-story / Speedgoat / French-press, openingCut, productTypeWords,
  attributesIn), +46 `tests/test_rubric_signals.py` (every signal's
  threshold buckets, ai-bot-access group semantics, MockTransport context
  builder with a pinned fake resolver, `audit_url` end-to-end + persistence).
- Phase checks: `python -m seoforge.cli --help` lists `rubric-audit`;
  `import seoforge.mcp_server` registers `rubric_audit` (83 `@mcp.tool`).

## Phase 4 (bot-log ingest) — COMPLETE

### Tasks
- **4.1 core** — migration 4 (`bot_connections` / `bot_events` / `bot_daily`
  / `bot_usage` / `bot_files`) + `seoforge/bot_ingest.py`: page
  normalization, connections + secrets (`sfb_` prefix, SHA-256 stored),
  `_read_request` fact limits, `verify_requests`, the cap write with
  `(other)` fold + crossed80-once, one-transaction file claim, batched
  prune, and the wait/stale/on report. Committed `83224bc`.
- **4.2 sender formats** — `seoforge/bot_log_formats.py` (magic-byte
  gzip inflate to a 16 MB ceiling, 20 000-line cap
  (`LogTooLargeError`), worker/logpush/vercel readers,
  `read_sender_body`), `seoforge/bot_worker_script.py` (pass-through
  edge worker, batch 100 + 10 s debounce, `passThroughOnException`;
  secret read from the `SEOFORGE_BOT_SECRET` Worker Secret, never
  inlined), `seoforge/bot_sender_setup.py` (LOGPUSH_FIELDS, `logpush_job`,
  `vercel_drain`). Committed `8131888`.
- **4.3 surfaces + docs** — dashboard `POST /bots/ingest` (bearer checked
  before the body is read, kind-based readers, 413 `too_large` / 400
  `unreadable_body` / 500 `ingest_failed`, duplicate answered 200) +
  connections CRUD (400 bad kind / 403 locked / 503 not-set-up / 201 with
  the secret-once payload); CLI `bot-connections`,
  `bot-connection-add`, `bot-connection-revoke`, `bot-ingest-file`
  (path or `-` = stdin), `bot-report`, `bot-prune`; MCP
  `bot_connections_list`, `bot_connection_create`,
  `bot_connection_revoke`, `bot_ingest_requests`, `bot_report`,
  `bot_prune` (83 → 89 tools) + `connection_by_id` /
  `current_caps` / `public_ingest_endpoint` helpers;
  `.env.example` keys (`SEOFORGE_PUBLIC_SERVER_URL`, `SEOFORGE_ENV`,
  `SEOFORGE_BOT_EVENT_CAP`, `SEOFORGE_BOT_RAW_CAP`); README +
  notices rows; this section.

### Deviations & decisions (code over plan)
- **Verdict contract**: a request counts as verified when the verdict
  carries a truthy `verified` with a string bot key (the shape
  `ai_bots.verify_bot_request` returns) or a dict-shaped bot record.
  Careful: the real verifier puts `bot` inside its *failure* verdicts too
  (`{"reason": "unverified_ip", "bot": ...}`), so a truthy `bot` alone is
  not a pass. Sync (offline fakes) and async (the real verifier)
  verifiers are both accepted.
- **Transaction split**: `_apply_verified` writes on the caller's open
  transaction (no BEGIN/COMMIT); `_write_verified` wraps it in
  `BEGIN IMMEDIATE` for the batch path. `ingest_log_file` verifies first
  (a range fetch may touch the network and must not hold the write lock),
  then opens one `BEGIN IMMEDIATE` folding duplicate check + claim + raw
  count + write, so a failure leaves nothing behind and the sender's
  retry reads as new.
- **`DELETE ... LIMIT` → `DELETE WHERE id/rowid IN (SELECT ... LIMIT ?)`**
  — stock SQLite has no LIMIT clause on DELETE.
- **Time parsing thresholds**: numeric timestamps are told apart by size —
  > 1e15 is nanoseconds (÷1e6), > 1e12 is milliseconds (as-is), else
  seconds (×1000); ISO strings parse with millisecond precision. No upper
  bound guard: the current epoch is ~1.79e18 ns, so a naive `> 1e18`
  guard rejects valid data dumped by an earlier draft; a far-off time is
  dropped by the +1 h / -7 d window instead.
- **The parse loop never yields**: the reference slices its record loop
  to keep the Node event loop responsive; Python's loop over ≤ MAX_BATCH
  (500) records and the synchronous gzip inflate run inside one
  event-loop step — no cooperative yield needed.
- **`public_ingest_endpoint` ported into `bot_ingest.py`** (the reference
  keeps it in `bot-ingest.js` too): `SEOFORGE_PUBLIC_SERVER_URL`, https
  required, http allowed only for localhost/127.0.0.1/::1 while
  `SEOFORGE_ENV` != "production"; the path from the URL is dropped —
  `/bots/ingest` is fixed.
- **`uncrawled` deferred**: `bot_report` returns `uncrawled: null` with an
  explanatory note until the citation-tracking surfaces exist (Phase 6);
  everything else of the reading (waiting → stale → on alert logic) is
  live now, including the folded-search-reads staleness signal.
- **Caps via env, single tenant**: no plan/brand tree; the usage row is
  keyed by month alone; `SEOFORGE_BOT_EVENT_CAP` / `SEOFORGE_BOT_RAW_CAP`
  (-1 unlimited, 0 = off) replace the reference's plan-guard lookups.
- **Debranding trail**: secret prefix `sfb_` (reference: `abot_`), Worker
  secret name `SEOFORGE_BOT_SECRET` (reference: `ANSVISOR_BOT_SECRET`),
  endpoint env `SEOFORGE_PUBLIC_SERVER_URL` (reference:
  `PUBLIC_SERVER_URL`); no reference branding in any string, docstring or
  output across the new modules, routes, CLI and tools.

### Toolchain
- Suite: 285 tests passing offline - +25 task 4.1 (`tests/test_bot_ingest.py`),
  +15 task 4.2 (`tests/test_bot_log_formats.py`), +15 task 4.3
  (`tests/test_bot_surfaces.py`: TestClient routes, in-proc CLI runs,
  MCP `.fn` tools; the default verifier stubbed offline everywhere; the
  suite never touches `data/seoforge.db`).
- Phase checks: `python -m pytest -q` green before every task commit;
  README/notices/port-notes rows in the task 4.3 commit;
  `python -m seoforge.cli --help` lists the six `bot-*` commands;
  89 `@mcp.tool` decorators.

## Phase 5 (outcome capture) — COMPLETE

### Tasks
- **5.1 core** — migration 5 (`completed_at` / `outcome` / `validation` /
  `validated_at` on `opportunities`, PRAGMA-guarded ALTERs, plus the
  `opportunity_events` trail) + `seoforge/outcomes.py` (windows, verdict
  rules, per-target reads, one-shot validation, sweep) + `mark_done`
  stamping the close and arming the measurement + 20 kernel tests
  (`tests/test_outcomes.py`). Committed `a4bac89`.
- **5.2 surfaces** — scheduler `outcome_validation` job (kind `outcomes`,
  24 h) + the `run_job` branch; CLI `outcomes` (measured verdicts) and
  `outcomes-validate` (one id, or the whole sweep); MCP `outcomes_list`
  and `outcome_validate` (89 → 91 tools); 7 surface tests
  (`tests/test_outcome_surfaces.py`). Committed `69fa469`.
- **5.3 docs** — README module-map row + schedule-table row + quickstart
  lines + roadmap v0.10 (91 tools); notices derivation rows (outcomes,
  the caller side, the tests); this section.

### Deviations & decisions (code over plan)
- **Scope**: the reference's `validate.js` verdict flow + the
  `improve-outcome.js` position kernel + the `action-engine.js`
  validation constants (windowDays 7, meaningfulChangeRatio 0.05).
  `improve-outcome`'s full per-platform prompt-position sweep is deferred
  to Phase 6 — it reads the `prompt_results` data layer (per-run
  per-platform AI-visibility runs) SEOForge does not have yet.
- **Mapping**: reference `actions` → the scored `opportunities` row;
  `action_events` → `opportunity_events`; the reference's per-brand KPI
  basket (ai_visibility, sov, citations…) → the site's own time series:
  GSC clicks/impressions/position for the target's page (and its named
  query), the site's own best `rank_snapshots` position for its keyword
  (registrable-domain match via the crawler's offline PLS rules), and the
  Phase-4 `bot_daily` read counters — the bot report becomes a
  measurable source the same session it was built.
- **Windows**: before = 7 days ending the day before the work was
  raised; after = 7 days starting the day after it closed; wait =
  window + 1 (`VALIDATION_WAIT_DAYS`), and validate refuses while
  `after[1] >= today` — a still-running window reads low and would file
  recent work as a decline. The measurement grain is `yyyy-mm-dd` day
  strings on a UTC clock — windows, cutoffs and window-membership
  compares are plain string compares, never timestamp math.
- **Unmeasured ≠ zero**: no data in a window → `None`, never 0. Nothing
  measured at all → `not_measurable` with `validation` left NULL (no
  invented numbers). Legacy done rows without `completed_at` →
  `not_measurable` once; rows without `created_at` likewise. Still-open
  or not-yet-settled rows → `None` (not ready, nothing written). The
  one real-zero exception is deliberate: `bot_reads` for a page that
  *has* a key but no reads that window is a measured 0 — the counter
  exists and says zero — so a page-target row with only bot data
  available reads `no_meaningful_change`, not `not_measurable`. The
  surfaces tests first assumed otherwise and were corrected to match
  the kernel, not the reverse.
- **Fold rule**: the count derivation leads. When nothing countable was
  measured, the position verdicts fold (`outcome_from_verdicts`); when
  counts read `no_meaningful_change` but a position moved, movement
  wins (`improved`) — mixed movement is never hidden; and a *measured
  declined count stands* even when a position improved.
- **`gsc.extra` override**: `gsc.query` spreads `extra` after
  `_date_range`, so `gsc_window_rows` passes explicit `startDate` /
  `endDate` per window without touching `gsc.py` — and without moving
  the dashboard's own 3-day-lag default. GSC matching is bucket-based:
  the page bucket via `normalize_page_path` (scheme/host/query/fragment/
  trailing slash gone, lowercased), a named query filtering to that
  query's rows, and a query-only target matching every page the query
  lands on. GSC read failures — including a disconnected property —
  read as unmeasured, so `SystemExit` (a `BaseException`, not an
  `Exception`) is caught explicitly; the first draft's bare
  `except Exception` let it escape.
- **Idempotency**: a verdict is recorded once — `validate_opportunity`
  early-returns an already-answered (non-pending) outcome without
  writing; the sweep's WHERE clause backs that. `mark_done` stamps
  `completed_at` via COALESCE (a re-close never moves the windows) and
  records the `opportunity_done` event only on the open→done
  transition, so History shows the close once.
- **`target_parts` is type-aware**: the cannibalization rule writes the
  bare query as its target (`primary["page"] or q`), so a non-URL,
  non-path target with no scheme parse is treated as a query *for that
  type only* — free text like `ProductHunt (DR 90)` stays a target with
  no measurable object instead of being guessed into a fake query.
- **Trail stamps**: `opportunity_events.at` uses the wall clock
  (`db.now()`); piping the pinned measurement *date* into the event
  row originally tripped sqlite3's deprecated date-to-text adapter —
  the day only belongs in the `measuredAt` data field.
- **Sweep shape**: `LIMIT 500` oldest-close-first, best-effort per row,
  unexpected per-row errors count as `failed` and never stop the rest.
- **Verdict wording**: `improved` / `declined` / `no_meaningful_change`
  / `not_measurable` mirror the reference's outcome vocabulary; the
  "end-user words vs internal words" stop-and-ask stays open (asked in
  the phase report with History-column examples).

### Toolchain
- Suite: 312 tests passing offline — +20 task 5.1 (`tests/test_outcomes.py`:
  window math, both verdict rules at the floor boundaries, target parts,
  GSC buckets, rank/bot reads, one-shot validation, sweep, mark_done),
  +7 task 5.2 (`tests/test_outcome_surfaces.py`: the default job, the
  sweep job through the real kernel, CLI and MCP round trips);
  `tests/test_migrations.py` steady at 3. GSC window reads are faked
  everywhere; the suite never touches `data/seoforge.db`.
- Phase checks: `python -m pytest -q` green before every task commit;
  README/notices/port-notes rows in the task 5.3 commit;
  `python -m seoforge.cli --help` lists `outcomes` / `outcomes-validate`;
  91 `@mcp.tool` decorators.

## Phase 6 (citation tracking + prompt positions) — COMPLETE

### Tasks
- Task 6.1 (`90f0a51`) — the prompt kernel (`seoforge/prompts.py`,
  migration 6: `prompts` + `prompt_results`, both append-only):
  buying questions tracked verbatim and idempotent by text, the fixed
  7-platform answer vocabulary, deterministic parse at record time
  (mentions, first-mention position, own-domain citations via
  host-or-subdomain matching, visibility score with half-up rounding,
  sentiment, per-competitor citation counts), per-platform 7-day
  states, the PAA + watchlist seed loader, part of the prompt seed
  tests (`tests/test_prompts.py`).
- Task 6.2 (`f3bf5cb`) — the outcome machinery: `outcome_sweep` (lazy
  immutable baseline ending the day before the work was raised,
  interim current-window refresh while open or inside the 30 tracking
  days, day-30 final verdict recorded once with a
  `position_outcome_recorded` event), `bot_ingest._cited_not_crawled`
  and the alert-gated report (an honest empty list when nothing is
  cited but unread), the per-prompt `improve_ai_visibility` gap queue
  in `integrations.py` replacing the stale whole-report lane, and the
  daily `prompt_positions` scheduler job (`tests/test_prompt_wiring.py`;
  one Phase-4 bot-report test contract updated: `uncrawled` answers
  `[]` + an explicit `uncrawled_count` when the alert is on).
- Task 6.3 (`1d204fc`) — the surfaces + docs:
  `prompts-add` / `prompts-list` / `answer-record` (space- or
  comma-separated citation URLs, `--sentiment`, `--at`) /
  `prompt-states` (one prompt or all) / `prompt-sweep` CLI commands;
  `prompt_create` / `prompt_record_answer` / `prompt_states` /
  `prompt_gap` MCP tools (91 → 95); README roadmap + feature-table
  rows; THIRD_PARTY_NOTICES derivation rows; this section
  (`tests/test_prompt_surfaces.py`, 4 tests).
- Task 6.4 (this commit) — the Brave submit lane: no `data/ai_bots.json`
  entry (decision below); `browser_agent.brave_submit_url` fills
  Brave's documented re-fetch form (`search.brave.com/submit-url`) with
  browser-use and always reports what the page answered;
  `indexing.brave_submit` is the graceful handoff — manual steps when
  the browser agent is not installed, `invalid` for non-URLs, an honest
  `failed` status when the page refuses — carrying the three
  operator-documented facts as guidance; `brave-submit` CLI +
  `brave_submit` MCP tool (95 → 96); `tests/test_brave_submit.py`
  (+4, fixture-faked agent, no page ever opened).

### Decisions & deviations (code over plan)
- No Bravebot entry in `data/ai_bots.json`: Brave's own crawler page
  documents that the crawler "does not advertise a differentiated user
  agent", and ingest verification compiles registry user-agent tokens —
  a fabricated token would make every bot-report attribution a
  fiction. The registry stays verification-only; the submit lane
  carries the documented facts instead (Googlebot-crawlability gate,
  robots.txt vs noindex split, nothing attributable in the report).
- Live AI-answer fetching is deliberately deferred: an answer enters
  as a manual paste-in (`source='manual'`) and is parsed by exactly
  the rules a fetched answer would get. No surface pretends to ask
  ChatGPT — `ai_citation_check` (Phase 1) stays the scripted lane;
  prompt positions stay the measured-what-you-paste lane.
- The platform list is a fixed, versioned 7-entry constant
  (`PLATFORMS`, shopping surfaces excluded as the reference's own
  Insights decision did) — no per-user platform configuration in this
  phase; naming a platform never fetches one.
- Single-tenant collapses: brand identity comes from settings (name +
  the site's registrable domain via `outcomes.site_domain`) instead of
  the reference's brand rows; one prompt namespace, one citation
  namespace.
- Rounding parity kept where Task 6.1 verified it: the visibility
  score uses the reference's half-up integer rounding
  (`_round_half_up`), the mention-rate cut sits at the same 0.5, and
  the position-change floor is the same 0.5 — floors and boundaries
  have dedicated tests.
- Own-domain citation matching is exact host or subdomain
  (`host == d or host.endswith("." + d)`); the reference's withdrawn
  substring check never carried over (it matched the brand inside
  other sites' paths).
- Surfaces map kernel `ValueError`s (unknown platform, unknown prompt,
  empty text) to `{"error": "invalid", ...}` answers instead of raising
  through CLI/MCP; the kernel keeps raising for programmatic callers.
- Unported on purpose: the reference completions flow ("Padre") and
  the prompt-improve UI helpers — SEOForge's product surface here is
  the CLI/MCP set plus the dashboard, not the reference's app screens.
- The Phase-4 bot-report contract updated in Task 6.2 is a deliberate
  breaking-shape change recorded above: `uncrawled` is a list, and
  `uncrawled_count` was added, so honest readings do not hide behind a
  placeholder `None`.

### Toolchain
- Suite as observed after each run: 312 through Phase 5; 379 after
  Task 6.2 (earlier per-task sums drifted by 3 test-collection
  differences attributed to `test_execution.py` pickup — the observed
  full-suite count after every run is the only baseline); 383 after
  Task 6.3 (+4 `tests/test_prompt_surfaces.py`: CLI round trips for
  the five commands, MCP round trips including every invalid path, the
  seven-platform `no_runs` state grid, the gap raising only weak
  states); 387 after Task 6.4 (+4 `tests/test_brave_submit.py`: the
  no-browser handoff with the manual steps, the invalid-URL answer,
  the agent-filled origin default, the honest failure path, and CLI/MCP
  round trips). GSC, AI-answer fetching and the browser agent are
  faked or stubbed everywhere; the suite never touches
  `data/seoforge.db` and never opens the submit page.
- Phase checks: `python -m pytest -q` green before every commit;
  README/port-notes rows in the task 6.4 commit (the Brave lane is
  SEOForge-original — no third-party derivation, no notices row);
  `python -m seoforge.cli --help` lists `prompts-add` / `prompts-list`
  / `answer-record` / `prompt-states` / `prompt-sweep` /
  `brave-submit`; 96 `@mcp.tool` decorators.
- Clock pinning: prompt tests monkeypatch `prompts._today` (the sweep
  additionally accepts `now=`, the scheduler calls it bare with its
  defaults); the scheduler's bot-report keeps the real clock and its
  tests seed stamps from `dt.date.today()`, as Phase 4 does.
