# Phase 5 Task List — Backtest / Empirical Data Analysis Foundation

**Status:** Phase 5 passed Theo's human quality gate and is the accepted Phase 6
baseline at `70e49ffdc7366047672b04e848424b62251d67bb`. Phase 6 is authorized
on 2026-09-05 under the approved design and plan appended below.
Phase 4 passed and is recorded in [`docs/phase-4-acceptance.md`](../docs/phase-4-acceptance.md).

## dataset-foundation

### Task 1: Add local data foundation

**Description:** Add minimal pinned pandas/openpyxl dependencies, ignored data
directory, SQLite metadata migration, immutable import/mapping-version models,
and thread-owned empirical-evidence models.

**Acceptance criteria:**
- [x] Dataset identity includes immutable hash, original name, row count, import
  spec, status, and mapping/result version references.
- [x] Mapping has atomic draft/confirmed parent snapshots with unique role-to-column
  entries; an analysis can use only a confirmed immutable version.
- [x] Thread deletion transaction removes its dataset scope/evidence/tool-output
  references without deleting a shared dataset or another thread's evidence.
- [x] Runtime artifacts are ignored and raw rows are not stored in Git or SQLite metadata.
- [x] Existing storage/conversation migrations remain compatible.

**Verification:**
- [x] Focused storage/migration tests and full pytest pass.
- [x] Dependency versions are checked against current official docs.

**Dependencies:** None
**Files likely touched:** `pyproject.toml`, `.gitignore`, `src/mentor/storage.py`,
`src/mentor/datasets.py`, `tests/test_datasets.py`
**Estimated scope:** Medium

### Task 2: Implement safe immutable CSV/XLSX import

**Description:** Build local signature/archive preflight, atomic copy/hash,
selected-sheet CSV/XLSX parse/import-spec recording, and rollback behavior.

**Acceptance criteria:**
- [x] Valid CSV/XLSX imports create exactly one immutable local dataset.
- [x] Invalid, oversize, macro/formula/external-link, archive-bomb, or unsupported
  files leave no usable partial dataset and clean temporary files.
- [x] Original bytes remain local; formula execution, cached-formula trust, and
  external transfer never occur.

**Verification:**
- [x] Known CSV/XLSX, CSV dialect/decode/parse, XLSX signature/archive,
  formula/macro, and rollback fixtures pass.
- [x] Hash/new-upload/duplicate-row behavior is deterministic.

**Dependencies:** Task 1
**Files likely touched:** `src/mentor/datasets.py`, `src/mentor/storage.py`,
`tests/test_datasets.py`, `.gitignore`
**Estimated scope:** Medium

### Task 3: Add inspection and user-confirmed semantic mapping

**Description:** Expose bounded local schema/preview, deterministic alias
suggestions, and complete user-confirmed mapping snapshots for Phase 5 roles/units.

**Acceptance criteria:**
- [x] Any columns can be inspected before a mapping exists.
- [x] Suggestions never become active semantics until confirmed by Theo.
- [x] Mapping version preserves role/unit/type health, parser/import policy, and
  clear unavailable-capability reasons.
- [x] Any generic column exposed to the Mentor has a Theo-approved analysis-safe
  label and opaque field ID; raw headers remain local by default.
- [x] Aggregate group labels are model-disclosed only through the explicit
  categorical/boolean mapping confirmation, with cardinality and length limits.

**Verification:**
- [x] Fixtures cover aliases, blank/invalid cells, mapping draft/confirm/edit/clear/versioning,
  ambiguous dates, and unique-role constraints.
- [x] No model call is needed to suggest or confirm mapping.

**Dependencies:** Task 2
**Files likely touched:** `src/mentor/datasets.py`, `src/mentor/storage.py`,
`src/mentor/server.py`, `tests/test_datasets.py`, `tests/test_server.py`
**Estimated scope:** Medium

### Checkpoint A: Local import and mapping

- [x] Tasks 1–3 focused tests and full pytest are green.
- [x] Raw uploads are ignored, private and never sent to OpenAI.
- [x] A manual local import can preview a file and confirm a mapping.

## deterministic-analysis

### Task 4: Establish the validated analysis-frame boundary

**Description:** Convert a confirmed mapping/import specification plus original
local file into a pure validated analysis frame with typed filters, stable row
ordinal, and exclusion accounting.

**Acceptance criteria:**
- [x] Every operation has explicit required roles and reports source, filtered,
  valid and excluded N with reasons while preserving the confirmed return unit.
- [x] Streak/equity order defaults to recorded source row ordinal; explicit time
  ordering is validated, recorded, and never guessed.
- [x] Typed filters reject unknown columns, incompatible values, and unsupported operators.
- [x] No malformed value becomes a plausible trade result.

**Verification:**
- [x] Pure-function tests cover type failures, filters, and no-data cases.
- [x] Result fixtures contain no raw dataset dump.

**Dependencies:** Task 3
**Files likely touched:** `src/mentor/analysis.py`, `src/mentor/datasets.py`,
`tests/test_analysis.py`, `tests/test_datasets.py`
**Estimated scope:** Medium

### Task 5: Implement core metrics and reproducible result envelopes

**Description:** Add outcome, R, streak, equity/drawdown and distribution
metrics with a versioned `USER_EMPIRICAL_EVIDENCE` envelope.

**Acceptance criteria:**
- [x] Known fixtures prove N, explicit outcome denominator, native-unit return
  metrics, row-ordered streaks/drawdown/recovery, quantiles, and unavailable metrics.
- [x] Every result identifies dataset hash, mapping version, operation, filters,
  metric definitions, exclusions, and limitations.
- [x] No metric changes P&L units or invents missing outcome/MFE/MAE data;
  R-specific outputs are unavailable unless the confirmed return unit is R.

**Verification:**
- [x] Exact expected-value tests and full pytest pass.

**Dependencies:** Task 4
**Files likely touched:** `src/mentor/analysis.py`, `src/mentor/storage.py`,
`tests/test_analysis.py`, `tests/test_storage.py`
**Estimated scope:** Medium

### Task 6: Implement groups and effect comparisons

**Description:** Add one/two-column grouping and validated A-versus-B comparison
on top of core metrics.

**Acceptance criteria:**
- [x] Each group displays N, valid N, exclusions, and bounded standardized metrics.
- [x] Comparison returns both sides and defined deltas, not a causal conclusion.
- [x] At most two group columns and 50 returned groups are enforced with omission metadata.

**Verification:**
- [x] Fixtures cover session, boolean condition, empty/small groups, filters,
  same-value comparisons, and context truncation.
- [x] Full pytest passes.

**Dependencies:** Task 5
**Files likely touched:** `src/mentor/analysis.py`, `tests/test_analysis.py`,
`src/mentor/datasets.py`
**Estimated scope:** Small

### Task 7: Add temporal, MFE/MAE, and uncertainty summaries

**Description:** Add date-gated temporal buckets/halves/rolling windows,
MFE/MAE distribution analysis, and disciplined uncertainty information.

**Acceptance criteria:**
- [x] Temporal operations require a valid mapped time field, compatible timezone
  state, and preserve bucket N.
- [x] MFE/MAE is unavailable without mapped fields and never invents units.
- [x] Win-rate intervals and R spread are descriptive; no p-value or causal edge detector appears.

**Verification:**
- [x] Temporal/MFE/MAE/interval fixtures and full pytest pass.

**Dependencies:** Tasks 5–6
**Files likely touched:** `src/mentor/analysis.py`, `tests/test_analysis.py`,
`src/mentor/datasets.py`
**Estimated scope:** Small

### Task 7A: Normalize authoritative group-evidence partition

**Description:** Replace the currently provisional grouped-evidence shape with
one self-validating `GroupEvidencePartition`; preserve healthy Task 4–7
calculation behavior without resetting or rewriting it unnecessarily.

**Acceptance criteria:**
- [x] A filtered population partitions only into returned groups, one omitted
  aggregate, and ungrouped rows; each returned group partitions into valid and
  excluded analysis rows.
- [x] Zero-valid real groups remain visible; omitted and ungrouped populations
  remain distinct; no duplicate writable totals can contradict the partition.
- [x] The partition rejects contradictions on production, persistence, replay,
  and pre-tool validation.

**Verification:**
- [x] Known-value fixtures prove all three reconciliation equalities; individual
  omitted/ungrouped `filtered = valid + excluded`; exact-once production
  source-row allocation; unique returned keys; no overlap/synthetic rows;
  excluded-only groups; omission limits; and replay structural rejection for
  each invalid partition field.
- [x] Existing Task 4–7 fixtures and full pytest remain green.

**Dependencies:** Tasks 4–7
**Files likely touched:** `src/mentor/analysis.py`, `src/mentor/storage.py`,
`tests/test_analysis.py`, `tests/test_storage.py`
**Estimated scope:** Medium

### Task 7B: Add explicit qualitative-text evidence boundary

**Description:** Extend immutable mapping snapshots with per-field Mentor access
policy and add local-only bounded text-evidence retrieval semantics. This task
introduces no Responses call or final mapping/compose UI; Task 9 exposes the
approved controls.

**Acceptance criteria:**
- [x] Text is denied by default; only a confirmed mapping version with an
  explicitly approved field can expose bounded text or structured row context.
- [x] A server-validated, default-false per-turn consent signal is required in
  addition to mapping permission; the model cannot set or reuse that signal.
- [x] The local text envelope applies canonical filters, deterministic order,
  row/cell/character bounds, sanitization, and complete-or-explicitly-partial
  metadata without raw header/path/file disclosure.
- [x] Raw text is absent from persisted evidence, diagnostics, and replay;
  only safe disclosure metadata is retained.

**Verification:**
- [x] Privacy-safe 50/100/200-note and long-journal fixtures prove default
  denial, permission/revocation versioning, model-independent consent, bounds,
  truncation, ordering, incomplete metadata, and context-field permission.
- [x] No OpenAI/network call is needed; full pytest remains green.

**Dependencies:** Task 7A, Task 3
**Files likely touched:** `src/mentor/datasets.py`, `src/mentor/analysis.py`,
`src/mentor/storage.py`, `tests/test_datasets.py`, `tests/test_analysis.py`,
`tests/test_storage.py`
**Estimated scope:** Medium

### Amended Checkpoint B: Deterministic and qualitative evidence boundary

- [x] Tasks 4–7B and full pytest are green.
- [x] Numeric evidence preserves N, exclusions, reproducibility, and
  `USER_EMPIRICAL_EVIDENCE`; grouped evidence is one validated partition.
- [x] Text disclosure is explicit, local-default-denied, bounded, and
  complete-or-explicitly-partial; raw text never enters replay/persistence.
- [x] Independent deterministic/privacy review finds no P0/P1 evidence-boundary
  issue before Task 8 begins.

## empirical-mentor and data-workspace

### Task 8: Integrate typed analysis tools with the Mentor

**Description:** Replace the narrow profile-only dispatcher with a bounded
generic local-function dispatcher, then add strict analysis functions,
numeric/qualitative provenance instructions, and replay-safe evidence storage.

**Acceptance criteria:**
- [x] Sol can request only approved inspect/summarize/group/compare/MFE-MAE/time
  operations plus `read_text_evidence` through an analysis batch capped at
  three calls (at most two aggregate and one text call) and one terminal continuation.
- [x] Existing profile mutation idempotence remains one call/continuation;
  profile-plus-analysis mixed batches are safely rejected, while File Search and
  citation repair retain their existing behavior.
- [x] Arguments are locally validated against the active thread dataset.
- [x] Sol receives bounded results, not raw spreadsheets, and distinguishes
  deterministic user empirical evidence, disclosed user qualitative data, AI
  qualitative interpretation, Jacob teaching, hypotheses, and user decisions.

**Verification:**
- [x] Chat fixtures prove multi-call dispatch/continuation, invalid/mixed-call
  rejections, permission/scope failure for text requests, numeric/text budgets,
  replay evidence linkage without raw text, partial-text wording, no fabricated
  arithmetic, provenance separation, bounded sanitized payloads, and existing
  citation behavior.
- [x] No paid call is needed for deterministic tests.

**Dependencies:** Amended Checkpoint B
**Files likely touched:** `src/mentor/chat_service.py`, `src/mentor/analysis.py`,
`src/mentor/storage.py`, `tests/test_chat_service.py`, `tests/test_analysis.py`
**Estimated scope:** Medium

### Task 9: Build the minimal Data workspace and visible thread scope

**Description:** Add static loopback UI/API paths for local upload, inspection,
mapping confirmation including per-field Mentor access, dataset list, and
select/change/clear active dataset in chat; add the one-turn **Include approved
notes in this answer** compose control.

**Acceptance criteria:**
- [x] Upload -> preview -> mapping -> select is usable without a spreadsheet editor.
- [x] Active dataset is visible, persists per conversation, is never silently
  inherited by a new thread, and historic evidence retains its original settings.
- [x] Text fields and optional structured context fields are visibly denied by
  default; Theo can approve/revoke future row disclosure through a new mapping
  version. The loopback mapping UI may show Theo raw headers to select a column,
  but headers never reach Sol, replay/evidence payloads, logs, or non-loopback
  surfaces; the rest of the workbook remains undisclosed.
- [x] The compose control is clear, defaults off for every message, and shows
  that it only permits already approved fields for that one response.
- [x] Accessible controls, errors, and existing chat/profile UI remain intact.

**Verification:**
- [x] Server/API and browser smoke cover import/mapping/access choice/one-turn
  consent/scope/clear/reload and user-visible text-disclosure state.
- [x] Console is clean; upload bytes travel only in the loopback upload request,
  never to OpenAI, File Search/vector stores, logs, Git, or non-loopback endpoints.

**Dependencies:** Tasks 3 and 8
**Files likely touched:** `src/mentor/server.py`, `src/mentor/static/app.js`,
`src/mentor/static/style.css`, `tests/test_server.py`, `tests/test_browser_smoke.py`
**Estimated scope:** Medium

### Checkpoint C: End-to-end local data flow

- [x] Tasks 8–9 focused suites and full pytest are green.
- [x] Browser flow shows visible thread-local scope, explicit text-disclosure
  state, and no unapproved raw-data network path.
- [x] Existing Phase 1–4 chat/profile/source behavior remains green.

### Task 9UX: Chat-first attachment redesign

**Description:** Replace normal Data administration with a local attachment
flow while preserving the accepted Phase 5 engine and privacy contracts.

**Acceptance criteria:**
- [x] A compact composer attachment control imports and automatically scopes a
  strictly safe auto-mapped CSV/XLSX to the current thread; a new thread has no
  inherited attachment.
- [x] Ambiguous fields receive a compact local clarification, and a second
  attachment asks to replace the current one; no raw header is a chat/model
  message.
- [x] Normal UI has no Data mode, mapping grid, permanent note checkbox, or
  permanent no-data banner; advanced mapping remains reachable from the chip.
- [x] Qualitative access remains fresh-turn, server-enforced, and is requested
  only just in time; numeric analysis does not request it.
- [x] Theo's Trading Mentor is the normal identity with a dark-by-default,
  keyboard-accessible, responsive theme and compact settings menu.

**Verification:**
- [x] Focused server/dataset/UI tests cover auto-map, scope, ambiguity,
  replacement, fresh consent and advanced-settings reachability.
- [x] Browser desktop/mobile/keyboard/console checks prove a normal chat-first
  interaction, dark default, and persisted appearance choice.
- [x] Full pytest and independent design/code/UX review pass.

## phase5-evaluation

### Task 10: Run Phase 5 acceptance evaluation and stop for Theo

**Description:** Assemble privacy-safe numeric-and-notes fixtures for the
approved examples, run deterministic/browser/full checks, obtain independent
review, and present the human quality gate without beginning Phase 6.

**Acceptance criteria:**
- [x] All design acceptance examples, including a combined numeric-plus-approved
  notes analysis, are covered by fixtures and the local UI.
- [x] Privacy, Git diff, bounded-context/sanitizer, source/provenance, deletion/
  historic-evidence, text completeness/replay, and Phase 1–4 regression checks
  are recorded.
- [x] Theo receives a clear pass/fail gate; no Projects, web research, or
  scientific supervisor begins.

**Verification:**
- [x] Focused tests, full pytest, browser smoke, and independent review pass.
- [x] Clean worktree and pushed feature branch are verified before handoff.

**Dependencies:** Checkpoint C
**Files likely touched:** `tests/test_analysis.py`, `tests/test_chat_service.py`,
`tests/test_browser_smoke.py`, `docs/phase-5-evaluation.md`, `tasks/todo.md`
**Estimated scope:** Medium

### Final checkpoint: Await Theo's Phase 5 decision

- [x] Complete deterministic suite, browser smoke, privacy/diff review, and
  independent review are green.
- [x] Feature commits are pushed; runtime data stays ignored.
- [x] Theo has made the Phase 5 human acceptance decision.
- [x] Phase 5 stopped at its gate; Phase 6 began only after separate design,
  plan, and implementation authorization.

# Phase 6 Task List — Strategy Projects, Multi-Mentor Knowledge & Coaching

**Plan:** [`docs/superpowers/plans/2026-09-03-phase-6-strategy-projects-implementation-plan.md`](../docs/superpowers/plans/2026-09-03-phase-6-strategy-projects-implementation-plan.md)

## Phase 6A foundation

- [x] Task 1 — Phase 6 contracts, migration fixture, and synthetic-spike contract.
- [x] Task 2 — Neutral General Mentor and project-local conversation scope.
- [x] Task 3 — Corpus-scoped mentor libraries and legacy Jacob compatibility.
- [x] Task 4 — Browser folder staging, confirmation, immutable import, and remote adapter.
- [x] Checkpoint A — Source/project isolation, migration parity, privacy, full suite, review.

## Phase 6A Mentor behavior and Phase 6B state

- [x] Task 5 — Saved/temporary source scope, six-library budgets, native citation gate.
- [ ] Task 6 — Project-aware attribution, citations, disagreement, and replay.
- [ ] Task 7 — Persistent coaching state and constrained project tools.
- [ ] Task 8 — Research ledger and safe Phase 5 empirical evidence links.
- [ ] Task 9 — Explicit promotion gate and immutable playbook lineage.
- [ ] Checkpoint B — Project/source/privacy/replay integrity, full suite, review.

## Phase 6 UI and final proof

- [ ] Task 10 — Chat-first project navigation, source controls, and browser folder import.
- [ ] Task 11 — Compact Roadmap, research history, and playbook inspection.
- [ ] Task 12 — Synthetic contract/behavioral proof, regressions, and human-gate package.
- [ ] Checkpoint C — Full deterministic/live/browser/privacy review and clean pushed branch.
- [ ] Theo human quality gate — stop before Phase 7.
