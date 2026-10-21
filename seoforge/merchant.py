from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from . import db
from .config import settings

NS_G = "http://base.google.com/ns/1.0"


def upsert_product(p: dict) -> None:
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO products(id,title,description,link,image_link,price,brand,
               availability,gtin,condition)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                 description=excluded.description, link=excluded.link,
                 image_link=excluded.image_link, price=excluded.price,
                 brand=excluded.brand, availability=excluded.availability,
                 gtin=excluded.gtin, condition=excluded.condition""",
            (
                p.get("id") or p.get("sku"),
                p.get("title"),
                p.get("description"),
                p.get("link"),
                p.get("image_link"),
                p.get("price"),
                p.get("brand") or settings.brand_name,
                p.get("availability", "in stock"),
                p.get("gtin"),
                p.get("condition", "new"),
            ),
        )


def load_products_csv(path: str) -> int:
    path = Path(path)
    n = 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            upsert_product(row)
            n += 1
    return n


def products_from_db() -> list[dict]:
    with db._conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM products").fetchall()]


def build_feed() -> str:
    products = products_from_db()
    if not products:
        raise SystemExit("No products. Add via CLI: import-products file.csv or upsert-product")
    ET.register_namespace("g", NS_G)
    rss = ET.Element("rss", {"version": "2.0", "xmlns:g": NS_G})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{settings.brand_name} - Google Merchant Center feed"
    for p in products:
        item = ET.SubElement(channel, "item")
        _sub(item, "id", p["id"])
        _sub(item, "title", p["title"])
        _sub(item, "description", _clean(p.get("description") or ""))
        link = p["link"] or f"{settings.site_url.rstrip('/')}/products/{p['id']}"
        _sub(item, "link", link)
        img = p["image_link"] or ""
        if img:
            _sub(item, "image_link", img)
        price = (p.get("price") or "").strip()
        if price and not price[-1].isalpha():
            _sub(item, "price", f"{price} INR")
        _sub(item, "brand", p.get("brand") or settings.brand_name)
        _sub(item, "condition", "new")
        av = (p.get("availability") or "in stock").strip().lower()
        _sub(item, "availability", av.replace(" ", ""))
    ET.indent(rss, space="  ")
    xml = xml_header(ET.tostring(rss, encoding="unicode"))
    out = Path(settings.exports_dir) / "merchant_center.xml"
    out.write_text(xml, encoding="utf-8")
    return str(out)


def xml_header(s: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + s


def _sub(parent, tag: str, text: str | None):
    if text is not None:
        el = ET.SubElement(parent, "{%s}%s" % (NS_G, tag))
        el.text = text


def _clean(s: str, limit: int = 5000) -> str:
    import re

    s = re.sub(r"<[^>]+>", "", s)
    return " ".join(s.split())[:limit]


def upsert_product_json(payload: str) -> str:
    data = json.loads(payload)
    upsert_product(data)
    return f"product {data.get('id')} saved"
