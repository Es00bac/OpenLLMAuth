"""Process-local idempotency primitives.

Invariant: a key is bound to a canonical payload fingerprint; matching payloads
replay, mismatched payloads conflict.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional


@dataclass(frozen=True)
class IdempotencyToken:
    key: str
    fingerprint: str


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: Dict[str, Any]
    created_at: float


@dataclass(frozen=True)
class ClaimResult:
    status: Literal["new", "replay", "conflict"]
    token: Optional[IdempotencyToken] = None
    response: Optional[StoredResponse] = None


@dataclass
class _Entry:
    fingerprint: str
    created_at: float
    future: "asyncio.Future[StoredResponse]"


class IdempotencyStore:
    """Bounded in-memory store for replay-safe request handling."""
    def __init__(self, *, ttl_seconds: int = 24 * 60 * 60, max_entries: int = 10000):
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._max_entries = max(1000, int(max_entries))
        self._entries: Dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def fingerprint(payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def claim(self, *, key: str, fingerprint: str) -> ClaimResult:
        """Claim a key for the current request or replay/conflict an existing one."""
        now = time.time()

        async with self._lock:
            self._prune_locked(now)
            entry = self._entries.get(key)
            if entry is None:
                loop = asyncio.get_running_loop()
                future: "asyncio.Future[StoredResponse]" = loop.create_future()
                self._entries[key] = _Entry(
                    fingerprint=fingerprint,
                    created_at=now,
                    future=future,
                )
                return ClaimResult(
                    status="new",
                    token=IdempotencyToken(key=key, fingerprint=fingerprint),
                )

            if entry.fingerprint != fingerprint:
                return ClaimResult(status="conflict")

            future = entry.future

        # Wait outside lock; in-flight duplicate calls will replay when first finishes.
        response = await future
        return ClaimResult(status="replay", response=response)

    async def store(
        self,
        token: IdempotencyToken,
        *,
        status_code: int,
        body: Dict[str, Any],
    ) -> None:
        """Publish the final response for a previously claimed idempotent request."""
        async with self._lock:
            entry = self._entries.get(token.key)
            if entry is None or entry.fingerprint != token.fingerprint:
                return
            if not entry.future.done():
                entry.future.set_result(
                    StoredResponse(
                        status_code=int(status_code),
                        body=body,
                        created_at=time.time(),
                    )
                )

    def _prune_locked(self, now: float) -> None:
        expiry_cutoff = now - self._ttl_seconds
        stale = [
            key
            for key, entry in self._entries.items()
            if entry.created_at < expiry_cutoff and entry.future.done()
        ]
        for key in stale:
            self._entries.pop(key, None)

        if len(self._entries) <= self._max_entries:
            return

        # Drop oldest completed entries first to keep memory bounded.
        ordered = sorted(
            self._entries.items(),
            key=lambda kv: kv[1].created_at,
        )
        for key, entry in ordered:
            if len(self._entries) <= self._max_entries:
                break
            if entry.future.done():
                self._entries.pop(key, None)
