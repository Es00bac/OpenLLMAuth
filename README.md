# OpenLLMAuth

![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)
![Framework](https://img.shields.io/badge/framework-FastAPI-05998b)
![Surface](https://img.shields.io/badge/api-OpenAI--compatible-6b46c1)
![Status](https://img.shields.io/badge/status-active%20development-c97b18)

OpenLLMAuth is a local AI-provider gateway. It gives tools, agents, and scripts
one stable API for chat completions, model discovery, provider credentials,
runtime task execution, and policy enforcement.

The project is useful when you want local clients to call one endpoint while the
gateway handles provider selection, auth profiles, scoped access tokens,
outbound egress checks, usage accounting, and durable task ownership.

## What It Provides

- OpenAI-compatible `POST /v1/chat/completions` and `GET /v1/models`.
- A universal task API for agent runtimes and long-running work.
- A browser admin dashboard for providers, auth profiles, usage, and model
  selection.
- Provider credential resolution from config, environment variables, OAuth
  profiles, CLI-backed providers, and AWS SDK credentials.
- Scoped bearer-token access for read, write, and admin routes.
- Egress policy checks for outbound provider URLs.
- Durable SQLite-backed idempotency and task ownership state.
- Provider adapters for OpenAI-compatible, Anthropic-compatible, Codex,
  Bedrock Converse, CLI-backed, and local OpenAI-compatible runtimes.

## Quickstart

Requirements:

- Python 3.11 or newer
- `uv` recommended, or `pip` with a virtual environment

```bash
git clone https://github.com/Es00bac/OpenLLMAuth.git
cd OpenLLMAuth
uv sync
uv run open-llm-auth serve --host 127.0.0.1 --port 8080
```

If you are not using `uv`:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
open-llm-auth serve --host 127.0.0.1 --port 8080
```

Useful URLs after startup:

- `http://127.0.0.1:8080/` - admin dashboard
- `http://127.0.0.1:8080/chat` - simple browser chat UI
- `http://127.0.0.1:8080/docs` - FastAPI OpenAPI docs
- `http://127.0.0.1:8080/health` - health check

## First Provider

Add an API-key profile:

```bash
uv run open-llm-auth auth add-api-key openai --profile default
uv run open-llm-auth models set-default openai/gpt-5.2
uv run open-llm-auth auth set-server-token
uv run open-llm-auth models list
```

Start the server, then call the OpenAI-compatible endpoint with your local
server token:

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-5.2",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}]
  }'
```

Protected calls use the same bearer token:

```bash
curl http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer YOUR_LOCAL_TOKEN"
```

## Documentation

- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [API Reference](docs/api.md)
- [Providers](docs/providers.md)
- [Security Model](docs/security.md)
- [Agent Bridge Integration](docs/agent-bridge.md)
- [Development](docs/development.md)

## Source Layout

The active Python package is [`src/open_llm_auth`](src/open_llm_auth).

Important entrypoints:

- [`src/open_llm_auth/main.py`](src/open_llm_auth/main.py) - FastAPI app assembly
- [`src/open_llm_auth/cli.py`](src/open_llm_auth/cli.py) - Typer CLI
- [`src/open_llm_auth/server/routes.py`](src/open_llm_auth/server/routes.py) - `/v1/*` routes
- [`src/open_llm_auth/server/config_routes.py`](src/open_llm_auth/server/config_routes.py) - `/config/*` routes
- [`src/open_llm_auth/auth/manager.py`](src/open_llm_auth/auth/manager.py) - provider and credential resolution
- [`src/open_llm_auth/provider_catalog.py`](src/open_llm_auth/provider_catalog.py) - built-in provider and model catalog

## Supported Route Families

- `GET /health`
- `GET /`, `GET /chat`, `GET /docs`
- `POST /v1/chat/completions`
- `POST /v1/embeddings`
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
- `/config/*` admin, provider, model, usage, and profile endpoints

## Testing

```bash
uv run pytest -q tests
```

Live Anthropic adapter tests are opt-in:

```bash
OPEN_LLM_AUTH_LIVE_TESTS=1 uv run pytest -q tests/test_anthropic_adapter.py
```

For a smaller smoke pass:

```bash
uv run pytest -q tests/test_provider_manager.py tests/test_config_routes_dashboard.py
```

## Project Status

OpenLLMAuth is active development software. The public API is intended to be
practical and stable enough for local experimentation, but provider catalogs and
third-party auth flows can change. Check the documentation and test coverage
before using it as a critical production gateway.
