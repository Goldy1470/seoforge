"""The SERP provider matrix: eleven API services the rank engine can
call, ported one-to-one as declarative contracts — the request URL each
one wants, the headers they carry, which response key carries the organic
results, and how to read them out.

Two families exist, exactly as the reference groups them:

- **native pagination** (`serpapi`, `searchapi`, `crazyserp`): one
  request returns up to 100 ranked results, positions come from the
  provider itself.
- **paginated** (`scrapingant`, `scrapingrobot`, `serply`, `spaceSerp`,
  `serper`, `valueserp`, `hasdata`): ten results per request, one page
  at a time; the orchestrator numbers positions from the page offset.

`proxy` (and the default fallback) read raw Google HTML instead of a
JSON body, so the module also carries the HTML parser both use.

Fact-level fidelity notes: provider ids are the reference's (including
the camelCase `spaceSerp`); the per-service docstrings are the record
of every oddity kept verbatim."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import quote

from .serp_countries import COUNTRIES

# JS encodeURI() leaves these unquoted; the two Google-HTML providers
# build their query string with it, so the byte-level output matches.
ENCODE_URI_SAFE = "/?:@&=+$,;!~*'()#"

SCRAPE_COUNTRY_SET = {
    # services with fixed proxy countries silently downgrade anything
    # else to US (the reference does the same inside the URL builder)
    "SCRAPINGANT": ("AE", "BR", "CN", "DE", "ES", "FR", "GB", "HK", "PL", "IN", "IT", "IL", "JP", "NL", "RU", "SA", "US", "CZ"),
    "SERPLY": ("US", "CA", "IE", "GB", "FR", "DE", "SE", "IN", "JP", "KR", "SG", "AU", "BR"),
}

GOOGLE_VIA_PROXY_DOMAINS = {
    "GB": "www.google.co.uk",
    "CA": "www.google.ca",
    "DE": "www.google.de",
    "FR": "www.google.fr",
    "ES": "www.google.es",
    "IT": "www.google.it",
    "NL": "www.google.nl",
}

DESKTOP_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/42.0.2311.135 Safari/537.36 Edge/12.246"
)
MOBILE_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; SM-G996U Build/QP1A.190711.020; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Mobile Safari/537.36"
)

# The provider-facing settings the URL builders read. The reference
# spells the key `scaping_api` (a typo preserved upstream); this port
# normalizes it to `scraping_api` and takes it from a plain dict.
SETTING_KEY = "scraping_api"

DEFAULT_PAGE = {"start": 0, "num": 10, "page": 1}


def _encode_uri(value: str) -> str:
    """JS encodeURI(): separators survive, spaces do not."""
    return quote(str(value), safe=ENCODE_URI_SAFE)


def _encode_component(value: str) -> str:
    """JS encodeURIComponent(): every reserved character is escaped."""
    return quote(str(value), safe="!*'()")


def _country_name(code: str) -> str:
    row = COUNTRIES.get((code or "US").upper())
    return row[0] if row else "United States"


def _lang(code: str) -> str:
    row = COUNTRIES.get((code or "US").upper())
    return row[2] if row else "en"


# ---------------------------------------------------------------- JSON readers


def _json_items(content: Any) -> list:
    """Provider payloads arrive as a JSON string or a decoded list —
    both are accepted, items never reshaped."""
    if isinstance(content, str):
        return json.loads(content)
    return list(content or [])


def _link_items(content: Any) -> list[dict]:
    """The common shape: {title, link, position} kept when title and
    link both exist, positions as the provider sent them."""
    out = []
    for item in _json_items(content):
        title = item.get("title")
        link = item.get("link")
        if title and link:
            out.append({"title": title, "url": link, "position": item.get("position", 0)})
    return out


def _serply_items(content: Any) -> list[dict]:
    """Serply names its rank `realPosition`."""
    out = []
    for item in _json_items(content):
        if item.get("title") and item.get("link"):
            out.append(
                {
                    "title": item["title"],
                    "url": item["link"],
                    "position": item.get("realPosition", 0),
                }
            )
    return out


def _crazyserp_items(content: Any) -> list[dict]:
    """parsed_data.organic or a bare list; items carry `url`."""
    parsed = _json_items(content)
    results = parsed.get("organic", parsed) if isinstance(parsed, dict) else parsed
    out = []
    for item in results or []:
        if item.get("title") and item.get("url"):
            out.append(
                {
                    "title": item["title"],
                    "url": item["url"],
                    "position": item.get("position", 0),
                }
            )
    return out


# --------------------------------------------------------- Google HTML reader


class _Elem:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent: "_Elem | None"):
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Elem | str] = []
        self.parent = parent

    def text(self) -> str:
        parts = [
            p if isinstance(p, str) else p.text() for p in self.children
        ]
        return "".join(parts).strip()

    def walk(self) -> "list[_Elem]":
        out = []
        for child in self.children:
            if isinstance(child, _Elem):
                out.append(child)
                out.extend(child.walk())
        return out


def _closest(node: _Elem, tag: str) -> _Elem | None:
    cur: _Elem | None = node
    while cur is not None:
        if cur.tag == tag:
            return cur
        cur = cur.parent
    return None


class _GoogleHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Elem("document", {}, None)
        self.cur: _Elem = self.root

    def handle_starttag(self, tag, attrs):
        node = _Elem(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in ("meta", "img", "br", "input", "link", "hr"):
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(_Elem(tag, dict(attrs), self.cur))

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        if data:
            self.cur.children.append(data)


def _tree(content: str) -> _GoogleHTMLParser:
    parser = _GoogleHTMLParser()
    parser.feed(str(content))
    parser.close()
    return parser


def _elems_with_id(tree: _GoogleHTMLParser, node: _Elem, element_id: str) -> list[_Elem]:
    return [e for e in node.walk() for ident in [e.attrs.get("id")] if ident == element_id]


def _by_tag(node: _Elem, tag: str) -> list[_Elem]:
    return [e for e in node.walk() if e.tag == tag]


def _descendants(node: _Elem, tag: str, role: str | None = None) -> list[_Elem]:
    return [
        e
        for e in node.walk()
        if e.tag == tag and (role is None or e.attrs.get("role") == role)
    ]


def extract_google_html(content: str, device: str = "desktop") -> list[dict]:
    """Parse a raw Google results page the way the fallback extractor
    does: classic desktop layout first (#search > div > div with h3
    titles), then the newer #rso [role="heading"] layout, then the
    mobile #rso blocks when the keyword was a mobile one. Titles are
    flattened to text (the reference keeps inner HTML)."""
    tree = _tree(content)
    extracted: list[dict] = []
    body = _by_tag(tree.root, "body")
    if not body:
        return extracted

    # valid page: a #search or #rso block must exist
    if not (_elems_with_id(tree, body[0], "search") or _elems_with_id(tree, body[0], "rso")):
        return extracted

    last = 0
    for ident in _elems_with_id(tree, body[0], "search"):
        for div in [d for d in ident.children if isinstance(d, _Elem) and d.tag == "div"]:
            for inner in [d for d in div.children if isinstance(d, _Elem) and d.tag == "div"]:
                for h3 in _descendants(inner, "h3"):
                    url_node = _closest(h3, "a")
                    anchor = url_node.attrs.get("href") if url_node else None
                    if h3.text() and anchor:
                        last += 1
                        extracted.append({"title": h3.text(), "url": anchor, "position": last})

    if not extracted:
        # new layout: every element with role=heading inside #rso
        for rso in _elems_with_id(tree, body[0], "rso"):
            for heading in [e for e in rso.walk() if e.attrs.get("role") == "heading"]:
                url_node = _closest(heading, "a")
                anchor = url_node.attrs.get("href") if url_node else None
                if heading.text() and anchor and anchor.startswith("http"):
                    last += 1
                    extracted.append({"title": heading.text(), "url": anchor, "position": last})

    if not extracted and device == "mobile":
        for rso in _elems_with_id(tree, body[0], "rso"):
            for block in [d for d in rso.children if isinstance(d, _Elem) and d.tag == "div"]:
                for link in _descendants(block, "a", "presentation"):
                    anchors_with_role = [e for e in link.walk() if e.attrs.get("role") == "link"]
                    title = anchors_with_role[0].text() if anchors_with_role else ""
                    anchor = link.attrs.get("href")
                    if title and anchor:
                        last += 1
                        extracted.append({"title": title, "url": anchor, "position": last})
    return extracted


def extract_proxy_html(content: str) -> list[dict]:
    """The proxy page's own layout: #main headings. Google wraps direct
    result links in /url?q=... redirects — the reference strips the
    wrapper, cuts at the first `&`, and keeps the clean https address."""
    tree = _tree(content)
    extracted: list[dict] = []
    body = _by_tag(tree.root, "body")
    if not body:
        return extracted
    mains = _elems_with_id(tree, body[0], "main")
    if not mains:
        return extracted
    last = 0
    for h3 in _descendants(mains[0], "h3"):
        url_node = _closest(h3, "a")
        anchor = url_node.attrs.get("href") if url_node else None
        # the two regexes from the reference: keep from 'https:' on, cut at '&'
        cleaned = ""
        if anchor:
            cleaned = re.sub(r"^.+?(?=https:|$)", "", anchor)
            cleaned = cleaned.split("&")[0]
        if h3.text() and anchor:
            last += 1
            extracted.append({"title": h3.text(), "url": cleaned, "position": last})
    return extracted


# ------------------------------------------------------------- URL builders


def _serpapi_url(keyword: dict, settings: dict, pagination: dict) -> str:
    country = (keyword.get("country") or "US").upper()
    country_name = _country_name(country)
    location = ""
    city = keyword.get("city", "")
    if city:
        location = f"&location={_encode_component(f'{city},{country_name}')}"
    return (
        f"https://serpapi.com/search?q={_encode_component(keyword['keyword'])}"
        f"&num={pagination['num']}&gl={country}&device={keyword['device']}{location}"
        f"&api_key={settings[SETTING_KEY]}"
    )


def _searchapi_url(keyword: dict, settings: dict, pagination: dict) -> str:
    country = (keyword.get("country") or "US").upper()
    country_name = _country_name(country)
    location = ""
    if keyword.get("city", "") and country_name:
        location = f"&location={_encode_component(f'{keyword['city']},{country_name}')}"
    return (
        "https://www.searchapi.io/api/v1/search?engine=google"
        f"&q={_encode_component(keyword['keyword'])}&num={pagination['num']}"
        f"&gl={country}&device={keyword['device']}{location}"
    )


def _crazyserp_url(keyword: dict, settings: dict, pagination: dict) -> str:
    country = (keyword.get("country") or "US").upper()
    lang = _lang(country)
    country_name = _country_name(country)
    location = f"{keyword['city']},{country_name}" if keyword.get("city", "") else country_name
    google_domain = GOOGLE_VIA_PROXY_DOMAINS.get(country, "www.google.com")
    return (
        f"https://crazyserp.com/api/search?q={_encode_component(keyword['keyword'])}"
        # verbatim from the reference: it asks its own page size on every page
        f"&page={pagination['num']}&pageOffset=0"
        f"&location={_encode_component(location)}&googleDomain={google_domain}"
        f"&gl={country.lower()}&hl={lang}&safe=off&filter=1&nfpr=0&device={keyword['device']}"
    )


def _google_via(url_tail: str, settings: dict, scrapingant: bool, keyword: dict, country: str) -> str:
    if scrapingant:
        return (
            f"https://api.scrapingant.com/v2/extended?url={_encode_uri(url_tail)}"
            f"&x-api-key={settings[SETTING_KEY]}&proxy_country={country}&browser=false"
        )
    device = "&mobile=true" if keyword.get("device") == "mobile" else ""
    return (
        f"https://api.scrapingrobot.com/?token={settings[SETTING_KEY]}"
        f"&proxyCountry={country}&render=false{device}&url={_encode_uri(url_tail)}"
    )


def _scrapingant_url(keyword: dict, settings: dict, pagination: dict) -> str:
    country = (keyword.get("country") or "US").upper()
    if country not in SCRAPE_COUNTRY_SET["SCRAPINGANT"]:
        country = "US"
    page = f"num={pagination['num']}&start={pagination['start']}"
    # the reference encodes the whole raw tail once (encodeURI): the
    # keyword reaches Google's own query string with live separators
    url_tail = f"https://www.google.com/search?{page}&hl={_lang(country)}&q={keyword['keyword']}"
    return _google_via(url_tail, settings, True, keyword, country)


def _scrapingrobot_url(keyword: dict, settings: dict, pagination: dict) -> str:
    country = (keyword.get("country") or "US").upper()
    page = f"num={pagination['num']}&start={pagination['start']}"
    url_tail = (
        f"https://www.google.com/search?{page}&hl={_lang(country)}"
        f"&gl={country}&q={keyword['keyword']}"
    )
    return _google_via(url_tail, settings, False, keyword, country)


def _serper_url(keyword: dict, settings: dict, pagination: dict) -> str:
    country = (keyword.get("country") or "US").upper()
    country_name = _country_name(country)
    location = ""
    if keyword.get("city", ""):
        location = f"&location={_encode_component(f'{keyword['city']},{country_name}')}"
    return (
        f"https://google.serper.dev/search?q={_encode_component(keyword['keyword'])}"
        f"&gl={country}&hl={_lang(country)}{location}"
        f"&page={pagination['page']}&apiKey={settings[SETTING_KEY]}"
    )


def _serply_url(keyword: dict, settings: dict, pagination: dict) -> str:
    return (
        f"https://api.serply.io/v1/search/q={_encode_component(keyword['keyword'])}"
        f"&num={pagination['num']}&start={pagination['start']}"
        f"&hl={(keyword.get('country') or 'US').upper()}"
    )


def _spaceserp_url(keyword: dict, settings: dict, pagination: dict) -> str:
    country = (keyword.get("country") or "US").upper()
    country_name = _country_name(country)
    location = ""
    if keyword.get("city", ""):
        location = f"&location={_encode_component(f'{keyword['city']},{country_name}')}"
    device = "&device=mobile" if keyword.get("device") == "mobile" else ""
    return (
        f"https://api.spaceserp.com/google/search?apiKey={settings[SETTING_KEY]}"
        f"&q={_encode_component(keyword['keyword'])}&pageSize={pagination['num']}"
        f"&pageNo={pagination['page']}&gl={country}&hl={_lang(country)}{location}{device}"
        "&resultBlocks="
    )


def _valueserp_url(keyword: dict, settings: dict, pagination: dict) -> str:
    country = (keyword.get("country") or "US").upper()
    country_name = _country_name(country)
    location = ""
    if keyword.get("city", ""):
        location = f"&location={_encode_component(f'{keyword['city']},{country_name}')}"
    device = "&device=mobile" if keyword.get("device") == "mobile" else ""
    return (
        f"https://api.valueserp.com/search?api_key={settings[SETTING_KEY]}"
        f"&q={_encode_component(keyword['keyword'])}&gl={country}&hl={_lang(country)}"
        f"{device}{location}&page={pagination['page']}&output=json"
        "&include_answer_box=false&include_advertiser_info=false"
    )


def _hasdata_url(keyword: dict, settings: dict, pagination: dict) -> str:
    country = (keyword.get("country") or "US").upper()
    country_name = _country_name(country)
    location = ""
    if keyword.get("city", "") and country_name:
        location = f"&location={_encode_component(f'{keyword['city']},{country_name}')}"
    return (
        f"https://api.scrape-it.cloud/scrape/google/serp?q={_encode_component(keyword['keyword'])}"
        f"{location}&num={pagination['num']}&start={pagination['start']}"
        f"&gl={country.lower()}&deviceType={keyword['device']}"
    )


def _proxy_url(keyword: dict, settings: dict, pagination: dict) -> str:
    return (
        "https://www.google.com/search?num={num}&start={start}&q={q}".format(
            num=pagination["num"], start=pagination["start"], q=_encode_uri(keyword["keyword"])
        )
    )


def _scrapingant_headers(keyword: dict, settings: dict) -> dict:
    return {"Ant-User-Agent": MOBILE_AGENT} if keyword.get("device") == "mobile" else {}


def _serpapi_headers(keyword: dict, settings: dict) -> dict:
    return {"Content-Type": "application/json", "X-API-Key": settings[SETTING_KEY]}


def _searchapi_headers(keyword: dict, settings: dict) -> dict:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {settings[SETTING_KEY]}"}


def _crazyserp_headers(keyword: dict, settings: dict) -> dict:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {settings[SETTING_KEY]}"}


def _serply_headers(keyword: dict, settings: dict) -> dict:
    country = (keyword.get("country") or "US").upper()
    if country not in SCRAPE_COUNTRY_SET["SERPLY"]:
        country = "US"
    return {
        "Content-Type": "application/json",
        "X-User-Agent": "mobile" if keyword.get("device") == "mobile" else "desktop",
        "X-Api-Key": settings[SETTING_KEY],
        "X-Proxy-Location": country,
    }


def _hasdata_headers(keyword: dict, settings: dict) -> dict:
    return {"Content-Type": "application/json", "x-api-key": settings[SETTING_KEY]}


def _proxy_headers(keyword: dict, settings: dict) -> dict:
    return {"Accept": "gzip,deflate,compress;"}


# ------------------------------------------------------------------ registry


class Provider:
    """One service's contract: identity, the URL to call, the headers to
    carry, where the organic results live in the response, and how to
    read them out."""

    __slots__ = (
        "id", "name", "website", "result_object_key", "allows_city",
        "native_pagination", "headers", "scrape_url", "extractor",
    )

    def __init__(
        self,
        id: str,
        name: str,
        website: str,
        *,
        result_object_key: str,
        allows_city: bool = False,
        native_pagination: bool = False,
        headers: Callable | None = None,
        scrape_url: Callable | None = None,
        extractor: Callable | None = None,
    ):
        self.id = id
        self.name = name
        self.website = website
        self.result_object_key = result_object_key
        self.allows_city = allows_city
        self.native_pagination = native_pagination
        self.headers = headers
        self.scrape_url = scrape_url
        self.extractor = extractor

    def request(self, keyword: dict, settings: dict, pagination: dict | None = None) -> dict:
        """Build one request: the URL, merged headers, and the response
        key the orchestrator reads. An empty dict answers honestly when
        the contract cannot build a URL."""
        page = {**DEFAULT_PAGE, **(pagination or {})}
        url = self.scrape_url(keyword, settings, page) if self.scrape_url else None
        if not url:
            return {}
        headers = {
            **_base_headers(keyword),
            **(self.headers(keyword, settings) if self.headers else {}),
        }
        return {"url": url, "headers": headers, "result_object_key": self.result_object_key}

    def extract(self, content: Any, device: str = "desktop") -> list[dict]:
        if self.extractor:
            return self.extractor(content)
        if self.id == "proxy":
            return extract_proxy_html(content)
        return extract_google_html(content, device)


def _base_headers(keyword: dict) -> dict:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": DESKTOP_AGENT,
        "Accept": "application/json; charset=utf8;",
    }
    if keyword.get("device") == "mobile":
        headers["User-Agent"] = MOBILE_AGENT
    return headers


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        Provider(
            "scrapingrobot",
            "Scraping Robot",
            "scrapingrobot.com",
            result_object_key="result",
            scrape_url=_scrapingrobot_url,
        ),
        Provider(
            "scrapingant",
            "ScrapingAnt",
            "scrapingant.com",
            result_object_key="result",
            headers=_scrapingant_headers,
            scrape_url=_scrapingant_url,
        ),
        Provider(
            "serpapi",
            "SerpApi.com",
            "serpapi.com",
            result_object_key="organic_results",
            allows_city=True,
            native_pagination=True,
            headers=_serpapi_headers,
            scrape_url=_serpapi_url,
            extractor=_link_items,
        ),
        Provider(
            "serply",
            "Serply",
            "serply.io",
            result_object_key="result",
            headers=_serply_headers,
            scrape_url=_serply_url,
            extractor=_serply_items,
        ),
        Provider(
            "spaceSerp",
            "Space Serp",
            "spaceserp.com",
            result_object_key="organic_results",
            allows_city=True,
            scrape_url=_spaceserp_url,
            extractor=_link_items,
        ),
        Provider(
            "proxy",
            "Proxy",
            "",
            result_object_key="data",
            headers=_proxy_headers,
            scrape_url=_proxy_url,
            extractor=extract_proxy_html,
        ),
        Provider(
            "searchapi",
            "SearchApi.io",
            "searchapi.io",
            result_object_key="organic_results",
            allows_city=True,
            native_pagination=True,
            headers=_searchapi_headers,
            scrape_url=_searchapi_url,
            extractor=_link_items,
        ),
        Provider(
            "valueserp",
            "Value Serp",
            "valueserp.com",
            result_object_key="organic_results",
            allows_city=True,
            scrape_url=_valueserp_url,
            extractor=_link_items,
        ),
        Provider(
            "serper",
            "Serper.dev",
            "serper.dev",
            result_object_key="organic",
            allows_city=True,
            scrape_url=_serper_url,
            extractor=_link_items,
        ),
        Provider(
            "hasdata",
            "HasData",
            "hasdata.com",
            result_object_key="organicResults",
            allows_city=True,
            headers=_hasdata_headers,
            scrape_url=_hasdata_url,
            extractor=_link_items,
        ),
        Provider(
            "crazyserp",
            "CrazySERP",
            "crazyserp.com",
            result_object_key="parsed_data",
            allows_city=True,
            native_pagination=True,
            headers=_crazyserp_headers,
            scrape_url=_crazyserp_url,
            extractor=_crazyserp_items,
        ),
    )
}

PROVIDER_IDS = tuple(PROVIDERS)


def get_provider(provider_id: str) -> Provider:
    try:
        return PROVIDERS[str(provider_id)]
    except KeyError:
        raise ValueError(
            f"unknown provider {provider_id!r}: expect one of {sorted(PROVIDERS)}"
        ) from None
