# Roundtable Discussion Log - BAA Generative Split

Decision Topic: Should we split the BAA execution adapter into two distinct pathways — LiveRepairAdapter (current 15-step repair loop) and LiveGenerativeAdapter (high-iteration feature-building loop)? Review the architectural proposal in `baa_generative_split_plan.md`.

## Participants (Expertise)
- The Core Architect | structural integrity of state machines and adapter routing
- The Auth Hacker | token budgeting, rate limits, and 429 backoff under sustained bursts
- The Cognitive Systems Engineer | ReAct loop cognition, context collapse, convergence tuning
- The Memory Archivist | episode confidence differentiation for generative vs repair tasks
- The Sandbox Guardian | git checkpoint safety and execution adapter isolation
- The Security Auditor | deep-loop command execution risk and jailbreak surface area
- The Telemetry Analyst | new metrics for generative iteration tracking
- The Dashboard Weaver | UI representation of long-running generative tasks
- The Integration Ambassador | user-facing progress streaming during 100-step loops

## Round 1 - Initial Positions

**The Core Architect**: The split is architecturally sound. The current `LiveExecutionAdapter` conflates two fundamentally different workflows. The `TaskLifecycleMachine` already supports `verifying → executing` transitions (line 62 of the state machine), which is exactly the "soft rollback" path the generative adapter needs. However, I have a concern: the `AgentAssistantAgent.runAttempt()` method (lines 341-446) hardcodes the phase sequence `detect → snapshot → reproduce → diagnose → patch → verify → deploy → postcheck`. The generative adapter needs a different phase sequence — we should not fork `runAttempt()` but instead let the adapter itself declare which phases to skip. I propose an `adapter.requiredPhases()` method that the agent consults.

**The Auth Hacker**: A 100-step loop will consume roughly 6-7x the tokens of the current 15-step loop. With `open_llm_auth`'s current exponential backoff in the provider manager, a sustained 100-step burst could hit the backoff ceiling and stall mid-generation. The current backoff starts at 1s and doubles — by the 5th retry we're at 16s waits. For a generative loop, we need either: (a) a higher rate-limit budget allocated per-task in the gateway, or (b) a "burst mode" flag in the auth profile that uses a more aggressive retry schedule. I'd recommend (b) — add a `burstMode: boolean` to `AgentTaskInput` that the gateway honors with shorter backoff windows.

**The Cognitive Systems Engineer**: This is the most critical concern. I've studied ReAct loop degradation extensively. At 15 steps, the LLM maintains coherent goal pursuit. At 60-100 steps, you will see **context window saturation** — the accumulated tool results and LLM responses will push the original objective out of the effective attention window. By step 40-50, the LLM starts "forgetting" what it was building and makes contradictory edits. We absolutely need an intermediate "summarize and compact" mechanism. I propose: every 20 iterations, the adapter should call a dedicated `summarize_progress` tool that compresses the conversation history into a structured checkpoint summary, then truncates the older messages. This is non-optional for a 100-step loop.

**The Memory Archivist**: Generative tasks should produce episodes with different confidence initialization than repair tasks. A successful generative task that took 80 steps to build a feature is fundamentally different from a successful repair that took 3 steps. The current Phase 1 confidence formula (`0.95` for success + verified + 1 attempt) would give both the same score. I recommend: for generative tasks, base confidence on `verification_pass_rate * 0.7 + (1 - steps_used/max_steps) * 0.3`. This way, a clean 20-step generation scores higher than a messy 90-step one. The `taskMode` flag should propagate into the episode metadata for the confidence calculator.

**The Sandbox Guardian**: The current rollback mechanism is clean — `git reset --hard` to a checkpoint. For generative tasks, we need a more nuanced approach. A hard rollback after 80 steps of scaffolding would destroy significant valid work. I propose: (a) the generative adapter creates **incremental checkpoints** every 15-20 steps (not just at the start), and (b) on verification failure, the adapter rolls back only to the most recent incremental checkpoint, not the beginning. This preserves the bulk of valid work while reverting recent errors. The `artifacts` array on `AgentTask` already supports tracking multiple checkpoint IDs.

**The Security Auditor**: A 100-iteration loop is a 6.7x increase in attack surface for command injection. The current `validateCommand()` checks are per-command, which is good, but there's a cumulative risk: an LLM could use early benign commands to set up state (create scripts, modify PATH, write shell aliases) that later commands exploit. I recommend: (a) maintain a **per-task filesystem diff watchlist** — if the LLM writes to any executable path or modifies shell config, flag and halt, (b) add a **cumulative command budget** beyond the per-command allowlist — track total unique commands executed and alert if diversity exceeds a threshold (indicating the LLM is exploring rather than building), (c) the existing `maxFilesTouched: 10` and `maxLinesChanged: 500` constraints in `SecurityAuditor.ts` are far too restrictive for generative tasks — we need mode-aware constraint profiles.

**The Telemetry Analyst**: We need distinct metrics pipelines for the two modes. Current telemetry tracks `attempt` count and `convergenceScore` which are repair-centric. For generative mode, I need: (a) `generativeIterationCount` — how many of the 100 steps were used, (b) `generativeCompilePasses` — how many times the code compiled clean during the loop, (c) `generativeTestPassRate` — running test pass rate across iterations, (d) `contextCompactionCount` — how many times the summarize-and-compact was triggered. These feed into a generative-specific convergence score that measures "forward progress" rather than "error reduction".

**The Dashboard Weaver**: A 100-step generative task could run for 15-30 minutes. The current dashboard shows task status as a simple badge (queued/running/done/failed). For generative tasks, we need: (a) a **progress bar** showing current step / max steps, (b) a **live activity feed** showing the last 3-5 tool calls with timestamps, (c) a **phase indicator** that distinguishes "writing code" vs "running tests" vs "fixing errors" within the generative loop, (d) estimated time remaining based on average step duration. The `BaaProgressEvent` already emits per-phase — we just need the generative adapter to emit more granular sub-phase events.

**The Integration Ambassador**: Users interact with Agent primarily through Telegram. A 15-30 minute generative task with no feedback will make users think the bot crashed. We need: (a) periodic progress messages every 20-30 steps or every 5 minutes (whichever comes first), (b) the message should include: current step, last action taken, current file being modified, and a one-line summary of what was just built, (c) a "working on it..." typing indicator in Telegram during active processing, (d) an explicit "task started" and "task completed" notification pair. The EventBus already has `BaaProgressEvent` — I can hook a Telegram notifier to it, but the generative adapter needs to emit richer progress payloads than the current adapter does.

## Round 2 - Cross-Critique and Refinement

**The Core Architect** responding to **Cognitive Systems Engineer**: The context compaction proposal is essential. However, I'd go further — rather than a simple "summarize every 20 steps" approach, use a **sliding window with structured state**. The generative adapter should maintain a `GenerativeState` object that tracks: files created, tests passing, current build status, and remaining objectives. This state persists across compaction boundaries. The LLM receives this state object + last 10 messages, not the full history. This is architecturally cleaner than truncation.

**The Cognitive Systems Engineer** responding to **Core Architect**: Agreed on `GenerativeState`. I'll add: the state object should also include a `blockers` list — things the LLM tried that failed — to prevent repetitive failed approaches. This is the generative equivalent of the `ConvergenceGuard`'s "same error signature" detection.

**The Security Auditor** responding to **Sandbox Guardian**: Incremental checkpoints are good but add complexity to the rollback decision. Who decides *which* checkpoint to roll back to? If the LLM itself chooses, that's a manipulation vector — it could checkpoint right after planting malicious code, then trigger a "failure" that rolls back to just after the payload. The checkpoint selection must be automated by the adapter based on test results, not LLM suggestion.

**The Sandbox Guardian** responding to **Security Auditor**: Fair point. Checkpoint selection should be deterministic: roll back to the last checkpoint where all tests were passing. The adapter tracks `lastGreenCheckpoint` — the most recent checkpoint ID where `verify()` returned `passed: true`. If no green checkpoint exists, roll back to the initial checkpoint (start of task).

**The Auth Hacker** responding to **Telemetry Analyst**: The `contextCompactionCount` metric is also important for the gateway — each compaction likely involves an additional LLM call for summarization. The gateway should track these as separate billing events, not lump them with the main generation calls. We need a `requestType: 'generation' | 'compaction' | 'verification'` tag on auth requests.

**The Memory Archivist** responding to **Security Auditor**: The per-task filesystem diff watchlist should also feed into episode metadata. If a generative task touches executable paths or shell config, the resulting episode should get a `securityFlag: true` marker that reduces its confidence score and triggers review before it's used as a retrieval pattern.

**The Dashboard Weaver** responding to **Integration Ambassador**: We should align the progress payload format. I propose a unified `GenerativeProgress` event structure: `{ step: number, maxSteps: number, phase: 'coding' | 'testing' | 'fixing', lastAction: string, filesModified: string[], testStatus: { passed: number, failed: number, total: number }, estimatedRemainingMs: number }`. Both the dashboard and Telegram notifier consume this same event.

**The Integration Ambassador** responding to **Dashboard Weaver**: Agreed on the unified event. I'd add `summary: string` — a one-line human-readable description generated by the compaction step. This becomes the Telegram message body without additional formatting.

**The Telemetry Analyst** responding to **Cognitive Systems Engineer**: For the generative convergence score, I propose: `generativeScore = compilePasses/totalSteps * 0.35 + testPassRate * 0.40 + (1 - rollbackCount/totalSteps) * 0.25`. This captures "forward momentum" — high compile rate, improving tests, few rollbacks. If this score drops below 0.2 for 3 consecutive windows, escalate.

## Round 3 - operator Feedback: Token-Budget-Aware Compaction

**Human-in-loop input (operator)**: The fixed 20-step compaction interval is naive. The system uses a variety of models with a range of context window sizes — a fixed interval may be too frequent for large-context models (wasting summarization calls) or too infrequent for small-context models (causing context overflow before compaction fires).

**The Cognitive Systems Engineer**: operator is right — this is a fundamental flaw in my Round 1 proposal. A fixed step interval is model-ignorant. An 8k-context model could overflow by step 8 with verbose tool results, while a 200k-context model might not need compaction until step 50+. The compaction trigger must be **token-budget-aware**, not step-count-based.

**The Auth Hacker**: The model's context window size is available through the model profile in `open_llm_auth`. The gateway already knows the effective context limit for each provider/model combination. We can expose this as part of the task configuration or let the adapter query it at task start. The adapter should call `getModelContextWindow(modelProfile)` during initialization.

**The Core Architect**: This changes the `GenerativeState` design. Instead of a step counter triggering compaction, the adapter should track **running token usage** of the conversation history (system prompt + all accumulated messages + tool results). The compaction trigger becomes: `if (estimatedTokens > modelContextWindow * threshold) → compact()`. The `GenerativeState` object is always injected at the top of the context as a structured anchor, and only the N most recent messages after it are retained.

**The Telemetry Analyst**: We need to track the threshold ratio. I propose a configurable `compactionThreshold` (default 0.70 = 70% of context window). This gives 30% headroom for the next LLM response + tool result before hitting the wall. The metric becomes `contextUtilization = estimatedTokens / modelContextWindow` — we track this per-step and alert if it ever exceeds 0.90 (emergency compaction).

**The Cognitive Systems Engineer**: Agreed on 0.70 as default. But we also need a **floor** — never compact if fewer than 5 steps have occurred since the last compaction, regardless of token count. Otherwise a single massive tool result (e.g., a large file read) could trigger back-to-back compactions that thrash the state.

**The Sandbox Guardian**: For incremental checkpoints, this means checkpoint timing should also be token-aware rather than fixed-step. Create a checkpoint whenever compaction fires — this naturally aligns checkpoint boundaries with context boundaries. If the adapter compacts and then rolls back, the checkpoint matches the compacted state cleanly.

**The Memory Archivist**: Token estimation doesn't need to be exact. A simple `text.length / 4` heuristic (roughly 4 chars per token for English) is sufficient for the threshold check. We're not billing — we're just deciding when to compact. A 10-15% estimation error is acceptable since the 0.70 threshold already has 30% headroom.

**The Security Auditor**: No objections to the token-aware approach. If anything, it's safer — small-context models compact more frequently, which means less accumulated history for an attacker to exploit in prompt injection chains.

**The Dashboard Weaver**: The progress UI should show context utilization as a secondary indicator alongside step count. A thin bar showing "context: 45% used" gives the user insight into why compaction events happen.

**The Integration Ambassador**: For Telegram notifications, compaction events should be silent — users don't care about internal memory management. Only report step progress, test results, and completion.

### Revised Compaction Design (Consensus)

**Trigger**: `estimatedTokens > modelContextWindow * compactionThreshold` (default threshold: 0.70)

**Floor**: Minimum 5 steps between compactions to prevent thrashing.

**Emergency**: If `estimatedTokens > modelContextWindow * 0.90`, force immediate compaction regardless of floor.

**Token estimation**: `messageText.length / 4` (simple char-to-token heuristic, sufficient for threshold decisions).

**Context window source**: Queried from model profile at task start via `open_llm_auth` gateway or adapter config.

**Post-compaction state**: `GenerativeState` anchor (always retained) + last N messages that fit within 30% of context window.

**Checkpoint alignment**: Create incremental git checkpoint whenever compaction fires.

## Round 4 - Final Convergence

**Consensus on the split**: Unanimous agreement that the split is necessary and the proposed architecture is sound. The current 15-step repair loop should remain untouched as `LiveRepairAdapter`.

**Consensus on implementation priorities** (ordered):

1. **Phase 1 (Types + Routing)**: Add `taskMode: 'repair' | 'generate'` to `AgentTaskInput`. Route in `AgentAssistantAgent` based on mode. Add `adapter.requiredPhases()` so the agent doesn't hardcode phase sequences.

2. **Phase 2 (Adapter Core)**: Create `LiveGenerativeAdapter.ts` with:
   - Bypass reproduce phase
   - 80-step loop (consensus: 100 is too generous for an initial version; start at 80 with config override)
   - `GenerativeState` tracking object for structured context management
   - **Token-budget-aware compaction** (compact when estimated tokens exceed 70% of model context window; minimum 5-step floor; emergency compact at 90%)
   - Context window size queried from model profile at task start
   - Soft rollback: on verify failure, feed errors back into loop instead of hard reset

3. **Phase 3 (Safety + Checkpoints)**:
   - Incremental checkpoints aligned with compaction events (checkpoint fires when compaction fires)
   - Deterministic rollback to `lastGreenCheckpoint`
   - Mode-aware constraint profiles (higher `maxFilesTouched`/`maxLinesChanged` for generative)
   - Filesystem diff watchlist for executable paths
   - Automated checkpoint selection (no LLM influence)

4. **Phase 4 (Convergence + Telemetry)**:
   - Generative-specific convergence formula
   - Lenient `restraint` calculation for generative mode
   - New telemetry metrics: iteration count, compile passes, test pass rate, compaction count, **contextUtilization per-step**
   - Generative escalation threshold

5. **Phase 5 (UX + Integration)**:
   - Unified `GenerativeProgress` event structure (includes `contextUtilization` field)
   - Dashboard progress bar, live activity feed, **and context utilization indicator**
   - Telegram periodic progress notifications (compaction events are silent)
   - Gateway burst-mode support for sustained token usage

**Risks identified**:
- Context collapse remains the biggest risk — now mitigated by token-aware compaction rather than fixed intervals
- Token estimation heuristic (`length/4`) may be inaccurate for non-English or code-heavy content — acceptable given 30% headroom
- 80-step budget could still be insufficient for very large features — make it configurable
- Checkpoint-on-compaction adds git overhead proportional to compaction frequency — small-context models will checkpoint more often
- Mode-aware safety constraints must not create a "generative mode = less safe" perception — the constraints are different, not weaker

**Deferred decisions**:
- Exact `burstMode` backoff schedule for `open_llm_auth` (defer to implementation)
- Episode confidence formula for generative tasks (defer to Phase 4, needs data from real runs)
- Whether `taskMode` should be auto-detected from the objective text or always explicit (consensus: start explicit, add auto-detection later)
- Whether to use a more accurate tokenizer (e.g., tiktoken) instead of `length/4` (defer — only add if heuristic proves insufficient)

## Step Card

**What changes**: Split the BAA execution pathway into two adapters — `LiveRepairAdapter` (renamed from current `LiveExecutionAdapter`, no behavioral changes) and `LiveGenerativeAdapter` (new, for feature-building tasks).

**Why**: The current 15-step repair loop with hard rollback destroys valid generative work when it can't verify within the iteration budget. Users who ask Agent to build features get their work wiped. The generative adapter uses a higher iteration budget (80 steps), soft rollbacks (feed errors back instead of reset), token-budget-aware compaction, incremental checkpoints, and structured context management to support long-running feature development.

**Risk**: Context collapse in long loops (mitigated by token-aware compaction that adapts to model context window size), increased attack surface from more iterations (mitigated by mode-aware safety constraints and filesystem watchlist), git checkpoint overhead (mitigated by compaction-aligned intervals).

**Rollback**: The repair adapter is untouched. If the generative adapter has issues, remove the routing and all tasks fall back to repair mode. The `taskMode` flag defaults to `'repair'` so existing behavior is preserved.

**Implementation**: 5 phases, starting with type definitions and routing (Phase 1), then adapter core with token-aware compaction (Phase 2), safety (Phase 3), convergence/telemetry (Phase 4), and UX/integration (Phase 5).

## Human Approval
- Pending.
