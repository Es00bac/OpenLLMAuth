"""Guided setup metadata for provider families exposed through OpenLLMAuth."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional


PROVIDER_SETUP_CATALOG: Dict[str, Dict[str, Any]] = {
    "openai": {
        "family_id": "openai",
        "display_name": "OpenAI / ChatGPT",
        "description": "Configure ChatGPT Pro style access through the Codex app login, or use a direct OpenAI API key.",
        "usage_note": "OpenAI-compatible adapters expose live rate-limit telemetry when headers are available. Billing and subscription state are not queried by this gateway.",
        "presets": [
            {
                "preset_id": "codex_cli",
                "provider_id": "codex-cli",
                "label": "ChatGPT Pro via Codex app login",
                "auth_kind": "cli",
                "recommended": True,
                "supports_auth_profile": False,
                "supports_api_key": False,
                "supports_custom_base_url": False,
                "supports_custom_models": True,
                "default_model": "openai/gpt-5.5",
                "help_text": "Uses the canonical OpenAI model route backed by the local Codex app / CLI login instead of an API key.",
            },
            {
                "preset_id": "api_key",
                "provider_id": "openai",
                "label": "OpenAI API key",
                "auth_kind": "api_key",
                "recommended": False,
                "supports_auth_profile": True,
                "supports_api_key": True,
                "supports_custom_base_url": True,
                "supports_custom_models": True,
                "default_model": "openai/gpt-5.5",
                "help_text": "Use this only if you also have platform API access.",
            },
            {
                "preset_id": "oauth_tokens",
                "provider_id": "openai-codex",
                "label": "OpenAI Codex OAuth tokens (advanced)",
                "auth_kind": "oauth",
                "recommended": False,
                "supports_auth_profile": True,
                "supports_api_key": False,
                "supports_custom_base_url": False,
                "supports_custom_models": True,
                "default_model": "openai/gpt-5.5",
                "help_text": "Advanced token-based setup for environments that already manage Codex OAuth refresh tokens; the model route stays canonical as openai/gpt-*.",
            },
        ],
    },
    "anthropic": {
        "family_id": "anthropic",
        "display_name": "Anthropic / Claude",
        "description": "Configure Claude Plus access through the local Claude app login, or use a direct Anthropic API key.",
        "usage_note": "Anthropic-compatible adapters surface live rate-limit headers when the upstream sends them. Account billing data is not queried here.",
        "presets": [
            {
                "preset_id": "claude_cli",
                "provider_id": "claude-cli",
                "label": "Claude app login",
                "auth_kind": "cli",
                "recommended": True,
                "supports_auth_profile": False,
                "supports_api_key": False,
                "supports_custom_base_url": False,
                "supports_custom_models": True,
                "default_model": "claude-cli/sonnet",
                "help_text": "Uses the local Claude CLI / Claude Code login instead of an API key.",
            },
            {
                "preset_id": "api_key",
                "provider_id": "anthropic",
                "label": "Anthropic API key",
                "auth_kind": "api_key",
                "recommended": False,
                "supports_auth_profile": True,
                "supports_api_key": True,
                "supports_custom_base_url": True,
                "supports_custom_models": True,
                "default_model": "anthropic/claude-opus-4-7",
                "help_text": "Use this when you have direct Anthropic API access.",
            },
        ],
    },
    "google": {
        "family_id": "google",
        "display_name": "Google / Gemini",
        "description": "Configure Gemini with an API key for chat and embeddings.",
        "usage_note": "Google models can be used for chat and embeddings. This gateway surfaces live request telemetry but does not query Google billing/subscription details.",
        "presets": [
            {
                "preset_id": "api_key",
                "provider_id": "google",
                "label": "Gemini API key",
                "auth_kind": "api_key",
                "recommended": True,
                "supports_auth_profile": True,
                "supports_api_key": True,
                "supports_custom_base_url": True,
                "supports_custom_models": True,
                "default_model": "google/gemini-2.5-pro",
                "help_text": "Use a Gemini / Google AI Studio API key or another compatible Google gateway key.",
            }
        ],
    },
    "zai": {
        "family_id": "zai",
        "display_name": "Z.ai / GLM",
        "description": "Configure Z.ai / GLM coding models using the API keys provided by Z.ai.",
        "usage_note": "Z.ai integrations use OpenAI-compatible or Anthropic-compatible wire formats depending on the selected preset. Telemetry is limited to observed request metadata.",
        "presets": [
            {
                "preset_id": "coding_api",
                "provider_id": "zai-coding",
                "label": "GLM coding API key",
                "auth_kind": "api_key",
                "recommended": True,
                "supports_auth_profile": True,
                "supports_api_key": True,
                "supports_custom_base_url": True,
                "supports_custom_models": True,
                "default_model": "zai-coding/glm-5.1",
                "help_text": "Recommended for GLM coding plans and OpenAI-compatible model access.",
            },
            {
                "preset_id": "anthropic_api",
                "provider_id": "zai-anthropic",
                "label": "GLM Anthropic-compatible API key",
                "auth_kind": "api_key",
                "recommended": False,
                "supports_auth_profile": True,
                "supports_api_key": True,
                "supports_custom_base_url": True,
                "supports_custom_models": True,
                "default_model": "zai-anthropic/glm-5.1",
                "help_text": "Use this when you want Z.ai models through an Anthropic-compatible tool-calling path.",
            },
        ],
    },
    "moonshot": {
        "family_id": "moonshot",
        "display_name": "Moonshot / Kimi",
        "description": "Configure Kimi coding plans using Moonshot / Kimi API keys.",
        "usage_note": "Kimi adapters expose request telemetry and live rate-limit headers when available. Subscription details are not queried by this gateway.",
        "presets": [
            {
                "preset_id": "kimi_coding_api",
                "provider_id": "kimi-coding",
                "label": "Kimi coding API key",
                "auth_kind": "api_key",
                "recommended": True,
                "supports_auth_profile": True,
                "supports_api_key": True,
                "supports_custom_base_url": True,
                "supports_custom_models": True,
                "default_model": "kimi-coding/kimi-for-coding",
                "help_text": "Recommended for the Kimi coding plan.",
            },
            {
                "preset_id": "moonshot_api",
                "provider_id": "moonshot",
                "label": "Moonshot API key",
                "auth_kind": "api_key",
                "recommended": False,
                "supports_auth_profile": True,
                "supports_api_key": True,
                "supports_custom_base_url": True,
                "supports_custom_models": True,
                "default_model": "moonshot/kimi-k2.6",
                "help_text": "Use the generic Moonshot API when you want the current public Kimi model line instead of the dedicated coding endpoint.",
            },
        ],
    },
}


def get_provider_setup_catalog() -> Dict[str, Dict[str, Any]]:
    """Return a deep copy so callers can safely decorate response payloads."""
    return deepcopy(PROVIDER_SETUP_CATALOG)


def get_provider_setup_family(family_id: str) -> Optional[Dict[str, Any]]:
    """Return one provider family definition by id."""
    return deepcopy(PROVIDER_SETUP_CATALOG.get(str(family_id).strip().lower()))


def get_provider_setup_preset(
    family_id: str,
    preset_id: str,
) -> Optional[Dict[str, Any]]:
    """Return one provider setup preset."""
    family = PROVIDER_SETUP_CATALOG.get(str(family_id).strip().lower())
    if not family:
        return None
    for preset in family.get("presets", []):
        if str(preset.get("preset_id")).strip().lower() == str(preset_id).strip().lower():
            return deepcopy(preset)
    return None
