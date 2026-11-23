#!/usr/bin/env python3
"""Print pipeline status: row counts, products by label/source, LLM-stage progress. Run: python scripts/status.py"""
import os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.store import Store

store = Store(os.environ.get("DB", "data/pipeline.db"))
print("== rows")
for rt in ["STARTUP", "PRODUCT", "RESEARCH_PAPER", "JOB", "NEWS"]:
    print(f"  {rt:15} {store.count(rt)}")
print("== products by pricingModel:", dict(Counter(p["content"]["pricingModel"] for p in store.payloads("PRODUCT"))))
ev = store.aux_items("pricing_evidence")
print("== products added by the LLM stage:", sum(1 for e in ev if e.get("via")), "| by rules:", sum(1 for e in ev if not e.get("via")))
print("== LLM stage: pending pages", len(store.aux_items("pricing_pending")),
      "| answered", len(store.aux_items("pricing_llm_done")),
      "| startups known", len(store.aux_items("startup_meta")), "| startups crawled", len(store.aux_items("product_done")))
print("== mapping log rows:", len(store.mapping_rows()))
store.close()
