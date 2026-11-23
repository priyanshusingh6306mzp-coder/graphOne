import sqlite3
import json
from urllib.parse import urlsplit

conn = sqlite3.connect("data/pipeline.db")
c = conn.cursor()

# 1. Check pricing_evidence
product_rows = c.execute("SELECT source_url, payload FROM records WHERE record_type='PRODUCT'").fetchall()
startup_rows = c.execute("SELECT source_url, payload FROM records WHERE record_type='STARTUP'").fetchall()

print(f"Total products: {len(product_rows)}")
print(f"Total startups: {len(startup_rows)}")

# Build startup maps
startup_by_yc_url = {}
startup_by_name = {}
for url, pstr in startup_rows:
    p = json.loads(pstr)
    name = p["content"]["entityName"]
    startup_by_yc_url[url] = name
    startup_by_name[name] = (url, name)

# Build startup_meta maps
meta_rows = c.execute("SELECT payload FROM aux WHERE kind='startup_meta'").fetchall()
meta_by_website = {}
meta_by_yc_url = {}
for r in meta_rows:
    m = json.loads(r[0])
    site = m.get("website")
    if site:
        netloc = urlsplit(site).netloc.lower().removeprefix("www.")
        meta_by_website[netloc] = m
    yc_url = m.get("yc_url")
    if yc_url:
        meta_by_yc_url[yc_url] = m

evidence_rows = c.execute("SELECT dedup_key, payload FROM aux WHERE kind='pricing_evidence'").fetchall()
print(f"Total pricing_evidence rows: {len(evidence_rows)}")

# Test linkage of each product
linked_by_evidence = 0
linked_by_domain = 0
not_linked = 0

for p_url, p_str in product_rows:
    p = json.loads(p_str)
    raw_name = p["content"]["startupName"]
    # Check domain
    domain = urlsplit(p_url).netloc.lower().removeprefix("www.")
    if domain in meta_by_website:
        linked_by_domain += 1
    elif raw_name in startup_by_name:
        linked_by_evidence += 1
    else:
        not_linked += 1

print(f"Linked by domain: {linked_by_domain}, by name: {linked_by_evidence}, not linked: {not_linked}")
