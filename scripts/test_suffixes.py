import sqlite3
import re

conn = sqlite3.connect("data/pipeline.db")
names = [r[0] for r in conn.execute("SELECT json_extract(payload, '$.content.entityName') FROM records WHERE record_type='STARTUP'").fetchall() if r[0]]

suffix_pattern = re.compile(
    r"(?i)[,\s]+(inc\.?|incorporated|llc\.?|ltd\.?|limited|corp\.?|corporation|pbc\.?|public benefit corp(oration)?|gmbh|s\.?a\.?|s\.?a\.?s\.?|co\.?|company)\s*$"
)

stripped_examples = []
for n in names:
    m = suffix_pattern.search(n)
    if m:
        stripped = suffix_pattern.sub("", n).strip()
        stripped_examples.append((n, stripped, m.group(0)))

print(f"Total stripped: {len(stripped_examples)}")
for orig, clean, sfx in stripped_examples[:25]:
    print(f"  '{orig}' -> '{clean}' (suffix: '{sfx}')")
