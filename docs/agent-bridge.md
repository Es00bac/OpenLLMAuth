# Agent Bridge Integration

OpenLLMAuth can route chat and task calls to a local Agent Bridge compatible
runtime through the `agent_bridge` provider.

## Defaults

The Agent Bridge provider defaults to:

```text
http://127.0.0.1:20100/v1
```

Provider aliases:

- `agent_bridge`
- `agent`

## Chat Behavior

Normal chat requests call the runtime chat endpoint. Because the bridge chat API
is single-turn, OpenLLMAuth folds recent conversation context into a bounded
synthetic system block before forwarding the request.

## Task Behavior

Universal task routes map onto Agent Bridge lifecycle operations:

- create task
- get task
- list tasks
- approve task
- retry task
- cancel task
- stream task events
- wait for task completion

Mutating task operations attach contract headers such as:

```text
X-Provider-Contract-Version
```

The gateway can check runtime task-contract compatibility before allowing
mutating task routes.

## Streaming

Task streaming is synthesized by polling task snapshots and event history, then
emitting OpenAI-style server-sent event chunks to clients.

## When To Use It

Use the Agent Bridge provider when a caller needs structured, durable task
lifecycle behavior rather than a single model response.
