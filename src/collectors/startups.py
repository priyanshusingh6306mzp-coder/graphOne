"""Startups from the yc-oss community mirror of Y Combinator's public company directory.

source.url points to the original ycombinator.com company page. We also stash each company's
website/one-liner in the aux table for the products collector (pricing pages).
"""
import logging
from typing import Optional

from ..core.http import AsyncFetcher
from ..core.store import Store
from ..models import Source, Startup, StartupContent, StartupData
from . import parsing as P

log = logging.getLogger("startups")
YC_URL = "https://yc-oss.github.io/api/companies/all.json"


async def collect_startups(store: Store, fetcher: AsyncFetcher, limit: Optional[int] = None,
                           scope: str = "ai") -> int:
    data = await fetcher.get_json(YC_URL)
    def in_scope(c) -> bool:
        if scope == "all":
            return True
        if P.is_ai_startup(c):
            return True
        return scope == "ai+b2b" and c.get("industry") == "B2B"  # software companies that publish pricing

    ai = [c for c in data if c.get("name") and c.get("url") and c.get("status") == "Active" and in_scope(c)]
    # prefer records with employee count and a website
    ai.sort(key=lambda c: (P.employee_count(c) is None, not c.get("website")))
    if limit:
        ai = ai[:limit]
    added = 0
    for c in ai:
        rec = Startup(
            source=Source(name="Y Combinator", url=c["url"]),
            content=StartupContent(entityName=c["name"].strip(),
                                   data=StartupData(employeeCount=P.employee_count(c))),
        )
        if store.insert_record("STARTUP", rec.source.url, rec.model_dump(mode="json")):
            added += 1
        store.put_aux("startup_meta", c["url"], {
            "name": c["name"].strip(), "yc_url": c["url"], "website": c.get("website"),
            "one_liner": c.get("one_liner"), "tags": c.get("tags") or [],
        })
    log.info("startups: +%d (total %d) from %d AI candidates", added, store.count("STARTUP"), len(ai))
    return store.count("STARTUP")
