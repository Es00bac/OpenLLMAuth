from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import threading
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import BaseProvider

log = logging.getLogger(__name__)


class LocalEmbeddingProvider(BaseProvider):
    """Self-contained local embedding adapter owned by OpenLLMAuth.

    Host applications should route local/offline embedding requests here rather
    than importing model runtimes directly. This keeps model identity,
    dimensions, and degradation metadata centralized in the gateway layer.
    """

    def __init__(
        self,
        *,
        provider_id: str = "local-embeddings",
        model_definitions: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        super().__init__(
            provider_id=provider_id,
            api_key=None,
            base_url="",
            headers={},
            timeout=30.0,
        )
        self.model_definitions = dict(model_definitions or {})
        self._models: Dict[str, Any] = {}
        self._lock = threading.Lock()

    async def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError("LocalEmbeddingProvider only supports embeddings")

    async def chat_completion_stream(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        raise NotImplementedError("LocalEmbeddingProvider only supports embeddings")

    async def list_models(self) -> List[Dict[str, Any]]:
        return [dict(definition) for definition in self.model_definitions.values()]

    async def embeddings(
        self,
        *,
        model: str,
        input_texts: List[str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        definition = self._definition_for(model)
        dimensions = self._requested_dimensions(payload) or self._definition_dimensions(
            definition
        )
        runtime = str(definition.get("local_runtime") or "hash").strip().lower()
        degraded = False
        degraded_reason: Optional[str] = None

        if runtime == "sentence_transformers":
            if not self._sentence_transformers_enabled():
                degraded = True
                degraded_reason = (
                    "in-process sentence_transformers execution disabled; "
                    "set OPEN_LLM_AUTH_ENABLE_LOCAL_EMBEDDING_MODELS=1 to enable"
                )
                vectors = [self._hash_embedding(text, dimensions) for text in input_texts]
            else:
                try:
                    vectors = await self._sentence_transformer_batch(model, input_texts)
                except Exception as exc:
                    degraded = True
                    degraded_reason = f"{type(exc).__name__}: {exc}"
                    log.warning(
                        "Local embedding model %s unavailable; using deterministic hash fallback: %s",
                        model,
                        exc,
                    )
                    vectors = [self._hash_embedding(text, dimensions) for text in input_texts]
        else:
            vectors = [self._hash_embedding(text, dimensions) for text in input_texts]

        response = {
            "object": "list",
            "model": model,
            "data": [
                {
                    "object": "embedding",
                    "index": index,
                    "embedding": list(vector),
                }
                for index, vector in enumerate(vectors)
            ],
            "usage": {
                "prompt_tokens": sum(self._estimate_tokens(text) for text in input_texts),
                "total_tokens": sum(self._estimate_tokens(text) for text in input_texts),
            },
        }
        response = self.attach_response_telemetry(
            response,
            headers=None,
            endpoint="embeddings.local",
        )
        response["_open_llm_auth"].update(
            {
                "provider": self.provider_id,
                "local_runtime": runtime,
                "degraded": degraded,
                "degraded_reason": degraded_reason,
                "dimensions": len(vectors[0]) if vectors else dimensions,
            }
        )
        return response

    def _definition_for(self, model: str) -> Dict[str, Any]:
        definition = self.model_definitions.get(model)
        if definition is None:
            raise ValueError(f"Local embedding model '{model}' is not registered.")
        return dict(definition)

    @staticmethod
    def _requested_dimensions(payload: Dict[str, Any]) -> Optional[int]:
        raw = payload.get("dimensions")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _definition_dimensions(definition: Dict[str, Any]) -> int:
        try:
            parsed = int(definition.get("dimensions") or 256)
        except (TypeError, ValueError):
            return 256
        return parsed if parsed > 0 else 256

    @staticmethod
    def _sentence_transformers_enabled() -> bool:
        raw = os.getenv("OPEN_LLM_AUTH_ENABLE_LOCAL_EMBEDDING_MODELS", "").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    async def _sentence_transformer_batch(
        self,
        model: str,
        input_texts: List[str],
    ) -> List[List[float]]:
        transformer = await asyncio.to_thread(self._load_sentence_transformer, model)
        vectors = await asyncio.to_thread(
            transformer.encode,
            input_texts,
            batch_size=64,
            show_progress_bar=False,
        )
        return [list(vector) for vector in vectors]

    def _load_sentence_transformer(self, model: str) -> Any:
        cached = self._models.get(model)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._models.get(model)
            if cached is not None:
                return cached
            self._configure_torch_threads()
            from sentence_transformers import SentenceTransformer

            token = os.getenv("HF_TOKEN")
            transformer = SentenceTransformer(
                model,
                device=os.getenv("OPEN_LLM_AUTH_LOCAL_EMBEDDING_DEVICE", "cpu"),
                token=token,
                trust_remote_code=True,
            )
            self._models[model] = transformer
            return transformer

    @staticmethod
    def _configure_torch_threads() -> None:
        raw_value = (
            os.getenv("OPEN_LLM_AUTH_TORCH_THREADS")
            or os.getenv("OPENCAS_TORCH_THREADS")
            or ""
        ).strip()
        if not raw_value:
            return
        try:
            threads = int(raw_value)
        except ValueError:
            log.warning("Ignoring invalid torch thread count %r", raw_value)
            return
        if threads < 1:
            log.warning("Ignoring non-positive torch thread count %r", raw_value)
            return
        try:
            import torch

            torch.set_num_threads(threads)
        except Exception as exc:
            log.debug("Torch thread configuration skipped: %s", exc)

    @staticmethod
    def _hash_embedding(text: str, dim: int) -> List[float]:
        vector = [0.0] * max(1, int(dim))
        lowered = text.lower()
        if len(lowered) < 3:
            lowered = f"  {lowered}  "
        for index in range(max(1, len(lowered) - 2)):
            tri = lowered[index : index + 3]
            bucket = int(hashlib.md5(tri.encode("utf-8")).hexdigest(), 16) % len(vector)
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)
