"""The position-tracker's email digest: one HTML email per domain with
the keyword table, the movement arrows and the stat line, sent over
plain SMTP.

Ported from the reference's digest email builder and its notify route:
each row shows the keyword with its country flag and device, the current
position with the change arrow, the best position ever, and how long
since the keyword was last updated. The subject is
`[domain] Keyword Positions Update`, the recipient is the domain's own
`notification_emails` list when set and the global address otherwise,
and the send is per-domain: one domain's failure never blocks the rest.

SMTP settings come from environment knobs instead of the reference's
encrypted settings file. Device icons are plain text labels and the
template carries no external images (the reference pulls its logo and
icons from its own CDN)."""
from __future__ import annotations

import smtplib
from email.utils import formataddr
from typing import Callable

from . import db, serp_tracking as st
from .config import settings

TEMPLATE = """<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f5f7;">
<span style="display:none;">{{preheader}}</span>
<div style="max-width:640px;margin:24px auto;font-family:Arial,Helvetica,sans-serif;background:#ffffff;border-radius:10px;">
  <div style="padding:16px 24px;border-bottom:1px solid #eee;">
    <b style="font-size:18px;">SEO Forge</b>
    <span style="color:#888;font-size:12px;float:right;">{{currentDate}}</span>
  </div>
  <div style="padding:16px 24px;">
    <h2 style="margin:0 0 4px;font-size:20px;">{{domainName}}</h2>
    <p style="margin:0 0 12px;color:#555;font-size:13px;">
      {{keywordsCount}} tracked keywords. {{stat}}.
    </p>
    <table style="width:100%;border-collapse:collapse;font-size:13px;" cellpadding="8">
      <tr style="background:#fafafa;color:#777;text-align:left;">
        <th>Keyword</th><th>Position</th><th>Best</th><th>Updated</th>
      </tr>
      {{keywordsTable}}
    </table>
  </div>
  <div style="padding:12px 24px;border-top:1px solid #eee;color:#999;font-size:11px;">
    Sent by <a href="{{appURL}}">{{appURL}}</a>
  </div>
</div>
</body></html>
"""


def smtp_ready() -> bool:
    """Host, port and a global recipient are all in place."""
    return bool(settings.smtp_host and settings.smtp_port and settings.smtp_notify_email)


def time_since(stamp: str, *, now=None) -> str:
    """Human `... ago` for a keyword's last update, ported from the
    reference's timeSince: years, months, days, hours, minutes, seconds
    in that order, first band wins (days and hours read at >= 1, the
    others tick over past 1)."""
    if not stamp:
        return "-"
    import datetime as _dt

    end = now or db.now()
    try:
        then = parse_stamp(stamp)
        moment = parse_stamp(end) if isinstance(end, str) else end
        if then.tzinfo is None:
            then = then.replace(tzinfo=_dt.timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return "-"
    seconds = int((moment - then).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds // 31536000 > 1:
        return f"{seconds // 31536000} years ago"
    if seconds // 2592000 > 1:
        return f"{seconds // 2592000} months ago"
    if seconds // 86400 >= 1:
        return f"{seconds // 86400} days ago"
    if seconds // 3600 >= 1:
        return f"{seconds // 3600} hrs ago"
    if seconds // 60 > 1:
        return f"{seconds // 60} mins ago"
    return f"{seconds} secs ago"


def parse_stamp(value: str):
    from datetime import datetime

    text = str(value).strip()
    for shape in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            candidate = datetime.strptime(text, shape)
        except ValueError:
            continue
        candidate = candidate if candidate.tzinfo else candidate.replace(tzinfo=None)
        return candidate
    raise ValueError(text)


def _flag_emoji(code: str) -> str:
    """Regional-indicator flag from a country code, as the reference
    builds it; unknown codes degrade to their letters."""
    upper = (code or "US").upper()
    if len(upper) != 2 or not upper.isalpha():
        return upper
    return "".join(chr(127397 + ord(ch)) for ch in upper)


def _row(keyword: dict) -> str:
    change = st.position_change(keyword.get("history") or {}, int(keyword.get("position") or 0))
    arrow = ""
    if change > 0:
        arrow = f' <span style="color:#0f766e;">&#9650; {change}</span>'
    elif change < 0:
        arrow = f' <span style="color:#b91c1c;">&#9660; {abs(change)}</span>'
    best = st.best_position(keyword.get("history") or {})
    best_text = best if best is not None else "-"
    return (
        "<tr>"
        f'<td>{_flag_emoji(keyword.get("country", "US"))} <span title="{keyword.get("device", "desktop")}">'
        f'{"&#128241;" if keyword.get("device") == "mobile" else "&#128421;"}</span> '
        f"{keyword['keyword']}</td>"
        f'<td>{int(keyword.get("position") or 0) or "-"}{arrow}</td>'
        f"<td>{best_text}</td>"
        f'<td>{time_since(keyword.get("last_updated") or "")}</td>'
        "</tr>"
    )


def build_email(domain_name: str, keywords: list[dict]) -> tuple[str, str]:
    """One digest's HTML and its subject; the stat line doubles as the
    preheader (the reference's `{{stat}}` / `{{preheader}}`)."""
    improved = declined = 0
    for keyword in keywords:
        change = st.position_change(keyword.get("history") or {}, int(keyword.get("position") or 0))
        if change > 0:
            improved += 1
        elif change < 0:
            declined += 1
    bits = []
    if improved:
        bits.append(f"{improved} Improved")
    if declined:
        bits.append(f"{declined} Declined")
    stat = ", ".join(bits) or "no movement noted"
    rows = "".join(_row(k) for k in keywords)
    html = (
        TEMPLATE.replace("{{preheader}}", stat)
        .replace("{{currentDate}}", db.now()[:10])
        .replace("{{domainName}}", domain_name)
        .replace("{{keywordsCount}}", str(len(keywords)))
        .replace("{{stat}}", stat)
        .replace("{{keywordsTable}}", rows)
        .replace("{{appURL}}", settings.site_url or "")
    )
    return html, f"[{domain_name}] Keyword Positions Update"


def _smtp_send(from_header: str, to: str, subject: str, html: str) -> None:
    """The live send: one smtplib session, optional STARTTLS, login when
    credentials are set."""
    port = int(settings.smtp_port)
    timeout = 30
    if port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, port, timeout=timeout)
    else:
        server = smtplib.SMTP(settings.smtp_host, port, timeout=timeout)
    from_email = settings.smtp_from_email or settings.smtp_notify_email
    message = (
        f"From: {from_header}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        f"{html}"
    )
    try:
        if port != 465:
            server.starttls()
        if settings.smtp_user and settings.smtp_password:
            server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(from_email, [to], message.encode("utf-8"))
    finally:
        try:
            server.quit()
        except smtplib.SMTPError:
            pass


def digest_domain(
    domain_name: str,
    keywords: list[dict],
    *,
    send: Callable | None = None,
) -> dict:
    """One domain's digest: build the email and send it. With no SMTP
    settings the intent answers honestly instead of half-sending."""
    html, subject = build_email(domain_name, keywords)
    if send is None and not smtp_ready():
        # the reference's upfront gate: host, port and recipient checked together
        return {"domain": domain_name, "queued": False, "error": "SMTP has not been set up yet"}
    to = ""
    for row in st.list_domains():
        if row["domain"] == domain_name:
            to = row["notification_emails"] or settings.smtp_notify_email
            break
    else:
        to = settings.smtp_notify_email
    if not to:
        return {"domain": domain_name, "queued": False, "error": "no notification address configured"}
    from_name = settings.smtp_from_name or "SEOForge"
    from_email = settings.smtp_from_email or settings.smtp_notify_email
    from_header = formataddr((from_name, from_email))
    sender = send or _smtp_send
    try:
        sender(from_header, to, subject, html)
    except Exception as exc:  # one domain failing never blocks the rest
        return {"domain": domain_name, "queued": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return {"domain": domain_name, "queued": True, "to": to, "subject": subject}


def digest_all(*, domain: str = "", send: Callable | None = None) -> dict:
    """Every tracked domain with its notification flag on (the reference
    notify route's default pass); a named domain digests regardless of
    the flag when it exists."""
    domains = st.list_domains()
    targets = [
        d
        for d in domains
        if (domain and d["domain"] == domain)
        or (not domain and d["notification"])
    ]
    if not targets:
        return {"sent": 0, "results": []}
    results = []
    for d in targets:
        keywords = st.list_keywords(d["domain"])
        results.append(digest_domain(d["domain"], keywords, send=send))
    return {"sent": sum(1 for r in results if r.get("queued")), "results": results}
