"""The product-description-opening checker, against its reference fixture table.

#1279: a product description's first 40 words or 250 characters must name the
product type and at least two key attributes — what ChatGPT appears to read
before it stops. Cases mirror the reference fixture table.
"""

import pytest

from seoforge.product_description import (
    OPENING_CHARS,
    OPENING_WORDS,
    attributes_in,
    check_description_opening,
    opening_cut,
    product_type_words,
)

BRAND_STORY = (
    "Since 1987, our family has crafted every pair by hand in a small workshop at the foot of the Alps. "
    "We believe the mountains belong to everyone, and every step you take carries a little of our story. "
    "This men’s trail running shoe weighs 268 g and has a 6 mm drop."
)

# [what, description, product_name, brand, passes]
FIXTURES = [
    (
        "type first, two measures",
        "Men’s trail running shoe, 268 g, 6 mm drop, with a grippy outsole for wet rock and mud.",
        "Acme Ridge Men’s Trail Running Shoe",
        "Acme",
        True,
    ),
    ("brand story first", BRAND_STORY, "Acme Men’s Trail Running Shoe", "Acme", False),
    (
        "type and material and fit",
        "Slim fit organic cotton crew neck t-shirt in a midweight jersey that keeps its shape wash after wash.",
        "Northwind Crew Neck T-Shirt",
        "Northwind",
        True,
    ),
    (
        "type named, one attribute",
        "A backpack you will love on every adventure, made by people who care about the details.",
        "Summit Daypack Backpack",
        "Summit",
        False,
    ),
    (
        "type with a plural and a use",
        "Waterproof hiking boots with a 30 L gaiter loop and full-grain leather uppers for long days on the trail.",
        "Granite Hiking Boot",
        "Granite",
        True,
    ),
    (
        "attributes, but another product type",
        "Lightweight, breathable and waterproof: our best-selling jacket for rainy commutes.",
        "Acme Trail Running Shoe",
        "Acme",
        False,
    ),
    (
        "the model named, with two measures and a use",
        "The Speedgoat 5 weighs 292 g, has a 4 mm drop and a cushioned midsole for long runs.",
        "Hoka Speedgoat 5",
        "Hoka",
        True,
    ),
    (
        "a kitchen product with a capacity and a material",
        "Stainless steel French press coffee maker, 1 L, double-walled to keep coffee hot for an hour.",
        "Barista French Press Coffee Maker",
        "Barista",
        True,
    ),
]


@pytest.mark.parametrize("what,description,name,brand,passes", FIXTURES, ids=[f[0] for f in FIXTURES])
def test_check_description_opening_fixtures(what, description, name, brand, passes):
    result = check_description_opening(description, product_name=name, brand=brand)
    assert result["passes"] is passes


def test_passes_on_type_and_two_measures():
    result = check_description_opening(FIXTURES[0][1], product_name=FIXTURES[0][2], brand=FIXTURES[0][3])
    assert result["names_type"] is True
    assert result["attributes"] == ["268 g", "6 mm"]


def test_flags_brand_story_opening():
    result = check_description_opening(BRAND_STORY, product_name="Acme Men’s Trail Running Shoe", brand="Acme")
    assert result["names_type"] is False
    assert "trail running shoe" not in result["opening"]
    assert BRAND_STORY.index("trail running shoe") > result["cut"]


def test_no_type_to_judge_when_name_is_brand_and_model():
    result = check_description_opening(
        "Weighs 292 g, with a 4 mm drop and a cushioned midsole for long runs.",
        product_name="Hoka X-200",
        brand="Hoka",
    )
    assert result["names_type"] is None
    assert result["passes"] is True


def test_opening_cut_stops_at_40_words():
    text = " ".join(f"w{i}" for i in range(60))
    cut = opening_cut(text)
    assert len(text[:cut].split(" ")) == OPENING_WORDS
    assert cut < OPENING_CHARS


def test_opening_cut_stops_at_250_chars():
    text = " ".join(["characterful"] * 60)
    assert opening_cut(text) == OPENING_CHARS


def test_opening_cut_keeps_short_text_whole():
    assert opening_cut("Merino wool beanie, one size.") == 29


def test_product_type_words_drops_brand_model_and_filler():
    assert product_type_words("Acme Ridge 2 Men’s Trail Running Shoes", "Acme") == [
        "ridge", "men", "trail", "running", "shoe",
    ]
    assert product_type_words("Hoka Speedgoat 5", "Hoka") == ["speedgoat"]


def test_attributes_in_counts_each_kind_once():
    attributes = attributes_in("Slim fit organic cotton running tee, 180 g", ["tee"])
    assert sorted(attributes) == sorted(["180 g", "organic cotton", "running", "slim fit"])


def test_attributes_in_skips_type_words_and_ambiguities():
    assert attributes_in("Trail running shoe", ["trail", "running", "shoe"]) == []
    assert attributes_in("3 in stock, in a wide range of colors") == []
