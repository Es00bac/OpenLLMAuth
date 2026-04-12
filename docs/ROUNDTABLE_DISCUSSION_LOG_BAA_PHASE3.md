# Roundtable Discussion Log - BAA Generative Split Phase 3

Decision Topic: Implement incremental checkpoints aligned with compaction, deterministic rollback to `lastGreenCheckpoint`, mode-aware constraint profiles, and filesystem diff watchlist for the generative adapter.

## Participants (Expertise)
- The Core Architect | adapter structure, checkpoint integration points
- The Auth Hacker | token accounting continuity across checkpoint/rollback
- The Cognitive Systems Engineer | context coherence after rollback, prompt continuity
- The Memory Archivist | episode metadata for checkpoint-aware tasks
- The Sandbox Guardian | incremental checkpoints, rollback determinism, git overhead
- The Security Auditor | filesystem watchlist enforcement, constraint profile escalation
- The Telemetry Analyst | checkpoint/rollback event metrics
- The Dashboard Weaver | progress event structure for checkpoint events
- The Integration Ambassador | Telegram relay of checkpoint/rollback events

## Round 1 - Design Proposals

**The Sandbox Guardian**: Phase 3 centers on three things: (1) incremental checkpoints that fire when compaction fires, (2) deterministic rollback to the last checkpoint where tests passed (`lastGreenCheckpoint`), and (3) a filesystem watchlist that blocks (not just warns) writes to sensitive paths.

For incremental checkpoints, the natural trigger point is the compaction event in `LiveGenerativeAdapter.diagnose()`. When the adapter compacts context, it also creates a git checkpoint. This gives us a restore point that's semantically aligned with what the LLM "remembers" — if we roll back to a checkpoint, the compaction summary at that point accurately describes what the code looked like. The adapter tracks `lastGreenCheckpoint: string | null` — updated whenever a tool call to `bash_run_command` with a test/build command succeeds AND the `testsStatus.failing === 0`.

For rollback: when the 80-step budget is exhausted without `task_complete()`, the adapter's `verify()` method runs a final check. If it fails, we need to decide: do we roll back to `lastGreenCheckpoint`, or leave the code as-is and let the outer agent loop retry? I recommend: roll back to `lastGreenCheckpoint` if one exists, leave as-is if none exists (the initial checkpoint from `runAttempt()` is the safety net).

**The Core Architect**: The checkpoint must be created inside `diagnose()` since that's where the loop runs. Currently `GitCheckpoint.create()` takes `(repoPath, taskId, attempt)`. For incremental checkpoints we need a way to distinguish them — I propose adding an optional `label` parameter: `create(repoPath, taskId, attempt, label?)` where label is like `'compaction-3'` for the 3rd compaction. The tag name becomes `baa-checkpoint-taskid-a2-compaction-3-timestamp`.

The adapter needs `repoPath` to create checkpoints. Currently `diagnose()` receives the `AgentTask` which has `task.input.repoPath`. That's sufficient — no constructor changes needed.

**The Security Auditor**: For the filesystem watchlist, Phase 2 logs warnings for sensitive file patterns. Phase 3 should upgrade to blocking. But we need two tiers:

1. **Hard block** (never allow, regardless of mode): `.bashrc`, `.profile`, `.ssh/*`, `.gnupg/*`, `*.pem`, `*.key`
2. **Soft block** (block in repair mode, warn in generative mode): `.sh`, `.bash`, `.env`, `Makefile`

The rationale: a generative task might legitimately need to create a shell script or Makefile as part of feature scaffolding. But it should never write to user auth files. The constraint check should happen inside `executeFsWriteFile()` before the actual `fs.writeFileSync` call.

Also: mode-aware constraint profiles. The current `maxFilesTouched: 50, maxLinesChanged: 2000` from IntegrationHub is fine for generative tasks. But we should formalize this in the constraints: add a `taskMode` field to `AgentTaskConstraints` so `SafetyPolicy.validatePatchBudget()` can apply different thresholds.

**The Auth Hacker**: After a rollback, the LLM's context includes tool results from code that no longer exists. If we compact at checkpoint time AND roll back to that checkpoint, the compaction summary accurately describes the state. But any messages appended between the checkpoint and the rollback are stale. The adapter should: (1) rollback the git state, (2) reset messages to the compaction summary from that checkpoint point, (3) append a new user message explaining: "Code was rolled back to checkpoint X because tests were failing. Here's what happened since then: [summary of failed attempts]."

This preserves learning — the LLM knows what it tried and why it didn't work.

**The Cognitive Systems Engineer**: Agreed on the rollback-aware context reset. The rollback message should be structured:
```
ROLLBACK NOTICE: Code has been restored to the state at iteration {N} (checkpoint {id}).
Reason: Tests were failing after {M} iterations without resolution.
What was attempted since the checkpoint:
- [files modified]
- [errors encountered]
Your task objective remains: {objective}
Please try a different approach.
```

This gives the LLM enough context to avoid repeating the same mistakes.

**The Memory Archivist**: When a task involves rollbacks, the `BaaCompletedEvent` should include `rollbackCount` and `checkpointCount` in the task artifacts. These feed into the confidence formula in Phase 4 — tasks that required rollbacks should have lower initial confidence.

**The Telemetry Analyst**: Checkpoint events should be emitted via the progress callback: `{ type: 'checkpoint', checkpointId, iteration, isGreen }`. Rollback events: `{ type: 'rollback', fromIteration, toCheckpointId, reason }`. These are separate from the iteration progress events.

**The Dashboard Weaver**: The progress callback should fire on checkpoint and rollback events immediately (not waiting for the 5-iteration interval). These are significant state changes the user should see in real-time.

**The Integration Ambassador**: Agreed — checkpoint and rollback events should trigger Telegram notifications immediately. A rollback means something went wrong, and the user should know.

## Round 2 - Cross-Critique

**The Core Architect** responding to **Sandbox Guardian**: The `lastGreenCheckpoint` approach is clean. But we need to handle the case where the adapter compacts at step 15 (creating checkpoint A), tests pass at step 20, then compacts again at step 35 (creating checkpoint B), but tests fail between step 25-35. `lastGreenCheckpoint` should be checkpoint A (the last checkpoint BEFORE the failing tests started), not checkpoint B (created after tests started failing). This means: only update `lastGreenCheckpoint` when creating a new checkpoint AND tests are currently passing.

**The Sandbox Guardian** responding to **Core Architect**: Correct. The logic is: on every compaction event, create checkpoint. But only update `lastGreenCheckpoint = newCheckpointId` if `generativeState.testsStatus.failing === 0` at that moment. If tests are failing when we compact, we still create the checkpoint (for audit purposes) but don't mark it as green.

**The Security Auditor** responding to **Auth Hacker**: The rollback context reset is important for security too. If we don't reset the context, the LLM has tool results showing file contents that have been rolled back. A malicious payload in those results could influence the LLM's next actions on the restored codebase. Resetting to the compaction summary eliminates that vector.

**The Cognitive Systems Engineer** responding to **Security Auditor**: Good point. The compaction summary is a structured, adapter-generated message — not raw LLM/tool output. It's a trusted context boundary. After rollback: `[system_prompt, compaction_summary_from_checkpoint, rollback_notice]` — no raw tool results from the rolled-back period.

**The Auth Hacker** responding to **Core Architect**: One more concern — `GitCheckpoint.create()` runs `git add -A && git commit`. In a fast generative loop, we might checkpoint every 10-15 steps. Each checkpoint involves disk I/O for the git operations. For a repo with many files, this could add noticeable latency. I recommend: only checkpoint files that the adapter knows were modified (tracked in `generativeState.filesModified`), not the entire working tree. Use `git add <specific files>` instead of `git add -A`.

**The Sandbox Guardian** responding to **Auth Hacker**: Good optimization. The adapter already tracks `filesCreated` and `filesModified` in GenerativeState. We can pass these to a new `GitCheckpoint.createIncremental(repoPath, taskId, attempt, label, files)` method that stages only the specified files.

## Round 3 - Convergence

**Consensus on Phase 3 scope:**

1. **Incremental checkpoints**:
   - Trigger: aligned with compaction events (same condition: token budget threshold)
   - Method: new `GitCheckpoint.createIncremental()` that stages only modified files
   - Track `lastGreenCheckpoint` — updated only when checkpoint is created AND `testsStatus.failing === 0`

2. **Deterministic rollback**:
   - Triggered: when 80-step budget exhausted without `task_complete()`
   - Target: `lastGreenCheckpoint` if exists, else leave as-is (outer agent loop has initial checkpoint)
   - Context reset: replace messages with `[system_prompt, checkpoint_compaction_summary, rollback_notice]`
   - Rollback notice includes: what was attempted, what failed, instruction to try different approach

3. **Mode-aware constraint profiles**:
   - Add `taskMode` to `AgentTaskConstraints` (already exists on `AgentTaskInput`)
   - SafetyPolicy: No changes needed — current limits (50 files, 2000 lines) are sufficient for generative
   - Defer per-mode budget differentiation to Phase 4 if needed

4. **Filesystem watchlist upgrade**:
   - Hard block (both modes): `.bashrc`, `.profile`, `.ssh/*`, `.gnupg/*`, `*.pem`, `*.key`
   - Soft block (block in repair, warn in generative): `.sh`, `.bash`, `.env`, `Makefile`
   - Enforcement point: inside `executeFsWriteFile()` in both adapters
   - New function: `checkFilesystemWatchlist(filePath, taskMode)` in SafetyPolicy.ts

5. **Progress events**:
   - Checkpoint/rollback events fire immediately via progress callback
   - Include in BaaCompletedEvent: `rollbackCount`, `checkpointCount`

**Deferred:**
- Per-mode constraint budgets (Phase 4 if needed)
- Convergence formula incorporating rollback count (Phase 4)

## Step Card

**What changes**: Add incremental checkpoints aligned with compaction events, deterministic rollback to last green checkpoint, filesystem watchlist enforcement (hard block + soft block tiers), and rollback-aware context reset to `LiveGenerativeAdapter`. Minor additions to `GitCheckpoint` and `SafetyPolicy`.

**Why**: Phase 2 delivered the generative loop but deferred safety hardening. Without incremental checkpoints, a failed 80-step generation loses all progress. Without the filesystem watchlist upgrade, the agent can write to sensitive system files. Without rollback-aware context, the LLM repeats failed approaches.

**Risk**: Git overhead from incremental checkpoints (mitigated by staging only modified files). Rollback context reset loses some recent LLM reasoning (mitigated by structured rollback notice with failure summary). Hard-blocked file patterns may be too restrictive for some legitimate tasks (mitigated by two-tier system with generative mode allowing soft-blocked files).

**Rollback**: Remove incremental checkpoint calls from `LiveGenerativeAdapter.diagnose()`, revert `GitCheckpoint` to single-checkpoint mode, revert `SafetyPolicy` watchlist changes. Falls back to Phase 2 behavior (single checkpoint, warn-only for sensitive files).

**Implementation scope**: Modify `GitCheckpoint.ts` (add `createIncremental`), `SafetyPolicy.ts` (add `checkFilesystemWatchlist`), `LiveGenerativeAdapter.ts` (integrate checkpoints with compaction, add rollback logic). Minor type additions.

## Human Approval
- Pending.
