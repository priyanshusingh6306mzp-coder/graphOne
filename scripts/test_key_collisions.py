import sqlite3
import re
from collections import defaultdict

conn = sqlite3.connect("data/pipeline.db")
names = [r[0] for r in conn.execute("SELECT json_extract(payload, '$.content.entityName') FROM records WHERE record_type='STARTUP'").fetchall() if r[0]]

def clean_key(name):
    # strip suffix
    s = re.sub(r"(?i)[,\s]+(inc\.?|incorporated|llc\.?|ltd\.?|limited|corp\.?|corporation|pbc\.?|gmbh|labs?|technologies|solutions|co\.?|company)\s*$", "", name).strip()
    # alphanumeric only lowercase
    return re.sub(r"[^a-z0-9]", "", s.lower())

clusters = defaultdict(list)
for n in set(names):
    k = clean_key(n)
    clusters[k].append(n)

collisions = {k: v for k, v in clusters.items() if len(v) > 1}
print(f"Total keys: {len(clusters)}, collisions: {len(collisions)}")
for k, v in list(collisions.items())[:20]:
    print(f"  key '{k}': {v}")
