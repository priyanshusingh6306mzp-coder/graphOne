"""Anti-hallucination guards: every extracted value must be grounded in the fetched source text."""
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Tuple


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s$€£₹%.+/-]", " ", s)).strip()


def quote_in_text(quote: str, source: str) -> bool:
    """Strict: a (normalized) quote must appear verbatim in the (normalized) source."""
    q = normalize(quote or "")
    return len(q) >= 3 and q in normalize(source)


def is_grounded(value: Any, source: str, min_ratio: float = 0.9) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (list, tuple)):
        return all(is_grounded(v, source, min_ratio) for v in value)
    if isinstance(value, (int, float)):
        src = re.sub(r"(?<=\d),(?=\d)", "", source)
        num = str(int(value)) if float(value).is_integer() else str(value)
        return re.search(rf"(?<![\d.]){re.escape(num)}(?![\d])", src) is not None
    text = normalize(value)
    if not text:
        return False
    src = normalize(source)
    if text in src:
        return True
    toks = text.split()
    src_tokens = set(src.split())
    return sum(t in src_tokens for t in toks) / len(toks) >= min_ratio


def ground_fields(obj: Dict[str, Any], source: str, fields: Iterable[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Null out any listed field whose value is not grounded in `source`. Returns (clean_obj, dropped)."""
    clean, dropped = dict(obj), []
    for f in fields:
        if clean.get(f) is not None and not is_grounded(clean[f], source):
            clean[f] = None
            dropped.append(f)
    return clean, dropped
