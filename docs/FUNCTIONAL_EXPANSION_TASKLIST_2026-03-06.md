# Functional Expansion Tasklist (2026-03-06)

Scope: OpenBulma audit item 2 for `open_llm_auth`

## Expert Personas Applied
- Core Architect: provider wiring and gateway integration paths.
- Auth Hacker: Bedrock SigV4 and credential-token parsing resilience.
- Security Auditor: fail-closed auth behavior and parser hardening tests.

## Plan
1. Wire Bedrock provider (`bedrock-converse-stream`) into runtime provider construction.
2. Refine Claude local credential extraction to tolerate schema drift.
3. Add tests for Bedrock provider behavior and Claude parser edge cases.
4. Run targeted test suite and record concrete results.

## Task Checklist
- [x] Create tasklist and bind work to relevant expert personas.
- [x] Integrate `BedrockConverseProvider` into provider exports and `ProviderManager`.
- [x] Harden `_refresh_anthropic_from_claude_cli` extraction logic.
- [x] Add/extend tests for Bedrock and token parsing.
- [x] Execute tests and capture actual pass/fail outputs.

## Work Log
- 2026-03-06: Created tracking document and formalized implementation sequence.
- 2026-03-06: Added Bedrock provider export and ProviderManager dispatch for `bedrock-converse-stream`.
- 2026-03-06: Refactored Claude credential refresh to support schema drift, explicit credential path override, strict expiry checks, and safer candidate selection.
- 2026-03-06: Added tests:
  - `tests/test_provider_manager.py` (Bedrock build path assertion)
  - `tests/test_bedrock_provider.py` (SigV4/header/region/response mapping)
  - `tests/test_auth_manager_parsing.py` (Claude schema variants, rejection cases, and Anthropic refresh gating)
- 2026-03-06: Test execution:
  - `pytest -q tests/test_provider_manager.py tests/test_bedrock_provider.py tests/test_auth_manager_parsing.py` -> `15 passed`
  - `pytest -q tests -k 'not TestLiveAnthropicAdapter'` -> `55 passed, 7 deselected`
  - `pytest -q tests` -> fails only for live integration class `TestLiveAnthropicAdapter` due connection errors in this environment.
