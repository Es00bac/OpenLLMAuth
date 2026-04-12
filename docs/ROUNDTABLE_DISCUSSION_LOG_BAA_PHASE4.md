# Roundtable Discussion Log - BAA Generative Split Phase 4

Decision Topic: Add generative-specific convergence detection and telemetry emission to `LiveGenerativeAdapter` — including non-improving detection, per-iteration progress events to EventBus, and enhanced `BaaCompletedEvent` metadata.

## Participants (Expertise)
- The Core Architect | convergence integration points, event contracts
- The Auth Hacker | token/iteration budget awareness in convergence formula
- The Cognitive Systems Engineer | stuck-loop detection, approach diversity tracking
- The Memory Archivist | generative confidence formula for episode ingest
- The Sandbox Guardian | convergence-triggered safety escalation
- The Security Auditor | preventing convergence gaming by LLM
- The Telemetry Analyst | generative metrics emission and dashboard feed
- The Dashboard Weaver | progress event structure for real-time display
- The Integration Ambassador | Telegram relay of generative progress

## Round 1 - Design Proposals

**The Cognitive Systems Engineer**: The repair `ConvergenceGuard` tracks error signatures across a sliding window. For generative tasks, "convergence" means something different — the agent should be making *forward progress* (new files, passing tests, no repeated failures). I propose a `GenerativeConvergenceTracker` that evaluates three signals per iteration window:

1. **Progress rate**: Are `filesCreated + filesModified` growing? If the last 10 iterations produced zero new file changes, the agent is stuck.
2. **Test stability**: Are test results improving? Track `passingDelta` over the window. Stagnant or decreasing = non-improving.
3. **Error diversity**: Are the same errors repeating? Track last N error strings from test output. If >80% are duplicates, the agent is cycling.

Score: `progressRate * 0.35 + testImprovement * 0.40 + errorDiversity * 0.25`. Non-improving when score < 0.20 for 10+ consecutive iterations.

**The Core Architect**: The tracker should live inside `LiveGenerativeAdapter` as a lightweight class, not a standalone module like `ConvergenceGuard`. The generative adapter already tracks `GenerativeState` — the convergence check is a function that reads the state history. I propose adding a `convergenceHistory: ConvergenceSnapshot[]` to `GenerativeState`, capturing a snapshot every iteration: `{ iteration, filesCreatedCount, testsPassingCount, testFailingCount, errorHash }`. The non-improving check runs every 10 iterations.

**The Telemetry Analyst**: The adapter should emit `BaaProgressEvent` during the generative loop via the EventBus. Currently the adapter has an `onProgress` callback but the agent doesn't translate this into EventBus emissions. Two options: (a) the adapter emits directly to EventBus, or (b) the callback-to-EventBus translation happens in the agent.

I recommend (b) — keep the adapter decoupled. The agent's `runAttempt()` passes a callback that emits `BaaProgressEvent` on the EventBus. This preserves the adapter's testability.

**The Memory Archivist**: For generative task confidence in `BaaCompletedEvent`:
- Task completed + tests passing + ≤40 iterations: **0.90**
- Task completed + tests passing + >40 iterations: **0.80**
- Task completed + no tests run: **0.60** (can't verify)
- Budget exhausted + rolled back to green: **0.50**
- Budget exhausted + no green checkpoint: **0.20**
- Escalated: **0.15**

This is more nuanced than the repair formula. The iteration count matters — a feature built in 20 iterations is more trustworthy than one that took 75.

**The Dashboard Weaver**: The `BaaCompletedEvent` needs new generative-specific fields: `taskMode`, `iterationCount`, `maxIterations`, `testPassRate`, `compactionCount`, `checkpointCount`, `rollbackCount`. These feed the dashboard summary card and the Telegram completion message.

**The Integration Ambassador**: For Telegram relay during long generative loops, the progress callback should fire every 5 iterations (already implemented) AND on convergence warnings. The Telegram message should be a compact one-liner: `"🔄 Step 15/80 | 3 files | Tests: 8/10 ✅"`.

## Round 2 - Convergence

**Consensus**:

1. **GenerativeConvergenceTracker**: Lightweight class inside the adapter. Tracks per-iteration snapshots. Evaluates progress rate + test improvement + error diversity. Non-improving detection at score < 0.20 for 10+ consecutive iterations. On non-improving: break the loop early (don't waste iterations) and set a flag in the result.

2. **Progress emission**: Adapter keeps `onProgress` callback. Agent passes a callback that translates to `BaaProgressEvent` emissions. No direct EventBus dependency in the adapter.

3. **BaaCompletedEvent extensions**: Add `taskMode`, `iterationCount`, `maxIterations`, `testPassRate`, `checkpointCount`, `rollbackCount` fields. IntegrationHub uses `taskMode` to select the appropriate confidence formula.

4. **Generative confidence formula**: Implemented in IntegrationHub's `onBaaCompleted` handler, gated on `taskMode === 'generate'`.

## Step Card

**What changes**: Add `GenerativeConvergenceTracker` to the generative adapter for non-improving detection. Extend `BaaCompletedEvent` with generative metadata fields. Add generative-specific confidence formula in IntegrationHub. Wire progress callback from agent to EventBus.

**Why**: Without convergence detection, the generative loop wastes iterations on stuck loops. Without telemetry, users have no visibility into 80-step tasks. Without the confidence formula, generative episodes get repair-mode confidence scores that don't reflect iteration quality.

**Risk**: Convergence thresholds may need tuning with real tasks. Non-improving detection could trigger too early on legitimately complex tasks. Mitigated by conservative threshold (score < 0.20 for 10+ iterations).

**Rollback**: Remove convergence tracker from adapter, revert BaaCompletedEvent extensions, revert IntegrationHub confidence formula. Falls back to Phase 3 behavior.

## Human Approval
- Auto-approved per user directive (continue through Phase 5 without asking).
