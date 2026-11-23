import unittest

try:
    from src.collectors.papers import build_record
    from src.models import Source, Startup, StartupContent
    from src.export.flatten import COLUMNS, to_row
    HAVE_DEPS = True
except ImportError:  # pydantic/aiohttp not installed (e.g. offline sandbox)
    HAVE_DEPS = False


@unittest.skipUnless(HAVE_DEPS, "pydantic/aiohttp not installed")
class ModelTests(unittest.TestCase):
    def test_startup_row(self):
        s = Startup(source=Source(name="YC", url="https://www.ycombinator.com/companies/x"),
                    content=StartupContent(entityName="X"))
        row = to_row(s.model_dump(mode="json"), "STARTUP")
        self.assertEqual(len(row), len(COLUMNS["STARTUP"]))
        self.assertEqual(row[COLUMNS["STARTUP"].index("recordType")], "STARTUP")
        self.assertEqual(row[COLUMNS["STARTUP"].index("content.data.employeeCount")], "")

    def test_paper_record(self):
        meta = {"arxiv_id": "1810.10559", "title": "T", "authors": ["A", "B"],
                "published": "2018-10-24T10:00:00Z"}
        rec = build_record(meta, ("https://github.com/p/q", 12))
        row = to_row(rec.model_dump(mode="json"), "RESEARCH_PAPER")
        cols = COLUMNS["RESEARCH_PAPER"]
        self.assertEqual(row[cols.index("content.authors")], "A; B")
        self.assertEqual(row[cols.index("content.github_stars")], 12)
        self.assertEqual(row[cols.index("source.url")], "https://arxiv.org/abs/1810.10559")
        self.assertTrue(row[cols.index("content.published_date")].startswith("2018-10-24T10:00:00"))

    def test_bad_source_url_rejected(self):
        with self.assertRaises(Exception):
            Source(name="x", url="ftp://nope")


if __name__ == "__main__":
    unittest.main()
