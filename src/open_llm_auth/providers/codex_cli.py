from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import BaseProvider


class CodexCliProvider(BaseProvider):
    """
    Provider that spawns the OpenAI Codex CLI subprocess.
    Uses the codex CLI's built-in OAuth authentication.
    """

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
    ) -> List[str]:
        """Build codex CLI arguments."""
        args = [self.cli_path]

        # Add model argument if specified
        if model and model != "default":
            args.extend(["--model", model])

        # Build prompt from messages
        prompt = self._messages_to_prompt(messages)

        # Codex CLI uses a different format - run in non-interactive mode
        args.extend(["exec", "--json", "--color", "never", "--sandbox", "read-only"])

        if max_tokens:
            args.extend(["--max-tokens", str(max_tokens)])

        # Add the prompt at the end
        args.append(prompt)

        return args

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
        """Execute chat completion via codex CLI."""
        args = self._build_cli_args(
            model=model,
            messages=messages,
            max_tokens=payload.get("max_tokens"),
        )

        # Clear API keys to ensure CLI uses its own OAuth
        env = {
            k: v for k, v in os.environ.items() if not k.startswith("OPENAI_API_KEY")
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
            return self._parse_cli_output(output, model)

        except asyncio.TimeoutError:
            raise RuntimeError(f"codex CLI timed out after {self.timeout}s")

    def _parse_cli_output(self, output: str, model: str) -> Dict[str, Any]:
        """Parse codex CLI JSONL output to OpenAI format."""
        output = output.strip()

        if not output:
            return self._create_empty_response(model)

        # Codex CLI outputs JSONL - take the last line with the result
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
                    if "result" in data:
                        last_content = str(data["result"])
                    elif "content" in data:
                        last_content = str(data["content"])
                    elif "response" in data:
                        last_content = str(data["response"])
            except json.JSONDecodeError:
                # If not JSON, use the line as content
                last_content = line

        return self._create_response(last_content or output, model)

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
        """Return list of available Codex models."""
        models = [
            {
                "id": "gpt-5.3-codex",
                "object": "model",
                "created": 0,
                "owned_by": "openai",
            },
            {
                "id": "gpt-5.2-codex",
                "object": "model",
                "created": 0,
                "owned_by": "openai",
            },
            {
                "id": "gpt-5.1-codex-mini",
                "object": "model",
                "created": 0,
                "owned_by": "openai",
            },
        ]
        return models
