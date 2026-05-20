from __future__ import annotations

import httpx

from open_llm_auth.auth.oauth_refresh import _openai_codex_refresh_error_message


def test_openai_codex_refresh_error_names_reused_refresh_token() -> None:
    response = httpx.Response(
        401,
        json={
            "error": {
                "message": "Your refresh token has already been used to generate a new access token. Please try signing in again.",
                "code": "refresh_token_reused",
            }
        },
        request=httpx.Request("POST", "https://auth.openai.com/oauth/token"),
    )

    message = _openai_codex_refresh_error_message(response)

    assert "refresh_token_reused" in message
    assert "sign in again" in message
