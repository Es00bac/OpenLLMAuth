"""OpenAI Codex (ChatGPT Plus/Pro) OAuth PKCE login flow."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse, parse_qs

import httpx


CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid profile email offline_access"
JWT_CLAIM_PATH = "https://api.openai.com/auth"

SUCCESS_HTML = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>Authentication successful</title></head>
<body><p>Authentication successful. Return to your terminal to continue.</p></body></html>"""


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    challenge_hash = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(challenge_hash).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _extract_account_id(token: str) -> Optional[str]:
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


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""
    code: Optional[str] = None
    expected_state: str = ""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        params = parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        if state != self.expected_state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"State mismatch")
            return

        code = params.get("code", [""])[0]
        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing authorization code")
            return

        _CallbackHandler.code = code
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(SUCCESS_HTML)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress server logs


async def run_openai_codex_oauth() -> Optional[Dict[str, Any]]:
    """Run the OpenAI Codex OAuth PKCE flow.

    Opens a browser for authentication and waits for the callback.
    Falls back to manual URL paste if the callback server can't bind.

    Returns dict with access, refresh, expires, account_id or None on failure.
    """
    verifier, challenge = _generate_pkce()
    state = secrets.token_hex(16)

    auth_params = urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "pi",
    })
    auth_url = f"{AUTHORIZE_URL}?{auth_params}"

    code: Optional[str] = None

    # Try to start local callback server
    server: Optional[HTTPServer] = None
    try:
        _CallbackHandler.code = None
        _CallbackHandler.expected_state = state
        server = HTTPServer(("127.0.0.1", 1455), _CallbackHandler)
        server.timeout = 1.0
    except OSError:
        server = None
        print("Could not bind to port 1455. You'll need to paste the redirect URL manually.")

    print(f"\nOpening browser for OpenAI authentication...")
    print(f"If the browser doesn't open, visit this URL:\n{auth_url}\n")
    webbrowser.open(auth_url)

    if server:
        print("Waiting for authentication callback...")
        # Wait up to 60 seconds for callback
        import time
        start = time.time()
        while time.time() - start < 60:
            server.handle_request()
            if _CallbackHandler.code:
                code = _CallbackHandler.code
                break
        server.server_close()

    if not code:
        # Fall back to manual paste
        print("\nPaste the full redirect URL or authorization code:")
        try:
            user_input = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            return None

        if not user_input:
            return None

        # Try to parse as URL
        try:
            parsed = urlparse(user_input)
            params = parse_qs(parsed.query)
            code = params.get("code", [""])[0]
            url_state = params.get("state", [""])[0]
            if url_state and url_state != state:
                print("State mismatch!")
                return None
        except Exception:
            code = user_input  # Assume raw code

    if not code:
        print("No authorization code received.")
        return None

    # Exchange code for tokens
    print("Exchanging authorization code for tokens...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            print(f"Token exchange failed: {response.status_code} {response.text}")
            return None

        data = response.json()
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in")

        if not access_token or not refresh_token or not isinstance(expires_in, (int, float)):
            print("Token response missing required fields.")
            return None

        import time
        account_id = _extract_account_id(access_token)
        if not account_id:
            print("Warning: Could not extract account ID from token.")

        return {
            "access": access_token,
            "refresh": refresh_token,
            "expires": int(time.time() * 1000) + int(expires_in * 1000),
            "account_id": account_id,
        }
