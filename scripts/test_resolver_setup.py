import json
import os
import sqlite3
from collections import Counter, defaultdict
from rapidfuzz import fuzz

from src.core.store import Store
from src.resolver.normalize import clean_display_name, normalize_key

def test_resolve_dry_run():
    store = Store("data/pipeline.db")
    with open("src/resolver/seed_startups.json", "r", encoding="utf-8") as f:
        seeds = json.load(f)
    print(f"Loaded {len(seeds)} seeds.")

    # Test seed mapping structures
    seed_by_key = {}
    alias_by_key = {}
    for s in seeds:
        s_name = s["name"]
        s_key = normalize_key(s_name)
        seed_by_key[s_key] = s
        for alias in s.get("aliases", []):
            a_key = normalize_key(alias)
            if a_key:
                alias_by_key[a_key] = s

    print(f"seed_by_key: {len(seed_by_key)}, alias_by_key: {len(alias_by_key)}")
    store.close()

if __name__ == "__main__":
    test_resolve_dry_run()
