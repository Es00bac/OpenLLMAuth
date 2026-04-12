from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import open_llm_auth.providers.openbulma as openbulma_module
from open_llm_auth.providers.openbulma import OpenBulmaProvider


@pytest.mark.asyncio
async def test_mutating_calls_send_contract_headers() -> None:
    provider = OpenBulmaProvider(provider_id="openbulma", base_url="http://127.0.0.1:1")
    seen: list[dict[str, str]] = []

    async def _fake_post(path, payload, *, extra_headers=None):
        assert isinstance(path, str)
        assert isinstance(payload, dict)
        assert isinstance(extra_headers, dict)
        seen.append(extra_headers)
        return {"ok": True}

    provider._post_json = _fake_post  # type: ignore[method-assign]

    await provider.run_task({"objective": "x"})
    await provider.retry_task("task-1", operator="operator")
    await provider.approve_task("task-1", "approval-1", True)
    await provider.cancel_task("task-1")

    assert len(seen) == 4
    for headers in seen:
        assert headers["X-Provider-Contract-Version"] == "1.0"
        assert headers["X-Gateway-Version"] == "open_llm_auth/1.0"
        assert headers["X-Request-Id"].startswith("req-")


@pytest.mark.asyncio
async def test_chat_completion_forwards_bounded_context_into_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenBulmaProvider(provider_id="openbulma", base_url="http://127.0.0.1:1")
    seen: dict[str, Any] = {}

    class _FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
            seen["url"] = url
            seen["json"] = json
            seen["headers"] = headers
            return _FakeResponse({"reply": "context preserved"})

    monkeypatch.setattr(openbulma_module.httpx, "AsyncClient", _FakeAsyncClient)

    response = await provider.chat_completion(
        model="kimi-coding/k2p5",
        messages=[
            {"role": "system", "content": "Stay terse."},
            {"role": "user", "content": "We were reviewing the gateway yesterday."},
            {"role": "assistant", "content": "Yes, task streaming was shallow."},
            {"role": "user", "content": [{"type": "text", "text": "Please finish T8."}]},
        ],
        payload={"temperature": 0.25, "authProfile": "operator"},
    )

    assert response["choices"][0]["message"]["content"] == "context preserved"
    assert seen["url"] == "http://127.0.0.1:1/chat"
    assert seen["json"]["message"] == "Please finish T8."
    assert seen["json"]["temperature"] == 0.25
    assert seen["json"]["authProfile"] == "operator"
    assert seen["json"]["modelProfile"] == "kimi-coding/k2p5"
    assert "Stay terse." in seen["json"]["system"]
    assert "Gateway conversation context:" in seen["json"]["system"]
    assert "user: We were reviewing the gateway yesterday." in seen["json"]["system"]
    assert "assistant: Yes, task streaming was shallow." in seen["json"]["system"]
    assert "Please finish T8." not in seen["json"]["system"]


@pytest.mark.asyncio
async def test_task_stream_polls_runtime_until_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenBulmaProvider(provider_id="openbulma", base_url="http://127.0.0.1:1")
    statuses = [
        {
            "id": "task-1",
            "status": "running",
            "currentStage": "planning",
            "phase": "detect",
            "summary": "detecting incident",
            "updatedAtMs": 1,
        },
        {
            "id": "task-1",
            "status": "running",
            "currentStage": "needs_approval",
            "phase": "patch",
            "summary": "waiting for approval",
            "updatedAtMs": 2,
            "blockedDurationMs": 2500,
            "stageErrorContext": {
                "errorType": "approval_required",
                "errorMessage": "Operator approval required before applying patch",
                "approvalId": "approval-1",
            },
        },
        {
            "id": "task-1",
            "status": "done",
            "currentStage": "done",
            "phase": "done",
            "summary": "all checks passed",
            "updatedAtMs": 3,
            "completionState": "verified",
        },
    ]
    events = [
        [
            {"ts": "2026-03-12T00:00:00Z", "note": "created", "task": statuses[0]},
            {"ts": "2026-03-12T00:00:01Z", "note": "phase_detect", "task": statuses[0]},
        ],
        [
            {"ts": "2026-03-12T00:00:00Z", "note": "created", "task": statuses[0]},
            {"ts": "2026-03-12T00:00:01Z", "note": "phase_detect", "task": statuses[0]},
            {"ts": "2026-03-12T00:00:02Z", "note": "needs_approval", "task": statuses[1]},
        ],
        [
            {"ts": "2026-03-12T00:00:00Z", "note": "created", "task": statuses[0]},
            {"ts": "2026-03-12T00:00:01Z", "note": "phase_detect", "task": statuses[0]},
            {"ts": "2026-03-12T00:00:02Z", "note": "needs_approval", "task": statuses[1]},
            {"ts": "2026-03-12T00:00:03Z", "note": "done", "task": statuses[2]},
        ],
    ]
    state = {"index": 0}

    async def _fake_run_task(task_input: dict[str, Any]) -> dict[str, Any]:
        assert task_input["objective"] == "Run the gateway task."
        return {
            "taskId": "task-1",
            "status": "queued",
            "createdAt": 123,
            "dispatchId": "dispatch-1",
        }

    async def _fake_get_task(task_id: str) -> dict[str, Any]:
        assert task_id == "task-1"
        current = statuses[min(state["index"], len(statuses) - 1)]
        state["index"] += 1
        return current

    async def _fake_get_task_events(task_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        assert task_id == "task-1"
        assert limit == provider.TASK_STREAM_EVENT_LIMIT
        return events[min(max(state["index"] - 1, 0), len(events) - 1)]

    async def _fake_sleep(_: float) -> None:
        return None

    provider.run_task = _fake_run_task  # type: ignore[method-assign]
    provider.get_task = _fake_get_task  # type: ignore[method-assign]
    provider.get_task_events = _fake_get_task_events  # type: ignore[method-assign]
    monkeypatch.setattr(openbulma_module.asyncio, "sleep", _fake_sleep)

    stream = await provider.chat_completion_stream(
        model="assistant",
        messages=[{"role": "user", "content": "Run the gateway task."}],
        payload={},
    )
    chunks = [chunk async for chunk in stream]
    content = _collect_stream_content(chunks)

    assert "Queued Bulma task task-1 (status: queued)." in content
    assert "Phase update: detect. Summary: detecting incident" in content
    assert "Needs approval. Summary: waiting for approval" in content
    assert "Approval id: approval-1" in content
    assert "Blocked for: 2.5s" in content
    assert "Task status: done." in content
    assert "Completion state: verified" in content
    assert chunks[-1] == b"data: [DONE]\n\n"


def _collect_stream_content(chunks: list[bytes]) -> str:
    parts: list[str] = []
    for raw in chunks:
        text = raw.decode("utf-8")
        if not text.startswith("data: {"):
            continue
        payload = json.loads(text[len("data: ") :].strip())
        delta = payload["choices"][0]["delta"]
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts)
