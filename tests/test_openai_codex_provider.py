from open_llm_auth.providers.openai_codex import OpenAICodexProvider


def test_openai_codex_body_drops_unsupported_prompt_cache_hints() -> None:
    provider = OpenAICodexProvider(
        provider_id="openai-codex",
        api_key="token",
        base_url="https://chatgpt.com/backend-api",
    )

    body = provider._build_codex_body(
        model="gpt-5.5",
        messages=[{"role": "user", "content": "Hello"}],
        payload={
            "prompt_cache_key": "opencas:conversation:light:abc123",
            "prompt_cache_retention": "in_memory",
        },
    )

    assert "prompt_cache_key" not in body
    assert "prompt_cache_retention" not in body
