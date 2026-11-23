#!/usr/bin/env python3
"""Verify JOB / NEWS freshness and field quality straight from the DB. Run: python scripts/check_fresh.py
Shows per-source counts, age of the oldest/newest item (hours, relative to NOW), fill rates, and anything stale."""
import os, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.store import Store
from src.dates import parse_any

now = datetime.now(timezone.utc)
store = Store(os.environ.get("DB", "data/pipeline.db"))
print("NOW (UTC):", now.isoformat(timespec="seconds"))
for rt in ("JOB", "NEWS"):
    rows = store.payloads(rt)
    print(f"\n=== {rt}: {len(rows)} rows")
    by = defaultdict(list)
    for r in rows:
        by[r["source"]["name"]].append(r)
    stale = 0
    for name, items in sorted(by.items()):
        ages = []
        for r in items:
            d = parse_any(r["content"].get("date"), now)
            ages.append((now - d).total_seconds() / 3600 if d else float("nan"))
        stale += sum(1 for a in ages if not (-0.2 <= a <= 24))
        print(f"  {name:28} n={len(items):<4} age_h min={min(ages):5.1f} max={max(ages):5.1f}")
    print(f"  STALE or missing-date rows (>24h old): {stale}")
    if rt == "JOB":
        print("  role_family:", dict(Counter(r["content"].get("role_family") for r in rows)))
        print("  is_remote  :", dict(Counter(r["content"].get("is_remote") for r in rows)))
        print("  companies  :", dict(Counter(r["content"].get("company") for r in rows).most_common(12)))
    else:
        filled = sum(1 for r in rows if r["content"].get("summary"))
        ents = [len(r["content"].get("entities") or []) for r in rows]
        print(f"  summary filled: {filled}/{len(rows)} | avg entities: {sum(ents)/max(1, len(ents)):.1f}")
    urls = [r["source"]["url"] for r in rows]
    print("  duplicate source URLs:", len(urls) - len(set(urls)))
print("\nmapping_log:", dict(Counter((m["entity_type"], m["match_method"]) for m in store.mapping_rows())))
store.close()