# SPEC.md: GraphOne / FrontierAtlas AI Engineer Trial

Project context for coding agents. Follow this file exactly. If something is ambiguous, pick the simplest option and record it in `docs/DECISIONS.md`.

## 1. Goal
Build a scalable, fault-tolerant async ingestion pipeline that outputs 6 Google Sheet tabs plus a GitHub repo (README, `src/`, `architecture.pdf` max 3 pages).

## 2. Non-negotiable rules
1. **No hallucinated data.** Every record has a valid `source.url` that was actually fetched. The LLM may only extract values present in the fetched text. After extraction, verify each string field appears in the source text (normalized, fuzzy tolerance). If not, set it to null or drop the record.
2. **Never invent URLs, dates, stars, employee counts.** Unknown means `null`.
3. **Freshness:** Jobs and News contain only items published within 24h of run start (UTC).
4. **Uniqueness:** dedupe on normalized URL and on canonical entity name.
5. **Compliant crawling:** official APIs, RSS, sitemaps first. Honest User-Agent, obey robots.txt, per-domain rate limits, backoff on 403/429. No captcha solving, fingerprint spoofing, or proxy evasion.
6. Secrets only in `.env`; ship `.env.example`.

## 3. Schemas (exact field names; flat dot-notation headers in Sheets)

Common to every record (the prompt omits some; we add them for traceability): `schemaVersion` = "1.0", `recordType`, `source.name`, `source.url`, `collectedAt` (ISO-8601 UTC).

### STARTUP
`schemaVersion`, `recordType`="STARTUP", `source.name`, `source.url`, `content.entityName` (canonical), `content.data.employeeCount` (int|null), `collectedAt`

### PRODUCT
`schemaVersion`, `recordType`="PRODUCT", `source.name`, `source.url`, `content.startupName` (canonical), `content.pricingModel` (FREE|FREEMIUM|PAID|ENTERPRISE), `collectedAt`
(Also keep `content.productName` as an extra column so rows are meaningful.)

### RESEARCH_PAPER
`schemaVersion`, `recordType`="RESEARCH_PAPER", `content.title`, `content.authors` (array; Sheets: joined with "; "), `content.paper_url` (arXiv/PDF), `content.github_url` (null if none), `content.github_stars` (int|null; fetched live from GitHub API), `content.published_date` (ISO-8601), plus `source.name`, `source.url`, `collectedAt`

### JOB
`schemaVersion`, `recordType`="JOB", `content.company` (canonical), `content.date` (ISO-8601, within 24h), `content.is_remote` (bool), `content.role_family` (e.g. Engineering, Research, Product, Design, Sales, Other), plus `source.name`, `source.url`, `collectedAt`
(Extra columns allowed: `content.title`.)

### NEWS (schema NOT given in the prompt; our assumption, documented in README)
`schemaVersion`, `recordType`="NEWS", `source.name`, `source.url`, `content.title`, `content.date` (ISO-8601, within 24h), `content.summary` (from the article text only), `content.entities` (canonicalized orgs mentioned, optional), `collectedAt`

### ENTITY_MAPPING_LOG
`raw_name`, `canonical_name`, `match_method` (exact|alias|fuzzy|unresolved), `score`, `entity_type` (STARTUP|PRODUCT|JOB_COMPANY|NEWS), `source.url`, `collectedAt`

## 4. Sheet tabs and targets
| Tab | Target |
|---|---|
| Startups | ≥1,000 unique (aim 1,200+) |
| Products | ≥1,000 unique (aim 1,200+) |
| Research Papers | ≥1,000 unique, with github_stars (aim 1,200+; ≥90% with repo) |
| Jobs | all 24h-fresh jobs from 5 job sources |
| News | all 24h-fresh items from 5 news sources |
| Entity Mapping Log | every raw to canonical decision |

## 5. Repo layout
```
src/
  core/        http.py (aiohttp, semaphores, retries, robots, rate limit), store.py (SQLite), logging.py, config.py
  collectors/  papers.py, startups.py, products.py, jobs.py, news.py
  llm/         chain.py (Gemini Flash -> Groq Llama 3 -> DeepSeek), chunking.py, verify.py (grounding check), prompts.py
  resolver/    normalize.py, resolve.py, seed_startups.json (50 known AI startups)
  dates.py     published-date extraction + normalization + 24h filter
  export/      sheets.py
  models.py    Pydantic models matching section 3
  main.py      CLI: collect | extract | resolve | export | all
tests/
docs/          architecture.pdf, DECISIONS.md
README.md  .env.example  requirements.txt
```

## 6. Components

### HTTP core
asyncio + aiohttp; global semaphore plus per-domain semaphore and token bucket; timeouts; retries via tenacity (exponential backoff + jitter, honor `Retry-After`); conditional requests (ETag/If-Modified-Since) where supported; robots.txt cache; structured logs.

### Collectors (prefer structured sources)
- **Papers:** arXiv API (cs.AI, cs.LG, cs.CL), extract GitHub URL from abstract/comments/links, then GitHub REST API for stars (token auth, batch with GraphQL if needed). Also Hugging Face Daily Papers. Verify on Day 0 whether Papers with Code still resolves (the prompt's example URL uses `paperswithcode.co`); if not, document the fallback.
- **Startups/Products:** verify on Day 0: YC directory, Product Hunt API (needs OAuth), HN Algolia API (Show HN), GitHub search/trending, Hugging Face Spaces. Keep 2+ fallbacks.
- **Jobs (5):** Greenhouse/Lever/Ashby public board APIs, RemoteOK, WeWorkRemotely RSS.
- **News (5):** RSS/Atom feeds from 5 AI news sources; fetch the article full text with trafilatura.

### Dates
Order: structured field (RSS/API) then JSON-LD / `article:published_time` / `<time datetime>` then sitemap `lastmod` then dateparser (relative: "2 hours ago") then heuristic (first-seen hash vs last run; if undeterminable, exclude from the 24h tabs). Always store UTC ISO-8601. Test with 20+ cases.

### LLM chain
Gemini Flash -> Groq Llama 3 -> DeepSeek. JSON-mode output validated by Pydantic. Fall through on 429, 5xx, timeout, invalid JSON, or grounding failure.
- **Chunking (413 prevention):** clean text (trafilatura), count tokens (tiktoken or approximation), budget per model; keep title + lead + highest-density paragraphs; on a 413 or context error, halve the budget and retry.
- **429:** exponential backoff + jitter, per-provider token bucket, circuit breaker per provider.
- Cache results by content hash. Use the LLM only where rules/APIs cannot extract.

### Entity resolution
Normalize (case, punctuation, whitespace, suffixes Inc/Ltd/LLC/Corp/Labs/AI-spacing) then exact/alias match against the 50-startup seed list, then rapidfuzz above a threshold (start at 92), else `unresolved` (keep the cleaned name, never force a merge). Examples: "OpenAI", "OpenAI, Inc.", "Open AI" all become "OpenAI". Log every decision.

### Freshness / dedup
Unique key = hash(normalized URL). Store `first_seen`; skip already-processed URLs across runs.

## 7. Architecture doc (max 3 pages) must cover
1. **Scale to 500k:** sharded URL frontier (Redis/Kafka), stateless workers, autoscaling, source adapters (config-driven, no code change).
2. **413/429 at scale:** token budgeting, global distributed rate limiter, provider pools, fallback tiers, queue with retry/dead-letter.
3. **Freshness across nodes:** URL hash in Redis set/Bloom filter + Postgres unique constraint, idempotent writes.
4. **Storage:** Postgres (primary, JSONB), pgvector/Qdrant (semantic dedup), Neo4j (relationships: startup-product-paper-job-news).
5. **Anti-bot tiers:** Tier 1 APIs/RSS/sitemaps; Tier 2 polite aiohttp (UA, robots, rate limits, ETag, backoff); Tier 3 plain Playwright for JS-only pages that permit crawling, with circuit breakers; beyond that, licensed data or partnerships, or skip the source.

## 8. Acceptance checklist
- [ ] 1,000+ unique rows in Startups, Products, Papers (stars populated)
- [ ] Jobs/News contain only items from the last 24h; spot-checked
- [ ] 20 random rows per tab opened and verified against the source URL
- [ ] Mock tests: 429 backoff, 413 re-chunk, full fallback chain, date parsing, entity resolution
- [ ] Sheet is public, opens in incognito, headers use exact dot-notation
- [ ] README (setup + architecture overview), `architecture.pdf` (<=3 pages), clean logs
