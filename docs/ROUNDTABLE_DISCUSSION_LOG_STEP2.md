# Roundtable Discussion Log - Step 2 (Buffered Approval Batch)

## Round 1 - Proposals

The Core Architect: Next slice should be a task contract handshake between gateway and agent. Add `/v1/agent/contract` in openbulma-v4 and gate mutating universal task routes in open_llm_auth.

The Auth Hacker: Agree. We need contract version + schema hash, then pin that in gateway and reject mismatches on task mutation calls.

The Sandbox Guardian: Keep rollout practical. Cache contract with TTL and fail closed only for write routes first; read routes can remain available.

The Security Auditor: Handshake must prevent semantic drift from causing unsafe side effects. Deterministic failure code is required on mismatch.

The Integration Ambassador: Backward compatibility is key. Support current + previous contract version and return structured mismatch details.

The Memory Archivist: Add operational counters: contract checks, mismatch blocks, cache stale events. This gives measurable stability gains.

The Cognitive Systems Engineer: This also improves agent reliability because action/state semantics become explicit instead of implicit assumptions.

The Telemetry Analyst: I want explicit metrics and timestamps in the contract cache path for debugging skew across deployments.

The Dashboard Weaver: Expose contract status in a simple diagnostics route so operators can see version/capability alignment quickly.

## Round 2 - Critique and Refinement

The Core Architect: We should scope step 2 to handshake and write-route gating only, not full observability UI.

The Security Auditor: Accepted. Security minimum: mismatch must block `create/approve/retry/cancel` with a stable error code.

The Auth Hacker: Add provider adapter method `get_task_contract()` and central comparison helper to avoid route duplication.

The Sandbox Guardian: Add a feature flag for enforce mode versus monitor mode to avoid breaking live staggered deploys.

The Integration Ambassador: Compatibility behavior: if endpoint missing, treat as legacy in monitor mode; in enforce mode, block writes.

The Telemetry Analyst: Log whether decision came from fresh fetch or cached contract.

The Memory Archivist: Keep cache ephemeral for now; no migration needed.

The Cognitive Systems Engineer: Ensure machine-readable mismatch details (`gatewayVersion`, `providerVersion`, `requiredCapabilities`).

The Dashboard Weaver: Defer UI work; keep diagnostic payload API-first for now.

## Convergence

- Consensus next slice: **Task Contract Handshake v1**.
- Scope for this step:
  - Add openbulma-v4 contract endpoint.
  - Add open_llm_auth contract fetch/check helper.
  - Gate mutating universal task routes by contract compatibility.
  - Preserve read-route availability under mismatch.
  - Add deterministic error code and tests.
- Deferred follow-on items: richer telemetry dashboard and signed capability tokens.
