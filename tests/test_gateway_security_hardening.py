from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from open_llm_auth.config import (
    AccessTokenConfig,
    AuthProfile,
    AuthorizationConfig,
    Config,
    ProviderConfig,
)
from open_llm_auth.main import app
from open_llm_auth.auth import manager as manager_module
from open_llm_auth.server import auth as auth_module
from open_llm_auth.server import config_routes as config_routes_module
from open_llm_auth.server.durable_state import reset_durable_state_store_cache
from open_llm_auth.server.task_contract import reset_task_contract_cache
from open_llm_auth.server import routes as routes_module


def _patch_config(monkeypatch: pytest.MonkeyPatch, cfg: Config) -> None:
    db_path = Path(tempfile.gettempdir()) / f"open_llm_auth_test_{uuid4().hex}.sqlite3"
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


def test_fail_closed_requires_token_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    monkeypatch.delenv("OPEN_LLM_AUTH_TOKEN", raising=False)
    _patch_config(monkeypatch, Config())

    client = TestClient(app)
    models_resp = client.get("/v1/models")
    config_resp = client.get("/config")
    tasks_resp = client.get("/v1/universal/tasks")
    wait_resp = client.post(
        "/v1/universal/tasks/task-1/wait",
        json={"provider": "openbulma", "timeoutMs": 1000, "pollMs": 200},
    )

    assert models_resp.status_code == 401
    assert config_resp.status_code == 401
    assert tasks_resp.status_code == 401
    assert wait_resp.status_code == 401


def test_allow_anon_override_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_LLM_AUTH_ALLOW_ANON", "1")
    monkeypatch.delenv("OPEN_LLM_AUTH_TOKEN", raising=False)
    _patch_config(monkeypatch, Config())

    async def _fake_list_models() -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(routes_module.manager, "list_models", _fake_list_models)

    client = TestClient(app)
    response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": []}


def test_config_response_redacts_secret_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    cfg = Config(
        server_token="server-secret",
        providers={"anthropic": ProviderConfig(api_key="provider-secret")},
        auth_profiles={
            "anthropic:default": AuthProfile(
                id="anthropic:default",
                provider="anthropic",
                type="oauth",
                key="profile-key-secret",
                token="profile-token-secret",
                access="profile-access-secret",
                refresh="profile-refresh-secret",
            )
        },
    )
    _patch_config(monkeypatch, cfg)

    client = TestClient(app)
    response = client.get(
        "/config",
        headers={"Authorization": "Bearer server-secret"},
    )
    body = response.json()
    encoded = json.dumps(body)

    assert response.status_code == 200
    for raw in (
        "server-secret",
        "provider-secret",
        "profile-key-secret",
        "profile-token-secret",
        "profile-access-secret",
        "profile-refresh-secret",
    ):
        assert raw not in encoded


def test_upstream_http_errors_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    cfg = Config(server_token="server-secret")
    _patch_config(monkeypatch, cfg)

    class _FailingProvider:
        provider_id = "stub-provider"

        async def chat_completion(self, model, messages, payload):
            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
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

    failing_provider = _FailingProvider()
    resolved = SimpleNamespace(
        provider=failing_provider,
        provider_id="stub-provider",
        model_id="stub-model",
        providers=[failing_provider],
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer server-secret"},
        json={
            "model": "stub-provider/stub-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    body = response.json()
    encoded = json.dumps(body)

    assert response.status_code == 502
    assert body["error"]["code"] == "upstream_http_error"
    assert "leaked-upstream-secret" not in encoded


def test_legacy_server_token_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    cfg = Config(
        server_token="legacy-server-token",
        authorization=AuthorizationConfig(
            legacy_admin_compatibility=False,
            tokens={
                "reader": AccessTokenConfig(token="reader-token", scopes=["read"]),
            },
        ),
    )
    _patch_config(monkeypatch, cfg)

    async def _fake_list_models() -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(routes_module.manager, "list_models", _fake_list_models)

    client = TestClient(app)
    legacy_resp = client.get("/v1/models", headers={"Authorization": "Bearer legacy-server-token"})
    scoped_resp = client.get("/v1/models", headers={"Authorization": "Bearer reader-token"})

    assert legacy_resp.status_code == 401
    assert scoped_resp.status_code == 200
    assert scoped_resp.json() == {"object": "list", "data": []}


def test_config_route_requires_admin_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    cfg = Config(
        authorization=AuthorizationConfig(
            legacy_admin_compatibility=False,
            tokens={
                "reader": AccessTokenConfig(token="reader-token", scopes=["read"]),
            },
        ),
    )
    _patch_config(monkeypatch, cfg)

    client = TestClient(app)
    response = client.get("/config", headers={"Authorization": "Bearer reader-token"})

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "insufficient_scope"


def test_config_save_provider_blocks_metadata_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    cfg = Config(server_token="server-secret")
    _patch_config(monkeypatch, cfg)
    monkeypatch.setattr(Config, "save", lambda self: None)

    client = TestClient(app)
    response = client.put(
        "/config/providers/evil",
        headers={"Authorization": "Bearer server-secret"},
        json={
            "base_url": "https://169.254.169.254/v1",
            "api_key": "secret",
            "api": "openai-completions",
            "auth": "api-key",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsafe_destination"
    assert response.json()["detail"]["details"]["reason"] in {
        "denylisted_cidr",
        "private_ip",
        "resolved_denylisted_cidr",
    }


def test_config_save_provider_allows_openbulma_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    cfg = Config(server_token="server-secret")
    _patch_config(monkeypatch, cfg)
    monkeypatch.setattr(Config, "save", lambda self: None)

    client = TestClient(app)
    response = client.put(
        "/config/providers/openbulma",
        headers={"Authorization": "Bearer server-secret"},
        json={
            "base_url": "http://127.0.0.1:20100/v1",
            "api": "openai-completions",
            "auth": "api-key",
            "auth_header": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_runtime_resolution_block_returns_egress_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    cfg = Config(
        server_token="server-secret",
        providers={
            "evil": ProviderConfig(
                base_url="https://169.254.169.254/v1",
                api_key="secret",
            )
        },
    )
    _patch_config(monkeypatch, cfg)

    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer server-secret"},
        json={
            "model": "evil/test-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "egress_destination_blocked"


def test_config_bulk_save_blocks_unsafe_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPEN_LLM_AUTH_ALLOW_ANON", raising=False)
    cfg = Config(server_token="server-secret")
    _patch_config(monkeypatch, cfg)
    monkeypatch.setattr(Config, "save", lambda self: None)

    payload = cfg.model_dump(by_alias=True)
    payload["providers"] = {
        "evil": {
            "baseUrl": "https://169.254.169.254/v1",
            "apiKey": "secret",
            "api": "openai-completions",
            "auth": "api-key",
        }
    }

    client = TestClient(app)
    response = client.post(
        "/config",
        headers={"Authorization": "Bearer server-secret"},
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsafe_destination"
