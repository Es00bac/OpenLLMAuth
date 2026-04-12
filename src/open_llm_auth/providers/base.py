from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional


class BaseProvider(ABC):
    def __init__(
        self,
        *,
        provider_id: str,
        api_key: Optional[str],
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 300.0,
    ):
        self.provider_id = provider_id
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout

    @abstractmethod
    async def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def chat_completion_stream(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        raise NotImplementedError

    @abstractmethod
    async def list_models(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def embeddings(
        self,
        *,
        model: str,
        input_texts: List[str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            f"Provider '{self.provider_id}' does not support embeddings"
        )

    async def get_usage_telemetry(self, days: int = 7) -> Dict[str, Any]:
        return {
            "available": False,
            "provider": self.provider_id,
            "window_days": max(1, int(days)),
            "kind": "provider_account",
            "supported_fields": {
                "live_rate_limits": False,
                "account_usage": False,
                "billing_cycle": False,
                "subscription_cost": False,
            },
            "note": "Provider telemetry is not implemented for this adapter.",
        }

    def attach_response_telemetry(
        self,
        payload: Dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
        endpoint: str,
    ) -> Dict[str, Any]:
        telemetry = {
            "endpoint": endpoint,
            "rate_limits": extract_rate_limit_headers(headers),
        }
        payload["_open_llm_auth"] = telemetry
        return payload


def compact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None}


def extract_rate_limit_headers(headers: Mapping[str, str] | None) -> Dict[str, Any]:
    if not headers:
        return {}
    normalized = {str(k).lower(): v for k, v in headers.items()}
    pairs = {
        "requests_limit": ("x-ratelimit-limit-requests", "anthropic-ratelimit-requests-limit"),
        "requests_remaining": ("x-ratelimit-remaining-requests", "anthropic-ratelimit-requests-remaining"),
        "requests_reset": ("x-ratelimit-reset-requests", "anthropic-ratelimit-requests-reset"),
        "tokens_limit": ("x-ratelimit-limit-tokens", "anthropic-ratelimit-tokens-limit"),
        "tokens_remaining": ("x-ratelimit-remaining-tokens", "anthropic-ratelimit-tokens-remaining"),
        "tokens_reset": ("x-ratelimit-reset-tokens", "anthropic-ratelimit-tokens-reset"),
        "retry_after": ("retry-after",),
    }
    out: Dict[str, Any] = {}
    for key, options in pairs.items():
        for option in options:
            value = normalized.get(option)
            if value not in (None, ""):
                out[key] = value
                break
    return out
