"""Multi-tier LLM extraction chain: Gemini -> Groq -> DeepSeek (any subset, from env keys).

Resilience:
  * 429 / 5xx / timeouts: the HTTP layer already retried with exponential backoff + jitter and
    Retry-After; if a provider still fails, the chain falls through to the next tier.
  * 413 / context-length 400: re-truncate the input (keep the lead + densest segments) with half the
    token budget, and retry on the same provider.
  * Invalid JSON / schema mismatch: fall through to the next tier.
  * 401/403 or repeated hard failures: the provider is disabled for the rest of the run.
  * Results are cached by content hash, so re-runs cost nothing.
The network call is injected (`post`) so the chain is unit-testable without network or SDKs.
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple

from .chunking import truncate_smart

log = logging.getLogger("llm")

# post(url, headers, body) -> (http_status, parsed_json_or_None, raw_text)
Post = Callable[[str, Dict[str, str], Dict[str, Any]], Awaitable[Tuple[int, Any, str]]]


class AllProvidersFailed(Exception):
    pass


@dataclass
class Provider:
    name: str
    model: str
    url: str
    headers: Dict[str, str]
    kind: str  # "gemini" | "openai"  (openai-compatible: Groq, DeepSeek)
    max_input_tokens: int = 20000
    enabled: bool = True
    hard_failures: int = 0
    cooldown_until: float = 0.0   # monotonic time before which this tier is skipped (after 429/5xx)
    backoff: float = 20.0         # next cooldown length in seconds (doubles up to 240)

    def body(self, system: str, user: str, max_output_tokens: int = 1024) -> Dict[str, Any]:
        if self.kind == "gemini":
            return {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": max_output_tokens,
                                     "responseMimeType": "application/json"},
            }
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": max_output_tokens,
            "response_format": {"type": "json_object"},
        }

    def parse(self, data: Any) -> str:
        if self.kind == "gemini":
            return "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])
        return data["choices"][0]["message"]["content"]


def providers_from_env(env: Mapping[str, str]) -> List[Provider]:
    """Build the tier list from whichever API keys are present. Model names are overridable via env
    (check scripts/check_llm.py for the models your keys can use)."""
    out: List[Provider] = []
    key = env.get("GEMINI_API_KEY")
    if key:
        for model in filter(None, [env.get("GEMINI_MODEL", "gemini-flash-latest"), env.get("GEMINI_FALLBACK_MODEL")]):
            out.append(Provider(
                name=f"gemini:{model}", model=model, kind="gemini", headers={"x-goog-api-key": key},
                url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                max_input_tokens=int(env.get("GEMINI_MAX_INPUT_TOKENS", "30000"))))
    key = env.get("GROQ_API_KEY")
    if key:
        for model in filter(None, [env.get("GROQ_MODEL", "llama-3.3-70b-versatile"), env.get("GROQ_FALLBACK_MODEL")]):
            out.append(Provider(
                name=f"groq:{model}", model=model, kind="openai",
                headers={"Authorization": f"Bearer {key}"},
                url="https://api.groq.com/openai/v1/chat/completions",
                max_input_tokens=int(env.get("GROQ_MAX_INPUT_TOKENS", "5000"))))
    key = env.get("DEEPSEEK_API_KEY")
    if key:
        model = env.get("DEEPSEEK_MODEL", "deepseek-chat")
        out.append(Provider(
            name=f"deepseek:{model}", model=model, kind="openai",
            headers={"Authorization": f"Bearer {key}"},
            url="https://api.deepseek.com/chat/completions",
            max_input_tokens=int(env.get("DEEPSEEK_MAX_INPUT_TOKENS", "24000"))))
    return out


def parse_json_object(text: str) -> Any:
    """Tolerate code fences / chatter around a JSON object."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in model output")
    return json.loads(t[start:end + 1])


_DAILY_QUOTA_RE = re.compile(r"(?i)per[\s_-]?day|requests_per_day|daily|limit:\s*0\b")
_RETRY_DELAY_RE = re.compile(r'(?i)"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"|retry in (\d+(?:\.\d+)?)\s?s')


def _retry_delay_from_body(raw: str) -> Optional[float]:
    m = _RETRY_DELAY_RE.search(raw or "")
    if not m:
        return None
    return float(m.group(1) or m.group(2)) + 1.0


def _is_context_error(raw: str) -> bool:
    return bool(re.search(r"(?i)context[ _-]?(length|window)|too (large|long|many tokens)|maximum.*tokens|"
                          r"exceed|payload too large|request too large", raw or ""))


class LLMChain:
    def __init__(self, providers: List[Provider], post: Post, cache: Any = None, max_attempts: int = 4,
                 max_rounds: int = 8, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.providers = providers
        self.post = post
        self.cache = cache  # needs cache_get(key) / cache_put(key, model, response)
        self.max_attempts = max_attempts
        self.max_rounds = max_rounds
        self.sleep = sleep
        self.clock = clock
        self.stats: Dict[str, int] = {}
        self._warned: set = set()

    def alive(self) -> bool:
        return any(p.enabled for p in self.providers)

    def _count(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

    def _warn_once(self, p: Provider, status: int, raw: str) -> None:
        if (p.name, status) not in self._warned:
            self._warned.add((p.name, status))
            log.warning("%s returned HTTP %s: %s", p.name, status, (raw or "").strip()[:300])

    def _cool(self, p: Provider, delay: Optional[float] = None) -> None:
        """Skip this tier for a while: the provider's own retry hint if it gave one, else doubling backoff."""
        wait = min(delay, 600.0) if delay is not None else p.backoff
        p.cooldown_until = self.clock() + wait
        log.info("%s cooling down for %.0fs", p.name, wait)
        if delay is None:
            p.backoff = min(p.backoff * 2, 240.0)

    async def _try(self, p: Provider, system: str, text: str, make_prompt: Callable[[str], str],
                   validate: Callable[[Any], Any], input_cap: Optional[int], errors: List[str]) -> Optional[Tuple[Any, str]]:
        budget = min(p.max_input_tokens, input_cap) if input_cap else p.max_input_tokens
        for _ in range(self.max_attempts):
            chunk = truncate_smart(text, budget)
            status, data, raw = await self.post(p.url, p.headers, p.body(system, make_prompt(chunk)))
            if status == 200 and data is not None:
                try:
                    obj = validate(parse_json_object(p.parse(data)))
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    errors.append(f"{p.name}: bad output ({exc})")
                    self._count("bad_output")
                    return None  # next tier
                p.hard_failures = 0
                p.backoff = 20.0
                self._count(f"ok:{p.name}")
                return obj, p.name
            if status != 200:
                self._warn_once(p, status, raw)
            if status == 413 or (status == 400 and _is_context_error(raw)):
                new_budget = max(400, budget // 2)
                self._count("rechunk")
                log.info("%s: payload too large (%s); budget %d -> %d tokens", p.name, status, budget, new_budget)
                if new_budget == budget:
                    return None
                budget = new_budget
                continue
            if status == 404:  # unknown model / wrong endpoint: a config error, retrying cannot help
                p.enabled = False
                errors.append(f"{p.name}: HTTP 404 (model name wrong or retired?); disabled")
                self._count("disabled")
                return None
            if status in (401, 403):
                p.enabled = False
                errors.append(f"{p.name}: auth failed ({status}); disabled")
                self._count("disabled")
                return None
            if status == 429 and _DAILY_QUOTA_RE.search(raw or ""):
                # a per-DAY free-tier quota: waiting minutes is pointless, so stop using this tier this run
                p.enabled = False
                errors.append(f"{p.name}: daily quota exhausted; disabled for this run")
                log.error("%s: daily quota exhausted. Skipping it for the rest of this run; re-run after the quota "
                          "resets, or add another provider key. Provider said: %s", p.name, (raw or "").strip()[:200])
                self._count("quota_exhausted")
                return None
            if status == 429 or status >= 500:  # rate limit / overload: cool this tier down, try the next
                self._cool(p, _retry_delay_from_body(raw) if status == 429 else None)
                errors.append(f"{p.name}: HTTP {status}")
                self._count(f"fallback:{status}")
                return None
            p.hard_failures += 1
            if p.hard_failures >= 5:
                p.enabled = False
            errors.append(f"{p.name}: HTTP {status}")
            self._count(f"fallback:{status}")
            return None
        return None

    async def extract(self, system: str, text: str, make_prompt: Callable[[str], str],
                      validate: Callable[[Any], Any], cache_extra: str = "",
                      input_cap: Optional[int] = None) -> Tuple[Any, str]:
        """Run the chain. `validate(obj)` must return the cleaned result or raise ValueError.
        Tiers cooling down after 429/5xx are skipped; if every usable tier is cooling down we wait for
        the earliest one instead of failing the item. Returns (result, provider_name)."""
        cache_key = hashlib.sha256(f"{system}|{cache_extra}|{text}".encode()).hexdigest()
        if self.cache is not None:
            hit = self.cache.cache_get(cache_key)
            if hit:
                try:
                    self._count("cache_hit")
                    return validate(json.loads(hit)), "cache"
                except ValueError:
                    pass
        errors: List[str] = []
        for _round in range(self.max_rounds):
            for p in self.providers:
                if not p.enabled or p.cooldown_until > self.clock():
                    continue
                result = await self._try(p, system, text, make_prompt, validate, input_cap, errors)
                if result is not None:
                    if self.cache is not None:
                        self.cache.cache_put(cache_key, result[1], json.dumps(result[0], default=str))
                    return result
            enabled = [p for p in self.providers if p.enabled]
            if not enabled:
                break
            now = self.clock()
            waits = [p.cooldown_until - now for p in enabled if p.cooldown_until > now]
            if not waits:
                break  # nothing is merely rate-limited: every tier failed for other reasons
            await self.sleep(min(max(min(waits), 0.5), 120.0))
        raise AllProvidersFailed("; ".join(errors[-6:]) or "no providers configured")
