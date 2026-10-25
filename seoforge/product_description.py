"""Whether a product description's opening is one an AI shopping assistant can
match to a question.

ChatGPT appears to read only about the first ~50 tokens of a description —
about 40 words or 250 characters (public research presented at brightonSEO
2026, from a few test prompts). An opening that tells the brand's story and
names the product type later may never be matched.

Pure and dependency-free: the rubric's product-description-opening signal
runs it on product pages today, and catalog / content tools can run it on
raw descriptions later. Every rule lives here, so they agree.
"""
from __future__ import annotations

import math
import re

# Where the reader stops: whichever of these ends first.
OPENING_WORDS = 40
OPENING_CHARS = 250

# Key attributes the opening needs beside the product type.
MIN_ATTRIBUTES = 2

# A number with its unit: 268 g, 6 mm, 30 L, 15.6", 5000 mAh, 100%. A bare
# "in" isn't one ("3 in stock"); inches are written out or as ".
_MEASURE = re.compile(
    r"(?<![\w.,])\d+(?:[.,]\d+)?\s?(?:mm|cm|km|m|mg|kg|g|lbs?|oz|ml|cl|l|"
    r"inch(?:es)?|ft|\"|w|kw|v|mah|wh|gb|tb|hz|ghz|mp|%)(?![\w])",
    re.IGNORECASE,
)

_MATERIALS = [
    "organic cotton", "cotton", "merino wool", "wool", "cashmere", "linen",
    "silk", "full-grain leather", "leather", "suede", "denim", "canvas",
    "nylon", "polyester", "fleece", "mesh", "gore-tex", "elastane",
    "spandex", "rubber", "memory foam", "foam", "silicone", "glass",
    "ceramic", "porcelain", "stainless steel", "steel", "aluminium",
    "aluminum", "titanium", "carbon fiber", "carbon fibre", "oak", "walnut",
    "bamboo", "recycled",
]

# How it fits; ambiguous words only in their fit phrase ("wide fit", not "a
# wide range").
_FITS = [
    "slim fit", "slim-fit", "slim", "regular fit", "relaxed fit", "loose fit",
    "oversized", "tapered", "straight leg", "straight-leg", "skinny",
    "wide fit", "wide-leg", "narrow fit", "fitted", "true to size",
    "four-way stretch", "stretch", "high-rise", "mid-rise", "low-rise",
]

# What it's for, or what it does in use.
_USES = [
    "running", "trail", "hiking", "walking", "training", "gym", "yoga",
    "cycling", "swimming", "travel", "outdoor", "everyday", "office",
    "gaming", "camping", "winter", "summer", "waterproof", "water-resistant",
    "windproof", "breathable", "lightweight", "cushioned", "insulated",
    "wireless", "rechargeable", "portable", "dishwasher-safe",
    "machine-washable",
]

# Every term, longest first, so "organic cotton" is one attribute, not two.
_TERMS = sorted(
    [(body, re.compile(rf"(?<![\w-]){re.escape(body)}(?![\w-])")) for body in
     (*_MATERIALS, *_FITS, *_USES)],
    key=lambda pair: -len(pair[0]),
)

# Words of a product's name that say nothing about what it is.
_NAME_NOISE = {"the", "a", "an", "and", "for", "with", "of", "by", "in", "new"}

_FOOT = re.compile(r"^\W+|\W+$")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)  # a letter, unicode-aware


def _stem(word: str) -> str:
    """A word's form for matching: lowercase, without a possessive or plural
    "s" ("shoes" → "shoe", "class" stays "class")."""
    w = word.lower()
    w = re.sub(r"[’']s$", "", w)
    # plural s at the end, preceded by two letters and a non-s character
    w = re.sub(r"(?<=[^\W\d_][^\W\d_][^s])s$", "", w)
    return w


def _words_of(text: str) -> list[tuple[str, int]]:
    """The words of a text, each with where it ends."""
    return [(m.group(0), m.end()) for m in re.finditer(r"\S+", text)]


def _stems_of(text: str) -> list[str]:
    """The words of a text as stems, punctuation trimmed."""
    out = []
    for word, _ in _words_of(text):
        trimmed = _FOOT.sub("", word)
        if trimmed:
            out.append(_stem(trimmed))
    return [s for s in out if s]


def product_type_words(product_name: str, brand: str) -> list[str]:
    """The product type from its name: the name's words without the brand's,
    without model codes and numbers (Speedgoat 5, X-200) and filler.
    "Acme Men's Trail Running Shoes" by Acme → men, trail, running, shoe."""
    brand_stems = set(_stems_of(brand or ""))
    seen: dict[str, None] = {}
    for word in _stems_of(product_name or ""):
        if (
            word not in brand_stems
            and word not in _NAME_NOISE
            and not any(ch.isdigit() for ch in word)
            and len(word) > 1
        ):
            seen.setdefault(word, None)
    return list(seen)


def opening_cut(text: str) -> int:
    """Where the opening of `text` ends: at 40 words or 250 characters,
    whichever comes first."""
    words = _words_of(text)
    if words and len(words) > OPENING_WORDS:
        by_words = words[OPENING_WORDS - 1][1]
    else:
        by_words = len(text)
    return min(by_words, OPENING_CHARS)


def attributes_in(opening: str, type_words: list[str] | None = None) -> list[str]:
    """The key attributes an opening names: measures, materials, fits and
    uses, each once. A word of the product type isn't an attribute too:
    "running" in a running shoe's opening says what it is, not something
    more."""
    text = opening.lower()
    type_stems = set(type_words or [])
    found: list[str] = []
    for match in _MEASURE.finditer(text):
        value = re.sub(r"\s+", " ", match.group(0)).strip()
        found.append(value)
    for term, pattern in _TERMS:
        if all(_stem(w) in type_stems for w in term.split()):
            continue
        if any(term in f for f in found):
            continue
        if pattern.search(text):
            found.append(term)
    return found


def check_description_opening(
    description: str, product_name: str = "", brand: str = ""
) -> dict:
    """The verdict on one description: does its opening name the product type
    and at least two key attributes?

    `names_type` is None when the name says nothing of the type (only a brand
    and a model), so only the attributes can be judged.
    """
    text = re.sub(r"\s+", " ", description or "").strip()
    cut = opening_cut(text)
    opening = text[:cut]
    type_words = product_type_words(product_name, brand)
    opening_stems = set(_stems_of(opening))
    named = sum(1 for word in type_words if word in opening_stems)
    names_type = None if not type_words else named >= math.ceil(len(type_words) / 2)
    attributes = attributes_in(opening, type_words)
    return {
        "opening": opening,
        "cut": cut,
        "type_words": type_words,
        "names_type": names_type,
        "attributes": attributes,
        "passes": names_type is not False and len(attributes) >= MIN_ATTRIBUTES,
    }
