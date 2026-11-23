import json
import os
import sqlite3

conn = sqlite3.connect("data/pipeline.db")

print("=== 1. RECORD COUNTS ACROSS ALL TYPES ===")
rows = conn.execute("SELECT record_type, count(*) FROM records GROUP BY record_type ORDER BY record_type").fetchall()
for rt, cnt in rows:
    print(f"{rt}: {cnt}")
total = sum(c for _, c in rows)
print(f"Total records: {total}")

print("\n=== 1b. PER-SOURCE COUNTS FOR JOB ===")
job_sources = conn.execute("SELECT json_extract(payload, '$.source.name'), count(*) FROM records WHERE record_type='JOB' GROUP BY 1 ORDER BY 2 DESC").fetchall()
for src, cnt in job_sources:
    print(f"  {src}: {cnt}")

print("\n=== 1c. PER-SOURCE COUNTS FOR NEWS ===")
news_sources = conn.execute("SELECT json_extract(payload, '$.source.name'), count(*) FROM records WHERE record_type='NEWS' GROUP BY 1 ORDER BY 2 DESC").fetchall()
for src, cnt in news_sources:
    print(f"  {src}: {cnt}")

print("\n=== 2. LLM CALLS AND CACHE ===")
cache_rows = conn.execute("SELECT model, count(*) FROM llm_cache GROUP BY model ORDER BY 2 DESC").fetchall()
for m, cnt in cache_rows:
    print(f"  Model '{m}': {cnt} entries")
print(f"Total llm_cache entries: {sum(c for _, c in cache_rows)}")

print("\n=== 3. PRODUCT BREAKDOWN (RULES VS LLM) ===")
aux_kinds = conn.execute("SELECT kind, count(*) FROM aux GROUP BY kind").fetchall()
print(f"Aux table kinds: {aux_kinds}")

# Inspect pricing_evidence and pricing_pending
evidence_rows = conn.execute("SELECT payload FROM aux WHERE kind='pricing_evidence'").fetchall()
llm_evidence_count = 0
rules_evidence_count = 0
for r in evidence_rows:
    d = json.loads(r[0])
    if d.get("method") == "llm" or "model" in d or d.get("by") == "llm":
        llm_evidence_count += 1
    else:
        rules_evidence_count += 1
print(f"pricing_evidence breakdown: {rules_evidence_count} by rules, {llm_evidence_count} by LLM (total {len(evidence_rows)})")

pricing_models = conn.execute("SELECT json_extract(payload, '$.content.pricingModel'), count(*) FROM records WHERE record_type='PRODUCT' GROUP BY 1 ORDER BY 2 DESC").fetchall()
print("Product pricingModel distribution:")
for pm, cnt in pricing_models:
    print(f"  {pm}: {cnt}")

print("\n=== 4. JOB ROLE_FAMILY DISTRIBUTION ===")
role_dist = conn.execute("SELECT json_extract(payload, '$.content.role_family'), count(*) FROM records WHERE record_type='JOB' GROUP BY 1 ORDER BY 2 DESC").fetchall()
for rf, cnt in role_dist:
    print(f"  {rf}: {cnt}")

all_cache = conn.execute("SELECT content_hash, model, response FROM llm_cache").fetchall()
job_llm_calls = [r for r in all_cache if "role_family" in r[2]]
news_llm_calls = [r for r in all_cache if "entities" in r[2] and "summary" in r[2]]
pricing_llm_calls = [r for r in all_cache if "pricing" in r[2] or "evidence" in r[2] or ("FREEMIUM" in r[2] or "PAID" in r[2] or "ENTERPRISE" in r[2] or "FREE" in r[2])]
print(f"LLM cache breakdown: {len(pricing_llm_calls)} product/pricing, {len(job_llm_calls)} jobs, {len(news_llm_calls)} news")

print("\n=== 5. CURRENT CONFIGURED JOB BOARD SLUGS IN SRC/COLLECTORS/JOBS.PY ===")
import src.collectors.jobs as jobs_mod
print("Greenhouse slugs:", jobs_mod.GREENHOUSE_SLUGS)
print("Ashby slugs:", jobs_mod.ASHBY_SLUGS)
print("Lever slugs:", jobs_mod.LEVER_SLUGS)
print("RemoteOK URL:", jobs_mod.REMOTEOK_URL)
print("WeWorkRemotely RSS URL:", jobs_mod.WWR_RSS_URL)

print("\n=== DETAILED PRODUCT EXPLANATION ===")
product_times = conn.execute("SELECT substr(first_seen, 1, 16) as t, count(*) FROM records WHERE record_type='PRODUCT' GROUP BY t ORDER BY t").fetchall()
print("Product collection batches by timestamp (first_seen):")
for r in product_times:
    print(f"  {r[0]}: {r[1]} rows")
evidence_samples = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM aux WHERE kind='pricing_evidence' LIMIT 5").fetchall()]
print("Sample evidence fields:", [list(e.keys()) for e in evidence_samples])

llm_done_keys = set(r[0] for r in conn.execute("SELECT dedup_key FROM aux WHERE kind='pricing_llm_done'").fetchall())
product_keys = set(r[0] for r in conn.execute("SELECT dedup_key FROM records WHERE record_type='PRODUCT'").fetchall())
overlap = len(product_keys & llm_done_keys)
print(f"Total PRODUCT rows in DB: {len(product_keys)}")
print(f"Products from LLM re-judgment (in pricing_llm_done): {overlap}")
print(f"Products from initial deterministic rules crawl: {len(product_keys) - overlap}")

# Check pricing_evidence details
methods = conn.execute("SELECT json_extract(payload, '$.method'), count(*) FROM aux WHERE kind='pricing_evidence' GROUP BY 1").fetchall()
print("Pricing evidence methods:", methods)

# Check the pricing_llm_answer records
answers = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM aux WHERE kind='pricing_llm_answer'").fetchall()]
valid_llm_answers = [a for a in answers if a.get("label") in ("FREE", "FREEMIUM", "PAID", "ENTERPRISE")]
print(f"Valid LLM answers in pricing_llm_answer: {len(valid_llm_answers)} out of {len(answers)}")

print("\n=== LLM PROVIDER AND MODEL STATUS ===")
import os
from dotenv import load_dotenv
load_dotenv()
print("GEMINI_API_KEY:", "SET" if os.getenv("GEMINI_API_KEY") else "NOT SET")
print("GEMINI_MODEL:", os.getenv("GEMINI_MODEL"))
print("GEMINI_FALLBACK_MODEL:", os.getenv("GEMINI_FALLBACK_MODEL"))
print("GROQ_API_KEY:", "SET" if os.getenv("GROQ_API_KEY") else "NOT SET")
print("GROQ_MODEL:", os.getenv("GROQ_MODEL"))
print("GROQ_FALLBACK_MODEL:", os.getenv("GROQ_FALLBACK_MODEL"))
print("DEEPSEEK_API_KEY:", "SET" if os.getenv("DEEPSEEK_API_KEY") else "NOT SET (omitted per instructions)")
