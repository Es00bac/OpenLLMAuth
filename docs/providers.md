# Providers

OpenLLMAuth resolves provider/model references through
`src/open_llm_auth/auth/manager.py` and the built-in catalog in
`src/open_llm_auth/provider_catalog.py`.

## Provider Reference Format

Prefer explicit provider/model references:

```text
provider/model
```

Examples:

```text
openai/gpt-5.2
anthropic/claude-sonnet-4-6
kimi-coding/k2p5
ollama/llama3.1
```

Bare model IDs are accepted only when provider inference is unambiguous or a
small built-in heuristic can safely infer the provider.

## Built-In Provider Families

The catalog includes these provider families:

- OpenAI-compatible: `openai`, `google`, `moonshot`, `openrouter`, `together`,
  `huggingface`, `mistral`, `venice`, `nvidia`, `qianfan`, `volcengine`,
  `byteplus`, `kilocode`, `opencode`, `vercel-ai-gateway`, `litellm`, `ollama`,
  and `vllm`.
- Anthropic-compatible: `anthropic`, `kimi-coding`, `minimax`,
  `minimax-portal`, `xiaomi`, `synthetic`, and `cloudflare-ai-gateway`.
- Codex and coding surfaces: `openai-codex`, `claude-cli`, and `codex-cli`.
- GitHub Copilot: `github-copilot`.
- AWS Bedrock Converse: `amazon-bedrock`.
- Local agent runtime bridge: `agent_bridge` and the `agent` alias.

## Credential Resolution

For a requested model, the provider manager resolves credentials in this order:

1. Explicit preferred profile, if supplied.
2. Configured auth-order list for the provider.
3. Discovered profiles for the provider.
4. Provider-specific environment variables.
5. Provider config `api_key`.
6. Special provider paths such as AWS SDK or CLI-backed providers.

## OAuth and Device Login Helpers

OpenLLMAuth includes CLI helpers for providers that need interactive auth:

```bash
uv run open-llm-auth auth login-openai-codex
uv run open-llm-auth auth login-github-copilot
```

You can also add token and OAuth profiles manually:

```bash
uv run open-llm-auth auth add-token PROVIDER --profile default
uv run open-llm-auth auth add-oauth PROVIDER --profile default
```

## Local Providers

`ollama`, `vllm`, and `litellm` point at local OpenAI-compatible endpoints by
default. They do not become active simply because they are listed in the
catalog; the backing local service must be running and the provider must resolve
under the current auth/config rules.

## Agent Bridge Provider

`agent_bridge` is a local runtime bridge rather than a remote model provider.
It can proxy normal chat calls and lifecycle task calls to a compatible local
agent runtime.

See [Agent Bridge Integration](agent-bridge.md).
