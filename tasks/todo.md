# Phase 4 Task Checklist: Trader Profile / Editable Memory

The binding proposed design is
[2026-08-25-trading-mentor-phase-4-design.md](../docs/superpowers/specs/2026-08-25-trading-mentor-phase-4-design.md).
The detailed proposed execution plan is [plan.md](plan.md).

**Status:** Design and plan complete; awaiting Theo's implementation approval.
Phase 4 is based on Phase 2 closure `f809193ef2749dbe53c0af14e5d3196420c896f9`.
Phase 3/3B remains archived on `feature/phase-3-knowledge-assimilation` and is
not part of this branch.

## profile-storage

- [x] **Task 1 — Local profile schema and transactional lifecycle**
  - Acceptance: constrained versioned records and origin fields migrate safely;
    global profile state survives thread deletion; destructive actions are
    atomic and replacement identity is unambiguous.
  - Verify: 14 focused storage tests and 52 full pytest tests passed.
  - Depends on: none.

## profile-context

- [x] **Task 2 — Profile service and bounded deterministic selection**
  - Acceptance: only confirmed current items select through the documented
    intent, per-item applicability, deterministic ranking, dedupe, and cap
    policy; categories alone never select. Exact source lookup normally has no
    context; structural constraints remain eligible where policy defines them.
    Diagnostics retain safe reason/tier only. Output is within six items/1,200
    characters.
  - Verify: focused profile tests and full pytest.
  - Depends on: Task 1.

## mentor-profile-integration

- [x] **Task 3 — Sol profile context and controlled explicit write tool**
  - Acceptance: user context is bounded and non-authoritative; each turn has at
    most one idempotent validated write/proposal; Phase 2 raw-source contracts
    remain unchanged.
  - Verify: Responses-shaped fixtures, citation/compaction regressions, full
    pytest, and current official tool-schema check.
  - Depends on: Task 2.

## profile-api

- [x] **Task 4 — Safe loopback Profile API**
  - Acceptance: local browser-safe projections and validated mutations; no raw
    replay/tool reasoning disclosure.
  - Verify: focused server tests and full pytest.
  - Depends on: Tasks 1–3.

### Checkpoint A — Profile foundation

- [x] Cross-thread relevant context works; irrelevant context stays out.
- [x] Edit/delete/supersede/conflict changes active context deterministically.
- [x] Full pytest passes; Phase 2 boundaries remain intact.

## profile-ui

- [x] **Task 5 — Restrained static Trader Profile panel**
  - Acceptance: grouped current items, separate tentative proposals, history,
    provenance, and explicit edit/confirm/reject/archive/delete controls.
  - Verify: static/server tests, full pytest, local desktop/mobile browser smoke.
  - Depends on: Checkpoint A.

- [x] **Task 6 — Compact chat profile-update affordances**
  - Acceptance: chat acknowledges saved/proposed updates without turning the
    conversation into an admin interface or reactivating history.
  - Verify: fixtures, full pytest, and two-thread browser smoke.
  - Depends on: Task 5.

### Checkpoint B — User-controlled personalisation

- [x] Profile is inspectable/editable/deletable and remains local.
- [x] Deleted/superseded/inferred records do not influence later prompts or
  cause historic chat/replay statements to become current profile truth.
- [x] Direct source teaching still needs native raw citations.

## profile-evaluation

- [x] **Task 7 — Deterministic Phase 4 regression suite**
  - Acceptance: profile lifecycle, relevance, provenance, source authority,
    context budget, thread boundary, and Phase 2 regressions are covered.
  - Verify: full pytest, browser smoke, diff/secret review; no paid calls.
  - Depends on: Checkpoint B.

- [ ] **Task 8 — Theo's explicit Phase 4 human quality gate**
  - Acceptance: Theo evaluates cross-thread recall, control, research guidance,
    source integrity, UX, and cost/context quality, then records pass/fail.
  - Verify: full pytest before Theo's local private evaluation.
  - Depends on: Task 7.

### Final checkpoint — Await Theo's Phase 4 decision

- [ ] All deterministic and browser checks pass.
- [ ] The feature branch is committed and pushed.
- [ ] Theo has made the human acceptance decision.
- [ ] Stop: do not start Phase 5 or merge to main.
