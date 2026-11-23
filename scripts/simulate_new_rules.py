import sqlite3
import json
import re
from collections import defaultdict, Counter
from urllib.parse import urlsplit
from rapidfuzz import fuzz

LEGAL_SUFFIXES_RE = re.compile(
    r"(?i)[,\s]+(inc\.?|incorporated|llc\.?|l\.l\.c\.?|ltd\.?|limited|corp\.?|corporation|"
    r"pbc\.?|p\.b\.c\.?|public benefit corp(oration)?|gmbh|sas|s\.a\.s\.?|sa|s\.a\.?|"
    r"co\.?|company)\s*$"
)
DOT_COM_RE = re.compile(r"(?i)\.com\s*$")
STRAY_PUNCT_RE = re.compile(r"^[,\.\-\s/]+|[,\.\-\s/]+$")

def conservative_clean_name(name: str) -> str:
    if not name:
        return ""
    cleaned = name.strip()
    cleaned = DOT_COM_RE.sub("", cleaned).strip()
    while True:
        m = LEGAL_SUFFIXES_RE.search(cleaned)
        if not m:
            break
        cleaned = LEGAL_SUFFIXES_RE.sub("", cleaned).strip().rstrip(",").strip()
    cleaned = STRAY_PUNCT_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else name.strip()

def normalize_key(name: str) -> str:
    if not name:
        return ""
    clean = conservative_clean_name(name)
    return re.sub(r"[^a-z0-9]", "", clean.lower())

with open("src/resolver/seed_startups.json", "r", encoding="utf-8") as f:
    seeds = json.load(f)

seed_by_key = {}
alias_by_key = {}
seed_keys = []
for s in seeds:
    s_key = normalize_key(s["name"])
    seed_by_key[s_key] = s
    seed_keys.append((s_key, s))
    for a in s.get("aliases", []):
        ak = normalize_key(a)
        if ak:
            alias_by_key[ak] = s

conn = sqlite3.connect("data/pipeline.db")
c = conn.cursor()

# 1. Startups
s_rows = c.execute("SELECT dedup_key, source_url, payload FROM records WHERE record_type='STARTUP'").fetchall()
startups = []
for dkey, url, pstr in s_rows:
    p = json.loads(pstr)
    aux = c.execute("SELECT payload FROM aux WHERE kind='raw_name_STARTUP' AND dedup_key=?", (dkey,)).fetchone()
    raw = json.loads(aux[0])["raw_name"] if aux else p["content"]["entityName"]
    startups.append((dkey, url, raw, p))

# Find potential collisions among startups if cleaned
cand_map = defaultdict(set)
for dkey, url, raw, p in startups:
    clean_cand = conservative_clean_name(raw)
    cand_map[clean_cand].add(raw)

colliding_cands = {c for c, raws in cand_map.items() if len(raws) > 1}

startup_resolutions = {} # url -> (raw, canon, method, score)
for dkey, url, raw, p in startups:
    clean_cand = conservative_clean_name(raw)
    # Check if cleaning causes collision with another distinct startup's raw name
    if clean_cand in colliding_cands:
        # DO NOT MERGE! Keep distinct raw name!
        canon = raw
        method = "unresolved"
        score = None
    elif clean_cand != raw:
        canon = clean_cand
        method = "alias"
        score = 100.0
    else:
        # Check if matches seed
        k = normalize_key(raw)
        if k in seed_by_key and seed_by_key[k]["name"] == raw:
            canon = raw
            method = "exact"
            score = 100.0
        else:
            canon = raw
            method = "unresolved"
            score = None
    startup_resolutions[url] = (raw, canon, method, score)

# 2. Check SUSPICIOUS on startups right now
by_canon = defaultdict(list)
for url, (raw, canon, method, score) in startup_resolutions.items():
    by_canon[canon].append({"source.url": url, "raw_name": raw, "canonical_name": canon})

sus = []
for canon, rs in by_canon.items():
    urls = {r["source.url"] for r in rs}
    if len(urls) > 1 and any(r["raw_name"] != canon for r in rs):
        sus.append((canon, rs))

print(f"SUSPICIOUS count on STARTUP: {len(sus)}")
if sus:
    for canon, rs in sus[:5]:
        print("  sus:", canon, rs)

# 3. Products
meta_rows = c.execute("SELECT payload FROM aux WHERE kind='startup_meta'").fetchall()
meta_by_domain = {}
for r in meta_rows:
    m = json.loads(r[0])
    site = m.get("website")
    if site:
        netloc = urlsplit(site).netloc.lower().removeprefix("www.")
        meta_by_domain[netloc] = m.get("yc_url")

p_rows = c.execute("SELECT dedup_key, source_url, payload FROM records WHERE record_type='PRODUCT'").fetchall()
product_resolutions = []
startup_canon_by_name = {res[1]: res[1] for res in startup_resolutions.values()}
startup_canon_by_raw = {res[0]: res[1] for res in startup_resolutions.values()}

mismatches = 0
for dkey, url, pstr in p_rows:
    p = json.loads(pstr)
    aux = c.execute("SELECT payload FROM aux WHERE kind='raw_name_PRODUCT' AND dedup_key=?", (dkey,)).fetchone()
    raw = json.loads(aux[0])["raw_name"] if aux else p["content"]["startupName"]
    domain = urlsplit(url).netloc.lower().removeprefix("www.")
    parent_yc_url = meta_by_domain.get(domain)

    if parent_yc_url and parent_yc_url in startup_resolutions:
        p_canon = startup_resolutions[parent_yc_url][1]
    elif raw in startup_canon_by_name:
        p_canon = startup_canon_by_name[raw]
    elif raw in startup_canon_by_raw:
        p_canon = startup_canon_by_raw[raw]
    else:
        p_canon = conservative_clean_name(raw)

    method = "exact" if raw == p_canon else "alias"
    product_resolutions.append((raw, p_canon, method, 100.0 if method != "unresolved" else None))

print(f"Total products resolved: {len(product_resolutions)}")
print(f"Product non-identity count: {sum(1 for r, c, m, s in product_resolutions if r != c)}")
