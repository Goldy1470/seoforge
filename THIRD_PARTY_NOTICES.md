# Third-party notices

SEOForge re-implements (not copies) logic, data vocabulary and file shapes from
the open-source project **Ansvisor** (https://github.com/ansvisor/ansvisor),
pinned at reference commit `17f1924f`.

Ansvisor is distributed under the MIT License, copyright (c) 2026 Empler AI Inc.:

```
MIT License

Copyright (c) 2026 Empler AI Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Derivations

| SEOForge file | Derives from Ansvisor | Notes |
|---|---|---|
| `seoforge/safe_fetch.py` | `server/src/lib/safe-fetch.js` | SSRF-safe fetch semantics (blocklist, DNS-rebinding-resistant pinning, manual redirect re-validation, byte/time caps, typed error codes) re-implemented for httpx/asyncio; accept-list extended beyond HTML for robots/sitemaps/JSON. |
| `tests/test_safe_fetch.py` | behaviour matrix of `safe-fetch.js` + `fetchPage` test hooks | offline httpx.MockTransport harness; resolver/ports knobs mirror the JS `lookup`/`ports` test hooks. |
| `data/ai_bots.json` | `server/src/lib/ai-bots.json` | registry structure (versioned file, key/name/operator/type/userAgent/ranges), the 8 verified bots and their operator-published range URLs re-entered; 9 extended agents added (ranges null, verified false); change-history text rewritten in SEOForge's own words. |
| `seoforge/ai_bots.py` | `server/src/lib/ai-bots.js` | verification semantics re-implemented in Python: token-boundary UA matching, published-range IP verification, 24 h cache with stale-keep-on-failure and a short negative-cache retry gate. Storage moved from in-memory JS maps to SQLite (`ai_bot_ranges`, migration 2); fetching goes through SEOForge's SSRF-safe fetcher; the range parser is intentionally tolerant of several published JSON shapes (verified live: OpenAI, Anthropic and Perplexity currently share one `prefixes:[{ipv4Prefix,ipv6Prefix}]` shape). |
| `tests/test_ai_bots.py` (+ `tests/fixtures/ai_bot_ranges/*.json`) | behaviour matrix of `ai-bots.js` | offline MockTransport harness; fixture payloads are synthetic (RFC 5737/2001:db8) shape samples, not operator data. |
| `data/ai_sources.json` | `server/src/lib/ai-sources.json` | 13 sources with their hosts, aliases, the ChatGPT Shopping surface and Bing's `/ck/a` referrer gate re-entered; the version-3 change history rewritten in SEOForge's own words. |
| `seoforge/ai_sources.py` | `server/src/lib/ai-sources.js` + `server/src/lib/tracking-script.js` | classifier semantics re-implemented in Python: normalize (lower/trim/one leading `www.`), origin-aware counting (`utm: false`, `analytics: false` opt-outs), exact alias or host/subdomain matching (substring matching deliberately absent), the referrer → utm_source order and the referrerPath gate from the tracking snippet, `landingMedium` (first `utm_medium`, name in any case), exact-medium surface lookup, and the RE2 `aiSourcePattern` for GA4 queries. |
| `tests/test_ai_sources.py` | behaviour matrix of `ai-sources.js` + `.test.js` + `tracking-script.js` tests | table-driven over every source in the file, including the Bing gate, the shopping surface and the substring-mismatch trap. |

## dir-derivation rules honoured

- No Ansvisor branding, product URLs or self-referential how-to text in rewritten strings.
- Logic re-implemented in Python; JS not copied line-by-line.
- Reference tree is never modified; only read.
