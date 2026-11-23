"""News collector: 5 verified AI news RSS feeds with full-text trafilatura extraction and grounded LLM summarization.

Feeds:
1. The Verge AI (Atom)
2. The Decoder (RSS2)
3. TechCrunch AI (RSS2)
4. Wired AI (RSS2)
5. MIT Technology Review AI (RSS2)
(VentureBeat 429 and MarkTechPost 403 are strictly omitted, not scraped or circumvented).

Features:
- Strict 24h UTC freshness enforcement via src.dates.is_fresh(..., now, 24)
- Full article text extraction with trafilatura
- Grounded summarization and entity extraction via LLMChain (Gemini -> Groq)
- Anti-hallucination verification via src.llm.verify.is_grounded (nulling or dropping ungrounded strings)
- Validated via src.models.News and persisted via store.insert_record("NEWS", ...)
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import feedparser
import trafilatura

from src import dates, models
from src.collectors.jobs import canonical_company
from src.core.http import AsyncFetcher, BlockedError, FetchError
from src.core.store import Store
from src.core.urls import normalize_url
from src.llm import verify as verify_mod
from src.llm.chain import LLMChain

log = logging.getLogger("news")

NEWS_FEEDS: Tuple[Tuple[str, str], ...] = (
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("The Decoder", "https://the-decoder.com/feed/"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("Wired AI", "https://www.wired.com/feed/tag/ai/latest/rss"),
    ("MIT Technology Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
)


def _utc_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)


def parse_feed_items(xml_text: str, kind: str = "auto") -> List[Dict[str, Any]]:
    """Parse raw XML (Atom or RSS2) using feedparser into normalized dictionaries."""
    parsed = feedparser.parse(xml_text)
    out: List[Dict[str, Any]] = []
    for e in getattr(parsed, "entries", []):
        title = (e.get("title") or "").strip()
        link = (e.get("link") or "").strip()
        pub = (e.get("published") or e.get("updated") or "").strip()
        summary = (e.get("summary") or "").strip()
        if title and link:
            out.append({"title": title, "url": link, "raw_date": pub, "fallback_text": summary})
    return out


def _parse_entry_date(entry: Any, html: Optional[str], now: datetime) -> Optional[datetime]:
    for candidate in (entry.get("published"), entry.get("updated")):
        if candidate:
            try:
                dt = dates.parse_any(candidate, now)
                if dt:
                    return dt
            except Exception:
                pass
    if html:
        try:
            res = dates.extract_published(html, now)
            if res:
                return res[0]
        except Exception:
            pass
    return None


def _extract_article_text(html: str, fallback_summary: str) -> str:
    if html:
        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if extracted and len(extracted.strip()) >= 50:
            return extracted.strip()
    # If trafilatura fails to extract or HTML empty, clean fallback summary
    import re
    plain = re.sub(r"<[^>]+>", " ", fallback_summary or "")
    return re.sub(r"\s+", " ", plain).strip()


async def extract_summary_and_entities(
    chain: Optional[LLMChain],
    title: str,
    article_text: str,
) -> Tuple[Optional[str], List[str]]:
    """Extract factual summary and named entities using LLMChain with strict grounding verification."""
    if not article_text.strip():
        return None, []

    if chain is None or not chain.alive():
        # Without LLM: no invented summary; take first sentence if grounded, else None
        first_sent = article_text.split(". ")[0].strip()
        if len(first_sent) >= 30 and verify_mod.is_grounded(first_sent, article_text):
            return f"{first_sent}.", []
        return None, []

    system = (
        "You are a factual news analyzer. Based strictly on the provided article text, extract:\n"
        "1. 'summary': A concise 1-2 sentence factual summary directly stated in the text.\n"
        "2. 'entities': A list of companies, organizations, or products explicitly named in the text.\n"
        "Return a JSON object: {\"summary\": \"...\", \"entities\": [\"...\"]}\n"
        "Never include facts, entities, or speculations not present in the article text."
    )

    def make_prompt(txt: str) -> str:
        return f"Title: {title}\n\nArticle text:\n{txt}"

    def validate(obj: Any) -> Dict[str, Any]:
        if not isinstance(obj, dict):
            raise ValueError("Expected JSON object")
        summary = obj.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Missing summary")
        entities = obj.get("entities")
        if not isinstance(entities, list):
            obj["entities"] = []
        return obj

    try:
        res, _ = await chain.extract(
            system,
            article_text,
            make_prompt,
            validate,
            input_cap=8000,
        )
    except Exception as exc:
        log.warning("LLM news extraction failed: %s", exc)
        return None, []

    # Grounding verification: null out ungrounded summary, drop ungrounded entities
    summary_raw = str(res.get("summary") or "").strip()
    valid_summary: Optional[str] = None
    if summary_raw and verify_mod.is_grounded(summary_raw, article_text, min_ratio=0.65):
        valid_summary = summary_raw
    else:
        log.debug("News summary for '%s' failed grounding verification; nulled", title)

    raw_entities = res.get("entities") or []
    grounded_entities: List[str] = []
    seen_lower = set()
    for item in raw_entities:
        ent = str(item).strip()
        if not ent or len(ent) > 100 or ent.lower() in seen_lower:
            continue
        if verify_mod.is_grounded(ent, article_text):
            seen_lower.add(ent.lower())
            grounded_entities.append(ent)
        else:
            log.debug("Entity '%s' for '%s' failed grounding; dropped", ent, title)

    return valid_summary, grounded_entities


async def collect_news(
    store: Store,
    fetcher: AsyncFetcher,
    chain: Optional[LLMChain] = None,
    now: Optional[datetime] = None,
) -> int:
    """Collect 24h fresh news from 5 verified feeds with full text and grounded extraction."""
    run_now = _utc_now(now)
    added = 0
    stats = {"feeds_ok": 0, "fresh_candidates": 0, "inserted": 0, "errors": 0}

    log.info("Starting news collection across 5 feeds (reference time UTC: %s)", run_now.isoformat())

    for source_name, feed_url in NEWS_FEEDS:
        try:
            res = await fetcher.get(feed_url, respect_robots=False)
            if not res.ok:
                log.warning("news feed=%s (%s) returned status %s", source_name, feed_url, res.status)
                stats["errors"] += 1
                continue
            parsed = feedparser.parse(res.text)
            stats["feeds_ok"] += 1
        except (FetchError, BlockedError, Exception) as exc:
            stats["errors"] += 1
            log.warning("news feed=%s (%s) failed: %s", source_name, feed_url, exc)
            continue

        entries = getattr(parsed, "entries", [])
        log.info("%s: found %d feed entries", source_name, len(entries))

        for entry in entries:
            raw_link = entry.get("link") or ""
            if not raw_link:
                continue
            article_url = normalize_url(urljoin(feed_url, raw_link))

            # Parse publication date
            entry_dt = _parse_entry_date(entry, None, run_now)
            html: Optional[str] = None

            # If date missing from feed, fetch page once to check meta/time tags
            if entry_dt is None:
                try:
                    page_res = await fetcher.get(article_url, respect_robots=False)
                    if page_res.ok and page_res.text:
                        html = page_res.text
                        entry_dt = _parse_entry_date(entry, html, run_now)
                except Exception:
                    html = None

            if entry_dt is None:
                continue

            # Enforce strict 24h UTC freshness
            if not dates.is_fresh(entry_dt, run_now, 24):
                continue

            stats["fresh_candidates"] += 1

            # Fetch full text if not already fetched
            if html is None:
                try:
                    page_res = await fetcher.get(article_url, respect_robots=False)
                    if page_res.ok and page_res.text:
                        html = page_res.text
                except Exception as exc:
                    stats["errors"] += 1
                    log.debug("article fetch failed url=%s: %s", article_url, exc)
                    continue

            title = (entry.get("title") or "").strip()
            if not title:
                continue

            article_text = _extract_article_text(html or "", entry.get("summary") or "")
            summary, entities = await extract_summary_and_entities(chain, title, article_text)

            news_rec = models.News(
                source=models.Source(name=source_name, url=article_url),
                collectedAt=run_now,
                content=models.NewsContent(
                    title=title,
                    date=entry_dt,
                    summary=summary,
                    entities=entities,
                ),
            )

            # Log entity mappings for grounded extracted entities
            for ent in entities:
                canonical, method, score = canonical_company(ent)
                store.log_mapping(
                    entity_type="NEWS",
                    raw_name=ent,
                    canonical_name=canonical,
                    match_method=method,
                    score=score,
                    source_url=article_url,
                )

            inserted = store.insert_record("NEWS", article_url, news_rec.model_dump(mode="json"))
            if inserted:
                store.mark_seen(article_url)
                added += 1
                stats["inserted"] += 1

    total_in_db = store.count("NEWS")
    log.info("News collection finished: +%d newly inserted (total %d in DB, stats=%s)", added, total_in_db, stats)
    return total_in_db
