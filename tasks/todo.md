# Phase 2 Task Checklist

The binding design is
[2026-08-20-trading-mentor-phase-2-design.md](../docs/superpowers/specs/2026-08-20-trading-mentor-phase-2-design.md).
The detailed proposed execution plan is [plan.md](plan.md).

**Status:** Tasks 1–8 complete on feature/phase-2-unified-mentor. Task 9 awaits
Theo's human evaluation; do not begin Phase 3.

## conversation-lifecycle

- [x] **Task 1 — Idempotent display-turn migration and deletion primitive**
  - Acceptance: legacy Phase 1 threads backfill safely; one transaction deletes
    all thread-owned state and preserves sources/vector-store settings.
  - Verify: focused storage migration/deletion tests; full pytest.
  - Depends on: none.

- [x] **Task 2 — Persist browser-safe display turns for new responses**
  - Acceptance: every completed/incomplete turn has Markdown, evidence,
    diagnostics, historical configuration, and raw replay positions; encrypted
    reasoning remains server-only.
  - Verify: API-shaped chat-service fixtures; full pytest.
  - Depends on: Task 1.

- [x] **Task 3 — Safe restore and permanent-delete HTTP API**
  - Acceptance: timeline GET is browser-safe and side-effect free; DELETE is
    local-only, transactional, and preserves sources.
  - Verify: HTTP restore/delete/security tests; full pytest.
  - Depends on: Tasks 1–2.

### Checkpoint A — Conversation storage and API contract

- [x] A migrated Phase 1 thread restores through the safe timeline route.
- [x] Deletion persists after reread/reload and shared source state survives.
- [x] Encrypted reasoning state never appears in browser JSON.
- [x] Full pytest passes.

## mentor-orchestration

- [x] **Task 4 — Unified turn composition and research-depth policy**
  - Acceptance: one Jacob capability seam; Auto/Normal/Deep/Exhaustive remains
    independent from reasoning effort/mode; no future capability is built.
  - Verify: request/policy/provenance fixtures; full pytest.
  - Depends on: Checkpoint A.

- [x] **Task 5 — Compact evidence and truthful usage diagnostics**
  - Acceptance: retain all native evidence and research counts; retain accurate
    historical configuration; do not invent unavailable platform cost.
  - Verify: multi-search and missing-usage fixtures; full pytest.
  - Depends on: Task 4.

### Checkpoint B — Mentor policy and observability contract

- [x] Phase 1 exhaustive-search safeguards remain intact.
- [x] Auto/manual depth and reasoning controls are independently historical.
- [x] Full pytest passes.

## chat-foundation-ui

- [x] **Task 6 — Restored conversations, switching, titles, and delete UI**
  - Acceptance: history restores through reload/switch; title is useful; delete
    is confirmed, keyboard reachable, and permanent locally.
  - Verify: full pytest plus two-chat browser restore/delete smoke flow.
  - Depends on: Checkpoint B.

- [x] **Task 7 — Research-depth control and compact disclosures**
  - Acceptance: future-turn depth control; historical settings remain intact;
    evidence/diagnostics are compact; NaN. is diagnosed before any fix.
  - Verify: fixtures/static tests, full pytest, desktop/mobile browser smoke.
  - Depends on: Task 6.

### Checkpoint C — Persistent-chat user flow

- [x] New and restored turns show their own settings/evidence.
- [x] Delete survives reload and leaves shared sources available.
- [x] Static responsive chat remains intact; full pytest passes.

## phase-2-regression

- [x] **Task 8 — Deterministic Phase 2 regression suite**
  - Acceptance: lifecycle, migration, replay, provenance, security, semantic
    prompts, and resolved NaN. behavior are covered without paid API calls.
  - Verify: full pytest, diff check, secret scan, local browser smoke.
  - Depends on: Checkpoint C.

- [ ] **Task 9 — Explicit Phase 2 human quality checkpoint**
  - Acceptance: a small private paid evaluation confirms persistent-chat,
    normal/exhaustive research, evidence/diagnostics, and deletion behavior.
  - Verify: Theo's browser evaluation and pass/fail decision.
  - Depends on: Task 8.

### Final checkpoint — Await Theo's Phase 2 decision

- [x] All deterministic and browser checks pass.
- [ ] The branch is committed and pushed.
- [ ] Theo has made the human acceptance decision.
- [ ] Stop: do not merge to main or begin Phase 3.
