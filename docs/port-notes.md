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

## Open questions for the human (plan §12 — still pending)
- (Phase 6) scraper-backed provider for consumer AI surfaces — cost + ToS review.
- (Phase 7) Postgres before first paying customers vs SQLite closed beta.
- (Phase 5) outcome verdict wording shown to end users vs internal only.
