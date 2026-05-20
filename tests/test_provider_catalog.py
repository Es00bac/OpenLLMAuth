from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from open_llm_auth.provider_catalog import get_builtin_provider_models
from open_llm_auth.provider_setup_catalog import get_provider_setup_catalog


def _model_ids(provider_id: str) -> list[str]:
    return [str(model.get("id")) for model in get_builtin_provider_models(provider_id)]


def test_openai_catalog_includes_current_gpt55_family() -> None:
    models = _model_ids("openai")
    assert "gpt-5.5" in models
    assert "gpt-5.5-pro" in models
    assert "gpt-5.4" in models
    assert "gpt-5.4-mini" in models
    assert "gpt-5.4-nano" in models


def test_codex_cli_catalog_includes_current_gpt55_family() -> None:
    models = _model_ids("codex-cli")
    assert "gpt-5.5" in models
    assert "gpt-5.5-pro" in models
    assert "gpt-5.3-codex-spark" in models


def test_anthropic_catalog_includes_current_claude_line() -> None:
    models = _model_ids("anthropic")
    assert "claude-opus-4-7" in models
    assert "claude-sonnet-4-6" in models
    assert "claude-haiku-4-5" in models


def test_zai_catalog_includes_current_glm5_line() -> None:
    models = _model_ids("zai-coding")
    assert "glm-4.7-flash" in models
    assert "glm-4.7-flashx" in models
    assert "glm-5.1" in models
    assert "glm-5v-turbo" in models
    assert "glm-5-turbo" in models


def test_moonshot_catalog_includes_current_kimi_and_moonshot_models() -> None:
    models = _model_ids("moonshot")
    assert "kimi-k2.6" in models
    assert "kimi-k2.5" in models
    assert "kimi-k2-0905-preview" in models
    assert "moonshot-v1-128k" in models


def test_kimi_coding_catalog_includes_provider_facing_k26_alias() -> None:
    models = _model_ids("kimi-coding")
    assert "kimi-for-coding" in models
    assert "k2p6" in models
    assert "k2p5" in models


def test_guided_setup_defaults_exist_in_builtin_catalog() -> None:
    catalog = get_provider_setup_catalog()

    for family in ("openai", "zai", "moonshot"):
        for preset in catalog[family]["presets"]:
            provider_id = str(preset["provider_id"])
            default_model = str(preset["default_model"])
            _, model_id = default_model.split("/", 1)
            assert model_id in _model_ids(provider_id)
