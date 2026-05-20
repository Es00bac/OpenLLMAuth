"""Anthropic-compatible inbound routes for the gateway.

Claude Code speaks Anthropic's /v1/messages API. This router accepts those
requests, resolves the provider, and either:

1. Passes through natively to Anthropic-compatible backends (kimi-coding, etc.)
2. Translates to/from OpenAI format for OpenAI-compatible backends (zai-coding, etc.)
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from ..auth.manager import ProviderManager
from ..providers import AnthropicCompatibleProvider
from .auth import Principal, require_scopes, verify_server_token

router = APIRouter(prefix="/v1")
manager = ProviderManager()


def _anthropic_error(message: str, error_type: str = "invalid_request_error") -> Dict[str, Any]:
    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": message,
        },
    }


def _enforce_scope(principal: Principal, scope: str) -> Optional[Dict[str, Any]]:
    try:
        require_scopes(principal, scope)
    except Exception:
        return {
            "type": "error",
            "error": {
                "type": "permission_error",
                "message": f"Missing required scope '{scope}'.",
            },
        }
    return None


def _verify_anthropic_inbound(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
) -> Principal:
    """Accept Bearer token OR x-api-key (Claude Code sends the latter natively).

    Falls back to anonymous principal when OPEN_LLM_AUTH_ALLOW_ANON=1 so local
    Claude Code launchers don't need to sync tokens across settings.json.
    """
    try:
        if authorization:
            return verify_server_token(authorization=authorization)
        if x_api_key:
            return verify_server_token(authorization=f"Bearer {x_api_key}")
        return verify_server_token(authorization=None)
    except HTTPException:
        if os.getenv("OPEN_LLM_AUTH_ALLOW_ANON", "").strip().lower() in {"1", "true", "yes", "on"}:
            return Principal(
                subject="anonymous",
                token_id="anonymous",
                scopes=frozenset({"read", "write", "admin"}),
                is_admin=True,
                source="anonymous",
            )
        raise


# ---------- Anthropic -> OpenAI conversion utilities ----------


def convert_tools_anthropic_to_openai(anthropic_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    openai_tools = []
    for tool in anthropic_tools:
        name = tool.get("name", "")
        description = tool.get("description", "")
        parameters = tool.get("input_schema", tool.get("parameters", {}))
        openai_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            }
        })
    return openai_tools


def convert_tool_choice_anthropic_to_openai(anthropic_tool_choice: Any) -> Optional[Any]:
    if not anthropic_tool_choice:
        return None
    if isinstance(anthropic_tool_choice, str):
        if anthropic_tool_choice == "any":
            return "required"
        return anthropic_tool_choice  # auto / none / provider-specific
    t = anthropic_tool_choice.get("type", "auto")
    if t == "tool":
        return {"type": "function", "function": {"name": anthropic_tool_choice.get("name", "")}}
    if t == "any":
        return "required"
    if t == "auto":
        return "auto"
    if t == "none":
        return "none"
    return t


def _extract_anthropic_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks)
    if content is None:
        return ""
    return str(content)


def estimate_anthropic_input_tokens(body: Dict[str, Any]) -> int:
    """Conservative local fallback for Claude Code's count-tokens request."""
    pieces = [
        _extract_anthropic_text(body.get("system")),
        json.dumps(body.get("messages", []), ensure_ascii=False),
        json.dumps(body.get("tools", []), ensure_ascii=False),
    ]
    text = "\n".join(piece for piece in pieces if piece)
    return max(1, len(text.split()), math.ceil(len(text) / 4))


def anthropic_messages_to_openai_messages(anthropic_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    openai_messages = []
    for msg in anthropic_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            tool_calls = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text":
                    parts.append({"type": "text", "text": block.get("text", "")})
                elif bt == "image":
                    source = block.get("source", {})
                    parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{source.get('media_type','image/png')};{source.get('type','base64')},{source.get('data','')}"
                        }
                    })
                elif bt == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        }
                    })
                elif bt == "tool_result":
                    tool_call_id = block.get("tool_use_id", "")
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        result_text = "\n".join(
                            b.get("text", "") for b in result_content if isinstance(b, dict) and b.get("type") == "text"
                        )
                    else:
                        result_text = str(result_content)
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_text,
                    })
            if tool_calls:
                msg = {"role": role, "tool_calls": tool_calls}
                if parts:
                    text_parts = [
                        p.get("text", "")
                        for p in parts
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    msg["content"] = "".join(text_parts) or None
                else:
                    msg["content"] = None
                openai_messages.append(msg)
            elif parts:
                openai_messages.append({"role": role, "content": parts})
            elif role == "assistant":
                openai_messages.append({"role": role, "content": ""})
        else:
            openai_messages.append({"role": role, "content": content})
    return openai_messages


def map_stop_reason_openai_to_anthropic(openai_reason: Optional[str]) -> str:
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "content_filter": "content_filter",
        "tool_calls": "tool_use",
    }
    return mapping.get(openai_reason or "", openai_reason or "end_turn")


def build_anthropic_content_from_openai_message(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    content = []
    reasoning = message.get("reasoning_content", "")
    text = message.get("content", "")
    if reasoning:
        content.append({"type": "thinking", "thinking": reasoning, "signature": ""})
    if text:
        content.append({"type": "text", "text": text})
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        if tc.get("type") == "function":
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            content.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "input": args,
            })
    if not content:
        content = [{"type": "text", "text": ""}]
    return content


def openai_chunk_to_anthropic_events(
    chunk_data: Dict[str, Any], tool_call_buffer: Dict[int, Dict[str, Any]]
) -> List[str]:
    delta = chunk_data.get("choices", [{}])[0].get("delta", {})
    events = []

    # Text delta
    if "content" in delta and delta["content"]:
        events.append(
            f'event: content_block_delta\ndata: {json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": delta["content"]}})}\n\n'
        )

    # Tool call deltas
    if "tool_calls" in delta:
        for dtc in delta["tool_calls"]:
            idx = dtc.get("index", 0)
            tc_id = dtc.get("id")
            func = dtc.get("function", {})
            name = func.get("name")
            args = func.get("arguments", "")

            if tc_id and name:
                tool_call_buffer[idx] = {"id": tc_id, "name": name, "arguments": args or ""}
                events.append(
                    f'event: content_block_start\ndata: {json.dumps({"type": "content_block_start", "index": idx + 1, "content_block": {"type": "tool_use", "id": tc_id, "name": name, "input": {}}})}\n\n'
                )
            elif args:
                if idx not in tool_call_buffer:
                    tool_call_buffer[idx] = {"id": "", "name": "", "arguments": ""}
                tool_call_buffer[idx]["arguments"] += args
                events.append(
                    f'event: content_block_delta\ndata: {json.dumps({"type": "content_block_delta", "index": idx + 1, "delta": {"type": "input_json_delta", "partial_json": args}})}\n\n'
                )

    return events


class OpenAIAnthropicStreamTranslator:
    """Stateful OpenAI SSE chunk -> Anthropic Messages event translator."""

    def __init__(self, *, model: str, message_id: str = "msg-proxy") -> None:
        self.model = model
        self.message_id = message_id
        self._next_block_index = 0
        self._text_block_index: Optional[int] = None
        self._tool_block_indices: Dict[int, int] = {}
        self._opened_any_block = False

    @staticmethod
    def _event(name: str, data: Dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    def start_events(self) -> List[str]:
        return [
            self._event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": self.message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": self.model,
                        "content": [],
                        "stop_reason": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
        ]

    def accept_chunk(self, chunk: Dict[str, Any]) -> List[str]:
        choices = chunk.get("choices") or [{}]
        first_choice = choices[0] if choices else {}
        delta = first_choice.get("delta") or {}
        events: List[str] = []

        content = delta.get("content")
        if isinstance(content, str) and content:
            if self._text_block_index is None:
                self._text_block_index = self._next_block_index
                self._next_block_index += 1
                self._opened_any_block = True
                events.append(
                    self._event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": self._text_block_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                )
            events.append(
                self._event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self._text_block_index,
                        "delta": {"type": "text_delta", "text": content},
                    },
                )
            )

        for tool_delta in delta.get("tool_calls") or []:
            if not isinstance(tool_delta, dict):
                continue
            tool_index = int(tool_delta.get("index", 0))
            func = tool_delta.get("function", {}) if isinstance(tool_delta.get("function"), dict) else {}
            name = func.get("name")
            tool_id = tool_delta.get("id")

            if tool_index not in self._tool_block_indices:
                if not tool_id and not name:
                    continue
                block_index = self._next_block_index
                self._tool_block_indices[tool_index] = block_index
                self._next_block_index += 1
                self._opened_any_block = True
                events.append(
                    self._event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_id or f"call_{int(time.time() * 1000)}",
                                "name": name or "",
                                "input": {},
                            },
                        },
                    )
                )

            args = func.get("arguments")
            if isinstance(args, str) and args:
                events.append(
                    self._event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": self._tool_block_indices[tool_index],
                            "delta": {"type": "input_json_delta", "partial_json": args},
                        },
                    )
                )

        return events

    def finish_events(self, finish_reason: Optional[str]) -> List[str]:
        events: List[str] = []
        if not self._opened_any_block:
            events.append(
                self._event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            )
            events.append(self._event("content_block_stop", {"type": "content_block_stop", "index": 0}))
        else:
            if self._text_block_index is not None:
                events.append(
                    self._event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": self._text_block_index},
                    )
                )
            for block_index in sorted(self._tool_block_indices.values()):
                events.append(
                    self._event("content_block_stop", {"type": "content_block_stop", "index": block_index})
                )
        events.append(
            self._event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": map_stop_reason_openai_to_anthropic(finish_reason),
                        "stop_sequence": None,
                    },
                    "usage": {"output_tokens": 0},
                },
            )
        )
        events.append(self._event("message_stop", {"type": "message_stop"}))
        return events


def _forward_anthropic_client_headers(request: Request, headers: Dict[str, str]) -> Dict[str, str]:
    forwarded = {**headers}
    for header_name in (
        "anthropic-beta",
        "anthropic-version",
        "x-claude-code-session-id",
    ):
        value = request.headers.get(header_name)
        if value:
            forwarded[header_name] = value
    return forwarded


# ---------- Route ----------

@router.post("/messages/count_tokens")
async def anthropic_count_tokens_route(
    request: Request,
    principal: Principal = Depends(_verify_anthropic_inbound),
):
    denied = _enforce_scope(principal, "read")
    if denied is not None:
        return JSONResponse(status_code=403, content=denied)
    body = await request.json()
    return JSONResponse(content={"input_tokens": estimate_anthropic_input_tokens(body)})

@router.post("/messages")
async def anthropic_messages_route(
    request: Request,
    x_auth_profile: Optional[str] = Header(default=None, alias="X-Auth-Profile"),
    principal: Principal = Depends(_verify_anthropic_inbound),
):
    denied = _enforce_scope(principal, "write")
    if denied is not None:
        return JSONResponse(status_code=403, content=denied)

    body = await request.json()
    model = body.get("model", "")
    stream = body.get("stream", False)

    try:
        resolved = manager.resolve(model, preferred_profile=x_auth_profile)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=_anthropic_error(str(exc)),
        )

    provider = resolved.provider

    # 1) Native Anthropic backend — pass through
    if isinstance(provider, AnthropicCompatibleProvider):
        forward_body = {**body, "model": resolved.model_id}
        headers = _forward_anthropic_client_headers(request, provider.headers)
        headers.setdefault("Content-Type", "application/json")

        if stream:
            headers.setdefault("Accept", "text/event-stream")
            client = httpx.AsyncClient(timeout=None)
            http_req = client.build_request(
                "POST",
                f"{provider.base_url}/v1/messages",
                json=forward_body,
                headers=headers,
            )
            resp = await client.send(http_req, stream=True)
            if resp.status_code >= 400:
                error_body = await resp.aread()
                return Response(
                    content=error_body,
                    media_type="application/json",
                    status_code=resp.status_code,
                )
            return StreamingResponse(
                resp.aiter_bytes(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            async with httpx.AsyncClient(timeout=provider.timeout) as client:
                resp = await client.post(
                    f"{provider.base_url}/v1/messages",
                    json=forward_body,
                    headers=headers,
                )
                return Response(
                    content=resp.content,
                    media_type="application/json",
                    status_code=resp.status_code,
                )

    # 2) OpenAI-compatible backend — translate
    messages = body.get("messages", [])
    system = body.get("system", "")
    max_tokens = body.get("max_tokens", 4096)
    temperature = body.get("temperature", 1.0)
    top_p = body.get("top_p")
    stop_sequences = body.get("stop_sequences")
    tools = body.get("tools", [])
    tool_choice = body.get("tool_choice")

    openai_messages = anthropic_messages_to_openai_messages(messages)
    if system:
        openai_messages.insert(0, {"role": "system", "content": system})

    payload: Dict[str, Any] = {
        "model": resolved.model_id,
        "messages": openai_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    if top_p is not None:
        payload["top_p"] = top_p
    if stop_sequences is not None:
        payload["stop"] = stop_sequences
    if tools:
        payload["tools"] = convert_tools_anthropic_to_openai(tools)
    if tool_choice is not None:
        tc = convert_tool_choice_anthropic_to_openai(tool_choice)
        if tc is not None:
            payload["tool_choice"] = tc

    if stream:
        try:
            stream_iter = await provider.chat_completion_stream(
                model=resolved.model_id,
                messages=openai_messages,
                payload=payload,
            )
        except httpx.HTTPStatusError as exc:
            return Response(
                content=exc.response.content,
                media_type="application/json",
                status_code=exc.response.status_code,
            )
        except Exception as exc:
            logging.exception("Anthropic route stream start failed")
            return JSONResponse(
                status_code=500,
                content=_anthropic_error(f"Stream start failed: {exc}"),
            )

        async def _translated_stream() -> AsyncIterator[str]:
            translator = OpenAIAnthropicStreamTranslator(model=model)
            for event in translator.start_events():
                yield event
            finish_reason = "end_turn"
            buffer = ""
            done = False

            async for chunk_bytes in stream_iter:
                buffer += chunk_bytes.decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    data_lines = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
                    if not data_lines:
                        continue
                    data_str = "\n".join(data_lines).strip()
                    if data_str == "[DONE]":
                        done = True
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [{}])
                    if choices and choices[0].get("finish_reason"):
                        finish_reason = map_stop_reason_openai_to_anthropic(choices[0]["finish_reason"])

                    for ev in translator.accept_chunk(chunk):
                        yield ev
                if done:
                    break

            for event in translator.finish_events(finish_reason):
                yield event

        return StreamingResponse(
            _translated_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    else:
        try:
            response_data = await provider.chat_completion(
                model=resolved.model_id,
                messages=openai_messages,
                payload=payload,
            )
        except httpx.HTTPStatusError as exc:
            return Response(
                content=exc.response.content,
                media_type="application/json",
                status_code=exc.response.status_code,
            )
        except Exception as exc:
            logging.exception("Anthropic route call failed")
            return JSONResponse(
                status_code=500,
                content=_anthropic_error(f"Provider call failed: {exc}"),
            )

        choice = response_data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = build_anthropic_content_from_openai_message(message)

        anthropic_response = {
            "id": response_data.get("id", "msg-proxy"),
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": map_stop_reason_openai_to_anthropic(choice.get("finish_reason")),
            "usage": {
                "input_tokens": response_data.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": response_data.get("usage", {}).get("completion_tokens", 0),
            },
        }
        return JSONResponse(content=anthropic_response)
