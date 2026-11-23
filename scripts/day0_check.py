#!/usr/bin/env python3
"""Day 0 source verification. Stdlib only. Run: python scripts/day0_check.py
Optional env: GITHUB_TOKEN, USER_AGENT. Writes data/day0_report.json and prints a table.
Paste the printed table back so collectors can be written against what actually works."""
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

UA = os.getenv("USER_AGENT", "GraphOneTrialBot/0.1 (+mailto:you@example.com)")
GH = os.getenv("GITHUB_TOKEN", "")

TARGETS = [
    # (group, name, url, extra_headers)
    ("papers", "arXiv API", "http://export.arxiv.org/api/query?search_query=cat:cs.AI&start=0&max_results=5&sortBy=submittedDate", {}),
    ("papers", "HF daily papers API", "https://huggingface.co/api/daily_papers?limit=5", {}),
    ("papers", "HF trending papers page", "https://huggingface.co/papers/trending", {}),
    ("papers", "PwC .com (redirect?)", "https://paperswithcode.com/", {}),
    ("papers", "PwC .co example", "https://paperswithcode.co/paper/98456", {}),
    ("papers", "PwC archive repo", "https://api.github.com/repos/paperswithcode/paperswithcode-data", {}),
    ("github", "GitHub rate limit", "https://api.github.com/rate_limit", {"Authorization": f"Bearer {GH}"} if GH else {}),
    ("github", "GitHub repo stars", "https://api.github.com/repos/huggingface/transformers", {"Authorization": f"Bearer {GH}"} if GH else {}),
    ("startups", "HN Algolia (Show HN)", "https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage=5", {}),
    ("startups", "YC companies page", "https://www.ycombinator.com/companies", {}),
    ("startups", "YC company sample", "https://www.ycombinator.com/companies/openai", {}),
    ("startups", "GitHub search repos", "https://api.github.com/search/repositories?q=topic:llm&sort=stars&per_page=5", {"Authorization": f"Bearer {GH}"} if GH else {}),
    ("startups", "HF spaces API", "https://huggingface.co/api/spaces?sort=trendingScore&limit=5", {}),
    ("products", "Product Hunt (site)", "https://www.producthunt.com/", {}),
    ("products", "Product Hunt GraphQL (needs token)", "https://api.producthunt.com/v2/api/graphql", {}),
    ("jobs", "Greenhouse board (anthropic)", "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs", {}),
    ("jobs", "Lever postings (mistral)", "https://api.lever.co/v0/postings/mistral?mode=json", {}),
    ("jobs", "Ashby board (openai)", "https://api.ashbyhq.com/posting-api/job-board/openai", {}),
    ("jobs", "RemoteOK API", "https://remoteok.com/api", {}),
    ("jobs", "WeWorkRemotely RSS", "https://weworkremotely.com/categories/remote-programming-jobs.rss", {}),
    ("news", "TechCrunch AI RSS", "https://techcrunch.com/category/artificial-intelligence/feed/", {}),
    ("news", "VentureBeat AI RSS", "https://venturebeat.com/category/ai/feed/", {}),
    ("news", "The Verge AI RSS", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", {}),
    ("news", "MIT Tech Review AI RSS", "https://www.technologyreview.com/topic/artificial-intelligence/feed", {}),
    ("news", "HF blog RSS", "https://huggingface.co/blog/feed.xml", {}),
    ("news", "HN front page (Algolia)", "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=AI&hitsPerPage=5", {}),
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(400_000).decode("utf-8", "replace")
            return r.status, r.geturl(), dict(r.headers), body, time.time() - t, None
    except urllib.error.HTTPError as e:
        body = e.read(50_000).decode("utf-8", "replace") if e.fp else ""
        return e.code, url, dict(e.headers or {}), body, time.time() - t, None
    except Exception as e:  # network / DNS / timeout
        return 0, url, {}, "", time.time() - t, repr(e)


def summarize(body):
    s = body.lstrip()
    if s[:1] in "[{":
        try:
            data = json.loads(body)
            if isinstance(data, list):
                return f"json list len={len(data)}"
            return "json keys=" + ",".join(list(data)[:6])
        except Exception:
            return "json (truncated)"
    n = len(re.findall(r"<item[ >]|<entry[ >]", body))
    return f"xml/html items={n}" if n else f"html/text {len(body)}B"


def robots_ok(url):
    p = urlsplit(url)
    rp = RobotFileParser()
    try:
        req = urllib.request.Request(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            rp.parse(r.read().decode("utf-8", "replace").splitlines())
        return "allow" if rp.can_fetch(UA, url) else "DISALLOW"
    except urllib.error.HTTPError as e:
        return "allow(no robots)" if 400 <= e.code < 500 else f"robots {e.code}"
    except Exception:
        return "robots n/a"


def main():
    os.makedirs("data", exist_ok=True)
    report = []
    print(f"{'group':9} {'source':34} {'status':>6}  {'robots':16} summary")
    for group, name, url, hdr in TARGETS:
        status, final, headers, body, dt, err = fetch(url, hdr)
        flags = []
        if final.rstrip("/") != url.rstrip("/"):
            flags.append(f"->{final}")
        low = body[:3000].lower()
        if headers.get("cf-mitigated") or ("just a moment" in low and status in (403, 503)):
            flags.append("CLOUDFLARE-CHALLENGE")
        if headers.get("x-ratelimit-remaining"):
            flags.append(f"gh-remaining={headers['x-ratelimit-remaining']}")
        summ = err or summarize(body)
        rob = robots_ok(url) if group in ("news", "jobs", "startups", "products", "papers") and "api." not in url and "/api" not in url else "n/a (API)"
        print(f"{group:9} {name:34} {status:>6}  {rob:16} {summ} {' '.join(flags)}")
        report.append({"group": group, "name": name, "url": url, "status": status, "final_url": final,
                       "robots": rob, "summary": summ, "flags": flags, "seconds": round(dt, 2),
                       "sample": body[:300]})
        time.sleep(0.3)
    with open("data/day0_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\nSaved data/day0_report.json")
    print("Env: GITHUB_TOKEN", "set" if GH else "NOT set (60 req/h limit)")
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        print(f"Env: {k}", "set" if os.getenv(k) else "NOT set")


if __name__ == "__main__":
    main()
