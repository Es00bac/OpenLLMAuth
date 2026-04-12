from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from open_llm_auth.config import Config
from open_llm_auth.auth import manager as manager_module
from open_llm_auth.server.auth import Principal
from open_llm_auth.server import auth as auth_module
from open_llm_auth.server import config_routes as config_routes_module
from open_llm_auth.server.durable_state import reset_durable_state_store_cache
from open_llm_auth.server.task_contract import reset_task_contract_cache
from open_llm_auth.server import routes as routes_module
from open_llm_auth.server.models import EmbeddingRequest
from open_llm_auth.provider_catalog import get_builtin_provider_models


def _patch_config(monkeypatch: pytest.MonkeyPatch, cfg: Config) -> None:
    db_path = Path(tempfile.gettempdir()) / f"open_llm_auth_embeddings_{uuid4().hex}.sqlite3"
    cfg = cfg.model_copy(
        update={
            "durable_state": cfg.durable_state.model_copy(
                update={"db_path": str(db_path), "enabled": True}
            )
        }
    )
    reset_durable_state_store_cache()
    reset_task_contract_cache()
    monkeypatch.setattr(auth_module, "load_config", lambda: cfg)
    monkeypatch.setattr(manager_module, "load_config", lambda: cfg)
    monkeypatch.setattr(config_routes_module, "load_config", lambda: cfg)
    monkeypatch.setattr(routes_module, "load_config", lambda: cfg)
    routes_module.manager._config = cfg
    routes_module.manager._providers = {}


def _principal() -> Principal:
    return Principal(
        subject="test",
        token_id="test",
        scopes=frozenset({"read", "write", "admin"}),
        is_admin=True,
        source="configured_token",
    )


@pytest.mark.asyncio
async def test_embeddings_route_dispatches_to_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))

    class _EmbeddingProvider:
        provider_id = "ollama"

        async def embeddings(self, model, input_texts, payload):
            assert model == "nomic-embed-text"
            assert input_texts == ["hello", "world"]
            assert payload["dimensions"] == 768
            return {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
                    {"object": "embedding", "index": 1, "embedding": [0.3, 0.4]},
                ],
                "model": "nomic-embed-text",
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            }

    provider = _EmbeddingProvider()
    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="ollama",
        model_id="nomic-embed-text",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    response = await routes_module.embeddings(
        EmbeddingRequest(
            model="ollama/nomic-embed-text",
            input=["hello", "world"],
            dimensions=768,
        ),
        x_auth_profile=None,
        principal=_principal(),
    )

    assert response["model"] == "nomic-embed-text"
    assert len(response["data"]) == 2


@pytest.mark.asyncio
async def test_embeddings_route_rejects_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))

    response = await routes_module.embeddings(
        EmbeddingRequest(
            model="ollama/nomic-embed-text",
            input=[],
        ),
        x_auth_profile=None,
        principal=_principal(),
    )

    assert response.status_code == 400
    assert json.loads(response.body)["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_embeddings_route_sanitizes_upstream_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))

    class _FailingProvider:
        provider_id = "ollama"

        async def embeddings(self, model, input_texts, payload):
            request = httpx.Request("POST", "http://127.0.0.1:11434/v1/embeddings")
            response = httpx.Response(
                502,
                request=request,
                json={"error": {"message": "leaked-upstream-secret"}},
            )
            raise httpx.HTTPStatusError(
                "upstream failed with leaked-upstream-secret",
                request=request,
                response=response,
            )

    provider = _FailingProvider()
    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="ollama",
        model_id="nomic-embed-text",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    response = await routes_module.embeddings(
        EmbeddingRequest(
            model="ollama/nomic-embed-text",
            input=["hello"],
        ),
        x_auth_profile=None,
        principal=_principal(),
    )

    assert response.status_code == 502
    payload = json.loads(response.body)
    assert payload["error"]["code"] == "upstream_http_error"
    assert "leaked-upstream-secret" not in str(payload)


def test_ollama_builtin_models_include_nomic_embed_text() -> None:
    models = get_builtin_provider_models("ollama")
    assert any(model.get("id") == "nomic-embed-text" for model in models)
