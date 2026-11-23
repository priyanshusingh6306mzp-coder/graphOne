"""Entity name normalization for resolution and deduplication."""
import re

# Match legal entity / corporate suffixes at the end of the name when preceded by space or comma.
# Does NOT match brand tokens like 'Labs' or 'AI' (e.g. 'Cognition Labs', 'Mistral AI', 'ElevenLabs').
LEGAL_SUFFIXES_RE = re.compile(
    r"(?i)[,\s]+(inc\.?|incorporated|llc\.?|l\.l\.c\.?|ltd\.?|limited|corp\.?|corporation|"
    r"pbc\.?|p\.b\.c\.?|public benefit corp(oration)?|gmbh|sas|s\.a\.s\.?|sa|s\.a\.?|"
    r"co\.?|company)\s*$"
)

DOT_COM_RE = re.compile(r"(?i)\.com\s*$")
STRAY_PUNCT_RE = re.compile(r"^[,\.\-\s/]+|[,\.\-\s/]+$")


def strip_legal_suffixes(name: str) -> str:
    """Repeatedly strip legal/corporate suffixes from the end of a name."""
    if not name:
        return ""
    cleaned = name.strip()
    while True:
        m = LEGAL_SUFFIXES_RE.search(cleaned)
        if not m:
            break
        cleaned = LEGAL_SUFFIXES_RE.sub("", cleaned).strip().rstrip(",").strip()
    return cleaned


def conservative_clean_name(name: str) -> str:
    """Canonicalize names using a conservative cleaned display name.

    Strips:
    - Trailing .com (e.g. 'Matidor.com' -> 'Matidor')
    - Legal suffixes (Inc, LLC, Ltd, Corp, Corporation, PBC, GmbH, SA)
    - Stray leading/trailing punctuation
    - Collapses internal whitespace

    Keeps:
    - Brand tokens like 'Labs' and 'AI' (e.g. 'Cognition Labs', 'Mistral AI', 'ElevenLabs')
    """
    if not name:
        return ""
    cleaned = name.strip()
    # 1. Strip trailing .com
    cleaned = DOT_COM_RE.sub("", cleaned).strip()
    # 2. Strip legal suffixes
    cleaned = strip_legal_suffixes(cleaned)
    # 3. Strip stray punctuation from edges
    cleaned = STRAY_PUNCT_RE.sub("", cleaned).strip()
    # 4. Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else name.strip()


def clean_display_name(name: str) -> str:
    """Alias for conservative_clean_name."""
    return conservative_clean_name(name)


def normalize_key(name: str) -> str:
    """Return a normalized alphanumeric lowercased key for exact and clustered matching.

    Handles:
    - Case folding (lowercasing)
    - Conservative cleaning (stripping legal suffixes, trailing .com, stray punctuation)
    - Spacing differences, including 'AI' spacing (e.g. 'Open AI' -> 'openai', 'Hugging Face' -> 'huggingface')
    """
    if not name:
        return ""
    clean = conservative_clean_name(name)
    # Lowercase and keep only alphanumeric characters (a-z0-9)
    key = re.sub(r"[^a-z0-9]", "", clean.lower())
    return key
