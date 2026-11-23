"""Token budgeting and intelligent truncation so payloads never trigger 413 / context errors
while keeping the most information-dense content."""
import math
import re
from typing import List


def estimate_tokens(text: str) -> int:
    """Conservative approximation (~4 chars/token). Good enough for budgeting; tiktoken is optional."""
    return max(1, math.ceil(len(text) / 4)) if text else 0


def _segments(text: str, max_seg: int = 700) -> List[str]:
    segs: List[str] = []
    for block in re.split(r"\n+", text):
        block = block.strip()
        if not block:
            continue
        if len(block) <= max_seg:
            segs.append(block)
            continue
        buf = ""
        for sent in re.split(r"(?<=[.!?])\s+", block):
            if len(buf) + len(sent) + 1 > max_seg and buf:
                segs.append(buf)
                buf = sent
            else:
                buf = f"{buf} {sent}".strip()
        if buf:
            segs.append(buf)
    return segs


def _density(seg: str) -> float:
    words = seg.split()
    if not words:
        return 0.0
    digits = sum(c.isdigit() for c in seg)
    caps = sum(1 for w in words if w[:1].isupper())
    money = sum(seg.count(c) for c in "$€£₹%")
    return (len(set(w.lower() for w in words)) + 0.5 * digits + 2 * caps + 5 * money) / (len(seg) ** 0.5 + 1)


def truncate_smart(text: str, max_tokens: int, head_fraction: float = 0.4) -> str:
    """Keep the lead (title/intro) plus the densest remaining segments, in original order."""
    if estimate_tokens(text) <= max_tokens:
        return text
    budget = max_tokens * 4
    segs = _segments(text)
    chosen = set()
    used = 0
    head_budget = int(budget * head_fraction)
    for i, seg in enumerate(segs):  # lead
        if used + len(seg) > head_budget and chosen:
            break
        chosen.add(i)
        used += len(seg) + 1
    rest = sorted((i for i in range(len(segs)) if i not in chosen), key=lambda i: -_density(segs[i]))
    for i in rest:
        if used + len(segs[i]) + 1 > budget:
            continue
        chosen.add(i)
        used += len(segs[i]) + 1
    out = " ".join(segs[i] for i in sorted(chosen))
    if len(out) > budget:  # a single huge segment: hard cut
        out = out[:budget]
    return out
