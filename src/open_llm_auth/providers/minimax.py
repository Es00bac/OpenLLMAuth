from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..provider_catalog import get_builtin_provider_models
from .anthropic_compatible import AnthropicCompatibleProvider


class MinimaxProvider(AnthropicCompatibleProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.minimax.io/anthropic",
        headers: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            provider_id="minimax",
            api_key=api_key,
            base_url=base_url,
            headers=headers,
        )

    async def list_models(self) -> List[Dict[str, Any]]:
        models = get_builtin_provider_models("minimax")
        if models:
            return [
                {
                    "id": m["id"],
                    "object": "model",
                    "created": 0,
                    "owned_by": "minimax",
                }
                for m in models
            ]
        return await super().list_models()
