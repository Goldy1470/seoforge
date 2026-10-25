"""What each AI-bot sender posts to the ingest endpoint, read into one
request shape — {userAgent, ip, url, status, at, country} — for
`bot_ingest.py` to verify (derivation in THIRD_PARTY_NOTICES.md):

* Cloudflare Worker (option A): JSON { requests: [...] } already in that
  shape (bot-worker-script.py).
* Cloudflare Logpush (option B): newline-delimited JSON of the
  http_requests dataset, gzipped — ClientRequestUserAgent, ClientIP,
  ClientRequestHost, ClientRequestURI, EdgeResponseStatus,
  EdgeStartTimestamp (unix ns, unix s or RFC 3339, as the job was set)
  and ClientCountry. Creating the job uploads a test file,
  {"content":"tests"}, which reads as no requests.
* Vercel drain (option C): a JSON array or NDJSON of log entries; only
  those with a `proxy` object describe a request (clientIp, userAgent[],
  host, path, statusCode, timestamp in ms). Build and function output
  lines are skipped.

Log senders report every line they hold; `records` is that count, for the
raw-record cap.

A file is read without holding up the API's other requests: it's inflated
by magic bytes, to at most MAX_INFLATED_BYTES, and holds at most
MAX_LOG_LINES (LogTooLargeError otherwise). A line whose time can't be read
is dropped, not the file.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import zlib

MAX_INFLATED_BYTES = 16 * 1024 * 1024

#: Lines one body may hold: twice what such a Logpush file holds.
MAX_LOG_LINES = 20_000


class LogTooLargeError(Exception):
    """A body past MAX_INFLATED_BYTES or MAX_LOG_LINES; the route answers 413."""


def _log_too_many_lines(count: int) -> LogTooLargeError:
    return LogTooLargeError(f"The file holds {count} lines; at most {MAX_LOG_LINES} are read")


def body_text(buffer) -> str:
    """The body as text. A gzipped file sent as is (Logpush's) is told by its
    magic bytes and inflated here, opening to at most MAX_INFLATED_BYTES."""
    if not buffer:
        return ""
    data = buffer if isinstance(buffer, (bytes, bytearray)) else str(buffer).encode("utf-8")
    if len(data) < 2 or data[0] != 0x1F or data[1] != 0x8B:  # not gzipped
        return data.decode("utf-8", errors="replace")
    return _inflate(bytes(data))


def _inflate(data: bytes) -> str:
    decomp = zlib.decompressobj(zlib.MAX_WBITS | 16)
    parts: list = []
    total = 0
    feed = data
    while True:
        try:
            chunk = decomp.decompress(feed, MAX_INFLATED_BYTES + 1)
        except zlib.error:
            raise
        total += len(chunk)
        if total > MAX_INFLATED_BYTES:
            raise LogTooLargeError(f"The file opens to more than {MAX_INFLATED_BYTES} bytes")
        parts.append(chunk)
        feed = decomp.unconsumed_tail
        if decomp.eof:
            break
        if not feed:
            raise zlib.error("the gzip stream ended before its header trailer")
    return b"".join(parts).decode("utf-8", errors="replace")


def _read_lines(text: str) -> list:
    """JSON lines, or one JSON array; malformed lines are left out."""
    trimmed = text.strip()
    if not trimmed:
        return []
    if trimmed.startswith("["):
        try:
            parsed = json.loads(trimmed)
        except ValueError:
            return []
        if not isinstance(parsed, list):
            return []
        if len(parsed) > MAX_LOG_LINES:
            raise _log_too_many_lines(len(parsed))
        return parsed
    parts = trimmed.split("\n")
    if len(parts) > MAX_LOG_LINES:
        raise _log_too_many_lines(len(parts))
    out = []
    for part in parts:
        line = part.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            pass  # a malformed line is left out
    return out


def _iso_time(ms) -> str | None:
    """An ISO time with milliseconds, or None for a value that isn't one."""
    if isinstance(ms, bool) or ms is None:
        return None
    if isinstance(ms, str):
        return _iso_time_str(ms)
    if isinstance(ms, (int, float)):
        try:
            stamp = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
        return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return None


def _iso_time_str(value: str) -> str | None:
    try:
        stamp = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _logpush_time(value) -> str | None:
    """Logpush's EdgeStartTimestamp in any of its formats, as an ISO time."""
    if isinstance(value, str) and not value.strip().isdigit():
        return _iso_time_str(value)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    n = float(value)
    if n != n or n in (float("inf"), float("-inf")) or n < 0:
        return None
    # ns, ms or s since the epoch, told apart by size.
    return _iso_time(n / 1e6 if n > 1e15 else n if n > 1e12 else n * 1000)


def read_worker_body(text) -> dict:
    """A Worker's batch: { records, requests } already in the request shape."""
    try:
        body = json.loads(text or "{}")
    except (ValueError, TypeError):
        return {"records": 0, "requests": []}
    requests = body.get("requests") if isinstance(body, dict) else None
    if not isinstance(requests, list):
        requests = []
    return {"records": len(requests), "requests": requests}


def read_logpush_body(text) -> dict:
    lines = _read_lines(text)
    requests = []
    for line in lines:
        if not isinstance(line, dict) or "ClientRequestUserAgent" not in line:
            continue
        host = line.get("ClientRequestHost")
        uri = line.get("ClientRequestURI")
        requests.append(
            {
                "userAgent": line.get("ClientRequestUserAgent"),
                "ip": line.get("ClientIP"),
                "url": f"https://{host}{uri if uri is not None else '/'}" if host else uri,
                "status": line.get("EdgeResponseStatus"),
                "at": _logpush_time(line.get("EdgeStartTimestamp")),
                "country": line.get("ClientCountry"),
            }
        )
    requests = [r for r in requests if r["at"] is not None]
    return {"records": len(lines), "requests": requests}


def read_vercel_body(text) -> dict:
    lines = _read_lines(text)
    requests = []
    for entry in lines:
        if not isinstance(entry, dict):
            continue
        proxy = entry.get("proxy")
        if not isinstance(proxy, dict):
            continue  # build and function output lines are skipped
        stamp = proxy.get("timestamp")
        if stamp is None:
            stamp = entry.get("timestamp")
        if stamp is None:
            stamp = time.time() * 1000
        user_agent = proxy.get("userAgent")
        if isinstance(user_agent, list):
            user_agent = user_agent[0] if user_agent else None
        host = proxy.get("host")
        path = proxy.get("path")
        url = (
            f"https://{host}{path if path is not None else '/'}" if host else path
        )
        requests.append(
            {
                "userAgent": user_agent,
                "ip": proxy.get("clientIp"),
                "url": url,
                "status": proxy.get("statusCode"),
                "at": _iso_time(stamp),
                "country": None,
            }
        )
    requests = [r for r in requests if r["at"] is not None]
    return {"records": len(lines), "requests": requests}


_READERS = {
    "cloudflare_worker": read_worker_body,
    "cloudflare_logpush": read_logpush_body,
    "vercel_drain": read_vercel_body,
}


def read_sender_body(kind: str, buffer) -> dict:
    """A connection's body as requests, and how many records it held."""
    read = _READERS.get(kind)
    if not read:
        return {"records": 0, "requests": []}
    return read(body_text(buffer))
