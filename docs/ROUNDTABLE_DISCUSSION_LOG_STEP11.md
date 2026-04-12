# Roundtable Discussion Log - Step 11

Decision Topic: After Step 10, overall tests pass but memory still has weak probes (`schedule-boundary`, `assistant-scope`, `anchor-memory-m9`) and no explicit anti-hallucination memory tests. Decide next implementation slice.

## Participants (Expertise)
- The Core Architect (Euler) | architecture consistency, non-destructive improvement planning
- The Auth Hacker (Pascal) | compatibility and contract-safe internal behavior tuning
- The Cognitive Systems Engineer (James) | user-facing response quality and continuity behavior
- The Memory Archivist (Averroes) | retrieval grounding and memory correctness
- The Sandbox Guardian (Gauss) | stability, deterministic gating, anti-flake behavior
- Russell | systems reliability, repeat-run consistency and noise control
- The Security Auditor | hallucination resistance and fail-safe behavior
- The Telemetry Analyst | metrics quality, trend reporting, probe diagnostics
- The Dashboard Weaver | report clarity and operator observability
- The Integration Ambassador | cross-project alignment (openbulma-v4 main + open_llm_auth support)

## Round 1 - Initial Positions
- The Core Architect: add one focused “evidence-coverage gate” so personal recall answers must reflect retrieved memory terms.
- The Auth Hacker: keep endpoint contracts unchanged; implement entirely in internal chat/memory ranking path.
- The Cognitive Systems Engineer: reduce accidental task-delegation replies in chat for direct planning prompts.
- The Memory Archivist: for explicit `MemoryTag` recalls, force memory-grounded response instead of generic “I don’t have it.”
- The Sandbox Guardian: avoid broad rewrites; small deterministic checks only.
- Russell: run the same 2-run extended suite before/after to validate signal.
- The Security Auditor: add negative-memory probes (abstain tests) to track false recall risk.
- The Telemetry Analyst: include separate “hallucination guard” section in report with pass/fail counts.
- The Dashboard Weaver: keep report scan-friendly with aggregate first, then failures.
- The Integration Ambassador: this slice helps openbulma usability without narrowing gateway/provider scope.

## Round 2 - Discussion
- The Core Architect to Memory Archivist: “Can we generalize beyond MemoryTag without hacks?”
- The Memory Archivist: “Yes: use evidence-term coverage from top recalled memories; force fallback when reply lacks those terms.”
- Russell to Sandbox Guardian: “Will deterministic forcing hurt latency?”
- The Sandbox Guardian: “Minimal impact; reuse existing retrieval results and string checks only.”
- Security Auditor to group: “Add 2-3 unknown-fact probes requiring abstain language and no fabricated numeric secrets.”
- The Cognitive Systems Engineer: “Also gate BAA tool exposure on intent to prevent delegation-only replies in user-facing tasks.”
- Dashboard Weaver: “Add per-probe failure list in report so next tuning loop is obvious.”

## Round 3 - Convergence
- Consensus slice:
  1. Strengthen memory-grounded fallback forcing for explicit recall intents when answer lacks evidence coverage.
  2. Keep BAA tool disabled unless dispatch intent is detected for the user prompt.
  3. Extend harness with anti-hallucination memory probes and report section.
  4. Re-run repeated extended suite on clean state and compare.

## Step Card Presented
- Patch `IntegrationHub.ts` for evidence-coverage forcing + explicit MemoryTag handling + intent-gated tool exposure.
- Patch harness to add abstain probes (unknown memory checks) and include aggregate guard metrics.
- Run `check`, `build`, then repeated live harness on clean state (`2 runs`, extended suite).

## Human Approval Context
- User message: “I approve” after prior cycle summary and next-step options.
- Treated as explicit approval to execute this Step 11 card.

## Implementation Consult Addendum (9 Personas)
Before Step 11 edits, a second implementation consult pass was run across all 9 personas from `project_audit/expertise_analysis.md`.

- The Core Architect: enforce exact MemoryTag evidence and abstain when evidence confidence is weak.
- The Auth Hacker: keep endpoint contracts unchanged; limit changes to internal retrieval/ranking/harness logic.
- The Cognitive Systems Engineer: keep planning prompts answered in-chat; avoid delegation-only artifacts in usability responses.
- The Memory Archivist: apply deterministic source filtering for explicit tag and personal-recall paths.
- The Sandbox Guardian: run in isolated state/runtime and require repeated runs for stability.
- The Security Auditor: add explicit anti-hallucination probes with abstain markers + forbidden secret/hostname patterns.
- The Telemetry Analyst: include per-probe failure-code diagnostics and hallucination signal counts.
- The Dashboard Weaver: keep report scan-first (failure queue and abstain queue first).
- The Integration Ambassador: preserve OpenBulma/open_llm_auth contract shapes while tuning internals.

Consensus addendum:
1. Enforce stricter explicit-tag grounding (`MemoryTag Mx` must match evidence rows).
2. Filter low-trust recall sources (`memory:association`, report/script artifacts, and `foundation:recent-chat` in recall paths).
3. Add abstain probes and machine-comparable failure diagnostics in harness output.
