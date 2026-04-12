# Human-in-the-Loop Protocol (Hard Stop)

This protocol is mandatory for every implementation slice.
Discussion is civil, expertise-led, and consensus-oriented.

## Required order
1. `roundtable_open`
2. `discussion_in_progress`
3. `discussion_completed`
4. `step_card_presented`
5. `human_approved`
6. `implementation_started`
7. `tests_passed`
8. `reported`

No code edits are allowed before state `human_approved`.
`discussion_completed` is blocked unless transcript validation passes.

## Buffered Approval Mode (5-Step Queue)
When explicitly requested by operator, approvals can be queued so five steps run before the next human check-in:
- Keep the same discussion and step-card process.
- After `step_card_presented`, consume one queued approval instead of waiting for live approval.
- After five consumed approvals, stop and return a consolidated summary.

## Mandatory 9 persona coverage
Every discussion transcript must include these persona names explicitly:
1. `The Core Architect`
2. `The Auth Hacker`
3. `The Cognitive Systems Engineer`
4. `The Memory Archivist`
5. `The Sandbox Guardian`
6. `The Security Auditor`
7. `The Telemetry Analyst`
8. `The Dashboard Weaver`
9. `The Integration Ambassador`

At least two round markers must be present (e.g., `Round 1`, `Round 2`).

## Guard script
Use:

```bash
python tools/human_loop_guard.py status
python tools/human_loop_guard.py set discussion_in_progress --note "9-persona discussion started"
python tools/human_loop_guard.py validate-discussion docs/ROUNDTABLE_DISCUSSION_LOG.md
python tools/human_loop_guard.py set discussion_completed --note "discussion validated"
python tools/human_loop_guard.py set step_card_presented --note "plain-language step card shown"
python tools/human_loop_guard.py set human_approved --note "operator approved"
python tools/human_loop_guard.py queue-approvals 5 --note "operator authorized 5-step buffered approvals"
python tools/human_loop_guard.py auto-approve --note "consume one queued approval"
python tools/human_loop_guard.py can-implement
python tools/human_loop_guard.py set implementation_started --note "coding begins"
python tools/human_loop_guard.py set tests_passed --note "targeted tests green"
python tools/human_loop_guard.py set reported --note "results delivered"
```

State file:
- `docs/HUMAN_LOOP_STATE.json`

## Required artifacts per slice
- Raw roundtable discussion log (shown first, with persona labels).
- Plain-language step card (what changes, why, risk, rollback).
- Explicit human approval text.
- Test and manual validation evidence.
