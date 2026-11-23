import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone

from src import dates as D
from src.llm.chain import (AllProvidersFailed, LLMChain, Provider, parse_json_object, providers_from_env)
from src.llm.chunking import estimate_tokens, truncate_smart
from src.llm.verify import ground_fields, is_grounded, quote_in_text

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)


class DateTests(unittest.TestCase):
    def test_absolute_formats(self):
        cases = {
            "2026-11-21T10:30:00Z": datetime(2026, 11, 21, 10, 30, tzinfo=timezone.utc),
            "2026-11-21T10:30:00+05:30": datetime(2026, 11, 21, 5, 0, tzinfo=timezone.utc),
            "2026-11-21 10:30:00": datetime(2026, 11, 21, 10, 30, tzinfo=timezone.utc),
            "2026-11-21": datetime(2026, 11, 21, tzinfo=timezone.utc),
            "Fri, 20 Nov 2026 19:53:41 +0000": datetime(2026, 11, 20, 19, 53, 41, tzinfo=timezone.utc),
            "Fri, 20 Nov 2026 19:53:41 GMT": datetime(2026, 11, 20, 19, 53, 41, tzinfo=timezone.utc),
            "Nov 20, 2026": datetime(2026, 11, 20, tzinfo=timezone.utc),
            "November 20, 2026": datetime(2026, 11, 20, tzinfo=timezone.utc),
            "20 November 2026": datetime(2026, 11, 20, tzinfo=timezone.utc),
            "Posted on Nov 20th, 2026": datetime(2026, 11, 20, tzinfo=timezone.utc),
            1795262400: datetime.fromtimestamp(1795262400, timezone.utc),
            "1795262400000": datetime.fromtimestamp(1795262400, timezone.utc),
        }
        for raw, want in cases.items():
            self.assertEqual(D.parse_any(raw, NOW), want, raw)

    def test_relative(self):
        cases = {
            "2 hours ago": NOW - timedelta(hours=2),
            "an hour ago": NOW - timedelta(hours=1),
            "45 minutes ago": NOW - timedelta(minutes=45),
            "3h ago": NOW - timedelta(hours=3),
            "1 day ago": NOW - timedelta(days=1),
            "2d ago": NOW - timedelta(days=2),
            "Posted 30+ days ago": NOW - timedelta(days=30),
            "yesterday": NOW - timedelta(days=1),
            "just now": NOW,
            "a week ago": NOW - timedelta(weeks=1),
        }
        for raw, want in cases.items():
            self.assertEqual(D.parse_any(raw, NOW), want, raw)

    def test_garbage(self):
        for raw in (None, "", "not a date", 12345, "soon"):
            self.assertIsNone(D.parse_any(raw, NOW), raw)

    def test_extract_published(self):
        jsonld = '<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-11-21T08:00:00Z"}</script>'
        meta = '<meta property="article:published_time" content="2026-11-20T23:00:00+00:00">'
        meta_rev = '<meta content="2026-11-20T22:00:00Z" name="date">'
        time_tag = '<p>By X <time datetime="2026-11-21T01:00:00Z">1 am</time></p>'
        modified_only = '<meta property="article:modified_time" content="2026-11-21T09:00:00Z">'
        self.assertEqual(D.extract_published(jsonld, NOW)[1], "json-ld")
        self.assertEqual(D.extract_published(meta, NOW)[0], datetime(2026, 11, 20, 23, tzinfo=timezone.utc))
        self.assertEqual(D.extract_published(meta_rev, NOW)[0], datetime(2026, 11, 20, 22, tzinfo=timezone.utc))
        self.assertEqual(D.extract_published(time_tag, NOW)[1], "time-tag")
        self.assertIsNone(D.extract_published(modified_only, NOW))  # modified != published
        self.assertIsNone(D.extract_published("<html>nothing</html>", NOW))

    def test_freshness(self):
        self.assertTrue(D.is_fresh(NOW - timedelta(hours=23, minutes=59), NOW))
        self.assertFalse(D.is_fresh(NOW - timedelta(hours=24, minutes=1), NOW))
        self.assertFalse(D.is_fresh(NOW + timedelta(hours=2), NOW))  # far future = bad date
        self.assertTrue(D.is_fresh(NOW + timedelta(minutes=3), NOW))  # clock skew tolerance
        self.assertFalse(D.is_fresh(None, NOW))


class ChunkingTests(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(truncate_smart("hello world", 100), "hello world")

    def test_truncation_respects_budget_and_keeps_lead(self):
        filler = "Some generic sentence about nothing in particular. " * 40
        text = "\n".join(["TITLE: Acme raises $50M Series B led by Foo Ventures.",
                          filler, "Revenue grew 300% to $12M in 2026 per CEO Jane Doe.", filler, filler])
        out = truncate_smart(text, 200)
        self.assertLessEqual(estimate_tokens(out), 200 + 5)
        self.assertTrue(out.startswith("TITLE: Acme raises $50M"))
        self.assertIn("Revenue grew 300%", out)  # dense segment kept over filler

    def test_single_huge_blob_is_hard_cut(self):
        out = truncate_smart("x" * 100000, 100)
        self.assertLessEqual(len(out), 400)

    def test_order_preserved(self):
        text = "\n".join(f"Segment {i} with Number {i} and $ {i}." for i in range(200))
        out = truncate_smart(text, 120)
        nums = [int(w) for w in out.replace("$", " ").split() if w.isdigit()]
        firsts = [n for n in nums][::3]
        self.assertEqual(firsts, sorted(firsts))


class VerifyTests(unittest.TestCase):
    SRC = "Acme Corp, based in Berlin, hired 1,200 engineers. CEO Jane Doe said pricing starts at $29/month."

    def test_grounded(self):
        self.assertTrue(is_grounded("Acme Corp", self.SRC))
        self.assertTrue(is_grounded("jane doe", self.SRC))
        self.assertTrue(is_grounded(1200, self.SRC))
        self.assertTrue(is_grounded(29, self.SRC))
        self.assertTrue(is_grounded(None, self.SRC))
        self.assertTrue(is_grounded(["Berlin", "Jane Doe"], self.SRC))
        self.assertFalse(is_grounded("Globex Inc", self.SRC))
        self.assertFalse(is_grounded(5000, self.SRC))
        self.assertFalse(is_grounded(["Berlin", "Paris"], self.SRC))

    def test_ground_fields_nulls_hallucinations(self):
        obj = {"company": "Acme Corp", "city": "Paris", "employees": 1200, "other": "kept"}
        clean, dropped = ground_fields(obj, self.SRC, ["company", "city", "employees"])
        self.assertEqual(dropped, ["city"])
        self.assertIsNone(clean["city"])
        self.assertEqual(clean["company"], "Acme Corp")
        self.assertEqual(clean["other"], "kept")

    def test_quote(self):
        self.assertTrue(quote_in_text("pricing starts at $29/month", self.SRC))
        self.assertTrue(quote_in_text("PRICING  starts at $29/month.", self.SRC))
        self.assertFalse(quote_in_text("pricing starts at $19/month", self.SRC))
        self.assertFalse(quote_in_text("", self.SRC))


class FakeCache:
    def __init__(self):
        self.d = {}

    def cache_get(self, k):
        return self.d.get(k)

    def cache_put(self, k, model, resp):
        self.d[k] = resp


def gemini_ok(text):
    return 200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}, ""


def openai_ok(text):
    return 200, {"choices": [{"message": {"content": text}}]}, ""


def validate(obj):
    if "label" not in obj:
        raise ValueError("missing label")
    return obj


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


class FakeTime:
    """Deterministic clock + sleep so cooldown logic is tested without waiting."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class ChainTests(unittest.TestCase):
    def providers(self):
        return providers_from_env({"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q", "DEEPSEEK_API_KEY": "d"})

    def chain(self, script, cache=None, providers=None):
        calls = []

        async def post(url, headers, body):
            calls.append((url, body))
            return script(url, headers, body, len(calls))

        self.ft = FakeTime()
        return LLMChain(providers or self.providers(), post, cache=cache,
                        sleep=self.ft.sleep, clock=self.ft.clock), calls

    def test_providers_from_env(self):
        names = [p.name for p in self.providers()]
        self.assertEqual(len(names), 3)
        self.assertTrue(names[0].startswith("gemini:") and names[1].startswith("groq:") and names[2].startswith("deepseek:"))
        self.assertEqual(providers_from_env({}), [])
        self.assertEqual(len(providers_from_env({"GROQ_API_KEY": "q", "GROQ_FALLBACK_MODEL": "small"})), 2)

    def test_first_tier_success(self):
        chain, calls = self.chain(lambda u, h, b, n: gemini_ok('{"label": "A"}'))
        res, who = run(chain.extract("sys", "text", lambda t: t, validate))
        self.assertEqual(res, {"label": "A"})
        self.assertTrue(who.startswith("gemini"))
        self.assertEqual(len(calls), 1)

    def test_429_falls_back_to_next_tier(self):
        def script(url, h, b, n):
            return (429, None, "rate limited") if "googleapis" in url else openai_ok('{"label": "B"}')
        chain, calls = self.chain(script)
        res, who = run(chain.extract("sys", "text", lambda t: t, validate))
        self.assertEqual(res["label"], "B")
        self.assertTrue(who.startswith("groq"))
        self.assertEqual(chain.stats.get("fallback:429"), 1)

    def test_413_rechunks_then_succeeds_on_same_tier(self):
        sizes = []

        def script(url, h, b, n):
            prompt = b["contents"][0]["parts"][0]["text"]
            sizes.append(len(prompt))
            return (413, None, "Payload Too Large") if len(sizes) < 3 else gemini_ok('{"label": "C"}')
        big = "\n".join(f"Paragraph {i} about Things and $ {i} stuff here." for i in range(20000))
        chain, _ = self.chain(script, providers=providers_from_env({"GEMINI_API_KEY": "g", "GEMINI_MAX_INPUT_TOKENS": "8000"}))
        res, who = run(chain.extract("sys", big, lambda t: t, validate))
        self.assertEqual(res["label"], "C")
        self.assertTrue(sizes[0] > sizes[1] > sizes[2])  # payload shrinks each time
        self.assertEqual(chain.stats["rechunk"], 2)

    def test_context_length_400_also_rechunks(self):
        state = {"n": 0}

        def script(url, h, b, n):
            state["n"] += 1
            return (400, None, "maximum context length exceeded") if state["n"] == 1 else gemini_ok('{"label": "D"}')
        chain, _ = self.chain(script, providers=providers_from_env({"GEMINI_API_KEY": "g"}))
        res, _ = run(chain.extract("sys", "word " * 50000, lambda t: t, validate))
        self.assertEqual(res["label"], "D")

    def test_invalid_json_and_schema_fall_through(self):
        def script(url, h, b, n):
            if "googleapis" in url:
                return gemini_ok("sorry I cannot")
            if "groq" in url:
                return openai_ok('{"other": 1}')
            return openai_ok('```json\n{"label": "E"}\n```')
        chain, _ = self.chain(script)
        res, who = run(chain.extract("sys", "text", lambda t: t, validate))
        self.assertEqual(res["label"], "E")
        self.assertTrue(who.startswith("deepseek"))
        self.assertEqual(chain.stats["bad_output"], 2)

    def test_auth_failure_disables_provider(self):
        def script(url, h, b, n):
            return (401, None, "bad key") if "googleapis" in url else openai_ok('{"label": "F"}')
        chain, calls = self.chain(script)
        run(chain.extract("sys", "t1", lambda t: t, validate))
        n_after_first = len(calls)
        run(chain.extract("sys", "t2", lambda t: t, validate))
        self.assertEqual(len(calls) - n_after_first, 1)  # gemini not retried for the second item

    def test_404_model_not_found_disables_immediately_and_falls_back(self):
        def script(url, h, b, n):
            if "googleapis" in url:
                return 404, None, "This model is no longer available"
            return openai_ok('{"label": "H"}')
        chain, calls = self.chain(script)
        res, who = run(chain.extract("sys", "t1", lambda t: t, validate))
        self.assertTrue(who.startswith("groq"))
        self.assertTrue(chain.alive())
        n = len(calls)
        run(chain.extract("sys", "t2", lambda t: t, validate))
        self.assertEqual(len(calls) - n, 1)  # gemini is not tried again
        only404 = LLMChain(providers_from_env({"GEMINI_API_KEY": "g"}), lambda *a: None)

        async def post404(u, h, b):
            return 404, None, "nope"
        only404.post = post404
        with self.assertRaises(AllProvidersFailed):
            run(only404.extract("sys", "t", lambda t: t, validate))
        self.assertFalse(only404.alive())

    def test_all_fail_raises(self):
        chain, _ = self.chain(lambda u, h, b, n: (599, None, "timeout"))
        with self.assertRaises(AllProvidersFailed):
            run(chain.extract("sys", "text", lambda t: t, validate))
        self.assertTrue(self.ft.slept)  # it waited for cooldowns between rounds, then gave up
        with self.assertRaises(AllProvidersFailed):
            run(LLMChain([], None).extract("sys", "text", lambda t: t, validate))

    def test_waits_for_cooldown_instead_of_failing_item(self):
        state = {"n": 0}

        def script(url, h, b, n):
            state["n"] += 1
            # every tier is rate-limited on the first pass; after the cooldown, Gemini answers
            if state["n"] <= 3:
                return 429, None, "quota"
            return gemini_ok('{"label": "W"}') if "googleapis" in url else openai_ok('{"label": "W"}')
        chain, calls = self.chain(script)
        res, who = run(chain.extract("sys", "text", lambda t: t, validate))
        self.assertEqual(res["label"], "W")
        self.assertTrue(self.ft.slept and self.ft.slept[0] >= 20)  # waited out the first cooldown
        self.assertEqual(len(calls), 4)

    def test_daily_quota_disables_tier_instead_of_waiting(self):
        daily = ('{"error": {"message": "Quota exceeded for metric generate_content_free_tier_requests, '
                 'limit: 20, model: gemini-x", "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}')

        def script(url, h, b, n):
            return (429, None, daily) if "googleapis" in url else openai_ok('{"label": "D"}')
        chain, calls = self.chain(script)
        res, who = run(chain.extract("sys", "t1", lambda t: t, validate))
        self.assertTrue(who.startswith("groq"))
        self.assertEqual(self.ft.slept, [])                      # never waited
        self.assertEqual(chain.stats.get("quota_exhausted"), 1)   # the gemini tier is disabled, not retried
        n = len(calls)
        run(chain.extract("sys", "t2", lambda t: t, validate))
        self.assertEqual(len(calls) - n, 1)                       # gemini is not tried again this run
        only = LLMChain(providers_from_env({"GEMINI_API_KEY": "g"}), None, sleep=self.ft.sleep, clock=self.ft.clock)

        async def post(u, h, b):
            return 429, None, daily
        only.post = post
        with self.assertRaises(AllProvidersFailed):
            run(only.extract("sys", "t3", lambda t: t, validate))
        self.assertFalse(only.alive())

    def test_per_minute_429_uses_the_providers_retry_hint(self):
        hint = '{"error": {"message": "Please retry in 28.5s", "details": [{"retryDelay": "28s"}]}}'
        state = {"n": 0}

        def script(url, h, b, n):
            state["n"] += 1
            return (429, None, hint) if state["n"] == 1 else gemini_ok('{"label": "R"}')
        chain, _ = self.chain(script, providers=providers_from_env({"GEMINI_API_KEY": "g"}))
        res, _ = run(chain.extract("sys", "t", lambda t: t, validate))
        self.assertEqual(res["label"], "R")
        self.assertEqual(len(self.ft.slept), 1)
        self.assertAlmostEqual(self.ft.slept[0], 29.0, delta=1.5)   # waited what Google asked, not a blind 20s

    def test_cooling_tier_is_skipped_for_next_item(self):
        def script(url, h, b, n):
            return (429, None, "quota") if "googleapis" in url else openai_ok('{"label": "S"}')
        chain, calls = self.chain(script)
        run(chain.extract("sys", "t1", lambda t: t, validate))
        n = len(calls)
        run(chain.extract("sys", "t2", lambda t: t, validate))
        # gemini's two tiers are cooling down; only groq is called for the second item
        self.assertEqual(len(calls) - n, 1)

    def test_input_cap_limits_payload(self):
        sizes = []

        def script(url, h, b, n):
            sizes.append(len(b["contents"][0]["parts"][0]["text"]))
            return gemini_ok('{"label": "C"}')
        big = "\n".join(f"Paragraph {i} about Things and $ {i} stuff here." for i in range(20000))
        chain, _ = self.chain(script, providers=providers_from_env({"GEMINI_API_KEY": "g"}))
        run(chain.extract("sys", big, lambda t: t, validate, input_cap=500))
        self.assertLess(sizes[0], 500 * 4 + 200)

    def test_cache_prevents_second_call(self):
        cache = FakeCache()
        chain, calls = self.chain(lambda u, h, b, n: gemini_ok('{"label": "G"}'), cache=cache)
        run(chain.extract("sys", "same text", lambda t: t, validate))
        res, who = run(chain.extract("sys", "same text", lambda t: t, validate))
        self.assertEqual(who, "cache")
        self.assertEqual(len(calls), 1)

    def test_parse_json_object(self):
        self.assertEqual(parse_json_object('Here: {"a": 1} done'), {"a": 1})
        with self.assertRaises(ValueError):
            parse_json_object("no json")


try:
    from src.collectors.pricing_llm import _validate, label_supported
    HAVE = True
except ImportError:
    HAVE = False


@unittest.skipUnless(HAVE, "pydantic not installed")
class PricingLLMTests(unittest.TestCase):
    def test_label_supported(self):
        self.assertTrue(label_supported("FREEMIUM", "Free plan $0 /month"))
        self.assertTrue(label_supported("PAID", "Pro $20 per user"))
        self.assertTrue(label_supported("ENTERPRISE", "Contact sales for pricing"))
        self.assertFalse(label_supported("FREE", "Pro $20 per user"))
        self.assertFalse(label_supported("PAID", "Welcome to our product"))

    def test_validate(self):
        self.assertEqual(_validate({"pricingModel": "paid", "evidence": "x"})["pricingModel"], "PAID")
        with self.assertRaises(ValueError):
            _validate({"pricingModel": "cheap"})
        with self.assertRaises(ValueError):
            _validate([])


if __name__ == "__main__":
    unittest.main()
