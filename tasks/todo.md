# Phase 3 Task Checklist

Binding design: [2026-08-21-trading-mentor-phase-3-design.md](../docs/superpowers/specs/2026-08-21-trading-mentor-phase-3-design.md).
Authoritative task detail: [plan.md](plan.md).

**Status:** Planning complete; implementation requires separate Theo approval.
Stay on feature/phase-3-knowledge-assimilation. Do not begin Phase 4.

## knowledge-library

- [ ] **Task 1 — Generic library identity and additive migration**
  - Depends on: none. Verify: focused migration tests; full pytest.
- [ ] **Task 2 — Idempotent Jacob registry migration and change detection**
  - Depends on: Task 1. Verify: importer/registry tests; full pytest.

### Checkpoint A — Library safety

- [ ] Phase 2 conversations/citations remain readable; Jacob backfill is
  idempotent; full pytest passes.

## source-anchors

- [ ] **Task 3 — Durable anchor model and deterministic validation**
  - Depends on: Checkpoint A. Verify: anchor drift/offset/timestamp/name tests;
    full pytest.

## compilation-lifecycle

- [ ] **Task 4 — Compilation runs and immutable candidate snapshots**
  - Depends on: Task 3. Verify: lifecycle transition/isolation tests; full pytest.

## source-extraction

- [ ] **Task 5 — Typed derived-record schema and persistence**
  - Depends on: Task 4. Verify: family/facet/provenance tests; full pytest.
- [ ] **Task 6 — Mocked per-source extraction with versioned prompts**
  - Depends on: Task 5. Verify: fake Responses fixtures only; full pytest.
- [ ] **Task 7 — Deterministic and independent semantic claim validation**
  - Depends on: Task 6. Verify: hash/range/semantic fixtures; full pytest.

### Checkpoint B — Mechanical compiler proof

- [ ] All fixtures are synthetic/mocked; no OpenAI calls; full pytest passes.

## concept-synthesis

- [ ] **Task 8 — Typed synthesis for concepts, relationships, and procedures**
  - Depends on: Checkpoint B. Verify: structure/justification tests; full pytest.
- [ ] **Task 9 — Evolution, negative-evidence, and conflict semantics**
  - Depends on: Task 8. Verify: coverage/absence/conflict tests; full pytest.

## invalidation-publication

- [ ] **Task 10 — Dependency DAG and selective stale propagation**
  - Depends on: Task 9. Verify: cycle/closure/staleness tests; full pytest.
- [ ] **Task 11 — Local candidate validation and atomic publication**
  - Depends on: Task 10. Verify: failed-candidate/pointer-swap tests; full pytest.

### Checkpoint C — Safe local publication

- [ ] Synthetic publication is safe; no remote candidate store exists; full
  pytest passes.

## derived-orientation-retrieval

- [ ] **Task 12 — Vector-store adapter and guarded capability preflight**
  - Depends on: Checkpoint C. Verify: fake adapter tests; disposable live
    preflight only with Theo's explicit approval.
- [ ] **Task 13 — Bounded published-snapshot orientation service**
  - Depends on: Task 12. Verify: stale/wrong/duplicate/budget/raw-dump tests;
    full pytest.

## mentor-knowledge-orchestration

- [ ] **Task 14 — Mentor integration, diagnostics, and replay safety**
  - Depends on: Task 13. Verify: citation/timestamp/compaction/streaming
    fixtures; full pytest.

### Checkpoint D — Mentor regression gate

- [ ] Phase 2 behavior remains green; broad fixtures orient and narrow fixtures
  do not force orientation; full pytest passes.

## knowledge-inspection

- [ ] **Task 15 — Read-only Knowledge Inspector API**
  - Depends on: Checkpoint D. Verify: loopback/read-only/safe-JSON API tests;
    full pytest.
- [ ] **Task 16 — Minimal static Assimilation Inspector**
  - Depends on: Task 15. Verify: full pytest and desktop/compact browser smoke.

## phase-3-evaluation

- [ ] **Task 17 — Deterministic regression suite and evaluation harness**
  - Depends on: Task 16. Verify: full pytest, diff/secret checks, browser smoke;
    no paid calls.
- [ ] **Task 18 — Six-source pilot manifest and measured-cost protocol**
  - Depends on: Task 17. Verify: manifest/evaluation tests; full pytest; no
    pilot call.

### Gate 1 — Paid six-source pilot (explicit Theo authorization)

- [ ] Confirm preconditions and run only the reviewed six-source pilot.
- [ ] Audit anchors, independent validation, synthesis/evolution/conflict,
  derived retrieval/orientation, and Phase 2 baseline comparison.
- [ ] Record measured cost forecast locally and STOP for Theo's pilot review.
- [ ] **Task 19 — Record Gate 1 pilot decision**
  - Depends on: Gate 1. Verify: Theo review; stage only non-private summary.

### Gate 2 — Full Jacob assimilation (separate Theo approval)

- [ ] **Task 20 — Full 150-source assimilation after separate approval**
  - Depends on: Task 19 and explicit Theo approval. Verify:
    coverage/anchor/validation/dependency audit; full pytest.

### Gate 3 — Mentor evaluation and human acceptance

- [ ] **Task 21 — Baseline-versus-assimilated Mentor evaluation**
  - Depends on: Task 20 published full snapshot. Verify: paid evaluation, raw
    citation inspection, and full pytest.
- [ ] **Task 22 — Theo's final Phase 3 human acceptance**
  - Depends on: Task 21. Verify: Theo's explicit pass/fail decision.
