"""Unit tests for News collector (PR 5B). Offline with injected fake fetcher and clock."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.collectors.news import (
    NEWS_FEEDS,
    _extract_article_text,
    _parse_entry_date,
    collect_news,
    extract_summary_and_entities,
    parse_feed_items,
)
from src.core.http import FetchResult
from src.core.store import Store
from src.llm.chain import LLMChain, Provider

FIXED_NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


class FakeFetcher:
    """In-memory fake fetcher that returns pre-configured responses by URL."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    async def get_json(self, url: str, **kw):
        self.calls.append(url)
        return self.routes.get(url, {})

    async def get(self, url: str, **kw):
        self.calls.append(url)
        if url in self.routes:
            val = self.routes[url]
            if isinstance(val, Exception):
                raise val
            return FetchResult(url=url, status=200, text=val)
        return FetchResult(url=url, status=404, text="")


class NewsFeedParsingTests(unittest.TestCase):
    def test_parse_atom_feed(self):
        fresh_iso = (FIXED_NOW - timedelta(hours=2)).isoformat()
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>The Verge AI</title>
  <entry>
    <title>New AI Regulation Agreement</title>
    <link href="https://www.theverge.com/ai/agreement-123"/>
    <published>{fresh_iso}</published>
    <summary>Negotiators reached an agreement on autonomous systems.</summary>
  </entry>
</feed>"""
        items = parse_feed_items(xml, "atom")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "New AI Regulation Agreement")
        self.assertEqual(items[0]["url"], "https://www.theverge.com/ai/agreement-123")
        self.assertEqual(items[0]["raw_date"], fresh_iso)

    def test_parse_rss2_feed(self):
        fresh_rfc = (FIXED_NOW - timedelta(hours=3)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>The Decoder</title>
    <item>
      <title>GPT-6 Announced by OpenAI</title>
      <link>https://the-decoder.com/gpt-6-announced/</link>
      <pubDate>{fresh_rfc}</pubDate>
      <description>OpenAI introduced GPT-6 with enhanced reasoning capabilities.</description>
    </item>
  </channel>
</rss>"""
        items = parse_feed_items(xml, "rss2")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "GPT-6 Announced by OpenAI")
        self.assertEqual(items[0]["url"], "https://the-decoder.com/gpt-6-announced/")
        self.assertEqual(items[0]["raw_date"], fresh_rfc)


class ArticleExtractionTests(unittest.TestCase):
    def test_trafilatura_extraction(self):
        html = """<!DOCTYPE html>
<html>
<head><title>Test Article</title></head>
<body>
<article>
  <h1>Major AI Breakthrough</h1>
  <p>Researchers at Anthropic have introduced a novel interpretability method that maps internal representations across billion-parameter neural networks.</p>
  <p>The findings were published on Sunday, showing significant improvements over previous activation steering approaches.</p>
</article>
</body>
</html>"""
        text = _extract_article_text(html, "Fallback summary")
        self.assertIn("Anthropic", text)
        self.assertIn("interpretability method", text)

    def test_fallback_on_empty_html(self):
        fallback = "This is a fallback summary from RSS."
        text = _extract_article_text("", fallback)
        self.assertEqual(text, fallback)


class GroundedExtractionTests(unittest.TestCase):
    ARTICLE_TEXT = (
        "OpenAI announced a partnership with Apple on Sunday. "
        "The deal integrates ChatGPT into iOS 19, enabling advanced Siri assistance across iPhone devices. "
        "CEO Sam Altman stated that user privacy remains protected with zero data retention."
    )

    def test_grounded_summary_and_entities_preserved(self):
        prov = Provider("mock", "mock-model", "http://mock", {}, "openai")

        async def fake_post(url, headers, body):
            resp = {
                "summary": "OpenAI partnered with Apple to integrate ChatGPT into iOS 19 with privacy protection.",
                "entities": ["OpenAI", "Apple", "ChatGPT", "Sam Altman"],
            }
            return 200, {"choices": [{"message": {"content": json.dumps(resp)}}]}, ""

        chain = LLMChain([prov], fake_post)
        summary, entities = asyncio.run(
            extract_summary_and_entities(chain, "OpenAI and Apple Deal", self.ARTICLE_TEXT)
        )
        self.assertIsNotNone(summary)
        self.assertIn("Apple", summary)
        self.assertEqual(set(entities), {"OpenAI", "Apple", "ChatGPT", "Sam Altman"})

    def test_ungrounded_summary_is_nulled(self):
        prov = Provider("mock", "mock-model", "http://mock", {}, "openai")

        async def fake_post(url, headers, body):
            # Hallucinated summary with content completely outside the text
            resp = {
                "summary": "Google acquired DeepMind in London for 500 million dollars in cash.",
                "entities": ["Google", "DeepMind"],
            }
            return 200, {"choices": [{"message": {"content": json.dumps(resp)}}]}, ""

        chain = LLMChain([prov], fake_post)
        summary, entities = asyncio.run(
            extract_summary_and_entities(chain, "AI News", self.ARTICLE_TEXT)
        )
        self.assertIsNone(summary)  # Must be nulled because it's not in the article!
        self.assertEqual(entities, [])  # Google and DeepMind are not in the article, so dropped!

    def test_ungrounded_entity_dropped_while_grounded_kept(self):
        prov = Provider("mock", "mock-model", "http://mock", {}, "openai")

        async def fake_post(url, headers, body):
            resp = {
                "summary": "OpenAI announced a partnership with Apple.",
                "entities": ["OpenAI", "Apple", "HallucinatedCorp", "FantasyAI"],
            }
            return 200, {"choices": [{"message": {"content": json.dumps(resp)}}]}, ""

        chain = LLMChain([prov], fake_post)
        summary, entities = asyncio.run(
            extract_summary_and_entities(chain, "Partnership", self.ARTICLE_TEXT)
        )
        self.assertIsNotNone(summary)
        self.assertIn("OpenAI", entities)
        self.assertIn("Apple", entities)
        self.assertNotIn("HallucinatedCorp", entities)
        self.assertNotIn("FantasyAI", entities)


class FullCollectNewsOrchestrationTests(unittest.TestCase):
    def test_collect_news_end_to_end(self):
        fresh_rfc = (FIXED_NOW - timedelta(hours=4)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        stale_rfc = (FIXED_NOW - timedelta(hours=30)).strftime("%a, %d %b %Y %H:%M:%S +0000")

        decoder_rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>The Decoder</title>
    <item>
      <title>Mistral AI Releases New Model</title>
      <link>https://the-decoder.com/mistral-new-model/</link>
      <pubDate>{fresh_rfc}</pubDate>
      <description>Mistral AI announced a new lightweight language model.</description>
    </item>
    <item>
      <title>Old Stale News Article</title>
      <link>https://the-decoder.com/old-news/</link>
      <pubDate>{stale_rfc}</pubDate>
      <description>Stale news from two days ago.</description>
    </item>
  </channel>
</rss>"""

        article_html = """<html><body><article>
<h1>Mistral AI Releases New Model</h1>
<p>Mistral AI, the Paris-based artificial intelligence company, released its latest open-weights model today.</p>
<p>The model achieves state-of-the-art benchmark results across mathematics and coding benchmarks.</p>
</article></body></html>"""

        routes = {
            "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml": "<feed xmlns='http://www.w3.org/2005/Atom'></feed>",
            "https://the-decoder.com/feed/": decoder_rss,
            "https://techcrunch.com/category/artificial-intelligence/feed/": "<rss><channel></channel></rss>",
            "https://www.wired.com/feed/tag/ai/latest/rss": "<rss><channel></channel></rss>",
            "https://www.technologyreview.com/topic/artificial-intelligence/feed": "<rss><channel></channel></rss>",
            "https://the-decoder.com/mistral-new-model": article_html,
            "https://the-decoder.com/mistral-new-model/": article_html,
        }
        fetcher = FakeFetcher(routes)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_news.db")
            store = Store(db_path)
            try:
                prov = Provider("mock", "mock-model", "http://mock", {}, "openai")

                async def fake_post(url, headers, body):
                    resp = {
                        "summary": "Mistral AI released its latest open-weights model today in Paris.",
                        "entities": ["Mistral AI", "Paris"],
                    }
                    return 200, {"choices": [{"message": {"content": json.dumps(resp)}}]}, ""

                chain = LLMChain([prov], fake_post)
                total = asyncio.run(collect_news(store, fetcher, chain=chain, now=FIXED_NOW))

                self.assertEqual(total, 1)
                self.assertEqual(store.count("NEWS"), 1)

                payloads = store.payloads("NEWS")
                self.assertEqual(len(payloads), 1)
                article = payloads[0]
                self.assertEqual(article["recordType"], "NEWS")
                self.assertEqual(article["source"]["name"], "The Decoder")
                self.assertEqual(article["source"]["url"], "https://the-decoder.com/mistral-new-model")
                self.assertEqual(article["content"]["title"], "Mistral AI Releases New Model")
                self.assertIsNotNone(article["content"]["summary"])
                self.assertIn("Mistral AI", article["content"]["entities"])

                # Check mapping log
                mapping = store.mapping_rows()
                news_mappings = [m for m in mapping if m["entity_type"] == "NEWS"]
                self.assertGreaterEqual(len(news_mappings), 1)
                self.assertEqual(news_mappings[0]["raw_name"], "Mistral AI")
                self.assertEqual(news_mappings[0]["canonical_name"], "Mistral AI")
            finally:
                store.close()
