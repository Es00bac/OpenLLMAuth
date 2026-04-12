from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from open_llm_auth.providers.bedrock_converse import BedrockConverseProvider


def _provider() -> BedrockConverseProvider:
    return BedrockConverseProvider(
        provider_id="amazon-bedrock",
        api_key=None,
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        headers={},
    )


def test_build_request_headers_sigv4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "verysecret")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)

    provider = _provider()
    payload = b'{"messages":[]}'
    headers = provider._build_request_headers(
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/m/converse",
        payload,
        region="us-east-1",
    )

    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json"
    assert headers["x-amz-content-sha256"] == hashlib.sha256(payload).hexdigest()
    assert "x-amz-date" in headers
    assert headers["Authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/"
    )
    assert "SignedHeaders=" in headers["Authorization"]
    assert "Signature=" in headers["Authorization"]


def test_resolve_region_from_runtime_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    provider = BedrockConverseProvider(
        provider_id="amazon-bedrock",
        api_key=None,
        base_url="https://bedrock-runtime.us-west-2.amazonaws.com",
        headers={},
    )

    assert provider._resolve_region() == "us-west-2"


def test_convert_converse_response_to_openai_shape() -> None:
    data = {
        "modelId": "anthropic.claude-3-5-sonnet-20240620-v1:0",
        "output": {
            "message": {
                "content": [
                    {"text": "hello"},
                    {"text": " world"},
                ]
            }
        },
        "usage": {"inputTokens": 12, "outputTokens": 5},
        "stopReason": "max_tokens",
    }

    converted = BedrockConverseProvider._convert_converse_response(
        data, fallback_model="fallback-model"
    )

    assert converted["model"] == "anthropic.claude-3-5-sonnet-20240620-v1:0"
    assert converted["choices"][0]["message"]["content"] == "hello world"
    assert converted["choices"][0]["finish_reason"] == "length"
    assert converted["usage"]["prompt_tokens"] == 12
    assert converted["usage"]["completion_tokens"] == 5
    assert converted["usage"]["total_tokens"] == 17
