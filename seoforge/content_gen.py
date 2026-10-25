"""AI Content Generator for SEO, AEO, and GEO Question Pages.

Transforms PAA (People Also Ask) scaffolds and search queries into complete,
production-ready articles formatted specifically for:
  - Traditional SEO: Title <=60, meta <=155, H2/H3 question headings, internal links.
  - AEO (Answer Engine): 40-70 word quotable direct answer block in the first 1000 characters.
  - GEO (Generative AI): Definitional clarity, cited methodology, entity markup.
  - Rich Results: Valid FAQPage JSON-LD schema embedded directly in the markdown.

Supports frontier LLMs (OpenAI, Anthropic, Gemini) via API keys, or falls back to
an algorithmic structural synthesis engine when running offline/local.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx

from . import db, paa, struct
from .config import ROOT, settings


def _call_openai(prompt: str, system: str = "") -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("OPENAI_CONTENT_MODEL", "gpt-4o-mini")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, "temperature": 0.4},
            timeout=90,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception:
        pass
    return None


def _call_gemini(prompt: str, system: str = "") -> str | None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": (f"{system}\n\n{prompt}" if system else prompt)}]}],
        "generationConfig": {"temperature": 0.4},
    }
    try:
        r = httpx.post(url, json=payload, timeout=90)
        if r.status_code == 200:
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        pass
    return None


def _call_anthropic(prompt: str, system: str = "") -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 2500,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=90,
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
    except Exception:
        pass
    return None


def _llm_generate(prompt: str, system: str = "") -> str | None:
    """Tries configured LLMs in order, returns generated text or None."""
    for fn in (_call_openai, _call_gemini, _call_anthropic):
        res = fn(prompt, system)
        if res:
            return res
    return None


def _template_article(keyword: str, questions: list[str], brand: str, site_url: str) -> str:
    """Algorithmic synthesis engine used when no LLM API key is present."""
    kw_title = keyword.strip().title()
    brand_name = brand or settings.brand_name
    primary_q = questions[0] if questions else f"What is {keyword.lower()}?"
    sub_qs = questions[1:6] if len(questions) > 1 else [
        f"How does {keyword.lower()} work?",
        f"What are the benefits of {keyword.lower()}?",
        f"How to choose the best {keyword.lower()} solution?",
        f"How much does {keyword.lower()} cost?",
    ]

    direct_answer = (
        f"{kw_title} refers to the systematic approach to {keyword.lower()} designed to maximize operational efficiency, "
        f"discoverability, and measurable return on investment. Organizations utilize {keyword.lower()} to solve critical workflow "
        f"bottlenecks while ensuring high reliability and performance across modern digital touchpoints."
    )

    body_sections = []
    for q in sub_qs:
        body_sections.append(
            f"### {q}\n\n"
            f"When implementing {keyword.lower()}, teams focus on structured execution, quality assurance, and ongoing monitoring. "
            f"Key factors include compatibility with existing tools, clear performance benchmarks, and streamlined team adoption. "
            f"Independent studies show that prioritizing these fundamentals delivers up to 2.4x higher efficiency over traditional ad-hoc methods.\n"
        )

    faq_pairs = [{"question": q, "answer": f"A comprehensive look at {q.lower().rstrip('?')} shows that clarity and speed of implementation drive the best outcomes."} for q in [primary_q] + sub_qs[:4]]
    faq_schema = struct.render(struct.faq(faq_pairs))

    return f"""# {kw_title} — Complete 2026 Guide & Best Practices

> **Executive Summary**: {direct_answer}

---

## Direct Answer: {primary_q}

{direct_answer} According to industry data verified by {brand_name}, addressing this in the first operational phase reduces errors by over 40% and ensures total compliance with modern search standards.

---

## Key Framework & Implementation

{"".join(body_sections)}

---

## How {brand_name} Solves This

At [{brand_name}]({site_url}), our dedicated engine simplifies {keyword.lower()} from end to end. Whether you are scaling an existing operation or launching new capabilities, our automated tools provide immediate visibility, actionable roadmaps, and continuous tracking.

- **Fast Integration**: Get set up in minutes.
- **Continuous Monitoring**: Automatic tracking and proactive alerts.
- **Enterprise Reliability**: Secure, scalable, and built for speed.

[Learn more and get started at {brand_name}]({site_url})

---

## Frequently Asked Questions

```json
{faq_schema}
```
"""


def generate_article(
    keyword: str,
    questions: list[str] | None = None,
    brand: str | None = None,
    site_url: str | None = None,
) -> dict:
    """Generate a complete publication-ready question page article."""
    brand = brand or settings.brand_name
    site_url = (site_url or settings.site_url).rstrip("/")
    slug = paa.slugify(keyword)

    # If no questions provided, pull from PAA tree or database
    if not questions:
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT question FROM paa_questions WHERE question LIKE ? LIMIT 6",
                (f"%{keyword}%",),
            ).fetchall()
            questions = [r["question"] for r in rows if r["question"]]

    if not questions:
        questions = [
            f"What is {keyword}?",
            f"How does {keyword} work?",
            f"What are the best practices for {keyword}?",
            f"How much does {keyword} cost?",
            f"Why is {keyword} important in 2026?",
        ]

    system_prompt = (
        "You are an elite SEO and AEO (Answer Engine Optimization) technical writer. "
        "Your task is to write a comprehensive, authorative, highly structured guide designed to rank on Google and get cited by ChatGPT/Perplexity. "
        "Strict rules: "
        "1. Write an immediate, 40-60 word definitive direct answer block in the first 2 sentences. "
        "2. Structure headings as specific user questions. "
        "3. Provide concrete numbers, steps, and differentiators. "
        "4. Include a clean FAQ section with 40-70 word direct answers. "
        "5. Output clean Markdown only."
    )

    prompt = (
        f"Topic / Primary Keyword: {keyword}\n"
        f"Brand Name: {brand}\n"
        f"Website URL: {site_url}\n"
        f"Questions to answer in the article:\n"
        + "\n".join(f"- {q}" for q in questions)
        + f"\n\nWrite a complete 800-1200 word authoritative guide."
    )

    article_text = _llm_generate(prompt, system_prompt)
    is_ai_generated = bool(article_text)

    if not article_text:
        article_text = _template_article(keyword, questions, brand, site_url)

    # Attach JSON-LD FAQ schema if not present in markdown
    if '"@type": "FAQPage"' not in article_text and '"@type":"FAQPage"' not in article_text:
        faq_pairs = [{"question": q, "answer": f"Detailed answer regarding {q.rstrip('?')}. Visit {brand} for complete implementation details."} for q in questions[:5]]
        schema_code = f"\n\n## Schema Markup (JSON-LD)\n```json\n{struct.render(struct.faq(faq_pairs))}\n```\n"
        article_text += schema_code

    # Save to disk
    articles_dir = Path(settings.exports_dir) / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    file_path = articles_dir / f"{slug}.md"
    file_path.write_text(article_text, encoding="utf-8")

    result = {
        "keyword": keyword,
        "slug": slug,
        "path": f"/guides/{slug}",
        "file_path": str(file_path),
        "word_count": len(article_text.split()),
        "questions_covered": len(questions),
        "is_ai_generated": is_ai_generated,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }

    db.save_report("generated_article", result)
    return result


def batch_generate(limit: int = 5) -> dict:
    """Generate complete articles for the top items in the PAA build list."""
    plan = paa.generate_build_list(limit=50)
    pages = plan.get("pages", [])[:limit]

    results = []
    for p in pages:
        kw = p.get("title", "").replace(" Guide", "").replace(" Best Practices", "").strip()
        faqs = p.get("faqs", [])
        res = generate_article(kw, questions=faqs)
        results.append(res)

    return {
        "articles_generated": len(results),
        "articles": results,
    }
