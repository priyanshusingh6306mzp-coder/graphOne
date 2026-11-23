"""Jobs collector: Greenhouse, Ashby, Lever, RemoteOK, and WeWorkRemotely.

Features:
- 5 job board families:
  * Greenhouse (anthropic, scaleai, databricks) via first_published, absolute_url, location, company_name
  * Ashby (openai, cohere, perplexity) via publishedAt, isRemote, workplaceType, department/team, jobUrl, descriptionPlain
  * Lever (palantir, etc.) via createdAt (epoch ms), hostedUrl, categories, workplaceType
  * RemoteOK (/api, skipping index 0 legal notice) filtered to AI/ML roles
  * WeWorkRemotely RSS filtered to AI/ML roles
- Strict 24h UTC freshness via src.dates.is_fresh(..., now, 24)
- Deterministic derivation of is_remote and role_family (Engineering, Research, Product, Design, Sales, Operations, Other)
- Fallback to LLM chain for ambiguous role_family with strict grounding verification
- Validated via src.models.Job and persisted via store.insert_record("JOB", ...)
"""
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core.http import AsyncFetcher, BlockedError, FetchError
from ..core.store import Store
from ..dates import is_fresh, parse_any
from ..llm.chain import LLMChain
from ..llm.verify import is_grounded, quote_in_text
from ..models import Job, JobContent, Source

log = logging.getLogger("jobs")

GREENHOUSE_SLUGS = ["anthropic", "scaleai", "databricks"]
ASHBY_SLUGS = ["openai", "cohere", "perplexity"]
LEVER_SLUGS = ["palantir"]
REMOTEOK_URL = "https://remoteok.com/api"
WWR_RSS_URL = "https://weworkremotely.com/remote-jobs.rss"

ROLE_FAMILIES = {"Engineering", "Research", "Product", "Design", "Sales", "Operations", "Other"}

AI_ROLE_RE = re.compile(
    r"\b(ai|ml|llm|llms|nlp|gpt|rag|genai|deep\s+learning|machine\s+learning|computer\s+vision|"
    r"data\s+scientist|data\s+science|artificial\s+intelligence|prompt\s+engineer|mlops|"
    r"applied\s+scientist|research\s+scientist|agentic)\b",
    re.I,
)

COMPANY_ALIASES = {
    "anthropic": "Anthropic",
    "scaleai": "Scale AI",
    "scale ai": "Scale AI",
    "databricks": "Databricks",
    "openai": "OpenAI",
    "open ai": "OpenAI",
    "cohere": "Cohere",
    "perplexity": "Perplexity",
    "perplexity ai": "Perplexity",
    "palantir": "Palantir",
    "palantir technologies": "Palantir",
    "google deepmind": "Google DeepMind",
    "deepmind": "Google DeepMind",
    "hugging face": "Hugging Face",
    "huggingface": "Hugging Face",
    "mistral": "Mistral AI",
    "mistral ai": "Mistral AI",
    "stability": "Stability AI",
    "stability ai": "Stability AI",
    "midjourney": "Midjourney",
    "runway": "Runway",
    "runwayml": "Runway",
    "anyscale": "Anyscale",
    "replicate": "Replicate",
    "together": "Together AI",
    "together ai": "Together AI",
}


def canonical_company(raw: str) -> Tuple[str, str, float]:
    """Normalize and resolve company name. Returns (canonical_name, match_method, score)."""
    raw_clean = (raw or "").strip()
    if not raw_clean:
        return "Unknown", "unresolved", 0.0

    lower = raw_clean.lower()
    if lower in COMPANY_ALIASES:
        return COMPANY_ALIASES[lower], "exact" if lower == raw_clean.lower() else "alias", 1.0

    # Strip corporate legal suffixes (Inc., LLC, Ltd., Corp., etc.)
    stripped = re.sub(
        r"(?i)[,\s]+(inc|incorporated|llc|ltd|limited|corp|corporation|gmbh|sa|pvt|co)\.?\s*$",
        "",
        raw_clean,
    ).strip(" ,.-")

    lower_stripped = stripped.lower()
    if lower_stripped in COMPANY_ALIASES:
        return COMPANY_ALIASES[lower_stripped], "alias", 0.95

    return stripped if stripped else raw_clean, "unresolved", 0.8


def is_ai_job(title: str, tags: Optional[List[str]] = None, extra_text: str = "") -> bool:
    """Return True if title, tags, or extra_text explicitly indicate an AI/ML role."""
    combined = f"{title} {' '.join(tags or [])} {extra_text[:200]}"
    return bool(AI_ROLE_RE.search(combined))


def classify_role_family_deterministic(title: str, department: str = "", team: str = "") -> Optional[str]:
    """Deterministically categorize a job into one of the 7 role families."""
    combined = f"{title} {department} {team}".lower()

    # 1. Research
    if re.search(
        r"\b(research\s+(scientist|engineer|director|fellow|lead|specialist|intern)|applied\s+scientist|"
        r"staff\s+scientist|principal\s+scientist|ai\s+researcher|ml\s+researcher|postdoc|postdoctoral|fellow|fellowship|residency)\b",
        combined,
    ):
        return "Research"
    if re.search(r"\b(scientist|researcher)\b", combined) and not re.search(
        r"\b(data\s+scientist|user\s+research|market\s+research)\b", combined
    ):
        return "Research"

    # 2. Design
    if re.search(r"\b(design|designer|ui/ux|ux|ui|creative\s+director|art\s+director|visual\s+designer)\b", combined):
        if not re.search(r"\b(system\s+design|hardware\s+design|chip\s+design|asic\s+design)\b", combined):
            return "Design"

    # 3. Product
    if re.search(
        r"\b(product\s+manager|product\s+lead|head\s+of\s+product|director\s+of\s+product|vp\s+of\s+product|"
        r"product\s+owner|technical\s+program\s+manager|tpm|program\s+manager)\b",
        combined,
    ):
        return "Product"

    # 4. Engineering
    if re.search(
        r"\b(engineer|engineering|developer|architect|frontend|front-end|backend|back-end|fullstack|full-stack|"
        r"devops|sre|infrastructure|infra|platform|firmware|qa|quality\s+assurance|tester|data\s+engineer|"
        r"data\s+scientist|machine\s+learning\s+engineer|mle|applied\s+ai\s+engineer|solutions\s+architect|"
        r"cloud\s+architect|systems\s+engineer|software|member\s+of\s+technical\s+staff|mts)\b",
        combined,
    ):
        return "Engineering"

    # 5. Operations / People / Finance / Legal (check before sales so "Commercial Counsel" is Operations)
    if re.search(
        r"\b(operations|ops|finance|accounting|accountant|payroll|tax|legal|counsel|attorney|compliance|"
        r"people|human\s+resources|hr\b|recruiter|recruiting|talent|office\s+manager|workplace|"
        r"executive\s+assistant|chief\s+of\s+staff)\b",
        combined,
    ):
        return "Operations"

    # 6. Sales / GTM / Marketing
    if re.search(
        r"\b(sales|account\s+executive|bdr|sdr|account\s+manager|business\s+development|bizdev|revops|"
        r"revenue|commercial|partnerships|gtm|go-to-market|marketing|growth|customer\s+success|csm|"
        r"solutions\s+engineer|sales\s+engineer)\b",
        combined,
    ):
        return "Sales"

    return None


async def resolve_role_family(
    title: str,
    department: str = "",
    team: str = "",
    description: str = "",
    chain: Optional[LLMChain] = None,
) -> str:
    """Derive role_family: deterministic first; chain fallback if ambiguous with grounding check."""
    det = classify_role_family_deterministic(title, department, team)
    if det is not None:
        return det

    if chain is not None and chain.alive():
        system = (
            "You are a job classifier. Classify the job into EXACTLY ONE role_family: "
            "Engineering, Research, Product, Design, Sales, Operations, Other.\n"
            "Return a JSON object: {\"role_family\": \"...\", \"quote\": \"...\"}\n"
            "The quote MUST be an exact verbatim substring from the input text justifying the family."
        )
        context = f"Title: {title}\nDepartment: {department}\nTeam: {team}\nDescription: {description[:1000]}"

        def make_prompt(txt: str) -> str:
            return f"Classify this job:\n{txt}"

        def validate_resp(obj: Any) -> Dict[str, Any]:
            fam = obj.get("role_family")
            if fam not in ROLE_FAMILIES:
                raise ValueError(f"invalid role_family: {fam}")
            return obj

        try:
            res, _ = await chain.extract(system, context, make_prompt, validate_resp)
            fam = res.get("role_family")
            quote = res.get("quote", "")
            if fam and is_grounded(quote, context):
                return fam
        except Exception as exc:
            log.debug("LLM role classification failed for %s: %s", title, exc)

    return "Other"


# ---------------------------------------------------------------------------
# Family 1: Greenhouse
# ---------------------------------------------------------------------------
async def collect_greenhouse(
    fetcher: AsyncFetcher,
    slugs: List[str],
    now: datetime,
    chain: Optional[LLMChain] = None,
) -> List[Job]:
    out: List[Job] = []
    for slug in slugs:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        try:
            data = await fetcher.get_json(url, respect_robots=False)
        except (FetchError, BlockedError) as exc:
            log.warning("Greenhouse fetch failed for %s: %s", slug, exc)
            continue

        raw_company = slug.title()
        for item in (data or {}).get("jobs", []):
            # Greenhouse rule: use first_published (never updated_at!)
            first_pub = item.get("first_published")
            if not first_pub:
                continue

            pub_dt = parse_any(first_pub, now)
            if not is_fresh(pub_dt, now, 24):
                continue

            company_name = item.get("company_name") or raw_company
            title = (item.get("title") or "").strip()
            job_url = item.get("absolute_url")
            if not job_url:
                continue

            location_raw = item.get("location")
            loc_name = ""
            if isinstance(location_raw, dict):
                loc_name = location_raw.get("name") or ""
            elif isinstance(location_raw, str):
                loc_name = location_raw

            is_remote = "remote" in loc_name.lower() or "remote" in title.lower()
            role_family = await resolve_role_family(title, "", "", "", chain)

            out.append(
                Job(
                    source=Source(name=f"Greenhouse ({company_name})", url=job_url),
                    collectedAt=now,
                    content=JobContent(
                        company=company_name,
                        title=title,
                        date=pub_dt,
                        is_remote=is_remote,
                        role_family=role_family,
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Family 2: Ashby
# ---------------------------------------------------------------------------
async def collect_ashby(
    fetcher: AsyncFetcher,
    slugs: List[str],
    now: datetime,
    chain: Optional[LLMChain] = None,
) -> List[Job]:
    out: List[Job] = []
    for slug in slugs:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        try:
            data = await fetcher.get_json(url, respect_robots=False)
        except (FetchError, BlockedError) as exc:
            log.warning("Ashby fetch failed for %s: %s", slug, exc)
            continue

        for item in (data or {}).get("jobs", []):
            pub_at = item.get("publishedAt")
            if not pub_at:
                continue

            pub_dt = parse_any(pub_at, now)
            if not is_fresh(pub_dt, now, 24):
                continue

            title = (item.get("title") or "").strip()
            job_url = item.get("jobUrl")
            if not job_url:
                continue

            is_remote = bool(item.get("isRemote")) or str(item.get("workplaceType")).lower() == "remote" or "remote" in title.lower()
            dept = item.get("department") or ""
            team = item.get("team") or ""
            desc = item.get("descriptionPlain") or ""  # never truncate large payloads

            role_family = await resolve_role_family(title, dept, team, desc, chain)

            out.append(
                Job(
                    source=Source(name=f"Ashby ({slug.title()})", url=job_url),
                    collectedAt=now,
                    content=JobContent(
                        company=slug.title(),
                        title=title,
                        date=pub_dt,
                        is_remote=is_remote,
                        role_family=role_family,
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Family 3: Lever
# ---------------------------------------------------------------------------
async def collect_lever(
    fetcher: AsyncFetcher,
    slugs: List[str],
    now: datetime,
    chain: Optional[LLMChain] = None,
) -> List[Job]:
    out: List[Job] = []
    for slug in slugs:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        try:
            data = await fetcher.get_json(url, respect_robots=False)
        except (FetchError, BlockedError) as exc:
            log.warning("Lever fetch failed for %s: %s", slug, exc)
            continue

        if not isinstance(data, list):
            continue

        for item in data:
            created_at = item.get("createdAt")  # epoch ms
            if not created_at:
                continue

            pub_dt = parse_any(created_at, now)
            if not is_fresh(pub_dt, now, 24):
                continue

            title = (item.get("text") or "").strip()
            job_url = item.get("hostedUrl")
            if not job_url:
                continue

            categories = item.get("categories") or {}
            workplace_type = str(item.get("workplaceType") or "").lower()
            location = str(categories.get("location") or "").lower()
            is_remote = workplace_type == "remote" or "remote" in location or "remote" in title.lower()

            team = str(categories.get("team") or "")
            dept = str(categories.get("department") or "")
            role_family = await resolve_role_family(title, dept, team, "", chain)

            out.append(
                Job(
                    source=Source(name=f"Lever ({slug.title()})", url=job_url),
                    collectedAt=now,
                    content=JobContent(
                        company=slug.title(),
                        title=title,
                        date=pub_dt,
                        is_remote=is_remote,
                        role_family=role_family,
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Family 4: RemoteOK
# ---------------------------------------------------------------------------
async def collect_remoteok(
    fetcher: AsyncFetcher,
    now: datetime,
    chain: Optional[LLMChain] = None,
) -> List[Job]:
    out: List[Job] = []
    try:
        data = await fetcher.get_json(REMOTEOK_URL, respect_robots=False)
    except (FetchError, BlockedError) as exc:
        log.warning("RemoteOK fetch failed: %s", exc)
        return out

    if not isinstance(data, list) or len(data) <= 1:
        return out

    # Skip index 0 (legal notice)
    for item in data[1:]:
        if not isinstance(item, dict) or not item.get("id"):
            continue

        date_val = item.get("date") or item.get("epoch")
        if not date_val:
            continue

        pub_dt = parse_any(date_val, now)
        if not is_fresh(pub_dt, now, 24):
            continue

        title = (item.get("position") or "").strip()
        tags = item.get("tags") or []
        company = (item.get("company") or "RemoteOK").strip()
        desc = (item.get("description") or "")[:500]

        # Filter strictly to AI/ML roles
        if not is_ai_job(title, tags, desc):
            continue

        job_url = item.get("url") or item.get("apply_url")
        if not job_url:
            continue

        role_family = await resolve_role_family(title, " ".join(tags), "", desc, chain)

        out.append(
            Job(
                source=Source(name="RemoteOK", url=job_url),
                collectedAt=now,
                content=JobContent(
                    company=company,
                    title=title,
                    date=pub_dt,
                    is_remote=True,  # RemoteOK is remote by definition
                    role_family=role_family,
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Family 5: WeWorkRemotely RSS
# ---------------------------------------------------------------------------
async def collect_weworkremotely(
    fetcher: AsyncFetcher,
    now: datetime,
    chain: Optional[LLMChain] = None,
) -> List[Job]:
    out: List[Job] = []
    try:
        res = await fetcher.get(WWR_RSS_URL, respect_robots=False)
        if not res.ok:
            log.warning("WeWorkRemotely fetch returned %s", res.status)
            return out
        root = ET.fromstring(res.text)
    except (FetchError, BlockedError, ET.ParseError) as exc:
        log.warning("WeWorkRemotely fetch failed: %s", exc)
        return out

    for item in root.findall(".//item"):
        pub_date_val = item.findtext("pubDate")
        if not pub_date_val:
            continue

        pub_dt = parse_any(pub_date_val, now)
        if not is_fresh(pub_dt, now, 24):
            continue

        full_title = (item.findtext("title") or "").strip()
        # In WWR RSS, title is formatted as 'Company Name: Job Title'
        if ": " in full_title:
            raw_company, title = full_title.split(": ", 1)
        else:
            raw_company, title = "WeWorkRemotely", full_title

        categories = [c.text or "" for c in item.findall("category")]
        desc = (item.findtext("description") or "")[:500]

        # Filter strictly to AI/ML roles
        if not is_ai_job(title, categories, desc):
            continue

        job_url = item.findtext("link")
        if not job_url:
            continue

        role_family = await resolve_role_family(title, " ".join(categories), "", desc, chain)

        out.append(
            Job(
                source=Source(name="WeWorkRemotely", url=job_url),
                collectedAt=now,
                content=JobContent(
                    company=raw_company.strip(),
                    title=title.strip(),
                    date=pub_dt,
                    is_remote=True,  # WeWorkRemotely is remote by definition
                    role_family=role_family,
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Main Orchestration: collect_jobs
# ---------------------------------------------------------------------------
async def collect_jobs(
    store: Store,
    fetcher: AsyncFetcher,
    chain: Optional[LLMChain] = None,
    now: Optional[datetime] = None,
) -> int:
    """Collect 24h fresh jobs across 5 board families and persist them in Store."""
    current_now = now or datetime.now(timezone.utc)
    log.info("Starting jobs collection (reference time UTC: %s)", current_now.isoformat())

    results = await asyncio.gather(
        collect_greenhouse(fetcher, GREENHOUSE_SLUGS, current_now, chain),
        collect_ashby(fetcher, ASHBY_SLUGS, current_now, chain),
        collect_lever(fetcher, LEVER_SLUGS, current_now, chain),
        collect_remoteok(fetcher, current_now, chain),
        collect_weworkremotely(fetcher, current_now, chain),
        return_exceptions=True,
    )

    all_jobs: List[Job] = []
    family_names = ["Greenhouse", "Ashby", "Lever", "RemoteOK", "WeWorkRemotely"]
    for name, r in zip(family_names, results):
        if isinstance(r, Exception):
            log.error("Job family %s failed with: %s", name, r)
        elif isinstance(r, list):
            log.info("Job family %s produced %d fresh jobs", name, len(r))
            all_jobs.extend(r)

    added = 0
    for job in all_jobs:
        # Canonicalize company name and log mapping
        raw_company = job.content.company
        canonical, method, score = canonical_company(raw_company)
        job.content.company = canonical

        store.log_mapping(
            entity_type="JOB_COMPANY",
            raw_name=raw_company,
            canonical_name=canonical,
            match_method=method,
            score=score,
            source_url=job.source.url,
        )

        inserted = store.insert_record("JOB", job.source.url, job.model_dump(mode="json"))
        if inserted:
            added += 1

    total_in_db = store.count("JOB")
    log.info("Jobs collection finished: +%d newly inserted (total %d in DB)", added, total_in_db)
    return total_in_db
