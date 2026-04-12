# Roundtable Discussion Log - Step 5 (Buffered Approval Batch)

## Round 1 - Proposal

The Core Architect: Final batch step should add provider-side optional enforcement for contract headers on mutating routes.

The Auth Hacker: Validate `X-Provider-Contract-Version` against allowed versions and reject mismatch with stable machine code.

The Sandbox Guardian: Enforcement must be toggle-gated and default non-breaking (`monitor`/`off`) for mixed-version rollout.

The Security Auditor: Missing or invalid contract version headers should have deterministic fail path when enforce mode is on.

The Integration Ambassador: Keep compatibility by allowing monitor mode to pass requests while returning diagnostic headers.

The Memory Archivist: Include policy mode and received version in logs for traceability.

The Cognitive Systems Engineer: Header checks should only apply to mutating task routes to avoid unnecessary read-path coupling.

The Telemetry Analyst: Use one env/config switch and record mode transitions.

The Dashboard Weaver: Response should include contract mode/version headers for future UI visibility.

## Round 2 - Convergence

- Consensus slice: **OpenBulma optional header enforcement for mutating routes**.
- Scope:
  - Add policy mode (`off|monitor|enforce`) and accepted version list.
  - In enforce mode, reject missing/mismatched header.
  - In monitor mode, allow but emit diagnostics headers and logs.
  - Add endpoint tests.

