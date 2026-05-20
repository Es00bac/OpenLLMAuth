from __future__ import annotations

from open_llm_auth.cli import _ensure_active_provider
from open_llm_auth.config import Config


def test_ensure_active_provider_adds_openai_codex_without_duplicates() -> None:
    cfg = Config(activeProviderIds=["codex-cli", "kimi-coding"])

    _ensure_active_provider(cfg, "openai-codex")
    _ensure_active_provider(cfg, "openai-codex")

    assert cfg.active_provider_ids == ["codex-cli", "kimi-coding", "openai-codex"]
