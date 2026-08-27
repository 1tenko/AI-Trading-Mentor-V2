# Phase 5 Task List — Backtest / Empirical Data Analysis Foundation

**Status:** Planning complete; do not implement until Theo separately approves.
Phase 4 passed and is recorded in [`docs/phase-4-acceptance.md`](../docs/phase-4-acceptance.md).

## dataset-foundation

### Task 1: Add local data foundation

**Description:** Add minimal pinned pandas/openpyxl dependencies, ignored data
directory, SQLite metadata migration, immutable import/mapping-version models,
and thread-owned empirical-evidence models.

**Acceptance criteria:**
- [ ] Dataset identity includes immutable hash, original name, row count, import
  spec, status, and mapping/result version references.
- [ ] Mapping has atomic draft/confirmed parent snapshots with unique role-to-column
  entries; an analysis can use only a confirmed immutable version.
- [ ] Thread deletion transaction removes its dataset scope/evidence/tool-output
  references without deleting a shared dataset or another thread's evidence.
- [ ] Runtime artifacts are ignored and raw rows are not stored in Git or SQLite metadata.
- [ ] Existing storage/conversation migrations remain compatible.

**Verification:**
- [ ] Focused storage/migration tests and full pytest pass.
- [ ] Dependency versions are checked against current official docs.

**Dependencies:** None
**Files likely touched:** `pyproject.toml`, `.gitignore`, `src/mentor/storage.py`,
`src/mentor/datasets.py`, `tests/test_datasets.py`
**Estimated scope:** Medium

### Task 2: Implement safe immutable CSV/XLSX import

**Description:** Build local signature/archive preflight, atomic copy/hash,
selected-sheet CSV/XLSX parse/import-spec recording, and rollback behavior.

**Acceptance criteria:**
- [ ] Valid CSV/XLSX imports create exactly one immutable local dataset.
- [ ] Invalid, oversize, macro/formula/external-link, archive-bomb, or unsupported
  files leave no usable partial dataset and clean temporary files.
- [ ] Original bytes remain local; formula execution, cached-formula trust, and
  external transfer never occur.

**Verification:**
- [ ] Known CSV/XLSX, CSV dialect/decode/parse, XLSX signature/archive,
  formula/macro, and rollback fixtures pass.
- [ ] Hash/new-upload/duplicate-row behavior is deterministic.

**Dependencies:** Task 1
**Files likely touched:** `src/mentor/datasets.py`, `src/mentor/storage.py`,
`tests/test_datasets.py`, `.gitignore`
**Estimated scope:** Medium

### Task 3: Add inspection and user-confirmed semantic mapping

**Description:** Expose bounded local schema/preview, deterministic alias
suggestions, and complete user-confirmed mapping snapshots for Phase 5 roles/units.

**Acceptance criteria:**
- [ ] Any columns can be inspected before a mapping exists.
- [ ] Suggestions never become active semantics until confirmed by Theo.
- [ ] Mapping version preserves role/unit/type health, parser/import policy, and
  clear unavailable-capability reasons.
- [ ] Any generic column exposed to the Mentor has a Theo-approved analysis-safe
  label and opaque field ID; raw headers remain local by default.
- [ ] Aggregate group labels are model-disclosed only through the explicit
  categorical/boolean mapping confirmation, with cardinality and length limits.

**Verification:**
- [ ] Fixtures cover aliases, blank/invalid cells, mapping draft/confirm/edit/clear/versioning,
  ambiguous dates, and unique-role constraints.
- [ ] No model call is needed to suggest or confirm mapping.

**Dependencies:** Task 2
**Files likely touched:** `src/mentor/datasets.py`, `src/mentor/storage.py`,
`src/mentor/server.py`, `tests/test_datasets.py`, `tests/test_server.py`
**Estimated scope:** Medium

### Checkpoint A: Local import and mapping

- [ ] Tasks 1–3 focused tests and full pytest are green.
- [ ] Raw uploads are ignored, private and never sent to OpenAI.
- [ ] A manual local import can preview a file and confirm a mapping.

## deterministic-analysis

### Task 4: Establish the validated analysis-frame boundary

**Description:** Convert a confirmed mapping/import specification plus original
local file into a pure validated analysis frame with typed filters, stable row
ordinal, and exclusion accounting.

**Acceptance criteria:**
- [ ] Every operation has explicit required roles and reports source, filtered,
  valid and excluded N with reasons while preserving the confirmed return unit.
- [ ] Streak/equity order defaults to recorded source row ordinal; explicit time
  ordering is validated, recorded, and never guessed.
- [ ] Typed filters reject unknown columns, incompatible values, and unsupported operators.
- [ ] No malformed value becomes a plausible trade result.

**Verification:**
- [ ] Pure-function tests cover type failures, filters, and no-data cases.
- [ ] Result fixtures contain no raw dataset dump.

**Dependencies:** Task 3
**Files likely touched:** `src/mentor/analysis.py`, `src/mentor/datasets.py`,
`tests/test_analysis.py`, `tests/test_datasets.py`
**Estimated scope:** Medium

### Task 5: Implement core metrics and reproducible result envelopes

**Description:** Add outcome, R, streak, equity/drawdown and distribution
metrics with a versioned `USER_EMPIRICAL_EVIDENCE` envelope.

**Acceptance criteria:**
- [ ] Known fixtures prove N, explicit outcome denominator, native-unit return
  metrics, row-ordered streaks/drawdown/recovery, quantiles, and unavailable metrics.
- [ ] Every result identifies dataset hash, mapping version, operation, filters,
  metric definitions, exclusions, and limitations.
- [ ] No metric changes P&L units or invents missing outcome/MFE/MAE data;
  R-specific outputs are unavailable unless the confirmed return unit is R.

**Verification:**
- [ ] Exact expected-value tests and full pytest pass.

**Dependencies:** Task 4
**Files likely touched:** `src/mentor/analysis.py`, `src/mentor/storage.py`,
`tests/test_analysis.py`, `tests/test_storage.py`
**Estimated scope:** Medium

### Task 6: Implement groups and effect comparisons

**Description:** Add one/two-column grouping and validated A-versus-B comparison
on top of core metrics.

**Acceptance criteria:**
- [ ] Each group displays N, valid N, exclusions, and bounded standardized metrics.
- [ ] Comparison returns both sides and defined deltas, not a causal conclusion.
- [ ] At most two group columns and 50 returned groups are enforced with omission metadata.

**Verification:**
- [ ] Fixtures cover session, boolean condition, empty/small groups, filters,
  same-value comparisons, and context truncation.
- [ ] Full pytest passes.

**Dependencies:** Task 5
**Files likely touched:** `src/mentor/analysis.py`, `tests/test_analysis.py`,
`src/mentor/datasets.py`
**Estimated scope:** Small

### Task 7: Add temporal, MFE/MAE, and uncertainty summaries

**Description:** Add date-gated temporal buckets/halves/rolling windows,
MFE/MAE distribution analysis, and disciplined uncertainty information.

**Acceptance criteria:**
- [ ] Temporal operations require a valid mapped time field, compatible timezone
  state, and preserve bucket N.
- [ ] MFE/MAE is unavailable without mapped fields and never invents units.
- [ ] Win-rate intervals and R spread are descriptive; no p-value or causal edge detector appears.

**Verification:**
- [ ] Temporal/MFE/MAE/interval fixtures and full pytest pass.

**Dependencies:** Tasks 5–6
**Files likely touched:** `src/mentor/analysis.py`, `tests/test_analysis.py`,
`src/mentor/datasets.py`
**Estimated scope:** Small

### Checkpoint B: Deterministic evidence engine

- [ ] Tasks 4–7 and full pytest are green.
- [ ] Results preserve N, exclusions, reproducibility, and `USER_EMPIRICAL_EVIDENCE`.
- [ ] Independent calculation review finds no arithmetic or type-boundary issue.

## empirical-mentor and data-workspace

### Task 8: Integrate typed analysis tools with the Mentor

**Description:** Replace the narrow profile-only dispatcher with a bounded
generic local-function dispatcher, then add strict analysis functions,
empirical provenance instructions, and replay-safe evidence storage.

**Acceptance criteria:**
- [ ] Sol can request only approved inspect/summarize/group/compare/MFE-MAE/time
  operations through an analysis batch capped at three calls and one terminal continuation.
- [ ] Existing profile mutation idempotence remains one call/continuation;
  profile-plus-analysis mixed batches are safely rejected, while File Search and
  citation repair retain their existing behavior.
- [ ] Arguments are locally validated against the active thread dataset.
- [ ] Sol receives bounded results, not raw spreadsheets, and distinguishes local
  evidence, Jacob teaching, hypotheses, and user decisions.

**Verification:**
- [ ] Chat fixtures prove multi-call dispatch/continuation, invalid/mixed-call
  rejections, replay evidence linkage, shared 8,000-character batch/replay
  budget, no fabricated arithmetic, provenance separation, bounded sanitized
  payloads, and existing citation behavior.
- [ ] No paid call is needed for deterministic tests.

**Dependencies:** Tasks 4–7
**Files likely touched:** `src/mentor/chat_service.py`, `src/mentor/analysis.py`,
`src/mentor/storage.py`, `tests/test_chat_service.py`, `tests/test_analysis.py`
**Estimated scope:** Medium

### Task 9: Build the minimal Data workspace and visible thread scope

**Description:** Add static loopback UI/API paths for local upload, inspection,
mapping confirmation, dataset list, and select/change/clear active dataset in chat.

**Acceptance criteria:**
- [ ] Upload -> preview -> mapping -> select is usable without a spreadsheet editor.
- [ ] Active dataset is visible, persists per conversation, is never silently
  inherited by a new thread, and historic evidence retains its original settings.
- [ ] Accessible controls, errors, and existing chat/profile UI remain intact.

**Verification:**
- [ ] Server/API and browser smoke cover import/mapping/scope/clear/reload.
- [ ] Console is clean; upload bytes travel only in the loopback upload request,
  never to OpenAI, File Search/vector stores, logs, Git, or non-loopback endpoints.

**Dependencies:** Tasks 3 and 8
**Files likely touched:** `src/mentor/server.py`, `src/mentor/static/app.js`,
`src/mentor/static/style.css`, `tests/test_server.py`, `tests/test_browser_smoke.py`
**Estimated scope:** Medium

### Checkpoint C: End-to-end local data flow

- [ ] Tasks 8–9 focused suites and full pytest are green.
- [ ] Browser flow shows visible thread-local scope and no raw-data network path.
- [ ] Existing Phase 1–4 chat/profile/source behavior remains green.

## phase5-evaluation

### Task 10: Run Phase 5 acceptance evaluation and stop for Theo

**Description:** Assemble privacy-safe fixtures for the approved examples, run
deterministic/browser/full checks, obtain independent review, and present the
human quality gate without beginning Phase 6.

**Acceptance criteria:**
- [ ] All design acceptance examples are covered by fixtures and the local UI.
- [ ] Privacy, Git diff, bounded-context/sanitizer, source/provenance, deletion/
  historic-evidence, and Phase 1–4 regression checks are recorded.
- [ ] Theo receives a clear pass/fail gate; no Projects, web research, or
  scientific supervisor begins.

**Verification:**
- [ ] Focused tests, full pytest, browser smoke, and independent review pass.
- [ ] Clean worktree and pushed feature branch are verified before handoff.

**Dependencies:** Checkpoint C
**Files likely touched:** `tests/test_analysis.py`, `tests/test_chat_service.py`,
`tests/test_browser_smoke.py`, `docs/phase-5-evaluation.md`, `tasks/todo.md`
**Estimated scope:** Medium

### Final checkpoint: Await Theo's Phase 5 decision

- [ ] Complete deterministic suite, browser smoke, privacy/diff review, and
  independent review are green.
- [ ] Feature commits are pushed; runtime data stays ignored.
- [ ] Theo has made the Phase 5 human acceptance decision.
- [ ] Stop: do not start Phase 6, Phase 7, web research, or Strategy Projects.
