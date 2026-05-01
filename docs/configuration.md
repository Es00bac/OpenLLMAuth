# Configuration

OpenLLMAuth stores operator configuration in:

```text
~/.open_llm_auth/config.json
```

The config model is defined in `src/open_llm_auth/config.py`.

## Main Sections

- `authProfiles`: named credential profiles.
- `authOrder`: provider-specific fallback order for profiles.
- `providers`: custom provider definitions.
- `models.mode`: model catalog mode.
- `models.providers`: provider model overrides.
- `authorization.tokens`: scoped local API tokens.
- `durableState`: SQLite-backed task ownership and idempotency settings.
- `egressPolicy`: outbound URL safety policy.
- `taskContract`: Agent Bridge task-contract requirements.
- `defaultModel`: model used when callers omit a model.
- `serverToken`: legacy local admin token.

## Auth Profiles

Auth profiles are named credentials. Profile IDs usually use
`provider:name`, for example `openai:default`.

Supported credential types include:

- `api_key`
- `token`
- `oauth`
- `aws-sdk`
- `cli`

Common CLI commands:

```bash
uv run open-llm-auth auth add-api-key openai --profile default
uv run open-llm-auth auth add-token github-copilot --profile default
uv run open-llm-auth auth add-oauth openai-codex --profile default
uv run open-llm-auth auth login-openai-codex
uv run open-llm-auth auth login-github-copilot
uv run open-llm-auth auth set-order openai openai:default openai:backup
uv run open-llm-auth auth list
```

Secrets are redacted in config API responses. Do not expect raw API keys,
refresh tokens, bearer tokens, or server tokens to round-trip through the admin
API.

## Provider Definitions

Built-in providers live in `src/open_llm_auth/provider_catalog.py`. You can add
or override provider entries in `providers` or `models.providers`.

Typical provider fields:

```json
{
  "baseUrl": "https://provider.example/v1",
  "api": "openai-completions",
  "auth": "api-key"
}
```

Supported API adapter values are defined by `ModelApi` in
`src/open_llm_auth/config.py`.

## Environment Variables

Provider credentials can also come from provider-specific environment variables.
The provider catalog defines the exact environment variable names for each
provider.

Gateway controls:

- `OPEN_LLM_AUTH_TOKEN`: legacy local API token.
- `OPEN_LLM_AUTH_ALLOW_ANON=1`: allow anonymous admin access only when no
  configured or legacy token exists.

## Egress Policy

Provider base URLs are validated before use. The policy blocks unsafe outbound
destinations such as metadata-service addresses unless a provider is explicitly
allowed as a local provider.

Local providers such as `ollama`, `vllm`, and `litellm` are designed to route to
loopback services when configured intentionally.

## Durable State

Universal task ownership and idempotency use SQLite-backed durable state by
default. The default database is:

```text
~/.open_llm_auth/runtime_state.sqlite3
```

This state lets the gateway preserve task ownership, reject conflicting
idempotency replays, and keep task mutation behavior stable across restarts.
