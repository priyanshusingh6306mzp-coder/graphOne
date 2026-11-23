"""Publication-date extraction and normalization (stdlib only; dateparser used as optional fallback).

Handles ISO-8601, RFC-822 (RSS), epoch seconds/ms, "Nov 21, 2026", relative phrases ("2 hours ago",
"yesterday", "just now") and missing meta tags (JSON-LD, <meta>, <time datetime>).
All results are timezone-aware UTC.
"""
import email.utils
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "wk": 604800, "wks": 604800, "week": 604800, "weeks": 604800,
    "mo": 2592000, "month": 2592000, "months": 2592000,
    "y": 31536000, "yr": 31536000, "yrs": 31536000, "year": 31536000, "years": 31536000,
}
_REL_RE = re.compile(
    r"(?i)\b(\d+|an?|one)\s*\+?\s*(" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")\b\.?\s*ago\b"
)
_JUST_NOW_RE = re.compile(r"(?i)\b(just now|moments? ago|a moment ago)\b")
_YESTERDAY_RE = re.compile(r"(?i)\byesterday\b")
_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d", "%b %d %Y", "%B %d %Y",
                 "%A, %B %d, %Y", "%a, %d %b %Y")


def _utc(d: datetime) -> datetime:
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def parse_relative(text: str, now: datetime) -> Optional[datetime]:
    if _JUST_NOW_RE.search(text):
        return now
    if _YESTERDAY_RE.search(text):
        return now - timedelta(days=1)
    m = _REL_RE.search(text)
    if not m:
        return None
    n = m.group(1).lower()
    qty = 1 if n in ("a", "an", "one") else int(n)
    return now - timedelta(seconds=qty * _UNITS[m.group(2).lower()])


def parse_any(value: Any, now: Optional[datetime] = None) -> Optional[datetime]:
    """Best-effort parse of a date-like value to an aware UTC datetime; None if unparseable."""
    now = _utc(now or datetime.now(timezone.utc))
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        n = float(value)
        if n > 1e11:  # milliseconds
            n /= 1000.0
        if 946684800 <= n <= 4102444800:  # 2000-01-01 .. 2100-01-01
            return datetime.fromtimestamp(n, timezone.utc)
        return None
    s = str(value).strip()
    if not s:
        return None
    try:  # ISO-8601 (py3.11+ accepts 'Z' and most variants)
        return _utc(datetime.fromisoformat(s.replace("Z", "+00:00") if s.endswith("Z") else s))
    except ValueError:
        pass
    try:  # RFC-822 / RSS
        return _utc(email.utils.parsedate_to_datetime(s))
    except (TypeError, ValueError, IndexError):
        pass
    rel = parse_relative(s, now)
    if rel:
        return rel
    cleaned = re.sub(r"(?i)^(posted|published|updated)( on)?[:\s]+", "", s)
    cleaned = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", cleaned)
    for fmt in _DATE_FORMATS:
        try:
            return _utc(datetime.strptime(cleaned, fmt))
        except ValueError:
            continue
    try:  # optional: dateparser handles many locales/formats
        import dateparser
        d = dateparser.parse(s, settings={"RELATIVE_BASE": now.replace(tzinfo=None), "TIMEZONE": "UTC",
                                          "RETURN_AS_TIMEZONE_AWARE": True, "TO_TIMEZONE": "UTC"})
        return _utc(d) if d else None
    except Exception:
        return None


_META_KEYS = ("article:published_time", "og:article:published_time", "datePublished", "pubdate",
              "publishdate", "publish_date", "date", "dc.date.issued", "dc.date", "parsely-pub-date",
              "sailthru.date", "article:modified_time")


def _meta_values(html: str):
    for key in _META_KEYS:
        k = re.escape(key)
        for pat in (
            rf'<meta[^>]+(?:property|name|itemprop)\s*=\s*["\']{k}["\'][^>]*?content\s*=\s*["\']([^"\']+)["\']',
            rf'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]*?(?:property|name|itemprop)\s*=\s*["\']{k}["\']',
        ):
            m = re.search(pat, html, re.I)
            if m:
                yield key, m.group(1)


def extract_published(html: str, now: Optional[datetime] = None) -> Optional[Tuple[datetime, str]]:
    """Find a publication date in raw HTML. Returns (datetime_utc, method) or None.
    Order: JSON-LD datePublished -> <meta> tags -> <time datetime> -> itemprop."""
    now = _utc(now or datetime.now(timezone.utc))
    for m in re.finditer(r'"datePublished"\s*:\s*"([^"]+)"', html):
        d = parse_any(m.group(1), now)
        if d:
            return d, "json-ld"
    for key, val in _meta_values(html):
        if key == "article:modified_time":
            continue  # modified != published; only used as last resort below
        d = parse_any(val, now)
        if d:
            return d, f"meta:{key}"
    for m in re.finditer(r'<time[^>]+datetime\s*=\s*["\']([^"\']+)["\']', html, re.I):
        d = parse_any(m.group(1), now)
        if d:
            return d, "time-tag"
    m = re.search(r'itemprop\s*=\s*["\']datePublished["\'][^>]*?(?:content|datetime)\s*=\s*["\']([^"\']+)["\']', html, re.I)
    if m:
        d = parse_any(m.group(1), now)
        if d:
            return d, "itemprop"
    return None


def is_fresh(dt: Optional[datetime], now: Optional[datetime] = None, hours: float = 24) -> bool:
    """True if dt is within the last `hours` (10-minute tolerance for clock skew into the future)."""
    if dt is None:
        return False
    now = _utc(now or datetime.now(timezone.utc))
    return now - timedelta(hours=hours) <= _utc(dt) <= now + timedelta(minutes=10)
