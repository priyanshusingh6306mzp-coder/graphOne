"""Products: one per AI startup, from the startup's own pricing page.

pricingModel is decided by rules on the page text (evidence snippets stored in aux for audit).
Ambiguous pages are parked in aux('pricing_pending') for the grounded LLM stage; pages with no
pricing page or no usable text are skipped, never guessed.
"""
import asyncio
import logging
import re
from collections import Counter
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlsplit

from ..core.http import AsyncFetcher, FetchError
from ..core.store import Store
from ..models import PricingModel, Product, ProductContent, Source
from . import parsing as P

log = logging.getLogger("products")


async def _process(fetcher: AsyncFetcher, store: Store, meta: Dict[str, Any], stats: Counter) -> None:
    site = meta.get("website")
    if not site:
        stats["no_website"] += 1
        return
    try:
        home = await fetcher.get(site)
    except FetchError:
        stats["homepage_unreachable"] += 1
        return
    if not home.ok:
        stats["homepage_http_error"] += 1
        return
    base = home.final_url or site
    candidates = P.find_pricing_links(home.text, base) or [urljoin(base, "/pricing")]
    for url in candidates[:2]:
        try:
            page = await fetcher.get(url)
        except FetchError:
            continue
        if not page.ok:
            continue
        text = P.html_to_text(page.text)
        label, evidence = P.classify_pricing(text)
        final = page.final_url or url
        # Every label is only trustworthy on a real pricing/plans page, not when the link redirected to the homepage or a demo form
        # (homepages show "$0" in product mockups, "Book a demo" in the navbar, etc.).
        if label and not P.looks_like_pricing_url(final):
            stats["not_a_pricing_page"] += 1
            return
        if label:
            host = urlsplit(final).netloc.lower().removeprefix("www.")
            rec = Product(
                source=Source(name=host, url=final),
                content=ProductContent(startupName=meta["name"], pricingModel=PricingModel(label)),
            )
            if store.insert_record("PRODUCT", final, rec.model_dump(mode="json")):
                store.put_aux("pricing_evidence", final, {"startup": meta["name"], "label": label,
                                                          "evidence": evidence})
                stats[f"classified_{label}"] += 1
            else:
                stats["duplicate"] += 1
        else:
            store.put_aux("pricing_pending", final, {"startup": meta["name"], "url": final,
                                                      "text": text[:6000]})
            stats["ambiguous"] += 1
        return
    stats["no_pricing_page"] += 1


async def collect_products(store: Store, fetcher: AsyncFetcher, limit: Optional[int] = None,
                           concurrency: int = 40) -> int:
    startups = [m for m in store.aux_items("startup_meta")
                if not store.has_aux("product_done", m["yc_url"])]
    if limit:
        startups = startups[:limit]
    stats: Counter = Counter()
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def run(meta):
        nonlocal done
        async with sem:
            try:
                await _process(fetcher, store, meta, stats)
                store.put_aux("product_done", meta["yc_url"], {})
            except Exception as exc:  # one bad site must never kill the run
                stats["error"] += 1
                log.warning("product failed for %s: %s", meta.get("website"), exc)
        done += 1
        if done % 200 == 0:
            log.info("progress %d/%d | products=%d | %s", done, len(startups),
                     store.count("PRODUCT"), dict(stats))

    await asyncio.gather(*(run(m) for m in startups))
    log.info("products done: %s", dict(stats))
    return store.count("PRODUCT")
