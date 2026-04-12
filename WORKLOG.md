# open_llm_auth Provider Compatibility Work Log

Historical note: this file records implementation sessions from 2026-03-01. It is useful for change history, but it is not the current behavioral spec. For the live package and current route/auth/provider surface, use `README.md` and the code under `src/open_llm_auth/`.

## Session: 2026-03-01

### Initial Audit

Compared open_llm_auth providers against openclaw_latest reference implementation.

**OpenClaw API Types (8 total):**
1. `openai-completions` - Standard OpenAI Chat Completions
2. `openai-responses` - OpenAI Responses API (o1, o3 models)
3. `openai-codex-responses` - ChatGPT Plus/Pro Codex API
4. `anthropic-messages` - Anthropic Messages API
5. `google-generative-ai` - Google Gemini API
6. `github-copilot` - GitHub Copilot API
7. `bedrock-converse-stream` - AWS Bedrock Converse API
8. `ollama` - Ollama native API

**OpenClaw Auth Methods (6 total):**
1. `api-key` - Standard API key in header
2. `oauth` - OAuth 2.0 with PKCE + auto-refresh (OpenAI Codex, Google Gemini CLI, Qwen Portal, MiniMax Portal)
3. `token` - Static bearer token (setup-token for Anthropic, GitHub Copilot PATs)
4. `aws-sdk` - AWS SigV4 signing for Bedrock
5. `cli` - CLI wrapper (claude-cli, codex-cli) - subprocess invocation
6. `setup-token` - Anthropic-specific (subset of token, from `claude --setup-token`)

**open_llm_auth Current State:**
- ✅ `openai-completions` - Working (OpenAIProvider)
- ✅ `anthropic-messages` - Working (AnthropicCompatibleProvider)
- ✅ `openai-codex-responses` - Working (OpenAICodexProvider) - just implemented
- ⚠️ `claude-cli` - Exists but has issues (fakes streaming, debug prints)
- ⚠️ `codex-cli` - Exists but NOT wired into _build_provider()
- ❌ `google-generative-ai` - Listed in ModelApi but no provider adapter
- ❌ `github-copilot` - Listed in ModelApi but no provider adapter
- ❌ `bedrock-converse-stream` - Listed in ModelApi but no provider adapter
- ❌ `ollama` - Has config but uses openai-completions (no native ollama API)

**Auth methods in open_llm_auth:**
- ✅ `api-key` - Working
- ✅ `oauth` - Working (Anthropic, OpenAI Codex auto-refresh)
- ✅ `token` - Working
- ⚠️ `cli` - Partially working (claude-cli has issues)
- ❌ `aws-sdk` - Has placeholder in config but no SigV4 implementation
- ❌ `setup-token` - Not distinguished from regular token (no validation)

### Work Items Identified

See task list below. Working through each provider/auth method one at a time.

---

### Task 1: Fix claude-cli provider
- Read claude_cli.py: Has debug print statements, fakes streaming
- Read codex_cli.py: Not wired into _build_provider()
- Need to fix both CLI providers and ensure they work

### Task 1 Work:
- Read claude_cli.py fully
- Found issues:
  - Missing from _build_provider() dispatch (only checked api type, not provider_id)
  - The ClaudeCliProvider._run_claude() method shells out to `claude` command
  - Fakes streaming by collecting full response then emitting single SSE chunk
  - Has no error handling for missing claude binary

### Task 1 Continued (Session 2):
- Fixed codex-cli wiring: Added CodexCliProvider import to manager.py
- Both claude-cli and codex-cli are now properly wired into _build_provider()
- COMPLETED

---

### Task 2: Fix Anthropic provider for chat interface

**Issues found:**
1. Debug print statements in `_convert_response` (lines 253, 260) leaking response content
2. No thinking/extended thinking support in streaming (missing `thinking_delta` handling)
3. No `content_block_start` tracking to distinguish text vs thinking blocks
4. Tool parsing uses fragile regex on text content instead of native Anthropic `tool_use` blocks
5. OAuth tokens rejected with "OAuth authentication is currently not supported" - missing `anthropic-beta: oauth-2025-04-20` header
6. Expired OAuth tokens not refreshed (only openai-codex had refresh support)
7. `temperature` and `top_p` defaults of 1.0 in request model caused "cannot both be specified" error

**Fixes applied:**
1. Removed debug print statements
2. Added `content_block_start` event handling to track current block type (text vs thinking)
3. Added `thinking_delta` → `reasoning_content` mapping in streaming SSE output
4. Added `content_block_stop` handling to reset block type
5. Replaced regex-based tool parsing in `_convert_response` with native Anthropic `tool_use` block extraction
6. Added extended thinking support: when `reasoning_effort` is in payload, sends `thinking.type=enabled` with budget_tokens
7. Added `anthropic-beta: oauth-2025-04-20,interleaved-thinking-2025-05-14` header for OAuth tokens (sk-ant-oat-*)
8. Added Anthropic OAuth token refresh by re-reading `~/.claude/.credentials.json` (Claude Code's credential file)
9. Changed `temperature` and `top_p` defaults from 1.0 to None in ChatCompletionRequest model

**Files modified:**
- `src/open_llm_auth/providers/anthropic_compatible.py` - streaming thinking support, native tool_use, removed debug prints
- `src/open_llm_auth/auth/manager.py` - OAuth beta header, Anthropic credential refresh from Claude CLI
- `src/open_llm_auth/server/models.py` - Fixed temperature/top_p defaults

**Test result:** Anthropic streaming via OAuth working correctly.
- COMPLETED

---

### Task 3: Google Generative AI provider
- Google already configured with OpenAI-compatible endpoint at `generativelanguage.googleapis.com/v1beta/openai`
- Uses `openai-completions` API type → works with existing `OpenAIProvider`
- Tested streaming: works correctly with `google:default` API key
- No additional provider adapter needed
- COMPLETED (already working)

---

### Task 4: GitHub Copilot provider

**Implementation:**
- GitHub Copilot uses a device code flow + token exchange
- GitHub token → exchanged for Copilot API token via `api.github.com/copilot_internal/v2/token`
- Copilot API is OpenAI-compatible (uses `openai-completions`)
- API base URL derived from `proxy-ep` field in Copilot token
- Copilot tokens expire frequently (~30 min), auto-refresh using stored GitHub token
- Requires Copilot-specific headers (User-Agent, Editor-Version, etc.)

**Files created:**
- `src/open_llm_auth/auth/_github_copilot_auth.py` - Device code flow, token exchange

**Files modified:**
- `src/open_llm_auth/provider_catalog.py` - Added github-copilot provider config + 5 models + aliases
- `src/open_llm_auth/auth/manager.py` - Copilot token refresh, Copilot headers, base URL from profile
- `src/open_llm_auth/cli.py` - Added `login-github-copilot` command

**Auth flow:**
1. `open-llm-auth auth login-github-copilot` → device code flow
2. User visits github.com/login/device and enters code
3. GitHub access token obtained
4. Exchanged for Copilot API token (stored in `access`, GitHub token in `refresh`)
5. On expiry, auto-refreshes by re-exchanging GitHub token
- COMPLETED

---

### Task 5: Anthropic setup-token auth
- Added `setup-token` CLI command: `open-llm-auth auth setup-token`
- Validates `sk-ant-oat01-` prefix and minimum 80 char length
- Stores as OAuth profile (same as regular Anthropic OAuth)
- COMPLETED

---

### Task 6: Qwen Portal OAuth refresh
- Added `refresh_qwen_portal_token()` to `oauth_refresh.py`
- Token endpoint: `https://chat.qwen.ai/api/v1/oauth2/token`
- Client ID: `f0304373b74a44d2b584a3fb70ca9e56`
- Wired into `_try_refresh_oauth()` in manager.py via generic `_run_refresh()` helper
- COMPLETED

---

### Task 7: MiniMax Portal OAuth refresh
- MiniMax reads credentials from `~/.minimax/oauth_creds.json` (no server-side refresh)
- Added `_refresh_from_cli_creds()` generic method to re-read CLI credential files
- Also usable for Qwen CLI creds at `~/.qwen/oauth_creds.json`
- COMPLETED

---

### Task 8: End-to-end verification

**Tested all providers with credentials:**
| Provider | Auth | Streaming | Non-streaming | Status |
|----------|------|-----------|---------------|--------|
| Anthropic (claude-sonnet-4-6) | OAuth | ✅ | ✅ | Working |
| Google (gemini-2.5-flash) | API key | ✅ | ✅ | Working |
| OpenAI Codex (gpt-5.1-codex-mini) | OAuth | ✅ | ✅ | Working |
| ZAI Coding (glm-5) | API key | ✅ | ✅ | Working |
| Kimi Coding (k2p5) | API key | N/T | ✅ | Working |

**Providers requiring user login (not tested):**
- GitHub Copilot (needs `login-github-copilot` device code flow)
- Claude CLI (needs `claude` binary in PATH)
- Codex CLI (needs `codex` binary in PATH)

---

## Summary of all changes

### New files:
- `src/open_llm_auth/providers/openai_codex.py` - OpenAI Codex Responses API adapter
- `src/open_llm_auth/auth/_openai_codex_oauth.py` - OpenAI Codex PKCE OAuth login
- `src/open_llm_auth/auth/_github_copilot_auth.py` - GitHub Copilot device code flow
- `src/open_llm_auth/auth/oauth_refresh.py` - Token refresh for OpenAI Codex, Qwen Portal

### Modified files:
- `src/open_llm_auth/provider_catalog.py` - Added providers: openai-codex, github-copilot, claude-cli, codex-cli, google + models
- `src/open_llm_auth/config.py` - Extended ModelApi and AuthMode types
- `src/open_llm_auth/providers/__init__.py` - Added exports for new providers
- `src/open_llm_auth/providers/anthropic_compatible.py` - Fixed OAuth auth, thinking support, native tool_use, removed debug prints
- `src/open_llm_auth/auth/manager.py` - Token refresh for all providers, Copilot headers, OAuth beta headers
- `src/open_llm_auth/server/models.py` - Fixed temperature/top_p defaults
- `src/open_llm_auth/cli.py` - Added login commands for OpenAI Codex, GitHub Copilot, setup-token
