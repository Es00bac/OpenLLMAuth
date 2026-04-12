def test_config_dashboard_template(monkeypatch, tmp_path):
    from open_llm_auth.config import Config, AccessTokenConfig

    # Setup isolated config with auth token
    monkeypatch.setattr("open_llm_auth.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("open_llm_auth.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = Config()
    cfg.authorization.tokens["test"] = AccessTokenConfig(token="secret", admin=True, enabled=True)
    cfg.save()

    from fastapi.testclient import TestClient
    from open_llm_auth.main import app
    from open_llm_auth.server.auth import Principal, verify_admin_token

    app.dependency_overrides[verify_admin_token] = lambda: Principal(
        subject="test", token_id="test", scopes=frozenset({"admin"}), is_admin=True, source="configured_token"
    )
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Open LLM Auth" in resp.text
    assert "htmx" in resp.text
    assert "Alpine.js" in resp.text
    assert "Chart.js" in resp.text
