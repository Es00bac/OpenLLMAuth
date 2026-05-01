from __future__ import annotations

import asyncio
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
    AuthorizationConfig,
    Config,
    DurableStateConfig,
    TaskContractConfig,
)
from open_llm_auth.main import app
from open_llm_auth.auth import manager as manager_module
from open_llm_auth.providers import AgentBridgeProvider
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
    monkeypatch.setattr(auth_module, "load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(manager_module, "load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(config_routes_module, "load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(routes_module, "load_config", lambda *args, **kwargs: cfg)
    routes_module.manager._config = cfg
    routes_module.manager._providers = {}


def _auth_header(token: str = "server-secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_universal_returns_canonical_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))

    response_payload = {
        "id": "x",
        "choices": [{"message": {"role": "assistant", "content": "hello from provider"}}],
    }

    class _Provider:
        provider_id = "stub-provider"

        async def chat_completion(self, model, messages, payload):
            return response_payload

    resolved = SimpleNamespace(
        provider=_Provider(),
        providers=[_Provider()],
        provider_id="stub-provider",
        model_id="stub-model",
        profile_id=None,
        auth_source="env:TEST_KEY",
    )

    monkeypatch.setattr(
        routes_module.manager,
        "resolve",
        lambda model, preferred_profile=None: resolved,
    )

    client = TestClient(app)
    response = client.post(
        "/v1/universal",
        headers=_auth_header(),
        json={
            "model": "stub-provider/stub-model",
            "input": [{"role": "user", "content": "hi"}],
            "options": {"temperature": 0.1},
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["object"] == "universal.response"
    assert body["provider"] == "stub-provider"
    assert body["model"] == "stub-model"
    assert body["auth_source"] == "env:TEST_KEY"
    assert body["output_text"] == "hello from provider"
    assert body["response"] == response_payload


def test_universal_rejects_empty_input_without_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))

    client = TestClient(app)
    response = client.post(
        "/v1/universal",
        headers=_auth_header(),
        json={
            "model": "stub-provider/stub-model",
            "input": [],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_universal_task_only_derives_objective_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))
    observed: dict[str, object] = {}

    class _Provider:
        provider_id = "stub-provider"

        async def chat_completion(self, model, messages, payload):
            observed["messages"] = messages
            observed["payload"] = payload
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    provider = _Provider()
    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="stub-provider",
        model_id="stub-model",
        profile_id=None,
        auth_source="config",
    )
    monkeypatch.setattr(
        routes_module.manager,
        "resolve",
        lambda model, preferred_profile=None: resolved,
    )

    client = TestClient(app)
    response = client.post(
        "/v1/universal",
        headers=_auth_header(),
        json={
            "model": "stub-provider/stub-model",
            "task": {"objective": "refactor this module"},
        },
    )

    assert response.status_code == 200
    assert observed["messages"] == [{"role": "user", "content": "refactor this module"}]
    assert isinstance(observed["payload"], dict)
    assert observed["payload"]["task"]["objective"] == "refactor this module"


def test_openai_shim_and_universal_share_provider_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))

    response_payload = {
        "id": "same",
        "choices": [{"message": {"role": "assistant", "content": "shared-output"}}],
    }

    class _Provider:
        provider_id = "stub-provider"

        async def chat_completion(self, model, messages, payload):
            return response_payload

    provider = _Provider()
    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="stub-provider",
        model_id="stub-model",
        profile_id=None,
        auth_source="config",
    )
    monkeypatch.setattr(
        routes_module.manager,
        "resolve",
        lambda model, preferred_profile=None: resolved,
    )

    client = TestClient(app)
    openai_resp = client.post(
        "/v1/chat/completions",
        headers=_auth_header(),
        json={
            "model": "stub-provider/stub-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    universal_resp = client.post(
        "/v1/universal",
        headers=_auth_header(),
        json={
            "model": "stub-provider/stub-model",
            "input": [{"role": "user", "content": "hello"}],
        },
    )

    assert openai_resp.status_code == 200
    assert universal_resp.status_code == 200
    assert universal_resp.json()["response"] == openai_resp.json()


def test_universal_task_lifecycle_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))
    calls: dict[str, object] = {}

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _run_task(task_input):
        calls["run_task"] = task_input
        return {"taskId": "task-1", "status": "queued"}

    async def _get_task(task_id):
        calls["get_task"] = task_id
        return {"id": task_id, "status": "needs_approval"}

    async def _approve_task(task_id, approval_id, approved=True):
        calls["approve_task"] = {
            "task_id": task_id,
            "approval_id": approval_id,
            "approved": approved,
        }
        return {"id": task_id, "status": "running"}

    async def _retry_task(task_id, operator=None):
        calls["retry_task"] = {"task_id": task_id, "operator": operator}
        return {"id": task_id, "status": "running"}

    async def _cancel_task(task_id):
        calls["cancel_task"] = {"task_id": task_id}
        return {"id": task_id, "status": "canceled"}

    provider.run_task = _run_task  # type: ignore[method-assign]
    provider.get_task = _get_task  # type: ignore[method-assign]
    provider.approve_task = _approve_task  # type: ignore[method-assign]
    provider.retry_task = _retry_task  # type: ignore[method-assign]
    provider.cancel_task = _cancel_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(
        routes_module.manager,
        "resolve",
        lambda model, preferred_profile=None: resolved,
    )

    client = TestClient(app)

    create_resp = client.post(
        "/v1/universal/tasks",
        headers=_auth_header(),
        json={
            "provider": "agent_bridge",
            "task": {"objective": "fix tests"},
        },
    )
    status_resp = client.get(
        "/v1/universal/tasks/task-1",
        headers=_auth_header(),
    )
    approve_resp = client.post(
        "/v1/universal/tasks/task-1/approve",
        headers=_auth_header(),
        json={
            "provider": "agent_bridge",
            "approvalId": "approval-1",
            "approved": True,
        },
    )
    retry_resp = client.post(
        "/v1/universal/tasks/task-1/retry",
        headers=_auth_header(),
        json={
            "provider": "agent_bridge",
            "operator": "operator",
        },
    )
    cancel_resp = client.post(
        "/v1/universal/tasks/task-1/cancel",
        headers=_auth_header(),
        json={"provider": "agent_bridge"},
    )

    assert create_resp.status_code == 200
    assert status_resp.status_code == 200
    assert approve_resp.status_code == 200
    assert retry_resp.status_code == 200
    assert cancel_resp.status_code == 200

    assert create_resp.json()["operation"] == "create"
    assert status_resp.json()["operation"] == "status"
    assert approve_resp.json()["operation"] == "approve"
    assert retry_resp.json()["operation"] == "retry"
    assert cancel_resp.json()["operation"] == "cancel"

    assert calls["run_task"] == {"objective": "fix tests"}
    assert calls["get_task"] == "task-1"
    assert calls["approve_task"] == {
        "task_id": "task-1",
        "approval_id": "approval-1",
        "approved": True,
    }
    assert calls["retry_task"] == {"task_id": "task-1", "operator": "operator"}
    assert calls["cancel_task"] == {"task_id": "task-1"}


def test_task_contract_mismatch_blocks_mutating_task_routes_when_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(
        monkeypatch,
        Config(
            server_token="server-secret",
            task_contract=TaskContractConfig(
                enforce=True,
                supported_versions=["2.0"],
                allow_legacy_missing=False,
            ),
        ),
    )
    calls = {"run_task": 0}

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _get_task_contract():
        return {
            "contractVersion": "1.0",
            "supportedOperations": [
                "create",
                "status",
                "approve",
                "retry",
                "cancel",
                "list",
                "events",
                "wait",
            ],
        }

    async def _run_task(task_input):
        calls["run_task"] += 1
        return {"taskId": "task-contract", "status": "queued", "task": task_input}

    provider.get_task_contract = _get_task_contract  # type: ignore[method-assign]
    provider.run_task = _run_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(
        routes_module.manager,
        "resolve",
        lambda model, preferred_profile=None: resolved,
    )

    client = TestClient(app)
    response = client.post(
        "/v1/universal/tasks",
        headers=_auth_header(),
        json={"provider": "agent_bridge", "task": {"objective": "contract check"}},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "contract_mismatch"
    assert response.json()["error"]["details"]["decisionCode"] == "unsupported_contract_version"
    assert calls["run_task"] == 0


def test_task_contract_mismatch_monitor_mode_allows_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(
        monkeypatch,
        Config(
            server_token="server-secret",
            task_contract=TaskContractConfig(
                enforce=False,
                supported_versions=["2.0"],
                allow_legacy_missing=False,
            ),
        ),
    )
    calls = {"run_task": 0}

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _get_task_contract():
        return {
            "contractVersion": "1.0",
            "supportedOperations": [
                "create",
                "status",
                "approve",
                "retry",
                "cancel",
                "list",
                "events",
                "wait",
            ],
        }

    async def _run_task(task_input):
        calls["run_task"] += 1
        return {"taskId": "task-contract-monitor", "status": "queued", "task": task_input}

    provider.get_task_contract = _get_task_contract  # type: ignore[method-assign]
    provider.run_task = _run_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(
        routes_module.manager,
        "resolve",
        lambda model, preferred_profile=None: resolved,
    )

    client = TestClient(app)
    response = client.post(
        "/v1/universal/tasks",
        headers=_auth_header(),
        json={"provider": "agent_bridge", "task": {"objective": "monitor mode"}},
    )

    assert response.status_code == 200
    assert response.json()["operation"] == "create"
    assert calls["run_task"] == 1


def test_universal_contract_status_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(
        monkeypatch,
        Config(
            server_token="server-secret",
            task_contract=TaskContractConfig(
                enforce=True,
                supported_versions=["1.0"],
            ),
        ),
    )

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _get_task_contract():
        return {
            "contractVersion": "1.0",
            "supportedOperations": [
                "create",
                "status",
                "approve",
                "retry",
                "cancel",
                "list",
                "events",
                "wait",
            ],
        }

    provider.get_task_contract = _get_task_contract  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(
        routes_module.manager,
        "resolve",
        lambda model, preferred_profile=None: resolved,
    )

    client = TestClient(app)
    response = client.get("/v1/universal/contract/status", headers=_auth_header())

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "universal.contract.status"
    assert body["status"]["compatible"] is True
    assert body["status"]["decisionCode"] == "contract_compatible"
    assert isinstance(body["status"]["checkedAtMs"], int)
    assert isinstance(body["status"]["expiresAtMs"], int)


def test_task_create_idempotency_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))
    calls = {"count": 0}

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _run_task(task_input):
        calls["count"] += 1
        return {"taskId": "task-replay", "status": "queued", "objective": task_input.get("objective")}

    provider.run_task = _run_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)
    headers = {
        **_auth_header(),
        "Idempotency-Key": "idem-create-1",
    }
    body = {
        "provider": "agent_bridge",
        "task": {"objective": "idempotent-create"},
    }
    first = client.post("/v1/universal/tasks", headers=headers, json=body)
    second = client.post("/v1/universal/tasks", headers=headers, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert calls["count"] == 1
    assert first.headers.get("X-Idempotent-Replay") == "false"
    assert second.headers.get("X-Idempotent-Replay") == "true"


def test_task_create_idempotency_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))
    calls = {"count": 0}

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _run_task(task_input):
        calls["count"] += 1
        return {"taskId": "task-conflict", "status": "queued", "objective": task_input.get("objective")}

    provider.run_task = _run_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)
    headers = {
        **_auth_header(),
        "Idempotency-Key": "idem-create-conflict",
    }
    first = client.post(
        "/v1/universal/tasks",
        headers=headers,
        json={"provider": "agent_bridge", "task": {"objective": "first"}},
    )
    second = client.post(
        "/v1/universal/tasks",
        headers=headers,
        json={"provider": "agent_bridge", "task": {"objective": "second"}},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_key_conflict"
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_task_create_idempotency_concurrent_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))
    calls = {"count": 0}

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _run_task(task_input):
        calls["count"] += 1
        await asyncio.sleep(0.05)
        return {"taskId": "task-concurrent", "status": "queued", "objective": task_input.get("objective")}

    provider.run_task = _run_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer server-secret",
        "Idempotency-Key": "idem-concurrent",
    }
    body = {"provider": "agent_bridge", "task": {"objective": "parallel"}}

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first, second = await asyncio.gather(
            client.post("/v1/universal/tasks", headers=headers, json=body),
            client.post("/v1/universal/tasks", headers=headers, json=body),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] == 1
    replay_values = {first.headers.get("X-Idempotent-Replay"), second.headers.get("X-Idempotent-Replay")}
    assert replay_values == {"false", "true"}


def test_lifecycle_mutations_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))
    calls = {
        "approve": 0,
        "retry": 0,
        "cancel": 0,
    }

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _approve_task(task_id, approval_id, approved=True):
        calls["approve"] += 1
        return {"id": task_id, "status": "running", "approvalId": approval_id, "approved": approved}

    async def _retry_task(task_id, operator=None):
        calls["retry"] += 1
        return {"id": task_id, "status": "running", "operator": operator}

    async def _cancel_task(task_id):
        calls["cancel"] += 1
        return {"id": task_id, "status": "canceled"}

    provider.approve_task = _approve_task  # type: ignore[method-assign]
    provider.retry_task = _retry_task  # type: ignore[method-assign]
    provider.cancel_task = _cancel_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)

    approve_headers = {**_auth_header(), "Idempotency-Key": "idem-approve-1"}
    approve_body = {"provider": "agent_bridge", "approvalId": "approval-1", "approved": True}
    a1 = client.post("/v1/universal/tasks/task-7/approve", headers=approve_headers, json=approve_body)
    a2 = client.post("/v1/universal/tasks/task-7/approve", headers=approve_headers, json=approve_body)

    retry_headers = {**_auth_header(), "Idempotency-Key": "idem-retry-1"}
    retry_body = {"provider": "agent_bridge", "operator": "operator"}
    r1 = client.post("/v1/universal/tasks/task-7/retry", headers=retry_headers, json=retry_body)
    r2 = client.post("/v1/universal/tasks/task-7/retry", headers=retry_headers, json=retry_body)

    cancel_headers = {**_auth_header(), "Idempotency-Key": "idem-cancel-1"}
    cancel_body = {"provider": "agent_bridge"}
    c1 = client.post("/v1/universal/tasks/task-7/cancel", headers=cancel_headers, json=cancel_body)
    c2 = client.post("/v1/universal/tasks/task-7/cancel", headers=cancel_headers, json=cancel_body)

    assert a1.status_code == 200 and a2.status_code == 200
    assert r1.status_code == 200 and r2.status_code == 200
    assert c1.status_code == 200 and c2.status_code == 200
    assert calls == {"approve": 1, "retry": 1, "cancel": 1}
    assert a1.headers.get("X-Idempotent-Replay") == "false"
    assert a2.headers.get("X-Idempotent-Replay") == "true"
    assert r2.headers.get("X-Idempotent-Replay") == "true"
    assert c2.headers.get("X-Idempotent-Replay") == "true"


def test_universal_task_list_and_events_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))
    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _list_tasks():
        return [
            {"id": "task-1", "status": "running"},
            {"id": "task-2", "currentStage": "needs_approval"},
        ]

    async def _get_task_events(task_id, limit=200):
        return [
            {"seq": 1, "status": "queued"},
            {"seq": 2, "status": "running"},
            {"seq": 3, "currentStage": "needs_approval"},
        ]

    provider.list_tasks = _list_tasks  # type: ignore[method-assign]
    provider.get_task_events = _get_task_events  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)
    list_resp = client.get("/v1/universal/tasks", headers=_auth_header())
    events_resp = client.get("/v1/universal/tasks/task-1/events?limit=50", headers=_auth_header())

    assert list_resp.status_code == 200
    assert list_resp.json()["object"] == "universal.task.list"
    assert list_resp.json()["tasks"][0]["normalizedState"] == "running"
    assert list_resp.json()["tasks"][1]["normalizedState"] == "needs_approval"

    assert events_resp.status_code == 200
    body = events_resp.json()
    assert body["object"] == "universal.task.events"
    assert body["task_id"] == "task-1"
    assert body["events"][0]["normalizedState"] == "queued"
    assert body["events"][2]["normalizedState"] == "needs_approval"


def test_universal_task_wait_terminal_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, Config(server_token="server-secret"))
    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")
    state = {"calls": 0}

    async def _get_task(task_id):
        state["calls"] += 1
        if state["calls"] == 1:
            return {"id": task_id, "status": "running"}
        return {"id": task_id, "status": "completed"}

    provider.get_task = _get_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)
    ok_resp = client.post(
        "/v1/universal/tasks/task-9/wait",
        headers=_auth_header(),
        json={"provider": "agent_bridge", "timeoutMs": 3000, "pollMs": 200},
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["object"] == "universal.task.wait"
    assert ok_resp.json()["timed_out"] is False
    assert ok_resp.json()["state"] == "completed"

    async def _always_running(task_id):
        return {"id": task_id, "status": "running"}

    provider.get_task = _always_running  # type: ignore[method-assign]

    timeout_resp = client.post(
        "/v1/universal/tasks/task-10/wait",
        headers=_auth_header(),
        json={"provider": "agent_bridge", "timeoutMs": 1000, "pollMs": 200},
    )
    assert timeout_resp.status_code == 408
    assert timeout_resp.json()["object"] == "universal.task.wait"
    assert timeout_resp.json()["timed_out"] is True
    assert timeout_resp.json()["state"] == "running"


def test_universal_scope_enforcement_for_read_and_write(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config(
        authorization=AuthorizationConfig(
            legacy_admin_compatibility=False,
            tokens={
                "reader": AccessTokenConfig(token="read-secret", scopes=["read"]),
                "writer": AccessTokenConfig(token="write-secret", scopes=["write"]),
            },
        )
    )
    _patch_config(monkeypatch, cfg)

    client = TestClient(app)
    create_resp = client.post(
        "/v1/universal/tasks",
        headers=_auth_header("read-secret"),
        json={"provider": "agent_bridge", "task": {"objective": "scope-check"}},
    )
    list_resp = client.get(
        "/v1/universal/tasks",
        headers=_auth_header("write-secret"),
    )

    assert create_resp.status_code == 403
    assert create_resp.json()["error"]["code"] == "insufficient_scope"
    assert list_resp.status_code == 403
    assert list_resp.json()["error"]["code"] == "insufficient_scope"


def test_task_ownership_enforced_and_admin_bypasses(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config(
        authorization=AuthorizationConfig(
            legacy_admin_compatibility=False,
            tokens={
                "owner": AccessTokenConfig(token="owner-secret", scopes=["read", "write"]),
                "other": AccessTokenConfig(token="other-secret", scopes=["read", "write"]),
                "admin": AccessTokenConfig(token="admin-secret", admin=True),
            },
        )
    )
    _patch_config(monkeypatch, cfg)

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _run_task(task_input):
        objective = str(task_input.get("objective") or "").strip().lower()
        if objective == "owner":
            return {"taskId": "task-owner", "status": "queued"}
        if objective == "other":
            return {"taskId": "task-other", "status": "queued"}
        return {"taskId": "task-unknown", "status": "queued"}

    async def _get_task(task_id):
        return {"id": task_id, "status": "running"}

    async def _list_tasks():
        return [
            {"id": "task-owner", "status": "running"},
            {"id": "task-other", "status": "running"},
        ]

    provider.run_task = _run_task  # type: ignore[method-assign]
    provider.get_task = _get_task  # type: ignore[method-assign]
    provider.list_tasks = _list_tasks  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)
    owner_create = client.post(
        "/v1/universal/tasks",
        headers=_auth_header("owner-secret"),
        json={"provider": "agent_bridge", "task": {"objective": "owner"}},
    )
    other_create = client.post(
        "/v1/universal/tasks",
        headers=_auth_header("other-secret"),
        json={"provider": "agent_bridge", "task": {"objective": "other"}},
    )
    other_status_owner = client.get(
        "/v1/universal/tasks/task-owner",
        headers=_auth_header("other-secret"),
    )
    admin_status_owner = client.get(
        "/v1/universal/tasks/task-owner",
        headers=_auth_header("admin-secret"),
    )
    owner_list = client.get("/v1/universal/tasks", headers=_auth_header("owner-secret"))
    admin_list = client.get("/v1/universal/tasks", headers=_auth_header("admin-secret"))

    assert owner_create.status_code == 200
    assert other_create.status_code == 200
    assert other_status_owner.status_code == 403
    assert other_status_owner.json()["error"]["code"] == "task_owner_mismatch"
    assert admin_status_owner.status_code == 200
    assert owner_list.status_code == 200
    assert admin_list.status_code == 200
    assert [item["id"] for item in owner_list.json()["tasks"]] == ["task-owner"]
    assert sorted(item["id"] for item in admin_list.json()["tasks"]) == [
        "task-other",
        "task-owner",
    ]


def test_idempotency_key_is_actor_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config(
        authorization=AuthorizationConfig(
            legacy_admin_compatibility=False,
            tokens={
                "owner": AccessTokenConfig(token="owner-secret", scopes=["write"]),
                "other": AccessTokenConfig(token="other-secret", scopes=["write"]),
            },
        )
    )
    _patch_config(monkeypatch, cfg)
    calls = {"count": 0}

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _run_task(task_input):
        calls["count"] += 1
        return {"taskId": "task-actor", "status": "queued", "objective": task_input.get("objective")}

    provider.run_task = _run_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)
    headers_owner = {
        **_auth_header("owner-secret"),
        "Idempotency-Key": "idem-actor-scope",
    }
    headers_other = {
        **_auth_header("other-secret"),
        "Idempotency-Key": "idem-actor-scope",
    }
    body = {"provider": "agent_bridge", "task": {"objective": "actor-scope"}}

    first = client.post("/v1/universal/tasks", headers=headers_owner, json=body)
    second = client.post("/v1/universal/tasks", headers=headers_other, json=body)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_key_conflict"
    assert calls["count"] == 1


def test_idempotency_replay_survives_cache_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = Path(tempfile.gettempdir()) / f"open_llm_auth_durable_{uuid4().hex}.sqlite3"
    cfg = Config(
        server_token="server-secret",
        durable_state=DurableStateConfig(db_path=str(db_path), enabled=True),
    )
    reset_durable_state_store_cache()
    monkeypatch.setattr(auth_module, "load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(config_routes_module, "load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(routes_module, "load_config", lambda *args, **kwargs: cfg)
    calls = {"count": 0}

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _run_task(task_input):
        calls["count"] += 1
        return {"taskId": "task-cache-reset", "status": "queued", "objective": task_input.get("objective")}

    provider.run_task = _run_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)
    headers = {**_auth_header(), "Idempotency-Key": "idem-durable-reset-1"}
    body = {"provider": "agent_bridge", "task": {"objective": "durable replay"}}

    first = client.post("/v1/universal/tasks", headers=headers, json=body)
    reset_durable_state_store_cache()
    second = client.post("/v1/universal/tasks", headers=headers, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] == 1
    assert second.headers.get("X-Idempotent-Replay") == "true"


def test_task_owner_survives_cache_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = Path(tempfile.gettempdir()) / f"open_llm_auth_durable_{uuid4().hex}.sqlite3"
    cfg = Config(
        authorization=AuthorizationConfig(
            legacy_admin_compatibility=False,
            tokens={
                "owner": AccessTokenConfig(token="owner-secret", scopes=["read", "write"]),
                "other": AccessTokenConfig(token="other-secret", scopes=["read", "write"]),
                "admin": AccessTokenConfig(token="admin-secret", admin=True),
            },
        ),
        durable_state=DurableStateConfig(db_path=str(db_path), enabled=True),
    )
    reset_durable_state_store_cache()
    monkeypatch.setattr(auth_module, "load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(config_routes_module, "load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(routes_module, "load_config", lambda *args, **kwargs: cfg)

    provider = AgentBridgeProvider(provider_id="agent_bridge", base_url="http://127.0.0.1:1")

    async def _run_task(task_input):
        return {"taskId": "task-persist-owner", "status": "queued"}

    async def _get_task(task_id):
        return {"id": task_id, "status": "running"}

    provider.run_task = _run_task  # type: ignore[method-assign]
    provider.get_task = _get_task  # type: ignore[method-assign]

    resolved = SimpleNamespace(
        provider=provider,
        providers=[provider],
        provider_id="agent_bridge",
        model_id="assistant",
        profile_id=None,
        auth_source="provider-config:no-auth-header",
    )
    monkeypatch.setattr(routes_module.manager, "resolve", lambda model, preferred_profile=None: resolved)

    client = TestClient(app)
    create = client.post(
        "/v1/universal/tasks",
        headers=_auth_header("owner-secret"),
        json={"provider": "agent_bridge", "task": {"objective": "persist-owner"}},
    )
    assert create.status_code == 200

    reset_durable_state_store_cache()
    denied = client.get("/v1/universal/tasks/task-persist-owner", headers=_auth_header("other-secret"))
    allowed = client.get("/v1/universal/tasks/task-persist-owner", headers=_auth_header("admin-secret"))

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "task_owner_mismatch"
    assert allowed.status_code == 200
