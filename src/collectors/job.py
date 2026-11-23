from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import feedparser

from src import dates, models
from src.core.http import AsyncFetcher
from src.core.store import Store
from src.core.urls import normalize_url

log = logging.getLogger("jobs")

GREENHOUSE_SLUGS = ("anthropic", "scaleai", "databricks")
ASHBY_SLUGS = ("openai", "cohere", "perplexity")
LEVER_SLUGS = ("palantir", "scaleai", "cohere", "openai")
REMOTEOK_URL = "https://remoteok.com/api"
WWR_FEEDS = (
    ("WeWorkRemotely", "https://weworkremotely.com/categories/remote-programming-jobs.rss"),
    ("WeWorkRemotely", "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"),
)

AI_KEYWORDS_RE = re.compile(
    r"\b(ai|artificial intelligence|machine learning|ml|llm|nlp|computer vision|"
    r"deep learning|mlops|data scientist|research scientist|generative|agentic|inference)\b",
    re.IGNORECASE,
)


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _is_ai_relevant(*parts: str | None) -> bool:
    blob = " ".join(p for p in parts if p)
    return bool(AI_KEYWORDS_RE.search(blob))


def classify_role_family(title: str | None, department: str | None = None) -> str:
    """Deterministic role_family classifier from title/department before any LLM."""
    text = f"{department or ''} {title or ''}".lower()
    if any(k in text for k in ("research scientist", "research engineer", "ml researcher", "applied scientist", "research")):
        return "Research"
    if any(k in text for k in ("engineer", "developer", "mlops", "infrastructure", "platform", "backend", "frontend", "fullstack", "data scientist", "scientist")):
        return "Engineering"
    if any(k in text for k in ("product manager", "product lead", "product")):
        return "Product"
    if any(k in text for k in ("design", "designer", "ux", "ui")):
        return "Design"
    if any(k in text for k in ("sales", "account executive", "gtm", "solutions architect", "customer success", "revenue")):
        return "Sales"
    if any(k in text for k in ("operations", "ops", "recruiting", "people", "finance", "legal", "strategy")):
        return "Operations"
    return "Other"


def infer_is_remote(explicit_remote: Any, *location_hints: str | None) -> bool:
    """Derive remote eligibility from structured flags or location/workplace text."""
    if isinstance(explicit_remote, bool):
        return explicit_remote
    blob = " ".join(str(x) for x in location_hints if x).lower()
    if not blob:
        return False
    if "non-remote" in blob or "onsite only" in blob or "on-site only" in blob:
        return False
    return "remote" in blob or "anywhere" in blob or "distributed" in blob


def _parse_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        # Handle epoch seconds or milliseconds
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if hasattr(dates, "parse_any"):
            try:
                dt = dates.parse_any(s)
                if dt:
                    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _is_fresh_24h(dt: datetime | None, now: datetime) -> bool:
    if dt is None:
        return False
    if hasattr(dates, "is_fresh"):
        try:
            return bool(dates.is_fresh(dt, now, 24))
        except TypeError:
            return bool(dates.is_fresh(dt, now, hours=24))
    age_hours = (now - dt).total_seconds() / 3600.0
    return 0.0 <= age_hours <= 24.0


def _build_job_payload(
    *,
    source_name: str,
    source_url: str,
    company: str,
    published_dt: datetime,
    is_remote: bool,
    role_family: str,
    title: str | None,
    collected_at: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "recordType": "JOB",
        "source": {
            "name": source_name,
            "url": source_url,
        },
        "content": {
            "company": company.strip(),
            "date": _iso(published_dt),
            "is_remote": bool(is_remote),
            "role_family": role_family,
            "title": (title or "").strip() or None,
        },
        "collectedAt": collected_at,
    }


def _try_insert_job(
    store: Store,
    *,
    source_name: str,
    raw_url: str,
    company: str,
    published_dt: datetime,
    is_remote: bool,
    role_family: str,
    title: str | None,
    now: datetime,
) -> bool:
    if not raw_url or not company.strip():
        return False
    if not _is_fresh_24h(published_dt, now):
        return False
    norm_url = normalize_url(raw_url)
    if store.was_seen(norm_url):
        return False

    payload = _build_job_payload(
        source_name=source_name,
        source_url=norm_url,
        company=company,
        published_dt=published_dt,
        is_remote=is_remote,
        role_family=role_family,
        title=title,
        collected_at=_iso(now),
    )

    # Validate via Pydantic model if available
    job_cls = getattr(models, "Job", None)
    if job_cls is not None:
        try:
            obj = job_cls.model_validate(payload)
            payload = obj.model_dump(mode="json")
            # Ensure source.* and collectedAt stay preserved even if model omits them
            payload.setdefault("source", {"name": source_name, "url": norm_url})
            payload["source"]["name"] = source_name
            payload["source"]["url"] = norm_url
            payload["collectedAt"] = _iso(now)
        except Exception as exc:
            log.debug("job validation failed for %s: %s", norm_url, exc)
            return False

    inserted = store.insert_record("JOB", norm_url, payload)
    store.mark_seen(norm_url)
    return bool(inserted)


async def collect_jobs(
    store: Store,
    fetcher: AsyncFetcher,
    chain: Any = None,
    now: datetime | None = None,
) -> int:
    """Collect 24h-fresh AI jobs across Greenhouse, Ashby, Lever, RemoteOK, and WeWorkRemotely."""
    run_now = _utc_now(now)
    added = 0
    stats: dict[str, int] = {
        "greenhouse": 0,
        "ashby": 0,
        "lever": 0,
        "remoteok": 0,
        "weworkremotely": 0,
        "errors": 0,
    }

    # 1. Greenhouse boards (use first_published, never updated_at)
    for slug in GREENHOUSE_SLUGS:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        try:
            data = await fetcher.get_json(url)
            jobs_list = data.get("jobs", []) if isinstance(data, dict) else []
            for j in jobs_list:
                title = j.get("title")
                dept = ", ".join(d.get("name", "") for d in (j.get("departments") or []) if isinstance(d, dict))
                if not _is_ai_relevant(title, dept, slug):
                    continue
                pub_dt = _parse_dt(j.get("first_published"))
                loc_name = (j.get("location") or {}).get("name") if isinstance(j.get("location"), dict) else str(j.get("location") or "")
                company = j.get("company_name") or slug
                job_url = j.get("absolute_url")
                if pub_dt and job_url and _try_insert_job(
                    store,
                    source_name="Greenhouse",
                    raw_url=job_url,
                    company=company,
                    published_dt=pub_dt,
                    is_remote=infer_is_remote(None, loc_name, title),
                    role_family=classify_role_family(title, dept),
                    title=title,
                    now=run_now,
                ):
                    added += 1
                    stats["greenhouse"] += 1
        except Exception as exc:
            stats["errors"] += 1
            log.warning("greenhouse slug=%s failed: %s", slug, exc)

    # 2. Ashby boards (use publishedAt + isRemote; full JSON without truncation)
    for slug in ASHBY_SLUGS:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        try:
            data = await fetcher.get_json(url)
            postings = data.get("jobs", []) if isinstance(data, dict) else []
            for j in postings:
                title = j.get("title")
                dept = j.get("department") or j.get("team")
                desc_plain = j.get("descriptionPlain")
                if not _is_ai_relevant(title, dept, slug, desc_plain):
                    continue
                pub_dt = _parse_dt(j.get("publishedAt"))
                loc_name = j.get("location") or ""
                wp_type = j.get("workplaceType") or ""
                job_url = j.get("jobUrl") or j.get("applyUrl")
                if pub_dt and job_url and _try_insert_job(
                    store,
                    source_name="Ashby",
                    raw_url=job_url,
                    company=slug,
                    published_dt=pub_dt,
                    is_remote=infer_is_remote(j.get("isRemote"), loc_name, wp_type),
                    role_family=classify_role_family(title, dept),
                    title=title,
                    now=run_now,
                ):
                    added += 1
                    stats["ashby"] += 1
        except Exception as exc:
            stats["errors"] += 1
            log.warning("ashby slug=%s failed: %s", slug, exc)

    # 3. Lever postings (createdAt is epoch ms; hostedUrl)
    for slug in LEVER_SLUGS:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        try:
            data = await fetcher.get_json(url)
            postings = data if isinstance(data, list) else []
            for j in postings:
                title = j.get("text")
                cats = j.get("categories") if isinstance(j.get("categories"), dict) else {}
                team = cats.get("team") or cats.get("department")
                desc_plain = j.get("descriptionPlain") or j.get("openingPlain")
                if not _is_ai_relevant(title, team, slug, desc_plain):
                    continue
                pub_dt = _parse_dt(j.get("createdAt"))
                loc_name = cats.get("location") or ""
                wp_type = j.get("workplaceType") or ""
                job_url = j.get("hostedUrl") or j.get("applyUrl")
                if pub_dt and job_url and _try_insert_job(
                    store,
                    source_name="Lever",
                    raw_url=job_url,
                    company=slug,
                    published_dt=pub_dt,
                    is_remote=infer_is_remote(None, loc_name, wp_type, title),
                    role_family=classify_role_family(title, team),
                    title=title,
                    now=run_now,
                ):
                    added += 1
                    stats["lever"] += 1
        except Exception as exc:
            stats["errors"] += 1
            log.warning("lever slug=%s failed: %s", slug, exc)

    # 4. RemoteOK API (skip index 0 legal notice; filter to AI/ML)
    try:
        data = await fetcher.get_json(REMOTEOK_URL)
        items = data[1:] if isinstance(data, list) and len(data) > 1 else []
        for j in items:
            if not isinstance(j, dict):
                continue
            title = j.get("position")
            tags = " ".join(j.get("tags") or []) if isinstance(j.get("tags"), list) else str(j.get("tags") or "")
            desc = j.get("description") or ""
            if not _is_ai_relevant(title, tags, desc):
                continue
            pub_dt = _parse_dt(j.get("date") or j.get("epoch"))
            company = j.get("company") or ""
            loc_name = j.get("location") or ""
            job_url = j.get("url") or j.get("apply_url")
            if pub_dt and job_url and _try_insert_job(
                store,
                source_name="RemoteOK",
                raw_url=job_url,
                company=company,
                published_dt=pub_dt,
                is_remote=infer_is_remote(None, loc_name, tags, title),
                role_family=classify_role_family(title, tags),
                title=title,
                now=run_now,
            ):
                added += 1
                stats["remoteok"] += 1
    except Exception as exc:
        stats["errors"] += 1
        log.warning("remoteok failed: %s", exc)

    # 5. WeWorkRemotely RSS (filter to AI/ML)
    for src_name, feed_url in WWR_FEEDS:
        try:
            raw_xml = await fetcher.get_text(feed_url)
            parsed = feedparser.parse(raw_xml)
            for entry in getattr(parsed, "entries", []):
                raw_title = entry.get("title") or ""
                summary = entry.get("summary") or ""
                if not _is_ai_relevant(raw_title, summary):
                    continue
                pub_dt = _parse_dt(entry.get("published") or entry.get("updated"))
                if pub_dt is None:
                    continue
                # WWR titles often look like "Company: Role Title"
                if ":" in raw_title:
                    company_part, role_part = raw_title.split(":", 1)
                else:
                    company_part, role_part = "", raw_title
                link = entry.get("link") or ""
                full_link = urljoin(feed_url, link) if link else ""
                if company_part.strip() and full_link and _try_insert_job(
                    store,
                    source_name=src_name,
                    raw_url=full_link,
                    company=company_part.strip(),
                    published_dt=pub_dt,
                    is_remote=infer_is_remote(None, raw_title, summary, "remote"),
                    role_family=classify_role_family(role_part, summary),
                    title=role_part.strip(),
                    now=run_now,
                ):
                    added += 1
                    stats["weworkremotely"] += 1
        except Exception as exc:
            stats["errors"] += 1
            log.warning("wwr feed=%s failed: %s", feed_url, exc)

    log.info("jobs done: +%d (%s)", added, stats)
    return added