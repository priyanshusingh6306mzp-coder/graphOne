#!/usr/bin/env python3
"""Spot-check the DB: counts, coverage stats, and N random rows per type to verify against source URLs.
Run: python scripts/audit.py [--n 5] [--type PRODUCT]"""
import argparse, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.store import Store
from src.export.flatten import flatten

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="data/pipeline.db")
ap.add_argument("--n", type=int, default=5)
ap.add_argument("--type", default=None)
a = ap.parse_args()
store = Store(a.db)
evidence = {e["startup"]: e for e in store.aux_items("pricing_evidence")}
for rt in ["STARTUP", "PRODUCT", "RESEARCH_PAPER", "JOB", "NEWS"]:
    if a.type and a.type != rt:
        continue
    rows = store.payloads(rt)
    print(f"\n=== {rt}: {len(rows)} rows")
    if not rows:
        continue
    flat = [flatten(r) for r in rows]
    for col in sorted(flat[0].keys()):
        filled = sum(1 for f in flat if f.get(col) not in (None, ""))
        print(f"  {col:32} filled {filled}/{len(flat)}")
    if rt == "PRODUCT":
        from collections import Counter
        print("  pricingModel:", dict(Counter(f.get("content.pricingModel") for f in flat)))
    for f in random.sample(flat, min(a.n, len(flat))):
        if rt == "PRODUCT":
            ev = evidence.get(f.get("content.startupName"), {})
            print("  --", f.get("content.startupName"), "|", f.get("source.url"), "|", f.get("content.pricingModel"))
            for snip in ev.get("evidence", []):
                print("       evidence:", snip.replace("\n", " ")[:140])
            continue
        print("  --", {k: v for k, v in f.items() if k in ("source.url", "content.entityName", "content.startupName",
              "content.pricingModel", "content.title", "content.github_url", "content.github_stars",
              "content.data.employeeCount", "content.company", "content.date")})
store.close()
