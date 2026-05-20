from __future__ import annotations

import pytest

from open_llm_auth.auth import manager as manager_module
from open_llm_auth.config import Config
from open_llm_auth.server import anthropic_routes as anthropic_routes_module
from open_llm_auth.server import auth as auth_module
from open_llm_auth.server import config_routes as config_routes_module
from open_llm_auth.server import routes as routes_module
from open_llm_auth.server.anthropic_routes import (
    OpenAIAnthropicStreamTranslator,
    convert_tool_choice_anthropic_to_openai,
    estimate_anthropic_input_tokens,
)
from open_llm_auth.server.auth import verify_server_token_or_x_api_key


def _patch_config(monkeypatch: pytest.MonkeyPatch, cfg: Config) -> None:
    monkeypatch.setattr(auth_module, "load_config", lambda: cfg)
    monkeypatch.setattr(manager_module, "load_config", lambda: cfg)
    monkeypatch.setattr(config_routes_module, "load_config", lambda: cfg)
    monkeypatch.setattr(routes_module, "load_config", lambda: cfg)
    routes_module.manager._config = cfg
    routes_module.manager._providers = {}
    anthropic_routes_module.manager._config = cfg
    anthropic_routes_module.manager._providers = {}


def test_tool_choice_any_maps_to_openai_required() -> None:
    assert convert_tool_choice_anthropic_to_openai({"type": "any"}) == "required"


def test_server_token_auth_accepts_claude_code_x_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    _patch_config(monkeypatch, Config(server_token="server-secret"))

    principal = verify_server_token_or_x_api_key(authorization=None, x_api_key="server-secret")

    assert principal.is_admin is True
    assert principal.source == "legacy_server_token"


def test_messages_count_tokens_estimates_nonzero_input_tokens() -> None:
    count = estimate_anthropic_input_tokens(
        {
            "model": "openai/gpt-5.5",
            "system": "You are concise.",
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        }
    )

    assert count > 0


def test_stream_translator_starts_tool_only_response_without_empty_text_block() -> None:
    translator = OpenAIAnthropicStreamTranslator(model="openai/gpt-5.5")
    events = []
    events.extend(translator.start_events())
    events.extend(
        translator.accept_chunk(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "read_file", "arguments": ""},
                                }
                            ]
                        }
                    }
                ]
            }
        )
    )

    assert events[1].startswith("event: content_block_start")
    assert '"type": "tool_use"' in events[1]
    assert '"index": 0' in events[1]
