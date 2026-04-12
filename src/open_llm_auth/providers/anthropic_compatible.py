from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

import httpx

from .base import BaseProvider, compact_payload


def _normalize_tool_id(
    tool_id: str,
    *,
    id_map: Optional[Dict[str, str]] = None,
    used_ids: Optional[Set[str]] = None,
) -> str:
    """Normalize tool call ID to Anthropic's pattern [a-zA-Z0-9_-]{1,64}.

    Anthropic requires IDs that match this pattern. OpenAI IDs like
    'call_abc123' are fine, but some clients may send arbitrary strings.

    If `id_map` and `used_ids` are supplied, this function also guarantees
    per-request stability and collision avoidance so callback relationships
    remain intact even after normalization.
    """
    raw = str(tool_id or "").strip()
    if raw and id_map is not None and raw in id_map:
        return id_map[raw]

    seed = raw or f"toolu_{int(time.time() * 1000)}"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", seed)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_-")
    if not cleaned:
        cleaned = "toolu"
    candidate = cleaned[:64]

    if used_ids is not None:
        if candidate in used_ids:
            digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
            head = cleaned[:53]
            candidate = f"{head}_{digest}"[:64]
            salt = 1
            while candidate in used_ids:
                digest = hashlib.sha1(f"{seed}:{salt}".encode("utf-8")).hexdigest()[:10]
                candidate = f"{head}_{digest}"[:64]
                salt += 1
        used_ids.add(candidate)

    if raw and id_map is not None:
        id_map[raw] = candidate

    return candidate


def _invert_tool_id_map(id_map: Dict[str, str]) -> Dict[str, str]:
    return {v: k for k, v in id_map.items() if k and v}


def _convert_content_to_anthropic(content: Any) -> List[Dict[str, Any]]:
    """Convert OpenAI message content to Anthropic content blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]

    if isinstance(content, list):
        blocks: List[Dict[str, Any]] = []
        for part in content:
            if isinstance(part, str):
                blocks.append({"type": "text", "text": part})
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text", "")
                if text:
                    blocks.append({"type": "text", "text": text})
            elif part_type == "image_url":
                image_url = part.get("image_url", {})
                url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                if url.startswith("data:"):
                    # data URI: data:<mime>;base64,<data>
                    try:
                        header, b64data = url.split(",", 1)
                        media_type = header.split(":")[1].split(";")[0]
                        blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64data,
                            },
                        })
                    except Exception:
                        pass
                elif url:
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": url,
                        },
                    })
        return blocks

    return []


def _extract_text_only(content: Any) -> str:
    """Extract plain text from content (for system messages and fallback)."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        chunks: List[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
                continue
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks).strip()

    return ""


def _convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI tools format to Anthropic tools format."""
    anthropic_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            continue
        func = tool.get("function", {})
        if not isinstance(func, dict):
            continue
        name = func.get("name", "")
        if not name:
            continue
        anthropic_tool: Dict[str, Any] = {
            "name": name,
        }
        description = func.get("description")
        if description:
            anthropic_tool["description"] = description
        parameters = func.get("parameters")
        if parameters:
            anthropic_tool["input_schema"] = parameters
        else:
            anthropic_tool["input_schema"] = {"type": "object", "properties": {}}
        anthropic_tools.append(anthropic_tool)
    return anthropic_tools


def _convert_tool_choice(tool_choice: Any) -> Optional[Dict[str, Any]]:
    """Convert OpenAI tool_choice to Anthropic tool_choice."""
    if tool_choice is None:
        return None
    if tool_choice == "none":
        return None  # No Anthropic equivalent; omit tools entirely
    if tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "required":
        return {"type": "any"}
    if isinstance(tool_choice, dict):
        func = tool_choice.get("function", {})
        name = func.get("name") if isinstance(func, dict) else None
        if name:
            return {"type": "tool", "name": name}
    return {"type": "auto"}


class AnthropicCompatibleProvider(BaseProvider):
    async def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        tools = payload.get("tools")
        tool_choice = payload.get("tool_choice")
        tool_id_map: Dict[str, str] = {}
        system_prompt, anthropic_messages = self._convert_messages(messages, tool_id_map=tool_id_map)

        body: Dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": payload.get("max_tokens") or 4096,
            "temperature": payload.get("temperature"),
            "top_p": payload.get("top_p"),
            "stop_sequences": payload.get("stop"),
        }
        if system_prompt:
            body["system"] = system_prompt

        # Tools — omit entirely when tool_choice="none"
        if tools and tool_choice != "none":
            anthropic_tools = _convert_tools(tools)
            if anthropic_tools:
                body["tools"] = anthropic_tools
                tc = _convert_tool_choice(tool_choice)
                if tc:
                    body["tool_choice"] = tc

        body = compact_payload(body)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/messages",
                json=body,
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()

        return self.attach_response_telemetry(
            self._convert_response(
                data,
                fallback_model=model,
                tool_id_reverse_map=_invert_tool_id_map(tool_id_map),
            ),
            headers=response.headers,
            endpoint="messages",
        )

    async def chat_completion_stream(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        tools = payload.get("tools")
        tool_choice = payload.get("tool_choice")
        tool_id_map: Dict[str, str] = {}
        system_prompt, anthropic_messages = self._convert_messages(messages, tool_id_map=tool_id_map)
        tool_id_reverse_map = _invert_tool_id_map(tool_id_map)
        created = int(time.time())

        body: Dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": payload.get("max_tokens") or 4096,
            "temperature": payload.get("temperature"),
            "top_p": payload.get("top_p"),
            "stop_sequences": payload.get("stop"),
            "stream": True,
        }
        if system_prompt:
            body["system"] = system_prompt

        # Extended thinking support
        if payload.get("reasoning_effort"):
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": min(body["max_tokens"] - 1, 10000),
            }
            # Anthropic doesn't allow temperature with extended thinking
            body.pop("temperature", None)

        # Tools — omit entirely when tool_choice="none"
        if tools and tool_choice != "none":
            anthropic_tools = _convert_tools(tools)
            if anthropic_tools:
                body["tools"] = anthropic_tools
                tc = _convert_tool_choice(tool_choice)
                if tc:
                    body["tool_choice"] = tc

        body = compact_payload(body)

        headers = {**self.headers}
        headers.setdefault("Accept", "text/event-stream")

        async def _stream() -> AsyncIterator[bytes]:
            stream_id = f"chatcmpl-{self.provider_id}-{created}"
            emitted_role = False
            finish_reason: str = "stop"
            resolved_model = model
            # Track current content block
            current_block_type: str = "text"
            current_tool_id: Optional[str] = None
            current_tool_openai_id: Optional[str] = None
            current_tool_name: Optional[str] = None
            current_tool_args: List[str] = []
            # Index of the current tool call in the output
            current_tool_index: int = 0
            tool_call_count: int = 0

            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/messages",
                    json=body,
                    headers=headers,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        response.raise_for_status()

                    async for event in self._iter_sse_json(response):
                        event_type = str(event.get("type", "")).strip().lower()
                        if event_type == "message_start":
                            message = event.get("message")
                            if isinstance(message, dict):
                                stream_id = str(message.get("id") or stream_id)
                                resolved_model = str(message.get("model") or resolved_model)
                            continue

                        if event_type == "content_block_start":
                            block = event.get("content_block")
                            if isinstance(block, dict):
                                current_block_type = block.get("type", "text")
                                if current_block_type == "tool_use":
                                    raw_tool_id = str(block.get("id", f"toolu_{int(time.time() * 1000)}"))
                                    current_tool_id = raw_tool_id
                                    current_tool_openai_id = tool_id_reverse_map.get(raw_tool_id, raw_tool_id)
                                    current_tool_name = block.get("name", "")
                                    current_tool_args = []
                                    current_tool_index = tool_call_count
                                    tool_call_count += 1

                                    if not emitted_role:
                                        emitted_role = True
                                        yield self._openai_chunk(
                                            chunk_id=stream_id,
                                            created=created,
                                            model=resolved_model,
                                            delta={"role": "assistant", "content": None},
                                            finish_reason=None,
                                        )

                                    # Emit tool_call start chunk
                                    yield self._openai_chunk(
                                        chunk_id=stream_id,
                                        created=created,
                                        model=resolved_model,
                                        delta={
                                            "tool_calls": [{
                                                "index": current_tool_index,
                                                "id": current_tool_openai_id,
                                                "type": "function",
                                                "function": {
                                                    "name": current_tool_name,
                                                    "arguments": "",
                                                },
                                            }]
                                        },
                                        finish_reason=None,
                                    )
                            continue

                        if event_type == "content_block_delta":
                            delta = event.get("delta")
                            if not isinstance(delta, dict):
                                continue
                            delta_type = delta.get("type", "")

                            if current_block_type == "tool_use" and delta_type == "input_json_delta":
                                partial = delta.get("partial_json", "")
                                if partial:
                                    current_tool_args.append(partial)
                                    yield self._openai_chunk(
                                        chunk_id=stream_id,
                                        created=created,
                                        model=resolved_model,
                                        delta={
                                            "tool_calls": [{
                                                "index": current_tool_index,
                                                "function": {"arguments": partial},
                                            }]
                                        },
                                        finish_reason=None,
                                    )
                                continue

                            if not emitted_role:
                                emitted_role = True
                                yield self._openai_chunk(
                                    chunk_id=stream_id,
                                    created=created,
                                    model=resolved_model,
                                    delta={"role": "assistant", "content": None},
                                    finish_reason=None,
                                )

                            if delta_type == "thinking_delta":
                                text = delta.get("thinking", "")
                                if text:
                                    yield self._openai_chunk(
                                        chunk_id=stream_id,
                                        created=created,
                                        model=resolved_model,
                                        delta={"reasoning_content": text},
                                        finish_reason=None,
                                    )
                            elif delta_type == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield self._openai_chunk(
                                        chunk_id=stream_id,
                                        created=created,
                                        model=resolved_model,
                                        delta={"content": text},
                                        finish_reason=None,
                                    )
                            continue

                        if event_type == "content_block_stop":
                            current_block_type = "text"
                            current_tool_id = None
                            current_tool_openai_id = None
                            current_tool_name = None
                            current_tool_args = []
                            continue

                        if event_type == "message_delta":
                            delta = event.get("delta")
                            if isinstance(delta, dict):
                                reason = delta.get("stop_reason")
                                if isinstance(reason, str) and reason.strip():
                                    finish_reason = self._map_finish_reason(reason)
                            continue

                        if event_type == "message_stop":
                            if not emitted_role:
                                emitted_role = True
                                yield self._openai_chunk(
                                    chunk_id=stream_id,
                                    created=created,
                                    model=resolved_model,
                                    delta={"role": "assistant", "content": None},
                                    finish_reason=None,
                                )
                            yield self._openai_chunk(
                                chunk_id=stream_id,
                                created=created,
                                model=resolved_model,
                                delta={},
                                finish_reason=finish_reason,
                            )
                            yield b"data: [DONE]\n\n"
                            return

                    # Defensive fallback when upstream closes without message_stop.
                    if not emitted_role:
                        yield self._openai_chunk(
                            chunk_id=stream_id,
                            created=created,
                            model=resolved_model,
                            delta={"role": "assistant", "content": None},
                            finish_reason=None,
                        )
                    yield self._openai_chunk(
                        chunk_id=stream_id,
                        created=created,
                        model=resolved_model,
                        delta={},
                        finish_reason=finish_reason,
                    )
                    yield b"data: [DONE]\n\n"

        return _stream()

    async def list_models(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=min(self.timeout, 20.0)) as client:
            response = await client.get(f"{self.base_url}/v1/models", headers=self.headers)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                models = data.get("data")
                if isinstance(models, list):
                    return [m for m in models if isinstance(m, dict)]
            return []

    def _convert_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        tool_id_map: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Convert OpenAI messages to Anthropic format.

        Returns (system_prompt, anthropic_messages).
        """
        system_chunks: List[str] = []
        out: List[Dict[str, Any]] = []
        normalized_tool_id_map = tool_id_map if tool_id_map is not None else {}
        used_tool_ids: Set[str] = set(normalized_tool_id_map.values())

        for msg in messages:
            role = str(msg.get("role", "")).strip().lower()
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")  # for assistant messages
            tool_call_id = msg.get("tool_call_id")  # for tool result messages

            if role == "system":
                text = _extract_text_only(content)
                if text:
                    system_chunks.append(text)
                continue

            if role == "tool":
                # Tool result: wrap in tool_result block in a user message
                result_content = _extract_text_only(content)
                if not result_content:
                    result_content = ""
                normalized_id = _normalize_tool_id(
                    str(tool_call_id or ""),
                    id_map=normalized_tool_id_map,
                    used_ids=used_tool_ids,
                )
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": normalized_id,
                    "content": result_content,
                }
                # Try to merge with previous user message if it already has tool_results
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(tool_result_block)
                else:
                    out.append({"role": "user", "content": [tool_result_block]})
                continue

            if role == "assistant":
                anthropic_content: List[Dict[str, Any]] = []

                # Text content
                if content:
                    text = _extract_text_only(content)
                    if text:
                        anthropic_content.append({"type": "text", "text": text})

                # Tool use blocks from tool_calls
                if tool_calls and isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        tc_id = tc.get("id", f"toolu_{int(time.time() * 1000)}")
                        func = tc.get("function", {})
                        name = func.get("name", "") if isinstance(func, dict) else ""
                        arguments = func.get("arguments", "{}") if isinstance(func, dict) else "{}"
                        try:
                            input_obj = json.loads(arguments) if isinstance(arguments, str) else arguments
                        except Exception:
                            input_obj = {}
                        anthropic_content.append({
                            "type": "tool_use",
                            "id": _normalize_tool_id(
                                str(tc_id),
                                id_map=normalized_tool_id_map,
                                used_ids=used_tool_ids,
                            ),
                            "name": name,
                            "input": input_obj,
                        })

                if not anthropic_content:
                    # Skip empty assistant messages
                    continue

                # If only one text block, simplify to string for compat
                if len(anthropic_content) == 1 and anthropic_content[0]["type"] == "text":
                    out.append({"role": "assistant", "content": anthropic_content[0]["text"]})
                else:
                    out.append({"role": "assistant", "content": anthropic_content})
                continue

            # user role
            if role not in {"user", "assistant"}:
                role = "user"

            blocks = _convert_content_to_anthropic(content)
            if not blocks:
                continue

            # Simplify to string when just one text block
            if len(blocks) == 1 and blocks[0]["type"] == "text":
                out.append({"role": role, "content": blocks[0]["text"]})
            else:
                out.append({"role": role, "content": blocks})

        return "\n".join(system_chunks).strip(), out

    @staticmethod
    def _convert_response(
        data: Dict[str, Any],
        fallback_model: str,
        tool_id_reverse_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        output_text: List[str] = []
        tool_calls: List[Dict[str, Any]] = []

        for block in data.get("content", []):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str):
                    output_text.append(text)
            elif block_type == "tool_use":
                # Native Anthropic tool_use blocks
                tool_id = block.get("id", f"call_{int(time.time() * 1000)}")
                if tool_id_reverse_map:
                    tool_id = tool_id_reverse_map.get(str(tool_id), str(tool_id))
                name = block.get("name", "")
                args_input = block.get("input", {})
                tool_calls.append({
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args_input) if isinstance(args_input, dict) else str(args_input),
                    },
                })

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or 0)

        content_str = "".join(output_text)

        message_obj: Dict[str, Any] = {
            "role": "assistant",
            "content": content_str,
        }

        if tool_calls:
            message_obj["tool_calls"] = tool_calls

        return {
            "id": data.get("id") or "chatcmpl-anthropic",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": data.get("model") or fallback_model,
            "choices": [
                {
                    "index": 0,
                    "message": message_obj,
                    "finish_reason": "tool_calls" if tool_calls else AnthropicCompatibleProvider._map_finish_reason(data.get("stop_reason") or "stop"),
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    @staticmethod
    def _map_finish_reason(reason: str) -> str:
        normalized = reason.strip().lower()
        if normalized == "max_tokens":
            return "length"
        if normalized == "tool_use":
            return "tool_calls"
        return "stop"

    async def get_usage_telemetry(self, days: int = 7) -> Dict[str, Any]:
        return {
            "available": False,
            "provider": self.provider_id,
            "window_days": max(1, int(days)),
            "kind": "provider_account",
            "supported_fields": {
                "live_rate_limits": True,
                "account_usage": False,
                "billing_cycle": False,
                "subscription_cost": False,
            },
            "note": "Anthropic exposes organization usage and cost reporting through its admin surface, but this adapter currently surfaces live rate-limit headers only.",
        }

    async def _iter_sse_json(self, response: httpx.Response) -> AsyncIterator[Dict[str, Any]]:
        data_lines: List[str] = []
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
                continue

            if line.strip() != "":
                continue

            if not data_lines:
                continue

            payload = "\n".join(data_lines).strip()
            data_lines = []
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = json.loads(payload)
            except Exception:
                continue
            if isinstance(parsed, dict):
                yield parsed

        # Flush trailing frame if stream ended without a blank line terminator.
        if data_lines:
            payload = "\n".join(data_lines).strip()
            if payload and payload != "[DONE]":
                try:
                    parsed = json.loads(payload)
                except Exception:
                    return
                if isinstance(parsed, dict):
                    yield parsed

    @staticmethod
    def _openai_chunk(
        *,
        chunk_id: str,
        created: int,
        model: str,
        delta: Dict[str, Any],
        finish_reason: Any,
    ) -> bytes:
        chunk = {
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
        return f"data: {json.dumps(chunk, ensure_ascii=True)}\n\n".encode("utf-8")
