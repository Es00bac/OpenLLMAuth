"""Primary API routes for the live gateway.

This module serves two related protocol surfaces:
- an OpenAI-compatible chat/models shim for drop-in clients
- a universal/task API that preserves richer lifecycle semantics such as
  durable ownership, idempotency, wait semantics, and Agent Bridge contract checks
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..auth.manager import ProviderManager, ResolvedProvider
from ..config import load_config
from ..providers import AgentBridgeProvider
from .auth import Principal, require_scopes, verify_server_token
from .durable_state import (
    DurableIdempotencyToken,
    DurableControlStateStore,
    get_durable_state_store,
)
from .egress_policy import UnsafeDestinationError, unsafe_destination_detail
from .task_contract import evaluate_task_contract, get_task_contract_status
from .usage_store import get_usage_store
from .models import (
    ChatCompletionRequest,
    EmbeddingRequest,
    ModelList,
    UniversalRequest,
    UniversalTaskEventListResponse,
    UniversalTaskApproveRequest,
    UniversalTaskCancelRequest,
    UniversalTaskCreateRequest,
    UniversalTaskListResponse,
    UniversalTaskRetryRequest,
    UniversalTaskWaitRequest,
)


# `/v1` contains two parallel surfaces:
# - the OpenAI-compatible chat shim for drop-in client compatibility
# - a universal/task API that preserves richer task lifecycle semantics
# Future agents should pick the universal surface whenever they need durable
# ownership, idempotency, wait semantics, or task mutation endpoints.
router = APIRouter(prefix="/v1")
manager = ProviderManager()


def _get_control_state_store() -> DurableControlStateStore:
    """Return the durable task/idempotency store or raise when disabled."""
    cfg = load_config()
    if not cfg.durable_state.enabled:
        raise RuntimeError("durableState.enabled is false")
    return get_durable_state_store(cfg)


def _openai_error(
    message: str,
    *,
    code: str,
    error_type: str = "invalid_request_error",
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": code,
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    return payload


def _universal_error(
    message: str,
    *,
    code: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "error": {
            "message": message,
            "code": code,
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    return payload


def _provider_resolution_universal_payload(
    exc: ValueError,
) -> tuple[int, Dict[str, Any]]:
    if isinstance(exc, UnsafeDestinationError):
        return (
            403,
            _universal_error(
                "Outbound destination blocked by policy.",
                code="egress_destination_blocked",
                details=unsafe_destination_detail(exc),
            ),
        )
    message = str(exc)
    status_code = 400 if "not supported" in message else 404
    return status_code, _universal_error(message, code="provider_resolution_failed")


def _provider_resolution_universal_response(exc: ValueError) -> JSONResponse:
    status_code, content = _provider_resolution_universal_payload(exc)
    return JSONResponse(status_code=status_code, content=content)


def _provider_resolution_openai_response(exc: ValueError) -> JSONResponse:
    if isinstance(exc, UnsafeDestinationError):
        return JSONResponse(
            status_code=403,
            content=_openai_error(
                "Outbound destination blocked by policy.",
                code="egress_destination_blocked",
                details=unsafe_destination_detail(exc),
            ),
        )
    return JSONResponse(
        status_code=404,
        content=_openai_error(str(exc), code="provider_resolution_failed"),
    )


def _idempotency_headers(
    *,
    key: Optional[str],
    replay: Optional[bool] = None,
) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if key:
        headers["Idempotency-Key"] = key
    if replay is not None:
        headers["X-Idempotent-Replay"] = "true" if replay else "false"
    return headers


def _idempotency_fingerprint_payload(
    *,
    operation: str,
    route_task_id: Optional[str],
    actor: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    body = {
        "actor": actor,
        "operation": operation,
        "task_id": route_task_id,
        "payload": payload,
    }
    return body


async def _maybe_replay_idempotent(
    *,
    key: Optional[str],
    operation: str,
    route_task_id: Optional[str],
    actor: str,
    payload: Dict[str, Any],
) -> tuple[Optional[DurableIdempotencyToken], Optional[JSONResponse]]:
    if not key:
        return None, None

    try:
        control_state = _get_control_state_store()
    except Exception as exc:
        logging.error("Durable control-state unavailable: %s", exc, exc_info=True)
        unavailable = _universal_error(
            "Durable control-state store is unavailable.",
            code="persistence_unavailable",
        )
        return None, _json_response(status_code=503, content=unavailable)

    fingerprint_payload = _idempotency_fingerprint_payload(
        operation=operation,
        route_task_id=route_task_id,
        actor=actor,
        payload=payload,
    )
    fingerprint = control_state.fingerprint(fingerprint_payload)
    claim = await control_state.claim_idempotency(
        key=key,
        scope=operation,
        subject=actor,
        fingerprint=fingerprint,
    )

    if claim.status == "conflict":
        conflict = _universal_error(
            "Idempotency-Key was already used with a different request payload.",
            code="idempotency_key_conflict",
        )
        return None, JSONResponse(
            status_code=409,
            content=conflict,
            headers=_idempotency_headers(key=key, replay=False),
        )

    if claim.status == "replay" and claim.response is not None:
        return None, JSONResponse(
            status_code=claim.response.status_code,
            content=claim.response.body,
            headers=_idempotency_headers(key=key, replay=True),
        )

    if claim.status == "in_progress":
        in_progress = _universal_error(
            "Idempotency-Key request is currently in progress.",
            code="idempotency_in_progress",
        )
        return None, JSONResponse(
            status_code=409,
            content=in_progress,
            headers=_idempotency_headers(key=key, replay=False),
        )

    if claim.status == "new" and claim.token is not None:
        return claim.token, None

    # Defensive fallback.
    return None, JSONResponse(
        status_code=500,
        content=_universal_error(
            "Failed to process idempotency state.",
            code="idempotency_error",
        ),
        headers=_idempotency_headers(key=key, replay=False),
    )


def _scope_denied(scope: str) -> Dict[str, Any]:
    return _universal_error(
        f"Authenticated token is missing required scope '{scope}'.",
        code="insufficient_scope",
    )


def _enforce_scope(principal: Principal, scope: str) -> Optional[Dict[str, Any]]:
    try:
        require_scopes(principal, scope)
    except HTTPException:
        return _scope_denied(scope)
    return None


async def _enforce_task_owner(
    *,
    principal: Principal,
    provider_id: str,
    task_id: str,
) -> Optional[Dict[str, Any]]:
    if principal.is_admin:
        return None

    try:
        control_state = _get_control_state_store()
        owner = await control_state.get_task_owner(provider_id=provider_id, task_id=task_id)
    except Exception as exc:
        logging.error("Durable control-state unavailable: %s", exc, exc_info=True)
        return _universal_error(
            "Durable control-state store is unavailable.",
            code="persistence_unavailable",
        )

    if owner is None:
        return _universal_error(
            "Task ownership is unknown for this gateway instance. Admin token required.",
            code="task_owner_unknown",
        )
    if owner != principal.subject:
        return _universal_error(
            "Task is owned by a different principal.",
            code="task_owner_mismatch",
        )
    return None


def _owner_error_status(payload: Dict[str, Any]) -> int:
    code = str(payload.get("error", {}).get("code") or "").strip().lower()
    if code == "persistence_unavailable":
        return 503
    return 403


async def _enforce_mutating_task_contract(
    adapter: AgentBridgeProvider,
) -> Optional[Dict[str, Any]]:
    cfg = load_config()
    decision = await evaluate_task_contract(provider=adapter, cfg=cfg)
    if decision.compatible:
        return None
    if not cfg.task_contract.enforce:
        logging.warning(
            "Task contract mismatch in monitor mode: code=%s details=%s",
            decision.code,
            decision.details,
        )
        return None
    details = dict(decision.details)
    details["decisionCode"] = decision.code
    details["enforce"] = True
    details["fromCache"] = decision.from_cache
    return _universal_error(
        "Gateway and provider task contracts are incompatible.",
        code="contract_mismatch",
        details=details,
    )


async def _store_idempotent(
    token: Optional[DurableIdempotencyToken],
    *,
    status_code: int,
    content: Dict[str, Any],
) -> bool:
    if token is None:
        return True
    try:
        control_state = _get_control_state_store()
        return await control_state.store_idempotency(
            token,
            status_code=status_code,
            body=content,
        )
    except Exception as exc:
        logging.error("Durable control-state unavailable: %s", exc, exc_info=True)
        return False


async def _append_task_action(
    *,
    provider_id: str,
    task_id: Optional[str],
    action: str,
    subject: str,
    outcome: str,
    idempotency_key: Optional[str] = None,
    status_code: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        control_state = _get_control_state_store()
        await control_state.append_task_action(
            provider_id=provider_id,
            task_id=task_id,
            action=action,
            subject=subject,
            outcome=outcome,
            idempotency_key=idempotency_key,
            status_code=status_code,
            details=details,
        )
    except Exception:
        # Best effort history append; does not alter route outcome.
        logging.exception("Failed to append task action history")


def _json_response(
    *,
    status_code: int,
    content: Dict[str, Any],
    idempotency_key: Optional[str] = None,
    replay: Optional[bool] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=_idempotency_headers(key=idempotency_key, replay=replay),
    )


def _sse_data(payload: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=True)}\\n\\n".encode("utf-8")


def _safe_upstream_http_error(provider_id: str, status_code: int) -> Dict[str, Any]:
    return _openai_error(
        f"Upstream provider '{provider_id}' returned HTTP {status_code}",
        code="upstream_http_error",
        error_type="api_error",
    )


def _to_message_dicts(messages: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for message in messages:
        if hasattr(message, "model_dump"):
            out.append(message.model_dump(exclude_none=True))
        elif isinstance(message, dict):
            out.append({k: v for k, v in message.items() if v is not None})
    return out


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: List[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
        return "\\n".join(p for p in pieces if p)
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return content["text"]
    return ""


def _extract_primary_text(response_data: Dict[str, Any]) -> str:
    if not isinstance(response_data, dict):
        return ""

    choices = response_data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else {}
        if isinstance(message, dict):
            text = _extract_text_content(message.get("content"))
            if text:
                return text

    for key in ("reply", "rawText", "text", "content"):
        value = response_data.get(key)
        text = _extract_text_content(value)
        if text:
            return text

    return ""


def _universal_task_response(
    *,
    operation: str,
    resolved: ResolvedProvider,
    task_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "object": "universal.task",
        "operation": operation,
        "provider": resolved.provider_id,
        "model": resolved.model_id,
        "profile": resolved.profile_id,
        "auth_source": resolved.auth_source,
        "task_id": task_id,
        "response": payload,
    }


def _estimate_tokens_from_text(text: str) -> int:
    """Rough token estimator for usage logging when no exact count is available."""
    return max(1, len(text) // 4)


def _extract_usage_from_response(data: Dict[str, Any]) -> Dict[str, int]:
    """Extract OpenAI-style usage fields from a response dict."""
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or usage.get("promptTokens") or 0),
        "completion_tokens": int(
            usage.get("completion_tokens") or usage.get("completionTokens") or 0
        ),
        "total_tokens": int(usage.get("total_tokens") or usage.get("totalTokens") or 0),
    }


def _pop_response_telemetry(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = data.pop("_open_llm_auth", None)
    if isinstance(raw, dict):
        return raw
    return {}


def _record_usage(
    *,
    resolved: ResolvedProvider,
    endpoint: str,
    source: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: Optional[int] = None,
    latency_ms: int = 0,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort usage logging; never raises."""
    try:
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        meta_payload = dict(meta or {})
        meta_payload.setdefault("auth_source", resolved.auth_source)
        get_usage_store().record(
            provider=resolved.provider_id,
            model=resolved.model_id,
            endpoint=endpoint,
            source=source,
            profile_id=resolved.profile_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            cost=0.0,
            meta=meta_payload,
        )
    except Exception:
        logging.debug("Failed to record usage", exc_info=True)


def _resolve_agent_bridge_provider(
    *,
    provider: str,
    preferred_profile: Optional[str],
) -> tuple[ResolvedProvider, AgentBridgeProvider]:
    normalized = (provider or "").strip().lower() or "agent_bridge"
    if normalized not in {"agent_bridge", "agent"}:
        raise ValueError(
            f"Provider '{provider}' is not supported for universal task lifecycle routes."
        )

    resolved = manager.resolve(f"{normalized}/assistant", preferred_profile=preferred_profile)
    provider_instance = resolved.provider
    if not isinstance(provider_instance, AgentBridgeProvider):
        raise ValueError(
            f"Provider '{resolved.provider_id}' does not support universal task lifecycle operations."
    )
    return resolved, provider_instance


def _normalize_task_state(task_payload: Dict[str, Any]) -> str:
    current_stage = str(task_payload.get("currentStage") or "").strip().lower()
    status = str(task_payload.get("status") or "").strip().lower()

    value = current_stage or status
    if not value:
        return "unknown"
    if value in {"needs_approval", "waiting_approval"}:
        return "needs_approval"
    if value in {"done", "completed", "success"}:
        return "completed"
    if value in {"failed", "error"}:
        return "failed"
    if value in {"canceled", "cancelled"}:
        return "canceled"
    if value in {"running", "in_progress"}:
        return "running"
    if value in {"queued", "pending", "created", "idle"}:
        return "queued"
    return value


def _is_terminal_task_state(state: str) -> bool:
    return state in {"completed", "failed", "canceled"}


async def _wrap_sse_stream(
    stream_iter: AsyncIterator[bytes],
    *,
    provider_id: str,
) -> AsyncIterator[bytes]:
    try:
        async for chunk in stream_iter:
            yield chunk
    except httpx.HTTPStatusError as exc:
        payload = _safe_upstream_http_error(provider_id, exc.response.status_code)
        yield _sse_data(payload)
        yield b"data: [DONE]\\n\\n"
    except Exception as exc:
        logging.error(
            "Stream error for provider '%s': %s",
            provider_id,
            exc,
            exc_info=True,
        )
        yield _sse_data(
            _openai_error(
                f"Provider '{provider_id}' stream failed.",
                code="provider_error",
                error_type="api_error",
            )
        )
        yield b"data: [DONE]\\n\\n"


async def _wrap_universal_stream(
    stream_iter: AsyncIterator[bytes],
    *,
    provider_id: str,
    model_id: str,
) -> AsyncIterator[bytes]:
    try:
        async for chunk in stream_iter:
            event = {
                "object": "universal.chunk",
                "provider": provider_id,
                "model": model_id,
                "raw": chunk.decode("utf-8", errors="replace"),
            }
            yield _sse_data(event)
        yield b"data: [DONE]\\n\\n"
    except httpx.HTTPStatusError as exc:
        yield _sse_data(
            _universal_error(
                f"Upstream provider '{provider_id}' returned HTTP {exc.response.status_code}",
                code="upstream_http_error",
            )
        )
        yield b"data: [DONE]\\n\\n"
    except Exception as exc:
        logging.error(
            "Universal stream error for provider '%s': %s",
            provider_id,
            exc,
            exc_info=True,
        )
        yield _sse_data(
            _universal_error(
                f"Provider '{provider_id}' stream failed.",
                code="provider_error",
            )
        )
        yield b"data: [DONE]\\n\\n"


async def _execute_stream_with_fallbacks(
    *,
    resolved: ResolvedProvider,
    messages_dump: List[Dict[str, Any]],
    payload: Dict[str, Any],
) -> tuple[AsyncIterator[bytes], str]:
    last_exc: Optional[httpx.HTTPStatusError] = None

    for provider in resolved.providers:
        try:
            stream_iter = await provider.chat_completion_stream(
                model=resolved.model_id,
                messages=messages_dump,
                payload=payload,
            )

            aiter = stream_iter.__aiter__()
            try:
                first_chunk = await aiter.__anext__()
            except StopAsyncIteration:

                async def _empty() -> AsyncIterator[bytes]:
                    if False:
                        yield b""

                return _empty(), provider.provider_id

            async def _resume() -> AsyncIterator[bytes]:
                yield first_chunk
                async for chunk in aiter:
                    yield chunk

            return _resume(), provider.provider_id
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                logging.warning(
                    "Provider %s rate limited on stream, trying next fallback key.",
                    resolved.provider_id,
                )
                last_exc = exc
                continue
            raise

    if last_exc:
        raise last_exc
    raise ValueError("No providers available to handle the request.")


async def _execute_non_stream_with_fallbacks(
    *,
    resolved: ResolvedProvider,
    messages_dump: List[Dict[str, Any]],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    last_exc: Optional[httpx.HTTPStatusError] = None

    for provider in resolved.providers:
        try:
            return await provider.chat_completion(
                model=resolved.model_id,
                messages=messages_dump,
                payload=payload,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                logging.warning(
                    "Provider %s rate limited, trying next fallback key.",
                    resolved.provider_id,
                )
                last_exc = exc
                continue
            raise

    if last_exc:
        raise last_exc
    raise ValueError("No providers available to handle the request.")


async def _execute_embeddings_with_fallbacks(
    *,
    resolved: ResolvedProvider,
    input_texts: List[str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    last_exc: Optional[httpx.HTTPStatusError] = None

    for provider in resolved.providers:
        try:
            return await provider.embeddings(
                model=resolved.model_id,
                input_texts=input_texts,
                payload=payload,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                logging.warning(
                    "Provider %s rate limited on embeddings, trying next fallback key.",
                    resolved.provider_id,
                )
                last_exc = exc
                continue
            raise

    if last_exc:
        raise last_exc
    raise ValueError("No providers available to handle the request.")


@router.post("/universal")
async def universal_completions(
    request: UniversalRequest,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    principal: Principal = Depends(verify_server_token),
):
    """Universal provider call surface with optional task payload semantics."""
    denied = _enforce_scope(principal, "write")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    preferred_profile = (request.auth_profile or x_auth_profile or "").strip() or None
    payload = dict(request.options or {})
    payload["stream"] = bool(request.stream)

    if request.task is not None:
        payload["task"] = request.task

    messages_dump = _to_message_dicts(request.input)

    # Allow task-only requests by deriving a basic user message from objective text.
    if not messages_dump and isinstance(request.task, dict):
        objective = request.task.get("objective")
        if isinstance(objective, str) and objective.strip():
            messages_dump = [{"role": "user", "content": objective.strip()}]

    if not messages_dump and request.task is None:
        return JSONResponse(
            status_code=400,
            content=_universal_error(
                "Request must include non-empty input or task.",
                code="invalid_request",
            ),
        )

    prompt_text = "\n".join(str(m.get("content", "")) for m in messages_dump)
    estimated_prompt_tokens = _estimate_tokens_from_text(prompt_text)

    try:
        resolved = manager.resolve(request.model, preferred_profile=preferred_profile)
    except ValueError as exc:
        return _provider_resolution_universal_response(exc)

    if request.stream:
        try:
            stream_iter, active_provider_id = await _execute_stream_with_fallbacks(
                resolved=resolved,
                messages_dump=messages_dump,
                payload=payload,
            )
            _record_usage(
                resolved=resolved,
                endpoint="universal.stream",
                source="universal",
                prompt_tokens=estimated_prompt_tokens,
            )
            return StreamingResponse(
                _wrap_universal_stream(
                    stream_iter,
                    provider_id=active_provider_id,
                    model_id=resolved.model_id,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        except httpx.HTTPStatusError as exc:
            return JSONResponse(
                status_code=exc.response.status_code,
                content=_universal_error(
                    f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
                    code="upstream_http_error",
                ),
            )
        except Exception as exc:
            logging.error(
                "Universal stream start error for provider '%s': %s",
                resolved.provider_id,
                exc,
                exc_info=True,
            )
            return JSONResponse(
                status_code=500,
                content=_universal_error(
                    f"Provider '{resolved.provider_id}' stream start failed.",
                    code="provider_error",
                ),
            )

    start = time.perf_counter()
    try:
        provider_response = await _execute_non_stream_with_fallbacks(
            resolved=resolved,
            messages_dump=messages_dump,
            payload=payload,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        telemetry_meta = _pop_response_telemetry(provider_response)
        usage = _extract_usage_from_response(provider_response)
        _record_usage(
            resolved=resolved,
            endpoint="universal",
            source="universal",
            prompt_tokens=usage.get("prompt_tokens", estimated_prompt_tokens),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", None),
            latency_ms=latency_ms,
            meta=telemetry_meta,
        )
        return {
            "object": "universal.response",
            "provider": resolved.provider_id,
            "model": resolved.model_id,
            "profile": resolved.profile_id,
            "auth_source": resolved.auth_source,
            "output_text": _extract_primary_text(provider_response),
            "response": provider_response,
        }
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            status_code=exc.response.status_code,
            content=_universal_error(
                f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
                code="upstream_http_error",
            ),
        )
    except Exception as exc:
        logging.error(
            "Universal request error for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=_universal_error(
                f"Provider '{resolved.provider_id}' call failed.",
                code="provider_error",
            ),
        )


@router.post("/universal/tasks")
async def universal_task_create(
    request: UniversalTaskCreateRequest,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(verify_server_token),
):
    """Create a task against an Agent Bridge-compatible runtime with durable guards."""
    denied = _enforce_scope(principal, "write")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    try:
        control_state = _get_control_state_store()
    except Exception as exc:
        logging.error("Durable control-state unavailable: %s", exc, exc_info=True)
        return _json_response(
            status_code=503,
            content=_universal_error(
                "Durable control-state store is unavailable.",
                code="persistence_unavailable",
            ),
        )

    request_body = request.model_dump(by_alias=True, exclude_none=True)
    token, replay_response = await _maybe_replay_idempotent(
        key=x_idempotency_key,
        operation="task_create",
        route_task_id=None,
        actor=principal.subject,
        payload=request_body,
    )
    if replay_response is not None:
        return replay_response

    preferred_profile = (request.auth_profile or x_auth_profile or "").strip() or None
    try:
        resolved, provider = _resolve_agent_bridge_provider(
            provider=request.provider,
            preferred_profile=preferred_profile,
        )
    except ValueError as exc:
        status_code, content = _provider_resolution_universal_payload(exc)
        await _store_idempotent(token, status_code=status_code, content=content)
        await _append_task_action(
            provider_id=request.provider,
            task_id=None,
            action="create",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=status_code,
            details={"reason": "provider_resolution_failed"},
        )
        return _json_response(
            status_code=status_code,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    contract_denied = await _enforce_mutating_task_contract(provider)
    if contract_denied is not None:
        await _store_idempotent(token, status_code=409, content=contract_denied)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=None,
            action="create",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=409,
            details={"reason": "contract_mismatch"},
        )
        return _json_response(
            status_code=409,
            content=contract_denied,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    try:
        response_data = await provider.run_task(dict(request.task))
        task_id = str(response_data.get("taskId") or response_data.get("id") or "")
        if task_id:
            claimed = await control_state.claim_task_owner(
                provider_id=resolved.provider_id,
                task_id=task_id,
                owner_subject=principal.subject,
                created_by_subject=principal.subject,
            )
            if not claimed:
                content = _universal_error(
                    "Task ownership conflict for returned task id.",
                    code="task_owner_conflict",
                )
                await _store_idempotent(token, status_code=409, content=content)
                await _append_task_action(
                    provider_id=resolved.provider_id,
                    task_id=task_id,
                    action="create",
                    subject=principal.subject,
                    outcome="rejected",
                    idempotency_key=x_idempotency_key,
                    status_code=409,
                    details={"reason": "task_owner_conflict"},
                )
                return _json_response(
                    status_code=409,
                    content=content,
                    idempotency_key=x_idempotency_key,
                    replay=False if token is not None else None,
                )
        content = _universal_task_response(
            operation="create",
            resolved=resolved,
            task_id=task_id,
            payload=response_data,
        )
        await _store_idempotent(token, status_code=200, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id or None,
            action="create",
            subject=principal.subject,
            outcome="applied",
            idempotency_key=x_idempotency_key,
            status_code=200,
        )
        return _json_response(
            status_code=200,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        content = _universal_error(
            f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
            code="upstream_http_error",
        )
        await _store_idempotent(token, status_code=status_code, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=None,
            action="create",
            subject=principal.subject,
            outcome="failed",
            idempotency_key=x_idempotency_key,
            status_code=status_code,
            details={"reason": "upstream_http_error"},
        )
        return _json_response(
            status_code=status_code,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )
    except Exception as exc:
        logging.error(
            "Universal task create failed for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        content = _universal_error(
            f"Provider '{resolved.provider_id}' task create failed.",
            code="provider_error",
        )
        await _store_idempotent(token, status_code=500, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=None,
            action="create",
            subject=principal.subject,
            outcome="failed",
            idempotency_key=x_idempotency_key,
            status_code=500,
            details={"reason": "provider_error"},
        )
        return _json_response(
            status_code=500,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )


@router.get("/universal/tasks/{task_id}")
async def universal_task_status(
    task_id: str,
    provider: str = "agent_bridge",
    auth_profile: Optional[str] = None,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "read")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    preferred_profile = (auth_profile or x_auth_profile or "").strip() or None
    try:
        resolved, adapter = _resolve_agent_bridge_provider(
            provider=provider,
            preferred_profile=preferred_profile,
        )
    except ValueError as exc:
        return _provider_resolution_universal_response(exc)

    owner_denied = await _enforce_task_owner(
        principal=principal,
        provider_id=resolved.provider_id,
        task_id=task_id,
    )
    if owner_denied is not None:
        return _json_response(status_code=_owner_error_status(owner_denied), content=owner_denied)

    try:
        response_data = await adapter.get_task(task_id)
        normalized_task_id = str(response_data.get("id") or task_id)
        return _universal_task_response(
            operation="status",
            resolved=resolved,
            task_id=normalized_task_id,
            payload=response_data,
        )
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            status_code=exc.response.status_code,
            content=_universal_error(
                f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
                code="upstream_http_error",
            ),
        )
    except Exception as exc:
        logging.error(
            "Universal task status failed for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=_universal_error(
                f"Provider '{resolved.provider_id}' task status failed.",
                code="provider_error",
            ),
        )


@router.post("/universal/tasks/{task_id}/approve")
async def universal_task_approve(
    task_id: str,
    request: UniversalTaskApproveRequest,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "write")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    request_body = request.model_dump(by_alias=True, exclude_none=True)
    token, replay_response = await _maybe_replay_idempotent(
        key=x_idempotency_key,
        operation="task_approve",
        route_task_id=task_id,
        actor=principal.subject,
        payload=request_body,
    )
    if replay_response is not None:
        return replay_response

    preferred_profile = (request.auth_profile or x_auth_profile or "").strip() or None
    try:
        resolved, adapter = _resolve_agent_bridge_provider(
            provider=request.provider,
            preferred_profile=preferred_profile,
        )
    except ValueError as exc:
        status_code, content = _provider_resolution_universal_payload(exc)
        await _store_idempotent(token, status_code=status_code, content=content)
        await _append_task_action(
            provider_id=request.provider,
            task_id=task_id,
            action="approve",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=status_code,
            details={"reason": "provider_resolution_failed"},
        )
        return _json_response(
            status_code=status_code,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    owner_denied = await _enforce_task_owner(
        principal=principal,
        provider_id=resolved.provider_id,
        task_id=task_id,
    )
    if owner_denied is not None:
        owner_status = _owner_error_status(owner_denied)
        await _store_idempotent(token, status_code=owner_status, content=owner_denied)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="approve",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=owner_status,
            details={"reason": owner_denied.get("error", {}).get("code", "owner_denied")},
        )
        return _json_response(
            status_code=owner_status,
            content=owner_denied,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    contract_denied = await _enforce_mutating_task_contract(adapter)
    if contract_denied is not None:
        await _store_idempotent(token, status_code=409, content=contract_denied)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="approve",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=409,
            details={"reason": "contract_mismatch"},
        )
        return _json_response(
            status_code=409,
            content=contract_denied,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    try:
        response_data = await adapter.approve_task(
            task_id=task_id,
            approval_id=request.approval_id,
            approved=request.approved,
        )
        normalized_task_id = str(response_data.get("id") or task_id)
        content = _universal_task_response(
            operation="approve",
            resolved=resolved,
            task_id=normalized_task_id,
            payload=response_data,
        )
        await _store_idempotent(token, status_code=200, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=normalized_task_id,
            action="approve",
            subject=principal.subject,
            outcome="applied",
            idempotency_key=x_idempotency_key,
            status_code=200,
        )
        return _json_response(
            status_code=200,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        content = _universal_error(
            f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
            code="upstream_http_error",
        )
        await _store_idempotent(token, status_code=status_code, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="approve",
            subject=principal.subject,
            outcome="failed",
            idempotency_key=x_idempotency_key,
            status_code=status_code,
            details={"reason": "upstream_http_error"},
        )
        return _json_response(
            status_code=status_code,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )
    except Exception as exc:
        logging.error(
            "Universal task approve failed for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        content = _universal_error(
            f"Provider '{resolved.provider_id}' task approve failed.",
            code="provider_error",
        )
        await _store_idempotent(token, status_code=500, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="approve",
            subject=principal.subject,
            outcome="failed",
            idempotency_key=x_idempotency_key,
            status_code=500,
            details={"reason": "provider_error"},
        )
        return _json_response(
            status_code=500,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )


@router.post("/universal/tasks/{task_id}/retry")
async def universal_task_retry(
    task_id: str,
    request: UniversalTaskRetryRequest,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "write")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    request_body = request.model_dump(by_alias=True, exclude_none=True)
    token, replay_response = await _maybe_replay_idempotent(
        key=x_idempotency_key,
        operation="task_retry",
        route_task_id=task_id,
        actor=principal.subject,
        payload=request_body,
    )
    if replay_response is not None:
        return replay_response

    preferred_profile = (request.auth_profile or x_auth_profile or "").strip() or None
    try:
        resolved, adapter = _resolve_agent_bridge_provider(
            provider=request.provider,
            preferred_profile=preferred_profile,
        )
    except ValueError as exc:
        status_code, content = _provider_resolution_universal_payload(exc)
        await _store_idempotent(token, status_code=status_code, content=content)
        await _append_task_action(
            provider_id=request.provider,
            task_id=task_id,
            action="retry",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=status_code,
            details={"reason": "provider_resolution_failed"},
        )
        return _json_response(
            status_code=status_code,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    owner_denied = await _enforce_task_owner(
        principal=principal,
        provider_id=resolved.provider_id,
        task_id=task_id,
    )
    if owner_denied is not None:
        owner_status = _owner_error_status(owner_denied)
        await _store_idempotent(token, status_code=owner_status, content=owner_denied)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="retry",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=owner_status,
            details={"reason": owner_denied.get("error", {}).get("code", "owner_denied")},
        )
        return _json_response(
            status_code=owner_status,
            content=owner_denied,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    contract_denied = await _enforce_mutating_task_contract(adapter)
    if contract_denied is not None:
        await _store_idempotent(token, status_code=409, content=contract_denied)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="retry",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=409,
            details={"reason": "contract_mismatch"},
        )
        return _json_response(
            status_code=409,
            content=contract_denied,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    try:
        response_data = await adapter.retry_task(task_id=task_id, operator=request.operator)
        normalized_task_id = str(response_data.get("id") or task_id)
        content = _universal_task_response(
            operation="retry",
            resolved=resolved,
            task_id=normalized_task_id,
            payload=response_data,
        )
        await _store_idempotent(token, status_code=200, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=normalized_task_id,
            action="retry",
            subject=principal.subject,
            outcome="applied",
            idempotency_key=x_idempotency_key,
            status_code=200,
        )
        return _json_response(
            status_code=200,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        content = _universal_error(
            f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
            code="upstream_http_error",
        )
        await _store_idempotent(token, status_code=status_code, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="retry",
            subject=principal.subject,
            outcome="failed",
            idempotency_key=x_idempotency_key,
            status_code=status_code,
            details={"reason": "upstream_http_error"},
        )
        return _json_response(
            status_code=status_code,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )
    except Exception as exc:
        logging.error(
            "Universal task retry failed for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        content = _universal_error(
            f"Provider '{resolved.provider_id}' task retry failed.",
            code="provider_error",
        )
        await _store_idempotent(token, status_code=500, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="retry",
            subject=principal.subject,
            outcome="failed",
            idempotency_key=x_idempotency_key,
            status_code=500,
            details={"reason": "provider_error"},
        )
        return _json_response(
            status_code=500,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )


@router.post("/universal/tasks/{task_id}/cancel")
async def universal_task_cancel(
    task_id: str,
    request: UniversalTaskCancelRequest,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "write")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    request_body = request.model_dump(by_alias=True, exclude_none=True)
    token, replay_response = await _maybe_replay_idempotent(
        key=x_idempotency_key,
        operation="task_cancel",
        route_task_id=task_id,
        actor=principal.subject,
        payload=request_body,
    )
    if replay_response is not None:
        return replay_response

    preferred_profile = (request.auth_profile or x_auth_profile or "").strip() or None
    try:
        resolved, adapter = _resolve_agent_bridge_provider(
            provider=request.provider,
            preferred_profile=preferred_profile,
        )
    except ValueError as exc:
        status_code, content = _provider_resolution_universal_payload(exc)
        await _store_idempotent(token, status_code=status_code, content=content)
        await _append_task_action(
            provider_id=request.provider,
            task_id=task_id,
            action="cancel",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=status_code,
            details={"reason": "provider_resolution_failed"},
        )
        return _json_response(
            status_code=status_code,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    owner_denied = await _enforce_task_owner(
        principal=principal,
        provider_id=resolved.provider_id,
        task_id=task_id,
    )
    if owner_denied is not None:
        owner_status = _owner_error_status(owner_denied)
        await _store_idempotent(token, status_code=owner_status, content=owner_denied)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="cancel",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=owner_status,
            details={"reason": owner_denied.get("error", {}).get("code", "owner_denied")},
        )
        return _json_response(
            status_code=owner_status,
            content=owner_denied,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    contract_denied = await _enforce_mutating_task_contract(adapter)
    if contract_denied is not None:
        await _store_idempotent(token, status_code=409, content=contract_denied)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="cancel",
            subject=principal.subject,
            outcome="rejected",
            idempotency_key=x_idempotency_key,
            status_code=409,
            details={"reason": "contract_mismatch"},
        )
        return _json_response(
            status_code=409,
            content=contract_denied,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )

    try:
        response_data = await adapter.cancel_task(task_id=task_id)
        normalized_task_id = str(response_data.get("id") or task_id)
        content = _universal_task_response(
            operation="cancel",
            resolved=resolved,
            task_id=normalized_task_id,
            payload=response_data,
        )
        await _store_idempotent(token, status_code=200, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=normalized_task_id,
            action="cancel",
            subject=principal.subject,
            outcome="applied",
            idempotency_key=x_idempotency_key,
            status_code=200,
        )
        return _json_response(
            status_code=200,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        content = _universal_error(
            f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
            code="upstream_http_error",
        )
        await _store_idempotent(token, status_code=status_code, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="cancel",
            subject=principal.subject,
            outcome="failed",
            idempotency_key=x_idempotency_key,
            status_code=status_code,
            details={"reason": "upstream_http_error"},
        )
        return _json_response(
            status_code=status_code,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )
    except Exception as exc:
        logging.error(
            "Universal task cancel failed for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        content = _universal_error(
            f"Provider '{resolved.provider_id}' task cancel failed.",
            code="provider_error",
        )
        await _store_idempotent(token, status_code=500, content=content)
        await _append_task_action(
            provider_id=resolved.provider_id,
            task_id=task_id,
            action="cancel",
            subject=principal.subject,
            outcome="failed",
            idempotency_key=x_idempotency_key,
            status_code=500,
            details={"reason": "provider_error"},
        )
        return _json_response(
            status_code=500,
            content=content,
            idempotency_key=x_idempotency_key,
            replay=False if token is not None else None,
        )


@router.get("/universal/tasks", response_model=UniversalTaskListResponse)
async def universal_task_list(
    provider: str = "agent_bridge",
    auth_profile: Optional[str] = None,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "read")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    preferred_profile = (auth_profile or x_auth_profile or "").strip() or None
    try:
        resolved, adapter = _resolve_agent_bridge_provider(
            provider=provider,
            preferred_profile=preferred_profile,
        )
    except ValueError as exc:
        return _provider_resolution_universal_response(exc)

    try:
        control_state = _get_control_state_store()
    except Exception as exc:
        logging.error("Durable control-state unavailable: %s", exc, exc_info=True)
        return _json_response(
            status_code=503,
            content=_universal_error(
                "Durable control-state store is unavailable.",
                code="persistence_unavailable",
            ),
        )

    try:
        tasks = await adapter.list_tasks()
        normalized_tasks: List[Dict[str, Any]] = []
        for task in tasks:
            item = dict(task)
            normalized_task_id = str(item.get("id") or item.get("taskId") or "").strip()
            if not principal.is_admin:
                if not normalized_task_id:
                    continue
                owner = await control_state.get_task_owner(
                    provider_id=resolved.provider_id,
                    task_id=normalized_task_id,
                )
                if owner != principal.subject:
                    continue
            item["normalizedState"] = _normalize_task_state(item)
            normalized_tasks.append(item)
        return {
            "object": "universal.task.list",
            "provider": resolved.provider_id,
            "model": resolved.model_id,
            "profile": resolved.profile_id,
            "auth_source": resolved.auth_source,
            "tasks": normalized_tasks,
        }
    except httpx.HTTPStatusError as exc:
        return _json_response(
            status_code=exc.response.status_code,
            content=_universal_error(
                f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
                code="upstream_http_error",
            ),
        )
    except Exception as exc:
        logging.error(
            "Universal task list failed for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        return _json_response(
            status_code=500,
            content=_universal_error(
                f"Provider '{resolved.provider_id}' task list failed.",
                code="provider_error",
            ),
        )


@router.get("/universal/tasks/{task_id}/events", response_model=UniversalTaskEventListResponse)
async def universal_task_events(
    task_id: str,
    limit: int = 200,
    provider: str = "agent_bridge",
    auth_profile: Optional[str] = None,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "read")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    preferred_profile = (auth_profile or x_auth_profile or "").strip() or None
    try:
        resolved, adapter = _resolve_agent_bridge_provider(
            provider=provider,
            preferred_profile=preferred_profile,
        )
    except ValueError as exc:
        return _provider_resolution_universal_response(exc)

    owner_denied = await _enforce_task_owner(
        principal=principal,
        provider_id=resolved.provider_id,
        task_id=task_id,
    )
    if owner_denied is not None:
        return _json_response(status_code=_owner_error_status(owner_denied), content=owner_denied)

    try:
        events = await adapter.get_task_events(task_id, limit=limit)
        normalized_events: List[Dict[str, Any]] = []
        for event in events:
            item = dict(event)
            if "normalizedState" not in item:
                item["normalizedState"] = _normalize_task_state(item)
            normalized_events.append(item)
        return {
            "object": "universal.task.events",
            "provider": resolved.provider_id,
            "model": resolved.model_id,
            "profile": resolved.profile_id,
            "auth_source": resolved.auth_source,
            "task_id": task_id,
            "events": normalized_events,
        }
    except httpx.HTTPStatusError as exc:
        return _json_response(
            status_code=exc.response.status_code,
            content=_universal_error(
                f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
                code="upstream_http_error",
            ),
        )
    except Exception as exc:
        logging.error(
            "Universal task events failed for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        return _json_response(
            status_code=500,
            content=_universal_error(
                f"Provider '{resolved.provider_id}' task events failed.",
                code="provider_error",
            ),
        )


@router.post("/universal/tasks/{task_id}/wait")
async def universal_task_wait(
    task_id: str,
    request: UniversalTaskWaitRequest,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "read")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    preferred_profile = (request.auth_profile or x_auth_profile or "").strip() or None
    timeout_ms = max(1000, min(120000, int(request.timeout_ms)))
    poll_ms = max(200, min(5000, int(request.poll_ms)))

    try:
        resolved, adapter = _resolve_agent_bridge_provider(
            provider=request.provider,
            preferred_profile=preferred_profile,
        )
    except ValueError as exc:
        return _provider_resolution_universal_response(exc)

    owner_denied = await _enforce_task_owner(
        principal=principal,
        provider_id=resolved.provider_id,
        task_id=task_id,
    )
    if owner_denied is not None:
        return _json_response(status_code=_owner_error_status(owner_denied), content=owner_denied)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + (timeout_ms / 1000.0)
    last_response: Dict[str, Any] = {}
    last_state = "unknown"

    try:
        while True:
            response_data = await adapter.get_task(task_id)
            last_response = dict(response_data)
            last_state = _normalize_task_state(last_response)
            last_response["normalizedState"] = last_state
            if _is_terminal_task_state(last_state):
                return {
                    "object": "universal.task.wait",
                    "provider": resolved.provider_id,
                    "model": resolved.model_id,
                    "profile": resolved.profile_id,
                    "auth_source": resolved.auth_source,
                    "task_id": task_id,
                    "timed_out": False,
                    "state": last_state,
                    "response": last_response,
                }

            now = loop.time()
            if now >= deadline:
                break
            remaining = deadline - now
            await asyncio.sleep(min(poll_ms / 1000.0, remaining))
    except httpx.HTTPStatusError as exc:
        return _json_response(
            status_code=exc.response.status_code,
            content=_universal_error(
                f"Upstream provider '{resolved.provider_id}' returned HTTP {exc.response.status_code}",
                code="upstream_http_error",
            ),
        )
    except Exception as exc:
        logging.error(
            "Universal task wait failed for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        return _json_response(
            status_code=500,
            content=_universal_error(
                f"Provider '{resolved.provider_id}' task wait failed.",
                code="provider_error",
            ),
        )

    timeout_payload = {
        "object": "universal.task.wait",
        "provider": resolved.provider_id,
        "model": resolved.model_id,
        "profile": resolved.profile_id,
        "auth_source": resolved.auth_source,
        "task_id": task_id,
        "timed_out": True,
        "state": last_state,
        "response": last_response or None,
    }
    return _json_response(
        status_code=408,
        content=timeout_payload,
    )


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "write")
    if denied is not None:
        return JSONResponse(status_code=403, content=denied)

    payload = request.model_dump(exclude_none=True, by_alias=True)
    messages_dump = _to_message_dicts(request.messages)

    try:
        resolved = manager.resolve(request.model, preferred_profile=x_auth_profile)
    except ValueError as exc:
        return _provider_resolution_openai_response(exc)

    prompt_text = "\n".join(str(m.get("content", "")) for m in messages_dump)
    estimated_prompt_tokens = _estimate_tokens_from_text(prompt_text)

    if payload.get("stream"):
        try:
            stream_iter, active_provider_id = await _execute_stream_with_fallbacks(
                resolved=resolved,
                messages_dump=messages_dump,
                payload=payload,
            )
            _record_usage(
                resolved=resolved,
                endpoint="chat.completions.stream",
                source="openai_compat",
                prompt_tokens=estimated_prompt_tokens,
            )
            return StreamingResponse(
                _wrap_sse_stream(stream_iter, provider_id=active_provider_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        except httpx.HTTPStatusError as exc:
            return JSONResponse(
                status_code=exc.response.status_code,
                content=_safe_upstream_http_error(
                    resolved.provider_id,
                    exc.response.status_code,
                ),
            )
        except Exception as exc:
            logging.error(
                "Error starting stream for provider '%s': %s",
                resolved.provider_id,
                exc,
                exc_info=True,
            )
            return JSONResponse(
                status_code=500,
                content=_openai_error(
                    f"Provider '{resolved.provider_id}' stream start failed.",
                    code="provider_error",
                    error_type="api_error",
                ),
            )

    start = time.perf_counter()
    try:
        response_data = await _execute_non_stream_with_fallbacks(
            resolved=resolved,
            messages_dump=messages_dump,
            payload=payload,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        telemetry_meta = _pop_response_telemetry(response_data)
        usage = _extract_usage_from_response(response_data)
        _record_usage(
            resolved=resolved,
            endpoint="chat.completions",
            source="openai_compat",
            prompt_tokens=usage.get("prompt_tokens", estimated_prompt_tokens),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", None),
            latency_ms=latency_ms,
            meta=telemetry_meta,
        )
        return response_data
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            status_code=exc.response.status_code,
            content=_safe_upstream_http_error(
                resolved.provider_id,
                exc.response.status_code,
            ),
        )
    except Exception as exc:
        logging.error(
            "Error calling provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=_openai_error(
                f"Provider '{resolved.provider_id}' call failed.",
                code="provider_error",
                error_type="api_error",
            ),
        )


@router.post("/embeddings")
async def embeddings(
    request: EmbeddingRequest,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "write")
    if denied is not None:
        return JSONResponse(status_code=403, content=denied)

    raw_input = request.input
    if isinstance(raw_input, str):
        input_texts = [raw_input]
    elif isinstance(raw_input, list) and all(isinstance(item, str) for item in raw_input):
        input_texts = [item for item in raw_input if item]
    else:
        input_texts = []

    if not input_texts:
        return JSONResponse(
            status_code=400,
            content=_openai_error(
                "Embedding input must be a non-empty string or string array.",
                code="invalid_request",
            ),
        )

    try:
        resolved = manager.resolve(request.model, preferred_profile=x_auth_profile)
    except ValueError as exc:
        return _provider_resolution_openai_response(exc)

    payload = request.model_dump(
        exclude_none=True,
        by_alias=True,
        exclude={"model", "input"},
    )

    estimated_prompt_tokens = sum(_estimate_tokens_from_text(t) for t in input_texts)
    start = time.perf_counter()
    try:
        response_data = await _execute_embeddings_with_fallbacks(
            resolved=resolved,
            input_texts=input_texts,
            payload=payload,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        telemetry_meta = _pop_response_telemetry(response_data)
        usage = _extract_usage_from_response(response_data)
        _record_usage(
            resolved=resolved,
            endpoint="embeddings",
            source="openai_compat",
            prompt_tokens=usage.get("prompt_tokens", estimated_prompt_tokens),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", None),
            latency_ms=latency_ms,
            meta=telemetry_meta,
        )
        return response_data
    except NotImplementedError as exc:
        return JSONResponse(
            status_code=400,
            content=_openai_error(str(exc), code="embeddings_not_supported"),
        )
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            status_code=exc.response.status_code,
            content=_safe_upstream_http_error(
                resolved.provider_id,
                exc.response.status_code,
            ),
        )
    except Exception as exc:
        logging.error(
            "Error creating embeddings for provider '%s': %s",
            resolved.provider_id,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=_openai_error(
                f"Provider '{resolved.provider_id}' embeddings failed.",
                code="provider_error",
                error_type="api_error",
            ),
        )


@router.get("/models", response_model=ModelList)
async def list_models(principal: Principal = Depends(verify_server_token)) -> ModelList:
    denied = _enforce_scope(principal, "read")
    if denied is not None:
        return _json_response(status_code=403, content=denied)
    models = await manager.list_models()
    return ModelList(data=models)


@router.get("/universal/contract/status")
async def universal_contract_status(
    provider: str = "agent_bridge",
    auth_profile: Optional[str] = None,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    principal: Principal = Depends(verify_server_token),
):
    denied = _enforce_scope(principal, "read")
    if denied is not None:
        return _json_response(status_code=403, content=denied)

    preferred_profile = (auth_profile or x_auth_profile or "").strip() or None
    try:
        resolved, adapter = _resolve_agent_bridge_provider(
            provider=provider,
            preferred_profile=preferred_profile,
        )
    except ValueError as exc:
        return _provider_resolution_universal_response(exc)

    status = await get_task_contract_status(provider=adapter, cfg=load_config())
    return {
        "object": "universal.contract.status",
        "provider": resolved.provider_id,
        "model": resolved.model_id,
        "profile": resolved.profile_id,
        "auth_source": resolved.auth_source,
        "status": status,
    }
