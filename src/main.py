"""CLI: python -m src.main <startups|papers|products|llm-pricing> [options]"""
import argparse
import asyncio
import json
import logging
import os

from dotenv import load_dotenv

from .collectors.papers import collect_papers
from .collectors.products import collect_products
from .collectors.startups import collect_startups
from .collectors.pricing_llm import collect_pricing_llm
from .collectors.jobs import collect_jobs
from .collectors.news import collect_news
from .resolver.resolve import resolve_entities
from .export.sheets import export_pipeline_data
from .core.http import AsyncFetcher, BlockedError, FetchError
from .llm.chain import LLMChain, providers_from_env
from .core.store import Store


def make_fetcher(max_retries: int = 5, timeout: float = 30.0, breaker_threshold: int = 5) -> AsyncFetcher:
    return AsyncFetcher(
        max_retries=max_retries, timeout=timeout, breaker_threshold=breaker_threshold,
        user_agent=os.getenv("USER_AGENT", "GraphOneTrialBot/0.1 (+mailto:you@example.com)"),
        domain_limits={"export.arxiv.org": 1, "api.github.com": 10, "datasets-server.huggingface.co": 4,
                       "generativelanguage.googleapis.com": 1, "api.groq.com": 1, "api.deepseek.com": 1},
        # free-tier LLM APIs have tight requests-per-minute limits; pace them per provider
        domain_intervals={"export.arxiv.org": 3.0, "api.github.com": 0.05, "datasets-server.huggingface.co": 0.3,
                          "generativelanguage.googleapis.com": 3.0, "api.groq.com": 2.2, "api.deepseek.com": 0.5},
    )


def make_poster(fetcher: AsyncFetcher):
    """Adapter: LLMChain's injected POST on top of AsyncFetcher (retries, backoff+jitter, breaker)."""
    async def post(url, headers, body):
        try:
            res = await fetcher.request("POST", url, headers=headers, json_body=body,
                                        respect_robots=False, max_retries=2, retry_on_429=False)
        except BlockedError as exc:
            # breaker open after repeated 429s is transient; a real 401/403 means bad key
            return (429, None, str(exc)) if "circuit open" in str(exc) else (403, None, str(exc))
        except FetchError as exc:
            return 599, None, str(exc)
        try:
            data = json.loads(res.text)
        except ValueError:
            data = None
        return res.status, data, res.text
    return post


async def run(args) -> None:
    store = Store(args.db)
    crawl = args.cmd == "products"  # arbitrary third-party sites: fail fast, don't wait on dead ones
    llm = args.cmd == "llm-pricing"  # the chain manages LLM cooldowns itself: no HTTP-level breaker
    if crawl:
        fetcher_cm = make_fetcher(max_retries=2, timeout=15)
    elif llm:
        fetcher_cm = make_fetcher(max_retries=1, timeout=60, breaker_threshold=10 ** 9)
    else:
        fetcher_cm = make_fetcher()
    async with fetcher_cm as fetcher:
        if args.cmd == "startups":
            total = await collect_startups(store, fetcher, limit=args.limit, scope=args.scope)
            print(f"STARTUP rows in DB: {total}")
        elif args.cmd == "products":
            if args.reset:
                store.clear("PRODUCT", ["pricing_evidence", "pricing_pending", "product_done"])
                print("cleared previous PRODUCT rows")
            total = await collect_products(store, fetcher, limit=args.limit)
            print(f"PRODUCT rows in DB: {total}")
        elif args.cmd == "llm-pricing":
            providers = providers_from_env(os.environ)
            if not providers:
                raise SystemExit("No LLM keys found. Set GEMINI_API_KEY and/or GROQ_API_KEY in .env")
            print("LLM tiers:", [p.name for p in providers])
            chain = LLMChain(providers, make_poster(fetcher), cache=store)
            total = await collect_pricing_llm(store, chain, fetcher, limit=args.limit,
                                               reprocess=args.reprocess)
            print(f"PRODUCT rows in DB: {total} | chain stats: {chain.stats}")
        elif args.cmd == "papers":
            total = await collect_papers(store, fetcher, target=args.target,
                                         github_token=os.getenv("GITHUB_TOKEN", ""))
            print(f"RESEARCH_PAPER rows in DB: {total}")
        elif args.cmd == "jobs":
            providers = providers_from_env(os.environ)
            chain = LLMChain(providers, make_poster(fetcher), cache=store) if providers else None
            total = await collect_jobs(store, fetcher, chain=chain)
            print(f"JOB rows in DB: {total}")
        elif args.cmd == "news":
            providers = providers_from_env(os.environ)
            chain = LLMChain(providers, make_poster(fetcher), cache=store) if providers else None
            total = await collect_news(store, fetcher, chain=chain)
            print(f"NEWS rows in DB: {total}")
        elif args.cmd == "resolve":
            if args.demo:
                resolve_entities(store, demo=True)
                return
            res = resolve_entities(store, dry_run=args.dry_run)
            print("=== RESOLVE RESULTS ===")
            print(f"Records evaluated: {res['records_scanned']}")
            print(f"Dry run: {res['dry_run']}")
            print("\nCounts by match method:")
            for m, cnt in sorted(res['method_counts'].items()):
                print(f"  {m}: {cnt}")
            print(f"\nTotal canonical merges: {res['canonical_merges']}")
            print("\nExample mappings:")
            for m, examples in sorted(res['samples'].items()):
                print(f"  [{m}]")
                for raw, canon, score in examples:
                    print(f"    '{raw}' -> '{canon}' (score: {score})")
        elif args.cmd == "export":
            res = export_pipeline_data(store, sheet_id=args.sheet_id, export_dir=args.export_dir)
            print("=== EXPORT RESULTS ===")
            if res["executed_sheets"]:
                print(f"Google Sheet URL: {res['sheet_url']}")
                print("\nGoogle Sheets per-tab row counts:")
                for tab, count in res["sheets_tab_counts"].items():
                    print(f"  {tab}: {count} rows")
            else:
                print("Notice: GOOGLE_SERVICE_ACCOUNT_JSON / SHEET_ID not configured or found.")
                print("Exported full CSV fallback per tab.")

            print(f"\nDropped stale items (>24h UTC freshness window):")
            print(f"  JOB: {res['dropped_items']['JOB']} dropped")
            print(f"  NEWS: {res['dropped_items']['NEWS']} dropped")

            print("\nVerified CSV export per tab:")
            for tab, info in res["csv_tabs"].items():
                print(f"  {tab}: {info['row_count']} rows -> {info['path']}")
    store.close()


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["startups", "papers", "products", "llm-pricing", "jobs", "news", "resolve", "export"])
    ap.add_argument("--db", default="data/pipeline.db")
    ap.add_argument("--target", type=int, default=1200)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--scope", choices=["ai", "ai+b2b", "all"], default="ai", help="startups: which YC companies to include")
    ap.add_argument("--reprocess", action="store_true", help="llm-pricing: re-judge pages already answered (uses the cache)")
    ap.add_argument("--reset", action="store_true", help="products: clear previous product rows first")
    ap.add_argument("--dry-run", action="store_true", help="resolve: dry run without updating database")
    ap.add_argument("--demo", action="store_true", help="resolve: print resolver demo on messy sample strings")
    ap.add_argument("--sheet-id", default=None, help="Google Sheet ID to export to")
    ap.add_argument("--export-dir", default="data/export", help="directory for CSV fallback export")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
