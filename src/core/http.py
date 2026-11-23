"""Async HTTP core: global + per-domain concurrency, per-domain pacing, robots.txt,
retries with exponential backoff + jitter (honors Retry-After), and a per-domain
circuit breaker that stops hammering a source that keeps returning 403/429.

Polite by design: honest User-Agent, robots.txt respected for crawled pages
(official APIs pass respect_robots=False and follow their own rate-limit docs).
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import aiohttp

from .retry import compute_backoff, parse_retry_after

log = logging.getLogger("http")

# Errors that will not fix themselves on retry (expired cert, DNS failure, bad URL).
_NO_RETRY = tuple(
    getattr(aiohttp, n) for n in
    ("ClientConnectorCertificateError", "ClientSSLError", "ClientConnectorDNSError", "InvalidURL")
    if hasattr(aiohttp, n)
)


class FetchError(Exception):
    pass


class BlockedError(FetchError):
    """403/401, robots disallow, or circuit breaker open. Do not retry; skip the source."""


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    headers: Dict[str, str] = field(default_factory=dict)
    elapsed: float = 0.0
    final_url: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class _Domain:
    def __init__(self, limit: int, interval: float):
        self.sem = asyncio.Semaphore(limit)
        self.interval = interval
        self.lock = asyncio.Lock()
        self.next_at = 0.0
        self.blocks = 0
        self.open_until = 0.0


class AsyncFetcher:
    def __init__(self, user_agent: str, global_limit: int = 50, per_domain_limit: int = 5,
                 per_domain_interval: float = 0.2, timeout: float = 30.0, max_retries: int = 5,
                 breaker_threshold: int = 5, breaker_cooldown: float = 120.0,
                 domain_limits: Optional[Dict[str, int]] = None,
                 domain_intervals: Optional[Dict[str, float]] = None):
        self.user_agent = user_agent
        self.global_limit = global_limit
        self.per_domain_limit = per_domain_limit
        self.per_domain_interval = per_domain_interval
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.breaker_threshold = breaker_threshold
        self.breaker_cooldown = breaker_cooldown
        self.domain_limits = domain_limits or {}        # host -> max concurrent requests
        self.domain_intervals = domain_intervals or {}  # host -> min seconds between requests
        self._session: Optional[aiohttp.ClientSession] = None
        self._global: Optional[asyncio.Semaphore] = None
        self._domains: Dict[str, _Domain] = {}
        self._robots: Dict[str, RobotFileParser] = {}
        self._robots_locks: Dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> "AsyncFetcher":
        self._global = asyncio.Semaphore(self.global_limit)
        self._session = aiohttp.ClientSession(
            timeout=self.timeout,
            max_line_size=32768, max_field_size=32768,  # some sites send >8KB headers (big CSPs)
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            connector=aiohttp.TCPConnector(limit=self.global_limit, ttl_dns_cache=300),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()

    # ---- internals ----
    def _domain(self, url: str) -> _Domain:
        host = urlsplit(url).netloc.lower()
        if host not in self._domains:
            self._domains[host] = _Domain(
                self.domain_limits.get(host, self.per_domain_limit),
                self.domain_intervals.get(host, self.per_domain_interval),
            )
        return self._domains[host]

    async def _pace(self, d: _Domain) -> None:
        async with d.lock:
            now = time.monotonic()
            wait = d.next_at - now
            d.next_at = max(now, d.next_at) + d.interval
        if wait > 0:
            await asyncio.sleep(wait)

    def _note_block(self, d: _Domain, host: str) -> None:
        d.blocks += 1
        if d.blocks >= self.breaker_threshold:
            d.open_until = time.monotonic() + self.breaker_cooldown
            d.blocks = 0
            log.warning("circuit open for %s (%.0fs)", host, self.breaker_cooldown)

    async def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            rp = self._robots.get(origin)
            if rp is None:
                rp = RobotFileParser()
                try:
                    res = await self.get(origin + "/robots.txt", respect_robots=False, max_retries=1)
                    if res.status == 200:
                        rp.parse(res.text.splitlines())
                    elif 400 <= res.status < 500:
                        rp.allow_all = True
                    else:
                        rp.disallow_all = True
                except FetchError:
                    rp.disallow_all = True
                self._robots[origin] = rp
        return rp.can_fetch(self.user_agent, url)

    # ---- public ----
    async def get(self, url: str, **kw: Any) -> FetchResult:
        return await self.request("GET", url, **kw)

    async def request(self, method: str, url: str, *, params: Optional[dict] = None,
                      headers: Optional[dict] = None, json_body: Any = None,
                      respect_robots: bool = True, max_retries: Optional[int] = None,
                      retry_on_429: bool = True) -> FetchResult:
        assert self._session is not None, "use `async with AsyncFetcher(...)`"
        retries = self.max_retries if max_retries is None else max_retries
        d = self._domain(url)
        host = urlsplit(url).netloc.lower()
        if respect_robots and not await self._allowed(url):
            raise BlockedError(f"robots.txt disallows {url}")

        last_error: Any = None
        for attempt in range(retries + 1):
            if time.monotonic() < d.open_until:
                raise BlockedError(f"circuit open for {host}")
            retry_after: Optional[float] = None
            async with self._global, d.sem:
                await self._pace(d)
                started = time.monotonic()
                try:
                    async with self._session.request(method, url, params=params, headers=headers,
                                                    json=json_body) as resp:
                        status = resp.status
                        if status in (401, 403):
                            self._note_block(d, host)
                            raise BlockedError(f"{status} from {host} for {url}")
                        if 520 <= status <= 530:  # Cloudflare "origin down/unreachable": retrying won't help
                            raise FetchError(f"HTTP {status} (origin unavailable) for {url}")
                        if status == 429 and not retry_on_429:  # caller handles quotas; return the body as-is
                            body = await resp.text(errors="replace")
                            return FetchResult(url, status, body, dict(resp.headers),
                                               time.monotonic() - started, str(resp.url))
                        if status == 429 or status >= 500:
                            retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                            if status == 429:
                                self._note_block(d, host)
                            last_error = f"HTTP {status}"
                        else:
                            d.blocks = 0
                            text = await resp.text(errors="replace")
                            return FetchResult(url, status, text, dict(resp.headers),
                                               time.monotonic() - started, str(resp.url))
                except _NO_RETRY as exc:
                    raise FetchError(f"{type(exc).__name__} for {url}") from exc
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_error = repr(exc)
            if attempt == retries:
                break
            delay = compute_backoff(attempt, retry_after=retry_after)
            log.info("retry %d/%d %s in %.1fs (%s)", attempt + 1, retries, url, delay, last_error)
            await asyncio.sleep(delay)
        raise FetchError(f"giving up on {url}: {last_error}")

    async def get_json(self, url: str, **kw) -> Any:
        res = await self.get(url, **kw)
        if not res.ok:
            raise FetchError(f"HTTP {res.status} for {url}")
        import json
        return json.loads(res.text)
