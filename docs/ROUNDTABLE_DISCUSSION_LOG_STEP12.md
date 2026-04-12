# Roundtable Discussion Log - Step 12

Decision Topic: Are current failures (`schedule-boundary`, `assistant-scope`) caused by missing assistant capability, memory selection/synthesis, or both? Decide next test step.

## Participants (Expertise)
- The Core Architect | architecture correctness and root-cause isolation
- The Auth Hacker | gateway/provider path integrity and routing impacts
- The Cognitive Systems Engineer | behavior policy and planning/delegation dynamics
- The Memory Archivist | retrieval/ranking behavior and source weighting
- The Sandbox Guardian | deterministic experiment design
- The Security Auditor | abstain-vs-recall safety tradeoff analysis
- The Telemetry Analyst | metric interpretation and uplift thresholds
- The Dashboard Weaver | usability/observability signal clarity
- The Integration Ambassador | openbulma-v4 main alignment and gateway boundary checks

## Round 1 - Initial Positions
- The Core Architect: likely both, but primary issue is memory selection/synthesis contamination.
- The Auth Hacker: not mainly auth/gateway; failures are `chat_under_min` with retrieval side passing.
- The Cognitive Systems Engineer: schedule/scope recall is high-dimensional and can fail while abstain still passes.
- The Memory Archivist: target facts are likely outranked by high-weight noisy sources.
- The Sandbox Guardian: run controlled A/B to separate capability gap from memory gap.
- The Security Auditor: abstain improvements can reduce hallucination while reducing recall completeness.
- The Telemetry Analyst: decisive signal is pass-rate uplift under oracle memory.
- The Dashboard Weaver: current diagnostics already show failure reasons; keep next step measurable and narrow.
- The Integration Ambassador: issue is in openbulma-v4 memory-answer path; gateway path can be tested separately.

## Round 2 - Discussion
- The Core Architect to group: “Given retrieve pass + chat fail, we need a single decisive experiment before more tuning.”
- The Memory Archivist: “Use same failing probes; replace memory source with oracle snippets and compare.”
- The Sandbox Guardian: “Hold model/settings constant, vary only memory context path.”
- The Telemetry Analyst: “Interpretation threshold: >20pp uplift indicates memory-selection bottleneck.”
- The Auth Hacker: “Optional follow-on: swap provider path if needed, but first isolate app-layer memory path.”
- The Security Auditor: “Keep safety gates intact; test should not weaken abstain policy.”
- The Integration Ambassador: “No endpoint contract changes required for this diagnostic.”

## Round 3 - Convergence
- Consensus diagnosis:
  - Primary: memory selection/synthesis issue.
  - Secondary: behavior synthesis under recall constraints.
  - Not primary: gateway/auth transport failure for these two probes.
- Consensus next step:
  1. Implement a focused A/B diagnostic for `schedule-boundary` and `assistant-scope`.
  2. Condition A: normal pipeline (existing query path).
  3. Condition B: oracle memory context only (gold snippets), same scoring criteria.
  4. Compute per-probe uplift and average uplift.

## Step Card Presented
- Add a dedicated diagnostic script in `openbulma-v4/scripts/` to run A/B and write markdown+json artifacts.
- Run it against isolated runtime state and report:
  - per-probe A vs B pass rates
  - average uplift in percentage points
  - interpretation (`>20pp` memory-selection bottleneck; `<5pp` capability bottleneck; otherwise mixed).

## Human Approval Context
- User response: “sure.”
- Treated as explicit approval to execute Step 12 diagnostic implementation and run.
