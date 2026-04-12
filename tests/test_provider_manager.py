from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from open_llm_auth.auth.manager import ProviderManager
from open_llm_auth.config import AuthProfile, Config, ProviderConfig
from open_llm_auth.providers import BedrockConverseProvider
from open_llm_auth.server.egress_policy import UnsafeDestinationError


def _clear_aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_PROFILE",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_builtin_vllm_does_not_self_activate_without_explicit_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProviderManager()
    manager._config = Config()
    provider_cfg = manager._resolve_provider_config("vllm")

    assert provider_cfg is not None

    with pytest.raises(ValueError, match="No credentials available for provider 'vllm'"):
        manager._resolve_api_keys(
            provider_id="vllm",
            provider_cfg=provider_cfg,
            preferred_profile=None,
        )


def test_builtin_ollama_does_not_self_activate_without_explicit_config() -> None:
    manager = ProviderManager()
    manager._config = Config()
    provider_cfg = manager._resolve_provider_config("ollama")

    assert provider_cfg is not None

    with pytest.raises(
        ValueError, match="No credentials available for provider 'ollama'"
    ):
        manager._resolve_api_keys(
            provider_id="ollama",
            provider_cfg=provider_cfg,
            preferred_profile=None,
        )


def test_builtin_bedrock_does_not_use_default_chain_without_explicit_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_aws_env(monkeypatch)

    manager = ProviderManager()
    manager._config = Config()
    provider_cfg = manager._resolve_provider_config("amazon-bedrock")

    assert provider_cfg is not None

    with pytest.raises(
        ValueError, match="No credentials available for provider 'amazon-bedrock'"
    ):
        manager._resolve_api_keys(
            provider_id="amazon-bedrock",
            provider_cfg=provider_cfg,
            preferred_profile=None,
        )


def test_explicit_no_auth_provider_config_still_allows_intentional_local_backends() -> None:
    manager = ProviderManager()
    manager._config = Config(
        providers={
            "vllm": ProviderConfig(auth_header=False),
        }
    )
    provider_cfg = manager._resolve_provider_config("vllm")

    assert provider_cfg is not None
    assert manager._resolve_api_keys(
        provider_id="vllm",
        provider_cfg=provider_cfg,
        preferred_profile=None,
    ) == [(None, "provider-config:no-auth-header", "api-key")]


def test_explicit_bedrock_provider_config_still_allows_default_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_aws_env(monkeypatch)

    manager = ProviderManager()
    manager._config = Config(
        providers={
            "amazon-bedrock": ProviderConfig(),
        }
    )
    provider_cfg = manager._resolve_provider_config("amazon-bedrock")

    assert provider_cfg is not None
    assert manager._resolve_api_keys(
        provider_id="amazon-bedrock",
        provider_cfg=provider_cfg,
        preferred_profile=None,
    ) == [(None, "aws-sdk:default-chain", "aws-sdk")]


def test_build_provider_supports_bedrock_adapter() -> None:
    manager = ProviderManager()
    manager._config = Config(
        providers={
            "amazon-bedrock": ProviderConfig(),
        }
    )
    provider_cfg = manager._resolve_provider_config("amazon-bedrock")
    assert provider_cfg is not None

    provider = manager._build_provider(
        provider_id="amazon-bedrock",
        provider_cfg=provider_cfg,
        api_key=None,
        auth_mode="aws-sdk",
        profile_id=None,
    )

    assert isinstance(provider, BedrockConverseProvider)
    assert provider.base_url == "https://bedrock-runtime.us-east-1.amazonaws.com"


def test_runtime_egress_policy_blocks_metadata_destination() -> None:
    manager = ProviderManager()
    manager._config = Config(
        providers={
            "evil": ProviderConfig(
                base_url="https://169.254.169.254/v1",
                api_key="secret",
            )
        }
    )
    manager.reload = lambda: None  # type: ignore[assignment]

    with pytest.raises(UnsafeDestinationError, match="Outbound destination blocked"):
        manager.resolve("evil/test-model")


def test_runtime_egress_policy_allows_agent_bridge_loopback() -> None:
    manager = ProviderManager()
    manager._config = Config(
        providers={
            "agent_bridge": ProviderConfig(
                base_url="http://127.0.0.1:20100/v1",
                auth_header=False,
                auth="api-key",
            )
        }
    )
    manager.reload = lambda: None  # type: ignore[assignment]

    resolved = manager.resolve("agent_bridge/assistant")

    assert resolved.provider_id == "agent_bridge"
    assert resolved.provider.base_url == "http://127.0.0.1:20100/v1"


def test_runtime_egress_policy_blocks_custom_loopback() -> None:
    manager = ProviderManager()
    manager._config = Config(
        providers={
            "custom": ProviderConfig(
                base_url="https://127.0.0.1/v1",
                api_key="secret",
            )
        }
    )
    manager.reload = lambda: None  # type: ignore[assignment]

    with pytest.raises(UnsafeDestinationError, match="Outbound destination blocked"):
        manager.resolve("custom/test-model")


def test_google_env_key_is_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-google-key")

    manager = ProviderManager()
    manager._config = Config()
    provider_cfg = ProviderConfig()

    result = manager._resolve_api_keys(
        provider_id="google",
        provider_cfg=provider_cfg,
        preferred_profile=None,
    )

    assert result[0][1].startswith("env:")
    assert result[0][0] == "env-google-key"


def test_google_env_key_takes_precedence_over_stored_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-google-key")

    manager = ProviderManager()
    manager._config = Config(
        authProfiles={
            "google:default": AuthProfile(
                provider="google",
                type="api_key",
                key="profile-google-key",
            )
        }
    )
    provider_cfg = ProviderConfig()

    result = manager._resolve_api_keys(
        provider_id="google",
        provider_cfg=provider_cfg,
        preferred_profile=None,
    )

    assert result[0][0] == "env-google-key"
    assert result[0][1].startswith("env:")
