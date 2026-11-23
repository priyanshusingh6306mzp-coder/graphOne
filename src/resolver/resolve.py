"""Idempotent Entity Resolution & Entity Mapping Log."""
import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from rapidfuzz import fuzz

from ..core.store import Store
from .normalize import conservative_clean_name, normalize_key


def load_seeds(path: str = "src/resolver/seed_startups.json") -> List[Dict[str, Any]]:
    """Load the 50-startup seed list."""
    if not os.path.exists(path):
        base = os.path.dirname(__file__)
        path = os.path.join(base, "seed_startups.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_single_name(
    raw: str,
    seed_by_key: Dict[str, Dict],
    alias_by_key: Dict[str, Dict],
    seed_keys: List[Tuple[str, Dict]],
) -> Optional[Tuple[str, str, Optional[float]]]:
    """Attempt to resolve a single name against the seed list (exact, alias, or fuzzy)."""
    if not raw:
        return ("", "unresolved", None)

    key = normalize_key(raw)
    clean = conservative_clean_name(raw)
    if not key:
        return (raw, "unresolved", None)

    # 1. Exact or suffix-stripped match to seed canonical name
    if key in seed_by_key:
        target = seed_by_key[key]["name"]
        method = "exact" if raw == target else "alias"
        return (target, method, 100.0)

    # 2. Match to seed alias
    if key in alias_by_key:
        target = alias_by_key[key]["name"]
        return (target, "alias", 100.0)

    # 3. Fuzzy match against seed names
    # Guard: BOTH normalized names must be >= 6 characters
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
            # Score >= 92 and margin >= 3.0 points over runner-up
            if best_score >= 92.0 and margin >= 3.0:
                return (best_s["name"], "fuzzy", round(float(best_score), 1))

    return None


def resolve_standalone_name(
    raw: str,
    seed_by_key: Dict[str, Dict],
    alias_by_key: Dict[str, Dict],
    seed_keys: List[Tuple[str, Dict]],
) -> Tuple[str, str, Optional[float]]:
    """Resolve a standalone name against seeds, falling back to conservative cleaning."""
    s_match = resolve_single_name(raw, seed_by_key, alias_by_key, seed_keys)
    if s_match:
        return s_match
    cleaned = conservative_clean_name(raw)
    if cleaned != raw:
        return (cleaned, "alias", 100.0)
    return (raw, "unresolved", None)


def resolve_demo(seed_path: str = "src/resolver/seed_startups.json") -> None:
    """Print resolver outputs on messy sample strings without modifying DB or mapping_log."""
    seeds = load_seeds(seed_path)
    seed_by_key = {}
    alias_by_key = {}
    seed_keys = []
    for s in seeds:
        sk = normalize_key(s["name"])
        seed_by_key[sk] = s
        seed_keys.append((sk, s))
        for a in s.get("aliases", []):
            ak = normalize_key(a)
            if ak:
                alias_by_key[ak] = s

    samples = [
        "Open AI",
        "OpenAI, Inc.",
        "OpenAI Inc",
        "Anthropic PBC",
        "Anthropic, PBC",
        "Anthropicc",
        "HuggingFace",
        "Hugging Face, Inc.",
        "Mistral AI",
        "Mistral AI SAS",
        "Scale AI",
        "Scale AI, Inc.",
        "Databricks, Inc.",
        "Perplexity AI",
        "Matidor.com",
        "Cognition Labs",
        "Decentralized AI Labs",
        "ElevenLabs",
        "GitLab, Inc.",
        "HENRY",
        "Henry",
        ".Wave",
        "Wave",
        "aSim",
        "Asim",
        "Unknown Random Startup LLC",
    ]

    print("=== RESOLVER DEMO (Messy Sample Strings) ===")
    print(f"{'Input (raw_name)':<30} -> {'Resolved Canonical':<25} {'Method':<12} {'Score':<8}")
    print("-" * 80)
    for s in samples:
        canon, method, score = resolve_standalone_name(s, seed_by_key, alias_by_key, seed_keys)
        score_str = f"{score:.1f}" if score is not None else "None"
        print(f"{s:<30} -> {canon:<25} {method:<12} {score_str:<8}")


def resolve_entities(
    store: Store,
    dry_run: bool = False,
    demo: bool = False,
    seed_path: str = "src/resolver/seed_startups.json",
) -> Dict[str, Any]:
    """Resolve all entities in the database idempotently and populate the mapping log.

    Guarantees:
    1. Two STARTUP records with different source URLs are NEVER merged together.
    2. Conservative cleaned canonical names strip legal suffixes, trailing .com, and stray punct,
       while keeping brand tokens like 'Labs' and 'AI'.
    3. PRODUCT records link to their parent STARTUP via startup_meta/domain first, ensuring
       Product.startupName always equals Startup.entityName.
    4. JOB_COMPANY and NEWS only link to a STARTUP/seed canonical name when unambiguous.
    5. Strict idempotency via stored original raw names in aux (raw_name_<RECORD_TYPE>).
    """
    if demo:
        resolve_demo(seed_path)
        return {"demo": True}

    seeds = load_seeds(seed_path)

    # Build seed lookup structures
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

    # 1. Gather all records and retrieve / initialize original raw names from aux for idempotency
    with store._lock:
        rows = store._conn.execute("SELECT record_type, dedup_key, source_url, payload FROM records").fetchall()

    startup_records = []
    product_records = []
    job_records = []
    news_records = []

    for rt, dkey, url, payload_str in rows:
        payload = json.loads(payload_str)
        if rt == "STARTUP":
            cur_raw = payload.get("content", {}).get("entityName", "")
            aux = store.get_aux("raw_name_STARTUP", url)
            if aux and "raw_name" in aux:
                raw_name = aux["raw_name"]
            else:
                raw_name = cur_raw
                if not dry_run:
                    store.put_aux("raw_name_STARTUP", url, {"raw_name": raw_name})
            startup_records.append((url, dkey, raw_name, payload))

        elif rt == "PRODUCT":
            cur_raw = payload.get("content", {}).get("startupName", "")
            aux = store.get_aux("raw_name_PRODUCT", url)
            if aux and "raw_name" in aux:
                raw_name = aux["raw_name"]
            else:
                raw_name = cur_raw
                if not dry_run:
                    store.put_aux("raw_name_PRODUCT", url, {"raw_name": raw_name})
            product_records.append((url, dkey, raw_name, payload))

        elif rt == "JOB":
            cur_raw = payload.get("content", {}).get("company", "")
            aux = store.get_aux("raw_name_JOB", url)
            if aux and "raw_name" in aux:
                raw_name = aux["raw_name"]
            else:
                raw_name = cur_raw
                if not dry_run:
                    store.put_aux("raw_name_JOB", url, {"raw_name": raw_name})
            job_records.append((url, dkey, raw_name, payload))

        elif rt == "NEWS":
            cur_ents = payload.get("content", {}).get("entities", [])
            aux = store.get_aux("raw_name_NEWS", url)
            if aux and "raw_names" in aux:
                raw_ents = aux["raw_names"]
            else:
                raw_ents = cur_ents
                if not dry_run:
                    store.put_aux("raw_name_NEWS", url, {"raw_names": raw_ents})
            news_records.append((url, dkey, raw_ents, payload))

    # 2. Resolve STARTUP records
    # Rule 1: Never merge distinct startups by name.
    # If cleaning would cause a collision between distinct startups with different URLs, keep distinct raw names!
    cand_to_raws = defaultdict(set)
    for url, dkey, raw, payload in startup_records:
        clean_cand = conservative_clean_name(raw)
        cand_to_raws[clean_cand].add(raw)

    colliding_cands = {cc for cc, raws in cand_to_raws.items() if len(raws) > 1}

    startup_resolutions = {}  # url -> (raw, canon, method, score)
    for url, dkey, raw, payload in startup_records:
        clean_cand = conservative_clean_name(raw)
        if clean_cand in colliding_cands:
            # Ambiguous collision: keep distinct raw name!
            canon = raw
            method = "unresolved"
            score = None
        elif clean_cand != raw:
            canon = clean_cand
            method = "alias"
            score = 100.0
        else:
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

    # Build startup lookup indexes for Product, Job, and News linking
    startup_canon_by_url = {url: res[1] for url, res in startup_resolutions.items()}
    startup_canon_by_name = {res[0]: res[1] for res in startup_resolutions.values()}

    startups_by_k = defaultdict(set)
    startup_canon_by_k = {}
    for url, res in startup_resolutions.items():
        k = normalize_key(res[1])
        startups_by_k[k].add(url)
        startup_canon_by_k[k] = res[1]

    # 3. Resolve PRODUCT records
    # Rule 3: Link Products via URL/startup_meta first!
    meta_rows = store.aux_items("startup_meta")
    meta_by_domain = {}
    meta_by_name = {}
    for m in meta_rows:
        site = m.get("website")
        yc_url = m.get("yc_url")
        if site and yc_url:
            netloc = urlsplit(site).netloc.lower().removeprefix("www.")
            meta_by_domain[netloc] = yc_url
        if m.get("name") and yc_url:
            meta_by_name[m["name"]] = yc_url

    product_resolutions = {}  # url -> (raw, canon, method, score)
    for url, dkey, raw, payload in product_records:
        domain = urlsplit(url).netloc.lower().removeprefix("www.")
        parent_yc_url = meta_by_domain.get(domain)

        if not parent_yc_url and raw in meta_by_name:
            parent_yc_url = meta_by_name[raw]

        if parent_yc_url and parent_yc_url in startup_canon_by_url:
            p_canon = startup_canon_by_url[parent_yc_url]
        elif raw in startup_canon_by_name:
            p_canon = startup_canon_by_name[raw]
        else:
            p_canon = conservative_clean_name(raw)

        method = "exact" if raw == p_canon else "alias"
        score = 100.0 if method != "unresolved" else None
        product_resolutions[url] = (raw, p_canon, method, score)

    # 4. Resolve JOB records
    # Rule 1 & 3: Resolve via seed/alias/unique startup match only when unambiguous.
    job_resolutions = {}  # url -> (raw, canon, method, score)
    for url, dkey, raw, payload in job_records:
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
        job_resolutions[url] = (raw, canon, method, score)

    # 5. Resolve NEWS records
    news_resolutions = {}  # url -> List[(raw_ent, canon_ent, method, score)]
    for url, dkey, raw_ents, payload in news_records:
        resolved_list = []
        for raw_ent in raw_ents:
            if not raw_ent:
                continue
            s_match = resolve_single_name(raw_ent, seed_by_key, alias_by_key, seed_keys)
            if s_match:
                canon, method, score = s_match
            else:
                k = normalize_key(raw_ent)
                if len(startups_by_k[k]) == 1:
                    canon = startup_canon_by_k[k]
                    method = "exact" if raw_ent == canon else "alias"
                    score = 100.0
                else:
                    canon = conservative_clean_name(raw_ent)
                    method = "unresolved"
                    score = None
            resolved_list.append((raw_ent, canon, method, score))
        news_resolutions[url] = resolved_list

    # 6. Apply updates and write to mapping_log (if not dry_run)
    if not dry_run:
        store.recreate_mapping_log_table()

    method_counts = Counter()
    canonical_merges = 0
    samples = defaultdict(list)

    # Update STARTUP
    for url, dkey, raw, payload in startup_records:
        raw, canon, method, score = startup_resolutions[url]
        method_counts[method] += 1
        if raw != canon:
            canonical_merges += 1
            if len(samples[method]) < 10:
                samples[method].append((raw, canon, score))
        if not dry_run:
            payload["content"]["entityName"] = canon
            store.update_payload("STARTUP", url, payload)
            store.log_mapping("STARTUP", raw, canon, method, score, url)

    # Update PRODUCT
    for url, dkey, raw, payload in product_records:
        raw, canon, method, score = product_resolutions[url]
        method_counts[method] += 1
        if raw != canon:
            canonical_merges += 1
            if len(samples[method]) < 10:
                samples[method].append((raw, canon, score))
        if not dry_run:
            payload["content"]["startupName"] = canon
            store.update_payload("PRODUCT", url, payload)
            store.log_mapping("PRODUCT", raw, canon, method, score, url)

    # Update JOB
    for url, dkey, raw, payload in job_records:
        raw, canon, method, score = job_resolutions[url]
        method_counts[method] += 1
        if raw != canon:
            canonical_merges += 1
            if len(samples[method]) < 10:
                samples[method].append((raw, canon, score))
        if not dry_run:
            payload["content"]["company"] = canon
            store.update_payload("JOB", url, payload)
            store.log_mapping("JOB_COMPANY", raw, canon, method, score, url)

    # Update NEWS
    for url, dkey, raw_ents, payload in news_records:
        ent_tuples = news_resolutions[url]
        resolved_names = []
        for raw_ent, canon_ent, method, score in ent_tuples:
            resolved_names.append(canon_ent)
            method_counts[method] += 1
            if raw_ent != canon_ent:
                canonical_merges += 1
                if len(samples[method]) < 10:
                    samples[method].append((raw_ent, canon_ent, score))
            if not dry_run:
                store.log_mapping("NEWS", raw_ent, canon_ent, method, score, url)
        if not dry_run:
            payload["content"]["entities"] = resolved_names
            store.update_payload("NEWS", url, payload)

    return {
        "records_scanned": len(startup_records) + len(product_records) + len(job_records) + len(news_records),
        "method_counts": dict(method_counts),
        "canonical_merges": canonical_merges,
        "samples": dict(samples),
        "dry_run": dry_run,
    }
