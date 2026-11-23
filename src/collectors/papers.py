"""Research papers: PwC archive (paper->repo links) + arXiv metadata + live GitHub stars.

No LLM involved: every field comes from a structured source, so nothing can be hallucinated.
  1. Sample paper->GitHub links from the Hugging Face pwc-archive dataset (strided across the dataset).
  2. Fetch authoritative metadata from the arXiv API (title, authors, date, categories).
  3. Keep AI/ML categories; fetch current stars from the GitHub API; drop dead repos.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..core.http import AsyncFetcher, BlockedError, FetchError
from ..core.store import Store
from ..models import PaperContent, ResearchPaper, Source
from . import parsing as P

log = logging.getLogger("papers")

HF_ROWS = "https://datasets-server.huggingface.co/rows"
PWC_DATASET = "pwc-archive/links-between-paper-and-code"
PWC_TOTAL_ROWS = 300_161
ARXIV_API = "https://export.arxiv.org/api/query"


def _chunks(items: List[Any], n: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


async def fetch_pwc_candidates(fetcher: AsyncFetcher, need: int, max_requests: int = 120,
                               length: int = 100) -> List[Dict[str, Any]]:
    step = max(length, PWC_TOTAL_ROWS // max_requests)
    offsets = list(range(0, PWC_TOTAL_ROWS - length, step))[:max_requests]
    cands: Dict[str, Dict[str, Any]] = {}

    async def one(offset: int) -> List[Dict[str, Any]]:
        params = {"dataset": PWC_DATASET, "config": "default", "split": "train",
                  "offset": offset, "length": length}
        try:
            return P.parse_pwc_rows(await fetcher.get_json(HF_ROWS, params=params, respect_robots=False))
        except FetchError as exc:
            log.warning("pwc rows offset=%s failed: %s", offset, exc)
            return []

    for batch in _chunks(offsets, 8):
        for rows in await asyncio.gather(*(one(o) for o in batch)):
            for r in rows:
                cur = cands.get(r["arxiv_id"])
                if cur is None or (r["is_official"] and not cur["is_official"]):
                    cands[r["arxiv_id"]] = r
        log.info("pwc candidates so far: %d", len(cands))
        if len(cands) >= need:
            break
    # official implementations first
    return sorted(cands.values(), key=lambda r: not r["is_official"])


async def fetch_arxiv_meta(fetcher: AsyncFetcher, ids: List[str], batch: int = 100) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunks(ids, batch):
        try:
            res = await fetcher.get(ARXIV_API, params={"id_list": ",".join(chunk), "max_results": len(chunk)},
                                    respect_robots=False)
        except FetchError as exc:
            log.warning("arxiv batch failed: %s", exc)
            continue
        if not res.ok:
            log.warning("arxiv HTTP %s", res.status)
            continue
        for m in P.parse_arxiv_atom(res.text):
            meta[m["arxiv_id"]] = m
    return meta


async def fetch_repo(fetcher: AsyncFetcher, full_name: str, token: str = "") -> Optional[Tuple[str, int]]:
    """Return (html_url, stargazers_count) from the GitHub API, or None if the repo is gone."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        res = await fetcher.get(f"https://api.github.com/repos/{full_name}", headers=headers,
                                respect_robots=False)
    except BlockedError as exc:  # 403 = rate limit exhausted or blocked; stop hammering
        log.warning("github blocked: %s", exc)
        return None
    except FetchError as exc:
        log.warning("github %s failed: %s", full_name, exc)
        return None
    if res.status != 200:
        return None
    data = json.loads(res.text)
    return data["html_url"], int(data["stargazers_count"])


def build_record(meta: Dict[str, Any], repo: Tuple[str, int]) -> ResearchPaper:
    abs_url = f"https://arxiv.org/abs/{meta['arxiv_id']}"
    return ResearchPaper(
        source=Source(name="arXiv", url=abs_url),
        content=PaperContent(
            title=meta["title"],
            authors=meta["authors"],
            paper_url=abs_url,
            github_url=repo[0],
            github_stars=repo[1],
            published_date=datetime.fromisoformat(meta["published"].replace("Z", "+00:00")),
        ),
    )


async def collect_papers(store: Store, fetcher: AsyncFetcher, target: int = 1200,
                         github_token: str = "", max_pwc_requests: int = 120) -> int:
    cands = await fetch_pwc_candidates(fetcher, need=int(target * 2.5), max_requests=max_pwc_requests)
    log.info("%d paper->repo candidates", len(cands))
    sem = asyncio.Semaphore(10)

    async def stars(full_name: str):
        async with sem:
            return await fetch_repo(fetcher, full_name, github_token)

    for chunk in _chunks(cands, 300):
        if store.count("RESEARCH_PAPER") >= target:
            break
        todo = [c for c in chunk
                if not store.has_record("RESEARCH_PAPER", f"https://arxiv.org/abs/{c['arxiv_id']}")]
        meta = await fetch_arxiv_meta(fetcher, [c["arxiv_id"] for c in todo])
        keep = [(c, meta[c["arxiv_id"]]) for c in todo
                if c["arxiv_id"] in meta and P.is_ai_category(meta[c["arxiv_id"]]["primary_category"])]
        infos = await asyncio.gather(*(stars(c["repo"]) for c, _ in keep))
        added = 0
        for (c, m), info in zip(keep, infos):
            if not info:
                continue
            rec = build_record(m, info)
            if store.insert_record("RESEARCH_PAPER", rec.source.url, rec.model_dump(mode="json")):
                added += 1
        log.info("chunk: %d meta, %d AI, +%d papers (total %d)", len(meta), len(keep), added,
                 store.count("RESEARCH_PAPER"))
    return store.count("RESEARCH_PAPER")
