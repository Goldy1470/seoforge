"""The provider matrix offline: every contract is tested against
synthetic documented shapes — the response keys and field names each
provider documents (no reference HTML fixtures exist). URL builders are
checked byte for byte where the two JS codecs matter."""
import pytest

from seoforge import serp_providers as sp

SETTINGS = {"scraping_api": "SK-TEST"}
KEYWORD = {"keyword": "best running shoes", "device": "desktop", "country": "US", "city": ""}


# ------------------------------------------------------------- the registry


def test_all_eleven_reference_providers_are_registered():
    assert set(sp.PROVIDERS) == {
        "scrapingrobot", "scrapingant", "serpapi", "serply", "spaceSerp",
        "proxy", "searchapi", "valueserp", "serper", "hasdata", "crazyserp",
    }


def test_family_grouping_matches_the_reference():
    native = {pid for pid, p in sp.PROVIDERS.items() if p.native_pagination}
    assert native == {"serpapi", "searchapi", "crazyserp"}
    cities = {pid for pid, p in sp.PROVIDERS.items() if p.allows_city}
    assert cities == {"serpapi", "spaceSerp", "searchapi", "valueserp", "serper", "hasdata", "crazyserp"}


def test_unknown_provider_answers_with_the_vocabulary():
    with pytest.raises(ValueError) as err:
        sp.get_provider("nope")
    assert "serpapi" in str(err.value)


def test_every_provider_can_build_one_request():
    page = {"start": 0, "num": 10, "page": 1}
    for pid, provider in sp.PROVIDERS.items():
        req = provider.request(KEYWORD, SETTINGS, page)
        assert req["url"].startswith("http"), pid
        assert req["result_object_key"] == provider.result_object_key, pid
        assert "User-Agent" in req["headers"] and "Content-Type" in req["headers"], pid


def test_base_headers_swap_agent_by_device():
    desktop = sp._base_headers(KEYWORD)["User-Agent"]
    mobile = sp._base_headers({**KEYWORD, "device": "mobile"})["User-Agent"]
    assert desktop == sp.DESKTOP_AGENT and mobile == sp.MOBILE_AGENT


# ------------------------------------------------------------ URL contracts


def test_serpapi_wants_one_hundred_native_results():
    req = sp.get_provider("serpapi").request(
        KEYWORD, SETTINGS, {"start": 0, "num": 100, "page": 1}
    )
    assert req["url"] == (
        "https://serpapi.com/search?q=best%20running%20shoes&num=100"
        "&gl=US&device=desktop&api_key=SK-TEST"
    )
    assert req["headers"] == {
        "Content-Type": "application/json",
        "User-Agent": sp.DESKTOP_AGENT,
        "Accept": "application/json; charset=utf8;",
        "X-API-Key": "SK-TEST",
    }


def test_serpapi_city_speaks_the_country_name():
    req = sp.get_provider("serpapi").request(
        {"keyword": "hotels", "device": "desktop", "country": "DE", "city": "Berlin"},
        SETTINGS,
        {"start": 0, "num": 100, "page": 1},
    )
    assert "location=Berlin%2CGermany" in req["url"]
    assert "gl=DE" in req["url"]


def test_component_encoding_is_escaped_everywhere():
    # encodeURIComponent escapes & and spaces; a city with an accent too
    req = sp.get_provider("serpapi").request(
        {"keyword": "coffee & tea", "device": "desktop", "country": "BR", "city": "São Paulo"},
        SETTINGS,
        {"start": 0, "num": 100, "page": 1},
    )
    assert "q=coffee%20%26%20tea" in req["url"]
    assert "location=S%C3%A3o%20Paulo%2CBrazil" in req["url"]
    assert sp._encode_component("a?b&c=d e") == "a%3Fb%26c%3Dd%20e"
    assert sp._encode_uri("a?b&c=d e") == "a?b&c=d%20e"


def test_google_via_proxy_encodes_the_raw_tail_once():
    req = sp.get_provider("scrapingant").request(KEYWORD, SETTINGS, {"start": 10, "num": 10, "page": 2})
    # encodeURI keeps the separators and the scheme raw; only spaces move
    assert req["url"].startswith(
        "https://api.scrapingant.com/v2/extended?url=https://www.google.com/search?num=10&start=10&hl=en&q=best%20running%20shoes&"
    )

    amped = sp.get_provider("scrapingant").request(
        {"keyword": "coffee & tea", "device": "desktop", "country": "FR", "city": ""},
        SETTINGS,
        {"start": 0, "num": 10, "page": 1},
    )
    # the & survives encodeURI too: one pass, nothing double-escaped
    assert "q=coffee%20&%20tea" in amped["url"]

    robot = sp.get_provider("scrapingrobot").request(KEYWORD, SETTINGS, {"start": 0, "num": 10, "page": 1})
    assert robot["url"].startswith("https://api.scrapingrobot.com/?token=SK-TEST")
    assert "render=false&url=https://www.google.com/search?num=10" in robot["url"]
    assert "q=best%20running%20shoes" in robot["url"]


def test_proxy_wants_plain_google():
    req = sp.get_provider("proxy").request(
        KEYWORD, SETTINGS, {"start": 10, "num": 10, "page": 1}
    )
    assert req["url"] == "https://www.google.com/search?num=10&start=10&q=best%20running%20shoes"
    assert req["headers"]["Accept"] == "gzip,deflate,compress;"


def test_serply_carries_its_proxy_and_device_headers():
    req = sp.get_provider("serply").request(
        {"keyword": "laptops", "device": "mobile", "country": "NL", "city": ""},
        SETTINGS,
        {"start": 0, "num": 10, "page": 1},
    )
    assert req["url"].startswith("https://api.serply.io/v1/search/q=laptops&num=10&start=0&hl=NL")
    headers = req["headers"]
    assert headers["X-User-Agent"] == "mobile"
    assert headers["X-Api-Key"] == "SK-TEST"
    # the URL keeps nl, but out-of-list countries downgrade only where the
    # reference downgrades: the proxy location header
    assert headers["X-Proxy-Location"] == "US"
    in_list = sp.get_provider("serply").request(
        {"keyword": "laptops", "device": "mobile", "country": "DE", "city": ""},
        SETTINGS,
        {"start": 0, "num": 10, "page": 1},
    )
    assert in_list["headers"]["X-Proxy-Location"] == "DE"


def test_serper_pages_and_hasdata_starts_and_gl():
    serper = sp.get_provider("serper").request(
        KEYWORD, SETTINGS, {"start": 0, "num": 10, "page": 3}
    )
    assert "page=3&apiKey=SK-TEST" in serper["url"] and "gl=US" in serper["url"]

    hasdata = sp.get_provider("hasdata").request(
        {"keyword": "laptops", "device": "mobile", "country": "GB", "city": "London"},
        SETTINGS,
        {"start": 20, "num": 10, "page": 1},
    )
    assert "location=London%2CUnited%20Kingdom" in hasdata["url"]
    assert "num=10&start=20" in hasdata["url"]
    assert "gl=gb&deviceType=mobile" in hasdata["url"]


def test_crazyserp_keeps_its_own_page_size_ask_and_domain_map():
    req = sp.get_provider("crazyserp").request(
        {"keyword": "coffee", "device": "desktop", "country": "GB", "city": ""},
        SETTINGS,
        {"start": 0, "num": 100, "page": 1},
    )
    assert req["url"].startswith("https://crazyserp.com/api/search?q=coffee")
    assert "page=100&pageOffset=0" in req["url"]  # asks its own size on every page
    assert "googleDomain=www.google.co.uk" in req["url"]
    assert "gl=gb&hl=en&safe=off" in req["url"]
    assert "nfpr=0&device=desktop" in req["url"]


def test_spacereserp_and_valueserp_pages_by_page_number():
    space = sp.get_provider("spaceSerp").request(
        KEYWORD, SETTINGS, {"start": 10, "num": 20, "page": 2}
    )
    assert "pageSize=20&pageNo=2" in space["url"] and "resultBlocks=" in space["url"]

    value = sp.get_provider("valueserp").request(
        KEYWORD, SETTINGS, {"start": 10, "num": 20, "page": 2}
    )
    assert "page=2&output=json" in value["url"]
    assert "include_answer_box=false" in value["url"]


# --------------------------------------------------------------- extractors


def test_serpapi_extractor_keeps_api_positions_and_drops_gaps():
    payload = '[{"position":1,"title":"A","link":"https://a"},{"position":2,"title":"B","link":""},{"position":3,"title":"C","link":"https://c"}]'
    items = sp.get_provider("serpapi").extract(payload)
    assert items == [
        {"title": "A", "url": "https://a", "position": 1},
        {"title": "C", "url": "https://c", "position": 3},
    ]
    # a decoded list answers the same
    assert sp.get_provider("serpapi").extract([{"title": "D", "link": "https://d", "position": 9}]) == [
        {"title": "D", "url": "https://d", "position": 9}
    ]


def test_serply_extractor_reads_real_position():
    items = sp.get_provider("serply").extract(
        '[{"title":"A","link":"https://a","realPosition":4},{"title":"","link":"https://x"}]'
    )
    assert items == [{"title": "A", "url": "https://a", "position": 4}]


def test_crazyserp_extractor_reads_the_organic_block_and_url_field():
    payload = (
        '{"parsed_data":"skipped","organic":['
        '{"position":1,"url":"https://a","title":"A","description":"d","is_video":false},'
        '{"position":2,"url":"","title":"nolink"}]}'
    )
    items = sp.get_provider("crazyserp").extract(payload)
    assert items == [{"title": "A", "url": "https://a", "position": 1}]
    # a bare list answers too
    assert sp.get_provider("crazyserp").extract([{"url": "https://b", "title": "B", "position": 7}]) == [
        {"title": "B", "url": "https://b", "position": 7}
    ]


def test_link_shaped_extractors_cover_five_services():
    want = [{"title": "T", "url": "https://t", "position": 2}]
    shape = '[{"title":"T","link":"https://t","position":2},{"title":"no","link":""}]'
    assert sp.get_provider("serper").extract(shape) == want
    assert sp.get_provider("searchapi").extract(shape) == want
    assert sp.get_provider("valueserp").extract(shape) == want
    assert sp.get_provider("spaceSerp").extract(shape) == want
    assert sp.get_provider("hasdata").extract(shape) == want


# --------------------------------------------------------- HTML publication


def test_google_html_classic_desktop_layout():
    html = (
        '<html><body><div id="search"><div>'
        '<div><a href="https://one.example"><h3>Tile One</h3></a></div>'
        '<div><h3>No Anchor Here</h3></div>'
        '<div><a href="https://two.example"><h3>Tile Two</h3></a></div>'
        "</div></div></body></html>"
    )
    assert sp.extract_google_html(html, "desktop") == [
        {"title": "Tile One", "url": "https://one.example", "position": 1},
        {"title": "Tile Two", "url": "https://two.example", "position": 2},
    ]


def test_google_html_new_layout_uses_role_headings():
    html = (
        "<html><body><div id=\"rso\">"
        '<a href="https://y.example"><div role="heading">Head Two</div></a>'
        '<a href="/relative"><div role="heading">Relative Skipped</div></a>'
        "</div></body></html>"
    )
    assert sp.extract_google_html(html, "desktop") == [
        {"title": "Head Two", "url": "https://y.example", "position": 1}
    ]


def test_google_html_mobile_layout_when_keyword_is_mobile():
    html = (
        '<html><body><div id="rso"><div>'
        '<a role="presentation" href="https://m.example"><span role="link">Mobile Title</span></a>'
        '</div></div></body></html>'
    )
    assert sp.extract_google_html(html, "mobile") == [
        {"title": "Mobile Title", "url": "https://m.example", "position": 1}
    ]
    assert sp.extract_google_html(html, "desktop") == []


def test_google_html_answers_empty_when_the_page_is_not_a_results_page():
    assert sp.extract_google_html("<html><body><p>capcha wall</p></body></html>", "desktop") == []
    assert sp.extract_google_html("", "desktop") == []


def test_proxy_extractor_cleans_google_redirect_wrappers():
    html = (
        '<html><body><div id="main">'
        '<a href="/url?q=https://z.example/track&sa=U"><h3>Z Title</h3></a>'
        '<span><h3>Headless</h3></span>'
        "</div></body></html>"
    )
    assert sp.extract_proxy_html(html) == [
        {"title": "Z Title", "url": "https://z.example/track", "position": 1}
    ]
    assert sp.extract_proxy_html("<html><body><p>nope</p></body></html>") == []


def test_scrapingant_row_content_runs_the_google_fallback():
    provider = sp.get_provider("scrapingant")
    html = '<html><body><div id="search"><div><div><a href="https://one.example"><h3>One</h3></a></div></div></div></body></html>'
    assert provider.extract(html, "desktop") == [
        {"title": "One", "url": "https://one.example", "position": 1}
    ]
