import asyncio

import httpx
import pytest

from seoforge import safe_fetch
from seoforge.safe_fetch import FetchError, fetch, check_url, is_public_address

PUBLIC = "93.184.216.34"  # a public example address (never actually connected)


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _html(text="<html><body>hi</body></html>", status=200, ct="text/html"):
    return httpx.Response(status, text=text, headers={"content-type": ct})


# ---------- URL checks ----------

@pytest.mark.parametrize("ip", [
    "0.0.0.1", "10.1.2.3", "100.64.1.1", "127.0.0.1", "169.254.169.254",
    "172.16.0.5", "192.0.0.9", "192.0.2.9", "192.88.99.1", "192.168.1.1",
    "198.18.1.1", "198.51.100.7", "203.0.113.9", "224.0.0.9", "240.0.0.1",
])
def test_is_public_address_blocks_every_range(ip):
    assert is_public_address(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
def test_is_public_address_allows_public(ip):
    assert is_public_address(ip) is True


@pytest.mark.parametrize("ip", ["::", "::1", "100::1", "2001::1", "2001:db8::1",
                                "2002::1", "fc00::1", "fe80::1", "ff00::1"])
def test_is_public_address_blocks_ipv6_ranges(ip):
    assert is_public_address(ip) is False


def test_internal_cloud_metadata_blocked():
    with pytest.raises(FetchError) as e:
        check_url("http://169.254.169.254/latest/meta-data/")
    assert e.value.code == "blocked"


def test_ipv4_mapped_ipv6_evaluated_as_ipv4():
    assert is_public_address("::ffff:127.0.0.1") is False
    assert is_public_address("::ffff:8.8.8.8") is True
    with pytest.raises(FetchError) as e:
        check_url("http://[::ffff:10.0.0.5]/")
    assert e.value.code == "blocked"


def test_url_with_credentials_invalid():
    with pytest.raises(FetchError) as e:
        check_url("http://user:pass@example.com/")
    assert e.value.code == "invalid_url"


def test_non_standard_port_invalid_unless_allowed():
    with pytest.raises(FetchError) as e:
        check_url("http://example.com:8080/")
    assert e.value.code == "invalid_url"
    assert check_url("http://example.com:8080/", ports=("8080",))  # test escape hatch


def test_non_http_scheme_invalid():
    with pytest.raises(FetchError) as e:
        check_url("ftp://example.com/")
    assert e.value.code == "invalid_url"


def test_check_url_drops_fragment_keeps_path():
    u = check_url("https://example.com/a/b?x=1#frag")
    assert str(u) == "https://example.com/a/b?x=1"


# ---------- fetch behaviour (offline, MockTransport) ----------

def run(coro):
    return asyncio.run(coro)


def test_fetch_pins_request_to_resolved_ip():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        seen["host"] = request.headers.get("host")
        return _html("<html><title>ok</title></html>")

    async def main():
        async with mock_client(handler) as c:
            return await fetch("https://testsite.example/x", resolver=lambda h: PUBLIC, client=c)

    r = run(main())
    assert seen["url"].host == PUBLIC          # connected address is validated one
    assert seen["host"] == "testsite.example"  # Host header keeps the real name
    assert r.url.startswith("https://testsite.example")
    assert r.status == 200
    assert "<html" in r.text


def test_fetch_accept_list_gates_content_type():
    async def main():
        async with mock_client(lambda req: _html(ct="application/pdf")) as c:
            await fetch("http://93.184.216.34/x", client=c)

    with pytest.raises(FetchError) as e:
        run(main())
    assert e.value.code == "not_html"

    async def main2():
        async with mock_client(lambda req: httpx.Response(200, json={"ok": 1}, headers={"content-type": "application/json"})) as c:
            return await fetch("http://93.184.216.34/x", accept=("application/json",), client=c)

    assert run(main2()).text == '{"ok":1}'  # type: ignore[index]


def test_fetch_accept_plain_text():
    async def main():
        async with mock_client(lambda req: httpx.Response(200, text="User-agent: *", headers={"content-type": "text/plain"})) as c:
            return await fetch("http://93.184.216.34/robots.txt", accept=("text/plain",), client=c)

    assert "User-agent" in run(main()).text


def test_redirect_revalidated_second_hop_blocked():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "http://169.254.169.254/e"}, text="")
        return _html()

    async def main():
        async with mock_client(handler) as c:
            await fetch("http://93.184.216.34/start", client=c)

    with pytest.raises(FetchError) as e:
        run(main())
    assert e.value.code == "blocked"
    assert len(calls) == 1  # blocked before the private hop was requested


def test_name_resolving_to_public_and_private_blocked():
    def handler(request):  # pragma: no cover - must never be reached
        return _html()

    async def main():
        async with mock_client(handler) as c:
            await fetch("http://rebind.example/", resolver=lambda h: [PUBLIC, "127.0.0.1"], client=c)

    with pytest.raises(FetchError) as e:
        run(main())
    assert e.value.code == "blocked"


def test_dotted_and_hex_numeric_hosts_blocked_via_resolver():
    # JS normalises these in new URL(); we catch them via getaddrinfo answers.
    # The fake resolver answers with what a real resolver would (loopback), the
    # validation layer then rejects it.
    def handler(request):  # pragma: no cover
        return _html()

    async def main(host):
        async with mock_client(handler) as c:
            await fetch(f"http://{host}/", resolver=lambda h: ["127.0.0.1"], client=c)

    for host in ("2130706433", "0x7f.1"):
        with pytest.raises(FetchError) as e:
            run(main(host))
        assert e.value.code == "blocked"


def test_too_many_redirects():
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return httpx.Response(302, headers={"location": f"/s{n['i']}"}, text="")

    async def main():
        async with mock_client(handler) as c:
            await fetch("http://93.184.216.34/start", client=c)

    with pytest.raises(FetchError) as e:
        run(main())
    assert e.value.code == "too_many_redirects"
    assert n["i"] == 6  # initial + 5 followed hops answered, no more


def test_body_cap_refused_and_truncated(monkeypatch):
    body = "x" * (safe_fetch.MAX_BYTES + 10)

    async def refused():
        async with mock_client(lambda req: httpx.Response(200, text=body, headers={"content-type": "text/html"})) as c:
            return await fetch("http://93.184.216.34/big", client=c)

    with pytest.raises(FetchError) as e:
        run(refused())
    assert e.value.code == "too_large"

    small = "x" + "y" * 99
    async def truncated():
        async with mock_client(lambda req: httpx.Response(200, text=body, headers={"content-type": "text/html"})) as c:
            return await fetch("http://93.184.216.34/big", max_bytes=100, truncate=True, client=c)

    r = run(truncated())
    assert r.truncated is True
    assert len(r.body_bytes) == 100


def test_timeout():
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2.0)
        return _html()

    async def main():
        async with mock_client(handler) as c:
            await fetch("http://93.184.216.34/", timeout=0.2, client=c)

    with pytest.raises(FetchError) as e:
        run(main())
    assert e.value.code == "timeout"


def test_unreachable_maps_transport_error():
    class Fail(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("connection refused")

    async def main():
        async with httpx.AsyncClient(transport=Fail()) as c:
            await fetch("http://93.184.216.34/", client=c)

    with pytest.raises(FetchError) as e:
        run(main())
    assert e.value.code == "unreachable"


def test_http_error_carries_status():
    async def main():
        async with mock_client(lambda req: httpx.Response(404, text="nf", headers={"content-type": "text/html"})) as c:
            await fetch("http://93.184.216.34/", client=c)

    with pytest.raises(FetchError) as e:
        run(main())
    assert e.value.code == "http_error" and e.value.status == 404


def test_private_fetch_env_allows_loopback(monkeypatch):
    monkeypatch.setenv("SEOFORGE_ALLOW_PRIVATE_FETCH", "true")
    async def handler(request: httpx.Request) -> httpx.Response:
        return _html("<html><title>local</title></html>")

    async def main():
        async with mock_client(handler) as c:
            return await fetch("http://127.0.0.1:9000/local", allow_ports=("9000",), client=c)

    r = run(main())
    assert r.status == 200
