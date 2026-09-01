# Phase 5 Implementation Plan: Backtest / Empirical Data Analysis Foundation

**Status:** Amended 2026-08-30 for Theo review — implementation is stopped at
the Phase 5 architecture breaker.
**Specification:** [`docs/superpowers/specs/2026-08-27-trading-mentor-phase-5-design.md`](../docs/superpowers/specs/2026-08-27-trading-mentor-phase-5-design.md)

## Overview

Phase 5 adds local CSV/XLSX backtest analysis. pandas/openpyxl performs parsing
and calculations; SQLite stores only dataset/result metadata; raw uploads stay
ignored local files. Existing Responses flow receives only typed local function
results, so GPT-5.6 Sol interprets facts instead of calculating them. The
2026-08-30 amendment adds one explicit, bounded qualitative-text disclosure
path; it does not expose arbitrary rows or a spreadsheet to Sol. The plan stops
before Strategy Projects, scientific supervision, and external web research.

## Dependency graph

```text
Task 1 storage/dependencies
  -> Task 2 safe immutable import
  -> Task 3 schema + mapping
  -> Task 4 validated analysis frame
  -> Tasks 5 metrics and 6 groups/filters/comparisons
  -> Task 7 temporal/MFE/uncertainty
  -> Task 7A authoritative group-evidence partition
  -> Task 7B approved qualitative-text evidence boundary
  -> amended Checkpoint B deterministic/privacy review
  -> Task 8 Mentor typed-tool integration
  -> Task 9 Data UI and thread scope
  -> Task 10 evaluation + Theo gate
```

Tasks 5 and 6 can be developed after Task 4, but are reviewed together before
Task 7. Tasks 7A and 7B are sequential: one establishes self-validating
numeric evidence; the other establishes text privacy and completeness before
any model-facing tool exists. Work stays on `feature/phase-5-backtest-analysis`;
pushes happen at sensible completed milestones. No merge to main is implied.

## 2026-09-01 Chat-First UX amendment workstream

The accepted Task 9 backend remains intact. Its normal Data workspace is
replaced by an attachment-first surface; advanced correction stays reachable
from an attachment chip. Build order is deliberately vertical:

1. strict local auto-map/attachment API and ambiguity contract;
2. compact composer/chip/automatic thread scope and replacement confirmation;
3. just-in-time pre-turn notes consent plus advanced-settings relocation;
4. minimal header, dark/light/system theme, responsive/accessibility checks.

No raw upload becomes a chat attachment or model input. Safe auto-confirmation
is restricted to the controlled policy in the Phase 5 design amendment; all
other mappings remain local UI clarification or advanced settings.

**Implementation status:** Complete and pushed after deterministic, browser, and
independent review. Task 10 remains the explicit Theo human quality gate.

## Decisions carried into implementation

- pandas plus openpyxl; no DuckDB or Polars in v1.
- A local immutable file, SHA-256, immutable import specification and SQLite
  metadata are the dataset identity; source row order is retained.
- Semantic mapping is suggested locally and becomes usable only as a confirmed
  immutable mapping snapshot.
- Pure typed functions own calculations. No model arithmetic, arbitrary Python/
  SQL, or raw spreadsheet context.
- `USER_EMPIRICAL_EVIDENCE` is explicit result provenance.
- One `GroupEvidencePartition` is the authoritative grouped population; all
  returned/omitted/ungrouped counts are derived from and validated against it.
- Text values are local-only by default. A confirmed mapping version grants
  per-field `Mentor access`; a default-false, server-owned one-turn consent
  signal is also required before a bounded `read_text_evidence` call may
  disclose approved fields and context.
- Qualitative text is user-supplied data; Sol's themes are explicitly labelled
  AI qualitative interpretation, never deterministic empirical evidence.
- The local engine only filters/orders/bounds/sanitizes text: no NLP,
  embeddings, text index, topic model, sentiment, classifier, or theme count.
- Raw qualitative payloads are ephemeral: replay retains safe disclosure
  metadata and the terminal answer, not text excerpts.
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
| Group envelope replays contradictory totals | Build one normalized partition; reject it at production, persistence, replay, and tool boundaries. |
| Approved notes accidentally disclose other spreadsheet data | Immutable per-field mapping permission, opaque IDs, server scope validation, and bounded text/context outputs. |
| A model tool call is mistaken for user consent | Require a default-false server-owned consent signal from the current compose action. |
| Sol overstates a thematic reading as a measured fact | Provenance-specific instructions and fixtures for partial/materially limited qualitative evidence. |
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
7A. Normalize authoritative group-evidence partition
7B. Add qualitative text evidence/disclosure boundary
8. Mentor tool/provenance integration, including bounded text evidence
9. Data workspace, thread-local scope, and visible disclosure UX
10. End-to-end numeric-plus-qualitative evaluation and Theo human gate

## Checkpoints

- **After Tasks 1–3:** local import/mapping lifecycle works; no private data is
  committed or externally transmitted.
- **Amended Checkpoint B (after Tasks 4–7B):** deterministic engine passes
  known-value fixtures; grouped evidence is a self-validating partition; text
  remains denied by default and has bounded, complete-or-explicitly-partial
  local evidence semantics. No model integration has begun.
- **After Tasks 8–9:** existing chat safely invokes analysis and browser scope
  and per-field text disclosure are visible with Phase 1–4 behavior intact.
- **Task 10:** full suite, privacy/diff review, independent review, then stop
  for Theo's explicit Phase 5 human quality gate.

## 2026-08-30 architecture-breaker preservation

Tasks 1–3 and their accepted checkpoint remain complete. The existing local
Tasks 4–7/checkpoint commits remain preserved and are not to be reset or
rewritten by this amendment. They are provisional until the amended Checkpoint
B validates the normalized partition; no current local implementation commit
is pushed as part of this design-only work.
