# GraphOne / FrontierAtlas: AI Engineering Ingestion Pipeline

[![Tests](https://img.shields.io/badge/Unit%20Tests-101%2F101%20Passing-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.14-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/Architecture-3--Page%20PDF%20Compliant-orange)](docs/architecture.pdf)

An enterprise-grade, fault-tolerant, asynchronous data ingestion and entity resolution pipeline designed for the **GraphOne / FrontierAtlas AI Engineer Assessment**. The system discovers, crawls, extracts, normalizes, resolves, and exports comprehensive intelligence across the global AI ecosystem covering **Startups, Products, Research Papers, Job Postings, and News Articles**.

Outputs to **6 Google Sheet tabs** (with automatic local multi-CSV fallback) and guarantees **zero hallucinations, strict 24h freshness windows, and full entity resolution idempotency**.

---

## Table of Contents
- [1. Quick Start & Setup](#1-quick-start--setup)
- [2. Environment Configuration (`.env`)](#2-environment-configuration-env)
- [3. CLI Commands & Pipeline Workflow](#3-cli-commands--pipeline-workflow)
- [4. Data Schemas & Flattening Standards](#4-data-schemas--flattening-standards)
- [5. Entity Resolution Architecture & Assumptions](#5-entity-resolution-architecture--assumptions)
- [6. Compliant Crawling Policy & Excluded Sources](#6-compliant-crawling-policy--excluded-sources)
- [7. System Architecture & Scaling Overview](#7-system-architecture--scaling-overview)
- [8. Testing & Verification](#8-testing--verification)

---

## 1. Quick Start & Setup

### Prerequisites
- Python 3.10+ (tested through Python 3.14 on Linux, macOS, and Windows)
- Git
- Valid API keys for GitHub and at least one LLM provider (Google Gemini or Groq)

### Installation
```bash
# 1. Clone repository and navigate to workspace
git clone https://github.com/your-org/graphone-pipeline.git
cd graphone-pipeline

# 2. Create and activate virtual environment
python -m venv .venv

# On Linux / macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# 3. Install production and development dependencies
pip install -r requirements.txt

# 4. Configure environment secrets
cp .env.example .env
# Edit .env and supply your API keys (see Section 2 below)

# 5. Run test suite to verify installation
python -m unittest discover -s tests -t . -v
```

---

## 2. Environment Configuration (`.env`)

All configuration is loaded via `python-dotenv`. Copy `.env.example` to `.env` and configure the following parameters:

| Variable | Required | Description | Example / Default |
|---|---|---|---|
| `USER_AGENT` | **Yes** | Transparent User-Agent identifying the crawler and contact email. | `GraphOneTrialBot/0.1 (+mailto:ops@frontieratlas.com)` |
| `GITHUB_TOKEN` | Recommended | GitHub Personal Access Token for fetching research paper repository star counts. | `ghp_xxxxxxxxxxxxxxxxxxxx` |
| `GEMINI_API_KEY` | Recommended | Google Gemini API Key (primary LLM extraction tier). | `AIzaSy...` |
| `GROQ_API_KEY` | Recommended | Groq Cloud API Key (high-speed fallback LLM tier). | `gsk_...` |
| `DEEPSEEK_API_KEY` | Optional | DeepSeek API Key (tertiary LLM tier). | `sk-...` |
| `GEMINI_MODEL` | Optional | Primary Gemini model alias. | `gemini-flash-lite-latest` |
| `GEMINI_FALLBACK_MODEL` | Optional | Secondary Gemini model alias. | `gemini-flash-latest` |
| `GROQ_MODEL` | Optional | Primary Groq chat model. | `openai/gpt-oss-120b` or `llama-3.3-70b-versatile` |
| `GROQ_FALLBACK_MODEL` | Optional | Secondary Groq chat model. | `openai/gpt-oss-20b` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Optional | Path to Google Service Account JSON credentials for Sheets export. | `./service_account.json` |
| `SHEET_ID` | Optional | Target Google Sheet ID for multi-tab export. | `1RllUXF3phP94BKN20Gf-Vv6vNa86-wJXOPx9mLNsJBI` |

> **Note on LLM Architecture:** The LLM integration uses direct REST over asynchronous `aiohttp` without vendor SDK wrappers. If a provider returns HTTP 404 (model retired/unknown) or HTTP 429 daily quota exhaustion, the engine automatically disables that tier for the duration of the run and routes requests seamlessly down the fallback chain.

---

## 3. CLI Commands & Pipeline Workflow

The pipeline is driven via `python -m src.main <cmd> [options]`:

```
CLI: python -m src.main <startups|papers|products|llm-pricing|jobs|news|resolve|export> [options]
```

### Ingestion Commands

#### 1. Collect Startups
Ingests startups from verified directories (YC OSS dataset with tag/industry filtering), mapping company profiles, URLs, and employee counts.
```bash
python -m src.main startups --scope ai --limit 1500
# Available scopes: 'ai' (default, ~2,700 AI startups), 'ai+b2b' (3,164 companies), 'all' (6,400+ companies)
```

#### 2. Collect Research Papers
Collects AI/ML research papers from the arXiv API (categories `cs.AI`, `cs.LG`, `cs.CL`, `cs.CV`, `cs.NE`, `stat.ML`) and Hugging Face Daily Papers, extracts associated GitHub repositories, and fetches live star counts via GitHub REST API.
```bash
python -m src.main papers --target 1200
```

#### 3. Collect Products & Rule-Based Pricing
Crawls startup homepages and pricing routes, extracting product offerings and applying deterministic keyword heuristics (`FREE`, `FREEMIUM`, `PAID`, `ENTERPRISE`). Ambiguous pricing pages are parked for LLM processing.
```bash
python -m src.main products --limit 2000
# Pass --reset to clear previous product records before running
```

#### 4. Grounded LLM Pricing Classifier
Processes ambiguous pricing pages using the fault-tolerant LLM chain. Enforces strict quote verification: the LLM must return an exact verbatim quote from the page matching the pricing tier. If the quote is missing or hallucinated, the record is rejected.
```bash
python -m src.main llm-pricing --limit 500
# Pass --reprocess to re-evaluate cached results against updated prompt rules
```

#### 5. Collect 24h-Fresh Job Postings
Fetches active job openings across 5 major job board families (Greenhouse, Ashby, Lever, RemoteOK, and WeWorkRemotely). Enforces a strict 24-hour UTC freshness window using `first_published` / `publishedAt` (never `updated_at`). Derives role family deterministically (`Engineering`, `Research`, `Product`, `Design`, `Sales`, `Operations`, `Other`).
```bash
python -m src.main jobs
```

#### 6. Collect 24h-Fresh AI News
Ingests AI news from 5 verified syndication feeds (The Verge AI, The Decoder, TechCrunch AI, Wired AI, MIT Technology Review AI). Extracts clean full text using `trafilatura`, generates grounded summaries and entity extractions via LLM, and nulls/drops any ungrounded strings.
```bash
python -m src.main news
```

### Entity Resolution & Export Commands

#### 7. Entity Resolution Demo
Runs an offline resolution preview over messy benchmark sample strings (e.g., `"Open AI"`, `"Anthropic PBC"`, `"Matidor.com"`, `"HENRY"`, `".Wave"`, `"Cognition Labs"`), demonstrating normalization, exact/alias matching, and collision guards without modifying any database records.
```bash
python -m src.main resolve --demo
```

#### 8. Execute Database Entity Resolution
Performs idempotent entity resolution across all records in `data/pipeline.db` (`STARTUP`, `PRODUCT`, `JOB_COMPANY`, `NEWS`). Backs up original raw names into the `aux` table, normalizes display names, links products to parent startups, applies fuzzy guards (score $\ge 92$, margin $\ge 3.0$), and populates `ENTITY_MAPPING_LOG`.
```bash
# Dry run mode (read-only inspection):
python -m src.main resolve --dry-run

# Production execution (idempotent write):
python -m src.main resolve
```

#### 9. Export to Google Sheets & CSV
Generates the final 6 tabs with frozen headers, dot-notation columns, and `; `-joined arrays. Filters dynamic items (`JOB` and `NEWS`) strictly to $\le 24$h UTC. If Google Service Account credentials are not supplied, it automatically writes full CSV exports to `data/export/*.csv`.
```bash
python -m src.main export --sheet-id "1RllUXF3phP94BKN20Gf-Vv6vNa86-wJXOPx9mLNsJBI" --export-dir "data/export"
```

---

## 4. Data Schemas & Flattening Standards

Every record includes traceability fields: `schemaVersion="1.0"`, `recordType`, `source.name`, `source.url`, and `collectedAt` (ISO-8601 UTC). When exported to Google Sheets or CSV, nested JSON structures are flattened into dot-notation headers via `src/export/flatten.py`:

```
record.source.url           -> "source.url"
record.content.entityName   -> "content.entityName"
record.content.data.employeeCount -> "content.data.employeeCount"
```

### Record Definitions

| Record Type | Key Dotted Columns | Types / Rules | Description |
|---|---|---|---|
| **`STARTUP`** | `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.entityName`, `content.data.employeeCount`, `collectedAt` | `content.entityName`: str (canonical)<br/>`content.data.employeeCount`: int \| null | AI startup profiles, canonical company name, and team size. |
| **`PRODUCT`** | `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.startupName`, `content.productName`, `content.pricingModel`, `collectedAt` | `content.startupName`: str (canonical)<br/>`content.pricingModel`: `FREE` \| `FREEMIUM` \| `PAID` \| `ENTERPRISE` | AI product offerings linked to parent startups with verified pricing models. |
| **`RESEARCH_PAPER`** | `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.title`, `content.authors`, `content.paper_url`, `content.github_url`, `content.github_stars`, `content.published_date`, `collectedAt` | `content.authors`: list (joined with `"; "`)<br/>`content.github_stars`: int \| null<br/>`content.published_date`: ISO-8601 UTC | ArXiv and Hugging Face AI papers with verified code repos and live GitHub stars. |
| **`JOB`** | `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.company`, `content.title`, `content.date`, `content.is_remote`, `content.role_family`, `collectedAt` | `content.company`: str (canonical)<br/>`content.date`: ISO-8601 UTC ($\le 24$h)<br/>`content.is_remote`: bool<br/>`content.role_family`: str | Active tech openings across Greenhouse, Ashby, Lever, RemoteOK, and WWR. |
| **`NEWS`** | `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.title`, `content.date`, `content.summary`, `content.entities`, `collectedAt` | `content.date`: ISO-8601 UTC ($\le 24$h)<br/>`content.summary`: str (grounded)<br/>`content.entities`: list (canonicalized) | AI news articles with grounded summaries and extracted organization entities. |
| **`ENTITY_MAPPING_LOG`** | `raw_name`, `canonical_name`, `match_method`, `score`, `entity_type`, `source.url`, `collectedAt` | `match_method`: `exact` \| `alias` \| `fuzzy` \| `unresolved`<br/>`score`: float \| null | Audit log recording every resolution decision across all entity types. |

---

## 5. Entity Resolution Architecture & Assumptions

Entity resolution normalizes raw names across Startups (`content.entityName`), Products (`content.startupName`), Jobs (`content.company`), and News (`content.entities`) into a unified canonical taxonomy.

### Key Rules & Guards

1. **Collision Guard (Never Merge Distinct Startups):**
   Startups sharing similar names or identical normalized keys with different source URLs (e.g. `'HENRY'` vs `'Henry'`, `'.Wave'` vs `'Wave'`, `'aSim'` vs `'Asim'`) are **never merged**. Distinct URLs indicate distinct legal entities. If conservative cleaning would merge them, both retain their distinct raw names as canonical.
   - **Verification Metric:** `scripts/mapping_report.py` asserts `SUSPICIOUS: 0`.

2. **Conservative Display Name Cleaning:**
   `conservative_clean_name` strips legal corporate suffixes (`Inc`, `LLC`, `Ltd`, `Corp`, `PBC`, `GmbH`, `SA`), trailing `.com`, and stray punctuation, and collapses whitespace. **Brand tokens such as `Labs` and `AI` are strictly preserved** (e.g. `Cognition Labs` and `Mistral AI`).

3. **Parent Startup Linkage for Products:**
   Products resolve to their parent startup's canonical entity name via `startup_meta` (or domain matching) first, rather than blind string resolution. This guarantees **zero mismatches** between `product.content.startupName` and `startup.content.entityName`.

4. **Curated 50-Startup Seed Taxonomy (`seed_startups.json`):**
   Matches real entities found in the dataset (OpenAI, Anthropic, Scale AI, Databricks, Cohere, Perplexity, Mistral AI, Hugging Face, etc.) with explicit alias variants (`"Open AI"`, `"OpenAI, Inc."`, `"Anthropic PBC"`).

5. **Guarded Fuzzy Matching:**
   Fuzzy resolution via RapidFuzz requires:
   - Candidate and seed normalized key length $\ge 6$ characters.
   - Similarity score $\ge 92.0$.
   - Runner-up margin $\ge 3.0$ points (`best_score - runner_up_score >= 3.0`).

6. **Full Idempotency & Composite Mapping Log:**
   Before rewriting records, original raw names are permanently backed up to the `aux` table keyed by source URL. Subsequent resolution runs always read from the original raw name, ensuring identical counts across consecutive runs. The `mapping_log` primary key is `(entity_type, raw_name, source_url)`.

---

## 6. Compliant Crawling Policy & Excluded Sources

FrontierAtlas adheres to ethical, legal, and compliant web crawling standards. **No CAPTCHA solving, browser fingerprint spoofing, TLS header forging, or residential proxy rotation is used.**

### Access Hierarchy
- **Tier 1 (Official APIs & Syndicated Feeds):** Primary access channel (arXiv API, GitHub REST API, Hugging Face datasets, Greenhouse/Ashby APIs, RSS/Atom feeds).
- **Tier 2 (Polite Asynchronous Crawling):** Honest `User-Agent` identifying the bot, strict `robots.txt` compliance, per-domain token bucket pacing, and circuit breakers opening after 5 consecutive 4xx/5xx errors.
- **Tier 3 (Plain Playwright Headless):** Permitted only for JavaScript-rendered public pages where allowed by `robots.txt`, without cloaking or evasion modules.

### Excluded Sources
The following sources were evaluated and explicitly excluded per our compliance policy:
- **VentureBeat:** Returned persistent HTTP 429 rate limit responses during discovery. Per policy, it was dropped immediately rather than bypassed.
- **MarkTechPost:** Returned HTTP 403 Forbidden responses. Dropped without evasion attempts.
- **Product Hunt:** Protected behind Cloudflare anti-bot verification and requires OAuth approval. Replaced with direct startup homepages and YC directory mirrors.
- **Papers with Code:** The domain `paperswithcode.com` was retired in July 2025 and redirected to Hugging Face Trending Papers, while `paperswithcode.co` serves a benchmarks site. Paper-to-code links were successfully sourced from the official Hugging Face `pwc-archive/links-between-paper-and-code` dataset (300k rows).

---

## 7. System Architecture & Scaling Overview

The accompanying technical architecture specification is detailed in **[`docs/architecture.pdf`](docs/architecture.pdf)** (and [`architecture.pdf`](architecture.pdf)), formatted strictly within a **3-page limit**.

```
docs/architecture.pdf
├── Page 1: High-Scale Ingestion (500k+ Records) & Distributed Freshness
│   ├── Partitioned URL Frontier (Kafka + Redis Streams by Domain Hash)
│   ├── Stateless Worker Fleet & Kubernetes KEDA Autoscaling
│   ├── Distributed Rate Limiting (Redis Token Buckets per Domain)
│   └── 24h Freshness & ETag/If-None-Match Conditional Caching
│
├── Page 2: LLM Resilience Engine & Enterprise Storage Strategy
│   ├── 413 Payload Chunking (tiktoken Smart Truncation + Halved Budget Retry)
│   ├── 429 Quota Management, Retry-After Backoff & Daily Quota Disabling
│   ├── Multi-Tier Provider Chain (Gemini Flash -> Groq -> DeepSeek)
│   └── Storage Evolution: SQLite Staging -> PostgreSQL JSONB -> pgvector -> Neo4j Graph
│
└── Page 3: Compliant Scraping Policy & Idempotent Entity Resolution
    ├── Ethical Crawling Hierarchy (Tiers 1-4, Zero Evasion)
    ├── Collision Guards (Never Merge Distinct Startups)
    └── Idempotent Mapping Log & Graph Relationship Modeling
```

---

## 8. Testing & Verification

The test suite contains **101 comprehensive unit tests** providing full coverage of HTTP mechanics, token chunking, LLM chains, Pydantic validation, job/news feeds, date parsing, entity resolution guards, and Google Sheets export flattening.

```bash
# Execute entire test suite
python -m unittest discover -s tests -t . -v

# Run individual test suites
python -m unittest tests.test_resolver -v
python -m unittest tests.test_export -v
python -m unittest tests.test_jobs -v
python -m unittest tests.test_news -v
python -m unittest tests.test_parsing -v
python -m unittest tests.test_day2 -v
python -m unittest tests.test_core -v

# Run entity resolution verification report
python scripts/mapping_report.py
```

### Verified Mapping Report Output
```
mapping_log rows: 5668
by (entity_type, method):
  JOB_COMPANY  exact       14
  JOB_COMPANY  unresolved  1
  NEWS         exact       28
  NEWS         unresolved  88
  PRODUCT      alias       4
  PRODUCT      exact       1110
  STARTUP      alias       9
  STARTUP      exact       46
  STARTUP      unresolved  4368

non-identity mappings: 15 of 5668
!! SUSPICIOUS: 0 canonical names where DIFFERENT startups were merged under one name
```
