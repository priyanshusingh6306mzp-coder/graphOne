import json
import sqlite3
from rapidfuzz import fuzz
from src.resolver.normalize import normalize_key

with open("src/resolver/seed_startups.json", "r", encoding="utf-8") as f:
    seeds = json.load(f)

conn = sqlite3.connect("data/pipeline.db")
names = [r[0] for r in conn.execute("SELECT json_extract(payload, '$.content.entityName') FROM records WHERE record_type='STARTUP'").fetchall() if r[0]]

seed_keys = [(normalize_key(s["name"]), s["name"]) for s in seeds]

fuzzy_matches = []
for name in set(names):
    k = normalize_key(name)
    if len(k) < 6:
        continue
    # skip exact / alias
    if any(k == sk for sk, _ in seed_keys):
        continue
    scores = []
    for sk, sname in seed_keys:
        if len(sk) >= 6:
            score = fuzz.ratio(k, sk)
            scores.append((score, sname, sk))
    scores.sort(key=lambda x: x[0], reverse=True)
    if scores:
        best_score, best_name, best_sk = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else 0.0
        margin = best_score - runner_up
        if best_score >= 92.0 and margin >= 3.0:
            fuzzy_matches.append((name, best_name, best_score, margin))

print(f"Total fuzzy matches found: {len(fuzzy_matches)}")
for n, bn, sc, mg in fuzzy_matches:
    print(f"  '{n}' -> '{bn}' (score: {sc:.1f}, margin: {mg:.1f})")
