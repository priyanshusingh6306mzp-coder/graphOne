import sqlite3
import json
from collections import Counter

conn = sqlite3.connect("data/pipeline.db")
names = Counter()
startup_names = Counter()
job_names = Counter()
news_names = Counter()
product_names = Counter()

# Startups
for r in conn.execute("SELECT json_extract(payload, '$.content.entityName') FROM records WHERE record_type='STARTUP'").fetchall():
    if r[0]:
        names[r[0]] += 1
        startup_names[r[0]] += 1

# Products
for r in conn.execute("SELECT json_extract(payload, '$.content.startupName') FROM records WHERE record_type='PRODUCT'").fetchall():
    if r[0]:
        names[r[0]] += 1
        product_names[r[0]] += 1

# Jobs
for r in conn.execute("SELECT json_extract(payload, '$.content.company') FROM records WHERE record_type='JOB'").fetchall():
    if r[0]:
        names[r[0]] += 1
        job_names[r[0]] += 1

# News
for r in conn.execute("SELECT json_extract(payload, '$.content.entities') FROM records WHERE record_type='NEWS'").fetchall():
    if r[0]:
        try:
            ents = json.loads(r[0]) if isinstance(r[0], str) else r[0]
            if isinstance(ents, list):
                for e in ents:
                    names[e] += 1
                    news_names[e] += 1
        except Exception:
            pass

print("=== JOB COMPANIES ===")
for n, c in job_names.most_common(20):
    print(f"  {n}: {c}")

print("\n=== NEWS ENTITIES ===")
for n, c in news_names.most_common(25):
    print(f"  {n}: {c}")

print("\n=== STARTUPS OVERLAPPING WITH PRODUCTS ===")
overlap = set(startup_names.keys()) & set(product_names.keys())
print(f"Total overlapping names: {len(overlap)}")

print("\n=== TOP 60 OVERALL ENTITIES ===")
for n, c in names.most_common(60):
    print(f"  {n}: {c}")
