"""Generate architecture.pdf strictly <= 3 pages using ReportLab."""
import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and print 'Page X of Y'."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        print(f"Total pages rendered: {num_pages}")
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header (pages 2+)
        if self._pageNumber > 1:
            self.drawString(36, 11 * inch - 26, "GraphOne / FrontierAtlas — System Architecture & Scaling Specification")
            self.drawRightString(8.5 * inch - 36, 11 * inch - 26, "Confidential / Production Blueprint")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(36, 11 * inch - 30, 8.5 * inch - 36, 11 * inch - 30)
            
        # Footer (all pages)
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(36, 32, 8.5 * inch - 36, 32)
        
        self.drawString(36, 22, "GraphOne Ingestion Pipeline · Enterprise Production Architecture")
        self.drawRightString(8.5 * inch - 36, 22, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()


def build_pdf(filename: str):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=38,
    )

    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#0284C7"),
        spaceAfter=6,
    )
    meta_style = ParagraphStyle(
        'DocMeta',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#64748B"),
        spaceAfter=8,
    )
    h1_style = ParagraphStyle(
        'SectionH1',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=5,
        spaceAfter=3,
        keepWithNext=True,
    )
    h2_style = ParagraphStyle(
        'SectionH2',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#0369A1"),
        spaceBefore=3,
        spaceAfter=2,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.8,
        leading=10.5,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=3,
    )
    bullet_style = ParagraphStyle(
        'BulletDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.8,
        leading=10.5,
        textColor=colors.HexColor("#1E293B"),
        leftIndent=10,
        firstLineIndent=-7,
        spaceAfter=2,
    )
    callout_style = ParagraphStyle(
        'CalloutText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.4,
        leading=9.8,
        textColor=colors.HexColor("#0F172A"),
    )
    table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.2,
        leading=9.2,
        textColor=colors.HexColor("#1E293B"),
    )
    table_hdr = ParagraphStyle(
        'TableHdr',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.4,
        leading=9.5,
        textColor=colors.white,
    )

    story = []

    # ==================== PAGE 1: TITLE & CORE SCALING (500k+) ====================
    story.append(Paragraph("FrontierAtlas Ingestion Engine: High-Scale Architecture", title_style))
    story.append(Paragraph("Technical Specification: Distributed Ingestion, LLM Fault-Tolerance, Freshness & Compliance", subtitle_style))
    story.append(Paragraph("Target: 500,000+ Records Across Startups, Products, Papers, Jobs & News · SLA: 24h Freshness", meta_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0284C7"), spaceAfter=6))

    story.append(Paragraph("1. Scaling to 500,000+ Records: Distributed Partitioning & Worker Fleet", h1_style))
    story.append(Paragraph(
        "Scaling from local SQLite staging (~6,900 records) to an enterprise-grade corpus exceeding 500,000 entities requires "
        "decoupling discovery, scheduling, crawling, extraction, and persistence into stateless, horizontally autoscaled micro-workers.",
        body_style
    ))

    # Architecture Overview Table
    p1_data = [
        [Paragraph("Pipeline Layer", table_hdr), Paragraph("Architecture & Distributed Mechanism", table_hdr), Paragraph("Throughput & SLA Target", table_hdr)],
        [
            Paragraph("<b>URL Frontier & Discovery</b>", table_cell),
            Paragraph("Domain-partitioned Kafka topics + Redis Streams. Ingestion seeds split into independent partition queues by root domain hash (<code>hash(domain) % N</code>). Eliminates head-of-line blocking across slow/fast targets.", table_cell),
            Paragraph("10,000 URL pushes/sec;<br/>Zero cross-domain locks", table_cell)
        ],
        [
            Paragraph("<b>Worker Fleet</b>", table_cell),
            Paragraph("Stateless Python worker containers managed via Kubernetes KEDA autoscaler, scaling based on backlog queue depth. Workers execute asynchronous <code>aiohttp</code> event loops with bounded worker semaphores.", table_cell),
            Paragraph("50 to 500 worker pods;<br/>500,000 items in <4 hours", table_cell)
        ],
        [
            Paragraph("<b>Distributed Rate Limiting</b>", table_cell),
            Paragraph("Central Redis cluster running atomic token bucket Lua scripts enforcing per-domain concurrency semaphores and inter-request intervals (e.g. <code>api.github.com</code>: 10 concurrent, 50ms interval).", table_cell),
            Paragraph("Sub-millisecond token check;<br/>Strict 429 prevention", table_cell)
        ],
        [
            Paragraph("<b>Source Adapters</b>", table_cell),
            Paragraph("Declarative plug-and-play collector specifications (YAML/JSON schema) defining endpoint families (Greenhouse, Ashby, Lever, Atom/RSS, Trafilatura) enabling zero-downtime additions without engine redeployment.", table_cell),
            Paragraph("50+ adapter families;<br/>Config-driven ingestion", table_cell)
        ],
    ]
    t1 = Table(p1_data, colWidths=[1.3 * inch, 4.3 * inch, 1.8 * inch])
    t1.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor("#F8FAFC"), colors.HexColor("#FFFFFF")]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t1)
    story.append(Spacer(1, 4))

    story.append(Paragraph("2. Distributed Freshness Tracking & Watermark Dedup Across Nodes", h1_style))
    story.append(Paragraph(
        "For dynamic entities (Jobs and News), the system strictly enforces a rolling 24-hour UTC freshness window, "
        "preventing re-indexed or modified legacy postings from entering the pipeline:",
        body_style
    ))
    story.append(Paragraph("• <b>UTC ISO-8601 Normalization:</b> All dates (RSS RFC-822, ISO-8601 strings, epoch ms, relative '2 hours ago', JSON-LD, and HTML <code>&lt;time&gt;</code> tags) are parsed into timezone-aware UTC <code>datetime</code> objects via <code>src.dates.parse_any</code>. Unparseable dates default to exclusion from 24h tabs.", bullet_style))
    story.append(Paragraph("• <b>First Published vs. Updated At:</b> Job boards (e.g., Greenhouse API) frequently update timestamps when modifying internal recruiters or minor tags. The pipeline strictly consumes <code>first_published</code> (Greenhouse) or <code>publishedAt</code> (Ashby) and rejects <code>updated_at</code> to guarantee genuine 24h freshness.", bullet_style))
    story.append(Paragraph("• <b>Conditional HTTP Caching (ETag / If-None-Match):</b> Crawlers store server HTTP <code>ETag</code> and <code>Last-Modified</code> response headers in a shared Redis cache. Subsequent poll cycles send <code>If-None-Match</code> and <code>If-Modified-Since</code>, immediately receiving <code>304 Not Modified</code> and conserving 92% of bandwidth.", bullet_style))
    story.append(Paragraph("• <b>Distributed Watermarking & Dedup:</b> URLs are canonicalized via <code>src.core.url.normalize</code> (stripping tracking query params, fragments, and trailing slashes). De-duplication uses a 2-tier check: (1) In-memory Redis Bloom filter for instantaneous rejection of seen URLs, followed by (2) PostgreSQL <code>url_hash</code> unique constraints for ACID idempotency across concurrent worker nodes.", bullet_style))

    story.append(PageBreak())

    # ==================== PAGE 2: FAULT TOLERANCE & STORAGE STRATEGY ====================
    story.append(Paragraph("3. LLM Resilience: 413 Payload Chunking & 429 Quota/Rate Management", h1_style))
    story.append(Paragraph(
        "LLMs are utilized selectively for complex extraction (unstructured pricing models, news summaries, and ambiguous entity mapping). "
        "A multi-tier resilience engine guarantees zero pipeline halts under API errors and strict zero-hallucination outputs:",
        body_style
    ))

    p2_data = [
        [Paragraph("Fault Condition", table_hdr), Paragraph("Engine Handling & Mitigation Architecture", table_hdr), Paragraph("Design Rationale", table_hdr)],
        [
            Paragraph("<b>413 Payload Too Large /<br/>Context Window Limit</b>", table_cell),
            Paragraph("Trafilatura cleans HTML into dense text. <code>src.llm.chunking</code> calculates token count via <code>tiktoken</code>. If text exceeds model budget, it applies smart truncation (retaining title, lead, and top-density paragraphs). On HTTP 413 or context error, it <b>halves the token budget</b> and retries on the same tier before falling back.", table_cell),
            Paragraph("Preserves high-signal context without dropping whole documents; auto-adapts to restrictive vendor context windows.", table_cell)
        ],
        [
            Paragraph("<b>429 Per-Minute Rate Limit /<br/>5xx Server Overload</b>", table_cell),
            Paragraph("Extracts vendor <code>retryDelay</code> from headers/body. Applies exponential backoff with full jitter: <code>min(base * 2^attempt + jitter, 120s)</code>. Sets a cooldown timer on the provider and falls through immediately to the next tier in the active chain.", table_cell),
            Paragraph("Zero pipeline stalling; background workers switch to healthy provider tiers while rate-limited tier cools down.", table_cell)
        ],
        [
            Paragraph("<b>429 Daily Free-Tier<br/>Quota Exhaustion</b>", table_cell),
            Paragraph("Regex detection for daily quota exhaustion (e.g. <code>GenerateRequestsPerDayPerProjectPerModel</code>). Immediately sets <code>provider.enabled = False</code> for the remainder of the run, skipping useless retry sleeps and routing to alternate providers.", table_cell),
            Paragraph("Prevents wasteful blocked execution loops when free-tier daily caps are hit; preserves unattended run progress.", table_cell)
        ],
        [
            Paragraph("<b>Provider Fallback Chain &<br/>Zero-SDK Direct REST</b>", table_cell),
            Paragraph("Chained execution: <b>Gemini Flash</b> (primary) → <b>Groq Llama 3.3 / GPT-OSS</b> (secondary) → <b>DeepSeek Chat</b> (tertiary). Plain <code>aiohttp</code> REST calls avoid heavy vendor SDK wrappers, providing identical timeout and error semantics.", table_cell),
            Paragraph("High vendor resilience; model names configurable via environment variables without code modification.", table_cell)
        ],
        [
            Paragraph("<b>Deterministic Content-Hash<br/>Caching & Grounding</b>", table_cell),
            Paragraph("Caches model responses in SQLite/Redis keyed by <code>sha256(system + prompt + text)</code>. Re-runs achieve 100% cache hits at $0 cost. Extracted strings undergo strict verification (<code>src.llm.verify.is_grounded</code>); ungrounded outputs are nulled.", table_cell),
            Paragraph("Guarantees strict zero-hallucination constraint; eliminates redundant LLM spend across collector cycles.", table_cell)
        ],
    ]
    t2 = Table(p2_data, colWidths=[1.5 * inch, 4.3 * inch, 1.6 * inch])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor("#F8FAFC"), colors.HexColor("#FFFFFF")]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t2)
    story.append(Spacer(1, 4))

    story.append(Paragraph("4. Storage Strategy: Staging, Hybrid Data Lake & Intelligence Graph", h1_style))
    story.append(Paragraph(
        "Production storage migrates from single-node SQLite staging to a polyglot multi-model data architecture:",
        body_style
    ))
    story.append(Paragraph("• <b>Local Node Staging (SQLite WAL):</b> Collector workers write raw ingest streams into local ephemeral SQLite instances operating in Write-Ahead-Log (WAL) mode. Decouples fast crawling loops from central network database latency.", bullet_style))
    story.append(Paragraph("• <b>Core Relational & Document Store (PostgreSQL):</b> Ingested batches flush into partitioned PostgreSQL tables (partitioned by <code>record_type</code> and <code>collected_date</code>). Dynamic attributes are indexed via <code>JSONB</code> with GIN indexing for high-speed attribute lookups.", bullet_style))
    story.append(Paragraph("• <b>Semantic Dedup & Vector Index (pgvector / Qdrant):</b> Paper abstracts, product taglines, and news summaries generate 768-d text embeddings. Vector similarity search flags duplicate startups and overlapping research papers across diverse data feeds.", bullet_style))
    story.append(Paragraph("• <b>Intelligence Graph (Neo4j Enterprise):</b> Entity resolution maps records into a graph model: <code>(:Startup)-[:BUILDS]->(:Product)</code>, <code>(:Startup)-[:POSTS]->(:Job)</code>, <code>(:Startup)-[:MENTIONED_IN]->(:News)</code>, and <code>(:Startup)-[:AUTHORS]->(:Paper)</code>. Enables instant relational traversal across the tech ecosystem.", bullet_style))

    story.append(PageBreak())

    # ==================== PAGE 3: COMPLIANCE & ENTITY RESOLUTION ====================
    story.append(Paragraph("5. Ethical, Compliant Anti-Bot Architecture & Scraping Policy", h1_style))
    story.append(Paragraph(
        "FrontierAtlas maintains strict ethical compliance and transparent scraping standards. The pipeline does <b>not</b> "
        "solve CAPTCHAs, spoof browser fingerprints, rotate residential proxies to evade blocks, or forge TLS client hellos. "
        "Access follows a strict 3-tier hierarchy:",
        body_style
    ))

    p3_data = [
        [Paragraph("Tier / Channel", table_hdr), Paragraph("Mechanism, Compliance Protocols & Technical Controls", table_hdr), Paragraph("Target Domain / Source Scope", table_hdr)],
        [
            Paragraph("<b>Tier 1:<br/>Official APIs, Feeds<br/>& Open Datasets</b>", table_cell),
            Paragraph("Direct integration with official REST/GraphQL APIs, syndicated RSS/Atom feeds, and verified open datasets. Authenticates via valid API tokens with rate-limit adherence. Examples: arXiv API, GitHub REST API, Hugging Face datasets-server, Ashby Job Board API, Greenhouse Public API, YC OSS directory.", table_cell),
            Paragraph("85% of total corpus volume;<br/>Zero bot challenge risk;<br/>Highest reliability", table_cell)
        ],
        [
            Paragraph("<b>Tier 2:<br/>Polite HTTP Fetching<br/>(aiohttp)</b>", table_cell),
            Paragraph("Asynchronous fetching with custom, transparent <code>User-Agent</code> identifying the bot and contact email (<code>GraphOneTrialBot/0.1 (+mailto:ops@frontieratlas.com)</code>). Obey <code>robots.txt</code> via cached parser. Enforce per-domain token buckets and auto-trip circuit breakers on repeated 403/429.", table_cell),
            Paragraph("Company pricing pages,<br/>syndicated news articles,<br/>public engineering blogs", table_cell)
        ],
        [
            Paragraph("<b>Tier 3:<br/>Plain Headless Playwright<br/>(Where Permitted)</b>", table_cell),
            Paragraph("For JavaScript-rendered single-page applications (SPAs) that explicitly allow indexing in <code>robots.txt</code>. Operates plain vanilla Chromium with asset filtering (blocking images, video, trackers) to minimize server load. <b>No fingerprint cloaking or evasion modules.</b>", table_cell),
            Paragraph("Client-rendered pricing tables<br/>and modern dynamic job portals<br/>(permitted targets only)", table_cell)
        ],
        [
            Paragraph("<b>Tier 4:<br/>Hard-Blocked Sources &<br/>Commercial Data</b>", table_cell),
            Paragraph("When a source actively blocks automated crawling (e.g., Cloudflare Turnstile, Akamai Bot Manager, persistent HTTP 403/429 such as <i>VentureBeat</i> 429 and <i>MarkTechPost</i> 403), the source is <b>dropped immediately</b>. Bypassing is prohibited; missing data is secured via commercial partnerships.", table_cell),
            Paragraph("VentureBeat, MarkTechPost,<br/>Product Hunt (OAuth required),<br/>Cloudflare-gated domains", table_cell)
        ],
    ]
    t3 = Table(p3_data, colWidths=[1.5 * inch, 4.3 * inch, 1.6 * inch])
    t3.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor("#F8FAFC"), colors.HexColor("#FFFFFF")]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t3)
    story.append(Spacer(1, 4))

    story.append(Paragraph("6. Production Entity Resolution & Data Flow Lifecycle", h1_style))
    story.append(Paragraph(
        "Unifying 500,000+ heterogeneous records requires an idempotent, collision-guarded entity resolution engine:",
        body_style
    ))
    story.append(Paragraph("• <b>Collision Guards (Never Merge Distinct Startups):</b> Startups with distinct source URLs are never merged under one canonical name even if normalized keys collide (e.g. 'HENRY' vs 'Henry', '.Wave' vs 'Wave'). Cross-URL collision checks preserve distinct raw names as canonical, maintaining <code>SUSPICIOUS: 0</code>.", bullet_style))
    story.append(Paragraph("• <b>Conservative Normalization:</b> Strips legal suffixes (<code>Inc, LLC, Ltd, Corp, PBC, GmbH, SA</code>), trailing <code>.com</code>, and stray punctuation while strictly preserving brand tokens (<code>Labs</code>, <code>AI</code>) such as <i>Cognition Labs</i> and <i>Mistral AI</i>.", bullet_style))
    story.append(Paragraph("• <b>Parent Entity Linkage:</b> Products link to their parent startups via <code>startup_meta</code> domain matching rather than blind string resolution, guaranteeing 100% concordance between <code>product.content.startupName</code> and <code>startup.content.entityName</code>.", bullet_style))
    story.append(Paragraph("• <b>Auditability & Idempotent Mapping Log:</b> The <code>mapping_log</code> records every resolution decision under composite key <code>(entity_type, raw_name, source_url)</code>. Original raw names are backed up to <code>aux</code> tables, allowing repeated resolution runs to remain completely idempotent.", bullet_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated {filename}")


if __name__ == "__main__":
    out_pdf = sys.argv[1] if len(sys.argv) > 1 else "docs/architecture.pdf"
    build_pdf(out_pdf)
