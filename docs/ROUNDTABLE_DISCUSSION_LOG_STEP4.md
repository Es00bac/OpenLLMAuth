# Roundtable Discussion Log - Step 4 (Buffered Approval Batch)

## Round 1 - Proposal

The Core Architect: Add explicit contract-context headers on gateway to agent_bridge mutating task calls.

The Auth Hacker: Use stable names: `X-Provider-Contract-Version`, `X-Gateway-Version`, `X-Request-Id`.

The Sandbox Guardian: Keep headers advisory during rollout and avoid hard-fail on missing inbound headers yet.

The Security Auditor: Long-term enforcement is needed, but immediate step should avoid mixed-version breakage.

The Integration Ambassador: This is the right incremental bridge between handshake checks and strict provider enforcement.

The Memory Archivist: Capture one telemetry field for outbound calls: `contract_version_sent`.

The Cognitive Systems Engineer: Per-request contract context makes behavior easier to reason about across retries.

The Telemetry Analyst: Include request id and contract version in debug metadata.

The Dashboard Weaver: Header metadata should surface in API diagnostics later; no UI dependency now.

## Round 2 - Convergence

- Consensus slice: **Gateway outbound contract headers on mutating task calls**.
- Scope:
  - open_llm_auth sets contract headers on agent_bridge mutating routes.
  - no strict inbound enforcement on agent_bridge yet (deferred to next step).
  - add tests that verify header propagation.

