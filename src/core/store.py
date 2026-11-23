"""SQLite store: records, seen-URL freshness table, LLM cache, entity mapping log.

Swap for Postgres in production (see architecture doc); the interface stays the same.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .urls import url_hash

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_type TEXT NOT NULL,
    dedup_key   TEXT NOT NULL,
    source_url  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (record_type, dedup_key)
);
CREATE TABLE IF NOT EXISTS aux (
    kind       TEXT NOT NULL,
    dedup_key  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (kind, dedup_key)
);
CREATE TABLE IF NOT EXISTS seen_urls (
    url_hash   TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
    content_hash TEXT PRIMARY KEY,
    model        TEXT NOT NULL,
    response     TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mapping_log (
    entity_type    TEXT NOT NULL,
    raw_name       TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    match_method   TEXT NOT NULL,
    score          REAL,
    source_url     TEXT NOT NULL DEFAULT '',
    collected_at   TEXT NOT NULL,
    PRIMARY KEY (entity_type, raw_name, source_url)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str = "data/pipeline.db"):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    # ---- records ----
    def insert_record(self, record_type: str, key_url: str, payload: Dict[str, Any]) -> bool:
        """Insert unless (record_type, normalized key_url) exists. True if newly inserted."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO records VALUES (?,?,?,?,?)",
                (record_type, url_hash(key_url), key_url, json.dumps(payload, default=str), _now()),
            )
            return cur.rowcount == 1

    def update_payload(self, record_type: str, key_url: str, payload: Dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE records SET payload=? WHERE record_type=? AND dedup_key=?",
                (json.dumps(payload, default=str), record_type, url_hash(key_url)),
            )

    def has_record(self, record_type: str, key_url: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM records WHERE record_type=? AND dedup_key=?",
                (record_type, url_hash(key_url)),
            ).fetchone() is not None

    # ---- aux data (e.g. startup website/one-liner used later by the products collector) ----
    def put_aux(self, kind: str, key_url: str, payload: Dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO aux VALUES (?,?,?)",
                (kind, url_hash(key_url), json.dumps(payload, default=str)),
            )

    def get_aux(self, kind: str, key_url: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM aux WHERE kind=? AND dedup_key=?", (kind, url_hash(key_url))
            ).fetchone()
        return json.loads(row[0]) if row else None

    def clear_aux(self, kind: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM aux WHERE kind=?", (kind,))

    def has_aux(self, kind: str, key_url: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM aux WHERE kind=? AND dedup_key=?", (kind, url_hash(key_url))
            ).fetchone() is not None

    def aux_items(self, kind: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT payload FROM aux WHERE kind=?", (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def clear(self, record_type: str, aux_kinds: Optional[List[str]] = None) -> None:
        """Delete all records of a type (and optional aux kinds), e.g. before re-running with new rules."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM records WHERE record_type=?", (record_type,))
            for kind in aux_kinds or []:
                self._conn.execute("DELETE FROM aux WHERE kind=?", (kind,))

    def count(self, record_type: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM records WHERE record_type=?", (record_type,)
            ).fetchone()[0]

    def payloads(self, record_type: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM records WHERE record_type=? ORDER BY first_seen", (record_type,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # ---- freshness: never process the same URL twice ----
    def mark_seen(self, url: str) -> bool:
        """True if this URL was NOT seen before (and is now recorded)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO seen_urls VALUES (?,?,?)", (url_hash(url), url, _now())
            )
            return cur.rowcount == 1

    def was_seen(self, url: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM seen_urls WHERE url_hash=?", (url_hash(url),)
            ).fetchone() is not None

    # ---- LLM cache ----
    def cache_get(self, content_hash: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT response FROM llm_cache WHERE content_hash=?", (content_hash,)
            ).fetchone()
        return row[0] if row else None

    def cache_put(self, content_hash: str, model: str, response: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)",
                (content_hash, model, response, _now()),
            )

    # ---- entity mapping log ----
    def clear_mapping_log(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM mapping_log")

    def recreate_mapping_log_table(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DROP TABLE IF EXISTS mapping_log")
            self._conn.execute("""
                CREATE TABLE mapping_log (
                    entity_type    TEXT NOT NULL,
                    raw_name       TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    match_method   TEXT NOT NULL,
                    score          REAL,
                    source_url     TEXT NOT NULL DEFAULT '',
                    collected_at   TEXT NOT NULL,
                    PRIMARY KEY (entity_type, raw_name, source_url)
                )
            """)

    def log_mapping(self, entity_type: str, raw_name: str, canonical_name: str,
                    match_method: str, score: Optional[float], source_url: Optional[str]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO mapping_log VALUES (?,?,?,?,?,?,?)",
                (entity_type, raw_name, canonical_name, match_method, score, source_url or "", _now()),
            )

    def mapping_rows(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT raw_name, canonical_name, match_method, score, entity_type, source_url, collected_at "
                "FROM mapping_log ORDER BY entity_type, raw_name"
            ).fetchall()
        keys = ["raw_name", "canonical_name", "match_method", "score", "entity_type", "source.url", "collectedAt"]
        return [dict(zip(keys, r)) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
