"""Pure parsing helpers (stdlib only, unit-testable offline)."""
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

_GH = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/#?].*)?$"
)
_GH_RESERVED = {"orgs", "topics", "sponsors", "features", "about", "settings", "marketplace", "collections"}
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
AI_CATEGORIES = {"cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.NE", "cs.IR", "cs.RO", "cs.MA", "stat.ML"}

_AI_CASE = re.compile(r"\bAI\b|\bML\b|\bLLMs?\b|\bGPT\b|\bNLP\b")
_AI_ANY = re.compile(
    r"artificial intelligence|machine learning|generative|deep learning|computer vision|large language",
    re.I,
)


def parse_github_repo(url: Optional[str]) -> Optional[str]:
    """'https://github.com/owner/repo(.git)(/tree/x)' -> 'owner/repo'; None if not a repo URL."""
    if not url:
        return None
    m = _GH.match(url.strip())
    if not m or m.group(1).lower() in _GH_RESERVED:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def parse_pwc_rows(data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """datasets-server /rows response for pwc-archive/links-between-paper-and-code."""
    out = []
    for item in (data or {}).get("rows", []):
        row = item.get("row", {})
        arxiv_id = str(row.get("paper_arxiv_id") or "").strip()
        repo = parse_github_repo(row.get("repo_url"))
        if not arxiv_id or arxiv_id.lower() in ("null", "none") or not repo:
            continue
        out.append({
            "arxiv_id": arxiv_id,
            "repo": repo,
            "is_official": str(row.get("is_official")).lower() == "true",
        })
    return out


def parse_arxiv_atom(xml_text: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", _NS):
        id_url = (e.findtext("a:id", "", _NS) or "").strip()
        m = re.search(r"arxiv\.org/abs/(.+?)(?:v\d+)?$", id_url)
        title = " ".join((e.findtext("a:title", "", _NS) or "").split())
        if not m or title.lower() == "error":
            continue
        prim = e.find("arxiv:primary_category", _NS)
        out.append({
            "arxiv_id": m.group(1),
            "title": title,
            "authors": [n for n in (" ".join((a.findtext("a:name", "", _NS) or "").split())
                                    for a in e.findall("a:author", _NS)) if n],
            "published": (e.findtext("a:published", "", _NS) or "").strip(),
            "primary_category": prim.get("term") if prim is not None else None,
            "categories": [c.get("term") for c in e.findall("a:category", _NS) if c.get("term")],
        })
    return out


def is_ai_category(primary_category: Optional[str]) -> bool:
    return primary_category in AI_CATEGORIES


def is_ai_startup(company: Dict[str, Any]) -> bool:
    parts = [company.get("one_liner") or "", company.get("industry") or "", company.get("subindustry") or ""]
    parts += list(company.get("tags") or []) + list(company.get("industries") or [])
    text = " ".join(str(p) for p in parts)
    return bool(_AI_CASE.search(text) or _AI_ANY.search(text))


def employee_count(company: Dict[str, Any]) -> Optional[int]:
    v = company.get("team_size")
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


# ---------------------------------------------------------------------------
# Pricing-page parsing (stdlib only): HTML -> text/links, then rule-based pricing model
# ---------------------------------------------------------------------------
from html.parser import HTMLParser
from typing import Tuple
from urllib.parse import urljoin, urlsplit


class _Extractor(HTMLParser):
    _SKIP = ("script", "style", "noscript", "svg", "template")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: List[str] = []
        self.links: List[Tuple[Optional[str], str]] = []
        self._skip = 0
        self._a: Optional[Dict[str, Any]] = None

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "a":
            self._a = {"href": dict(attrs).get("href"), "text": []}

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag == "a" and self._a is not None:
            self.links.append((self._a["href"], " ".join(self._a["text"]).strip()))
            self._a = None

    def handle_data(self, data):
        if self._skip:
            return
        d = data.strip()
        if d:
            self.text.append(d)
            if self._a is not None:
                self._a["text"].append(d)


def _extract(html: str) -> _Extractor:
    ex = _Extractor()
    try:
        ex.feed(html)
        ex.close()
    except Exception:  # malformed HTML: keep whatever was parsed
        pass
    return ex


def html_to_text(html: str) -> str:
    return " ".join(_extract(html).text)


def find_pricing_links(html: str, base_url: str, max_links: int = 3) -> List[str]:
    base_host = urlsplit(base_url).netloc.lower().removeprefix("www.")
    scored: Dict[str, int] = {}
    for href, text in _extract(html).links:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = urljoin(base_url, href.strip()).split("#")[0]
        parts = urlsplit(url)
        host = parts.netloc.lower().removeprefix("www.")
        if parts.scheme not in ("http", "https") or not (host == base_host or host.endswith("." + base_host)):
            continue
        path = parts.path.lower()
        score = 0
        if "pricing" in path:
            score = 3
        elif re.search(r"/(plans?|prices?|price)(/|$)", path):
            score = 2
        elif (re.fullmatch(r"(?i)\s*(pricing|plans?( & pricing)?|prices?)\s*", text or "")
              and not re.search(r"demo|contact|book|sales|sign-?up|log-?in|get-started|trial", path)):
            score = 1  # link text says "Pricing" but the path is not a demo/contact/signup page
        if score:
            scored[url] = max(score, scored.get(url, 0))
    return [u for u, _ in sorted(scored.items(), key=lambda kv: -kv[1])][:max_links]


_FREE_STRONG = re.compile(
    r"(?i)\b(?:free (?:plans?|tiers?|forever|version|for (?:individuals|personal|everyone))|forever free|always free|"
    r"free forever|open[- ]source and free|free[.,]?\s+(?:then\s+)?upgrade)\b"
    # "$0" / "$0.00" is a free plan, but not: "$0.065" (decimal price), "$0k" (slider label),
    # "$0/month + usage" (pay-as-you-go base fee), "$0 /24-hour trial" (a trial, not a plan)
    r"|[$€£₹]\s?0(?:\.0+)?(?!\.?\d)(?![A-Za-z])(?!\s*/?\s*(?:mo|month)?\s*(?:\+|plus)\s*usage)"
    r"(?!\s*/\s*\d+[- ]?(?:hour|day)s?[- ]*(?:free )?trial)"
    r"(?!\s+(?:today|now|due|for\s+(?:the\s+)?(?:first\s+)?\d+))"
    r"(?!\s*/?\s*(?:mo|month)?\s*(?:\+|then\b))"  # "$0 + carrier premiums", "$0 then $0.05/credit"
    r"(?!\s+(?:[\w-]+\s+){0,2}fees?\b)"  # "$0 platform fee", "$0 per-message fees"
    r"(?!\s+(?:volume|up-?front|commitment|minimum|overage|set-?up))"  # "$0 volume", "$0 setup"
    r"(?!\s*[$€£₹]\s?\d[\d,.]*\s?[kKmMbB]\b)"  # slider labels "$0 $15M $30M"
    r"(?!\s*/\s*\d+[- ]?(?:\w+\s+)?(?:hours?|days?)\b)"  # "$0 / 3 school days" (a trial)
)
# "Start for free" CTAs are often free *trials*, so they are only a weak signal
_FREE_WEAK = re.compile(r"(?i)\b(?:start|get started|try|sign up)(?: for)? free\b(?!\s+trial)")
_PRICE_RE = re.compile(
    r"(?i)[$€£₹]\s?(?:[1-9][\d,]*(?:\.\d+)?|0\.\d*[1-9]\d*)\s*(?:usd|eur)?\s*(?:/|per\b|a\b|mo\b|month|year|yr\b|user|seat|tokens?|credits?|1m|1k)"
)
# Looser price detector: if this fires but _PRICE_RE did not, prices exist in a format we did not parse,
# so the page is ambiguous (LLM stage), never "no price => ENTERPRISE".
_PRICE_LOOSE_RE = re.compile(
    r"(?i)[$€£₹]\s?(?:[1-9]|0\.\d*[1-9])|\b[1-9][\d,]*\s?(?:usd|eur|inr)\b|/\s?(?:mo|month|user|seat|year)\b"
    r"|per (?:month|user|seat|year)\b|billed (?:monthly|annually|yearly)"
)
# Explicit sales-contact wording only. Generic "Contact us" / "Book a demo" appear in every navbar and
# footer, so they are NOT evidence of enterprise pricing.
_CONTACT_RE = re.compile(
    r"(?i)contact (?:our )?sales|talk to (?:our )?sales|speak (?:to|with) (?:our )?sales|custom pricing|"
    r"get a quote|request (?:a )?quote|contact (?:us )?(?:for|about) (?:pricing|a quote|enterprise)"
)
_ENTERPRISE_RE = re.compile(r"(?i)\benterprise\b")


def _snippet(text: str, m: "re.Match[str]", width: int = 40) -> str:
    return text[max(0, m.start() - width): m.end() + width].strip()


def classify_pricing(text: str) -> Tuple[Optional[str], List[str]]:
    """Rule-based pricing model from visible page text.
    Returns (label|None, evidence snippets copied verbatim from the text).
    None means ambiguous or no pricing info; defer to the grounded LLM stage."""
    if len(text) < 200:  # JS shell / empty page
        return None, []
    strong, weak = _FREE_STRONG.search(text), _FREE_WEAK.search(text)
    price, contact = _PRICE_RE.search(text), _CONTACT_RE.search(text)
    evidence = [_snippet(text, m) for m in (strong or weak, price, contact) if m]
    loose = _PRICE_LOOSE_RE.search(text)
    if strong and (price or loose):
        return "FREEMIUM", evidence
    if strong:
        return ("FREEMIUM" if contact else "FREE"), evidence
    if price:  # incl. "start for free" + paid prices: most likely a free trial
        return "PAID", evidence
    if weak and not contact:
        return None, []  # only a CTA, no pricing evidence
    if loose:
        return None, []  # prices present in an unparsed format: ambiguous
    if contact:
        return "ENTERPRISE", evidence
    return None, []


_PRICING_PATH_RE = re.compile(r"pric|plans?|subscri")


def looks_like_pricing_url(url: str) -> bool:
    return bool(_PRICING_PATH_RE.search(urlsplit(url).path.lower()))


_NOT_PRICING_PAGE = re.compile(
    r"(?i)login|log-in|sign-?in|sign-?up|auth|demo|contact|book|blog|docs|calculator|optimi[sz]ation|"
    r"market-pricing|competit|policy|surge|careers|404|/solutions/|/products/"
)


def is_llm_pricing_candidate(url: str) -> bool:
    """Only spend LLM calls on pages that are plausibly a product's own pricing page."""
    path = urlsplit(url).path
    return looks_like_pricing_url(url) and not _NOT_PRICING_PAGE.search(path)


# ---------------------------------------------------------------------------
# Does the quoted evidence actually support the LLM's label? (stdlib, unit-tested)
# ---------------------------------------------------------------------------
_FREE_WORDS = re.compile(
    r"(?i)\bfree\b|gratis|gratuit|gratuito|grátis|kostenlos|[$€£₹]\s?0(?!\.?\d)|\b0\s?/\s?(?:mo|month)|"
    r"pay nothing|no cost|no charge|open[- ]source|self-host|\bMIT\b"
)
_FREE_PLAN_NAME = re.compile(r"(?i)\bfree\b\s+(?:[$€£₹]\s?0|0%|plans?|tiers?|forever|for\b)")
_FREE_OTHER = re.compile(
    r"(?i)pay nothing|no cost|no charge|free up to|up to [^.]{0,30}free|open[- ]source|self-host|free credits|"
    r"free tier|free plan|free forever|always free|\bMIT\b"
)
_MONEY = r"(?:[$€£₹]|r\$|\brs\.?|\b(?:inr|php|mxn|brl|chf|aed|sar|cad|aud|usd|eur|gbp))"
_PRICE_EVID = re.compile(
    rf"(?i){_MONEY}\s?\d|\b\d[\d,.]*\s?(?:usd|eur|inr|mxn|gbp|cad|aud|php|brl)\b|"
    r"/\s?(?:mo|month|year|yr|user|seat|mes|mois|monat|año)\b|per (?:month|user|seat|year)\b"
)
_CONTACT_EVID = re.compile(
    r"(?i)contact|sales|demo|quote|custom|request|get in touch|in touch|book a call|talk to|scope|tailor|enterprise|"
    r"let'?s talk|does not publish|don'?t publish|not publish|price list|proposal|priced around|pricing information|"
    r"headcount|case[- ]by[- ]case|on request|inquir"
)
_ALT_CURRENCY_PRICE = re.compile(r"(?i)\b(?:rs\.?|inr|php|mxn|brl)\s?\d")


def llm_label_supported(label: str, evidence: str, page_text: str = "") -> bool:
    """Light consistency check between an LLM label and its (already grounded) evidence quote.
    - FREE/FREEMIUM need real free-plan evidence; a bare "Start for free" CTA is not enough.
    - PAID needs a price (any common currency) or a subscription mention.
    - ENTERPRISE needs contact-sales / "no public price" evidence AND no real prices on the page."""
    e = evidence or ""
    if label in ("FREE", "FREEMIUM"):
        if not _FREE_WORDS.search(e):
            return False
        if (_FREE_WEAK.search(e) and not _FREE_STRONG.search(e) and not _FREE_PLAN_NAME.search(e)
                and not _FREE_OTHER.search(e)):
            return False
        return True
    if label == "PAID":
        return bool(_PRICE_EVID.search(e)) or "subscription" in e.lower()
    if label == "ENTERPRISE":
        if not _CONTACT_EVID.search(e):
            return False
        return not (page_text and (_PRICE_RE.search(page_text) or _ALT_CURRENCY_PRICE.search(page_text)))
    return False
