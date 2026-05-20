from __future__ import annotations

import httpx
import pytest

from open_llm_auth.auth.manager import ResolvedProvider, ResolvedProviderAttempt
from open_llm_auth.server.routes import (
    _execute_non_stream_with_fallbacks,
    _execute_stream_with_fallbacks,
)


class _FakeProvider:
    def __init__(self, provider_id: str, *, status_code: int | None = None) -> None:
        self.provider_id = provider_id
        self.status_code = status_code
        self.calls: list[str] = []

    async def chat_completion(self, *, model, messages, payload):
        self.calls.append(model)
        if self.status_code is not None:
            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("upstream failed", request=request, response=response)
        return {
            "id": "ok",
            "object": "chat.completion",
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }

    async def chat_completion_stream(self, *, model, messages, payload):
        self.calls.append(model)
        if self.status_code is not None:
            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("upstream failed", request=request, response=response)

        async def _stream():
            yield b"data: ok\n\n"

        return _stream()


@pytest.mark.asyncio
async def test_non_stream_execution_uses_cross_provider_fallback_metadata() -> None:
    primary = _FakeProvider("primary", status_code=503)
    fallback = _FakeProvider("fallback")
    resolved = ResolvedProvider(
        provider=primary,
        providers=[primary],
        provider_id="primary",
        model_id="bad-model",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
        retry_status_codes=[503],
        attempts=[
            ResolvedProviderAttempt(
                provider=primary,
                provider_id="primary",
                model_id="bad-model",
                profile_id=None,
                auth_source="provider-config:no-auth-header",
            ),
            ResolvedProviderAttempt(
                provider=fallback,
                provider_id="fallback",
                model_id="good-model",
                profile_id=None,
                auth_source="provider-config:no-auth-header",
                fallback_index=1,
                fallback_reason="configured:fallback/good-model",
            ),
        ],
    )

    response = await _execute_non_stream_with_fallbacks(
        resolved=resolved,
        messages_dump=[{"role": "user", "content": "hi"}],
        payload={},
    )

    assert primary.calls == ["bad-model"]
    assert fallback.calls == ["good-model"]
    assert response["_open_llm_auth"]["provider"] == "fallback"
    assert response["_open_llm_auth"]["model"] == "good-model"
    assert response["_open_llm_auth"]["fallback_active"] is True
    assert response["_open_llm_auth"]["fallback_failed_attempts"][0]["status_code"] == 503


@pytest.mark.asyncio
async def test_stream_execution_returns_active_fallback_metadata() -> None:
    primary = _FakeProvider("primary", status_code=503)
    fallback = _FakeProvider("fallback")
    resolved = ResolvedProvider(
        provider=primary,
        providers=[primary],
        provider_id="primary",
        model_id="bad-model",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
        retry_status_codes=[503],
        attempts=[
            ResolvedProviderAttempt(
                provider=primary,
                provider_id="primary",
                model_id="bad-model",
                profile_id=None,
                auth_source="provider-config:no-auth-header",
            ),
            ResolvedProviderAttempt(
                provider=fallback,
                provider_id="fallback",
                model_id="good-model",
                profile_id=None,
                auth_source="provider-config:no-auth-header",
                fallback_index=1,
                fallback_reason="configured:fallback/good-model",
            ),
        ],
    )

    stream, meta = await _execute_stream_with_fallbacks(
        resolved=resolved,
        messages_dump=[{"role": "user", "content": "hi"}],
        payload={"stream": True},
    )
    chunks = [chunk async for chunk in stream]

    assert chunks == [b"data: ok\n\n"]
    assert primary.calls == ["bad-model"]
    assert fallback.calls == ["good-model"]
    assert meta["provider"] == "fallback"
    assert meta["model"] == "good-model"
    assert meta["fallback_active"] is True
    assert meta["fallback_failed_attempts"][0]["status_code"] == 503
