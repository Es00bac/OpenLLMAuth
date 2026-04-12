# Anthropic Adapter — Change Report
**Date:** 2026-03-02
**Component:** `src/open_llm_auth/providers/anthropic_compatible.py`
**Affected providers:** `anthropic`, `minimax`, `minimax-portal`, `kimi-coding`, `xiaomi`, `synthetic`, `cloudflare-ai-gateway`

---

## Summary

The `AnthropicCompatibleProvider` adapter has been substantially upgraded to correctly translate the full OpenAI Chat Completions request/response surface into Anthropic's Messages API format. Previously the adapter dropped tool definitions, tool results, and image content entirely, which would have caused silent failures for any OpenBulma agent workflow that relies on function calling or multimodal input when routed through an Anthropic-backed provider.

---

## Changes

### 1. Tool Definitions (`tools` parameter)

**Before:** The `tools` array was silently dropped — Anthropic never received the tool schema, so it could not call any tools.

**After:** Each OpenAI tool definition is converted to Anthropic's format before the request is sent.

```
OpenAI                                  →  Anthropic
──────────────────────────────────────────────────────
tools[].type = "function"               →  (kept implicitly)
tools[].function.name                   →  tools[].name
tools[].function.description            →  tools[].description
tools[].function.parameters             →  tools[].input_schema
```

---

### 2. Tool Choice (`tool_choice` parameter)

**Before:** Dropped.

**After:** Mapped to the Anthropic equivalent:

| OpenAI `tool_choice` | Anthropic `tool_choice` |
|----------------------|-------------------------|
| `"auto"`             | `{"type": "auto"}`      |
| `"required"`         | `{"type": "any"}`       |
| `"none"`             | *(omitted — tools not sent)* |
| `{"type": "function", "function": {"name": "fn"}}` | `{"type": "tool", "name": "fn"}` |

---

### 3. Tool Result Messages (`role: "tool"`)

**Before:** Tool result messages were naively converted to a plain `user` text message, losing the `tool_call_id` linkage. Anthropic would have no idea which tool invocation the result belonged to, causing context corruption.

**After:** Tool results are properly wrapped in `tool_result` content blocks and merged into the preceding user turn:

```
OpenAI:
  { role: "tool", tool_call_id: "call_abc", content: "72°F, sunny" }

Anthropic:
  { role: "user", content: [
      { type: "tool_result", tool_use_id: "call_abc", content: "72°F, sunny" }
  ]}
```

Consecutive tool results from a multi-tool call are merged into a single user turn (required by the Anthropic API).

---

### 4. Assistant Messages Containing Tool Calls

**Before:** Only the text `content` of an assistant message was forwarded. Any `tool_calls` array on the assistant turn was dropped, breaking multi-turn agentic conversations.

**After:** `tool_calls` on assistant messages are converted to `tool_use` content blocks:

```
OpenAI:
  {
    role: "assistant",
    content: null,
    tool_calls: [{ id: "call_abc", function: { name: "get_weather", arguments: '{"location":"Paris"}' } }]
  }

Anthropic:
  {
    role: "assistant",
    content: [
      { type: "tool_use", id: "call_abc", name: "get_weather", input: { "location": "Paris" } }
    ]
  }
```

The `arguments` JSON string is parsed back into an object for the `input` field.

---

### 5. Tool Call ID Normalization

Anthropic enforces that tool/tool_use IDs match `[a-zA-Z0-9_-]{1,64}`. IDs arriving from upstream (e.g. containing spaces, slashes, or dots) are now sanitized to comply with this constraint before being sent.

---

### 6. Image Content (`image_url` parts)

**Before:** Image parts in multipart messages were silently discarded — only `text` parts were forwarded.

**After:** Image parts are converted to Anthropic `image` content blocks:

```
OpenAI:
  { type: "image_url", image_url: { url: "data:image/png;base64,abc..." } }

Anthropic:
  { type: "image", source: { type: "base64", media_type: "image/png", data: "abc..." } }
```

Both `data:` URI (base64) and plain HTTPS URL images are supported.

---

### 7. Streaming Tool Calls

**Before:** The streaming path had no handling for `tool_use` blocks — only `text_delta` and `thinking_delta` events were processed. A streaming request that triggered a tool call would produce an empty stream.

**After:** The streaming path now handles:

- `content_block_start` with `type: "tool_use"` → emits an OpenAI-compatible `tool_calls[].function.name` chunk with an empty `arguments` string.
- `content_block_delta` with `type: "input_json_delta"` → streams `partial_json` as incremental `tool_calls[].function.arguments` chunks.
- Multiple concurrent tool calls are tracked by index.

---

### 8. Role Chunk Fix

The initial `role: "assistant"` chunk in streaming responses now includes `"content": null` to match the OpenAI streaming spec (some clients reject a missing `content` field on the role chunk).

---

## Impact on OpenBulma v4

OpenBulma agents that use Anthropic-backed models (via `anthropic/`, `kimi-coding/`, or `minimax-portal/` routes) for function-calling workflows will now work correctly end-to-end:

- Tool schemas are delivered to the model.
- Tool invocations in the assistant turn are preserved in conversation history.
- Tool results are correctly attributed back to their originating call.
- Image inputs are forwarded rather than silently dropped.
- Streaming agentic loops no longer produce empty responses when a tool is called.

No changes to the gateway API surface, authentication, or routing logic were made. The fix is entirely internal to the adapter layer.

---

## Files Changed

| File | Change |
|------|--------|
| `src/open_llm_auth/providers/anthropic_compatible.py` | Full rewrite of `_convert_messages`, new `_convert_tools`, `_convert_tool_choice`, `_convert_content_to_anthropic`, `_normalize_tool_id` helpers; streaming tool call handling added |
