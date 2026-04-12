"""
Tests for the AnthropicCompatibleProvider adapter.

Covers all changes documented in ANTHROPIC_ADAPTER_CHANGES.md:
  1. Tool definitions conversion
  2. Tool choice mapping
  3. Tool result messages (role: "tool")
  4. Assistant messages with tool_calls
  5. Tool call ID normalization
  6. Image content conversion
  7. Streaming tool calls (live only)
  8. Role chunk fix (live only)
"""
from __future__ import annotations

import json
import sys
import asyncio
import os
from typing import Any, Dict, List

import pytest
import pytest_asyncio

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))

from open_llm_auth.providers.anthropic_compatible import (
    AnthropicCompatibleProvider,
    _convert_content_to_anthropic,
    _convert_tool_choice,
    _convert_tools,
    _invert_tool_id_map,
    _normalize_tool_id,
)

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

ROUTER_URL = os.environ.get("OPEN_LLM_AUTH_URL", "http://127.0.0.1:8000/v1")
ANTHROPIC_MODEL = "anthropic/claude-haiku-4-5-20251001"
# Strip provider prefix for the provider-level model ID
ANTHROPIC_MODEL_ID = ANTHROPIC_MODEL.split("/", 1)[1]

# A minimal provider instance (no real network calls in unit tests)
def _make_provider() -> AnthropicCompatibleProvider:
    return AnthropicCompatibleProvider(
        provider_id="anthropic",
        api_key="test-key",
        base_url="https://api.anthropic.com",
        headers={"x-api-version": "2023-06-01", "anthropic-version": "2023-06-01"},
    )


# ─────────────────────────────────────────────────────────────
# 1. Tool Definitions
# ─────────────────────────────────────────────────────────────

class TestConvertTools:
    def test_basic_function_tool(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                },
            }
        ]
        result = _convert_tools(tools)
        assert len(result) == 1
        t = result[0]
        assert t["name"] == "get_weather"
        assert t["description"] == "Get the current weather"
        assert t["input_schema"] == tools[0]["function"]["parameters"]

    def test_tool_without_description(self):
        tools = [{"type": "function", "function": {"name": "noop", "parameters": {}}}]
        result = _convert_tools(tools)
        assert result[0]["name"] == "noop"
        assert "description" not in result[0]

    def test_tool_without_parameters_gets_empty_schema(self):
        tools = [{"type": "function", "function": {"name": "ping"}}]
        result = _convert_tools(tools)
        assert result[0]["input_schema"] == {"type": "object", "properties": {}}

    def test_non_function_tools_are_skipped(self):
        tools = [{"type": "retrieval"}, {"type": "code_interpreter"}]
        result = _convert_tools(tools)
        assert result == []

    def test_multiple_tools(self):
        tools = [
            {"type": "function", "function": {"name": "a"}},
            {"type": "function", "function": {"name": "b"}},
        ]
        result = _convert_tools(tools)
        assert [t["name"] for t in result] == ["a", "b"]

    def test_empty_list(self):
        assert _convert_tools([]) == []


# ─────────────────────────────────────────────────────────────
# 2. Tool Choice Mapping
# ─────────────────────────────────────────────────────────────

class TestConvertToolChoice:
    def test_auto(self):
        assert _convert_tool_choice("auto") == {"type": "auto"}

    def test_required_maps_to_any(self):
        assert _convert_tool_choice("required") == {"type": "any"}

    def test_none_returns_none(self):
        assert _convert_tool_choice("none") is None

    def test_null_returns_none(self):
        assert _convert_tool_choice(None) is None

    def test_specific_function(self):
        choice = {"type": "function", "function": {"name": "get_weather"}}
        result = _convert_tool_choice(choice)
        assert result == {"type": "tool", "name": "get_weather"}

    def test_unknown_string_defaults_to_auto(self):
        result = _convert_tool_choice("something_else")
        assert result == {"type": "auto"}


# ─────────────────────────────────────────────────────────────
# 3. Tool Call ID Normalization
# ─────────────────────────────────────────────────────────────

class TestNormalizeToolId:
    def test_already_valid(self):
        assert _normalize_tool_id("call_abc123") == "call_abc123"

    def test_replaces_invalid_chars(self):
        result = _normalize_tool_id("call.abc/def")
        assert all(c.isalnum() or c in "_-" for c in result)

    def test_truncates_to_64(self):
        long_id = "a" * 100
        assert len(_normalize_tool_id(long_id)) == 64

    def test_empty_string_generates_fallback(self):
        result = _normalize_tool_id("")
        assert result  # non-empty
        assert all(c.isalnum() or c in "_-" for c in result)

    def test_with_spaces(self):
        result = _normalize_tool_id("call abc")
        assert " " not in result

    def test_collision_avoidance_with_mapping(self):
        id_map = {}
        used = set()
        first = _normalize_tool_id("call/abc", id_map=id_map, used_ids=used)
        second = _normalize_tool_id("call:abc", id_map=id_map, used_ids=used)
        assert first != second

    def test_stable_roundtrip_for_same_original_id(self):
        id_map = {}
        used = set()
        first = _normalize_tool_id("tool call:42", id_map=id_map, used_ids=used)
        second = _normalize_tool_id("tool call:42", id_map=id_map, used_ids=used)
        assert first == second


# ─────────────────────────────────────────────────────────────
# 4. Content Conversion (Image + Text)
# ─────────────────────────────────────────────────────────────

class TestConvertContentToAnthropic:
    def test_plain_string(self):
        blocks = _convert_content_to_anthropic("hello")
        assert blocks == [{"type": "text", "text": "hello"}]

    def test_list_with_text_part(self):
        blocks = _convert_content_to_anthropic([{"type": "text", "text": "hi"}])
        assert blocks == [{"type": "text", "text": "hi"}]

    def test_data_uri_image(self):
        url = "data:image/png;base64,abc123=="
        blocks = _convert_content_to_anthropic([{"type": "image_url", "image_url": {"url": url}}])
        assert len(blocks) == 1
        b = blocks[0]
        assert b["type"] == "image"
        assert b["source"]["type"] == "base64"
        assert b["source"]["media_type"] == "image/png"
        assert b["source"]["data"] == "abc123=="

    def test_https_url_image(self):
        url = "https://example.com/photo.jpg"
        blocks = _convert_content_to_anthropic([{"type": "image_url", "image_url": {"url": url}}])
        assert len(blocks) == 1
        b = blocks[0]
        assert b["type"] == "image"
        assert b["source"]["type"] == "url"
        assert b["source"]["url"] == url

    def test_mixed_text_and_image(self):
        content = [
            {"type": "text", "text": "Describe this:"},
            {"type": "image_url", "image_url": {"url": "https://img.example.com/1.png"}},
        ]
        blocks = _convert_content_to_anthropic(content)
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "image"

    def test_empty_list(self):
        assert _convert_content_to_anthropic([]) == []

    def test_empty_string_returns_no_blocks(self):
        # Empty text should not produce empty text blocks
        blocks = _convert_content_to_anthropic([{"type": "text", "text": ""}])
        assert blocks == []


# ─────────────────────────────────────────────────────────────
# 5. Message Conversion (_convert_messages)
# ─────────────────────────────────────────────────────────────

class TestConvertMessages:
    def setup_method(self):
        self.provider = _make_provider()

    def test_system_message_extracted(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, out = self.provider._convert_messages(messages)
        assert system == "You are helpful."
        assert len(out) == 1
        assert out[0]["role"] == "user"

    def test_simple_user_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        system, out = self.provider._convert_messages(messages)
        assert system == ""
        assert out == [{"role": "user", "content": "Hello"}]

    def test_simple_assistant_message(self):
        messages = [{"role": "assistant", "content": "Hi there"}]
        _, out = self.provider._convert_messages(messages)
        assert out == [{"role": "assistant", "content": "Hi there"}]

    def test_tool_result_wraps_in_tool_result_block(self):
        messages = [
            {"role": "tool", "tool_call_id": "call_abc", "content": "72°F, sunny"},
        ]
        _, out = self.provider._convert_messages(messages)
        assert len(out) == 1
        assert out[0]["role"] == "user"
        assert isinstance(out[0]["content"], list)
        block = out[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "call_abc"
        assert block["content"] == "72°F, sunny"

    def test_multiple_tool_results_merged_into_single_user_turn(self):
        messages = [
            {"role": "tool", "tool_call_id": "call_1", "content": "result-1"},
            {"role": "tool", "tool_call_id": "call_2", "content": "result-2"},
        ]
        _, out = self.provider._convert_messages(messages)
        # Both tool results should be in a single user message
        assert len(out) == 1
        assert out[0]["role"] == "user"
        assert len(out[0]["content"]) == 2
        ids = {b["tool_use_id"] for b in out[0]["content"]}
        assert ids == {"call_1", "call_2"}

    def test_assistant_with_tool_calls_converted_to_tool_use_blocks(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"location": "Paris"}'},
                    }
                ],
            }
        ]
        _, out = self.provider._convert_messages(messages)
        assert len(out) == 1
        msg = out[0]
        assert msg["role"] == "assistant"
        # Content should be a list with a tool_use block
        content = msg["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "tool_use"
        assert content[0]["id"] == "call_abc"
        assert content[0]["name"] == "get_weather"
        assert content[0]["input"] == {"location": "Paris"}

    def test_tool_id_mapping_restores_original_ids_in_response(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call.abc/def",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"location":"Paris"}'},
                    }
                ],
            }
        ]

        tool_id_map = {}
        _, out = self.provider._convert_messages(messages, tool_id_map=tool_id_map)
        normalized_tool_id = out[0]["content"][0]["id"]
        assert normalized_tool_id != "call.abc/def"

        response = self.provider._convert_response(
            {
                "id": "msg_1",
                "model": "claude-test",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": normalized_tool_id, "name": "get_weather", "input": {"location": "Paris"}}
                ],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
            fallback_model="claude-test",
            tool_id_reverse_map=_invert_tool_id_map(tool_id_map),
        )

        tool_calls = response["choices"][0]["message"]["tool_calls"]
        assert tool_calls[0]["id"] == "call.abc/def"

    def test_assistant_text_plus_tool_call(self):
        messages = [
            {
                "role": "assistant",
                "content": "Let me check that for you.",
                "tool_calls": [
                    {
                        "id": "call_xyz",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"q": "test"}'},
                    }
                ],
            }
        ]
        _, out = self.provider._convert_messages(messages)
        content = out[0]["content"]
        assert isinstance(content, list)
        types = [b["type"] for b in content]
        assert "text" in types
        assert "tool_use" in types

    def test_multi_turn_agentic_conversation(self):
        """Full round-trip: user → assistant tool_call → tool result → assistant reply."""
        messages = [
            {"role": "user", "content": "What is the weather in Paris?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"location":"Paris"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_abc", "content": "72°F, sunny"},
            {"role": "assistant", "content": "It's 72°F and sunny in Paris."},
        ]
        _, out = self.provider._convert_messages(messages)
        roles = [m["role"] for m in out]
        assert roles == ["user", "assistant", "user", "assistant"]

        # The second user turn should contain the tool_result
        user_turn = out[2]
        assert isinstance(user_turn["content"], list)
        assert user_turn["content"][0]["type"] == "tool_result"

    def test_invalid_role_falls_back_to_user(self):
        messages = [{"role": "function", "content": "some output"}]
        _, out = self.provider._convert_messages(messages)
        assert len(out) == 1
        assert out[0]["role"] == "user"

    def test_empty_assistant_message_skipped(self):
        messages = [{"role": "assistant", "content": None}]
        _, out = self.provider._convert_messages(messages)
        assert out == []


# ─────────────────────────────────────────────────────────────
# 6. Response Conversion
# ─────────────────────────────────────────────────────────────

class TestConvertResponse:
    def test_text_response(self):
        data = {
            "id": "msg_abc",
            "model": "claude-haiku-4-5-20251001",
            "content": [{"type": "text", "text": "Hello!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        result = AnthropicCompatibleProvider._convert_response(data, fallback_model="haiku")
        assert result["id"] == "msg_abc"
        assert result["choices"][0]["message"]["content"] == "Hello!"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 5

    def test_tool_use_response(self):
        data = {
            "id": "msg_xyz",
            "model": "claude-haiku-4-5-20251001",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "get_weather",
                    "input": {"location": "Paris"},
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 20, "output_tokens": 15},
        }
        result = AnthropicCompatibleProvider._convert_response(data, fallback_model="haiku")
        assert result["choices"][0]["finish_reason"] == "tool_calls"
        tool_calls = result["choices"][0]["message"]["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "toolu_01"
        assert tool_calls[0]["function"]["name"] == "get_weather"
        assert json.loads(tool_calls[0]["function"]["arguments"]) == {"location": "Paris"}

    def test_finish_reason_mapping(self):
        assert AnthropicCompatibleProvider._map_finish_reason("max_tokens") == "length"
        assert AnthropicCompatibleProvider._map_finish_reason("tool_use") == "tool_calls"
        assert AnthropicCompatibleProvider._map_finish_reason("end_turn") == "stop"
        assert AnthropicCompatibleProvider._map_finish_reason("stop_sequence") == "stop"


# ─────────────────────────────────────────────────────────────
# 7. Live Integration Tests (against running server at port 8000)
# ─────────────────────────────────────────────────────────────

LIVE = os.environ.get("OPEN_LLM_AUTH_LIVE_TESTS", "1") == "1"
pytest_live = pytest.mark.skipif(not LIVE, reason="live server not available")


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
class TestLiveAnthropicAdapter:
    """Integration tests through the open_llm_auth router against the anthropic provider."""

    async def _chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{ROUTER_URL}/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    async def _stream_chunks(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Collect all SSE chunks from a streaming request."""
        import httpx
        chunks = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{ROUTER_URL}/chat/completions",
                json={**payload, "stream": True},
                headers={"Content-Type": "application/json"},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data and data != "[DONE]":
                            chunks.append(json.loads(data))
        return chunks

    @pytest_live
    async def test_basic_completion(self):
        """Basic non-streaming completion returns valid OpenAI-shaped response."""
        result = await self._chat({
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "Reply with exactly one word: ACK"}],
            "max_tokens": 20,
        })
        assert result["object"] == "chat.completion"
        assert result["choices"][0]["message"]["role"] == "assistant"
        text = result["choices"][0]["message"]["content"]
        assert isinstance(text, str) and len(text) > 0

    @pytest_live
    async def test_tool_call_response(self):
        """Model calls a tool when tool_choice=required; response has tool_calls."""
        result = await self._chat({
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "What is the weather in Tokyo? Use the get_weather tool."}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Returns current weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string", "description": "City name"}
                            },
                            "required": ["location"],
                        },
                    },
                }
            ],
            "tool_choice": "required",
            "max_tokens": 200,
        })
        choice = result["choices"][0]
        assert choice["finish_reason"] == "tool_calls", (
            f"Expected finish_reason='tool_calls', got '{choice['finish_reason']}'. "
            f"Content: {choice['message'].get('content')}"
        )
        tool_calls = choice["message"].get("tool_calls", [])
        assert len(tool_calls) >= 1
        tc = tool_calls[0]
        assert tc["function"]["name"] == "get_weather"
        args = json.loads(tc["function"]["arguments"])
        assert "location" in args

    @pytest_live
    async def test_multi_turn_tool_result(self):
        """Full agentic loop: user → assistant tool_call → tool_result → assistant reply."""
        # Step 1: get tool call
        step1 = await self._chat({
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "Get the weather in Paris. Use get_weather."}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Returns current weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {"location": {"type": "string"}},
                            "required": ["location"],
                        },
                    },
                }
            ],
            "tool_choice": "required",
            "max_tokens": 200,
        })
        assert step1["choices"][0]["finish_reason"] == "tool_calls"
        tc = step1["choices"][0]["message"]["tool_calls"][0]

        # Step 2: send tool result back
        messages = [
            {"role": "user", "content": "Get the weather in Paris. Use get_weather."},
            {
                "role": "assistant",
                "content": step1["choices"][0]["message"].get("content") or "",
                "tool_calls": step1["choices"][0]["message"]["tool_calls"],
            },
            {"role": "tool", "tool_call_id": tc["id"], "content": "22°C, clear sky"},
        ]
        step2 = await self._chat({
            "model": ANTHROPIC_MODEL,
            "messages": messages,
            "max_tokens": 200,
        })
        text = step2["choices"][0]["message"]["content"]
        assert isinstance(text, str) and len(text) > 0
        # The model should mention the weather result
        assert any(w in text.lower() for w in ["22", "clear", "paris", "weather", "celsius"])

    @pytest_live
    async def test_streaming_basic(self):
        """Streaming response emits role chunk then content chunks then [DONE]."""
        chunks = await self._stream_chunks({
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 30,
        })
        assert len(chunks) > 0
        # First chunk should have role
        first = chunks[0]
        assert first["object"] == "chat.completion.chunk"
        assert first["choices"][0]["delta"].get("role") == "assistant"

        # Collect all text
        text = "".join(
            c["choices"][0]["delta"].get("content", "") or ""
            for c in chunks
        )
        assert len(text) > 0

        # Last data chunk before [DONE] should have finish_reason
        finish_chunks = [c for c in chunks if c["choices"][0].get("finish_reason")]
        assert len(finish_chunks) >= 1

    @pytest_live
    async def test_streaming_tool_call(self):
        """Streaming tool call emits tool_calls delta chunks."""
        chunks = await self._stream_chunks({
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "Use multiply to compute 6 * 7."}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "multiply",
                        "description": "Multiplies two numbers",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "number"},
                                "b": {"type": "number"},
                            },
                            "required": ["a", "b"],
                        },
                    },
                }
            ],
            "tool_choice": "required",
            "max_tokens": 200,
        })
        # Find any chunk that has tool_calls delta
        tool_chunks = [
            c for c in chunks
            if c["choices"][0]["delta"].get("tool_calls")
        ]
        assert len(tool_chunks) > 0, (
            "No tool_calls delta chunks found in streaming response. "
            f"Got chunks: {[c['choices'][0]['delta'] for c in chunks[:5]]}"
        )

        # First tool_calls chunk should have function name
        first_tc_chunk = tool_chunks[0]
        tc_delta = first_tc_chunk["choices"][0]["delta"]["tool_calls"][0]
        assert tc_delta.get("function", {}).get("name") == "multiply"

        # Collect all argument fragments
        args_fragments = "".join(
            c["choices"][0]["delta"].get("tool_calls", [{}])[0].get("function", {}).get("arguments", "")
            for c in chunks
            if c["choices"][0]["delta"].get("tool_calls")
        )
        # Arguments JSON should be valid and contain 6 and 7
        if args_fragments:
            args = json.loads(args_fragments)
            values = list(args.values())
            assert set(values) == {6, 7} or set(values) == {6.0, 7.0}

    @pytest_live
    async def test_system_message_extraction(self):
        """System messages are extracted and forwarded as Anthropic system param."""
        result = await self._chat({
            "model": ANTHROPIC_MODEL,
            "messages": [
                {"role": "system", "content": "Always respond in exactly 3 words."},
                {"role": "user", "content": "How are you?"},
            ],
            "max_tokens": 20,
        })
        text = result["choices"][0]["message"]["content"]
        # The model should try to follow the instruction
        assert isinstance(text, str) and len(text) > 0

    @pytest_live
    async def test_tool_choice_none_omits_tools(self):
        """tool_choice='none' should result in a plain text response."""
        result = await self._chat({
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "description": "Compute math",
                        "parameters": {
                            "type": "object",
                            "properties": {"expr": {"type": "string"}},
                        },
                    },
                }
            ],
            "tool_choice": "none",
            "max_tokens": 50,
        })
        choice = result["choices"][0]
        # Should be a stop/end_turn, not a tool_calls finish
        assert choice["finish_reason"] != "tool_calls"
        assert "tool_calls" not in choice["message"] or choice["message"]["tool_calls"] is None
