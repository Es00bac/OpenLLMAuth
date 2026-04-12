"""GitHub Copilot device code OAuth flow and token exchange."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx

# GitHub Copilot OAuth client ID (same as VS Code Copilot extension)
COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
COPILOT_TOKEN_EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"


@dataclass
class CopilotTokenResult:
    """Result of Copilot token exchange."""

    copilot_token: str
    expires_at: int  # milliseconds since epoch
    base_url: str  # derived from proxy-ep in token


@dataclass
class GitHubDeviceCodeResult:
    """Result of GitHub device code login."""

    github_token: str  # GitHub access token (PAT-like)


def derive_base_url_from_token(token: str) -> str:
    """Extract API base URL from the Copilot token's proxy-ep field."""
    match = re.search(r"(?:^|;)\s*proxy-ep=([^;\s]+)", token, re.IGNORECASE)
    if match:
        proxy_ep = match.group(1).strip()
        host = re.sub(r"^https?://", "", proxy_ep)
        host = re.sub(r"^proxy\.", "api.", host)
        return f"https://{host}"
    return "https://api.individual.githubcopilot.com"


async def exchange_github_token_for_copilot(
    github_token: str,
) -> CopilotTokenResult:
    """Exchange a GitHub access token for a Copilot API token."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            COPILOT_TOKEN_EXCHANGE_URL,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    copilot_token = data.get("token")
    expires_at_unix = data.get("expires_at", 0)

    if not copilot_token:
        raise RuntimeError("No Copilot token in response")

    # expires_at is in seconds, convert to ms
    expires_at_ms = int(expires_at_unix * 1000) if expires_at_unix else (
        int(time.time() * 1000) + 30 * 60 * 1000
    )
    base_url = derive_base_url_from_token(copilot_token)

    return CopilotTokenResult(
        copilot_token=copilot_token,
        expires_at=expires_at_ms,
        base_url=base_url,
    )


async def github_device_code_login() -> GitHubDeviceCodeResult:
    """Run the GitHub device code OAuth flow interactively."""
    import webbrowser

    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: Request device code
        resp = await client.post(
            "https://github.com/login/device/code",
            json={
                "client_id": COPILOT_CLIENT_ID,
                "scope": "read:user",
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        device_data = resp.json()

    device_code = device_data["device_code"]
    user_code = device_data["user_code"]
    verification_uri = device_data["verification_uri"]
    expires_in = device_data.get("expires_in", 900)
    interval = device_data.get("interval", 5)

    print(f"\n  GitHub Copilot Login")
    print(f"  ====================")
    print(f"  Open: {verification_uri}")
    print(f"  Enter code: {user_code}\n")

    try:
        webbrowser.open(verification_uri)
    except Exception:
        pass

    # Step 2: Poll for authorization
    deadline = time.time() + expires_in
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:
            await asyncio.sleep(interval)

            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                json={
                    "client_id": COPILOT_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )

            data = resp.json()
            error = data.get("error")

            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            elif error == "expired_token":
                raise RuntimeError("Device code expired. Please try again.")
            elif error == "access_denied":
                raise RuntimeError("Authorization denied by user.")
            elif error:
                raise RuntimeError(f"GitHub auth error: {error}")

            access_token = data.get("access_token")
            if access_token:
                print("  GitHub authorization successful!")
                return GitHubDeviceCodeResult(github_token=access_token)

    raise RuntimeError("Device code flow timed out.")


async def login_github_copilot() -> dict:
    """Full GitHub Copilot login: device code + token exchange.

    Returns dict with profile data ready for storage.
    """
    # Step 1: Get GitHub access token via device code
    device_result = await github_device_code_login()

    # Step 2: Exchange for Copilot token
    copilot_result = await exchange_github_token_for_copilot(
        device_result.github_token
    )

    print(f"  Copilot token obtained! API: {copilot_result.base_url}")

    return {
        "provider": "github-copilot",
        "type": "oauth",
        "access": copilot_result.copilot_token,  # Copilot token for API calls
        "refresh": device_result.github_token,  # GitHub token used to get fresh Copilot tokens
        "expires": copilot_result.expires_at,
        "baseUrl": copilot_result.base_url,
    }
