# API Reference

The running server exposes generated OpenAPI docs at `/docs`. This page
summarizes the route families that matter operationally.

## Health and UI

- `GET /health`
- `GET /`
- `GET /chat`
- `GET /docs`

## OpenAI-Compatible API

### Chat Completions

```http
POST /v1/chat/completions
```

Example:

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-5.2",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

The route accepts provider/model references such as `openai/gpt-5.2`.
Provider aliases are normalized by the provider manager.

### Embeddings

```http
POST /v1/embeddings
```

The embeddings route resolves the requested provider through the same provider
manager as chat completions.

### Models

```http
GET /v1/models
```

The model list is filtered through provider resolution, so it reflects built-in
catalog entries plus currently usable local credentials and provider config.

## Universal Task API

The universal task API is for agent/runtime backends that expose long-running
task lifecycle operations.

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

Task mutation routes use durable task ownership and idempotency checks. Include
an `Idempotency-Key` header for task creation and mutation calls where replay
protection matters.

## Config and Admin API

Admin routes are under `/config`.

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
- `GET /config/usage/summary`
- `GET /config/usage/chart`
- `GET /config/usage/providers`
- `GET /config/usage/models`
- `GET /config/usage/endpoints`
- `GET /config/usage/sources`
- `GET /config/usage/overview`
- `GET /config/usage/provider-telemetry`
- `GET /config/usage/recent`
- `POST /config/credentials/{profile_id}/test`
- `GET /config/profiles`
- `POST /config/profiles`
- `POST /config/profiles/{name}/activate`
- `POST /config/profiles/import`
- `GET /config/profiles/export/{name}`

Admin responses redact secret-bearing fields.

## Common Status Codes

- `401`: missing or invalid bearer token.
- `403`: insufficient scope, egress denial, or task ownership denial.
- `404`: unknown provider, route target, or task.
- `409`: idempotency conflict or task-contract mismatch.
- `503`: durable state unavailable for an operation that requires it.
