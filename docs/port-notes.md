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

## Open questions for the human (plan §12 — still pending)
- (Phase 6) scraper-backed provider for consumer AI surfaces — cost + ToS review.
- (Phase 7) Postgres before first paying customers vs SQLite closed beta.
- (Phase 5) outcome verdict wording shown to end users vs internal only.
