"""OAuth token refresh for providers that support it."""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx


log = logging.getLogger(__name__)

# OpenAI Codex OAuth constants
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
JWT_CLAIM_PATH = "https://api.openai.com/auth"


@dataclass
class RefreshedCredentials:
    access: str
    refresh: str
    expires: int  # ms since epoch
    account_id: Optional[str] = None


def _extract_account_id_from_jwt(token: str) -> Optional[str]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.b64decode(payload_b64))
        auth_claim = payload.get(JWT_CLAIM_PATH, {})
        account_id = auth_claim.get("chatgpt_account_id")
        return account_id if isinstance(account_id, str) and account_id else None
    except Exception:
        return None


async def refresh_openai_codex_token(refresh_token: str) -> RefreshedCredentials:
    """Refresh an OpenAI Codex OAuth token using the refresh token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            OPENAI_CODEX_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OPENAI_CODEX_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            log.error("OpenAI Codex token refresh failed: %s %s", response.status_code, response.text)
            raise ValueError(f"Token refresh failed: HTTP {response.status_code}")

        data = response.json()
        access_token = data.get("access_token")
        new_refresh = data.get("refresh_token")
        expires_in = data.get("expires_in")

        if not access_token or not new_refresh or not isinstance(expires_in, (int, float)):
            raise ValueError("Token refresh response missing required fields")

        import time
        expires_ms = int(time.time() * 1000) + int(expires_in * 1000)
        account_id = _extract_account_id_from_jwt(access_token)

        return RefreshedCredentials(
            access=access_token,
            refresh=new_refresh,
            expires=expires_ms,
            account_id=account_id,
        )


# Qwen Portal OAuth constants
QWEN_OAUTH_TOKEN_URL = "https://chat.qwen.ai/api/v1/oauth2/token"
QWEN_OAUTH_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56"


async def refresh_qwen_portal_token(refresh_token: str) -> RefreshedCredentials:
    """Refresh a Qwen Portal OAuth token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            QWEN_OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": QWEN_OAUTH_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )

        if response.status_code == 400:
            raise ValueError("Qwen OAuth refresh token expired. Re-authenticate.")
        if response.status_code != 200:
            log.error("Qwen token refresh failed: %s %s", response.status_code, response.text)
            raise ValueError(f"Qwen token refresh failed: HTTP {response.status_code}")

        data = response.json()
        access_token = data.get("access_token", "").strip()
        new_refresh = data.get("refresh_token", "").strip() or refresh_token
        expires_in = data.get("expires_in")

        if not access_token:
            raise ValueError("Qwen refresh response missing access token")
        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            raise ValueError("Qwen refresh response missing or invalid expires_in")

        import time
        expires_ms = int(time.time() * 1000) + int(expires_in * 1000)

        return RefreshedCredentials(
            access=access_token,
            refresh=new_refresh,
            expires=expires_ms,
        )
