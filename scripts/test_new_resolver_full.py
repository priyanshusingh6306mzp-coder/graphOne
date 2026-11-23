import json
import sqlite3
from urllib.parse import urlsplit
from src.core.store import Store
from src.resolver.normalize import conservative_clean_name, normalize_key
from src.resolver.resolve import load_seeds, resolve_single_name

conn = sqlite3.connect("data/pipeline.db")
c = conn.cursor()

seeds = load_seeds("src/resolver/seed_startups.json")
seed_by_key = {}
alias_by_key = {}
seed_keys = []
for s in seeds:
    sk = normalize_key(s["name"])
    seed_by_key[sk] = s
    seed_keys.append((sk, s))
    for a in s.get("aliases", []):
        ak = normalize_key(a)
        if ak:
            alias_by_key[ak] = s

# 1. Startups
s_rows = c.execute("SELECT dedup_key, source_url, payload FROM records WHERE record_type='STARTUP'").fetchall()
startups = []
for dkey, url, pstr in s_rows:
    p = json.loads(pstr)
    aux = c.execute("SELECT payload FROM aux WHERE kind='raw_name_STARTUP' AND dedup_key=?", (dkey,)).fetchone()
    raw = json.loads(aux[0])["raw_name"] if aux else p["content"]["entityName"]
    startups.append((url, dkey, raw, p))

cand_map = {}
cand_counts = {}
from collections import defaultdict
grouped = defaultdict(set)
for url, dkey, raw, p in startups:
    cc = conservative_clean_name(raw)
    grouped[cc].add(raw)

colliding_cands = {cc for cc, raws in grouped.items() if len(raws) > 1}

startup_res = {} # url -> (raw, canon, method, score)
for url, dkey, raw, p in startups:
    cc = conservative_clean_name(raw)
    if cc in colliding_cands:
        canon = raw
        method = "unresolved"
        score = None
    elif cc != raw:
        canon = cc
        method = "alias"
        score = 100.0
    else:
        k = normalize_key(raw)
        if k in seed_by_key and seed_by_key[k]["name"] == raw:
            canon = raw
            method = "exact"
            score = 100.0
        else:
            canon = raw
            method = "unresolved"
            score = None
    startup_res[url] = (raw, canon, method, score)

# Check SUSPICIOUS
by_canon = defaultdict(list)
for url, (raw, canon, method, score) in startup_res.items():
    by_canon[canon].append({"source.url": url, "raw_name": raw, "canonical_name": canon})

sus = []
for canon, rs in by_canon.items():
    urls = {r["source.url"] for r in rs}
    if len(urls) > 1 and any(r["raw_name"] != canon for r in rs):
        sus.append((canon, rs))

print("SUSPICIOUS count on STARTUP:", len(sus))

# 2. Products
meta_rows = c.execute("SELECT payload FROM aux WHERE kind='startup_meta'").fetchall()
meta_by_domain = {}
for r in meta_rows:
    m = json.loads(r[0])
    site = m.get("website")
    if site:
        netloc = urlsplit(site).netloc.lower().removeprefix("www.")
        meta_by_domain[netloc] = m.get("yc_url")

p_rows = c.execute("SELECT dedup_key, source_url, payload FROM records WHERE record_type='PRODUCT'").fetchall()
product_res = []
startup_canon_by_url = {url: res[1] for url, res in startup_res.items()}
startup_canon_by_raw = {res[0]: res[1] for res in startup_res.values()}

for dkey, url, pstr in p_rows:
    p = json.loads(pstr)
    aux = c.execute("SELECT payload FROM aux WHERE kind='raw_name_PRODUCT' AND dedup_key=?", (dkey,)).fetchone()
    raw = json.loads(aux[0])["raw_name"] if aux else p["content"]["startupName"]
    domain = urlsplit(url).netloc.lower().removeprefix("www.")
    parent_yc_url = meta_by_domain.get(domain)

    if parent_yc_url and parent_yc_url in startup_canon_by_url:
        p_canon = startup_canon_by_url[parent_yc_url]
    elif raw in startup_canon_by_raw:
        p_canon = startup_canon_by_raw[raw]
    else:
        p_canon = conservative_clean_name(raw)

    method = "exact" if raw == p_canon else "alias"
    product_res.append((raw, p_canon, method, 100.0 if method != "unresolved" else None))

print("Total products:", len(product_res))
non_id_prod = [r for r in product_res if r[0] != r[1]]
print("Non-identity products:", len(non_id_prod))
for r in non_id_prod:
    print(f"  {r[0]!r} -> {r[1]!r} ({r[2]})")

# 3. Check JOB and NEWS
startups_by_k = defaultdict(set)
startup_canon_by_k = {}
for url, res in startup_res.items():
    k = normalize_key(res[1])
    startups_by_k[k].add(url)
    startup_canon_by_k[k] = res[1]

j_rows = c.execute("SELECT dedup_key, source_url, payload FROM records WHERE record_type='JOB'").fetchall()
job_res = []
for dkey, url, pstr in j_rows:
    p = json.loads(pstr)
    aux = c.execute("SELECT payload FROM aux WHERE kind='raw_name_JOB' AND dedup_key=?", (dkey,)).fetchone()
    raw = json.loads(aux[0])["raw_name"] if aux else p["content"]["company"]
    # match seed first
    s_match = resolve_single_name(raw, seed_by_key, alias_by_key, seed_keys)
    if s_match:
        canon, method, score = s_match
    else:
        k = normalize_key(raw)
        if len(startups_by_k[k]) == 1:
            canon = startup_canon_by_k[k]
            method = "exact" if raw == canon else "alias"
            score = 100.0
        else:
            canon = conservative_clean_name(raw)
            method = "unresolved"
            score = None
    job_res.append((raw, canon, method, score))

print("Jobs resolved:", len(job_res))
for r in job_res:
    if r[0] != r[1]:
        print(f"  Job non-id: {r[0]!r} -> {r[1]!r} ({r[2]})")
