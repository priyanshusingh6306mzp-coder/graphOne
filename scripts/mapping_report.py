#!/usr/bin/env python3
"""Audit entity resolution. Run: python scripts/mapping_report.py
Shows counts by method, samples of every non-identity mapping type, and SUSPICIOUS merges:
distinct startups (different source URLs) collapsed into one canonical name."""
import os, random, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.store import Store

random.seed(7)
store = Store(os.environ.get("DB", "data/pipeline.db"))
rows = store.mapping_rows()
print(f"mapping_log rows: {len(rows)}")
print("by (entity_type, method):")
for k, v in sorted(Counter((r["entity_type"], r["match_method"]) for r in rows).items()):
    print(f"  {k[0]:12} {k[1]:11} {v}")
non_id = [r for r in rows if r["raw_name"] != r["canonical_name"]]
print(f"\nnon-identity mappings: {len(non_id)} of {len(rows)}")
for method in ("alias", "fuzzy", "cluster", "exact"):
    ms = [r for r in non_id if r["match_method"] == method]
    if not ms:
        continue
    print(f"\n-- {method}: {len(ms)} non-identity (showing {'all' if method == 'fuzzy' else 12})")
    for r in (sorted(ms, key=lambda r: -(r["score"] or 0))[:40] if method == "fuzzy" else random.sample(ms, min(12, len(ms)))):
        print(f"   [{r['entity_type']}] {r['raw_name']!r} -> {r['canonical_name']!r}  ({r['score']})")

by_canon = defaultdict(list)
for r in rows:
    if r["entity_type"] == "STARTUP":
        by_canon[r["canonical_name"]].append(r)
sus, same_name = [], []
for canon, rs in by_canon.items():
    urls = {r["source.url"] for r in rs}
    if len(urls) > 1:
        (sus if any(r["raw_name"] != canon for r in rs) else same_name).append((canon, rs))
print(f"\n!! SUSPICIOUS: {len(sus)} canonical names where DIFFERENT startups were merged under one name")
for canon, rs in sus[:25]:
    print(f"   {canon!r}: " + " | ".join(f"{r['raw_name']!r}<{r['source.url'].rsplit('/', 1)[-1]}>" for r in rs[:4]))
print(f"identical-name duplicates (different startups that already share a name): {len(same_name)}")
for canon, rs in same_name[:8]:
    print(f"   {canon!r} x{len(rs)}")
for rt, field in (("STARTUP", "entityName"), ("PRODUCT", "startupName")):
    names = Counter(p["content"][field] for p in store.payloads(rt))
    d = {n: c for n, c in names.items() if c > 1}
    print(f"\n{rt}: {len(names)} distinct {field} values; {len(d)} appear more than once, e.g. {list(d.items())[:6]}")
store.close()