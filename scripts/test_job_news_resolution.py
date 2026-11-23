import sqlite3
import json
from collections import Counter, defaultdict
from src.core.store import Store
from src.resolver.normalize import normalize_key
from src.resolver.resolve import load_seeds

conn = sqlite3.connect("data/pipeline.db")
c = conn.cursor()

seeds = load_seeds("src/resolver/seed_startups.json")
seed_by_key = {}
alias_by_key = {}
for s in seeds:
    s_key = normalize_key(s["name"])
    seed_by_key[s_key] = s
    for a in s.get("aliases", []):
        a_key = normalize_key(a)
        if a_key:
            alias_by_key[a_key] = s

# Count unique startups per normalized key
startup_rows = c.execute("SELECT source_url, payload FROM records WHERE record_type='STARTUP'").fetchall()
startups_by_key = defaultdict(set)
startup_name_by_key = {}
for url, pstr in startup_rows:
    p = json.loads(pstr)
    name = p["content"]["entityName"]
    k = normalize_key(name)
    startups_by_key[k].add(url)
    startup_name_by_key[k] = name

# Test JOB companies
job_rows = c.execute("SELECT source_url, payload FROM records WHERE record_type='JOB'").fetchall()
print("=== JOBS RESOLUTION TEST ===")
for url, pstr in job_rows:
    p = json.loads(pstr)
    comp = p["content"]["company"]
    k = normalize_key(comp)
    if k in seed_by_key:
        print(f"  JOB '{comp}' -> Seed '{seed_by_key[k]['name']}' (exact/alias)")
    elif k in alias_by_key:
        print(f"  JOB '{comp}' -> Seed alias '{alias_by_key[k]['name']}' (alias)")
    elif len(startups_by_key[k]) == 1:
        print(f"  JOB '{comp}' -> Unique startup '{startup_name_by_key[k]}'")
    elif len(startups_by_key[k]) > 1:
        print(f"  JOB '{comp}' -> AMBIGUOUS ({len(startups_by_key[k])} startups) -> unresolved")
    else:
        print(f"  JOB '{comp}' -> Unresolved")

# Test NEWS entities
news_rows = c.execute("SELECT source_url, payload FROM records WHERE record_type='NEWS'").fetchall()
print("\n=== NEWS ENTITIES TEST ===")
news_ents = set()
for url, pstr in news_rows:
    p = json.loads(pstr)
    for e in p["content"].get("entities", []):
        news_ents.add(e)

for e in sorted(news_ents):
    k = normalize_key(e)
    if k in seed_by_key:
        print(f"  NEWS '{e}' -> Seed '{seed_by_key[k]['name']}'")
    elif k in alias_by_key:
        print(f"  NEWS '{e}' -> Seed alias '{alias_by_key[k]['name']}'")
    elif len(startups_by_key[k]) == 1:
        print(f"  NEWS '{e}' -> Unique startup '{startup_name_by_key[k]}'")
    elif len(startups_by_key[k]) > 1:
        print(f"  NEWS '{e}' -> AMBIGUOUS ({len(startups_by_key[k])} startups) -> unresolved")
    else:
        print(f"  NEWS '{e}' -> Unresolved")
