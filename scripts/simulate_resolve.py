import json
import sqlite3
from collections import Counter, defaultdict
from rapidfuzz import fuzz

from src.core.store import Store
from src.resolver.normalize import clean_display_name, normalize_key

def select_cluster_canonical(raw_names, counts):
    candidates = list(set(clean_display_name(r) for r in raw_names))
    if len(candidates) == 1:
        return candidates[0]

    def score_candidate(cand: str):
        cnt = sum(counts[r] for r in raw_names if clean_display_name(r) == cand)
        is_title = cand.istitle()
        is_all_caps = cand.isupper() and len(cand) > 1
        is_all_lower = cand.islower()
        casing_score = 2 if is_title else (0 if (is_all_caps or is_all_lower) else 1)
        no_punct = 1 if not cand.startswith((".", "-", "_", "/")) else 0
        return (cnt, casing_score, no_punct, -len(cand))

    sorted_cands = sorted(candidates, key=score_candidate, reverse=True)
    return sorted_cands[0]

def simulate():
    store = Store("data/pipeline.db")
    with open("src/resolver/seed_startups.json", "r", encoding="utf-8") as f:
        seeds = json.load(f)

    seed_by_key = {}
    alias_by_key = {}
    seed_keys = []
    for s in seeds:
        s_name = s["name"]
        s_key = normalize_key(s_name)
        seed_by_key[s_key] = s
        seed_keys.append((s_key, s))
        for alias in s.get("aliases", []):
            a_key = normalize_key(alias)
            if a_key:
                alias_by_key[a_key] = s

    # 1. Collect all items from the 4 record types
    # Each item: (entity_type, dedup_key, source_url, raw_name, record_dict)
    items = []
    with store._lock:
        rows = store._conn.execute("SELECT record_type, dedup_key, source_url, payload FROM records").fetchall()

    for rt, dkey, url, payload_str in rows:
        payload = json.loads(payload_str)
        if rt == "STARTUP":
            raw = payload.get("content", {}).get("entityName")
            if raw:
                items.append(("STARTUP", dkey, url, raw, payload))
        elif rt == "PRODUCT":
            raw = payload.get("content", {}).get("startupName")
            if raw:
                items.append(("PRODUCT", dkey, url, raw, payload))
        elif rt == "JOB":
            raw = payload.get("content", {}).get("company")
            if raw:
                items.append(("JOB_COMPANY", dkey, url, raw, payload))
        elif rt == "NEWS":
            ents = payload.get("content", {}).get("entities", [])
            for ent in ents:
                if ent:
                    items.append(("NEWS", dkey, url, ent, payload))

    print(f"Total entity occurrences extracted: {len(items)}")

    # 2. Match against seeds
    seed_matches = {}  # raw_name -> (canonical, method, score)
    unmatched_raws = set()

    all_raw_names = set(it[3] for it in items)
    for raw in all_raw_names:
        clean = clean_display_name(raw)
        key = normalize_key(raw)
        if not key:
            seed_matches[raw] = (raw, "unresolved", None)
            continue

        if key in seed_by_key:
            target = seed_by_key[key]["name"]
            method = "exact" if raw == target else "alias"
            seed_matches[raw] = (target, method, 100.0)
        elif key in alias_by_key:
            target = alias_by_key[key]["name"]
            seed_matches[raw] = (target, "alias", 100.0)
        else:
            # Fuzzy match guard: len(key) >= 6
            matched_fuzzy = False
            if len(key) >= 6:
                candidates = []
                for s_key, s_obj in seed_keys:
                    if len(s_key) >= 6:
                        score = fuzz.ratio(key, s_key)
                        candidates.append((score, s_obj))
                candidates.sort(key=lambda x: x[0], reverse=True)
                if candidates:
                    best_score, best_s = candidates[0]
                    runner_up = candidates[1][0] if len(candidates) > 1 else 0.0
                    margin = best_score - runner_up
                    if best_score >= 92.0 and margin >= 3.0:
                        seed_matches[raw] = (best_s["name"], "fuzzy", round(best_score, 1))
                        matched_fuzzy = True
            if not matched_fuzzy:
                unmatched_raws.add(raw)

    print(f"Matched to seeds: {len(seed_matches)}, Unmatched raws: {len(unmatched_raws)}")

    # 3. Clustering pass for unmatched raws
    clusters = defaultdict(list)
    for raw in unmatched_raws:
        key = normalize_key(raw)
        clusters[key].append(raw)

    cluster_matches = {}  # raw -> (canonical, method, score)
    raw_counts = Counter(it[3] for it in items)
    for key, raws in clusters.items():
        canonical = select_cluster_canonical(raws, raw_counts)

        for raw in raws:
            if raw != canonical:
                cluster_matches[raw] = (canonical, "cluster", 100.0)
            else:
                cluster_matches[raw] = (raw, "unresolved", None)

    # Combine
    all_decisions = {}
    all_decisions.update(seed_matches)
    all_decisions.update(cluster_matches)

    # Tally across all items
    method_counts = Counter()
    merges = 0
    samples = defaultdict(list)

    for etype, dkey, url, raw, payload in items:
        canon, method, score = all_decisions[raw]
        method_counts[method] += 1
        if raw != canon:
            merges += 1
            if len(samples[method]) < 10:
                samples[method].append((raw, canon, score))

    print("\nMethod counts across records:")
    for m, c in method_counts.items():
        print(f"  {m}: {c}")
    print(f"Total canonical merges: {merges}")

    print("\nSamples:")
    for m, ex in samples.items():
        print(f"=== Method: {m} ===")
        for r, c, s in ex:
            print(f"  '{r}' -> '{c}' (score: {s})")

    store.close()

if __name__ == "__main__":
    simulate()
