# Phase 6.5 Source-Grounded Teaching Pilot Plan

**Scope:** Repair the confirmed multi-turn teaching defects, prepare a private two-topic reference set, and build a disabled local A/B/C evaluation harness. No paid calls, uploads, vector-store changes, live activation, or Phase 7 work.

## Task 1 — Freeze the observed failures

**Files:** `tests/test_chat_service.py`, `tests/test_source_scope.py`

1. Add deterministic regressions for the exact mixed profile/source turns, contextual source challenges, quoted mentor names, native-versus-literal citations, and real timestamp-range format.
2. Run the focused tests and retain the expected pre-fix failures.

**Complete when:** The tests reproduce zero research, wrong scope selection, literal citation markers, and unsupported timestamp handling without private corpus content.

## Task 2 — Repair source intent, scope, and evidence integrity

**Files:** `src/mentor/chat_service.py`, `src/mentor/source_scope.py`, `src/mentor/prompts.py`, focused tests

1. Classify profile inspection separately from source teaching/challenge intent; a questionnaire-field match alone must not suppress research.
2. Build a bounded contextual research query from the current follow-up and recent lesson state; prior assistant prose is query context, never evidence.
3. Exclude quoted prior text from mentor-scope overrides and inherit a still-enabled prior lesson scope only for contextual follow-ups.
4. Remove unverified literal citation markers from displayed answers while retaining native citation objects.
5. Validate exact timestamps against cited evidence in numeric and `HH:MM:SS.mmm` transcript ranges.
6. Scope the general mentor policy to any enabled source corpus while preserving legacy Jacob behavior; add capability-honest exercise guidance.

**Verification:**

```powershell
D:\projects\trading-mentor\.venv\Scripts\python.exe -m pytest -q tests/test_source_scope.py tests/test_chat_service.py -k "context or profile or citation or timestamp or project"
```

**Complete when:** The exact human follow-ups plan fresh bounded research, source scope is permission-safe, unsupported citation text is not displayed as native citation, and existing profile semantics remain green.

## Task 3 — Build the private two-topic reference set

**Private files:** `data/pilots/phase-6-5/reference-set.json`, `coverage-log.json`, `benchmark-reference.json`, `lesson-packages.json`

1. Read contiguous Garrett and Afyz source sections for the two approved topics.
2. Record source/revision identity, timestamp range, passage plus context, conditions, contrast, support state, visual dependence, and transcript uncertainty.
3. Record reviewed and unreviewed source areas explicitly and flag a small set of disputed interpretations for Theo.
4. Confirm all private files remain ignored and contain no remote resource identifiers.

**Complete when:** Both topics have traceable Garrett/Afyz support and no private excerpt or answer key is tracked by Git.

## Task 4 — Add the disabled A/B/C pilot harness

**Files:** `src/mentor/teaching_pilot.py`, `tests/test_teaching_pilot.py`, `tests/fixtures/teaching_pilot_cases.json`

1. Define compact lesson-continuity state with explicit source scope/version, learner state, exercise roles, disputes, and next action.
2. Prepare equivalent request envelopes for repaired retrieval, direct original sections, and prepared-note hybrid variants.
3. Keep direct passages and prepared notes external to tracked fixtures; validate source-scope membership and invalidate disabled-mentor material.
4. Make live dispatch disabled by default and require a conservative budget reservation before every eventual paid call; unknown usage does not release reserved spend.
5. Freeze approximately twelve content-free benchmark cases and critical-failure criteria separately from private reference answers.

**Verification:**

```powershell
D:\projects\trading-mentor\.venv\Scripts\python.exe -m pytest -q tests/test_teaching_pilot.py
```

**Complete when:** Mocked variants receive the same learner dialogue, teaching policy, and source permissions; accidental live execution fails closed; source leakage and budget overruns are rejected.

## Task 5 — Regression, browser, privacy, and review gate

1. Run relevant source/profile/chat/citation/replay suites.
2. Run the full deterministic suite.
3. Smoke-test desktop and 390px project chat behavior without paid/provider calls.
4. Review the diff for private corpus text, paths, IDs, prompt answer leakage, scope regressions, and unnecessary abstractions.
5. Resolve every P0/P1 finding.

**Commands:**

```powershell
D:\projects\trading-mentor\.venv\Scripts\python.exe -m pytest -q tests/test_source_scope.py tests/test_profile.py tests/test_chat_service.py tests/test_server.py tests/test_browser_smoke.py tests/test_teaching_pilot.py
D:\projects\trading-mentor\.venv\Scripts\python.exe -m pytest -q
git diff --check
git status --short
```

**Complete when:** Deterministic and browser verification is green, private artifacts are ignored, no live call path was used, and the independent review has no P0/P1 issue.

## Task 6 — Commit, push, and stop before paid evaluation

1. Commit only safe code, tests, synthetic fixture metadata, checklist, and content-free documentation.
2. Push `feature/phase-6-5-teaching-pilot` without merging or activating it.
3. Verify remote ancestry and a clean worktree.
4. Report the frozen cases, proposed smallest paid batch, model/reasoning settings, conservative reservation, and request Theo's explicit USD ceiling.

**Stop condition:** No paid comparison, upload, vector-store mutation, live activation, full-corpus compilation, or Phase 7 work is permitted in this plan.
