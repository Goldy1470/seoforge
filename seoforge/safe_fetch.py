"""SSRF-safe URL fetching for user-supplied addresses.

A URL typed by a user is checked like one (ported from the Ansvisor
approach, re-implemented for httpx):

* http/https only, standard web ports, no credentials in the URL;
* IP-literal hosts must be public, every address a name resolves to must
  be public, and the request is pinned to an address that was checked —
  a name cannot resolve public for the check and private for the request
  (DNS rebinding);
* redirects are followed by hand, at most MAX_REDIRECTS, each hop
  re-checked the same way;
* the whole fetch has a time limit and the body a size limit (refused or
  truncated per caller).

Errors carry a code the caller can surface: invalid_url, blocked,
unreachable, timeout, too_large, not_html, http_error, too_many_redirects.
"""
from __future__ import annotations

import asyncio
import inspect
import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

FETCH_TIMEOUT = 10.0
MAX_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5

USER_AGENT = "Mozilla/5.0 (compatible; SEOForgeBot/1.0; +https://github.com/Goldy1470/seoforge)"

WEB_PORTS = ("", "80", "443")

ACCEPT_HTML = ("text/html", "application/xhtml+xml")

# Private, local, shared, documentation and reserved ranges (RFC 6890 and
# friends) plus the IPv6 transitions that carry an IPv4 address inside.
# An IPv4-mapped address (::ffff:a.b.c.d) is evaluated as its IPv4 value
# before the ranges are consulted.
_BLOCKED_V4 = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.88.99.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
)
_BLOCKED_V6 = (
    "::/128",
    "::1/128",
    "64:ff9b::/96",
    "100::/64",
    "2001::/32",
    "2001:db8::/32",
    "2002::/16",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
)
_BLOCKED = tuple(ipaddress.ip_network(n) for n in _BLOCKED_V4 + _BLOCKED_V6)


class FetchError(Exception):
    """A rejected or failed fetch; `.code` is the stable, displayable reason."""

    def __init__(self, code: str, message: str, *, status: int | None = None, errno: str | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.errno = errno


@dataclass
class FetchResult:
    url: str  # the address after redirects (original hostname form)
    status: int
    body_bytes: bytes
    text: str
    content_type: str
    truncated: bool = False


def _allow_private_fetch() -> bool:
    """Local-development escape hatch.

    Read from the process environment only, at call time. No MCP tool or
    dashboard route ever sets it, so private fetches cannot be enabled from
    the product surface — only by starting the process with
    SEOFORGE_ALLOW_PRIVATE_FETCH=true (shell/.env of a local machine).
    """
    return os.getenv("SEOFORGE_ALLOW_PRIVATE_FETCH", "").strip().lower() in {"1", "true", "yes", "on"}


def is_public_address(ip: str) -> bool:
    """Whether an IP address is one a public web page may live at."""
    try:
        addr = ipaddress.ip_address(ip.strip("[]"))
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            addr = mapped
    for net in _BLOCKED:
        if addr in net:
            return False
    return bool(addr.is_global)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def check_url(value: str, ports=WEB_PORTS) -> httpx.URL:
    """A user-given URL checked before anything is fetched; raises invalid_url / blocked."""
    text = str(value).strip()
    try:
        sp = urlsplit(text)
    except ValueError:
        raise FetchError("invalid_url", "Not a URL")
    if sp.scheme not in ("http", "https"):
        raise FetchError("invalid_url", "Only http and https pages can be read")
    if sp.username or sp.password:
        raise FetchError("invalid_url", "URLs with credentials are not read")
    try:
        port = sp.port
    except ValueError:
        raise FetchError("invalid_url", "Only the standard web ports are read")
    if port is not None and str(port) not in ports:
        raise FetchError("invalid_url", "Only the standard web ports are read")
    host = (sp.hostname or "").strip("[]")
    if not host:
        raise FetchError("invalid_url", "Not a URL")
    if not _allow_private_fetch():
        if _is_ip_literal(host):
            # The URL parser has not normalised odd encodings the way the JS
            # one does; the resolver re-validates what getaddrinfo answers,
            # which is where 2130706433 / 0x7f.1 style hosts are rejected.
            if not is_public_address(host):
                raise FetchError("blocked", f"{host} is not a public address")
        elif host == "localhost":
            raise FetchError("blocked", "localhost is not a public address")
    return httpx.URL(urlunsplit((sp.scheme, sp.netloc, sp.path or "/", sp.query, "")))


async def _resolve(host: str, resolver=None) -> list[str]:
    if resolver is not None:
        out = resolver(host)
        if inspect.isawaitable(out):
            out = await out
        if isinstance(out, str):
            out = [out]
        return list(out or [])
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as e:
        raise FetchError("unreachable", f"The page host could not be resolved ({getattr(e, 'errno', None) or e})")
    return [info[4][0] for info in infos]


def _host_header(url: httpx.URL) -> str:
    port = url.port
    if port and not ((url.scheme == "http" and port == 80) or (url.scheme == "https" and port == 443)):
        return f"{url.host}:{port}"
    return url.host or ""


def _accepts(content_type: str, accept) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return any(ct == a or ct.startswith(a + "+") for a in accept)


def _charset_of(content_type: str) -> str:
    m = re.search(r"charset=[\"']?([\w-]+)", content_type or "", re.I)
    return m.group(1).lower() if m else "utf-8"


def _decode(buf: bytes, content_type: str) -> str:
    try:
        return buf.decode(_charset_of(content_type))
    except (LookupError, UnicodeDecodeError):
        return buf.decode("utf-8", errors="replace")


async def fetch(
    url: str,
    *,
    accept=ACCEPT_HTML,
    timeout: float = FETCH_TIMEOUT,
    max_bytes: int = MAX_BYTES,
    truncate: bool = False,
    max_redirects: int = MAX_REDIRECTS,
    resolver=None,
    allow_ports=None,
    client: httpx.AsyncClient | None = None,
) -> FetchResult:
    """Fetch a user-supplied URL safely and return FetchResult.

    `resolver` (tests): replacement DNS returning one public address or a list
    of them. `allow_ports` (tests): extra allowed ports, e.g. a local mock
    server's. `accept=None` skips the content-type gate. `client` (tests):
    an existing AsyncClient (e.g. bound to a MockTransport).
    """
    checked = check_url(url, tuple(allow_ports) if allow_ports is not None else WEB_PORTS)
    private_ok = _allow_private_fetch()
    own_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=False, timeout=timeout)
    try:
        try:
            async with asyncio.timeout(timeout):
                return await _hops(
                    client, checked, accept, max_bytes, truncate, max_redirects, resolver, private_ok,
                    tuple(allow_ports) if allow_ports is not None else WEB_PORTS,
                )
        except (asyncio.TimeoutError, TimeoutError):
            raise FetchError("timeout", "The page took too long to answer") from None
    finally:
        if own_client:
            await client.aclose()


async def _hops(client, url, accept, max_bytes, truncate, max_redirects, resolver, private_ok, ports) -> FetchResult:
    for hops in range(max_redirects + 1):
        host = url.host or ""
        check_ip = host if _is_ip_literal(host) else ""
        if not check_ip:
            addresses = await _resolve(host, resolver)
            if not addresses:
                raise FetchError("unreachable", f"{host} did not resolve to any address")
            if not private_ok and any(not is_public_address(a) for a in addresses):
                raise FetchError("blocked", f"{host} does not resolve to a public address")
            check_ip = addresses[0]
        pinned = check_ip and check_ip != host
        headers = {
            "user-agent": USER_AGENT,
            "accept": ", ".join(accept) if accept else "*/*",
            "host": _host_header(url),
        }
        req_url = url.copy_with(host=check_ip) if pinned else url
        ext = {"sni_hostname": host} if pinned and url.scheme == "https" else None
        try:
            async with client.stream(
                "GET", str(req_url), headers=headers, follow_redirects=False, extensions=ext or {}
            ) as resp:
                status = resp.status_code
                if 300 <= status < 400 and resp.headers.get("location"):
                    if hops >= max_redirects:
                        raise FetchError("too_many_redirects", "The page redirects too many times")
                    url = check_url(urljoin(str(url), resp.headers["location"]), ports)
                    continue
                return await _read_body(resp, str(url), status, accept, max_bytes, truncate)
        except FetchError:
            raise
        except httpx.TimeoutException as e:
            raise FetchError("timeout", f"The page took too long to answer ({e})") from e
        except httpx.HTTPError as e:
            raise FetchError(
                "unreachable",
                f"The page could not be reached ({e})",
                errno=str(getattr(e, "_errno", "")) or None,
            ) from e
    raise FetchError("too_many_redirects", "The page redirects too many times")


async def _read_body(resp, final_url, status, accept, max_bytes, truncate) -> FetchResult:
    if status < 200 or status >= 300:
        raise FetchError("http_error", f"The page answered {status}", status=status)
    content_type = resp.headers.get("content-type", "")
    if accept is not None and not _accepts(content_type, accept):
        raise FetchError("not_html", f"The address is {content_type or 'of an unreadable type'}")
    buf = bytearray()
    truncated = False
    async for chunk in resp.aiter_bytes():
        over = len(buf) + len(chunk) - max_bytes
        if over > 0:
            if not truncate:
                raise FetchError("too_large", "The page is too large to read")
            buf.extend(chunk[: len(chunk) - over])
            truncated = True
            break
        buf.extend(chunk)
    body = bytes(buf)
    return FetchResult(
        url=final_url,
        status=status,
        body_bytes=body,
        text=_decode(body, content_type),
        content_type=content_type,
        truncated=truncated,
    )


def fetch_sync(url: str, **kwargs) -> FetchResult:
    """Sync wrapper over fetch() for modules in synchronous call paths.

    Must not be called from inside a running event loop.
    """
    return asyncio.run(fetch(url, **kwargs))
