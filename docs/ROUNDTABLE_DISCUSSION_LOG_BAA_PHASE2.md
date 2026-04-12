# Roundtable Discussion Log - BAA Generative Split Phase 2

Decision Topic: Design and implement `LiveGenerativeAdapter.ts` — the high-iteration generative execution adapter with token-budget-aware compaction, GenerativeState tracking, and soft rollback.

## Participants (Expertise)
- The Core Architect | adapter structure, phase declaration, GenerativeState schema
- The Auth Hacker | token accounting, model context window resolution, LLM call budgeting
- The Cognitive Systems Engineer | ReAct loop prompt design, compaction strategy, context coherence
- The Memory Archivist | episode metadata from generative tasks, confidence signal propagation
- The Sandbox Guardian | incremental checkpoints, rollback safety, git overhead
- The Security Auditor | deep-loop command safety, filesystem watchlist, cumulative risk
- The Telemetry Analyst | generative-specific metrics emission, token utilization tracking
- The Dashboard Weaver | progress event structure for generative tasks
- The Integration Ambassador | Telegram progress relay, event payload normalization

## Round 1 - Design Proposals

**The Core Architect**: The `LiveGenerativeAdapter` must implement `AssistantExecutionAdapter` and declare `requiredPhases()` returning only `['diagnose', 'patch', 'verify']` — skipping detect, snapshot, reproduce, deploy, and postcheck. The `diagnose` method IS the generative loop (same as LiveRepairAdapter where `diagnose` contains the 15-step ReAct loop). The key structural decision: the adapter needs access to the `OpenLlmAuthClient` (same as `LiveRepairAdapter`), a `CommandRunner`, and a new `GenerativeState` object. I propose the adapter constructor takes `{ llm, maxIterations, compactionThreshold, modelContextWindow }`.

**The Auth Hacker**: For token-budget-aware compaction, we need the model's context window size. Two options: (a) accept it as a constructor parameter from config, or (b) query `open_llm_auth`'s `/v1/models` endpoint at task start. I recommend (a) for Phase 2 — simpler, no network dependency during execution. The `ChatCompletionOutput.usage` field gives us `promptTokens + completionTokens` per call. We can track cumulative `promptTokens` as our running context size estimate. When `promptTokens` exceeds `modelContextWindow * compactionThreshold`, trigger compaction. This is more accurate than the `text.length / 4` heuristic from Round 3 — we get real token counts from the LLM.

**The Cognitive Systems Engineer**: The generative ReAct loop prompt must differ fundamentally from the repair prompt. The repair prompt says "find and fix this bug." The generative prompt says "build this feature, test it yourself, fix your own errors." Key design decisions:

1. **System prompt**: Instruct the agent it's in Generative Mode. It must proactively run tests/compilers after writing code, before calling `task_complete()`.
2. **GenerativeState schema**: `{ objective, filesCreated[], filesModified[], testsStatus: { passing, failing, total }, blockers[], currentPhase: 'scaffolding' | 'implementing' | 'testing' | 'fixing', iterationCount, lastCompactionSummary }`.
3. **Compaction mechanism**: When triggered, build a structured summary from GenerativeState + last LLM response, replace the full message history with: `[system_prompt, state_summary_message, last_3_tool_results]`. The state summary becomes the new "memory" of what was built.
4. **`task_complete` gating**: The agent should only call `task_complete()` after at least one successful test/compile run. If it calls `task_complete` without evidence of verification, the adapter should reject it and prompt the agent to run tests first.

**The Memory Archivist**: When a generative task completes, the `BaaCompletedEvent` should include `taskMode: 'generate'` so the memory ingest pipeline can differentiate. For Phase 2, no changes to the episode confidence formula yet — that's Phase 4. But I want the adapter to track `filesCreated` and `testPassCount` in the task artifacts so the data is available when we implement generative-specific confidence later.

**The Sandbox Guardian**: For Phase 2, I recommend we defer incremental checkpoints to Phase 3 and use the existing single-checkpoint mechanism from `BulmaAssistantAgent.runAttempt()`. The agent already creates a checkpoint before `diagnose` runs. For now, the soft rollback means: when `verify()` fails, instead of the agent's `runAttempt` rolling back and re-entering the attempt loop, the generative adapter's `verify()` returns `passed: false` with error details, AND the adapter's internal loop feeds those errors back into the next iteration. The adapter handles retries internally within the 80-step budget, not via the agent's outer attempt loop.

Wait — this is a critical design question. Should the 80-step loop be INSIDE `diagnose()` (like the repair adapter's 15-step loop), or should it be the agent's outer `while (!task.canceled)` loop with the adapter returning `verificationPassed: false` to iterate?

I strongly recommend: **inside `diagnose()`**. The outer loop has convergence guard logic and hard rollback semantics that conflict with generative work. The generative adapter should handle everything inside `diagnose()` — including running its own verify cycles. The `patch()` method returns the final accumulated changes. The `verify()` method does one final verification. If it fails at that point, the outer loop can retry, but with a fresh 80-step budget.

**The Security Auditor**: For Phase 2, the same `validateCommand()` checks from `LiveRepairAdapter` apply. But the constraint profile needs adjustment. The current `deploy_baa_agent` dispatch in IntegrationHub sets `maxFilesTouched: 50, maxLinesChanged: 2000`. For a generative task building a feature, those limits are fine for Phase 2. I want to add one new safety check: if the agent writes to any file matching `*.sh`, `*.bash`, `.bashrc`, `.profile`, `.env`, or `Makefile`, log a warning. Don't block — just audit trail it. We'll tighten this in Phase 3 with the filesystem watchlist.

**The Telemetry Analyst**: The adapter should track and emit during the loop: `{ iteration, totalIterations, promptTokens, contextUtilization, compactionCount, lastToolName, testPassRate }`. These metrics feed into the `BaaProgressEvent` via `toPhase()` calls in the agent. For Phase 2, I recommend the adapter emit progress every 5 iterations via a callback, not via direct EventBus access (keep the adapter decoupled).

**The Dashboard Weaver**: For Phase 2, the existing `BaaProgressEvent` structure is sufficient if the adapter emits granular phase updates. The agent's `toPhase()` already emits progress. The generative adapter should call a progress callback that the agent translates into `toPhase()` calls. I need: step count, max steps, and current sub-phase ('coding' | 'testing' | 'fixing').

**The Integration Ambassador**: For Phase 2, Telegram notifications work through the existing `subscribeBaaEvents()` in IntegrationHub. The `diagnose` phase will be long-running — we should emit intermediate progress so the user sees activity. I recommend the adapter's progress callback fires every 5 iterations, and the agent translates these into `BaaProgressEvent` emissions that IntegrationHub can relay.

## Round 2 - Cross-Critique

**The Core Architect** responding to **Sandbox Guardian**: Agreed — the 80-step loop must be inside `diagnose()`. This is the cleanest approach. The adapter's `diagnose()` returns a `DiagnoseResult` with `patchCommands` being the empty list (since all commands were already executed inline during the loop), and `selectedSignature` reflecting the outcome. The `patch()` method becomes a no-op that returns the accumulated file change stats. The `verify()` method runs one final verification pass.

**The Cognitive Systems Engineer** responding to **Auth Hacker**: Using `promptTokens` from `ChatCompletionOutput.usage` is better than the heuristic. However, there's a subtlety: the first call's `promptTokens` tells us the initial context size, and it grows each iteration as we append tool results. The compaction trigger should check `promptTokens` from the most recent LLM response, since that reflects the actual context size the model just processed. After compaction, `promptTokens` should drop significantly on the next call.

**The Auth Hacker** responding to **Cognitive Systems Engineer**: Correct. One additional point: not all providers return `usage` in their responses. We should fall back to the `text.length / 4` heuristic if `usage` is undefined. Also, we need a default `modelContextWindow` value for when it's not configured — I suggest 32768 tokens as a conservative default that works with most models.

**The Security Auditor** responding to **Sandbox Guardian**: If the 80-step loop is inside `diagnose()`, the agent's outer safety checks (command validation, patch budget) in `runAttempt()` happen AFTER `diagnose()` returns. This means the agent validates the accumulated commands after the fact. For Phase 2 this is acceptable since `validateCommand()` is also called per-command inside the loop (same as LiveRepairAdapter). But it means the outer patch budget check validates the total change size, which is good — it's a final gate.

**The Sandbox Guardian** responding to **Security Auditor**: Agreed. The per-command validation inside the loop prevents individual dangerous commands. The post-loop patch budget check prevents excessive cumulative changes. Two-layer defense.

**The Memory Archivist** responding to **Telemetry Analyst**: The `testPassRate` metric should be included in the `BaaCompletedEvent` when the generative task finishes. This is the seed data for Phase 4's generative confidence formula.

**The Dashboard Weaver** responding to **Integration Ambassador**: The progress callback should include a `summary` string — a one-liner about what just happened. This becomes the Telegram message and the dashboard activity feed entry. The adapter can generate this from the last tool call name and result.

## Round 3 - Convergence

**Consensus on Phase 2 scope**: Build `LiveGenerativeAdapter` with the 80-step loop inside `diagnose()`, token-budget-aware compaction, GenerativeState tracking, and soft error feedback. Defer incremental checkpoints (Phase 3), convergence formula (Phase 4), and UX polish (Phase 5).

**Agreed adapter design:**

1. **Constructor**: `new LiveGenerativeAdapter(llm, options)` where options: `{ maxIterations: 80, compactionThreshold: 0.70, emergencyThreshold: 0.90, minStepsBetweenCompactions: 5, modelContextWindow: 32768, onProgress?: callback }`

2. **`requiredPhases()`**: Returns `['diagnose', 'patch', 'verify']`

3. **`diagnose()` (the generative loop)**:
   - Initialize `GenerativeState` from task objective
   - System prompt: generative mode instructions
   - Loop up to `maxIterations`:
     - Call LLM with tools + GenerativeState context
     - Execute tool calls (same parsing/execution as LiveRepairAdapter)
     - Track files created/modified, test results
     - Update GenerativeState
     - Check compaction trigger: if `promptTokens > modelContextWindow * compactionThreshold`, compact
     - Emit progress every 5 iterations via callback
     - On `task_complete()`: break
   - Return DiagnoseResult with accumulated commands list

4. **`patch()`**: No-op — returns accumulated stats from diagnose phase (files already written during loop)

5. **`verify()`**: Runs `buildVerificationCommands()` one final time as a gate

6. **Compaction**:
   - Triggered by token budget, not step count
   - Replaces message history with: `[system_prompt, generative_state_summary, last_3_messages]`
   - Uses `promptTokens` from LLM response; falls back to `text.length / 4` if unavailable
   - Floor: minimum 5 steps between compactions
   - Emergency: force compact at 90% context utilization

7. **Soft error feedback**: When a tool call fails (compiler error, test failure), the error is fed back as a user message in the loop — the agent self-corrects. No rollback within the loop.

8. **Other adapter methods** (detect, snapshot, reproduce, deploy, postcheck, rollback): Minimal no-op implementations since they're not in `requiredPhases()`.

**Risks for Phase 2:**
- LLM may not respect the generative mode instructions consistently across different models
- Token estimation may be inaccurate for models that don't return `usage`
- 80 iterations may exhaust rate limits on some providers
- The adapter reuses significant code from LiveRepairAdapter (tool parsing, command execution) — should extract shared utilities

**Deferred to later phases:**
- Incremental checkpoints (Phase 3)
- Filesystem watchlist blocking (Phase 3, Phase 2 only logs warnings)
- Generative convergence formula (Phase 4)
- Dashboard progress bar (Phase 5)
- Telegram periodic notifications (Phase 5)

## Step Card

**What changes**: Create `LiveGenerativeAdapter.ts` — a new execution adapter for feature-building tasks with an 80-step ReAct loop, token-budget-aware context compaction, GenerativeState tracking for coherent long-running generation, and soft error feedback (errors fed back into loop instead of rollback).

**Why**: Phase 1 added the routing and type infrastructure. Phase 2 delivers the actual generative execution capability. Without this, tasks dispatched with `taskMode: 'generate'` fall back to the repair adapter (since `generativeAdapter` is not yet provided).

**Risk**: LLM coherence over 80 steps (mitigated by compaction). Rate limit exhaustion (mitigated by existing backoff in open_llm_auth). Reused tool-parsing code from LiveRepairAdapter (acceptable duplication for now — extract shared utilities in a future cleanup pass).

**Rollback**: Delete `LiveGenerativeAdapter.ts` and remove the adapter wiring in `index.ts`. All tasks fall back to repair mode. Zero impact on existing functionality.

**Implementation scope**: One new file (`LiveGenerativeAdapter.ts`), one wiring change (`index.ts` to instantiate and pass to agent), minor test additions.

## Human Approval
- Pending.
