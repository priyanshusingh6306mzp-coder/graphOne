"""Unit tests for Jobs collector (PR 5A). Offline with injected fake fetcher and clock."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.collectors.jobs import (
    canonical_company,
    classify_role_family_deterministic,
    collect_ashby,
    collect_greenhouse,
    collect_jobs,
    collect_lever,
    collect_remoteok,
    collect_weworkremotely,
    is_ai_job,
    resolve_role_family,
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
        if url in self.routes:
            res = self.routes[url]
            if isinstance(res, Exception):
                raise res
            return res
        return {}

    async def get(self, url: str, **kw):
        self.calls.append(url)
        if url in self.routes:
            val = self.routes[url]
            if isinstance(val, Exception):
                raise val
            return FetchResult(url=url, status=200, text=val)
        return FetchResult(url=url, status=404, text="")


class RoleClassificationTests(unittest.TestCase):
    def test_deterministic_families(self):
        cases = [
            ("Senior Research Scientist, Alignment", "Research", "Research"),
            ("AI Researcher - Multimodal", "", "Research"),
            ("Postdoctoral Fellow, Machine Learning", "", "Research"),
            ("Senior Software Engineer, Platform", "Engineering", "Engineering"),
            ("Full Stack Developer (React/Node)", "Engineering", "Engineering"),
            ("Infrastructure SRE", "", "Engineering"),
            ("Machine Learning Engineer", "Core ML", "Engineering"),
            ("Staff Data Scientist", "Analytics", "Engineering"),
            ("Product Manager, Developer Experience", "Product", "Product"),
            ("Technical Program Manager, Compute", "TPM", "Product"),
            ("Lead Product Designer", "Design", "Design"),
            ("Senior UI/UX Designer", "", "Design"),
            ("Enterprise Account Executive", "Sales", "Sales"),
            ("Business Development Representative (BDR)", "GTM", "Sales"),
            ("Head of People & Culture", "HR", "Operations"),
            ("Corporate Counsel, Commercial", "Legal", "Operations"),
            ("Senior Accountant", "Finance", "Operations"),
        ]
        for title, dept, want in cases:
            got = classify_role_family_deterministic(title, dept)
            self.assertEqual(got, want, f"Failed for {title} (got {got}, expected {want})")

    def test_ambiguous_role_falls_back_to_other(self):
        self.assertIsNone(classify_role_family_deterministic("Chief Thought Officer", "Vibes"))

    def test_resolve_role_family_with_chain(self):
        async def run():
            # 1. Deterministic bypasses chain
            res = await resolve_role_family("Staff Software Engineer", chain=None)
            self.assertEqual(res, "Engineering")

            # 2. Ambiguous with grounded chain response
            prov = Provider("mock", "mock-model", "http://mock", {}, "openai")

            async def fake_post(url, headers, body):
                # Valid JSON with quote present in context
                resp = {"role_family": "Operations", "quote": "office coordinator"}
                return 200, {"choices": [{"message": {"content": json.dumps(resp)}}]}, ""

            chain = LLMChain([prov], fake_post)
            res2 = await resolve_role_family("Office Coordinator", description="Need an office coordinator", chain=chain)
            self.assertEqual(res2, "Operations")

            # 3. Ambiguous with hallucinated/ungrounded quote falls back to "Other"
            async def bad_post(url, headers, body):
                resp = {"role_family": "Sales", "quote": "invented quote not in text"}
                return 200, {"choices": [{"message": {"content": json.dumps(resp)}}]}, ""

            bad_chain = LLMChain([prov], bad_post)
            res3 = await resolve_role_family("Mystery Specialist", description="some description", chain=bad_chain)
            self.assertEqual(res3, "Other")

        asyncio.run(run())


class CompanyCanonicalizationTests(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(canonical_company("scaleai")[0], "Scale AI")
        self.assertEqual(canonical_company("Scale AI, Inc.")[0], "Scale AI")
        self.assertEqual(canonical_company("openai")[0], "OpenAI")
        self.assertEqual(canonical_company("Open AI LLC")[0], "OpenAI")
        self.assertEqual(canonical_company("Palantir Technologies, Inc.")[0], "Palantir")
        self.assertEqual(canonical_company("Acme Corp.")[0], "Acme")


class AIJobFilterTests(unittest.TestCase):
    def test_filter_matches(self):
        self.assertTrue(is_ai_job("Staff Data Scientist - Martech Platform (ML/Algos)"))
        self.assertTrue(is_ai_job("Software Engineer", tags=["python", "ai", "backend"]))
        self.assertTrue(is_ai_job("Product Manager, AI Foundations"))
        self.assertTrue(is_ai_job("Research Scientist, Deep Learning"))
        self.assertTrue(is_ai_job("Prompt Engineer"))
        self.assertFalse(is_ai_job("Deal Desk Analyst (Eastern Time Zone Preferred)", tags=["finance"]))
        self.assertFalse(is_ai_job("Senior React Developer", tags=["frontend", "javascript"]))


class JobFamilyCollectorsTests(unittest.TestCase):
    def test_greenhouse_first_published_rule(self):
        """Must use first_published and NEVER updated_at."""
        fresh_time = (FIXED_NOW - timedelta(hours=2)).isoformat()
        stale_time = (FIXED_NOW - timedelta(days=5)).isoformat()

        fake_data = {
            "jobs": [
                {
                    "title": "Member of Technical Staff",
                    "company_name": "Anthropic",
                    "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/111",
                    "first_published": fresh_time,
                    "updated_at": fresh_time,
                    "location": {"name": "San Francisco, CA"},
                },
                {
                    # Stale first_published, but fresh updated_at: MUST BE REJECTED
                    "title": "Old Job Recently Bumped",
                    "company_name": "Anthropic",
                    "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/222",
                    "first_published": stale_time,
                    "updated_at": fresh_time,
                    "location": {"name": "San Francisco, CA"},
                },
            ]
        }
        fetcher = FakeFetcher({"https://boards-api.greenhouse.io/v1/boards/anthropic/jobs": fake_data})
        jobs = asyncio.run(collect_greenhouse(fetcher, ["anthropic"], FIXED_NOW))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].content.title, "Member of Technical Staff")
        self.assertEqual(jobs[0].source.url, "https://job-boards.greenhouse.io/anthropic/jobs/111")
        self.assertEqual(jobs[0].content.role_family, "Engineering")

    def test_ashby_payload_and_remote(self):
        fresh_time = (FIXED_NOW - timedelta(hours=5)).isoformat()
        fake_data = {
            "jobs": [
                {
                    "title": "Research Scientist, Reasoning",
                    "publishedAt": fresh_time,
                    "jobUrl": "https://jobs.ashbyhq.com/openai/123",
                    "isRemote": False,
                    "workplaceType": "Remote",
                    "department": "Research",
                    "team": "Reasoning",
                    "descriptionPlain": "Full job description " * 100,  # large payload
                }
            ]
        }
        fetcher = FakeFetcher({"https://api.ashbyhq.com/posting-api/job-board/openai": fake_data})
        jobs = asyncio.run(collect_ashby(fetcher, ["openai"], FIXED_NOW))
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0].content.is_remote)
        self.assertEqual(jobs[0].content.role_family, "Research")
        self.assertEqual(jobs[0].content.company, "Openai")

    def test_lever_created_at_epoch_ms(self):
        fresh_ms = int((FIXED_NOW - timedelta(hours=6)).timestamp() * 1000)
        fake_data = [
            {
                "text": "Deployment Strategist, Defense",
                "createdAt": fresh_ms,
                "hostedUrl": "https://jobs.lever.co/palantir/456",
                "categories": {"location": "Washington, DC (Remote)", "team": "Deployment"},
                "workplaceType": "remote",
            }
        ]
        fetcher = FakeFetcher({"https://api.lever.co/v0/postings/palantir?mode=json": fake_data})
        jobs = asyncio.run(collect_lever(fetcher, ["palantir"], FIXED_NOW))
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0].content.is_remote)
        self.assertEqual(jobs[0].source.url, "https://jobs.lever.co/palantir/456")

    def test_remoteok_skips_index_0_and_filters_ai(self):
        fresh_iso = (FIXED_NOW - timedelta(hours=3)).isoformat()
        fake_data = [
            {"legal": "Legal notice to be skipped"},
            {
                "id": "1",
                "company": "Reddit",
                "position": "Machine Learning Engineer, Search",
                "tags": ["ml", "python"],
                "date": fresh_iso,
                "url": "https://remoteok.com/job/1",
            },
            {
                "id": "2",
                "company": "OtherCorp",
                "position": "General Accountant",
                "tags": ["finance"],
                "date": fresh_iso,
                "url": "https://remoteok.com/job/2",
            },
        ]
        fetcher = FakeFetcher({"https://remoteok.com/api": fake_data})
        jobs = asyncio.run(collect_remoteok(fetcher, FIXED_NOW))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].content.title, "Machine Learning Engineer, Search")
        self.assertTrue(jobs[0].content.is_remote)
        self.assertEqual(jobs[0].content.role_family, "Engineering")

    def test_weworkremotely_rss_parsing(self):
        fresh_rfc = (FIXED_NOW - timedelta(hours=4)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely</title>
    <item>
      <title>Celonis: Applied AI Engineer</title>
      <link>https://weworkremotely.com/jobs/ai-1</link>
      <pubDate>{fresh_rfc}</pubDate>
      <category>Programming</category>
      <description>We are looking for an AI engineer.</description>
    </item>
    <item>
      <title>SomeCo: Office Administrator</title>
      <link>https://weworkremotely.com/jobs/admin-2</link>
      <pubDate>{fresh_rfc}</pubDate>
      <category>Admin</category>
      <description>Administrative role.</description>
    </item>
  </channel>
</rss>"""
        fetcher = FakeFetcher({"https://weworkremotely.com/remote-jobs.rss": xml})
        jobs = asyncio.run(collect_weworkremotely(fetcher, FIXED_NOW))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].content.company, "Celonis")
        self.assertEqual(jobs[0].content.title, "Applied AI Engineer")
        self.assertTrue(jobs[0].content.is_remote)
        self.assertEqual(jobs[0].content.role_family, "Engineering")


class FullCollectJobsOrchestrationTests(unittest.TestCase):
    def test_collect_jobs_end_to_end(self):
        fresh_time = (FIXED_NOW - timedelta(hours=2)).isoformat()
        routes = {
            "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs": {
                "jobs": [
                    {
                        "title": "Research Engineer, Safety",
                        "company_name": "Anthropic",
                        "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/1",
                        "first_published": fresh_time,
                        "location": {"name": "San Francisco, CA"},
                    }
                ]
            },
            "https://boards-api.greenhouse.io/v1/boards/scaleai/jobs": {"jobs": []},
            "https://boards-api.greenhouse.io/v1/boards/databricks/jobs": {"jobs": []},
            "https://api.ashbyhq.com/posting-api/job-board/openai": {
                "jobs": [
                    {
                        "title": "Software Engineer, Infrastructure",
                        "publishedAt": fresh_time,
                        "jobUrl": "https://jobs.ashbyhq.com/openai/2",
                        "isRemote": True,
                        "department": "Engineering",
                    }
                ]
            },
            "https://api.ashbyhq.com/posting-api/job-board/cohere": {"jobs": []},
            "https://api.ashbyhq.com/posting-api/job-board/perplexity": {"jobs": []},
            "https://api.lever.co/v0/postings/palantir?mode=json": [],
            "https://remoteok.com/api": [{"legal": "legal"}],
            "https://weworkremotely.com/remote-jobs.rss": "<rss><channel></channel></rss>",
        }
        fetcher = FakeFetcher(routes)

        with tempfile.TemporaryDirectory() as tmpdir:
            import os
            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path)
            total = asyncio.run(collect_jobs(store, fetcher, now=FIXED_NOW))
            self.assertEqual(total, 2)
            self.assertEqual(store.count("JOB"), 2)

            rows = store.payloads("JOB")
            self.assertEqual(len(rows), 2)
            companies = {r["content"]["company"] for r in rows}
            self.assertIn("Anthropic", companies)
            self.assertIn("OpenAI", companies)

            # Check mapping log
            mapping = store.mapping_rows()
            job_mappings = [m for m in mapping if m["entity_type"] == "JOB_COMPANY"]
            self.assertGreaterEqual(len(job_mappings), 2)
            store.close()
