# Security Model

OpenLLMAuth is designed for local-first operation, but it still handles
credentials and outbound model calls. Treat it as a privileged local service.

## Local API Authentication

Protected routes expect:

```http
Authorization: Bearer YOUR_LOCAL_TOKEN
```

Token sources:

1. Scoped configured tokens from `authorization.tokens`.
2. Legacy compatibility token from `serverToken` or `OPEN_LLM_AUTH_TOKEN`.
3. Anonymous admin mode only when `OPEN_LLM_AUTH_ALLOW_ANON=1` and no
   configured or legacy token exists. Use this only for tightly controlled
   local development.

Scopes:

- `read`: read-only routes.
- `write`: chat and task mutation routes.
- `admin`: config and credential-management routes.

An admin token implies read, write, and admin access.

## Credential Storage

Credentials are stored in `~/.open_llm_auth/config.json` unless they come from
environment variables, CLI credential stores, or provider-native SDK discovery.

The config API redacts secret-bearing fields before returning responses.

Recommended practices:

- Do not expose the gateway directly to the public internet.
- Use a server token for any shared or network-reachable instance.
- Keep `~/.open_llm_auth/config.json` readable only by trusted local users.
- Prefer separate scoped tokens for different clients.
- Rotate provider credentials if logs or config files are exposed.

## Egress Policy

The gateway validates outbound provider base URLs before writing config and
again during runtime resolution. This reduces accidental calls to unsafe
destinations such as link-local metadata services.

Local providers can be allowed explicitly when the operator intends to talk to a
loopback service.

## Durable Task Ownership

Universal task routes use durable ownership checks so one caller cannot
arbitrarily mutate another caller's task unless it has sufficient privilege.

Idempotency keys are actor-scoped and payload-fingerprinted to prevent unsafe
replays.

## Error Handling

Upstream provider errors are sanitized before they are returned to callers.
Responses should not expose raw provider credentials.
