from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple

from ..config import Config


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class DurableIdempotencyToken:
    key: str
    scope: str
    subject: str
    fingerprint: str


@dataclass(frozen=True)
class DurableStoredResponse:
    status_code: int
    body: Dict[str, Any]
    created_at_ms: int


@dataclass(frozen=True)
class DurableClaimResult:
    status: Literal["new", "replay", "conflict", "in_progress"]
    token: Optional[DurableIdempotencyToken] = None
    response: Optional[DurableStoredResponse] = None


class DurableControlStateStore:
    def __init__(
        self,
        *,
        db_path: str,
        idempotency_ttl_seconds: int,
        pending_lease_seconds: int,
    ):
        path = Path(db_path).expanduser()
        if path.exists() and path.is_symlink():
            raise RuntimeError("Durable state db path cannot be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(path)
        self._idempotency_ttl_ms = max(60, int(idempotency_ttl_seconds)) * 1000
        self._pending_lease_ms = max(5, int(pending_lease_seconds)) * 1000
        self._lock = asyncio.Lock()
        self._pending_futures: Dict[str, "asyncio.Future[DurableStoredResponse]"] = {}
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._initialize_schema()
        try:
            os.chmod(self._db_path, 0o600)
        except OSError:
            # Best effort on platforms/filesystems that do not support chmod.
            pass

    @staticmethod
    def fingerprint(payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _configure(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=FULL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA trusted_schema=OFF;")

    def _initialize_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                idempotency_key TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                subject TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                lease_expires_at_ms INTEGER NOT NULL,
                response_status INTEGER,
                response_body_json TEXT,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_idempotency_expires
            ON idempotency_keys(expires_at_ms);

            CREATE TABLE IF NOT EXISTS task_ownership (
                owner_key TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                owner_subject TEXT NOT NULL,
                created_by_subject TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_task_ownership_lookup
            ON task_ownership(provider_id, task_id);

            CREATE TABLE IF NOT EXISTS task_action_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL,
                task_id TEXT,
                action TEXT NOT NULL,
                subject TEXT NOT NULL,
                outcome TEXT NOT NULL,
                idempotency_key TEXT,
                status_code INTEGER,
                details_json TEXT,
                created_at_ms INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_task_action_history_lookup
            ON task_action_history(provider_id, task_id, created_at_ms);
            """
        )
        self._conn.commit()

    def _purge_expired_locked(self, now_ms: int) -> None:
        self._conn.execute(
            """
            DELETE FROM idempotency_keys
            WHERE expires_at_ms < ? AND state != 'pending'
            """,
            (now_ms,),
        )
        self._conn.commit()

    async def claim_idempotency(
        self,
        *,
        key: str,
        scope: str,
        subject: str,
        fingerprint: str,
    ) -> DurableClaimResult:
        wait_future: Optional["asyncio.Future[DurableStoredResponse]"] = None
        async with self._lock:
            now_ms = _now_ms()
            self._purge_expired_locked(now_ms)

            row = self._conn.execute(
                """
                SELECT idempotency_key, scope, subject, fingerprint, state,
                       lease_expires_at_ms, response_status, response_body_json,
                       created_at_ms
                FROM idempotency_keys
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()

            if row is None:
                lease_expires = now_ms + self._pending_lease_ms
                expires_at = now_ms + self._idempotency_ttl_ms
                self._conn.execute(
                    """
                    INSERT INTO idempotency_keys (
                        idempotency_key, scope, subject, fingerprint, state,
                        lease_expires_at_ms, created_at_ms, updated_at_ms, expires_at_ms
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (key, scope, subject, fingerprint, lease_expires, now_ms, now_ms, expires_at),
                )
                self._conn.commit()
                loop = asyncio.get_running_loop()
                self._pending_futures[key] = loop.create_future()
                return DurableClaimResult(
                    status="new",
                    token=DurableIdempotencyToken(
                        key=key,
                        scope=scope,
                        subject=subject,
                        fingerprint=fingerprint,
                    ),
                )

            if (
                row["scope"] != scope
                or row["subject"] != subject
                or row["fingerprint"] != fingerprint
            ):
                return DurableClaimResult(status="conflict")

            if row["state"] == "completed" and row["response_status"] is not None:
                payload = row["response_body_json"] or "{}"
                parsed = json.loads(payload)
                if not isinstance(parsed, dict):
                    parsed = {}
                return DurableClaimResult(
                    status="replay",
                    response=DurableStoredResponse(
                        status_code=int(row["response_status"]),
                        body=parsed,
                        created_at_ms=int(row["created_at_ms"]),
                    ),
                )

            if row["state"] == "pending":
                lease_expires = int(row["lease_expires_at_ms"] or 0)
                if lease_expires <= now_ms:
                    new_lease = now_ms + self._pending_lease_ms
                    self._conn.execute(
                        """
                        UPDATE idempotency_keys
                        SET lease_expires_at_ms = ?, updated_at_ms = ?, expires_at_ms = ?
                        WHERE idempotency_key = ?
                        """,
                        (new_lease, now_ms, now_ms + self._idempotency_ttl_ms, key),
                    )
                    self._conn.commit()
                    loop = asyncio.get_running_loop()
                    self._pending_futures[key] = loop.create_future()
                    return DurableClaimResult(
                        status="new",
                        token=DurableIdempotencyToken(
                            key=key,
                            scope=scope,
                            subject=subject,
                            fingerprint=fingerprint,
                        ),
                    )
                wait_future = self._pending_futures.get(key)

        if wait_future is None:
            return DurableClaimResult(status="in_progress")

        try:
            response = await asyncio.wait_for(wait_future, timeout=self._pending_lease_ms / 1000.0)
            return DurableClaimResult(status="replay", response=response)
        except asyncio.TimeoutError:
            return DurableClaimResult(status="in_progress")

    async def store_idempotency(
        self,
        token: DurableIdempotencyToken,
        *,
        status_code: int,
        body: Dict[str, Any],
    ) -> bool:
        async with self._lock:
            now_ms = _now_ms()
            expires_at = now_ms + self._idempotency_ttl_ms
            payload = json.dumps(body, ensure_ascii=True, separators=(",", ":"))
            cur = self._conn.execute(
                """
                UPDATE idempotency_keys
                SET state = 'completed',
                    response_status = ?,
                    response_body_json = ?,
                    updated_at_ms = ?,
                    expires_at_ms = ?
                WHERE idempotency_key = ?
                  AND scope = ?
                  AND subject = ?
                  AND fingerprint = ?
                """,
                (
                    int(status_code),
                    payload,
                    now_ms,
                    expires_at,
                    token.key,
                    token.scope,
                    token.subject,
                    token.fingerprint,
                ),
            )
            self._conn.commit()
            updated = cur.rowcount > 0
            future = self._pending_futures.pop(token.key, None)
            if future is not None and not future.done():
                future.set_result(
                    DurableStoredResponse(
                        status_code=int(status_code),
                        body=body,
                        created_at_ms=now_ms,
                    )
                )
            return updated

    async def claim_task_owner(
        self,
        *,
        provider_id: str,
        task_id: str,
        owner_subject: str,
        created_by_subject: Optional[str] = None,
    ) -> bool:
        owner_key = self._owner_key(provider_id, task_id)
        async with self._lock:
            row = self._conn.execute(
                "SELECT owner_subject FROM task_ownership WHERE owner_key = ?",
                (owner_key,),
            ).fetchone()
            if row is None:
                now_ms = _now_ms()
                self._conn.execute(
                    """
                    INSERT INTO task_ownership (
                        owner_key, provider_id, task_id, owner_subject, created_by_subject, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner_key,
                        provider_id.strip().lower(),
                        task_id.strip(),
                        owner_subject,
                        created_by_subject or owner_subject,
                        now_ms,
                    ),
                )
                self._conn.commit()
                return True
            return str(row["owner_subject"]) == owner_subject

    async def get_task_owner(self, *, provider_id: str, task_id: str) -> Optional[str]:
        owner_key = self._owner_key(provider_id, task_id)
        async with self._lock:
            row = self._conn.execute(
                "SELECT owner_subject FROM task_ownership WHERE owner_key = ?",
                (owner_key,),
            ).fetchone()
            if row is None:
                return None
            return str(row["owner_subject"])

    async def append_task_action(
        self,
        *,
        provider_id: str,
        task_id: Optional[str],
        action: str,
        subject: str,
        outcome: str,
        idempotency_key: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            details_json: Optional[str] = None
            if details:
                details_json = json.dumps(details, ensure_ascii=True, separators=(",", ":"))
            self._conn.execute(
                """
                INSERT INTO task_action_history (
                    provider_id, task_id, action, subject, outcome, idempotency_key, status_code, details_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_id.strip().lower(),
                    task_id,
                    action,
                    subject,
                    outcome,
                    idempotency_key,
                    status_code,
                    details_json,
                    _now_ms(),
                ),
            )
            self._conn.commit()

    @staticmethod
    def _owner_key(provider_id: str, task_id: str) -> str:
        return f"{provider_id.strip().lower()}::{task_id.strip()}"


_STORE_CACHE: Dict[Tuple[str, int, int], DurableControlStateStore] = {}


def get_durable_state_store(cfg: Config) -> DurableControlStateStore:
    db_path = (cfg.durable_state.db_path or "").strip()
    if not db_path:
        raise RuntimeError("durableState.dbPath is required when durable state is enabled")
    key = (
        str(Path(db_path).expanduser()),
        int(cfg.durable_state.idempotency_ttl_seconds),
        int(cfg.durable_state.pending_lease_seconds),
    )
    store = _STORE_CACHE.get(key)
    if store is None:
        store = DurableControlStateStore(
            db_path=key[0],
            idempotency_ttl_seconds=key[1],
            pending_lease_seconds=key[2],
        )
        _STORE_CACHE[key] = store
    return store


def reset_durable_state_store_cache() -> None:
    _STORE_CACHE.clear()
