import sqlite3
import json
from urllib.parse import urlsplit

conn = sqlite3.connect("data/pipeline.db")
c = conn.cursor()

# Get startups
startup_rows = c.execute("SELECT source_url, payload FROM records WHERE record_type='STARTUP'").fetchall()
startup_by_yc_url = {}
startup_by_name = {}
for url, pstr in startup_rows:
    p = json.loads(pstr)
    name = p["content"]["entityName"]
    startup_by_yc_url[url] = name
    startup_by_name[name] = url

# Get startup_meta
meta_rows = c.execute("SELECT payload FROM aux WHERE kind='startup_meta'").fetchall()
meta_by_domain = {}
for r in meta_rows:
    m = json.loads(r[0])
    site = m.get("website")
    if site:
        netloc = urlsplit(site).netloc.lower().removeprefix("www.")
        meta_by_domain[netloc] = m.get("yc_url")

# Get products
product_rows = c.execute("SELECT source_url, payload FROM records WHERE record_type='PRODUCT'").fetchall()

matched = 0
for p_url, p_str in product_rows:
    p = json.loads(p_str)
    p_name = p["content"]["startupName"]
    p_domain = urlsplit(p_url).netloc.lower().removeprefix("www.")
    parent_yc_url = meta_by_domain.get(p_domain)
    if parent_yc_url and parent_yc_url in startup_by_yc_url:
        matched += 1
    elif p_name in startup_by_name:
        matched += 1
    else:
        print("Unmatched product:", p_name, p_url)

print(f"Total products: {len(product_rows)}, matched to parent startup: {matched}")
