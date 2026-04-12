"""Bearer-token authentication and scope enforcement for the gateway."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import FrozenSet, Literal, Optional

from fastapi import Header, HTTPException

from ..config import load_config


@dataclass(frozen=True)
class Principal:
    """Authenticated caller identity propagated through route handlers."""
    subject: str
    token_id: str
    scopes: FrozenSet[str]
    is_admin: bool
    source: Literal["configured_token", "legacy_server_token", "anonymous"]


def _allow_anonymous_access() -> bool:
    raw = os.getenv("OPEN_LLM_AUTH_ALLOW_ANON", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return token


def _normalize_scopes(scopes: list[str], *, admin: bool) -> FrozenSet[str]:
    normalized = {scope.strip().lower() for scope in scopes if isinstance(scope, str) and scope.strip()}
    if admin:
        normalized.update({"read", "write", "admin"})
    return frozenset(normalized)


def require_scopes(
    principal: Principal,
    *required: str,
    allow_admin: bool = True,
) -> Principal:
    required_scopes = [s.strip().lower() for s in required if s and s.strip()]
    if allow_admin and principal.is_admin:
        return principal
    missing = [scope for scope in required_scopes if scope not in principal.scopes]
    if missing:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "insufficient_scope",
                "required": missing,
            },
        )
    return principal


def verify_server_token(authorization: Optional[str] = Header(default=None)) -> Principal:
    """Authenticate a caller against configured scoped tokens or legacy fallback.

    Resolution order matters:
    1. explicit configured access tokens
    2. legacy admin token compatibility (`serverToken` / `OPEN_LLM_AUTH_TOKEN`)
    3. optional anonymous override when no tokens exist and `OPEN_LLM_AUTH_ALLOW_ANON=1`
    """
    cfg = load_config()
    configured = []
    for token_key, token_cfg in cfg.authorization.tokens.items():
        token_value = (token_cfg.token or "").strip()
        if not token_cfg.enabled or not token_value:
            continue
        configured.append((token_key, token_cfg, token_value))

    legacy_enabled = bool(cfg.authorization.legacy_admin_compatibility)
    server_token = (cfg.server_token or "").strip()
    env_token = os.getenv("OPEN_LLM_AUTH_TOKEN", "").strip()
    legacy_token = (server_token or env_token).strip() if legacy_enabled else ""

    if not configured and not legacy_token:
        if _allow_anonymous_access():
            return Principal(
                subject="anonymous",
                token_id="anonymous",
                scopes=frozenset({"read", "write", "admin"}),
                is_admin=True,
                source="anonymous",
            )
        raise HTTPException(
            status_code=401,
            detail=(
                "Server token is not configured. Set serverToken in config or "
                "OPEN_LLM_AUTH_TOKEN."
            ),
        )

    token = _extract_bearer_token(authorization)
    configured_matches = [
        (token_key, token_cfg)
        for token_key, token_cfg, token_value in configured
        if secrets.compare_digest(token, token_value)
    ]
    if len(configured_matches) > 1:
        raise HTTPException(status_code=401, detail="Ambiguous access token configuration")

    if configured_matches:
        token_key, token_cfg = configured_matches[0]
        token_id = (token_cfg.id or token_key).strip() or token_key
        is_admin = bool(token_cfg.admin)
        return Principal(
            subject=token_id,
            token_id=token_id,
            scopes=_normalize_scopes(token_cfg.scopes, admin=is_admin),
            is_admin=is_admin,
            source="configured_token",
        )

    if legacy_token and secrets.compare_digest(token, legacy_token):
        return Principal(
            subject="legacy-admin",
            token_id="legacy-server-token",
            scopes=frozenset({"read", "write", "admin"}),
            is_admin=True,
            source="legacy_server_token",
        )

    raise HTTPException(status_code=401, detail="Invalid server token")


def verify_admin_token(authorization: Optional[str] = Header(default=None)) -> Principal:
    """Authenticate and then require admin scope for config-mutating surfaces."""
    principal = verify_server_token(authorization=authorization)
    return require_scopes(principal, "admin")
