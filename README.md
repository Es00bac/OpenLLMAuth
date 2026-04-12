# Open LLM Auth

Open LLM Auth is a local gateway that exposes:
- an OpenAI-compatible chat surface,
- a universal task surface for agent/runtime backends,
- centralized auth/profile resolution,
- scoped access control,
- outbound egress enforcement,
- durable task ownership and idempotency state.

The active package is [`src/open_llm_auth`](./src/open_llm_auth). Duplicate trees such as `src/src/open_llm_auth` and packaged artifacts under `pkg/` are not the live source of truth for current development.

## Current Architecture

Live entrypoints:
- [`src/open_llm_auth/main.py`](./src/open_llm_auth/main.py): FastAPI app, static UI mount, root/chat/config pages
- [`src/open_llm_auth/server/routes.py`](./src/open_llm_auth/server/routes.py): `/v1/*` API routes
- [`src/open_llm_auth/server/config_routes.py`](./src/open_llm_auth/server/config_routes.py): `/config/*` admin/config routes
- [`src/open_llm_auth/cli.py`](./src/open_llm_auth/cli.py): Typer CLI

Core subsystems:
- `auth/manager.py`: provider resolution, profile/env/config credential lookup, fallback ordering, runtime egress validation
- `config.py`: persisted config model at `~/.open_llm_auth/config.json`
- `provider_catalog.py`: builtin provider/model catalog plus provider aliases and env-var lookup rules
- `server/auth.py`: bearer-token verification and scope enforcement
- `server/task_contract.py`: Agent Bridge task-contract compatibility checks
- `server/idempotency.py` and `server/durable_state.py`: in-memory and SQLite-backed idempotency/ownership primitives
- `providers/agent_bridge.py`: bridge to Agent Bridge chat and task lifecycle endpoints

## Installation

The repository already uses `uv` and a local venv in normal development.

```bash
cd /mnt/xtra/open_llm_auth
uv sync
```

Alternative editable install:

```bash
pip install -e .
```

## Running The Server

CLI:

```bash
open-llm-auth serve --host 127.0.0.1 --port 8080
```

Repo-local venv:

```bash
.venv/bin/python -m open_llm_auth.cli serve --host 127.0.0.1 --port 8080
```

Server surfaces:
- `GET /health`
- `GET /`
- `GET /chat`
- `GET /config`
- `GET /docs`
- static assets under `/static`

## Authentication Model

The gateway no longer assumes a single global server token only.

Current auth behavior from `src/open_llm_auth/server/auth.py`:
- configured access tokens live in `authorization.tokens`
- each token can carry scopes such as `read`, `write`, `admin`
- `admin=true` implies all three scopes
- legacy admin compatibility can still use `serverToken` or `OPEN_LLM_AUTH_TOKEN`
- `OPEN_LLM_AUTH_ALLOW_ANON=1` enables anonymous admin access only when no configured or legacy token exists
- config routes require admin scope
- task/chat routes generally require write scope

## Configuration Schema

Config file location:

```text
~/.open_llm_auth/config.json
```

Important top-level sections:
- `authProfiles` and `authOrder`
- compatibility mirrors: `auth.profiles` and `auth.order`
- `providers`
- `models.mode` and `models.providers`
- `authorization.tokens`
- `durableState`
- `egressPolicy`
- `taskContract`
- `defaultModel`
- `serverToken`

Current config behavior:
- secret-bearing fields are redacted in config API responses
- outbound provider base URLs are validated against egress policy both at config-write time and runtime resolution time
- durable task/idempotency state defaults to a SQLite file under `~/.open_llm_auth/runtime_state.sqlite3`

## CLI Surface

Top-level commands:
- `serve`
- `chat`
- `auth`
- `models`

Auth subcommands:
- `auth configure`
- `auth add-api-key`
- `auth add-token`
- `auth add-oauth`
- `auth login-openai-codex`
- `auth login-github-copilot`
- `auth setup-token`
- `auth set-order`
- `auth set-server-token`
- `auth list`

Model subcommands:
- `models set-default`
- `models list`

The CLI supports more than API-key management. It includes OAuth/device flows, provider fallback ordering, a default-model setter, and a small terminal chat mode.

## API Surface

### OpenAI-compatible surface

- `POST /v1/chat/completions`
- `GET /v1/models`

### Universal/task surface

- `POST /v1/universal`
- `POST /v1/universal/tasks`
- `GET /v1/universal/tasks`
- `GET /v1/universal/tasks/{task_id}`
- `POST /v1/universal/tasks/{task_id}/approve`
- `POST /v1/universal/tasks/{task_id}/retry`
- `POST /v1/universal/tasks/{task_id}/cancel`
- `GET /v1/universal/tasks/{task_id}/events`
- `POST /v1/universal/tasks/{task_id}/wait`
- `GET /v1/universal/contract/status`

### Config/admin surface

- `GET /config`
- `POST /config`
- `GET /config/builtin-providers`
- `GET /config/providers`
- `PUT /config/providers/{provider_id}`
- `DELETE /config/providers/{provider_id}`
- `GET /config/auth-profiles`
- `PUT /config/auth-profiles/{profile_id}`
- `DELETE /config/auth-profiles/{profile_id}`
- `GET /config/config-file-path`
- `GET /config/configured-providers`
- `GET /config/providers/{provider_id}/models`

## Provider Resolution Rules

`ProviderManager` merges builtin catalog entries with local config and then resolves credentials in this order:
- explicit preferred profile, if supplied,
- configured auth-order list for that provider,
- discovered profiles for that provider,
- provider-specific environment variables,
- provider config `api_key`,
- special auth paths such as AWS SDK or CLI-backed providers.

Important resolution behavior:
- provider aliases are normalized, for example `chatgpt -> openai-codex`, `codex -> openai-codex`, `bedrock -> amazon-bedrock`
- bare model IDs are inferred only when there is a unique match or a small heuristic fallback
- local backends such as `ollama`, `vllm`, and `amazon-bedrock` do not self-activate just because they exist in the catalog; they still need explicit config or usable credentials
- `agent_bridge` and `agent` are manager-defined local bridges, not entries in the builtin provider map

## Agent Bridge Bridge

The Agent Bridge adapter is more than a simple proxy.

Current behavior from `src/open_llm_auth/providers/agent_bridge.py`:
- base URL defaults to `http://127.0.0.1:20100/v1`
- standard chat requests call `POST /chat`
- task creation/status/retry/approve/cancel/list/events use the Agent Bridge agent lifecycle endpoints
- mutating task operations attach contract headers such as `X-Provider-Contract-Version`
- streaming task output is synthesized by polling task snapshots and task events and converting them into OpenAI-style SSE chunks
- plain chat requests rebuild a bounded context block from recent transcript turns because Agent Bridge's direct chat API is single-turn

## Security/Resilience Features

Implemented hardening that the older README did not describe:
- fail-closed auth when no token is configured
- optional scoped configured tokens instead of one all-powerful shared token
- egress policy with allow-local-provider exceptions and metadata-address denial
- durable task ownership checks for universal task routes
- durable idempotency keys for task mutations
- task-contract validation against Agent Bridge before mutating task routes
- sanitized upstream HTTP errors
- secret redaction in config responses

## Testing

The active test suite lives under [`tests`](./tests).

Important coverage areas:
- `tests/test_universal_gateway.py`
- `tests/test_gateway_security_hardening.py`
- `tests/test_provider_manager.py`
- `tests/test_agent_bridge_provider.py`
- `tests/test_bedrock_provider.py`
- `tests/test_anthropic_adapter.py`
- `tests/test_auth_manager_parsing.py`

Typical verification:

```bash
.venv/bin/pytest -q tests
```
