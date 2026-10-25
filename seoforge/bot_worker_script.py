"""The Cloudflare Worker a site installs to send AI-bot requests (option A).
It passes every request through untouched, and only for requests whose user
agent names a bot on the list sends {userAgent, ip, url, status, at,
country} to SEOForge in batches of about 100, after the response, without
slowing the page.

The bot tokens come from the registry (data/ai_bots.json), so the edge
filter and the server's check use the same list; the server still verifies
every IP.

The connection secret isn't in the code: the person adds it to the Worker
as a Secret named SEOFORGE_BOT_SECRET, so it doesn't sit in the source a
teammate copies, a version keeps or a screenshot shows. Without it the
Worker reports nothing and the site is unaffected.
"""
from __future__ import annotations

import json

from . import ai_bots

#: Requests a Worker isolate holds before it sends them.
WORKER_BATCH = 100

#: The Worker Secret (Settings › Variables and Secrets) the script reads the
#: connection secret from.
WORKER_SECRET_NAME = "SEOFORGE_BOT_SECRET"

_SCRIPT = """// SEOForge AI bot report — Cloudflare Worker.
// Passes every request through; for AI bots only, sends the request line to
// SEOForge after the response. Route it on your site's hostname, and add the
// connection secret under Settings › Variables and Secrets as a Secret named
// {SECRET_NAME}.
const ENDPOINT = {ENDPOINT};
const BOTS = {TOKENS};
const BATCH = {BATCH};
let queue = [];

function pageUrl(url) {{
  const u = new URL(url);
  return u.origin + u.pathname;
}}

function isAiBot(ua) {{
  const s = (ua || '').toLowerCase();
  return BOTS.some((t) => s.includes(t));
}}

async function flush(secret) {{
  if (queue.length === 0) return;
  const requests = queue;
  queue = [];
  try {{
    await fetch(ENDPOINT, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json', Authorization: 'Bearer ' + secret }},
      body: JSON.stringify({{ requests }}),
    }});
  }} catch (err) {{
    // Never let reporting affect the site.
  }}
}}

export default {{
  async fetch(request, env, ctx) {{
    // Should this script fail, the request still reaches the site.
    ctx.passThroughOnException();
    const response = await fetch(request);
    const secret = env && env.{SECRET_NAME};
    const ua = request.headers.get('user-agent');
    // No secret added yet: nothing is reported, and the site is unaffected.
    if (secret && isAiBot(ua)) {{
      queue.push({{
        userAgent: ua,
        ip: request.headers.get('cf-connecting-ip'),
        // The page, without its query string.
        url: pageUrl(request.url),
        status: response.status,
        at: new Date().toISOString(),
        country: request.cf && request.cf.country,
      }});
      if (queue.length >= BATCH) ctx.waitUntil(flush(secret));
      else ctx.waitUntil(new Promise((r) => setTimeout(r, 10000)).then(() => flush(secret)));
    }}
    return response;
  }},
}};
"""


def build_worker_script(endpoint: str) -> str:
    """The Worker's source, with the endpoint and the registry's tokens in it
    and the secret read from a Worker Secret, never from the code."""
    tokens = [bot.userAgent.lower() for bot in ai_bots.all_bots()]
    return _SCRIPT.format(
        SECRET_NAME=WORKER_SECRET_NAME,
        ENDPOINT=json.dumps(endpoint),
        TOKENS=json.dumps(tokens),
        BATCH=WORKER_BATCH,
    )
