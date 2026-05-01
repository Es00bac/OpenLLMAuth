# Development

This page is for contributors working on OpenLLMAuth itself.

## Setup

```bash
git clone https://github.com/Es00bac/OpenLLMAuth.git
cd OpenLLMAuth
uv sync
```

Run the gateway:

```bash
uv run open-llm-auth serve --host 127.0.0.1 --port 8080
```

Run tests:

```bash
uv run pytest -q tests
```

Live Anthropic adapter tests are skipped by default. Run them only when a
compatible live gateway and credentials are available:

```bash
OPEN_LLM_AUTH_LIVE_TESTS=1 uv run pytest -q tests/test_anthropic_adapter.py
```

## Code Organization

- `src/open_llm_auth/main.py`: FastAPI app assembly.
- `src/open_llm_auth/cli.py`: Typer command line interface.
- `src/open_llm_auth/config.py`: persisted config models and config loading.
- `src/open_llm_auth/provider_catalog.py`: built-in provider and model catalog.
- `src/open_llm_auth/auth/manager.py`: provider, model, and credential
  resolution.
- `src/open_llm_auth/providers/`: provider adapters.
- `src/open_llm_auth/server/routes.py`: OpenAI-compatible and universal routes.
- `src/open_llm_auth/server/config_routes.py`: admin/config/usage routes.
- `src/open_llm_auth/server/durable_state.py`: SQLite-backed control state.
- `src/open_llm_auth/server/egress_policy.py`: outbound URL validation.
- `src/open_llm_auth/server/task_contract.py`: Agent Bridge contract checks.

## Test Areas

Important test files:

- `tests/test_provider_manager.py`
- `tests/test_config_routes_dashboard.py`
- `tests/test_gateway_security_hardening.py`
- `tests/test_universal_gateway.py`
- `tests/test_agent_bridge_provider.py`
- `tests/test_bedrock_provider.py`
- `tests/test_anthropic_adapter.py`
- `tests/test_auth_manager_parsing.py`

## Release Checklist

1. Run the focused tests for the area you changed.
2. Run `uv run pytest -q tests` when the change affects routing, auth,
   provider resolution, config, or packaging.
3. Start the server and verify `/health`, `/docs`, `/v1/models`, and a basic
   `/v1/chat/completions` call.
4. Confirm config API responses redact secrets.
5. Confirm egress policy still blocks unsafe outbound destinations.
6. Update the relevant file in `docs/` before changing public behavior.
