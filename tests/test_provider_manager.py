from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from open_llm_auth.auth.manager import ProviderManager
from open_llm_auth.config import AuthProfile, Config, ProviderConfig, ProviderFallbackConfig
from open_llm_auth.providers import BedrockConverseProvider, LocalEmbeddingProvider
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


def test_active_provider_ids_limit_background_model_enumeration() -> None:
    manager = ProviderManager()
    manager._config = Config(
        activeProviderIds=["codex-cli", "kimi-coding", "zai-coding"],
    )

    providers = manager._all_provider_ids()

    assert providers == ["codex-cli", "kimi-coding", "zai-coding"]
    assert "anthropic" not in providers
    assert "claude-cli" not in providers
    assert "openai-codex" not in providers


def test_available_model_refs_include_active_cli_and_profile_backed_builtins() -> None:
    manager = ProviderManager()
    manager._config = Config(
        authProfiles={
            "kimi-coding:default": AuthProfile(
                provider="kimi-coding",
                type="api_key",
                key="test",
            )
        },
        authOrder={"kimi-coding": ["kimi-coding:default"]},
        activeProviderIds=["codex-cli", "kimi-coding"],
    )
    manager.reload = lambda: None  # type: ignore[assignment]

    refs = manager.available_model_refs()

    assert "codex-cli/gpt-5.5" in refs
    assert "kimi-coding/k2p6" in refs
    assert "anthropic/claude-sonnet-4-6" not in refs


def test_model_definition_comes_from_manager_catalog() -> None:
    manager = ProviderManager()
    manager._config = Config(activeProviderIds=["codex-cli"])
    manager.reload = lambda: None  # type: ignore[assignment]

    definition = manager.model_definition("codex-cli/gpt-5.5")

    assert definition is not None
    assert definition["contextWindow"] == 1000000
    assert definition["maxTokens"] == 128000


def test_openai_gpt_ref_uses_codex_cli_subscription_route_without_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manager = ProviderManager()
    manager._config = Config(activeProviderIds=["codex-cli", "openai"])
    manager.reload = lambda: None  # type: ignore[assignment]

    resolved = manager.resolve("openai/gpt-5.5")

    assert resolved.provider_id == "codex-cli"
    assert resolved.model_id == "gpt-5.5"
    assert resolved.auth_source == "cli:self-authenticated"


def test_openai_gpt_ref_strips_claude_code_1m_suffix_before_provider_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manager = ProviderManager()
    manager._config = Config(activeProviderIds=["codex-cli", "openai"])
    manager.reload = lambda: None  # type: ignore[assignment]

    resolved = manager.resolve("openai/gpt-5.5[1m]")

    assert resolved.provider_id == "codex-cli"
    assert resolved.model_id == "gpt-5.5"
    assert resolved.auth_source == "cli:self-authenticated"


def test_openai_gpt_ref_prefers_openai_codex_oauth_over_codex_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manager = ProviderManager()
    manager._config = Config(
        authProfiles={
            "openai-codex:test": AuthProfile(
                provider="openai-codex",
                type="oauth",
                access="test-access",
                refresh="test-refresh",
                expires=4102444800000,
            )
        },
        authOrder={"openai-codex": ["openai-codex:test"]},
        activeProviderIds=["codex-cli", "openai", "openai-codex"],
    )
    manager.reload = lambda: None  # type: ignore[assignment]

    resolved = manager.resolve("openai/gpt-5.5")

    assert resolved.provider_id == "openai-codex"
    assert resolved.model_id == "gpt-5.5"
    assert resolved.auth_source == "profile:openai-codex:test"


def test_openai_gpt_ref_preserves_direct_openai_api_key_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    manager = ProviderManager()
    manager._config = Config(activeProviderIds=["codex-cli", "openai"])
    manager.reload = lambda: None  # type: ignore[assignment]

    resolved = manager.resolve("openai/gpt-5.5")

    assert resolved.provider_id == "openai"
    assert resolved.model_id == "gpt-5.5"
    assert resolved.auth_source == "env:OPENAI_API_KEY"


def test_openai_gpt_ref_can_use_openai_codex_oauth_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manager = ProviderManager()
    manager._config = Config(
        authProfiles={
            "openai-codex:test": AuthProfile(
                provider="openai-codex",
                type="oauth",
                access="test-access",
                refresh="test-refresh",
                expires=4102444800000,
            )
        },
        authOrder={"openai-codex": ["openai-codex:test"]},
        activeProviderIds=["openai", "openai-codex"],
    )
    manager.reload = lambda: None  # type: ignore[assignment]

    resolved = manager.resolve("openai/gpt-5.5")

    assert resolved.provider_id == "openai-codex"
    assert resolved.model_id == "gpt-5.5"
    assert resolved.auth_source == "profile:openai-codex:test"


def test_available_model_refs_include_canonical_openai_codex_alias() -> None:
    manager = ProviderManager()
    manager._config = Config(activeProviderIds=["codex-cli"])
    manager.reload = lambda: None  # type: ignore[assignment]

    refs = manager.available_model_refs()

    assert "codex-cli/gpt-5.5" in refs
    assert "openai/gpt-5.5" in refs


def test_embedding_model_inventory_is_gateway_authoritative() -> None:
    manager = ProviderManager()
    manager._config = Config(activeProviderIds=["google"])
    manager.reload = lambda: None  # type: ignore[assignment]

    refs = manager.embedding_model_refs(require_credentials=False)
    default_definition = manager.embedding_model_definition(
        ProviderManager.default_embedding_model_ref()
    )
    google_definition = manager.embedding_model_definition("google/gemini-embedding-2-preview")

    assert ProviderManager.default_embedding_model_ref() in refs
    assert ProviderManager.offline_embedding_model_ref() in refs
    assert "google/gemini-embedding-2-preview" in refs
    assert default_definition is not None
    assert default_definition["dimensions"] == 768
    assert default_definition["local_runtime"] == "sentence_transformers"
    assert google_definition is not None
    assert google_definition["dimensions"] == 768
    assert google_definition["type"] == "embedding"


def test_local_embedding_refs_resolve_without_external_credentials() -> None:
    manager = ProviderManager()
    manager._config = Config()
    manager.reload = lambda: None  # type: ignore[assignment]

    resolved = manager.resolve(ProviderManager.default_embedding_model_ref())
    offline = manager.resolve(ProviderManager.offline_embedding_model_ref())

    assert resolved.provider_id == "local-embeddings"
    assert resolved.model_id == ProviderManager.default_embedding_model_ref()
    assert isinstance(resolved.provider, LocalEmbeddingProvider)
    assert resolved.auth_source == "local-embedding:self-contained"
    assert offline.provider_id == "local-embeddings"


@pytest.mark.asyncio
async def test_local_embedding_provider_serves_hash_fallback_without_credentials() -> None:
    manager = ProviderManager()
    manager._config = Config()
    manager.reload = lambda: None  # type: ignore[assignment]
    resolved = manager.resolve(ProviderManager.offline_embedding_model_ref())

    response = await resolved.provider.embeddings(
        model=resolved.model_id,
        input_texts=["offline vector"],
        payload={},
    )

    assert response["model"] == ProviderManager.offline_embedding_model_ref()
    assert len(response["data"]) == 1
    assert len(response["data"][0]["embedding"]) == 256
    assert response["_open_llm_auth"]["provider"] == "local-embeddings"
    assert response["_open_llm_auth"]["local_runtime"] == "hash"


@pytest.mark.asyncio
async def test_default_local_embedding_model_degrades_to_hash_without_opt_in() -> None:
    manager = ProviderManager()
    manager._config = Config()
    manager.reload = lambda: None  # type: ignore[assignment]
    resolved = manager.resolve(ProviderManager.default_embedding_model_ref())

    response = await resolved.provider.embeddings(
        model=resolved.model_id,
        input_texts=["default local vector"],
        payload={},
    )

    assert len(response["data"][0]["embedding"]) == 768
    assert response["_open_llm_auth"]["local_runtime"] == "sentence_transformers"
    assert response["_open_llm_auth"]["degraded"] is True
    assert "disabled" in response["_open_llm_auth"]["degraded_reason"]


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


def test_resolve_builds_ordered_cross_provider_fallback_chain() -> None:
    manager = ProviderManager()
    manager._config = Config(
        providers={
            "agent_bridge": ProviderConfig(base_url="http://127.0.0.1:20100/v1", auth_header=False),
            "agent": ProviderConfig(base_url="http://127.0.0.1:20100/v1", auth_header=False),
        },
        provider_fallback=ProviderFallbackConfig(
            order={"agent_bridge/assistant": ["agent/assistant"]},
            max_attempts=4,
        )
    )
    manager.reload = lambda: None  # type: ignore[assignment]

    resolved = manager.resolve("agent_bridge/assistant")

    assert [(a.provider_id, a.model_id, a.fallback_index) for a in resolved.attempts] == [
        ("agent_bridge", "assistant", 0),
        ("agent", "assistant", 1),
    ]
    assert resolved.retry_status_codes == [429, 500, 502, 503, 504]


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
