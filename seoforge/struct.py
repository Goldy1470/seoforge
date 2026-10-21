from __future__ import annotations

from urllib.parse import urljoin

from .config import settings

CTA = {"@context": "https://schema.org"}


def organization() -> dict:
    import os

    name = settings.brand_name
    site = settings.site_url.rstrip("/")
    same_as = [s.strip() for s in os.getenv("SEOFORGE_SAME_AS", "").split(",") if s.strip()]
    return {
        **CTA,
        "@type": "Organization",
        "@id": f"{site}/#organization",
        "name": name,
        "url": site,
        "description": settings.brand_tagline,
        "sameAs": same_as,
    }


def website_search_action() -> dict:
    site = settings.site_url.rstrip("/")
    return {
        **CTA,
        "@type": "WebSite",
        "@id": f"{site}/#website",
        "url": site,
        "name": settings.brand_name,
        "publisher": {"@id": f"{site}/#organization"},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint", "urlTemplate": f"{site}/search?q={{search_term_string}}"},
            "query-input": "required name=search_term_string",
        },
    }


def breadcrumb(parts: list[tuple[str, str]]) -> dict:
    return {
        **CTA,
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": name,
                "item": urljoin(settings.site_url, href),
            }
            for i, (name, href) in enumerate(parts)
        ],
    }


def faq(qa_pairs: list[tuple[str, str]] | list[dict]) -> dict:
    items = []
    for pair in qa_pairs:
        q, a = pair if isinstance(pair, tuple) else (pair.get("question"), pair.get("answer"))
        if not (q and a):
            continue
        items.append(
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
        )
    if not items:
        raise ValueError("FAQPage schema needs at least one Q/A pair")
    return {
        **CTA,
        "@type": "FAQPage",
        "@id": f"{settings.site_url.rstrip('/')}/#faq{abs(hash(tuple(i['name'] for i in items)))}",
        "mainEntity": items,
    }


def product(
    product_id: str,
    title: str,
    description: str,
    url: str,
    image: str,
    price: str,
    currency: str = "INR",
    availability: str = "InStock",
    brand: str | None = None,
    gtin: str | None = None,
    reviews: list[dict] | None = None,
    faqs: list[tuple[str, str]] | None = None,
) -> dict:
    """Product + AggregateRating + Review + optional embedded FAQ block.

    Google review snippet requirements: reviewer must be author name or org.
    Attach `reviews=[{"author","rating":1-5,"text","date"}]`.
    """
    site = settings.site_url.rstrip("/")
    schema = {
        **CTA,
        "@type": "Product",
        "@id": f"{url.rstrip('/')}/#product",
        "name": title,
        "description": description,
        "image": [image],
        "url": url,
        "brand": {
            "@type": "Brand",
            "name": brand or settings.brand_name,
        },
        "sku": product_id,
        "offers": {
            "@type": "Offer",
            "url": url,
            "priceCurrency": currency,
            "price": price,
            "availability": f"https://schema.org/{'InStock' if availability.lower() in {'in stock', 'instock'} else 'OutOfStock' if 'out' in availability.lower() else availability}",
            "itemCondition": "https://schema.org/NewCondition",
            "priceValidUntil": _next_quarter(),
        },
    }
    if gtin:
        schema["gtin"] = gtin
    if reviews:
        ratings = [float(r["rating"]) for r in reviews if r.get("rating")]
        if ratings:
            schema["aggregateRating"] = {
                "@type": "AggregateRating",
                "ratingValue": round(sum(ratings) / len(ratings), 1),
                "reviewCount": len(ratings),
                "bestRating": 5,
                "worstRating": 1,
            }
            schema["review"] = [
                {
                    "@type": "Review",
                    "author": {
                        "@type": "Person",
                        "name": r.get("author") or "customer",
                        "url": r.get("author_url", ""),
                    },
                    "datePublished": r.get("date", ""),
                    "reviewRating": {
                        "@type": "Rating",
                        "ratingValue": min(5, max(1, float(r["rating"]))),
                        "bestRating": 5,
                        "worstRating": 1,
                    },
                    "itemReviewed": {"@type": "Product", "name": title},
                    "reviewBody": r.get("text", ""),
                }
                for r in reviews
            ]
    return schema


def _next_quarter() -> str:
    import datetime as dt

    return str(dt.date.today() + dt.timedelta(days=90))


def product_faq_jsonld(qa_pairs: list[dict]) -> dict:
    return faq(qa_pairs)


def validate(schema: dict) -> list[str]:
    """Loose validation of the fields Google requires for rich results."""
    warnings = []
    t = schema.get("@type")

    def missing(path: dict, key: str):
        if not path.get(key):
            warnings.append(f"{t}.{key} is missing/empty (required for {t} rich results)")

    def nested(path: dict, outer: str, inner: str, required: list[str]):
        node = path.get(outer) or {}
        for r in required:
            missing(node, r)
            if not path.get(outer):
                warnings.append(f"{t}.{outer} missing entirely (Google: required)")

    if t == "Product":
        missing(schema, "image")
        missing(schema, "name")
        if not (schema.get("gtin") or schema.get("mpn") or schema.get("brand")):
            warnings.append("Product needs gtin/mpn/brand for full Pricing rich result")
        offers = schema.get("offers") or []
        offers = offers if isinstance(offers, list) else [offers]
        for o in offers:
            for key in ("price", "priceCurrency", "availability", "url"):
                if not o.get(key):
                    warnings.append(f"Product.offers.{key} missing")
        for r in schema.get("review", []) or []:
            if not r.get("author", {}).get("name"):
                warnings.append("Review.author.name required")
            if not r.get("reviewRating"):
                warnings.append("Review.rating required")
    if t == "FAQPage":
        for q in schema.get("mainEntity", []):
            if len(q.get("acceptedAnswer", {}).get("text", "")) < 40:
                warnings.append(f"FAQ answer too short (<40 chars): {q.get('name')}")
    return warnings


def render(schema: dict, indent: int = 2) -> str:
    import json

    return json.dumps(schema, ensure_ascii=False, indent=indent)
