from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from open_llm_auth.auth.manager import ProviderManager
from open_llm_auth.config import AuthProfile, Config


def _manager_with_anthropic_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProviderManager, AuthProfile]:
    monkeypatch.setattr(Config, "save", lambda self: None)
    manager = ProviderManager()
    profile = AuthProfile(
        provider="anthropic",
        type="oauth",
        access="old_access",
        refresh="old_refresh",
        expires=1,
    )
    manager._config = Config(auth_profiles={"anthropic:default": profile})
    return manager, profile


def test_refresh_claude_cli_credentials_legacy_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cred_path = tmp_path / ".claude" / ".credentials.json"
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    future_ms = int(time.time() * 1000) + 600_000
    cred_path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "new_access",
                    "refreshToken": "new_refresh",
                    "expiresAt": future_ms,
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    manager, profile = _manager_with_anthropic_profile(monkeypatch)

    refreshed = manager._refresh_anthropic_from_claude_cli("anthropic:default", profile)

    assert refreshed is not None
    assert profile.access == "new_access"
    assert profile.refresh == "new_refresh"
    assert profile.expires == future_ms


def test_refresh_claude_cli_credentials_nested_snake_case_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cred_path = tmp_path / ".claude" / "credentials.json"
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    future_seconds = int(time.time()) + 3_600
    cred_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "anthropic": {
                        "access_token": "snake_access",
                        "refresh_token": "snake_refresh",
                        "expires_at": str(future_seconds),
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    manager, profile = _manager_with_anthropic_profile(monkeypatch)

    refreshed = manager._refresh_anthropic_from_claude_cli("anthropic:default", profile)

    assert refreshed is not None
    assert profile.access == "snake_access"
    assert profile.refresh == "snake_refresh"
    assert profile.expires is not None
    assert profile.expires > int(time.time() * 1000)


def test_refresh_claude_cli_credentials_honors_override_and_iso_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cred_path = tmp_path / "custom-claude-creds.json"
    cred_path.write_text(
        json.dumps(
            {
                "auth": {
                    "oauth": {
                        "access": "iso_access",
                        "refresh": "iso_refresh",
                        "expiry": "2099-01-01T00:00:00Z",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("CLAUDE_CREDENTIALS_PATH", str(cred_path))
    manager, profile = _manager_with_anthropic_profile(monkeypatch)

    refreshed = manager._refresh_anthropic_from_claude_cli("anthropic:default", profile)

    assert refreshed is not None
    assert profile.access == "iso_access"
    assert profile.refresh == "iso_refresh"
    assert profile.expires is not None
    assert profile.expires > int(time.time() * 1000)


def test_refresh_claude_cli_credentials_rejects_expired_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cred_path = tmp_path / ".claude" / ".credentials.json"
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    past_ms = int(time.time() * 1000) - 60_000
    cred_path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "expired_access",
                    "refreshToken": "expired_refresh",
                    "expiresAt": past_ms,
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    manager, profile = _manager_with_anthropic_profile(monkeypatch)

    refreshed = manager._refresh_anthropic_from_claude_cli("anthropic:default", profile)

    assert refreshed is None
    assert profile.access == "old_access"
    assert profile.refresh == "old_refresh"
    assert profile.expires == 1


def test_refresh_claude_cli_credentials_ignores_unlabeled_token_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cred_path = tmp_path / ".claude" / ".credentials.json"
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    future_ms = int(time.time() * 1000) + 60_000
    cred_path.write_text(
        json.dumps(
            {
                "session": {
                    "token": "not_an_oauth_access_token",
                    "expiresAt": future_ms,
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    manager, profile = _manager_with_anthropic_profile(monkeypatch)

    refreshed = manager._refresh_anthropic_from_claude_cli("anthropic:default", profile)

    assert refreshed is None
    assert profile.access == "old_access"
    assert profile.refresh == "old_refresh"
    assert profile.expires == 1


def test_try_refresh_oauth_allows_anthropic_without_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProviderManager()
    profile = AuthProfile(
        provider="anthropic",
        type="oauth",
        access="old_access",
        refresh=None,
        expires=1,
    )

    called: dict[str, bool] = {"value": False}

    def _fake_refresh(profile_id: str, p: AuthProfile) -> AuthProfile:
        called["value"] = True
        return p

    monkeypatch.setattr(manager, "_refresh_anthropic_from_claude_cli", _fake_refresh)

    refreshed = manager._try_refresh_oauth("anthropic:default", profile)

    assert refreshed is profile
    assert called["value"] is True
