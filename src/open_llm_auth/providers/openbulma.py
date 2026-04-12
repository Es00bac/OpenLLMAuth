from __future__ import annotations

import asyncio
import json
from uuid import uuid4
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .base import BaseProvider


class OpenBulmaProvider(BaseProvider):
    """
    Bridge a standard gateway request into a live OpenBulma runtime.

    Important behavioral mismatch: OpenBulma chat is transport-level single-turn,
    while callers coming through this gateway often expect transcript continuity.
    This adapter rebuilds a bounded continuity block before forwarding chat and
    synthesizes task streaming by polling OpenBulma task state/events.
    """
    CONTRACT_HEADER_VERSION = "1.0"
    GATEWAY_VERSION = "open_llm_auth/1.0"
    CHAT_CONTEXT_TURN_LIMIT = 8
    CHAT_CONTEXT_CHARS_PER_TURN = 600
    SYSTEM_PROMPT_CHAR_LIMIT = 4000
    TASK_STREAM_POLL_INTERVAL_SECONDS = 0.75
    TASK_STREAM_EVENT_LIMIT = 200

    def __init__(
        self,
        *,
        provider_id: str,
        api_key: Optional[str] = None,
        base_url: str = "http://127.0.0.1:20100/v1",
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 600.0,
    ):
        super().__init__(
            provider_id=provider_id,
            api_key=api_key,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

    async def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute either a direct chat request or a task dispatch."""
        user_message = self._extract_last_user_message(messages)

        # A structured `task` payload means "use the runtime task API" even if
        # the caller entered through the chat-completions surface.
        task_input = payload.get("task")
        if isinstance(task_input, dict):
            body = dict(task_input)
            if not body.get("objective") and user_message:
                body["objective"] = user_message

            task_data = await self.run_task(body)

            task_id = task_data.get("taskId", "unknown")
            status = task_data.get("status", "queued")
            content = f"OpenBulma task queued: {task_id} (status: {status})"
            if isinstance(task_data.get("result"), str) and task_data["result"].strip():
                content = task_data["result"].strip()

            return {
                "id": f"bulma-task-{task_id}",
                "object": "chat.completion",
                "created": 0,
                "model": f"openbulma/{model}",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": content,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }

        if not user_message:
            raise ValueError("No user message found in request.")

        body = self._build_chat_body(model=model, messages=messages, payload=payload)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat",
                json=body,
                headers=self.headers,
            )
            response.raise_for_status()
            bulma_data = response.json()
            
            # Map OpenBulma's chat payload back into an OpenAI-style envelope so
            # ordinary clients can consume the bridge without provider-specific parsing.
            content = bulma_data.get("reply", bulma_data.get("rawText", ""))
            
            return {
                "id": f"bulma-{bulma_data.get('taskId', 'chat')}",
                "object": "chat.completion",
                "created": 0,
                "model": f"openbulma/{model}",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": content,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": bulma_data.get("usage") or {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                }
            }

    async def chat_completion_stream(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Return an OpenAI-style stream for chat or task execution."""
        # Streaming task output is emulated by polling task state and events.
        # Direct OpenBulma chat itself is still request/response.
        if (
            isinstance(payload.get("task"), dict)
            or model.startswith("assistant")
            or model.startswith("agent")
            or model.endswith("assistant")
        ):
            return await self._run_task_stream(model, messages, payload)

        # Non-task chat returns a single synthetic SSE sequence built from the
        # synchronous chat result so OpenAI-compatible clients can still stream.
        res = await self.chat_completion(model=model, messages=messages, payload=payload)

        async def _single_chunk_stream():
            yield self._openai_chunk(
                chunk_id=res["id"],
                model=res["model"],
                delta={"role": "assistant"},
                finish_reason=None,
            )
            yield self._openai_chunk(
                chunk_id=res["id"],
                model=res["model"],
                delta={"content": res["choices"][0]["message"]["content"]},
                finish_reason=None,
            )
            yield self._openai_chunk(
                chunk_id=res["id"],
                model=res["model"],
                delta={},
                finish_reason="stop",
            )
            yield b"data: [DONE]\n\n"

        return _single_chunk_stream()

    async def _run_task_stream(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        user_message = self._extract_last_user_message(messages)
        task_input = payload.get("task")
        if isinstance(task_input, dict):
            body = dict(task_input)
            if not body.get("objective") and user_message:
                body["objective"] = user_message
        else:
            body = {"objective": user_message}

        task_data = await self.run_task(body)
        task_id = task_data.get("taskId")
        chunk_id = f"bulma-task-{task_id or uuid4().hex}"
        stream_model = f"openbulma/{model}"

        async def _poll_task():
            yield self._openai_chunk(
                chunk_id=chunk_id,
                model=stream_model,
                delta={"role": "assistant"},
                finish_reason=None,
            )
            yield self._openai_chunk(
                chunk_id=chunk_id,
                model=stream_model,
                delta={
                    "content": self._format_task_start_message(
                        task_id=task_id,
                        task_data=task_data,
                        objective=body.get("objective", user_message),
                    )
                },
                finish_reason=None,
            )

            if not isinstance(task_id, str) or not task_id.strip():
                yield self._openai_chunk(
                    chunk_id=chunk_id,
                    model=stream_model,
                    delta={},
                    finish_reason="stop",
                )
                yield b"data: [DONE]\n\n"
                return

            seen_event_keys: set[str] = set()
            last_snapshot_signature: Optional[str] = None
            terminal_statuses = {"done", "failed", "canceled", "escalated"}

            while True:
                task = await self.get_task(task_id)
                events = await self.get_task_events(
                    task_id,
                    limit=self.TASK_STREAM_EVENT_LIMIT,
                )

                for event in events:
                    event_key = self._task_event_key(event)
                    if event_key in seen_event_keys:
                        continue
                    seen_event_keys.add(event_key)
                    content = self._format_task_event(event)
                    if not content:
                        continue
                    yield self._openai_chunk(
                        chunk_id=chunk_id,
                        model=stream_model,
                        delta={"content": content},
                        finish_reason=None,
                    )

                snapshot_signature = self._task_snapshot_signature(task)
                if snapshot_signature != last_snapshot_signature:
                    last_snapshot_signature = snapshot_signature
                    snapshot_content = self._format_task_snapshot(task)
                    if snapshot_content:
                        yield self._openai_chunk(
                            chunk_id=chunk_id,
                            model=stream_model,
                            delta={"content": snapshot_content},
                            finish_reason=None,
                        )

                if str(task.get("status") or "").lower() in terminal_statuses:
                    break
                await asyncio.sleep(self.TASK_STREAM_POLL_INTERVAL_SECONDS)

            yield self._openai_chunk(
                chunk_id=chunk_id,
                model=stream_model,
                delta={},
                finish_reason="stop",
            )
            yield b"data: [DONE]\n\n"

        return _poll_task()

    async def list_models(self) -> List[Dict[str, Any]]:
        return [
            {"id": "bulma", "name": "OpenBulma Agent"},
            {"id": "assistant", "name": "OpenBulma Assistant (BAA)"},
        ]

    async def run_task(self, task_input: Dict[str, Any]) -> Dict[str, Any]:
        return await self._post_json(
            "/agent/runTask",
            task_input,
            extra_headers=self._contract_headers(),
        )

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        return await self._get_json(f"/agent/status/{task_id}")

    async def retry_task(self, task_id: str, operator: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if isinstance(operator, str) and operator.strip():
            payload["operator"] = operator.strip()
        return await self._post_json(
            f"/agent/tasks/{task_id}/retry",
            payload,
            extra_headers=self._contract_headers(),
        )

    async def approve_task(
        self,
        task_id: str,
        approval_id: str,
        approved: bool = True,
    ) -> Dict[str, Any]:
        return await self._post_json(
            f"/agent/tasks/{task_id}/approve",
            {"approvalId": approval_id, "approved": bool(approved)},
            extra_headers=self._contract_headers(),
        )

    async def cancel_task(self, task_id: str) -> Dict[str, Any]:
        return await self._post_json(
            f"/agent/cancel/{task_id}",
            {},
            extra_headers=self._contract_headers(),
        )

    async def list_tasks(self) -> List[Dict[str, Any]]:
        data = await self._get_json("/agent/tasks")
        tasks = data.get("tasks")
        if isinstance(tasks, list):
            return [t for t in tasks if isinstance(t, dict)]
        return []

    async def get_task_events(self, task_id: str, *, limit: int = 200) -> List[Dict[str, Any]]:
        clamped = max(1, min(2000, int(limit)))
        data = await self._get_json(f"/agent/task-log/{task_id}?limit={clamped}")
        events = data.get("events")
        if isinstance(events, list):
            return [e for e in events if isinstance(e, dict)]
        return []

    async def get_task_contract(self) -> Dict[str, Any]:
        return await self._get_json("/agent/contract")

    def _contract_headers(self) -> Dict[str, str]:
        return {
            "X-Provider-Contract-Version": self.CONTRACT_HEADER_VERSION,
            "X-Gateway-Version": self.GATEWAY_VERSION,
            "X-Request-Id": f"req-{uuid4().hex}",
        }

    @classmethod
    def _extract_last_user_message(cls, messages: List[Dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            text = cls._message_text(msg)
            if text:
                return text
        return ""

    @classmethod
    def _build_chat_body(
        cls,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        user_message = cls._extract_last_user_message(messages)
        if not user_message:
            raise ValueError("No user message found in request.")

        body: Dict[str, Any] = {"message": user_message}
        system_prompt = cls._build_gateway_system_prompt(messages)
        if system_prompt:
            body["system"] = system_prompt

        if isinstance(payload.get("temperature"), (int, float)):
            body["temperature"] = float(payload["temperature"])
        if isinstance(payload.get("authProfile"), str) and payload["authProfile"].strip():
            body["authProfile"] = payload["authProfile"].strip()
        if isinstance(payload.get("modelProfile"), str) and payload["modelProfile"].strip():
            body["modelProfile"] = payload["modelProfile"].strip()
        elif isinstance(model, str) and model.strip() and model not in {"bulma", "assistant"}:
            body["modelProfile"] = model.strip()

        return body

    @classmethod
    def _build_gateway_system_prompt(cls, messages: List[Dict[str, Any]]) -> str:
        """Compress prior turns into a bounded continuity block for `/chat`."""
        if not isinstance(messages, list) or not messages:
            return ""

        explicit_system_parts: List[str] = []
        conversational_turns: List[str] = []
        last_user_index = max(
            (index for index, msg in enumerate(messages) if msg.get("role") == "user" and cls._message_text(msg)),
            default=-1,
        )

        for index, message in enumerate(messages):
            role = str(message.get("role") or "").strip().lower()
            text = cls._message_text(message)
            if not text:
                continue
            if role in {"system", "developer"}:
                explicit_system_parts.append(text)
                continue
            if role == "user" and index == last_user_index:
                continue
            conversational_turns.append(
                f"{role or 'message'}: {cls._truncate_text(text, cls.CHAT_CONTEXT_CHARS_PER_TURN)}"
            )

        recent_turns = conversational_turns[-cls.CHAT_CONTEXT_TURN_LIMIT :]
        system_blocks = [part.strip() for part in explicit_system_parts if part.strip()]

        # OpenBulma's direct chat API is single-turn. The gateway rebuilds a bounded
        # continuity block from the upstream transcript so external surfaces do not
        # silently lose context when they traverse open_llm_auth.
        if recent_turns:
            system_blocks.append(
                "\n".join(
                    [
                        "Gateway conversation context:",
                        "Treat the following as recent transcript context carried across the gateway.",
                        "The current user message is provided separately in the request body and should be answered as the active turn.",
                        *recent_turns,
                    ]
                )
            )

        combined = "\n\n".join(block for block in system_blocks if block).strip()
        return cls._truncate_text(combined, cls.SYSTEM_PROMPT_CHAR_LIMIT) if combined else ""

    @staticmethod
    def _message_text(message: Dict[str, Any]) -> str:
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                return text.strip()
            return ""
        if isinstance(content, list):
            texts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    stripped = item.strip()
                    if stripped:
                        texts.append(stripped)
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").lower()
                text = item.get("text")
                if item_type in {"text", "input_text", "output_text"} and isinstance(text, str):
                    stripped = text.strip()
                    if stripped:
                        texts.append(stripped)
                    continue
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
            return "\n".join(texts).strip()
        return ""

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 3)].rstrip() + "..."

    @classmethod
    def _format_task_start_message(
        cls,
        *,
        task_id: Any,
        task_data: Dict[str, Any],
        objective: str,
    ) -> str:
        status = str(task_data.get("status") or "queued")
        created_at = task_data.get("createdAt")
        dispatch_id = task_data.get("dispatchId")
        lines = [
            f"Queued Bulma task {task_id or 'unknown'} (status: {status}).",
            f"Objective: {cls._truncate_text(objective, 800)}",
        ]
        if created_at is not None:
            lines.append(f"Created at: {created_at}")
        if isinstance(dispatch_id, str) and dispatch_id.strip():
            lines.append(f"Dispatch: {dispatch_id.strip()}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _task_event_key(event: Dict[str, Any]) -> str:
        ts = str(event.get("ts") or "")
        note = str(event.get("note") or "")
        task = event.get("task")
        updated_at = ""
        if isinstance(task, dict):
            updated_at = str(task.get("updatedAtMs") or "")
        return "|".join((ts, note, updated_at))

    @classmethod
    def _task_snapshot_signature(cls, task: Dict[str, Any]) -> str:
        stage_error = task.get("stageErrorContext")
        error_signature = ""
        if isinstance(stage_error, dict):
            error_signature = "|".join(
                str(stage_error.get(key) or "")
                for key in ("errorType", "errorMessage", "approvalId")
            )
        return "|".join(
            str(task.get(key) or "")
            for key in (
                "status",
                "currentStage",
                "phase",
                "summary",
                "statusReason",
                "completionState",
                "clarificationQuestion",
                "blockedDurationMs",
            )
        ) + "|" + error_signature

    @classmethod
    def _format_task_event(cls, event: Dict[str, Any]) -> str:
        note = str(event.get("note") or "").strip()
        task = event.get("task")
        if not note or not isinstance(task, dict):
            return ""

        phase = str(task.get("phase") or "").strip()
        current_stage = str(task.get("currentStage") or "").strip()
        summary = cls._truncate_text(str(task.get("summary") or "").strip(), 240)

        if note == "created":
            return f"Task created. Current stage: {current_stage or 'queued'}.\n"
        if note == "running":
            return f"Task is now running in stage {current_stage or 'planning'}.\n"
        if note.startswith("phase_"):
            label = note.replace("phase_", "").replace("_", " ").strip() or phase or "unknown"
            if summary:
                return f"Phase update: {label}. Summary: {summary}\n"
            return f"Phase update: {label}.\n"
        if note == "checkpoint_created":
            return "Checkpoint created.\n"
        if note == "verification_failed":
            return f"Verification failed. Summary: {summary or 'Verification did not pass.'}\n"
        if note == "iterate_next_attempt":
            return f"Retrying with another attempt. Summary: {summary or 'Preparing next attempt.'}\n"
        if note in {"done", "failed", "canceled", "escalated"}:
            return cls._format_task_snapshot(task)

        pretty = note.replace("_", " ").strip()
        if summary:
            return f"{pretty.capitalize()}. Summary: {summary}\n"
        return f"{pretty.capitalize()}.\n"

    @classmethod
    def _format_task_snapshot(cls, task: Dict[str, Any]) -> str:
        status = str(task.get("status") or "unknown").strip()
        stage = str(task.get("currentStage") or "").strip()
        phase = str(task.get("phase") or "").strip()
        summary = cls._truncate_text(str(task.get("summary") or "").strip(), 320)
        status_reason = cls._truncate_text(str(task.get("statusReason") or "").strip(), 220)
        completion_state = str(task.get("completionState") or "").strip()
        clarification_question = cls._truncate_text(str(task.get("clarificationQuestion") or "").strip(), 280)
        blocked_duration_ms = task.get("blockedDurationMs")
        stage_error = task.get("stageErrorContext")

        lines = [f"Task status: {status}."]
        descriptors = [part for part in (stage and f"stage {stage}", phase and f"phase {phase}") if part]
        if descriptors:
            lines.append(f"Position: {', '.join(descriptors)}.")
        if summary and summary.lower() not in {"queued", status.lower()}:
            lines.append(f"Summary: {summary}")
        if status_reason:
            lines.append(f"Reason: {status_reason}")
        if completion_state:
            lines.append(f"Completion state: {completion_state}")
        if clarification_question and stage == "needs_clarification":
            lines.append(f"Clarification needed: {clarification_question}")
        if isinstance(blocked_duration_ms, (int, float)) and blocked_duration_ms > 0:
            lines.append(f"Blocked for: {round(float(blocked_duration_ms) / 1000.0, 1)}s")
        if isinstance(stage_error, dict):
            error_type = str(stage_error.get("errorType") or "").strip()
            error_message = cls._truncate_text(str(stage_error.get("errorMessage") or "").strip(), 240)
            approval_id = str(stage_error.get("approvalId") or "").strip()
            if error_type:
                lines.append(f"Stage error type: {error_type}")
            if error_message:
                lines.append(f"Stage error: {error_message}")
            if approval_id:
                lines.append(f"Approval id: {approval_id}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _openai_chunk(
        *,
        chunk_id: str,
        model: str,
        delta: Dict[str, Any],
        finish_reason: Any,
    ) -> bytes:
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        return f"data: {json.dumps(chunk, ensure_ascii=True)}\n\n".encode("utf-8")

    async def _post_json(
        self,
        path: str,
        payload: Dict[str, Any],
        *,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        headers = dict(self.headers)
        if extra_headers:
            headers.update(extra_headers)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    async def _get_json(self, path: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}{path}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()
