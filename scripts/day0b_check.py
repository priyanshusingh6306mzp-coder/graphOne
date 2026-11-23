#!/usr/bin/env python3
"""Day 0b: deeper checks that decide the final source list. Stdlib only.
Run: python scripts/day0b_check.py   (optional env: GITHUB_TOKEN, USER_AGENT)"""
import json, os, re, time, email.utils, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

UA = os.getenv("USER_AGENT", "GraphOneTrialBot/0.1 (+mailto:you@example.com)")
GH = os.getenv("GITHUB_TOKEN", "")
NOW = datetime.now(timezone.utc)


def get(url, headers=None, limit=600_000):
    h = {"User-Agent": UA, **(headers or {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25) as r:
            return r.status, r.read(limit).decode("utf-8", "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, (e.read(20000).decode("utf-8", "replace") if e.fp else ""), url
    except Exception as e:
        return 0, repr(e), url


def jget(url, headers=None):
    st, body, _ = get(url, headers)
    try:
        return st, json.loads(body)
    except Exception:
        return st, None


def parse_dt(s):
    s = (s or "").strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def feed_stats(url):
    st, body, final = get(url)
    dates = [parse_dt(m) for m in re.findall(r"<(?:pubDate|published|updated|dc:date)>([^<]+)<", body)]
    dates = [d for d in dates if d]
    fresh = sum(1 for d in dates if NOW - d <= timedelta(hours=24))
    newest = max(dates).isoformat() if dates else "n/a"
    return st, len(re.findall(r"<item[ >]|<entry[ >]", body)), fresh, newest


print("== 1. Papers: does HF daily_papers carry GitHub repo + stars?")
st, data = jget("https://huggingface.co/api/daily_papers?limit=5")
if isinstance(data, list) and data:
    print("item keys:", list(data[0].keys()))
    p = data[0].get("paper", {})
    print("paper keys:", list(p.keys()))
    for it in data:
        pp = it.get("paper", {})
        print(" ", pp.get("id"), "| repo:", pp.get("githubRepo"), "| stars:", pp.get("githubStars"))
else:
    print("status", st)

print("\n== 2. PwC .co page: what is it?")
st, body, final = get("https://paperswithcode.co/paper/98456")
t = re.search(r"<title>(.*?)</title>", body, re.S)
print("status", st, "| title:", (t.group(1).strip() if t else None), "| github links:", body.count("github.com"))

print("\n== 3. PwC archive data locations")
st, d = jget("https://huggingface.co/api/datasets?author=pwc-archive")
print("HF pwc-archive datasets:", [x.get("id") for x in d] if isinstance(d, list) else st)
gh_h = {"Authorization": f"Bearer {GH}"} if GH else {}
st, d = jget("https://api.github.com/repos/paperswithcode/paperswithcode-data/contents", gh_h)
print("GitHub repo files:", [x.get("name") for x in d][:15] if isinstance(d, list) else st)

print("\n== 4. Startups: YC open dataset (community mirror)")
for u in ["https://yc-oss.github.io/api/companies/all.json"]:
    st, body, _ = get(u, limit=3_000_000)
    try:
        arr = json.loads(body)
        print(u, st, "count>=", len(arr), "| keys:", list(arr[0].keys())[:14])
    except Exception:
        print(u, st, "not parseable (truncated or not JSON)", body[:80])

print("\n== 5. News feeds: items and how many are < 24h old")
feeds = {
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "MIT Tech Review AI": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "HF blog": "https://huggingface.co/blog/feed.xml",
    "VentureBeat AI (retry)": "https://venturebeat.com/category/ai/feed/",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "Wired AI": "https://www.wired.com/feed/tag/ai/latest/rss",
    "The Decoder": "https://the-decoder.com/feed/",
    "MarkTechPost": "https://www.marktechpost.com/feed/",
    "OpenAI news": "https://openai.com/news/rss.xml",
    "Google AI blog": "https://blog.google/technology/ai/rss/",
}
for name, u in feeds.items():
    if "retry" in name:
        time.sleep(10)
    st, n, fresh, newest = feed_stats(u)
    print(f"{name:24} status={st:<4} items={n:<4} <24h={fresh:<3} newest={newest}")
    time.sleep(1)

print("\n== 6. Jobs: ATS boards (count, date fields)")
boards = [
    ("greenhouse", "anthropic", "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs"),
    ("greenhouse", "databricks", "https://boards-api.greenhouse.io/v1/boards/databricks/jobs"),
    ("greenhouse", "scaleai", "https://boards-api.greenhouse.io/v1/boards/scaleai/jobs"),
    ("ashby", "openai", "https://api.ashbyhq.com/posting-api/job-board/openai"),
    ("ashby", "cohere", "https://api.ashbyhq.com/posting-api/job-board/cohere"),
    ("ashby", "perplexity", "https://api.ashbyhq.com/posting-api/job-board/perplexity"),
    ("lever", "palantir", "https://api.lever.co/v0/postings/palantir?mode=json"),
    ("lever", "anyscale", "https://api.lever.co/v0/postings/anyscale?mode=json"),
    ("lever", "mistral", "https://api.lever.co/v0/postings/mistral?mode=json"),
]
for kind, slug, u in boards:
    st, d = jget(u)
    jobs = d.get("jobs") if isinstance(d, dict) else d
    if isinstance(jobs, list) and jobs:
        j = jobs[0]
        datekeys = [k for k in j if re.search(r"date|publish|updated|created|posted", k, re.I)]
        print(f"{kind:10} {slug:12} status={st} jobs={len(jobs)} date-fields={datekeys} sample={ {k: j[k] for k in datekeys} }")
    else:
        print(f"{kind:10} {slug:12} status={st} jobs=0/none")
    time.sleep(0.5)
st, d = jget("https://hn.algolia.com/api/v1/search_by_date?tags=job&hitsPerPage=5")
print("HN jobs:", st, "hits:", len(d.get("hits", [])) if isinstance(d, dict) else None)
st, d = jget("https://remoteok.com/api")
if isinstance(d, list) and len(d) > 1:
    print("RemoteOK keys:", list(d[1].keys())[:15])
print("\nDone. Paste everything above.")
