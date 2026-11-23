"""Tests for entity resolution, normalization, fuzzy guards, and idempotency."""
import json
import os
import tempfile
import unittest

from src.core.store import Store
from src.resolver.normalize import (
    conservative_clean_name,
    normalize_key,
    strip_legal_suffixes,
)
from src.resolver.resolve import (
    load_seeds,
    resolve_entities,
    resolve_single_name,
    resolve_standalone_name,
)


class NormalizationTests(unittest.TestCase):
    def test_suffix_stripping_preserves_labs_and_ai(self):
        self.assertEqual(strip_legal_suffixes("OpenAI, Inc."), "OpenAI")
        self.assertEqual(strip_legal_suffixes("Anthropic PBC"), "Anthropic")
        self.assertEqual(strip_legal_suffixes("Scale AI, Inc."), "Scale AI")
        self.assertEqual(strip_legal_suffixes("Mistral AI SAS"), "Mistral AI")
        # Brand tokens 'Labs' and 'AI' must be KEPT!
        self.assertEqual(strip_legal_suffixes("Decentralized AI Labs"), "Decentralized AI Labs")
        self.assertEqual(strip_legal_suffixes("Cognition Labs"), "Cognition Labs")
        self.assertEqual(strip_legal_suffixes("ElevenLabs"), "ElevenLabs")
        self.assertEqual(strip_legal_suffixes("GitLab"), "GitLab")

    def test_conservative_clean_name(self):
        self.assertEqual(conservative_clean_name("  OpenAI, Inc.  "), "OpenAI")
        self.assertEqual(conservative_clean_name("Anthropic, PBC"), "Anthropic")
        self.assertEqual(conservative_clean_name("  .Wave  "), "Wave")
        self.assertEqual(conservative_clean_name("SuperTokens, Inc."), "SuperTokens")
        # Trailing .com stripped
        self.assertEqual(conservative_clean_name("Matidor.com"), "Matidor")
        self.assertEqual(conservative_clean_name("tday.com"), "tday")
        self.assertEqual(conservative_clean_name("Dench.com"), "Dench")

    def test_normalize_key_equivalences(self):
        # OpenAI variants
        self.assertEqual(normalize_key("OpenAI"), "openai")
        self.assertEqual(normalize_key("OpenAI, Inc."), "openai")
        self.assertEqual(normalize_key("Open AI"), "openai")

        # Anthropic variants
        self.assertEqual(normalize_key("Anthropic"), "anthropic")
        self.assertEqual(normalize_key("Anthropic PBC"), "anthropic")

        # Mistral variants
        self.assertEqual(normalize_key("Mistral AI"), "mistralai")
        self.assertEqual(normalize_key("Mistral AI SAS"), "mistralai")

        # Hugging Face variants
        self.assertEqual(normalize_key("Hugging Face"), "huggingface")
        self.assertEqual(normalize_key("HuggingFace"), "huggingface")
        self.assertEqual(normalize_key("Hugging Face, Inc."), "huggingface")


class FuzzyGuardsTests(unittest.TestCase):
    def setUp(self):
        self.seed_by_key = {
            "openai": {"name": "OpenAI"},
            "anthropic": {"name": "Anthropic"},
            "alphatest": {"name": "AlphaTest"},
            "betatest": {"name": "BetaTest"},
        }
        self.alias_by_key = {
            "openair": {"name": "OpenAI"},
        }
        self.seed_keys = [
            ("openai", {"name": "OpenAI"}),
            ("anthropic", {"name": "Anthropic"}),
            ("alphatest", {"name": "AlphaTest"}),
            ("betatest", {"name": "BetaTest"}),
        ]

    def test_short_names_rejected_below_6_chars(self):
        # "Open" has key len 4 (< 6 chars) -> must be rejected from fuzzy
        res = resolve_single_name("Open", self.seed_by_key, self.alias_by_key, self.seed_keys)
        self.assertIsNone(res)

    def test_score_below_92_rejected(self):
        # "Anthropical" (len 11) vs "Anthropic" (len 9): score = 90.0 (< 92) -> rejected
        res = resolve_single_name("Anthropical", self.seed_by_key, self.alias_by_key, self.seed_keys)
        self.assertIsNone(res)

    def test_runner_up_margin_guard(self):
        # Two seeds with identical distance
        seeds = [
            ("clusterx1", {"name": "ClusterX1"}),
            ("clusterx2", {"name": "ClusterX2"}),
        ]
        s_by_k = {k: v for k, v in seeds}
        # "ClusterX" has equal ratio to both -> margin is 0 < 3.0 -> rejected
        res = resolve_single_name("ClusterX", s_by_k, {}, seeds)
        self.assertIsNone(res)

    def test_valid_fuzzy_match(self):
        # "Anthropicc" (len 10) vs "Anthropic" (len 9): score = 94.7 >= 92, margin > 3 -> matches
        res = resolve_single_name("Anthropicc", self.seed_by_key, self.alias_by_key, self.seed_keys)
        self.assertIsNotNone(res)
        canon, method, score = res
        self.assertEqual(canon, "Anthropic")
        self.assertEqual(method, "fuzzy")
        self.assertGreaterEqual(score, 92.0)


class DistinctStartupsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test_distinct.db")
        self.store = Store(self.db_path)
        self.seed_file = os.path.join(self.tmp.name, "seeds.json")
        with open(self.seed_file, "w", encoding="utf-8") as f:
            json.dump([{"name": "OpenAI", "aliases": ["Open AI"]}], f)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_never_merge_distinct_startups_by_name(self):
        # Insert distinct startups that would collide if aggressively normalized
        self.store.insert_record("STARTUP", "https://yc.com/henry", {
            "schemaVersion": "1.0", "recordType": "STARTUP",
            "source": {"name": "YC", "url": "https://yc.com/henry"},
            "content": {"entityName": "HENRY", "data": {"employeeCount": 10}},
            "collectedAt": "2026-11-22T00:00:00Z"
        })
        self.store.insert_record("STARTUP", "https://yc.com/henry-2", {
            "schemaVersion": "1.0", "recordType": "STARTUP",
            "source": {"name": "YC", "url": "https://yc.com/henry-2"},
            "content": {"entityName": "Henry", "data": {"employeeCount": 5}},
            "collectedAt": "2026-11-22T00:00:00Z"
        })
        self.store.insert_record("STARTUP", "https://yc.com/wave", {
            "schemaVersion": "1.0", "recordType": "STARTUP",
            "source": {"name": "YC", "url": "https://yc.com/wave"},
            "content": {"entityName": "Wave", "data": {"employeeCount": 10}},
            "collectedAt": "2026-11-22T00:00:00Z"
        })
        self.store.insert_record("STARTUP", "https://yc.com/dotwave", {
            "schemaVersion": "1.0", "recordType": "STARTUP",
            "source": {"name": "YC", "url": "https://yc.com/dotwave"},
            "content": {"entityName": ".Wave", "data": {"employeeCount": 5}},
            "collectedAt": "2026-11-22T00:00:00Z"
        })

        resolve_entities(self.store, dry_run=False, seed_path=self.seed_file)

        s_payloads = {p["source"]["url"]: p["content"]["entityName"] for p in self.store.payloads("STARTUP")}
        # Must be kept DISTINCT!
        self.assertEqual(s_payloads["https://yc.com/henry"], "HENRY")
        self.assertEqual(s_payloads["https://yc.com/henry-2"], "Henry")
        self.assertEqual(s_payloads["https://yc.com/wave"], "Wave")
        self.assertEqual(s_payloads["https://yc.com/dotwave"], ".Wave")


class EndToEndResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test_res.db")
        self.store = Store(self.db_path)

        self.seed_file = os.path.join(self.tmp.name, "seeds.json")
        seeds = [
            {"name": "OpenAI", "aliases": ["Open AI", "OpenAI, Inc.", "OpenAI Inc"]},
            {"name": "Anthropic", "aliases": ["Anthropic PBC", "Anthropic, PBC"]},
            {"name": "Mistral AI", "aliases": ["Mistral AI SAS", "Mistral"]},
            {"name": "Hugging Face", "aliases": ["HuggingFace", "Hugging Face, Inc."]},
        ]
        with open(self.seed_file, "w", encoding="utf-8") as f:
            json.dump(seeds, f)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_resolution_product_linkage_and_idempotency(self):
        # 1. Startup: "OpenAI, Inc."
        self.store.insert_record("STARTUP", "https://yc.com/openai", {
            "schemaVersion": "1.0", "recordType": "STARTUP",
            "source": {"name": "YC", "url": "https://yc.com/openai"},
            "content": {"entityName": "OpenAI, Inc.", "data": {"employeeCount": 1000}},
            "collectedAt": "2026-11-22T00:00:00Z"
        })

        # Save startup_meta linking openai domain
        self.store.put_aux("startup_meta", "https://yc.com/openai", {
            "name": "OpenAI, Inc.", "yc_url": "https://yc.com/openai", "website": "https://openai.com"
        })

        # 2. Product: "Open AI" from https://openai.com/pricing
        self.store.insert_record("PRODUCT", "https://openai.com/pricing", {
            "schemaVersion": "1.0", "recordType": "PRODUCT",
            "source": {"name": "openai.com", "url": "https://openai.com/pricing"},
            "content": {"startupName": "Open AI", "pricingModel": "PAID"},
            "collectedAt": "2026-11-22T00:00:00Z"
        })

        # 3. Startup 2: "Matidor.com"
        self.store.insert_record("STARTUP", "https://yc.com/matidor", {
            "schemaVersion": "1.0", "recordType": "STARTUP",
            "source": {"name": "YC", "url": "https://yc.com/matidor"},
            "content": {"entityName": "Matidor.com", "data": {"employeeCount": 20}},
            "collectedAt": "2026-11-22T00:00:00Z"
        })
        self.store.put_aux("startup_meta", "https://yc.com/matidor", {
            "name": "Matidor.com", "yc_url": "https://yc.com/matidor", "website": "https://matidor.com"
        })

        # Product 2: "Matidor.com" from https://matidor.com/pricing
        self.store.insert_record("PRODUCT", "https://matidor.com/pricing", {
            "schemaVersion": "1.0", "recordType": "PRODUCT",
            "source": {"name": "matidor.com", "url": "https://matidor.com/pricing"},
            "content": {"startupName": "Matidor.com", "pricingModel": "PAID"},
            "collectedAt": "2026-11-22T00:00:00Z"
        })

        # 4. Job: "Anthropic PBC"
        self.store.insert_record("JOB", "https://anthropic.com/jobs/1", {
            "schemaVersion": "1.0", "recordType": "JOB",
            "source": {"name": "Greenhouse", "url": "https://anthropic.com/jobs/1"},
            "content": {"company": "Anthropic PBC", "role_family": "Engineering"},
            "collectedAt": "2026-11-22T00:00:00Z"
        })

        # 5. News: ["HuggingFace", "Mistral AI"]
        self.store.insert_record("NEWS", "https://theverge.com/news/1", {
            "schemaVersion": "1.0", "recordType": "NEWS",
            "source": {"name": "The Verge", "url": "https://theverge.com/news/1"},
            "content": {"title": "AI Models", "entities": ["HuggingFace", "Mistral AI"]},
            "collectedAt": "2026-11-22T00:00:00Z"
        })

        # --- First Run ---
        res1 = resolve_entities(self.store, dry_run=False, seed_path=self.seed_file)

        # Check records updated in DB
        s_payloads = {p["source"]["url"]: p["content"]["entityName"] for p in self.store.payloads("STARTUP")}
        p_payloads = {p["source"]["url"]: p["content"]["startupName"] for p in self.store.payloads("PRODUCT")}

        self.assertEqual(s_payloads["https://yc.com/openai"], "OpenAI")
        self.assertEqual(p_payloads["https://openai.com/pricing"], "OpenAI")

        self.assertEqual(s_payloads["https://yc.com/matidor"], "Matidor")
        self.assertEqual(p_payloads["https://matidor.com/pricing"], "Matidor")

        # Zero mismatches between product's startupName and parent startup's entityName!
        self.assertEqual(p_payloads["https://openai.com/pricing"], s_payloads["https://yc.com/openai"])
        self.assertEqual(p_payloads["https://matidor.com/pricing"], s_payloads["https://yc.com/matidor"])

        j_payload = self.store.payloads("JOB")
        self.assertEqual(j_payload[0]["content"]["company"], "Anthropic")

        n_payload = self.store.payloads("NEWS")
        self.assertEqual(n_payload[0]["content"]["entities"], ["Hugging Face", "Mistral AI"])

        # --- Second Run (Idempotency Check) ---
        res2 = resolve_entities(self.store, dry_run=False, seed_path=self.seed_file)
        self.assertEqual(res1["canonical_merges"], res2["canonical_merges"])
        self.assertEqual(res1["method_counts"], res2["method_counts"])


if __name__ == "__main__":
    unittest.main()
