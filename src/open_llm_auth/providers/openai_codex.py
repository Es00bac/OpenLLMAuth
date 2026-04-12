from __future__ import annotations

import base64
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .base import BaseProvider, compact_payload


JWT_CLAIM_PATH = "https://api.openai.com/auth"


def _extract_account_id(token: str) -> Optional[str]:
    """Extract chatgpt_account_id from a JWT access token."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.b64decode(payload_b64))
        auth_claim = payload.get(JWT_CLAIM_PATH, {})
        account_id = auth_claim.get("chatgpt_account_id")
        return account_id if isinstance(account_id, str) and account_id else None
    except Exception:
        return None


def _resolve_codex_url(base_url: str) -> str:
    """Resolve the full Codex responses endpoint URL."""
    raw = base_url.rstrip("/") if base_url else "https://chatgpt.com/backend-api"
    if raw.endswith("/codex/responses"):
        return raw
    if raw.endswith("/codex"):
        return f"{raw}/responses"
    return f"{raw}/codex/responses"


def _convert_messages_to_codex(
    messages: List[Dict[str, Any]],
) -> tuple[Optional[str], List[Dict[str, Any]]]:
    """Convert OpenAI chat completions messages to Codex Responses API input format.

    Returns (instructions, input_messages).
    """
    instructions: Optional[str] = None
    codex_input: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role in ("system", "developer"):
            # System messages become instructions
            if isinstance(content, str):
                instructions = content
            elif isinstance(content, list):
                texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                instructions = "\n".join(texts)
            continue

        if role == "user":
            if isinstance(content, str):
                codex_input.append({
                    "role": "user",
                    "content": [{"type": "input_text", "text": content}],
                })
            elif isinstance(content, list):
                parts: List[Dict[str, Any]] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        parts.append({"type": "input_text", "text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        url = item.get("image_url", {})
                        if isinstance(url, dict):
                            parts.append({
                                "type": "input_image",
                                "detail": "auto",
                                "image_url": url.get("url", ""),
                            })
                        elif isinstance(url, str):
                            parts.append({
                                "type": "input_image",
                                "detail": "auto",
                                "image_url": url,
                            })
                if parts:
                    codex_input.append({"role": "user", "content": parts})
            continue

        if role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                # Assistant message with tool calls
                if content:
                    codex_input.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content, "annotations": []}],
                        "status": "completed",
                    })
                for tc in tool_calls:
                    func = tc.get("function", {})
                    codex_input.append({
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", "{}"),
                    })
            elif content:
                codex_input.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content, "annotations": []}],
                    "status": "completed",
                })
            continue

        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            tool_content = content if isinstance(content, str) else json.dumps(content)
            codex_input.append({
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": tool_content,
            })
            continue

    return instructions, codex_input


def _convert_tools_to_codex(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Convert OpenAI chat completions tools to Codex Responses API format."""
    if not tools:
        return None
    result = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func = tool.get("function", {})
        result.append({
            "type": "function",
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "parameters": func.get("parameters", {}),
            "strict": None,
        })
    return result or None


class OpenAICodexProvider(BaseProvider):
    """Provider for OpenAI Codex (ChatGPT Plus/Pro) via the Responses API."""

    def __init__(
        self,
        *,
        provider_id: str,
        api_key: Optional[str],
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        account_id: Optional[str] = None,
    ):
        super().__init__(
            provider_id=provider_id,
            api_key=api_key,
            base_url=base_url,
            headers=headers,
        )
        self.account_id = account_id or (
            _extract_account_id(api_key) if api_key else None
        )

    def _build_codex_headers(self) -> Dict[str, str]:
        headers = {**self.headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.account_id:
            headers["chatgpt-account-id"] = self.account_id
        headers["OpenAI-Beta"] = "responses=experimental"
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "text/event-stream"
        return headers

    def _build_codex_body(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        instructions, codex_input = _convert_messages_to_codex(messages)

        body: Dict[str, Any] = {
            "model": model,
            "store": False,
            "stream": True,
            "input": codex_input,
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }

        body["instructions"] = instructions or ""

        # Note: temperature is not supported by the Codex responses API

        tools = _convert_tools_to_codex(payload.get("tools"))
        if tools:
            body["tools"] = tools

        return body

    async def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        url = _resolve_codex_url(self.base_url)
        headers = self._build_codex_headers()
        body = self._build_codex_body(model=model, messages=messages, payload=payload)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code >= 400:
                    await response.aread()
                    response.raise_for_status()

                return await self._collect_sse_to_completion(response, model)

    async def _collect_sse_to_completion(
        self, response: httpx.Response, model: str,
    ) -> Dict[str, Any]:
        """Collect SSE events and build a chat completions response."""
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        current_tool: Optional[Dict[str, Any]] = None
        usage: Dict[str, Any] = {}
        finish_reason = "stop"

        async for event in self._parse_sse(response):
            event_type = event.get("type", "")

            if event_type == "response.output_item.added":
                item = event.get("item", {})
                if item.get("type") == "function_call":
                    current_tool = {
                        "id": item.get("call_id", ""),
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": "",
                        },
                    }

            elif event_type == "response.output_text.delta":
                text_parts.append(event.get("delta", ""))

            elif event_type == "response.function_call_arguments.delta":
                if current_tool:
                    current_tool["function"]["arguments"] += event.get("delta", "")

            elif event_type == "response.output_item.done":
                item = event.get("item", {})
                if item.get("type") == "function_call" and current_tool:
                    tool_calls.append(current_tool)
                    current_tool = None

            elif event_type == "response.completed":
                resp = event.get("response", {})
                resp_usage = resp.get("usage", {})
                if resp_usage:
                    cached = (resp_usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
                    usage = {
                        "prompt_tokens": resp_usage.get("input_tokens", 0),
                        "completion_tokens": resp_usage.get("output_tokens", 0),
                        "total_tokens": resp_usage.get("total_tokens", 0),
                    }
                status = resp.get("status", "completed")
                if status == "incomplete":
                    finish_reason = "length"
                elif status in ("failed", "cancelled"):
                    finish_reason = "stop"
                if tool_calls:
                    finish_reason = "tool_calls"

            elif event_type == "error":
                msg = event.get("message", "")
                code = event.get("code", "")
                raise httpx.HTTPStatusError(
                    f"Codex error: {msg or code}",
                    request=httpx.Request("POST", _resolve_codex_url(self.base_url)),
                    response=httpx.Response(500),
                )

            elif event_type == "response.failed":
                err_msg = (event.get("response", {}).get("error", {}) or {}).get("message", "Response failed")
                raise httpx.HTTPStatusError(
                    err_msg,
                    request=httpx.Request("POST", _resolve_codex_url(self.base_url)),
                    response=httpx.Response(500),
                )

        message: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts) or None,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {
            "id": f"chatcmpl-codex-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    async def chat_completion_stream(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        url = _resolve_codex_url(self.base_url)
        headers = self._build_codex_headers()
        body = self._build_codex_body(model=model, messages=messages, payload=payload)

        async def _stream() -> AsyncIterator[bytes]:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        response.raise_for_status()

                    chunk_id = f"chatcmpl-codex-{int(time.time())}"
                    created = int(time.time())
                    tool_index = -1
                    current_tool_call_id = ""

                    async for event in self._parse_sse(response):
                        event_type = event.get("type", "")

                        if event_type == "response.output_item.added":
                            item = event.get("item", {})
                            if item.get("type") == "message":
                                # Send role chunk
                                delta: Dict[str, Any] = {"role": "assistant", "content": ""}
                                yield self._sse_chunk(chunk_id, created, model, delta)
                            elif item.get("type") == "function_call":
                                tool_index += 1
                                current_tool_call_id = item.get("call_id", "")
                                delta = {
                                    "tool_calls": [{
                                        "index": tool_index,
                                        "id": current_tool_call_id,
                                        "type": "function",
                                        "function": {
                                            "name": item.get("name", ""),
                                            "arguments": "",
                                        },
                                    }]
                                }
                                yield self._sse_chunk(chunk_id, created, model, delta)

                        elif event_type == "response.output_text.delta":
                            delta = {"content": event.get("delta", "")}
                            yield self._sse_chunk(chunk_id, created, model, delta)

                        elif event_type == "response.function_call_arguments.delta":
                            delta = {
                                "tool_calls": [{
                                    "index": tool_index,
                                    "function": {"arguments": event.get("delta", "")},
                                }]
                            }
                            yield self._sse_chunk(chunk_id, created, model, delta)

                        elif event_type == "response.completed":
                            resp = event.get("response", {})
                            status = resp.get("status", "completed")
                            finish_reason = "stop"
                            if status == "incomplete":
                                finish_reason = "length"
                            if tool_index >= 0:
                                finish_reason = "tool_calls"
                            yield self._sse_chunk(
                                chunk_id, created, model, {}, finish_reason=finish_reason,
                                usage=resp.get("usage"),
                            )
                            yield b"data: [DONE]\n\n"

                        elif event_type == "error":
                            msg = event.get("message", "Unknown error")
                            error_data = {
                                "error": {"message": msg, "type": "server_error", "code": event.get("code", "")}
                            }
                            yield f"data: {json.dumps(error_data)}\n\n".encode()
                            yield b"data: [DONE]\n\n"

                        elif event_type == "response.failed":
                            err_msg = (event.get("response", {}).get("error", {}) or {}).get("message", "Response failed")
                            error_data = {
                                "error": {"message": err_msg, "type": "server_error", "code": "response_failed"}
                            }
                            yield f"data: {json.dumps(error_data)}\n\n".encode()
                            yield b"data: [DONE]\n\n"

        return _stream()

    @staticmethod
    def _sse_chunk(
        chunk_id: str,
        created: int,
        model: str,
        delta: Dict[str, Any],
        *,
        finish_reason: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        chunk: Dict[str, Any] = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        if usage:
            cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            chunk["usage"] = {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        return f"data: {json.dumps(chunk, ensure_ascii=True)}\n\n".encode()

    @staticmethod
    async def _parse_sse(response: httpx.Response) -> AsyncIterator[Dict[str, Any]]:
        """Parse SSE events from an httpx streaming response."""
        buffer = ""
        async for raw_bytes in response.aiter_bytes():
            buffer += raw_bytes.decode("utf-8", errors="replace")
            while "\n\n" in buffer:
                chunk, buffer = buffer.split("\n\n", 1)
                data_lines = [
                    line[5:].strip()
                    for line in chunk.split("\n")
                    if line.startswith("data:")
                ]
                if not data_lines:
                    continue
                data = "\n".join(data_lines).strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue

    async def list_models(self) -> List[Dict[str, Any]]:
        # Codex API doesn't have a models endpoint; return empty
        return []
