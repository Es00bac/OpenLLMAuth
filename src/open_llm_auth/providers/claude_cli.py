from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .base import BaseProvider


class ClaudeCliProvider(BaseProvider):
    """
    Provider that spawns the Claude Code CLI subprocess.
    Uses the claude CLI's built-in OAuth authentication.
    """

    # Model aliases mapping to claude CLI model names
    MODEL_ALIASES: Dict[str, str] = {
        "opus": "opus",
        "opus-4.6": "opus",
        "opus-4.5": "opus",
        "opus-4": "opus",
        "claude-opus-4-6": "opus",
        "claude-opus-4-5": "opus",
        "claude-opus-4": "opus",
        "sonnet": "sonnet",
        "sonnet-4.6": "sonnet",
        "sonnet-4.5": "sonnet",
        "sonnet-4.1": "sonnet",
        "sonnet-4.0": "sonnet",
        "claude-sonnet-4-6": "sonnet",
        "claude-sonnet-4-5": "sonnet",
        "claude-sonnet-4-1": "sonnet",
        "claude-sonnet-4-0": "sonnet",
        "haiku": "haiku",
        "haiku-3.5": "haiku",
        "claude-haiku-3-5": "haiku",
        "default": "sonnet",
    }

    def __init__(
        self,
        *,
        provider_id: str = "claude-cli",
        api_key: Optional[str] = None,
        base_url: str = "",
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 300.0,
        cli_path: str = "claude",
    ):
        super().__init__(
            provider_id=provider_id,
            api_key=api_key,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )
        self.cli_path = cli_path

    def _resolve_model(self, model: str) -> str:
        """Resolve model alias to claude CLI model name."""
        normalized = model.lower().strip()
        return self.MODEL_ALIASES.get(normalized, normalized)

    def _build_cli_args(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> List[str]:
        """Build claude CLI arguments."""
        args = [self.cli_path]

        # Add model argument
        resolved_model = self._resolve_model(model)
        if resolved_model and resolved_model != "default":
            args.extend(["--model", resolved_model])

        # Add session ID if provided (for conversation continuity)
        if session_id:
            args.extend(["--session-id", session_id])

        # Build prompt from messages
        prompt = self._messages_to_prompt(messages)
        args.extend(["-p", prompt])

        # Output format
        args.extend(["--output-format", "json"])

        # Skip permissions for non-interactive use
        args.append("--dangerously-skip-permissions")

        return args

    def _messages_to_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """Convert OpenAI-style messages to a single prompt string."""
        parts: List[str] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, list):
                # Extract text from content parts
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
                parts.append(f"Assistant: {content}")
            else:
                parts.append(content)

        return "\n\n".join(parts)

    async def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute chat completion via claude CLI."""
        args = self._build_cli_args(
            model=model,
            messages=messages,
            max_tokens=payload.get("max_tokens"),
        )

        # Clear ANTHROPIC_API_KEY to ensure CLI uses its own OAuth
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_OLD")
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout,
            )

            output = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Try to parse output even if exit code is non-zero (rate limit returns exit 1)
            if output.strip():
                try:
                    return self._parse_cli_output(output, model)
                except RuntimeError:
                    raise  # Re-raise rate limit errors
                except Exception:
                    # If parsing fails and exit code is non-zero, raise error
                    if proc.returncode != 0:
                        error_msg = (
                            stderr_str if stderr_str else f"exit code {proc.returncode}"
                        )
                        raise RuntimeError(f"claude CLI failed: {error_msg}")
                    raise

            if proc.returncode != 0:
                error_msg = stderr_str if stderr_str else f"exit code {proc.returncode}"
                raise RuntimeError(f"claude CLI failed: {error_msg}")

            return self._create_empty_response(model)

        except asyncio.TimeoutError:
            raise RuntimeError(f"claude CLI timed out after {self.timeout}s")

    def _parse_cli_output(self, output: str, model: str) -> Dict[str, Any]:
        """Parse claude CLI JSON output to OpenAI format."""
        output = output.strip()

        if not output:
            return self._create_empty_response(model)

        # Try to parse as JSON array first (Claude CLI output format)
        if output.startswith("["):
            try:
                events = json.loads(output)
                if isinstance(events, list):
                    # Look for assistant message in the array
                    for event in events:
                        if event.get("type") == "assistant":
                            msg = event.get("message", {})
                            content = ""
                            for block in msg.get("content", []):
                                if block.get("type") == "text":
                                    content += block.get("text", "")
                            return self._create_response(content, model)
                        # Check for rate limit event
                        if event.get("type") == "rate_limit_event":
                            rate_info = event.get("rate_limit_info", {})
                            error_msg = "Rate limit reached"
                            if rate_info.get("resetsAt"):
                                error_msg += (
                                    f" · resets at timestamp {rate_info['resetsAt']}"
                                )
                            raise RuntimeError(error_msg)
                    # If no assistant message found, check for result
                    for event in events:
                        if event.get("type") == "result":
                            result_text = event.get("result", "")
                            return self._create_response(str(result_text), model)
            except json.JSONDecodeError:
                pass
            except RuntimeError:
                raise

        # Try to parse as single JSON object
        try:
            data = json.loads(output)
            if isinstance(data, dict):
                content = (
                    data.get("content")
                    or data.get("response")
                    or data.get("output")
                    or ""
                )
                if isinstance(content, dict):
                    content = content.get("text") or content.get("content") or ""
                return self._create_response(str(content), model)
        except json.JSONDecodeError:
            pass

        # If not valid JSON, treat entire output as content
        return self._create_response(output, model)

    def _create_response(self, content: str, model: str) -> Dict[str, Any]:
        """Create OpenAI-compatible response."""
        return {
            "id": f"chatcmpl-claude-cli-{int(time.time() * 1000)}",
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
        Streaming is not natively supported by claude CLI in JSON mode.
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
        """Return list of available Claude models via CLI."""
        models = [
            {"id": "opus", "object": "model", "created": 0, "owned_by": "anthropic"},
            {"id": "sonnet", "object": "model", "created": 0, "owned_by": "anthropic"},
            {"id": "haiku", "object": "model", "created": 0, "owned_by": "anthropic"},
            {
                "id": "claude-opus-4-6",
                "object": "model",
                "created": 0,
                "owned_by": "anthropic",
            },
            {
                "id": "claude-sonnet-4-6",
                "object": "model",
                "created": 0,
                "owned_by": "anthropic",
            },
            {
                "id": "claude-opus-4-5",
                "object": "model",
                "created": 0,
                "owned_by": "anthropic",
            },
            {
                "id": "claude-sonnet-4-5",
                "object": "model",
                "created": 0,
                "owned_by": "anthropic",
            },
        ]
        return models
