# Phase 5 Implementation Plan: Backtest / Empirical Data Analysis Foundation

**Status:** Proposed — implementation requires Theo's separate approval.
**Specification:** [`docs/superpowers/specs/2026-08-27-trading-mentor-phase-5-design.md`](../docs/superpowers/specs/2026-08-27-trading-mentor-phase-5-design.md)

## Overview

Phase 5 adds local CSV/XLSX backtest analysis. pandas/openpyxl performs parsing
and calculations; SQLite stores only dataset/result metadata; raw uploads stay
ignored local files. Existing Responses flow receives only typed local function
results, so GPT-5.6 Sol interprets facts instead of calculating them. The plan
stops before Strategy Projects, scientific supervision, and external web research.

## Dependency graph

```text
Task 1 storage/dependencies
  -> Task 2 safe immutable import
  -> Task 3 schema + mapping
  -> Task 4 validated analysis frame
  -> Tasks 5 metrics and 6 groups/filters/comparisons
  -> Task 7 temporal/MFE/uncertainty
  -> Task 8 Mentor typed-tool integration
  -> Task 9 Data UI and thread scope
  -> Task 10 evaluation + Theo gate
```

Tasks 5 and 6 can be developed after Task 4, but are reviewed together before
Task 7. Work stays on `feature/phase-5-backtest-analysis`; pushes happen at
sensible completed milestones. No merge to main is implied.

## Decisions carried into implementation

- pandas plus openpyxl; no DuckDB or Polars in v1.
- A local immutable file, SHA-256, immutable import specification and SQLite
  metadata are the dataset identity; source row order is retained.
- Semantic mapping is suggested locally and becomes usable only as a confirmed
  immutable mapping snapshot.
- Pure typed functions own calculations. No model arithmetic, arbitrary Python/
  SQL, or raw spreadsheet context.
- `USER_EMPIRICAL_EVIDENCE` is explicit result provenance.
- Dataset selection is thread-local, visible, and supplied explicitly to tools.
- One bounded generic function dispatcher replaces the current profile-only
  continuation boundary; analysis evidence is owned by the originating thread/
  turn so permanent chat deletion remains complete.
- Dataset deletion is deliberately deferred; it needs an explicit historic
  evidence-redaction design rather than an unsafe partial deletion.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Spreadsheet types silently become plausible numbers | Preserve parse failures and per-analysis exclusion reasons; test malformed values. |
| Small group looks like an edge | Include N and distribution/interval data in every group/comparison. |
| Raw data leaks through model context or Git | Test payload bounds, preserve ignored paths, and inspect diff before commits. |
| Model invents a calculation/filter | Strict function schemas, server validation, result envelopes and fixture tests. |
| Historic data evidence survives a deleted chat | Thread-owned evidence/scope cleanup in the existing deletion transaction. |
| Scope becomes an unbounded statistics platform | Implement specified v1 operations only; defer p-values, bootstrap, projects and web research. |

## Task index

1. Data foundation and local storage
2. Immutable CSV/XLSX import flow
3. Dataset inspection and semantic mapping
4. Validated analysis-frame boundary
5. Core metrics and reproducible results
6. Typed filters, groups and comparisons
7. Temporal, MFE/MAE and uncertainty outputs
8. Mentor tool/provenance integration
9. Data workspace and thread-local scope
10. End-to-end evaluation and Theo human gate

## Checkpoints

- **After Tasks 1–3:** local import/mapping lifecycle works; no private data is
  committed or externally transmitted.
- **After Tasks 4–7:** deterministic engine passes known-value fixtures,
  reports exclusions/N, and offers no causal edge claim.
- **After Tasks 8–9:** existing chat safely invokes analysis and browser scope
  is visible with Phase 1–4 behavior intact.
- **Task 10:** full suite, privacy/diff review, independent review, then stop
  for Theo's explicit Phase 5 human quality gate.
