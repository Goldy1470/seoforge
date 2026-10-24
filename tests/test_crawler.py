import asyncio
import httpx
import pytest

from seoforge import crawler
from seoforge.config import settings

BASE = "http://testsite.local"


def _handler_factory(home_delay: float = 0.3, sitemap_status: int = 404):
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
        if path == "/sitemap.xml":
            return httpx.Response(sitemap_status, text="no sitemap")
        if path == "/":
            await asyncio.sleep(home_delay)
            body = "<html><title>Home</title><a href='/one'>one</a><a href='/two'>two</a><a href='/secret'>secret</a></html>"
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        if path in ("/one", "/two"):
            return httpx.Response(
                200,
                text=f"<html><title>{path}</title><p>page body with enough words to not be thin</p></html>",
                headers={"content-type": "text/html"},
            )
        if path == "/secret":
            return httpx.Response(200, text="<html><title>Secret</title></html>", headers={"content-type": "text/html"})
        return httpx.Response(404, text="gone")

    return handler


def _crawl(monkeypatch, **kw):
    monkeypatch.setattr(settings, "max_concurrency", 3)
    monkeypatch.setattr(settings, "max_pages", 50)
    transport = httpx.MockTransport(_handler_factory(**kw))
    return asyncio.run(
        crawler.crawl_site(BASE, transport=transport, resolver=lambda h: "93.184.216.34")
    )


def test_crawl_discovers_links_after_slow_home(monkeypatch):
    """Regression: with N workers and only (/) initially queued, the empty
    queue must not terminate the other workers before the slow home page
    enqueues its children."""
    result = _crawl(monkeypatch, home_delay=0.3)
    assert result["pages"] >= 3  # /, /one, /two
    assert result["summary"]["total_urls"] >= 3
    with crawler.db._conn() as conn:
        urls = {r["url"] for r in conn.execute("SELECT url FROM pages")}
    assert {BASE + "/", BASE + "/one", BASE + "/two"} <= urls


def test_robots_disallow_blocks_fetch(monkeypatch):
    result = _crawl(monkeypatch)
    with crawler.db._conn() as conn:
        rows = {r["url"]: r["status"] for r in conn.execute("SELECT url, status FROM pages")}
    # blocked by robots -> recorded as declined (-1), never fetched
    assert rows.get(BASE + "/secret") == -1
    assert rows.get(BASE + "/one") == 200


def test_registrable_domain_uses_public_suffix_list():
    regs = {
        crawler._registrable("https://a.example.co.uk/page"),
        crawler._registrable("https://b.example.co.uk/page"),
        crawler._registrable("https://example.co.uk"),
    }
    assert regs == {"example.co.uk"}
    assert crawler._registrable("https://www.brand.io/x") == "brand.io"


def test_registrable_host_fallbacks():
    assert crawler._registrable("https://example.com:8443/p") == "example.com"
    assert crawler._registrable("https://192.168.1.4/p") == "192.168.1.4"


@pytest.mark.parametrize("url", ["https://example.co.uk/g", "https://example.com"])
def test_registrable_no_port_in_output(url):
    assert ":" not in crawler._registrable(url)


def test_crawl_refuses_private_address_by_default():
    from seoforge.safe_fetch import FetchError

    with pytest.raises(FetchError) as e:
        asyncio.run(crawler.crawl_site("http://127.0.0.1:8000"))
    assert e.value.code in ("blocked", "invalid_url")


def test_crawl_refuses_loopback_even_on_default_port():
    from seoforge.safe_fetch import FetchError

    with pytest.raises(FetchError) as e:
        asyncio.run(crawler.crawl_site("http://127.0.0.1/"))
    assert e.value.code == "blocked"
