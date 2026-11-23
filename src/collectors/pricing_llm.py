"""Grounded LLM stage for pricing pages the rules could not classify.

Guards against hallucination: the model must return an exact quote from the page as evidence;
the quote must appear verbatim in the fetched text AND be consistent with the label. Otherwise the
page is discarded (never guessed).
"""
import asyncio
import logging
import re
from collections import Counter
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from ..core.http import AsyncFetcher, FetchError
from ..core.store import Store
from ..llm.chain import AllProvidersFailed, LLMChain
from ..llm.verify import quote_in_text
from ..models import PricingModel, Product, ProductContent, Source
from . import parsing as P

log = logging.getLogger("pricing_llm")

SYSTEM = (
    "You classify a software product's pricing model using ONLY the pricing-page text provided. "
    "Return ONLY a JSON object: {\"pricingModel\": \"FREE\"|\"FREEMIUM\"|\"PAID\"|\"ENTERPRISE\"|\"UNKNOWN\", "
    "\"evidence\": \"<one exact quote copied from the text, max 200 characters>\"}. "
    "FREE = entirely free. FREEMIUM = a free plan AND paid plans. PAID = paid only (a free trial counts as PAID). "
    "ENTERPRISE = no public prices, contact sales. If the text does not clearly show the pricing model, "
    "answer UNKNOWN with empty evidence. Never guess and never invent text."
)
_PRICE = re.compile(r"(?i)[$€£₹]\s?\d|\d\s?(?:usd|eur|inr)\b|/\s?(?:mo|month|year|user|seat)|per (?:month|user|seat|year)")


def _validate(obj: Any) -> Dict[str, str]:
    if not isinstance(obj, dict):
        raise ValueError("not an object")
    label = str(obj.get("pricingModel", "")).upper()
    if label not in {"FREE", "FREEMIUM", "PAID", "ENTERPRISE", "UNKNOWN"}:
        raise ValueError(f"bad label {label!r}")
    return {"pricingModel": label, "evidence": str(obj.get("evidence") or "")[:300]}


def label_supported(label: str, evidence: str, page_text: str = "") -> bool:
    return P.llm_label_supported(label, evidence, page_text)


async def _page_text(fetcher: AsyncFetcher, item: Dict[str, Any]) -> str:
    """Re-fetch the pricing page so the model sees the full text (the parked copy is truncated)."""
    try:
        res = await fetcher.get(item["url"], max_retries=1)
        if res.ok:
            text = P.html_to_text(res.text)
            if len(text) > 200:
                return text
    except FetchError:
        pass
    return item["text"]


async def collect_pricing_llm(store: Store, chain: LLMChain, fetcher: AsyncFetcher,
                              limit: Optional[int] = None, concurrency: int = 1, reprocess: bool = False) -> int:
    if reprocess:  # LLM answers are cached by content hash, so re-judging them with new rules costs no API calls
        store.clear_aux("pricing_llm_done")
    pending = [m for m in store.aux_items("pricing_pending")
               if not store.has_aux("pricing_llm_done", m["url"])]
    candidates = [m for m in pending if P.is_llm_pricing_candidate(m["url"])]
    log.info("pending pricing pages: %d (%d look like real pricing pages; the rest are skipped)",
             len(pending), len(candidates))
    pending = candidates
    if limit:
        pending = pending[:limit]
    stats: Counter = Counter()
    fetch_sem = asyncio.Semaphore(8)           # page downloads are cheap: do them concurrently
    llm_sem = asyncio.Semaphore(concurrency)   # LLM calls are rate-limited: one at a time

    async def run(item: Dict[str, Any]) -> None:
        url = item["url"]
        async with fetch_sem:
            text = await _page_text(fetcher, item)
        async with llm_sem:
            # An answer we already have for this URL (or a cache hit) needs no provider at all, so re-judging
            # with improved rules works even when every provider's quota is exhausted.
            answer = store.get_aux("pricing_llm_answer", url)
            if answer:
                res, provider = {"pricingModel": answer["pricingModel"], "evidence": answer["evidence"]}, answer["provider"]
            else:
                try:
                    res, provider = await chain.extract(
                        SYSTEM, text, lambda t: f"Pricing page text:\n{t}\n\nReturn the JSON object.", _validate,
                        cache_extra="pricing-v1", input_cap=3500)
                except AllProvidersFailed as exc:
                    if chain.alive():
                        stats["llm_failed"] += 1
                        log.warning("LLM failed for %s: %s", url, exc)
                    else:
                        stats["skipped_no_provider"] += 1   # quota/model problem already reported once
                    return  # not marked done: retried on the next run
                store.put_aux("pricing_llm_answer", url, {**res, "provider": provider})
            store.put_aux("pricing_llm_done", url, {"label": res["pricingModel"], "provider": provider})
            label, evidence = res["pricingModel"], res["evidence"]

            def reject(reason: str) -> None:
                stats[reason] += 1
                log.info("REJECTED (%s) %s label=%s evidence=%r", reason, url, label, evidence[:140])

            if label == "UNKNOWN":
                stats["unknown"] += 1
                return
            if not quote_in_text(evidence, text):
                return reject("rejected_ungrounded")
            if not label_supported(label, evidence, text):
                return reject("rejected_inconsistent")
            host = urlsplit(url).netloc.lower().removeprefix("www.")
            rec = Product(source=Source(name=host, url=url),
                          content=ProductContent(startupName=item["startup"], pricingModel=PricingModel(label)))
            if store.insert_record("PRODUCT", url, rec.model_dump(mode="json")):
                store.put_aux("pricing_evidence", url, {"startup": item["startup"], "label": label,
                                                        "evidence": [evidence], "via": provider})
                stats[f"llm_{label}"] += 1
                log.info("ACCEPTED %s %s via %s: %r", url, label, provider, evidence[:100])

    await asyncio.gather(*(run(m) for m in pending))
    if not chain.alive():
        log.error("All LLM providers were disabled (see the warnings above, usually a wrong model name "
                  "or key). Fix GEMINI_MODEL / GROQ_MODEL in .env and re-run; unfinished pages are retried.")
    log.info("pricing-llm done: %s | chain: %s", dict(stats), chain.stats)
    return store.count("PRODUCT")
