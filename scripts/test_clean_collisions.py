import sqlite3
import json
import re
from collections import defaultdict, Counter

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

conn = sqlite3.connect("data/pipeline.db")
c = conn.cursor()

# Get all startups and their raw_name from aux or record
rows = c.execute("SELECT dedup_key, source_url, payload FROM records WHERE record_type='STARTUP'").fetchall()
startups = []
for dkey, url, pstr in rows:
    p = json.loads(pstr)
    aux = c.execute("SELECT payload FROM aux WHERE kind='raw_name_STARTUP' AND dedup_key=?", (dkey,)).fetchone()
    # note: store.get_aux used url_hash(url) which matches dedup_key
    raw = json.loads(aux[0])["raw_name"] if aux else p["content"]["entityName"]
    startups.append((url, raw))

print(f"Total startups: {len(startups)}")

# Group startups by raw_name
startups_by_raw = defaultdict(set)
for url, raw in startups:
    startups_by_raw[raw].add(url)

# Test candidate canonicals:
# For each raw_name, cand = conservative_clean_name(raw)
# Check which distinct raw_names map to the same candidate
cands_to_raws = defaultdict(set)
for raw in startups_by_raw:
    cand = conservative_clean_name(raw)
    cands_to_raws[cand].add(raw)

# Any candidate that merges multiple distinct raw_names that have different URLs would cause SUSPICIOUS!
colliding_cands = {cand: raws for cand, raws in cands_to_raws.items() if len(raws) > 1}
print(f"Colliding candidates: {len(colliding_cands)}")
for cand, raws in colliding_cands.items():
    print(f"  cand '{cand}': raws {raws}")
