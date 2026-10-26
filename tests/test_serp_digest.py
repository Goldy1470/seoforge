"""The email digest offline: rows built from tracked keywords, the
reference's change-arrow and stat-line vocabulary, the per-domain send
contract and the honest answers when SMTP is missing. A fake `send`
records mail; nothing ever opens a socket."""
import datetime as dt

import pytest

from seoforge import db, serp_digest as sd, serp_scraper as ss, serp_tracking as st
from seoforge.config import settings

NOW = "2026-10-26T09:00:00+00:00"


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    monkeypatch.setattr(st, "_today", lambda: dt.date(2026, 10, 26))
    monkeypatch.setattr(db, "now", lambda: NOW)


@pytest.fixture
def smtp_ready(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.org")
    monkeypatch.setattr(settings, "smtp_port", "587")
    monkeypatch.setattr(settings, "smtp_user", "mailer")
    monkeypatch.setattr(settings, "smtp_password", "pw")
    monkeypatch.setattr(settings, "smtp_notify_email", "team@acme.org")
    monkeypatch.setattr(settings, "smtp_from_email", "digest@acme.org")
    monkeypatch.setattr(settings, "smtp_from_name", "SEO Forge")


def _track(domain: str, keyword: str, device: str = "desktop", country: str = "US") -> dict:
    return st.add_keyword(keyword, domain, device=device, country=country)


def _record(keyword_id: int, day: dt.date, position: int) -> None:
    """One scraped day against a pinned calendar day."""
    real_today = st._today
    st._today = lambda: day
    try:
        st.record_scrape_result(keyword_id, position=position)
    finally:
        st._today = real_today


# ------------------------------------------------------------------ helpers


def test_time_since_buckets_sequential_units():
    import datetime as dtt

    now = dtt.datetime(2026, 10, 26, 9, 0, 0)
    # two-plus years falls into the years bucket; ~one year reads as months
    assert sd.time_since("2023-10-25T09:00:00+00:00", now=now) == "3 years ago"
    assert sd.time_since("2025-10-25T09:00:00+00:00", now=now) == "12 months ago"
    assert sd.time_since("2026-10-25T09:00:00+00:00", now=now) == "1 days ago"  # the >= 1 band
    assert sd.time_since("2026-10-26T07:30:00+00:00", now=now) == "1 hrs ago"  # under a day: the hours band
    assert sd.time_since("2026-10-26T08:58:00+00:00", now=now) == "2 mins ago"
    assert sd.time_since("2026-10-26T08:59:30+00:00", now=now) == "30 secs ago"
    assert sd.time_since("2026-10-26T09:00:30+00:00", now=now) == "0 secs ago"  # clock skew never lies
    assert sd.time_since("", now=now) == "-"
    assert sd.time_since("garbage", now=now) == "-"


def test_digest_reads_the_kernel_rollups():
    # the digest computes all movement through the tracking kernel
    assert sd.st.position_change({"2026-10-25": 5, "2026-10-26": 3}, 3) == 2
    assert sd.st.best_position({"2026-10-25": 5, "2026-10-26": 3}) == 3


def test_flag_emoji_rules():
    assert sd._flag_emoji("US") == "\U0001f1fa\U0001f1f8"
    assert sd._flag_emoji("DE") == "\U0001f1e9\U0001f1ea"
    assert sd._flag_emoji("us") == sd._flag_emoji("US")
    # an empty country degrades to the US default, as the rows always carry one
    assert sd._flag_emoji("") == sd._flag_emoji("US")


# ---------------------------------------------------------------- the email


def test_email_rows_carry_flag_device_arrow_best_and_age():
    tracked = _track("mail.com", "coffee machines", device="mobile", country="DE")
    _record(tracked["id"], dt.date(2026, 10, 25), 7)
    _record(tracked["id"], dt.date(2026, 10, 26), 5)

    row = sd._row(st.get_keyword(tracked["id"]))
    assert "\U0001f1e9\U0001f1ea" in row  # DE flag
    assert 'title="mobile"' in row  # device tail survives, icons are labels
    assert "coffee machines" in row
    assert "&#9650; 2" in row  # up arrow, 2: 7 -> 5 improved
    assert "<td>5 <span" in row  # the arrow trails the position cell
    assert "<td>5</td>" in row  # best ever
    assert "0 secs ago" in row  # records stamped now read fresh


def test_declined_and_lost_rows():
    tracked = _track("mail.com", "falling term")
    _record(tracked["id"], dt.date(2026, 10, 25), 4)
    _record(tracked["id"], dt.date(2026, 10, 26), 9)
    assert "&#9660; 5" in sd._row(st.get_keyword(tracked["id"]))  # down arrow, 5: 4 -> 9

    lost = _track("mail.com", "gone term")
    _record(lost["id"], dt.date(2026, 10, 25), 12)
    _record(lost["id"], dt.date(2026, 10, 26), 0)
    row = sd._row(st.get_keyword(lost["id"]))
    assert "&#9660; 88" in row  # dropped out reads as 12 - 100
    assert "<td>- <span" in row  # the lost keyword shows no rank number, arrow only
    assert "<td>12</td>" in row  # its best position stays visible


def test_build_email_stat_line_subject_and_placeholders():
    up = _track("stat.com", "riser")
    _record(up["id"], dt.date(2026, 10, 25), 8)
    _record(up["id"], dt.date(2026, 10, 26), 3)
    flat = _track("stat.com", "sitter")
    _record(flat["id"], dt.date(2026, 10, 26), 6)  # one day: no movement yet
    down = _track("stat.com", "sink")
    _record(down["id"], dt.date(2026, 10, 25), 6)
    _record(down["id"], dt.date(2026, 10, 26), 11)

    html, subject = sd.build_email("stat.com", st.list_keywords("stat.com"))
    assert subject == "[stat.com] Keyword Positions Update"
    assert "1 Improved, 1 Declined" in html
    assert "3 tracked keywords" in html
    assert "<td>3</td>" in html  # riser's new position
    assert "<td>6</td>" in html  # sitter's position and best
    assert "<td>11 <span" in html  # sink's declined position carries the arrow
    assert settings.site_url in html  # the app link fills its placeholder
    assert db.now()[:10] in html  # the date header


# ------------------------------------------------------------------- sends


def test_digest_domain_uses_the_domain_recipient_override(smtp_ready):
    st.add_domain("recip.com", notification_emails="owner@recip.com")
    k = _track("recip.com", "watched", country="GB")
    _record(k["id"], dt.date(2026, 10, 26), 4)

    sent = []

    def fake_send(from_header, to, subject, html):
        sent.append((from_header, to, subject, html))

    out = sd.digest_domain("recip.com", st.list_keywords("recip.com"), send=fake_send)
    assert out == {
        "domain": "recip.com",
        "queued": True,
        "to": "owner@recip.com",
        "subject": "[recip.com] Keyword Positions Update",
    }
    from_header, to, subject, html = sent[0]
    assert "digest@acme.org" in from_header and "SEO Forge" in from_header  # brand-neutral from name
    assert to == "owner@recip.com"
    assert html.startswith("<!DOCTYPE html>") and "watched" in html


def test_digest_falls_back_to_the_global_recipient(smtp_ready):
    st.add_domain("plain.com")
    k = _track("plain.com", "fallback kw")
    _record(k["id"], dt.date(2026, 10, 26), 2)
    sent = {}
    sd.digest_domain("plain.com", st.list_keywords("plain.com"), send=lambda *m: sent.setdefault("mail", m))
    assert sent["mail"][1] == "team@acme.org"


def test_digest_without_smtp_answers_honestly():
    st.add_domain("nosmtp.com")
    k = _track("nosmtp.com", "offline kw")
    _record(k["id"], dt.date(2026, 10, 26), 1)
    out = sd.digest_domain("nosmtp.com", st.list_keywords("nosmtp.com"))
    assert out["queued"] is False and out["error"] == "SMTP has not been set up yet"
    assert sd.digest_all()["sent"] == 0  # nothing half-sent


def test_digest_send_failure_is_recorded_per_domain(smtp_ready):
    st.add_domain("bad.com", notification_emails="x@bad.com")
    st.add_domain("good.com", notification_emails="y@good.com")
    for name in ("bad.com", "good.com"):
        k = _track(name, f"kw {name}")
        _record(k["id"], dt.date(2026, 10, 26), 9 if name == "bad.com" else 3)

    def flaky_send(from_header, to, subject, html):
        if "bad.com" in to:
            raise RuntimeError("relay not happy")

    out = sd.digest_all(send=flaky_send)
    assert out["sent"] == 1
    by_domain = {r["domain"]: r for r in out["results"]}
    assert by_domain["bad.com"]["queued"] is False
    assert "RuntimeError" in by_domain["bad.com"]["error"] and "relay not happy" in by_domain["bad.com"]["error"]
    assert by_domain["good.com"]["queued"] is True  # one failure never blocks the rest


def test_digest_all_honours_the_notification_flag(smtp_ready):
    st.add_domain("on.com", notification=True, notification_emails="on@on.com")
    st.add_domain("off.com", notification=False, notification_emails="off@off.com")
    ka = _track("on.com", "in the list")
    _record(ka["id"], dt.date(2026, 10, 26), 1)
    kb = _track("off.com", "opted out")
    _record(kb["id"], dt.date(2026, 10, 26), 2)

    destinations = []
    sd.digest_all(send=lambda f, to, s, h: destinations.append(to))
    assert destinations == ["on@on.com"]
    # a named domain digests even when its flag is off
    named = []
    sd.digest_all(domain="off.com", send=lambda f, to, s, h: named.append(to))
    assert named == ["off@off.com"]


def test_digest_flows_after_the_refresh_lane(smtp_ready, monkeypatch):
    """End to end: the scrape lands positions through the kernel, then
    the digest reads what it kept."""
    monkeypatch.setattr(settings, "serp_tracker_provider", "serper")
    monkeypatch.setattr(settings, "serp_tracker_api_key", "SK")
    st.add_domain("pipes.com", scrape_strategy="basic")
    st.add_keyword("pipe kw", "pipes.com")

    body = '[{"title":"P","link":"https://www.pipes.com/rank","position":2}]'
    out = ss.refresh_domain("pipes.com", fetch=lambda provider, parsed: body)
    assert out["refreshed"] == 1

    mails = []
    summary = sd.digest_all(send=lambda f, to, s, h: mails.append((to, s, h)))
    assert summary["sent"] == 1
    to, subject, html = mails[0]
    assert to == "team@acme.org"
    assert subject == "[pipes.com] Keyword Positions Update"
    assert "pipe kw" in html
    assert "<td>1</td>" in html  # rank landed through the kernel
    assert "1 tracked keywords" in html
