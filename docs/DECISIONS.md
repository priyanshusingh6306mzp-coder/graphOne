# Decisions & assumptions

1. **News schema** is not defined in the prompt. Assumed: schemaVersion, recordType="NEWS", source.name, source.url, content.title, content.date, content.summary, content.entities, collectedAt.
2. **source.* and collectedAt on every record.** The prompt omits them for Research Paper and Job; added so every record traces to a URL.
3. **Dot-notation Sheet headers.** Models stay nested (`record.source.url`); `export/flatten.py` flattens the `model_dump(mode="json")` dict into exact headers (`source.url`, `content.data.employeeCount`). Lists (authors, entities) are joined with "; ".
4. **Papers with Code.** The prompt's example uses `paperswithcode.co`. Reports say paperswithcode.com was retired in July 2025 and redirected to Hugging Face Trending Papers. Day 0: check both domains with curl (status, redirect target) and record the result here. Primary paper source = arXiv API + Hugging Face Daily Papers + GitHub API; PwC archive data (github.com/paperswithcode/paperswithcode-data) is an optional bulk fallback.
5. **Storage for the trial:** SQLite (single file, zero setup). Production: Postgres + pgvector/Qdrant + Neo4j (see architecture.pdf).
6. **Crawling policy:** official APIs/RSS first; robots.txt respected for crawled pages; per-domain pacing; circuit breaker on repeated 403/429. No captcha solving or fingerprint spoofing.

## Day 0 findings (verified 2026-11-21)
7. **Papers with Code:** `paperswithcode.com` redirects to Hugging Face Trending Papers; `paperswithcode.co` serves a relaunched benchmarks site (not the old `/paper/<id>` pages). Not used as a data source. Paper->repo links come from the Hugging Face `pwc-archive/links-between-paper-and-code` dataset (300k rows via datasets-server); metadata from the arXiv API; stars live from the GitHub API. HF `daily_papers` (carries `githubRepo` + `githubStars`) is the source for recent papers.
8. **Papers scope:** AI/ML only, by arXiv primary category (cs.AI, cs.LG, cs.CL, cs.CV, cs.NE, cs.IR, cs.RO, cs.MA, stat.ML). Repos that no longer exist (GitHub 404) are dropped. No LLM is used for papers, so none of those fields can be hallucinated.
9. **Startups:** `yc-oss` mirror of the YC directory (6,406 companies, ~2.7k AI-related by tags/industry/one-liner). `team_size` -> `employeeCount`; `source.url` = original ycombinator.com company page.
10. **Products:** derived from startups' own websites/pricing pages. `pricingModel` by keyword rules first, LLM only for ambiguous pages, and every LLM answer needs an evidence quote found in the page text. Product Hunt is behind a Cloudflare challenge and its API needs OAuth: not used.
11. **News (5):** The Verge AI, The Decoder, TechCrunch AI, Wired AI, MIT Technology Review AI (RSS + full-text fetch). VentureBeat returned 429 and MarkTechPost 403: dropped, not circumvented.
12. **Jobs (5):** Greenhouse, Ashby and Lever (each across many AI companies), plus RemoteOK and WeWorkRemotely filtered to AI/ML roles. Freshness fields: Greenhouse `first_published`, Ashby `publishedAt`, Lever `createdAt` (ms), RemoteOK `date`/`epoch`, WWR RSS `pubDate`.

## Day 1 results (2026-11-21)
13. **Products:** full crawl of 3,164 startups (AI + YC B2B) took ~9 minutes: 879 products by rules (446 FREEMIUM, 381 PAID, 40 ENTERPRISE, 12 FREE), 570 ambiguous pages parked for the grounded LLM stage. Manual audit of 25 random rows against their evidence: 23 correct, 1 wrong (a "$0 today" free trial, now excluded), 1 shaky.
14. **LLM integration uses plain REST (aiohttp), not vendor SDKs.** One code path for Gemini (generateContent), Groq and DeepSeek (OpenAI-compatible), so 429/413 are visible as HTTP statuses and handled by the same backoff+jitter layer, and the chain is unit-testable with an injected network function. Model names are configurable via env.
15. **Anti-hallucination for LLM output:** the model must return an exact quote from the fetched text; the quote must appear verbatim in that text and be consistent with the label, otherwise the record is discarded. Structured fields (company, dates, stars) come from APIs/feeds, not the LLM, wherever possible.

## Day 2 results & PR 5A Jobs implementation (2026-11-22)
16. **Jobs 5 board families:**
    - **Greenhouse:** (`anthropic`, `scaleai`, `databricks`) Uses `first_published` strictly; `updated_at` is never used so re-indexed stale postings are excluded. Location and company_name extracted directly.
    - **Ashby:** (`openai`, `cohere`, `perplexity`) Uses `publishedAt`, preserves `descriptionPlain` without truncation, derives `is_remote` from `isRemote` boolean and `workplaceType`.
    - **Lever:** (`palantir`) Uses `createdAt` (epoch ms parsed automatically into aware UTC datetime), `workplaceType` and categories.
    - **RemoteOK:** (`https://remoteok.com/api`) Skips index 0 (legal notice terms); filters to AI/ML positions and tags; `is_remote=True`.
    - **WeWorkRemotely RSS:** (`https://weworkremotely.com/remote-jobs.rss`) Parses RFC-822 `pubDate`, extracts `Company: Title` from RSS title tag, filters to AI/ML positions and categories; `is_remote=True`.
17. **Freshness & Classification:** Enforces strict 24h UTC window via `src.dates.is_fresh(..., now, 24)`. Role family (`Engineering`, `Research`, `Product`, `Design`, `Sales`, `Operations`, `Other`) is derived deterministically first; ambiguous cases fall back to the LLM chain with strict quote grounding verification (`is_grounded`), falling back to `Other`. Entity mapping is recorded in `mapping_log` with `entity_type="JOB_COMPANY"`.

## Day 2 results & PR 5B News implementation (2026-11-22)
18. **News 5 verified feeds:**
    - The Verge AI (Atom feed)
    - The Decoder (RSS 2.0)
    - TechCrunch AI (RSS 2.0)
    - Wired AI (RSS 2.0)
    - MIT Technology Review AI (RSS 2.0)
    - *VentureBeat* (429) and *MarkTechPost* (403) are strictly omitted, not scraped or bypassed.
19. **Article Extraction & Grounded Summarization:**
    - Publication dates are extracted from feed metadata (or HTML JSON-LD/meta as fallback) and strictly filtered with `src.dates.is_fresh(pub_dt, now, 24)`.
    - Full article body text is cleanly extracted using `trafilatura`.
    - `content.summary` and `content.entities` are extracted via `LLMChain` (Gemini Flash -> Groq chat completions).
    - Every extracted field is verified against the article text using `src.llm.verify.is_grounded`: ungrounded summaries are nulled (`None`), and any ungrounded entities are dropped (zero hallucinated data). Grounded entities are logged in `mapping_log` under `entity_type="NEWS"`.

## Day 2 results & PR 5C Entity Resolution implementation (2026-11-22)
20. **Seed List Construction (`seed_startups.json`):**
    - 50 entries constructed directly from frequent real entities present in `data/pipeline.db` across Startups, Jobs, and News (Anthropic, OpenAI, Scale AI, Databricks, Cohere, Perplexity, Mistral AI, Hugging Face, Palantir, Celonis, GitLab, Glean, Nvidia, Google, Dropbox, etc.).
    - Aliases strictly represent plain spelling and legal entity variants (e.g., `Open AI`, `OpenAI, Inc.`, `Anthropic PBC`).
    - High-ambiguity candidates that could cause false-positive collisions with distinct startups (e.g., `Paragon` vs `Parsagon`) were excluded in favor of distinct AI companies like `Decohere`.
21. **Shared Normalization (`src/resolver/normalize.py`):**
    - Shared across Startup (`content.entityName`), Product (`content.startupName`), Job (`content.company`), and News (`content.entities`).
    - Strips legal corporate suffixes (`Inc`, `LLC`, `Ltd`, `Corp`, `PBC`, `GmbH`, `SAS`, `Co.`, `Labs`) only when preceded by comma or whitespace, ensuring words like `ElevenLabs` and `GitLab` are never corrupted.
    - Key normalization lowercases and strips non-alphanumeric punctuation and spacing, collapsing `"OpenAI"`, `"OpenAI, Inc."`, and `"Open AI"` to `"openai"`.
22. **Fuzzy Guards & Precision:**
    - Enforces that BOTH candidate and seed normalized keys have `len >= 6`.
    - Requires rapidfuzz match score `>= 92.0`.
    - Requires runner-up margin `>= 3.0` points (`best_score - runner_up_score >= 3.0`), rejecting ambiguous matches.
23. **Cross-Dataset Clustering & Clean Canonical Selection:**
    - Groups non-seed names by normalized alphanumeric key across the dataset.
    - Canonical selection prioritizes: (1) higher occurrence frequency across records, (2) Title Case over all-caps/all-lower (`Henry` over `HENRY`, `Flower` over `FLOWER`, `Sieve` over `sieve`, `Hera` over `HERA`), (3) stripping leading punctuation (`Wave` over `.Wave`).
24. **Strict Idempotency & Mapping Log Composite Key:**
    - To guarantee repeatability, original raw entity names are backed up to the `aux` table (`raw_name_<RECORD_TYPE>`) keyed by source URL before rewriting any record payloads. Every subsequent resolution pass reads from this stored raw name.
    - `mapping_log` primary key updated to `(entity_type, raw_name, source_url)`, allowing multiple distinct entities sharing a name (e.g. multiple "Plane" startups) to be recorded without clobbering each other. Recreated/cleared idempotently at the start of `resolve`.

## Day 2 results & PR 5D Sheets Export & CSV Fallback implementation (2026-11-22)
25. **Tab Structure & Exact Dot-Notation Flattening (`src/export/flatten.py`):**
    - 6 exact tabs: `Startups`, `Products`, `Research Papers`, `Jobs`, `News`, `Entity Mapping Log`.
    - Every row is serialized through `to_row(record, record_type)` ensuring column headers strictly follow dot-notation (`COLUMNS`), list fields (authors, entities) are joined with `"; "`, `None` values become empty strings `""`, and timestamps are standardized ISO-8601 strings.
26. **Entity Mapping Log Ordering:**
    - `Entity Mapping Log` rows are ordered non-identity first (`raw_name != canonical_name`) so all active entity resolution merges appear at the top of the tab, followed by unchanged identity rows.
27. **Freshness Filter at Export Time:**
    - `Jobs` and `News` items are strictly evaluated with `dates.is_fresh(content.date, now, 24)` at export time. Any stale items older than 24 hours UTC are dropped and reported in export logs.
28. **Google Sheets Batch Writing, Quota Retries & Dual CSV Fallback:**
    - Batch writes use chunks of 500 rows with clear-then-write idempotency, `value_input_option="RAW"`, and frozen header row (`ws.freeze(rows=1)`).
    - Rate limits (429/quota errors) are handled with exponential backoff and jitter reusing `src.core.retry.compute_backoff`.
    - Strict read-back verification asserts `header == COLUMNS` and `row_count == expected` after writing.
    - Full CSV fallback export is simultaneously written to `data/export/*.csv`, guaranteeing complete data accessibility regardless of service account credential availability.
