import sqlite3
import json
from collections import defaultdict
from src.resolver.normalize import conservative_clean_name, normalize_key
from src.resolver.resolve import load_seeds, resolve_single_name
from scripts.test_new_resolver_full import startups_by_k, startup_canon_by_k, seed_by_key, alias_by_key, seed_keys

conn = sqlite3.connect("data/pipeline.db")
c = conn.cursor()

n_rows = c.execute("SELECT dedup_key, source_url, payload FROM records WHERE record_type='NEWS'").fetchall()
news_res = []
for dkey, url, pstr in n_rows:
    p = json.loads(pstr)
    aux = c.execute("SELECT payload FROM aux WHERE kind='raw_name_NEWS' AND dedup_key=?", (dkey,)).fetchone()
    raw_ents = json.loads(aux[0])["raw_names"] if aux else p["content"].get("entities", [])
    for raw in raw_ents:
        if not raw: continue
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
        news_res.append((raw, canon, method, score))

print("Total news entities resolved:", len(news_res))
non_id_news = [r for r in news_res if r[0] != r[1]]
print("Non-identity news entities:", len(non_id_news))
for r in non_id_news:
    print(f"  News non-id: {r[0]!r} -> {r[1]!r} ({r[2]})")
