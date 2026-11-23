#!/usr/bin/env python3
"""Day 0c: exact schemas of the sources we will actually use. Stdlib only.
Run: python scripts/day0c_check.py"""
import json, os, re, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

UA = os.getenv("USER_AGENT", "GraphOneTrialBot/0.1 (+mailto:you@example.com)")
NOW = datetime.now(timezone.utc)
BIG = 40_000_000


def get(url, limit=BIG):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
            return r.status, r.read(limit).decode("utf-8", "replace"), len(r.headers.get("Content-Length") or "")
    except urllib.error.HTTPError as e:
        return e.code, "", 0
    except Exception as e:
        return 0, repr(e), 0


def jget(url):
    st, body, _ = get(url)
    try:
        return st, json.loads(body)
    except Exception:
        return st, None


def short(v, n=110):
    s = json.dumps(v, default=str) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "..."


print("== 1. YC open dataset (yc-oss)")
st, arr = jget("https://yc-oss.github.io/api/companies/all.json")
if isinstance(arr, list) and arr:
    print("status", st, "| total companies:", len(arr))
    print("keys:", list(arr[0].keys()))
    ex = arr[0]
    for k in ("name", "slug", "website", "one_liner", "team_size", "industries", "tags", "status", "url", "batch", "launched_at"):
        if k in ex:
            print(f"  {k}: {short(ex[k])}")
    ai = [c for c in arr if re.search(r"\bAI\b|artificial intelligence|machine learning|LLM",
                                       json.dumps([c.get('tags'), c.get('industries'), c.get('one_liner')]), re.I)]
    print("AI-related (rough):", len(ai), "| with team_size:", sum(1 for c in ai if c.get("team_size")),
          "| with website:", sum(1 for c in ai if c.get("website")))
else:
    print("status", st, "could not parse")

print("\n== 2. HF pwc-archive datasets (schemas)")
for ds in ["pwc-archive/links-between-paper-and-code", "pwc-archive/papers-with-abstracts"]:
    st, sp = jget(f"https://datasets-server.huggingface.co/splits?dataset={ds}")
    if not sp or "splits" not in sp:
        print(ds, "splits status", st)
        continue
    s0 = sp["splits"][0]
    cfg, split = s0["config"], s0["split"]
    st, fr = jget(f"https://datasets-server.huggingface.co/first-rows?dataset={ds}&config={cfg}&split={split}")
    print(ds, "| config:", cfg, "| split:", split)
    if fr:
        print("  columns:", [f["name"] for f in fr.get("features", [])])
        for row in fr.get("rows", [])[:2]:
            print("  row:", {k: short(v, 80) for k, v in row["row"].items()})
    st, sz = jget(f"https://datasets-server.huggingface.co/size?dataset={ds}")
    if sz:
        print("  num_rows:", sz.get("size", {}).get("dataset", {}).get("num_rows"))
    time.sleep(1)

print("\n== 3. Job boards: full download, date fields, count < 24h")


def fresh_count(items, getter):
    n = 0
    for it in items:
        d = getter(it)
        if d and NOW - d <= timedelta(hours=24):
            n += 1
    return n


def iso(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


for slug in ["anthropic", "scaleai", "databricks"]:
    st, d = jget(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs = d.get("jobs") if isinstance(d, dict) else None
    if jobs:
        print(f"greenhouse {slug}: jobs={len(jobs)} first_published<24h={fresh_count(jobs, lambda j: iso(j.get('first_published')))}"
              f" | keys={list(jobs[0].keys())}")
    else:
        print(f"greenhouse {slug}: status {st} none")
for slug in ["openai", "cohere", "perplexity"]:
    st, d = jget(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    jobs = d.get("jobs") if isinstance(d, dict) else None
    if jobs:
        j = jobs[0]
        dk = [k for k in j if re.search(r"date|publish|updated|created|posted", k, re.I)]
        key = dk[0] if dk else None
        print(f"ashby {slug}: jobs={len(jobs)} date-fields={dk} <24h={fresh_count(jobs, lambda x: iso(x.get(key))) if key else 'n/a'}"
              f" | keys={list(j.keys())}")
    else:
        print(f"ashby {slug}: status {st} none")
st, d = jget("https://api.lever.co/v0/postings/palantir?mode=json")
if isinstance(d, list) and d:
    print("lever palantir: jobs=", len(d), "| keys=", list(d[0].keys()),
          "| createdAt<24h=", fresh_count(d, lambda x: datetime.fromtimestamp(x["createdAt"] / 1000, timezone.utc) if x.get("createdAt") else None))
st, d = jget("https://remoteok.com/api")
if isinstance(d, list) and len(d) > 1:
    print("remoteok: jobs=", len(d) - 1, "| keys=", list(d[1].keys())[:14],
          "| date<24h=", fresh_count(d[1:], lambda x: iso(x.get("date"))))
print("\nDone. Paste everything above.")
