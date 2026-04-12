# Open LLM Auth Execution Log

This log is maintained as a step-by-step checklist of all implementation actions.

## Conventions
- `[ ]` Planned
- `[x]` Completed
- Every implementation slice includes an "Expert Consult" section before code changes.

## 2026-03-06 - Universal Gateway + Task Lifecycle
- [x] Implemented fail-closed auth default with explicit `OPEN_LLM_AUTH_ALLOW_ANON` override.
- [x] Implemented recursive redaction for secrets in config responses.
- [x] Implemented upstream/provider error sanitization.
- [x] Added security regression tests and passed targeted suites.
- [x] Added universal endpoint (`POST /v1/universal`) with canonical response envelope.
- [x] Refactored OpenAI chat route to share the same core execution path.
- [x] Added native universal task lifecycle routes (create/status/approve/retry/cancel).
- [x] Extended OpenBulma provider adapter with explicit task lifecycle methods.
- [x] Ran automated tests (24 passed) and manual multi-process smoke tests.
- [x] Synced duplicate source tree under `src/src/open_llm_auth`.

## 2026-03-06 - Process Requirements Update (operator)
- [x] Requirement accepted: document every action in checklist form as work proceeds.
- [x] Requirement accepted: consult experts before each implementation slice.
- [ ] Next slice proposal pending expert consult and operator confirmation.

## 2026-03-06 - Expert Consult Round (Post-Lifecycle)
- [x] Consulted Euler (Core Architect): recommended idempotent create + task journal/list/events durability.
- [x] Consulted Russell (Systems): recommended idempotency-key semantics for all mutating lifecycle operations.
- [x] Consulted Pascal (Security): recommended gateway task registry + idempotency to prevent duplicate side effects and context drift.
- [x] Consulted James (Cognition/ops): recommended observability routes (`list/events/wait`) as reliability layer.
- [x] Consulted Averroes (Memory/durability): recommended strong idempotency + replay/conflict + concurrency proofs.
- [x] Consulted Gauss (Hardening): recommended idempotency + conflict semantics + replay headers.
- [x] Convergence result: next highest-value slice is idempotent universal task mutations (create/approve/retry/cancel), with optional task journal/list/events as follow-on.
- [x] Awaiting operator approval to implement converged next slice.

## 2026-03-06 - Slice: Idempotent Task Mutations (Completed)
- [x] Added idempotency module scaffold (`server/idempotency.py`) with claim/replay/conflict logic.
- [x] Wired idempotency handling into mutating universal task routes (create/approve/retry/cancel).
- [x] Added replay/conflict tests for create route.
- [x] Added concurrent duplicate-submit test for create route.
- [x] Added idempotency tests for approve/retry/cancel routes.
- [x] Run targeted automated tests and fix issues.
- [x] Run manual live smoke for duplicate-submission behavior.
- [x] Sync duplicate source tree and finalize summary.
- [x] Ran targeted automated tests after idempotency wiring (`28 passed`).
- [x] Ran manual live smoke for duplicate submissions:
  - create with same key replayed (`runCount=1`)
  - approve with same key replayed (`approveCount=1`)
  - same key + different payload returned `409 idempotency_key_conflict`
- [x] Synced duplicate source tree (`src/src/open_llm_auth`) for changed files.
- [x] Slice complete.

## 2026-03-06 - Slice Candidate: Post-Idempotency Maturity (Planning)
- [x] Started slice planning under process lock.
- [x] Initiated expert consult round before coding.
- [x] Consolidate expert recommendations into one candidate step card.
- [x] Present plain-language step card to operator for approval.
- [x] Implement only after explicit approval.
- [x] Received all six expert responses for post-idempotency slice.
- [x] Converged recommendation: add task observability primitives (`list/events/wait`) with bounded polling/stream safeguards.
- [x] Drafted candidate step card for operator approval (no code changes yet).

## 2026-03-06 - Slice: Task Observability (Approved)
- [x] operator approved implementation.
- [x] Add `GET /v1/universal/tasks` route.
- [x] Add `GET /v1/universal/tasks/{task_id}/events` route.
- [x] Add `POST /v1/universal/tasks/{task_id}/wait` route with bounded timeout/poll.
- [x] Extend OpenBulma provider for list/events helpers.
- [x] Add/extend tests for success/error/timeout/auth.
- [x] Run targeted tests and manual smoke.
- [x] Sync duplicate source tree and finalize.
- [x] Added universal task observability models (`list/events/wait`).
- [x] Added OpenBulma provider helpers for task listing and task event retrieval.
- [x] Added routes for `GET /v1/universal/tasks`, `GET /v1/universal/tasks/{task_id}/events`, `POST /v1/universal/tasks/{task_id}/wait` with bounded timeout/poll.
- [x] Added/updated tests for list/events/wait and auth enforcement.
- [x] Run targeted automated tests and fix issues.
- [x] Run manual live smoke for new observability endpoints.
- [x] Run targeted automated tests and fix issues (`30 passed`).
- [x] Run manual live smoke for new observability endpoints (list/events/wait success + timeout 408).
- [x] Sync duplicate source tree and finalize.

## 2026-03-06 - Slice Candidate: Post-Observability Maturity (Planning)
- [x] Started planning round under process lock.
- [x] Initiated full expert consult before coding.
- [ ] Converge on one recommended slice.
- [ ] Present plain-language step card to operator for approval.
- [ ] Implement only after explicit approval.
- [x] Received all six expert responses for post-observability planning.
- [x] Consult synthesis: majority recommendation is scoped authorization + task ownership enforcement (with compatibility mode), minority recommendation is durability/restart persistence and transition strictness.
- [x] Prepared plain-language step card for operator approval (no code changes yet).

## 2026-03-06 - Slice: Scoped Authorization + Task Ownership (Approved)
- [x] operator approved implementation.
- [x] Started expert consult round before code changes.
- [x] Consulted Euler (Core Architect) on principal model, scope checks, and compatibility semantics.
- [x] Consulted Russell (Systems) on ownership/idempotency race handling and route guard structure.
- [x] Consulted Pascal (Security) on deny-by-default semantics and edge-case abuse paths.
- [x] Consulted James (Delivery) on incremental rollout and regression-safe sequencing.
- [x] Consulted Averroes (Durability) on ephemeral owner-map assumptions and explicit documentation.
- [x] Consulted Gauss (Hardening) on invariants and deterministic authorization checks.
- [x] Converged implementation scope: principal auth context, scoped route checks, owner-or-admin task access, actor-bound idempotency.
- [x] Implement principal-based auth in `server/auth.py`.
- [x] Updated config routes to require admin scope (`server/config_routes.py`).
- [x] Enforce scopes/ownership/idempotency actor binding in `server/routes.py`.
- [x] Add/extend tests for scope matrix, ownership checks, actor-scoped idempotency, legacy compatibility toggle, and config admin scope.
- [x] Run targeted automated tests (`tests/test_universal_gateway.py` + `tests/test_gateway_security_hardening.py`): `20 passed`.
- [x] Run broader suite (`tests/`): core tests passed; 7 live Anthropic integration tests failed due network/connectivity (`httpx.ConnectError`), not local auth/route regressions.
- [x] Run manual smoke (token-scoped create/status): owner create `200`, non-owner status `403 task_owner_mismatch`, admin status `200`.
- [x] Sync duplicate source tree (`src/src/open_llm_auth`) for `config.py`, `server/auth.py`, `server/config_routes.py`, and `server/routes.py`.

## 2026-03-06 - Roundtable: Next Step Selection (Post-Scoped-Auth)
- [x] Initiated full expert consult before selecting next implementation slice.
- [x] Gathered responses from Euler, Russell, Pascal, James, Averroes, and Gauss.
- [x] Logged recommendations:
  - Euler: strict task-state transition enforcement with feature flag.
  - Russell: admission-control endpoint + circuit breaker between gateway and runtime.
  - Pascal: outbound destination policy to block token exfiltration/SSRF risks.
  - James: task audit trail + export for operational traceability.
  - Averroes: persistent ownership/idempotency storage (restart-safe correctness).
  - Gauss: deterministic transition kernel + compare-and-swap commit rule.
- [x] Convergence readout: strongest thematic overlap is lifecycle transition correctness (state machine + deterministic conflict handling), with durability as immediate follow-on.
- [ ] Present roundtable chat log first to operator.
- [ ] Present one plain-language recommended next slice for approval.

## 2026-03-06 - Roundtable v2: Multi-Round Consensus Process (operator Feedback Applied)
- [x] Process correction accepted: run multi-round expert debate (proposal -> critique/scoring -> convergence) instead of single-pass recommendations.
- [x] Round 1 complete: collected six distinct proposals.
- [x] Round 2 complete: cross-critique with per-option scoring and top-2 selection from each expert.
- [x] Round 3 complete: forced convergence (one next slice + one follow-on).
- [x] Constraint recorded: runtime agent thread limit is 6 (cannot run all 9 personas concurrently as separate agents).
- [x] Added persona coverage for missing roles via explicit one-reply persona overrides:
  - Telemetry Analyst
  - Dashboard Weaver
  - Integration Ambassador
- [x] Consensus trend after convergence: next slice = durable persistence for ownership/idempotency/action history; follow-on = outbound destination safety policy and/or contract handshake (ordering preference split).
- [ ] Present full roundtable v2 chat log first to operator.
- [ ] Present plain-language consensus and exact next-step card for approval.

## 2026-03-06 - Slice: Durable Control-State Persistence (In Progress)
- [x] operator approved: move on to implementation.
- [x] Started expert consult before coding this slice.
- [x] Consulted hardening expert for invariants and must-have correctness tests.
- [x] Consulted delivery expert for rollout/rollback acceptance criteria.
- [x] Consulted security expert for fail-closed persistence requirements.
- [x] Consulted durability expert for SQLite WAL schema + TTL/recovery guidance.
- [x] Consulted systems expert for practical schema and route integration shape.
- [x] Added durable SQLite-backed store (`server/durable_state.py`) for ownership, idempotency, and action history.
- [x] Added durable-state config (`durableState`) with db path, idempotency TTL, lease window, and fail-closed toggle.
- [x] Wired universal task routes to durable store (replaced in-memory ownership/idempotency authority paths).
- [x] Added action-history appends for mutating task operations (`create/approve/retry/cancel`).
- [x] Added restart-stability tests:
  - idempotency replay survives control-state cache reset
  - task ownership checks survive control-state cache reset
- [x] Updated test config helpers to isolate durable SQLite file per test and reset store cache.
- [x] Ran targeted tests and fixed regressions: `22 passed`.
- [x] Ran manual smoke for restart-stability semantics:
  - first request `200 X-Idempotent-Replay=false`
  - second request after cache reset `200 X-Idempotent-Replay=true`
  - provider side effect count remained `1`
- [x] Synced duplicate source tree (`src/src/open_llm_auth`) for changed files.

## 2026-03-07 - Process Hard-Stop Enforcement
- [x] Added hard-stop protocol doc: `docs/HUMAN_LOOP_PROTOCOL.md`.
- [x] Added process guard script: `tools/human_loop_guard.py`.
- [x] Initialized process state file via guard transitions:
  - `roundtable_open -> roundtable_logged`
  - `roundtable_logged -> step_card_presented`
- [x] Verified implementation block before approval:
  - `python tools/human_loop_guard.py can-implement` returned exit code `4` (`human_approved` required).

## 2026-03-07 - Process Cementing Update (Per operator Request)
- [x] Updated guard state machine to enforce civil discussion workflow:
  - `roundtable_open -> discussion_in_progress -> discussion_completed -> step_card_presented -> human_approved -> implementation_started -> tests_passed -> reported`
- [x] Added transcript validation command:
  - `python tools/human_loop_guard.py validate-discussion <path>`
- [x] Added strict 9-persona requirement in guard validation:
  - Core Architect, Auth Hacker, Cognitive Systems Engineer, Memory Archivist, Sandbox Guardian, Security Auditor, Telemetry Analyst, Dashboard Weaver, Integration Ambassador
- [x] Added minimum multi-round marker validation (at least two round markers).
- [x] Updated protocol document with civil discussion requirement and new command sequence.
- [x] Added reusable discussion template: `docs/ROUNDTABLE_DISCUSSION_TEMPLATE.md`.
- [x] Re-verified implementation remains blocked without approval/validation.

## 2026-03-07 - Civil 9-Persona Discussion (Validated)
- [x] Reopened cycle from `step_card_presented -> discussion_in_progress` using explicit guard transition.
- [x] Ran civil multi-round discussion on one decision: `B->C` vs `C->B` (post-durable-persistence next slice ordering).
- [x] Captured full transcript with 9 labeled personas and round markers:
  - `docs/ROUNDTABLE_DISCUSSION_LOG.md`
- [x] Validated transcript with guard command:
  - `python tools/human_loop_guard.py validate-discussion docs/ROUNDTABLE_DISCUSSION_LOG.md`
- [x] Transitioned guard state:
  - `discussion_in_progress -> discussion_completed -> step_card_presented`
- [x] Discussion convergence recorded:
  - vote tally `B->C` = 5, `C->B` = 4
  - consensus next slice `B` with immediate follow-on `C`

## 2026-03-07 - Slice: Outbound Destination Safety Policy (B) (Completed)
- [x] Ran focused expert consult for this slice (Core Architect, Sandbox Guardian, Security Auditor, Integration Ambassador, Memory Archivist, Auth Hacker).
- [x] Added shared egress policy engine:
  - `src/open_llm_auth/server/egress_policy.py`
  - blocks metadata/private/loopback targets (with local-provider exceptions), enforces scheme rules, and performs hostname resolution checks.
- [x] Added `egressPolicy` config section in `src/open_llm_auth/config.py`:
  - `mode`, `resolveDns`, `failClosed`, `enforceHttps`, `allowLocalProviders`, `denyHosts`, `denyCidrs`.
- [x] Enforced policy at config write-time in `src/open_llm_auth/server/config_routes.py`:
  - `POST /config`
  - `PUT /config/providers/{provider_id}`
  - `PUT /config/auth-profiles/{profile_id}`
- [x] Enforced policy at runtime in `src/open_llm_auth/auth/manager.py`:
  - effective base URL resolution (including profile-aware Copilot base URL)
  - per-provider runtime destination validation before provider construction
  - cache key includes effective base URL and selected profile context.
- [x] Added explicit gateway error mapping in `src/open_llm_auth/server/routes.py`:
  - blocked destinations return `403` with code `egress_destination_blocked` instead of generic provider-not-found.
- [x] Added/updated tests:
  - `tests/test_provider_manager.py` (runtime allow/block matrix)
  - `tests/test_gateway_security_hardening.py` (config write-time blocking + runtime API error mapping)
  - `tests/test_universal_gateway.py` (config patch helper hardening for manager reload path).
- [x] Ran targeted test suites with real results:
  - `./.venv/bin/pytest tests/test_universal_gateway.py tests/test_provider_manager.py tests/test_gateway_security_hardening.py -q`
  - result: `35 passed`.
- [x] Synced duplicate source tree (`src/src/open_llm_auth`) for changed files including new `server/egress_policy.py`.

## 2026-03-07 - Process Update: 5-Approval Buffered Iteration (operator Request)
- [x] Requirement accepted: run five consensus iterations before returning to operator, while preserving the same discussion process.
- [x] Updated guard script `tools/human_loop_guard.py`:
  - added `approval_queue` state field
  - added `queue-approvals <count>`
  - added `auto-approve` (consumes one queued approval and performs `step_card_presented -> human_approved`).
- [x] Updated protocol doc `docs/HUMAN_LOOP_PROTOCOL.md` with buffered approval mode instructions.

## 2026-03-07 - Buffered Iteration 2: Gateway-Provider Contract Handshake (Completed)
- [x] Logged and validated multi-round expert discussion:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP2.md`
- [x] Added OpenBulma contract endpoint in `openbulma-v4`:
  - `GET /v1/agent/contract` in `src/integration/IntegrationHub.ts`
- [x] Added gateway-side contract fetch support:
  - `get_task_contract()` in `src/open_llm_auth/providers/openbulma.py`
- [x] Added contract config and checker in gateway:
  - `taskContract` config in `src/open_llm_auth/config.py`
  - cache/check module `src/open_llm_auth/server/task_contract.py`
- [x] Enforced contract checks for mutating universal task routes in `src/open_llm_auth/server/routes.py`.
- [x] Ran tests with real results:
  - `open_llm_auth`: `37 passed`
  - `openbulma-v4`: lifecycle test passed + `npm run check` passed.

## 2026-03-07 - Buffered Iteration 3: Contract Diagnostics (Completed)
- [x] Logged and validated multi-round expert discussion:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP3.md`
- [x] Extended contract check status cache metadata in `src/open_llm_auth/server/task_contract.py`.
- [x] Added diagnostics route:
  - `GET /v1/universal/contract/status` in `src/open_llm_auth/server/routes.py`
- [x] Added endpoint test in `tests/test_universal_gateway.py`.
- [x] Ran tests with real results:
  - `open_llm_auth`: `38 passed`.

## 2026-03-07 - Buffered Iteration 4: Outbound Contract Headers (Completed)
- [x] Logged and validated multi-round expert discussion:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP4.md`
- [x] Added gateway outbound headers for mutating OpenBulma operations:
  - `X-Provider-Contract-Version`
  - `X-Gateway-Version`
  - `X-Request-Id`
  - implemented in `src/open_llm_auth/providers/openbulma.py`
- [x] Added provider header test:
  - `tests/test_openbulma_provider.py`
- [x] Ran tests with real results:
  - `open_llm_auth`: `39 passed`.

## 2026-03-07 - Buffered Iteration 5: OpenBulma Contract Header Validation (Completed)
- [x] Logged and validated multi-round expert discussion:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP5.md`
- [x] Added OpenBulma-side configurable policy for provider contract headers:
  - `taskContractHeaderPolicy` (`off|monitor|enforce`)
  - config/env wiring in:
    - `openbulma-v4/src/config/AppConfig.ts`
    - `openbulma-v4/src/config/RuntimeConfigManager.ts`
    - `openbulma-v4/src/index.ts`
- [x] Added route-level validation for mutating task endpoints in:
  - `openbulma-v4/src/integration/IntegrationHub.ts`
  - endpoints: `runTask`, `cancel`, `retry`, `approve`
- [x] Added/updated tests:
  - `openbulma-v4/tests/integration-hub-lifecycle.test.ts`
  - `openbulma-v4/tests/runtime-config-manager.test.ts`
  - `openbulma-v4/tests/dashboard-endpoints.test.ts`
  - `openbulma-v4/tests/governance-and-somatic-endpoints.test.ts`
- [x] Ran tests with real results:
  - `npm run test -- tests/integration-hub-lifecycle.test.ts tests/runtime-config-manager.test.ts tests/dashboard-endpoints.test.ts tests/governance-and-somatic-endpoints.test.ts` -> `8 passed`
  - `npm run check` -> passed
  - re-run focused suite: `npm run test -- tests/integration-hub-lifecycle.test.ts tests/runtime-config-manager.test.ts` -> `6 passed`

## 2026-03-07 - Buffered Iteration 6: OpenBulma Contract Header Diagnostics (Completed)
- [x] Logged and validated multi-round expert discussion:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP6.md`
- [x] Added OpenBulma diagnostics model and counters for contract-header checks:
  - totals (`ok/missing/mismatch`)
  - per-operation counters (`create/approve/retry/cancel`)
  - `lastFailure` sample with timestamp
  - implemented in `openbulma-v4/src/integration/IntegrationHub.ts`
- [x] Added new read-only diagnostics endpoint:
  - `GET /v1/agent/contract-header/status`
- [x] Added lifecycle test coverage:
  - `openbulma-v4/tests/integration-hub-lifecycle.test.ts`
  - verifies endpoint payload and counter updates after `ok`, `missing`, and `mismatch` calls.
- [x] Ran tests with real results:
  - `npm run test -- tests/integration-hub-lifecycle.test.ts` -> `6 passed`
  - `npm run check` -> passed
- [x] Synced mirrored gateway source tree for contract-related files:
  - `src/src/open_llm_auth/config.py`
  - `src/src/open_llm_auth/server/routes.py`
  - `src/src/open_llm_auth/server/task_contract.py`
  - `src/src/open_llm_auth/providers/openbulma.py`
- [x] Process state advanced to `reported`; buffered approval queue is now exhausted.

## 2026-03-07 - Step 7: Real Bulma Usability Harness (Completed)
- [x] Ran fresh 9-persona roundtable and validated transcript:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP7.md`
- [x] Convergence decision: run real Bulma API scenario harness now, then use results for next roundtable.
- [x] Added reusable harness script:
  - `openbulma-v4/scripts/run-bulma-usability-harness.ts`
- [x] Added npm command:
  - `openbulma-v4/package.json` -> `test:bulma-usability`
- [x] Harness behavior implemented:
  - seeds memory facts via `/v1/memory/ingest`
  - runs cross-domain chat scenarios via `/v1/chat`
  - runs memory probes via `/v1/memory/retrieve` + `/v1/chat`
  - writes markdown + JSON reports under `openbulma-v4/docs/`
- [x] Executed live harness against running stack:
  - command: `npm run test:bulma-usability`
  - results:
    - scenario pass: `6/7` (`85.7%`)
    - memory pass: `0/3` (`0.0%`)
    - overall: `FAIL`
  - reports:
    - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T05-23-38.457Z.md`
    - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T05-23-38.457Z.json`
- [x] Ran type-check after harness changes:
  - `npm run check` -> passed.

## 2026-03-07 - Step 8: Memory Recall Remediation + Re-Test (Completed)
- [x] Ran fresh 9-persona roundtable and validated transcript:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP8.md`
- [x] Convergence decision: targeted `IntegrationHub` memory-context refinement with no API contract changes.
- [x] Implemented memory retrieval tuning in `openbulma-v4/src/integration/IntegrationHub.ts`:
  - filtered noisy proper-noun anchors (question words like `what/when/where/...`)
  - replaced weak-retrieval lexical fallback with scored anchor-overlap fallback (less brittle than `tokens.every(...)`)
  - preserved bounded runtime behavior and existing endpoint contracts.
- [x] Type-check after patch:
  - `npm run check` -> passed.
- [x] Re-ran live usability harness:
  - command: `npm run test:bulma-usability`
  - result report artifacts:
    - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T05-35-05.295Z.md`
    - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T05-35-05.295Z.json`
  - measured outcome:
    - scenario pass improved to `7/7` (`100.0%`)
    - memory probes remained `0/3` (`0.0%`)
    - overall remains `FAIL`
- [x] Captured this as explicit input for the next roundtable (memory pipeline still primary blocker).

## 2026-03-07 - Step 9: Retrieval Hardening + Stable Harness Validation (Completed)
- [x] Ran fresh 9-persona roundtable and validated transcript:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP9.md`
- [x] Implemented `MemoryFabric` retrieval tuning in `openbulma-v4/src/memory/MemoryFabric.ts`:
  - stronger personal-recall weighting (higher recency/keyword, lower source dominance),
  - stricter penalties for artifact sources during personal recall (workspace tests, question-echo chat),
  - improved strong-anchor extraction to include 4-letter proper nouns (for names like `Alex`),
  - improved ephemeral chat promotion guard to reject question-echo memories.
- [x] Implemented `IntegrationHub` retrieval/context hardening in `openbulma-v4/src/integration/IntegrationHub.ts`:
  - widened memory-grounding detection for personal recall questions,
  - broadened memory-gap detection phrases for fallback activation,
  - personal-recall-aware memory context ranking/filtering,
  - ingest-side identity/salience defaults for inferred identity memories,
  - `/v1/memory/retrieve` lexical fallback merge before rerank.
- [x] Updated harness seeding for deterministic identity recall tests:
  - `openbulma-v4/scripts/run-bulma-usability-harness.ts`
  - seeds now include explicit `identityCore: true`, high salience, and identity seed metadata.
- [x] Resolved runtime reliability issue during testing:
  - switched from watch-mode runtime restarts to clean `build + start` runtime for stable evidence collection.
- [x] Ran validation commands:
  - `npm run check` (multiple runs) -> passed
  - `npm run build` -> passed
- [x] Ran live harness against updated runtime (`OPENBULMA_URL=http://127.0.0.1:20110`) with real artifacts:
  - interim run: `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T06-31-31.968Z.md` (`scenario 6/7`, `memory 1/3`, `overall FAIL`)
  - final run: `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T06-39-29.913Z.md` (`scenario 6/7`, `memory 2/3`, `overall PASS`)
- [x] Advanced human-loop guard state for Step 9:
  - `implementation_started -> tests_passed -> reported`

## 2026-03-07 - Step 10: Expanded Repeated Testing + Memory Depth Improvements (Completed)
- [x] Ran and logged Step 10 roundtable discussion with 9 personas:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP10.md`
- [x] Validated Step 10 transcript with process guard and advanced state to implementation.
- [x] Expanded usability harness in `openbulma-v4/scripts/run-bulma-usability-harness.ts`:
  - added repeated measured runs (`BULMA_HARNESS_RUNS`) + optional warmups (`BULMA_HARNESS_WARMUP_RUNS`)
  - added extended memory suite mode (`BULMA_HARNESS_MEMORY_SUITE=extended`) with 12 memory probes
  - added aggregate reporting across runs (run summary + per-scenario/per-probe pass rates + match/latency averages)
  - added stronger harness seed set (MemoryTag anchors + broader project/process/user facts)
- [x] Ran expanded baseline against active legacy runtime (`:20100`) to establish failure map:
  - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T07-22-49.988Z.md`
  - aggregate: scenario `85.7%`, memory `37.5%`, overall `FAIL`
- [x] Applied non-destructive retrieval/chat tuning in `openbulma-v4/src/integration/IntegrationHub.ts`:
  - conditional BAA tool exposure only when dispatch intent is true (reduced accidental delegation)
  - stronger forced memory-grounded fallback for explicit recall/personal-recall misses
  - explicit source preference/penalty in fallback ranking (prefer harness seed for explicit MemoryTag lookups)
  - expanded personal-recall intent detection to include process/MemoryTag recall queries
- [x] Rebuilt runtime (`npm run check`, `npm run build`) and re-tested on clean state runtime (`STATE_DIR=/tmp/openbulma_step10_state_clean`, `PORT=20110`).
- [x] Ran repeated extended suites after tuning:
  - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T08-00-20.843Z.md`
    - scenario `92.9%`, memory `75.0%`, overall `PASS`
  - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T08-21-07.795Z.md` (clean-state verification)
    - scenario `100.0%` (14/14), memory `79.2%` (19/24), passing runs `2/2`, overall `PASS`
- [x] Advanced human-loop guard state for Step 10:
  - `implementation_started -> tests_passed -> reported`

## 2026-03-07 - Step 11: Evidence-Coverage + Anti-Hallucination Harness (Completed)
- [x] Completed implementation-time consult with all 9 personas (from `project_audit/expertise_analysis.md`) and logged addendum in:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_STEP11.md`
- [x] Patched `openbulma-v4/src/integration/IntegrationHub.ts` for stricter recall correctness:
  - added explicit MemoryTag extraction (`extractExplicitMemoryTag`)
  - gated fallback forcing on exact tag evidence (`M9`/`M99` style matching)
  - added abstain fallback when evidence is insufficient instead of fabricating
  - filtered/penalized noisy sources in recall paths:
    - `memory:association`
    - `workspace:docs/BULMA_USABILITY_TEST_REPORT_*`
    - `workspace:scripts/run-bulma-usability-harness.ts`
    - `foundation:recent-chat` (for recall/fallback loops)
  - skipped turn-association ingest for explicit-tag/personal-recall queries to reduce contamination
  - strengthened source reliability mapping (`harness:seed:*` high, noisy artifacts low)
- [x] Patched `openbulma-v4/scripts/run-bulma-usability-harness.ts`:
  - added anti-hallucination abstain probes:
    - `abstain-unknown-memorytag`
    - `abstain-unknown-hostname`
    - `abstain-secret-injection`
  - expanded abstain marker detection and forbidden-pattern checks
  - added focused execution controls for repeatable rapid validation:
    - `BULMA_HARNESS_SCENARIOS=none|all`
    - `BULMA_HARNESS_PROBE_IDS=<csv>`
  - ensured aggregate logic handles scenario-disabled mode cleanly
- [x] Ran validation:
  - `cd /mnt/xtra/openbulma-v4 && npm run check` -> passed
  - `cd /mnt/xtra/openbulma-v4 && npm run build` -> passed
- [x] Test runtime hardening actions:
  - observed `postgres-qdrant` startup instability for isolated step runtime (`:20110`)
  - switched to isolated `file` backend runtime for deterministic Step 11 probe validation:
    - `STATE_DIR=/tmp/openbulma_step11_state_file`
    - `PORT=20111`
    - `MEMORY_BACKEND=file`
- [x] Executed repeated focused live harness runs (actual results):
  - command:
    - `OPENBULMA_URL=http://127.0.0.1:20111 BULMA_HARNESS_SCENARIOS=none BULMA_HARNESS_PROBE_IDS=schedule-boundary,assistant-scope,anchor-memory-m9,abstain-unknown-memorytag,abstain-unknown-hostname,abstain-secret-injection BULMA_HARNESS_RUNS=2 BULMA_HARNESS_MEMORY_SUITE=extended BULMA_HARNESS_REQUIRED_PASSING_RUNS=2 BULMA_HARNESS_TIMEOUT_MS=70000 BULMA_HARNESS_STRICT_CONTRACT=1 npm run test:bulma-usability`
  - iterative artifacts:
    - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T18-12-38.871Z.md` (initial focused run, FAIL)
    - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T18-15-18.865Z.md` (post-filter tuning, FAIL)
    - `openbulma-v4/docs/BULMA_USABILITY_TEST_REPORT_2026-03-07T18-19-59.935Z.md` (final focused run, PASS)
  - final measured outcome:
    - passing runs: `2/2` (required `2`)
    - memory pass: `8/12` (`66.7%`)
    - potential hallucination signals: `0/12`
    - overall: `PASS`
  - residual weak probes (still failing in final focused run):
    - `schedule-boundary`
    - `assistant-scope`

## 2026-03-07 - Step 12: Capability vs Memory A/B Diagnostic (Completed)
- [x] Ran fresh 9-persona roundtable focused on operator hypothesis:
  - question: are schedule/assistant fails from missing capability or memory pipeline selection?
  - transcript: `docs/ROUNDTABLE_DISCUSSION_LOG_STEP12.md`
- [x] Validated transcript with process guard:
  - `python tools/human_loop_guard.py validate-discussion docs/ROUNDTABLE_DISCUSSION_LOG_STEP12.md`
- [x] Presented step card and proceeded with explicit approval (`"sure"`).
- [x] Implemented dedicated diagnostic runner:
  - `openbulma-v4/scripts/run-memory-ab-diagnostic.ts`
  - npm command: `openbulma-v4/package.json` -> `test:memory-ab`
- [x] Diagnostic design implemented:
  - probes: `schedule-boundary`, `assistant-scope`
  - condition `A`: normal `/v1/chat` query path
  - condition `B`: oracle memory context injected via system prompt
  - fixed scoring thresholds and per-probe uplift computation
  - interpretation thresholds:
    - `>20pp` memory selection/synthesis bottleneck
    - `<5pp` capability bottleneck
    - else mixed
- [x] Ran validation:
  - `cd /mnt/xtra/openbulma-v4 && npm run check` -> passed
  - `cd /mnt/xtra/openbulma-v4 && npm run build` -> passed
- [x] Ran live A/B diagnostic with actual results:
  - command:
    - `OPENBULMA_URL=http://127.0.0.1:20111 BULMA_AB_RUNS=2 BULMA_HARNESS_TIMEOUT_MS=180000 npm run test:memory-ab`
  - artifact reports:
    - `openbulma-v4/docs/BULMA_MEMORY_AB_DIAGNOSTIC_2026-03-07T19-04-50.624Z.md`
    - `openbulma-v4/docs/BULMA_MEMORY_AB_DIAGNOSTIC_2026-03-07T19-04-50.624Z.json`
  - measured outcome:
    - `schedule-boundary`: A `0.0%` vs B `100.0%` (uplift `+100.0pp`)
    - `assistant-scope`: A `0.0%` vs B `100.0%` (uplift `+100.0pp`)
    - average uplift: `+100.0pp`
  - convergence result:
    - operator hypothesis confirmed: primary blocker is memory selection/synthesis path, not missing core scheduling/assistant capability.

## 2026-03-07 - Handoff Package for Claude Opus 4.6 (Completed)
- [x] Created full continuation handoff document:
  - `docs/HANDOFF_CLAUDE_OPUS_4_6_2026-03-07.md`
- [x] Included:
  - current guard/process state
  - completed Step 11/12 context
  - roundtable and artifact references
  - exact continuation commands
  - Step 13 recommended next card
  - operator-specific workflow constraints
- [x] Moved handoff document to shared parent workspace per operator direction:
  - from `open_llm_auth/docs/HANDOFF_CLAUDE_OPUS_4_6_2026-03-07.md`
  - to `/mnt/xtra/HANDOFF_CLAUDE_OPUS_4_6_2026-03-07.md`
- [x] Updated handoff header to explicitly state combined scope and priority:
  - project scope = `openbulma-v4 + open_llm_auth`
  - `openbulma-v4` is primary; `open_llm_auth` is supporting.

## 2026-03-07 - BAA Generative Split Phase 1: Types + Routing (Completed)
- [x] Ran 9-persona roundtable on BAA generative split proposal:
  - `docs/ROUNDTABLE_DISCUSSION_LOG_BAA_GENERATIVE_SPLIT.md`
  - operator feedback incorporated: token-budget-aware compaction (Round 3)
  - Consensus: 5-phase implementation plan, 80-step generative loop
- [x] operator approved step card.
- [x] Added `TaskMode = 'repair' | 'generate'` to `shared/types.ts`.
- [x] Added `taskMode?: TaskMode` to `AgentTaskInput`.
- [x] Added `requiredPhases(): AssistantPhase[]` to `AssistantExecutionAdapter` interface.
- [x] Added `REPAIR_PHASES` constant in `ExecutionAdapter.ts`.
- [x] Renamed `LiveExecutionAdapter` → `LiveRepairAdapter` (file + class + all imports).
- [x] Added `selectAdapter()` method in `BulmaAssistantAgent` to route based on `taskMode`.
- [x] Refactored `runAttempt()` to use `adapter.requiredPhases()` — phases not in the set are skipped.
- [x] Set `taskMode: 'generate'` in `IntegrationHub.handleBaaToolCall()` for user-dispatched tasks.
- [x] Updated test file rename: `live-execution-adapter.test.ts` → `live-repair-adapter.test.ts`.
- [x] Fixed shutdown test to handle pre-existing `drainQueue` race condition.
- [x] Ran validation:
  - `npm run check` → passed
  - `npm run build` → passed
  - 3 test suites (12 tests) → all passed
- Files changed:
  - `openbulma-v4/src/shared/types.ts`
  - `openbulma-v4/src/assistant/ExecutionAdapter.ts`
  - `openbulma-v4/src/assistant/LiveRepairAdapter.ts` (renamed from LiveExecutionAdapter.ts)
  - `openbulma-v4/src/assistant/BulmaAssistantAgent.ts`
  - `openbulma-v4/src/integration/IntegrationHub.ts`
  - `openbulma-v4/src/index.ts`
  - `openbulma-v4/scripts/test-baa-website.ts`
  - `openbulma-v4/tests/live-repair-adapter.test.ts` (renamed)
  - `openbulma-v4/tests/bulma-assistant-agent-shutdown.test.ts`

## 2026-03-07 - BAA Generative Split Phase 2: LiveGenerativeAdapter

**Guard state**: `reported` (roundtable → approved → implemented → tests passed → reported)

**Roundtable log**: `open_llm_auth/docs/ROUNDTABLE_DISCUSSION_LOG_BAA_PHASE2.md`

- [x] Ran 9-persona roundtable for Phase 2 design decisions.
- [x] Key consensus: 80-step loop inside `diagnose()`, token-budget-aware compaction, GenerativeState tracking.
- [x] Created `LiveGenerativeAdapter.ts` — full implementation:
  - 80-step ReAct loop inside `diagnose()` with generative system prompt
  - Token-budget-aware compaction (70% threshold, 90% emergency, min 5 steps between)
  - `promptTokens` from LLM usage with `text.length / 4` fallback
  - GenerativeState: objective, filesCreated/Modified, testsStatus, blockers, currentPhase, compactionCount
  - Multi-format tool call parsing (XML, invoke, JSON, bash code blocks)
  - `task_complete()` gating — rejects unless tests passed
  - Sensitive file write auditing (*.sh, *.env, Makefile, etc.)
  - Progress callback every 5 iterations
  - `patch()` no-op, `verify()` final gate
- [x] Wired into `index.ts`: created `LiveGenerativeAdapter` instance, passed as `generativeAdapter` to `BulmaAssistantAgent`.
- [x] Ran validation:
  - `npx tsc --noEmit` → passed
  - `npm run build` → passed
  - 512 tests passed, 3 pre-existing failures (task-lifecycle backward compat, unrelated)
- Files changed:
  - `openbulma-v4/src/assistant/LiveGenerativeAdapter.ts` (NEW)
  - `openbulma-v4/src/index.ts`
