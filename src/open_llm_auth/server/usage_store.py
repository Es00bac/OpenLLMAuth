"""SQLite usage aggregation store for the gateway.

Records token consumption, latency, and estimated cost per request so the
admin GUI can render usage charts and summaries.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class UsageRecord:
    ts: str
    provider: str
    model: str
    endpoint: str
    source: str
    profile_id: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    cost: float
    meta: Dict[str, Any]


class UsageStore:
    """Thread-safe SQLite usage store with daily aggregation helpers."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".open_llm_auth" / "usage.sqlite3"
        self._db_path = db_path
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'api',
                    profile_id TEXT,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    cost REAL NOT NULL DEFAULT 0.0,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_usage_ts
                ON usage_records(ts)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_usage_provider
                ON usage_records(provider)
                """
            )
            conn.commit()
            migrations = [
                "ALTER TABLE usage_records ADD COLUMN source TEXT NOT NULL DEFAULT 'api'",
                "ALTER TABLE usage_records ADD COLUMN profile_id TEXT",
                "ALTER TABLE usage_records ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'",
            ]
            for sql in migrations:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
            conn.commit()
            conn.close()

    def record(
        self,
        *,
        provider: str,
        model: str,
        endpoint: str,
        source: str = "api",
        profile_id: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: Optional[int] = None,
        latency_ms: int = 0,
        cost: float = 0.0,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO usage_records
                (ts, provider, model, endpoint, source, profile_id, prompt_tokens, completion_tokens, total_tokens, latency_ms, cost, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    provider,
                    model,
                    endpoint,
                    source or "api",
                    profile_id,
                    max(0, prompt_tokens),
                    max(0, completion_tokens),
                    max(0, total_tokens),
                    max(0, latency_ms),
                    max(0.0, cost),
                    json.dumps(meta or {}, ensure_ascii=True, sort_keys=True),
                ),
            )
            conn.commit()
            conn.close()

    def get_summary(self, days: int = 30) -> Dict[str, Any]:
        """Return high-level usage aggregates for the last N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as requests,
                    SUM(prompt_tokens) as prompt_tokens,
                    SUM(completion_tokens) as completion_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(cost) as total_cost,
                    AVG(latency_ms) as avg_latency_ms
                FROM usage_records
                WHERE ts >= ?
                """,
                (cutoff,),
            ).fetchone()
            conn.close()

        def _int(val: Any) -> int:
            return int(val or 0)

        def _float(val: Any) -> float:
            return float(val or 0.0)

        return {
            "days": days,
            "requests": _int(row["requests"]),
            "prompt_tokens": _int(row["prompt_tokens"]),
            "completion_tokens": _int(row["completion_tokens"]),
            "total_tokens": _int(row["total_tokens"]),
            "total_cost": round(_float(row["total_cost"]), 6),
            "avg_latency_ms": round(_float(row["avg_latency_ms"]), 2),
        }

    def get_chart_data(self, days: int = 30) -> Dict[str, Any]:
        """Return per-day time-series suitable for Chart.js."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT
                    date(ts) as day,
                    COUNT(*) as requests,
                    SUM(total_tokens) as total_tokens,
                    SUM(cost) as total_cost,
                    AVG(latency_ms) as avg_latency_ms
                FROM usage_records
                WHERE ts >= ?
                GROUP BY date(ts)
                ORDER BY day ASC
                """,
                (cutoff,),
            ).fetchall()
            conn.close()

        labels: List[str] = []
        requests_series: List[int] = []
        tokens_series: List[int] = []
        cost_series: List[float] = []
        latency_series: List[float] = []

        for row in rows:
            labels.append(str(row["day"]))
            requests_series.append(int(row["requests"] or 0))
            tokens_series.append(int(row["total_tokens"] or 0))
            cost_series.append(round(float(row["total_cost"] or 0.0), 4))
            latency_series.append(round(float(row["avg_latency_ms"] or 0.0), 2))

        return {
            "labels": labels,
            "requests": requests_series,
            "tokens": tokens_series,
            "cost": cost_series,
            "latency": latency_series,
        }

    def get_provider_breakdown(self, days: int = 30) -> List[Dict[str, Any]]:
        """Return per-provider aggregates for the last N days."""
        return self.get_breakdown("provider", days=days)

    def get_breakdown(self, field: str, days: int = 30) -> List[Dict[str, Any]]:
        """Return aggregate usage grouped by a supported field."""
        allowed = {"provider", "model", "endpoint", "source", "profile_id"}
        if field not in allowed:
            raise ValueError(f"Unsupported breakdown field '{field}'")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"""
                SELECT
                    COALESCE({field}, 'unknown') as label,
                    COUNT(*) as requests,
                    SUM(total_tokens) as total_tokens,
                    SUM(cost) as total_cost,
                    AVG(latency_ms) as avg_latency_ms
                FROM usage_records
                WHERE ts >= ?
                GROUP BY COALESCE({field}, 'unknown')
                ORDER BY total_tokens DESC, requests DESC
                """,
                (cutoff,),
            ).fetchall()
            conn.close()
        return [
            {
                field: row["label"],
                "requests": int(row["requests"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
                "total_cost": round(float(row["total_cost"] or 0.0), 4),
                "avg_latency_ms": round(float(row["avg_latency_ms"] or 0.0), 2),
            }
            for row in rows
        ]

    def get_latest_provider_meta(self, days: int = 30) -> List[Dict[str, Any]]:
        """Return the latest metadata blob seen for each provider/model pair."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT provider, model, source, profile_id, ts, meta_json
                FROM usage_records
                WHERE ts >= ?
                ORDER BY ts DESC
                """,
                (cutoff,),
            ).fetchall()
            conn.close()
        seen: set[tuple[str, str]] = set()
        items: List[Dict[str, Any]] = []
        for row in rows:
            key = (str(row["provider"] or "unknown"), str(row["model"] or "unknown"))
            if key in seen:
                continue
            seen.add(key)
            try:
                meta = json.loads(row["meta_json"] or "{}")
            except Exception:
                meta = {}
            items.append(
                {
                    "provider": key[0],
                    "model": key[1],
                    "source": row["source"] or "api",
                    "profile_id": row["profile_id"],
                    "ts": row["ts"],
                    "meta": meta,
                }
            )
        return items

    def get_overview(self, days: int = 30, recent_limit: int = 100) -> Dict[str, Any]:
        return {
            "summary": self.get_summary(days=days),
            "chart": self.get_chart_data(days=days),
            "providers": self.get_breakdown("provider", days=days),
            "models": self.get_breakdown("model", days=days),
            "endpoints": self.get_breakdown("endpoint", days=days),
            "sources": self.get_breakdown("source", days=days),
            "profiles": self.get_breakdown("profile_id", days=days),
            "recent": self.get_recent_records(limit=recent_limit),
            "latest_provider_meta": self.get_latest_provider_meta(days=days),
        }

    def get_recent_records(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return the most recent raw usage records."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT * FROM usage_records
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            conn.close()
        items: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["meta"] = json.loads(item.pop("meta_json", "{}") or "{}")
            except Exception:
                item["meta"] = {}
                item.pop("meta_json", None)
            items.append(item)
        return items


# Module-level singleton for convenience.
_default_store: Optional[UsageStore] = None


def get_usage_store() -> UsageStore:
    global _default_store
    if _default_store is None:
        _default_store = UsageStore()
    return _default_store
