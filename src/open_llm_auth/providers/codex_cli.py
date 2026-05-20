from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4

from .base import BaseProvider
from ..provider_catalog import get_builtin_provider_models


class CodexCliProvider(BaseProvider):
    """
    Provider that spawns the OpenAI Codex CLI subprocess.
    Uses the codex CLI's built-in OAuth authentication.
    """

    MAX_PROMPT_ARG_BYTES = 64_000

    def __init__(
        self,
        *,
        provider_id: str = "codex-cli",
        api_key: Optional[str] = None,
        base_url: str = "",
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 300.0,
        cli_path: str = "codex",
    ):
        super().__init__(
            provider_id=provider_id,
            api_key=api_key,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )
        self.cli_path = cli_path

    def _build_cli_args(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        *,
        prompt: Optional[str] = None,
        pipe_prompt: bool = False,
    ) -> List[str]:
        """Build codex CLI arguments."""
        args = [self.cli_path]

        # Add model argument if specified
        if model and model != "default":
            args.extend(["--model", model])

        if reasoning_effort:
            args.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])

        # Build prompt from messages
        if prompt is None:
            prompt = self._messages_to_prompt(messages)

        # Codex CLI uses a different format - run in non-interactive mode
        args.extend(["exec", "--json", "--color", "never", "--sandbox", "workspace-write"])

        if max_tokens:
            args.extend(["--max-tokens", str(max_tokens)])

        # Add the prompt at the end. Long OpenCAS context prompts must be
        # piped through stdin instead of argv or subprocess spawning can fail
        # with E2BIG before Codex has a chance to read the request.
        args.append("-" if pipe_prompt else prompt)

        return args

    def supports_reasoning_effort(self, *, model: Optional[str] = None) -> bool:
        return True

    def _messages_to_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """Convert OpenAI-style messages to a single prompt string."""
        parts: List[str] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                content = "\n".join(text_parts)

            if role == "system":
                parts.append(f"System: {content}")
            elif role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    parts.append(
                        "Assistant requested tool calls: "
                        + json.dumps(tool_calls, ensure_ascii=False)
                    )
                if content:
                    parts.append(f"Assistant: {content}")
            elif role == "tool":
                name = msg.get("name") or msg.get("tool_call_id") or "tool"
                parts.append(f"Tool result ({name}): {content}")
            else:
                parts.append(content)

        return "\n\n".join(parts)

    def _tool_protocol_prompt(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        tool_choice: Any = None,
    ) -> str:
        """Add a JSON tool-call bridge for providers without native tools."""
        compact_tools: List[Dict[str, Any]] = []
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if not isinstance(function, dict):
                continue
            compact_tools.append(
                {
                    "name": function.get("name"),
                    "description": function.get("description"),
                    "parameters": function.get("parameters") or {},
                }
            )
        if not compact_tools:
            return prompt
        bridge = (
            "OpenCAS tool-call bridge for this turn:\n"
            "You are being called by an OpenAI-compatible tool loop, but this transport "
            "does not support native function calling. Use this exact JSON protocol.\n"
            "If a tool is needed, respond with only this JSON object and no prose:\n"
            '{"tool_calls":[{"name":"tool_name","arguments":{"arg":"value"}}]}\n'
            "If no more tools are needed, respond with only this JSON object and no prose:\n"
            '{"final":"your final answer"}\n'
            "Do not claim that a web, file, browser, HTTP, or runtime tool was used unless "
            "a Tool result message for that tool is present above.\n"
            f"tool_choice={tool_choice or 'auto'}\n"
            "Available tools:\n"
            f"{json.dumps(compact_tools, ensure_ascii=False)}"
        )
        return f"{bridge}\n\nConversation:\n{prompt}"

    async def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute chat completion via codex CLI."""
        prompt = self._messages_to_prompt(messages)
        tools = payload.get("tools")
        if isinstance(tools, list) and tools:
            prompt = self._tool_protocol_prompt(prompt, tools, payload.get("tool_choice"))
        # Always use stdin for prompts. This avoids argv-size failures on large
        # OpenCAS contexts and prevents prompt/context leakage through process
        # listings for ordinary-sized calls.
        pipe_prompt = True
        args = self._build_cli_args(
            model=model,
            messages=messages,
            max_tokens=payload.get("max_tokens"),
            reasoning_effort=payload.get("reasoning_effort"),
            prompt=prompt,
            pipe_prompt=pipe_prompt,
        )

        # Clear API keys to ensure CLI uses its own OAuth
        env = {
            k: v for k, v in os.environ.items() if not k.startswith("OPENAI_API_KEY")
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE if pipe_prompt else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8") if pipe_prompt else None),
                timeout=self.timeout,
            )

            if proc.returncode != 0:
                error_msg = (
                    stderr.decode("utf-8", errors="replace")
                    if stderr
                    else "Unknown error"
                )
                raise RuntimeError(
                    f"codex CLI failed (exit {proc.returncode}): {error_msg}"
                )

            output = stdout.decode("utf-8", errors="replace")
            return self._parse_cli_output(output, model, tools=tools if isinstance(tools, list) else None)

        except asyncio.TimeoutError:
            raise RuntimeError(f"codex CLI timed out after {self.timeout}s")

    def _parse_cli_output(
        self,
        output: str,
        model: str,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Parse codex CLI JSONL output to OpenAI format."""
        output = output.strip()

        if not output:
            return self._create_empty_response(model)

        # Codex CLI outputs JSONL event streams. Prefer the final normalized
        # assistant text rather than returning raw event payloads verbatim.
        lines = output.split("\n")
        last_content = ""

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                # Look for result or content in the JSON
                if isinstance(data, dict):
                    if data.get("type") == "item.completed":
                        item = data.get("item") or {}
                        if item.get("type") == "agent_message":
                            text = item.get("text")
                            if text:
                                last_content = str(text)
                                continue
                    if data.get("type") == "agent_message":
                        text = data.get("text")
                        if text:
                            last_content = str(text)
                            continue
                    if "result" in data:
                        last_content = self._stringify_payload(data["result"])
                    elif "content" in data:
                        last_content = self._stringify_payload(data["content"])
                    elif "response" in data:
                        last_content = self._stringify_payload(data["response"])
            except json.JSONDecodeError:
                # If not JSON, use the line as content
                last_content = line

        content = last_content or output
        if tools:
            tool_response = self._structured_tool_bridge_response(content, model)
            if tool_response is not None:
                return tool_response
        return self._create_response(content, model)

    def _structured_tool_bridge_response(self, content: str, model: str) -> Optional[Dict[str, Any]]:
        payload = self._extract_json_object(content)
        if not isinstance(payload, dict):
            return None
        final = payload.get("final")
        if final is None:
            final = payload.get("answer")
        if final is not None:
            return self._create_response(str(final), model)
        raw_calls = payload.get("tool_calls")
        if raw_calls is None:
            raw_calls = payload.get("tools")
        if raw_calls is None:
            raw_calls = payload.get("calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            return None
        tool_calls: List[Dict[str, Any]] = []
        for raw in raw_calls:
            if not isinstance(raw, dict):
                continue
            function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
            name = raw.get("name") or raw.get("tool") or function.get("name")
            if not name:
                continue
            arguments = (
                raw.get("arguments")
                if "arguments" in raw
                else raw.get("args", raw.get("input", raw.get("parameters", function.get("arguments", {}))))
            )
            if isinstance(arguments, str):
                argument_text = arguments
            else:
                argument_text = json.dumps(arguments or {}, ensure_ascii=False)
            tool_calls.append(
                {
                    "id": str(raw.get("id") or f"call_{uuid4().hex[:16]}"),
                    "type": "function",
                    "function": {
                        "name": str(name),
                        "arguments": argument_text,
                    },
                }
            )
        if not tool_calls:
            return None
        return self._create_tool_call_response(tool_calls, model)

    @staticmethod
    def _extract_json_object(content: str) -> Optional[Dict[str, Any]]:
        text = str(content or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        candidates = [text]
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            candidates.append(text[first : last + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _stringify_payload(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        if isinstance(value, list):
            text_parts: List[str] = []
            for item in value:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if text:
                        text_parts.append(str(text))
                elif isinstance(item, str):
                    text_parts.append(item)
            if text_parts:
                return "\n".join(text_parts)
        return json.dumps(value, ensure_ascii=False)

    def _create_response(self, content: str, model: str) -> Dict[str, Any]:
        """Create OpenAI-compatible response."""
        return {
            "id": f"chatcmpl-codex-cli-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
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

    def _create_tool_call_response(
        self,
        tool_calls: List[Dict[str, Any]],
        model: str,
    ) -> Dict[str, Any]:
        response = self._create_response("", model)
        response["choices"][0]["message"]["tool_calls"] = tool_calls
        response["choices"][0]["finish_reason"] = "tool_calls"
        return response

    def _create_empty_response(self, model: str) -> Dict[str, Any]:
        """Create empty response for no output."""
        return self._create_response("", model)

    async def chat_completion_stream(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """
        Streaming is not natively supported by codex CLI in JSON mode.
        We return the full response as a single chunk.
        """
        response = await self.chat_completion(
            model=model,
            messages=messages,
            payload=payload,
        )

        # Yield the response as a single SSE chunk
        chunk = {
            "id": response["id"],
            "object": "chat.completion.chunk",
            "created": response["created"],
            "model": response["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")

        # Yield content chunk
        content = response["choices"][0]["message"]["content"]
        if content:
            chunk["choices"][0]["delta"] = {"content": content}
            yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")

        # Yield finish chunk
        chunk["choices"][0]["delta"] = {}
        chunk["choices"][0]["finish_reason"] = "stop"
        yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    async def list_models(self) -> List[Dict[str, Any]]:
        """Return builtin Codex models surfaced by the gateway catalog."""
        return [
            {
                "id": str(model["id"]),
                "object": "model",
                "created": 0,
                "owned_by": "openai",
            }
            for model in get_builtin_provider_models("codex-cli")
            if model.get("id")
        ]
