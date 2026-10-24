"""SEOForge - end-to-end SEO SaaS + MCP server for Codex/ChatGPT.

Modules: crawler, gsc, keywords, paa, backlinks, internal_links, facets,
struct (schema), merchant, geo, browser_agent, orchestrator, mcp_server, dashboard.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _md_version

try:
    __version__ = _md_version("seoforge")
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0.0.0.dev0"
