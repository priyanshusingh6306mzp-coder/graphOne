import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.core.retry import compute_backoff, parse_retry_after
from src.core.store import Store
from src.core.urls import normalize_url, url_hash
from src.export.flatten import COLUMNS, flatten, to_row


class UrlTests(unittest.TestCase):
    def test_normalize(self):
        a = normalize_url("http://www.Example.com/path/?utm_source=x&b=2&a=1#frag")
        self.assertEqual(a, "https://example.com/path?a=1&b=2")
        self.assertEqual(url_hash("https://example.com/p/"), url_hash("http://www.example.com/p?utm_campaign=z"))

    def test_rejects_non_http(self):
        with self.assertRaises(ValueError):
            normalize_url("ftp://x.com/a")
        with self.assertRaises(ValueError):
            normalize_url("not a url")


class RetryTests(unittest.TestCase):
    def test_retry_after_seconds_and_date(self):
        self.assertEqual(parse_retry_after("7"), 7.0)
        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
        later = (now + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertAlmostEqual(parse_retry_after(later, now=now), 30.0, places=0)
        self.assertIsNone(parse_retry_after("garbage"))
        self.assertIsNone(parse_retry_after(None))

    def test_backoff_grows_capped_and_honors_retry_after(self):
        fixed = lambda: 0.5
        d0, d3 = compute_backoff(0, rng=fixed), compute_backoff(3, rng=fixed)
        self.assertLess(d0, d3)
        self.assertLessEqual(compute_backoff(20, cap=60, rng=lambda: 1.0), 60)
        self.assertGreaterEqual(compute_backoff(0, retry_after=10, rng=fixed), 10)
        self.assertNotEqual(compute_backoff(2, rng=lambda: 0.1), compute_backoff(2, rng=lambda: 0.9))


class FlattenTests(unittest.TestCase):
    def test_dot_notation_and_order(self):
        rec = {
            "schemaVersion": "1.0", "recordType": "STARTUP",
            "source": {"name": "YC", "url": "https://yc.com/x"},
            "content": {"entityName": "OpenAI", "data": {"employeeCount": None}},
            "collectedAt": "2026-11-21T10:00:00Z",
        }
        flat = flatten(rec)
        self.assertEqual(flat["source.url"], "https://yc.com/x")
        self.assertIn("content.data.employeeCount", flat)
        row = to_row(rec, "STARTUP")
        self.assertEqual(len(row), len(COLUMNS["STARTUP"]))
        self.assertEqual(row[COLUMNS["STARTUP"].index("content.entityName")], "OpenAI")
        self.assertEqual(row[COLUMNS["STARTUP"].index("content.data.employeeCount")], "")

    def test_authors_joined(self):
        rec = {"content": {"authors": ["A B", "C D"]}}
        self.assertEqual(flatten(rec)["content.authors"], "A B; C D")

    def test_mapping_log_dotted_key(self):
        row = {"raw_name": "Open AI", "canonical_name": "OpenAI", "match_method": "fuzzy",
               "score": 96.0, "entity_type": "STARTUP", "source.url": "https://x.com", "collectedAt": "t"}
        self.assertEqual(to_row(row, "ENTITY_MAPPING_LOG")[5], "https://x.com")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_dedup_by_normalized_url(self):
        self.assertTrue(self.store.insert_record("STARTUP", "https://a.com/x/", {"n": 1}))
        self.assertFalse(self.store.insert_record("STARTUP", "http://www.a.com/x?utm_source=q", {"n": 2}))
        self.assertTrue(self.store.insert_record("PRODUCT", "https://a.com/x", {"n": 3}))
        self.assertEqual(self.store.count("STARTUP"), 1)
        self.assertEqual(self.store.payloads("STARTUP")[0]["n"], 1)

    def test_update_payload(self):
        self.store.insert_record("STARTUP", "https://a.com/x", {"n": 1})
        self.store.update_payload("STARTUP", "https://a.com/x", {"n": 9})
        self.assertEqual(self.store.payloads("STARTUP")[0]["n"], 9)

    def test_seen_urls(self):
        self.assertTrue(self.store.mark_seen("https://n.com/a"))
        self.assertFalse(self.store.mark_seen("https://www.n.com/a/"))
        self.assertTrue(self.store.was_seen("https://n.com/a"))
        self.assertFalse(self.store.was_seen("https://n.com/b"))

    def test_cache_and_mapping_log(self):
        self.assertIsNone(self.store.cache_get("h"))
        self.store.cache_put("h", "gemini", '{"a":1}')
        self.assertEqual(self.store.cache_get("h"), '{"a":1}')
        self.store.log_mapping("STARTUP", "Open AI", "OpenAI", "fuzzy", 96.0, "https://x.com")
        rows = self.store.mapping_rows()
        self.assertEqual(rows[0]["canonical_name"], "OpenAI")
        self.assertEqual(rows[0]["source.url"], "https://x.com")


if __name__ == "__main__":
    unittest.main()
