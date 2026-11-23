import sqlite3
import re
import json

conn = sqlite3.connect("data/pipeline.db")
suffix_pattern = re.compile(
    r"(?i)[,\s]+(inc\.?|incorporated|llc\.?|ltd\.?|limited|corp\.?|corporation|pbc\.?|public benefit corp(oration)?|gmbh|s\.?a\.?|s\.?a\.?s\.?|co\.?|company|labs?)\s*$"
)

all_names = []
for r in conn.execute("SELECT json_extract(payload, '$.content.company') FROM records WHERE record_type='JOB'").fetchall():
    if r[0]: all_names.append(r[0])

for r in conn.execute("SELECT json_extract(payload, '$.content.startupName') FROM records WHERE record_type='PRODUCT'").fetchall():
    if r[0]: all_names.append(r[0])

for r in conn.execute("SELECT json_extract(payload, '$.content.entities') FROM records WHERE record_type='NEWS'").fetchall():
    if r[0]:
        try:
            for e in json.loads(r[0]):
                all_names.append(e)
        except:
            pass

stripped = []
for n in set(all_names):
    m = suffix_pattern.search(n)
    if m:
        cleaned = suffix_pattern.sub("", n).strip()
        stripped.append((n, cleaned, m.group(0)))

print(f"Total stripped: {len(stripped)}")
for o, c, s in stripped[:20]:
    print(f"  '{o}' -> '{c}' (matched: '{s}')")
