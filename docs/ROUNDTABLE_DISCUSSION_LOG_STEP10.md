# Roundtable Discussion Log - Step 10

Decision Topic: User requested deeper, repeated testing with significantly expanded memory coverage, plus non-destructive tuning until results improve.

## Participants (Expertise)
- The Core Architect (Euler) | evaluation architecture, thresholds, release gates
- The Auth Hacker (Pascal) | safe compatibility and internal-only behavior changes
- The Cognitive Systems Engineer (James) | usability scenario quality and human-facing outcomes
- The Memory Archivist (Averroes) | memory probe design and retrieval correctness metrics
- The Sandbox Guardian (Gauss) | quantitative rigor, hardening, anti-flake criteria
- Russell | Systems Engineer | run stability, repeatability, runtime noise control
- The Security Auditor | adversarial safety + fail-closed behavior
- The Telemetry Analyst | measurement quality, trend and variance reporting
- The Dashboard Weaver | visibility and operator-facing report clarity
- The Integration Ambassador | cross-project compatibility (agent_bridge-v4 + open_llm_auth)

## Round 1 - Proposals
- Euler: keep the existing harness, but add repeated measured runs and class-level memory scoring so outcomes are statistically meaningful.
- Russell: add warmup runs and measured runs with fixed config, and report aggregate pass rates + per-run drift.
- Pascal: keep API contracts unchanged; all tuning should be internal ranking/scoring behavior.
- James: expand beyond 3 memory probes; include process, calendar, priorities, communication-style, and continuity probes.
- Averroes: minimum 10 memory probes, with retrieve/chat agreement metrics per probe.
- Gauss: define quantitative gates (run count, pass thresholds, consistency requirement) so we avoid one-run luck.
- Security Auditor: verify non-regression by checking that stricter memory behavior does not increase hallucinated “I remember” claims.
- Telemetry Analyst: emit aggregate + per-probe pass rates and latency for trend tracking.
- Integration Ambassador: no destructive removals; improvements must preserve existing endpoints and capabilities.

## Round 2 - Cross-Discussion
- Russell to Averroes: “If we jump to 12 probes now, can we keep runtime manageable?”
- Averroes: “Yes, if we run sequentially and cap measured runs at 2–3 per cycle.”
- Gauss to Euler: “Need explicit consistency gate, not just aggregate averages.”
- Euler: “Agree. Require minimum passing runs, not only aggregate pass rate.”
- James to Pascal: “Can we safely include MemoryTag anchors in test seeds to improve diagnosability?”
- Pascal: “Yes, as long as they are test-only and do not alter production API behavior.”
- Security Auditor to group: “Also include memory probes that check policy/process facts; these catch false confidence and weak retrieval.”
- Telemetry Analyst: “We should store per-probe average retrieve matches and chat matches; this tells us where drift is.”
- The Dashboard Weaver: “Include a compact run-summary table first, then detailed probe tables so operators can scan quickly.”
- Integration Ambassador: “This aligns with main-project goal and does not narrow provider compatibility.”

## Round 3 - Convergence
- Consensus decision:
  1. Upgrade harness to support repeated measured runs and warmups.
  2. Expand memory suite from 3 probes to 12 probes.
  3. Add aggregate metrics: per-run summary, per-scenario pass rates, per-probe pass rates, retrieve/chat match averages, latency averages.
  4. Keep endpoint contracts unchanged; no destructive feature removal.
  5. Use new expanded results to drive targeted retrieval tuning, then repeat tests.

## Step Card Presented
- Implement harness v2 features above in `agent_bridge-v4/scripts/run-agent-usability-harness.ts`.
- Run at least 2 measured runs with extended memory suite.
- Identify worst-performing memory probe categories and tune retrieval non-destructively.
- Re-run and compare aggregate metrics.

## Human Approval Context
- User instruction in-thread: continue testing, repeat tests, add more memory tests, and keep improving non-destructively.
- Treated as direct approval to execute this step.
