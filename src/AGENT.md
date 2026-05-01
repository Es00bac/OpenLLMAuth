# Open LLM Auth Agent Guide

This file is for agents that need to call the live gateway rather than edit it.

## Active Code Boundary

Use the package under `open_llm_auth` when reasoning about current behavior.

## Network Surfaces

Top-level HTTP surfaces:
- `GET /health`
- `GET /`
- `GET /chat`
- `GET /config`
- `POST /v1/chat/completions`
- `GET /v1/models`
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
- `/config/*` admin endpoints for provider/profile/config inspection

## Authentication Rules

Every protected route expects `Authorization: Bearer <token>`.

Token sources, in order of significance:
1. configured scoped tokens from `authorization.tokens`
2. legacy admin compatibility via `serverToken` or `OPEN_LLM_AUTH_TOKEN`
3. anonymous admin mode only if `OPEN_LLM_AUTH_ALLOW_ANON=1` and no configured/legacy token exists

Scope expectations:
- `write` is required for chat and task mutations
- `admin` is required for `/config/*`
- `admin=true` on a configured token implies `read`, `write`, and `admin`

## OpenAI-Compatible Calls

Standard chat endpoint:

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-5.2",
    "messages": [{"role":"user","content":"hello"}]
  }'
```

Model resolution rules:
- preferred form is `provider/model`
- provider aliases are normalized
- bare model IDs are only accepted when provider inference is unambiguous or matches a small heuristic fallback

## Universal Task API

Use the universal routes when the caller wants normalized task lifecycle behavior rather than raw provider output.

Create a task:

```bash
curl http://127.0.0.1:8080/v1/universal/tasks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: task-123" \
  -d '{
    "provider": "agent_bridge",
    "task": {"objective": "Inspect the runtime health endpoints."}
  }'
```

Important invariants:
- task ownership is tracked durably per `(provider, task_id)`
- non-admin callers can only read/mutate their own tasks
- `Idempotency-Key` is actor-scoped and fingerprinted against the request payload
- mutating Agent Bridge task routes are gated by task-contract compatibility checks

## Agent Bridge-Specific Behavior

The `agent_bridge` and `agent` providers are local-runtime bridges.

Current behavior:
- normal chat requests proxy to Agent Bridge `POST /chat`
- task requests proxy to Agent Bridge agent endpoints
- mutating task calls attach contract headers
- streaming is synthesized by polling task state and task-event history
- because Agent Bridge chat is single-turn, the gateway folds bounded transcript context into a synthetic system block before forwarding

## Config/Admin API

Admin routes are under `/config`.

Useful endpoints:
- `GET /config`
- `GET /config/builtin-providers`
- `GET /config/providers`
- `GET /config/auth-profiles`
- `GET /config/configured-providers`
- `GET /config/providers/{provider_id}/models`

Config responses redact secrets. Do not expect raw bearer tokens, API keys, refresh tokens, or `serverToken` values to round-trip through the API.

## Failure Modes To Expect

- `401`: missing/invalid bearer token, or no server token configured and anonymous access disabled
- `403`: insufficient scope, egress policy denial, or blocked ownership access
- `404`: provider not configured or task not found
- `409`: idempotency conflict/in-progress replay rules, or task-contract mismatch on mutating task routes
- `503`: durable control-state store unavailable when required for universal-task handling

## Source Files Worth Reading

- `open_llm_auth/server/auth.py`
- `open_llm_auth/server/routes.py`
- `open_llm_auth/server/task_contract.py`
- `open_llm_auth/server/idempotency.py`
- `open_llm_auth/auth/manager.py`
- `open_llm_auth/providers/agent_bridge.py`
- `open_llm_auth/config.py`
