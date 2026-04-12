import pytest
from fastapi.testclient import TestClient

from open_llm_auth.config import Config
from open_llm_auth.server.auth import Principal


def _admin_principal():
    return Principal(subject="test", token_id="test", scopes=frozenset({"admin"}), is_admin=True, source="configured_token")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Use a temporary directory for all config/state files."""
    monkeypatch.setattr("open_llm_auth.server.config_routes.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("open_llm_auth.server.config_routes.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr("open_llm_auth.server.config_routes._PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr("open_llm_auth.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("open_llm_auth.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = Config()
    from open_llm_auth.config import AccessTokenConfig
    cfg.authorization.tokens["test"] = AccessTokenConfig(token="secret", admin=True, enabled=True)
    cfg.save()


@pytest.fixture(autouse=True)
def isolated_usage(tmp_path, monkeypatch):
    """Use a temporary directory for usage store."""
    from open_llm_auth.server import usage_store
    db = tmp_path / "usage.sqlite3"
    store = usage_store.UsageStore(db_path=db)
    monkeypatch.setattr(usage_store, "_default_store", store)
    monkeypatch.setattr(usage_store, "get_usage_store", lambda: store)


@pytest.fixture
def client():
    from open_llm_auth.main import app
    from open_llm_auth.server.auth import verify_admin_token
    app.dependency_overrides[verify_admin_token] = _admin_principal
    return TestClient(app)


def test_usage_summary(client):
    from open_llm_auth.server.usage_store import get_usage_store
    get_usage_store().record(provider="p", model="m", endpoint="e", prompt_tokens=10, completion_tokens=5, latency_ms=100)
    resp = client.get("/config/usage/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["requests"] == 1
    assert data["total_tokens"] == 15


def test_usage_chart(client):
    from open_llm_auth.server.usage_store import get_usage_store
    get_usage_store().record(provider="p", model="m", endpoint="e", prompt_tokens=10, completion_tokens=5, latency_ms=100)
    resp = client.get("/config/usage/chart")
    assert resp.status_code == 200
    data = resp.json()
    assert "labels" in data
    assert "requests" in data


def test_usage_providers(client):
    from open_llm_auth.server.usage_store import get_usage_store
    get_usage_store().record(provider="openai", model="m", endpoint="e", prompt_tokens=10, completion_tokens=5, latency_ms=100)
    resp = client.get("/config/usage/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["provider"] == "openai"


def test_usage_overview(client):
    from open_llm_auth.server.usage_store import get_usage_store

    get_usage_store().record(
        provider="anthropic",
        model="claude-sonnet-4-6",
        endpoint="chat.completions",
        source="openai_compat",
        prompt_tokens=12,
        completion_tokens=8,
        latency_ms=120,
        meta={"rate_limits": {"requests_remaining": "99"}},
    )
    resp = client.get("/config/usage/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["requests"] == 1
    assert data["models"][0]["model"] == "claude-sonnet-4-6"
    assert data["sources"][0]["source"] == "openai_compat"


def test_usage_provider_telemetry(client, monkeypatch):
    from open_llm_auth.server import config_routes

    async def fake_collect_provider_telemetry(*, days=7, manager=None, store=None):
        return [
            {
                "provider": "anthropic",
                "profile_count": 1,
                "telemetry": {"available": False, "note": "Observed headers only"},
                "latest_observation": {"meta": {"rate_limits": {"requests_remaining": "99"}}},
            }
        ]

    monkeypatch.setattr(config_routes, "collect_provider_telemetry", fake_collect_provider_telemetry)
    resp = client.get("/config/usage/provider-telemetry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["providers"][0]["provider"] == "anthropic"
    assert data["providers"][0]["telemetry"]["note"] == "Observed headers only"


def test_list_profiles_empty(client):
    resp = client.get("/config/profiles")
    assert resp.status_code == 200
    assert resp.json() == []


def test_save_and_list_profiles(client):
    resp = client.post("/config/profiles", json={"name": "prod"})
    assert resp.status_code == 200
    resp = client.get("/config/profiles")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "prod"


def test_export_profile(client):
    client.post("/config/profiles", json={"name": "prod"})
    resp = client.get("/config/profiles/export/prod")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "prod"
    assert "content" in data


def test_import_profile(client):
    cfg = Config()
    payload = cfg.model_dump_json(by_alias=True)
    resp = client.post("/config/profiles/import", json={"name": "imported", "content": payload})
    assert resp.status_code == 200
    assert resp.json()["name"] == "imported"


def test_activate_profile(client):
    client.post("/config/profiles", json={"name": "snapshot"})
    resp = client.post("/config/profiles/snapshot/activate")
    assert resp.status_code == 200
    assert resp.json()["name"] == "snapshot"
