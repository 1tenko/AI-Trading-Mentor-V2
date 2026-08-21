# Implementation Plan: Phase 3 Knowledge Foundation + Jacob Assimilation

**Status:** Proposed. Theo must approve this plan before implementation begins.

## Scope and boundary

This plan implements only the approved Phase 3 design at [2026-08-21-trading-mentor-phase-3-design.md](../docs/superpowers/specs/2026-08-21-trading-mentor-phase-3-design.md) on feature/phase-3-knowledge-assimilation.

Phase 3 builds the generic Knowledge Library and assimilates Jacob 2025-2026 as its first corpus. It does not add another source, profile/memory, strategy feature, model routing, or Phase 4 work.

Routine pytest is local and mocked: it must not contact OpenAI. No task may run a real Jacob compiler or create a real candidate store until the explicit live gates. Corpus files, SQLite runtime data, pilot outputs, API keys, and private evaluation records remain untracked.

## Existing seam and implementation constraints

The application is a small Python 3.12+ / stdlib SQLite / openai>=2,<3 system with a loopback server, static UI, a single current Jacob source registry, native raw File Search, citation repair, and opaque context-compaction replay. Phase 3 extends these seams with dataclasses, SQLite tables, and focused modules. It does not add an ORM, graph database, schema framework, custom embeddings/ranking, or frontend framework.

OpenAI Vector Store Search supports queries, file-attribute filters, one to fifty results, and file identity/chunks/attributes/score. [OpenAI Vector Store Search](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/search)

Attachment/detachment are separate; detaching a vector-store file does not delete the File. The guarded preflight must prove current SDK behavior for same-File candidate attachment, filters, batches/status, search, and detachment. If it conflicts with the approved architecture, execution stops and reports it. [Create vector-store file](https://developers.openai.com/api/reference/python/resources/vector_stores/subresources/files/methods/create) [Delete vector-store file](https://developers.openai.com/api/reference/python/resources/vector_stores/subresources/files/methods/delete)

Responses supports built-in File Search and application-defined functions. The Mentor remains a direct Responses call so native raw citations survive. [Responses create](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)

## Interface contract

| Interface | Producer / consumer | Required contract |
|---|---|---|
| Collection, Source, SourceRevision | library, importer, anchors | Stable source identity; immutable SHA-256 revision; filename is metadata. |
| CorpusSnapshot, CompilationRun | lifecycle, publication, diagnostics | Candidate/published state, revision fingerprint, remote IDs, versions, metrics, status. |
| SourceAnchor | extraction, validation, Inspector | IDs, full hash, normalized range, timestamp when present, span fingerprint, locator version. |
| DerivedRecord | compiler, storage, retrieval | Typed family, derived kind, evidence/validation/lifecycle state, anchors, dependencies, typed payload, concise qualification. |
| Claim / Relationship / ProcedureSequenceHierarchy / Evolution / ConflictUnresolved | synthesis, Inspector | Small typed family payloads and typed facets; no arbitrary essay blob. |
| OrientationService | Responses function, Mentor | Current published derived store only; local validation, dedupe, hard record/token budget, concise typed output. |
| KnowledgeContext | Mentor, diagnostics, Inspector | Orientation use, snapshot/record IDs/count, budget result; no raw dump or hidden reasoning. |

Source-extracted remains derived. Direct source teaching still requires active raw verification and native raw citation. Persistent records store only conclusion, concise auditable justification, anchor basis, qualification, and outcome: never hidden reasoning, scratchpads, encrypted-reasoning prose, or arbitrary numeric confidence.

Normal requests resolve one published raw/derived pair at start. Candidate, archived, and stale IDs are rejected. Publication swaps the snapshot and both remote store IDs in one SQLite transaction. Broad/comparative/evolutionary/multi-concept/relationship-heavy/exhaustive questions normally orient before broad raw search; narrow and exact-source questions may go raw-first. Raw evidence always overrides orientation.

## Dependency graph

    knowledge-library (1-2)
      -> source-anchors (3)
      -> compilation-lifecycle (4)
      -> source-extraction (5-7)
      -> concept-synthesis (8-9)
      -> invalidation-publication (10-11)
      -> derived-orientation-retrieval (12-13)
      -> mentor-knowledge-orchestration (14)
      -> knowledge-inspection (15-16)
      -> phase-3-evaluation (17-22)

Tasks are sequential through shared storage contracts. The Inspector follows the stable read API. Paid work sits outside routine autonomous tasks.

## Task list

### Task 1 — Generic library identity and additive migration

**Module:** knowledge-library
**Purpose:** Add Collection, Source, and immutable SourceRevision values and idempotent SQLite tables beside Phase 2 sources.
**Dependencies:** None.
**Files/components:** src/mentor/knowledge.py; src/mentor/storage.py; tests/test_knowledge.py; tests/test_storage.py.
**Consumes/produces:** Phase-2-shaped sources -> typed library rows and revision-aware storage.
**Failing-test-first:** Write legacy-database migration tests before tables/methods.
**Acceptance criteria:** Initialization preserves threads, display turns, diagnostics, source linkage, and settings; source ID is stable; revision identity includes SHA-256; no corpus bytes enter fixtures.
**Verification:** Focused knowledge/storage tests, then full pytest.
**Commit boundary:** feat(knowledge-library): add revision-aware source model.
### Task 2 — Idempotent Jacob registry migration and change detection

**Module:** knowledge-library
**Purpose:** Backfill one Jacob collection without destructive import changes.
**Dependencies:** Task 1.
**Files/components:** src/mentor/source_registry.py; src/mentor/import_jacob.py; src/mentor/storage.py; tests/test_source_registry.py; tests/test_import_jacob.py.
**Consumes/produces:** Transcript discovery/current registry -> current revisions and visible pending replacement/removal state.
**Failing-test-first:** Write unchanged/replaced/removed/duplicate-looking-name fixture tests.
**Acceptance criteria:** Byte-identical records preserve file/store linkage; changed bytes create pending revision; unreadable input is visible; rerun is idempotent.
**Verification:** Focused importer/registry tests and full pytest.
**Commit boundary:** feat(knowledge-library): migrate Jacob registry to revisions.
### Checkpoint A — Library safety

- [ ] Phase 2 conversations and citation links remain readable.
- [ ] Backfill is idempotent and does not create/delete remote resources.
- [ ] Full pytest passes before anchors.


### Task 3 — Durable anchor model and deterministic validation

**Module:** source-anchors
**Purpose:** Make raw evidence locations revision-specific and drift-detectable.
**Dependencies:** Checkpoint A.
**Files/components:** src/mentor/anchors.py; src/mentor/knowledge.py; tests/test_anchors.py; tests/fixtures/anchor_transcript.txt.
**Consumes/produces:** SourceRevision plus transcript -> SourceAnchor and deterministic validation.
**Failing-test-first:** Test valid anchor, changed revision/span, invalid offset, timestamp drift, and duplicate-looking names.
**Acceptance criteria:** Validation checks all IDs, full hash, normalized offsets, timestamp when present, span fingerprint, and locator version; no OCR/video path.
**Verification:** Focused anchor tests and full pytest.
**Commit boundary:** feat(source-anchors): validate revision-specific evidence.
### Task 4 — Compilation runs and immutable candidate snapshots

**Module:** compilation-lifecycle
**Purpose:** Separate candidate construction from the sole published snapshot.
**Dependencies:** Task 3.
**Files/components:** src/mentor/compilation.py; src/mentor/storage.py; src/mentor/knowledge.py; tests/test_compilation.py.
**Consumes/produces:** Selected revisions -> CompilationRun, CorpusSnapshot, fingerprint, transitions, and zero-cost metric rows.
**Failing-test-first:** Test invalid transitions, failed-run isolation, and current-pointer lookup.
**Acceptance criteria:** Candidate does not alter published pointer; only build -> validate -> publish/fail transitions exist; run records model/prompt/schema and source/record/call/token/latency/cost/remote/failure metrics.
**Verification:** Focused lifecycle tests and full pytest.
**Commit boundary:** feat(compilation-lifecycle): add immutable snapshots.
### Task 5 — Typed derived-record schema and persistence

**Module:** source-extraction
**Purpose:** Store small composable semantic records, never record_type plus unconstrained JSON.
**Dependencies:** Task 4.
**Files/components:** src/mentor/derived_records.py; src/mentor/storage.py; src/mentor/knowledge.py; tests/test_derived_records.py.
**Consumes/produces:** Anchors/snapshot -> shared envelope plus Claim, Relationship, ProcedureSequenceHierarchy, Evolution, ConflictUnresolved, and typed facets.
**Failing-test-first:** Reject unknown family, free-form payload, missing anchor/dependency, invalid state, and numeric confidence.
**Acceptance criteria:** Every record has family, kind, states, anchors, dependencies, qualification; strategy implications default to synthesis unless explicitly raw taught; private reasoning is rejected.
**Verification:** Focused typed-record tests and full pytest.
**Commit boundary:** feat(source-extraction): add typed derived records.
### Task 6 — Mocked per-source extraction with versioned prompts

**Module:** source-extraction
**Purpose:** Produce bounded candidate records from one revision without self-approval or routine paid calls.
**Dependencies:** Task 5.
**Files/components:** src/mentor/compiler.py; src/mentor/compiler_prompts.py; src/mentor/derived_records.py; tests/test_compiler.py; tests/fixtures/compiler_responses.json.
**Consumes/produces:** One revision -> versioned extraction request and parsed candidate records through an injectable fake Responses client.
**Failing-test-first:** Test zero candidates, malformed family, missing anchors, and attempted self-validation.
**Acceptance criteria:** Extraction is per-source; Sol is live-mode only; prompt/schema versions persist; pytest has no credential/network path.
**Verification:** Focused compiler tests and full pytest.
**Commit boundary:** feat(source-extraction): add mocked candidate extraction.
### Task 7 — Deterministic and independent semantic claim validation

**Module:** source-extraction
**Purpose:** Ensure the extractor cannot certify its own claims.
**Dependencies:** Task 6.
**Files/components:** src/mentor/validation.py; src/mentor/compiler_prompts.py; src/mentor/anchors.py; tests/test_validation.py; tests/fixtures/validation_responses.json.
**Consumes/produces:** Candidate claim plus actual raw spans -> deterministic and independent semantic outcome.
**Failing-test-first:** Test bad hash/range and partial/unsupported/ambiguous results blocking source-extracted publication.
**Acceptance criteria:** Independent prompt/context ignores extractor rationale; only affirmative keeps source-extracted; other outcomes are excluded or unresolved with audit data.
**Verification:** Focused validation/compiler/anchor tests and full pytest.
**Commit boundary:** feat(source-extraction): validate extracted claims independently.
### Checkpoint B — Mechanical compiler proof

- [ ] Synthetic fixtures exercise all record families and validator outcomes.
- [ ] No test contacted OpenAI or inspected a real transcript.
- [ ] Full pytest passes before synthesis/invalidation.


### Task 8 — Typed synthesis for concepts, relationships, and procedures

**Module:** concept-synthesis
**Purpose:** Assemble validated records without a monolithic canonical glossary.
**Dependencies:** Checkpoint B.
**Files/components:** src/mentor/synthesis.py; src/mentor/derived_records.py; src/mentor/compilation.py; tests/test_synthesis.py.
**Consumes/produces:** Validated records -> bounded synthesis records with input IDs, transitive anchors, typed structure, and concise auditable justification.
**Failing-test-first:** Test invalid-record exclusion, ordered branches/prerequisites, and raw-text dump rejection.
**Acceptance criteria:** Relationships/procedures are structured; synthesis retains inputs/anchors/evidence state; no chain-of-thought or numeric confidence persists.
**Verification:** Focused synthesis tests and full pytest.
**Commit boundary:** feat(concept-synthesis): assemble typed knowledge records.
### Task 9 — Evolution, negative-evidence, and conflict semantics

**Module:** concept-synthesis
**Purpose:** Make year comparison and absence claims evidence-disciplined.
**Dependencies:** Task 8.
**Files/components:** src/mentor/synthesis.py; src/mentor/derived_records.py; tests/test_synthesis.py; tests/fixtures/synthesis_cases.json.
**Consumes/produces:** Validated records plus coverage -> Evolution and ConflictUnresolved records with supporting/competing anchors.
**Failing-test-first:** Reject unsupported never/new/removed/deprecated classifications; test compatible and unresolved conflicts.
**Acceptance criteria:** Not-found never becomes factual absence; later is not assumed new; evolution stores earlier/later sets; conflict remains visible until conditionally reconciled.
**Verification:** Focused synthesis tests and full pytest.
**Commit boundary:** feat(concept-synthesis): model evolution and uncertainty.
### Task 10 — Dependency DAG and selective stale propagation

**Module:** invalidation-publication
**Purpose:** Track raw revision -> extracted -> synthesis -> higher synthesis dependencies.
**Dependencies:** Task 9.
**Files/components:** src/mentor/dependencies.py; src/mentor/storage.py; src/mentor/derived_records.py; tests/test_dependencies.py.
**Consumes/produces:** Revisions/records -> DAG edges, reverse lookup, stale closure, and selective rebuild set.
**Failing-test-first:** Test self-cycle, multi-record cycle, direct/transitive stale, unaffected branch, and stale retrieval rejection.
**Acceptance criteria:** Cycles block candidate validation; changed revision marks every reverse-reachable record stale; rebuild set excludes unaffected branches.
**Verification:** Focused dependency tests and full pytest.
**Commit boundary:** feat(invalidation-publication): track derived dependencies.
### Task 11 — Local candidate validation and atomic publication

**Module:** invalidation-publication
**Purpose:** Swap a fully validated raw/derived snapshot pair atomically.
**Dependencies:** Task 10.
**Files/components:** src/mentor/compilation.py; src/mentor/storage.py; src/mentor/dependencies.py; tests/test_compilation.py; tests/test_storage.py.
**Consumes/produces:** Validated candidate/store IDs -> current snapshot pointer and archived/stale predecessor.
**Failing-test-first:** Test missing validation, failed candidate, read at swap boundary, and old/new store exclusion.
**Acceptance criteria:** Normal request cannot resolve a candidate; one transaction swaps snapshot/raw/derived IDs; predecessor is history, never current retrieval.
**Verification:** Focused lifecycle/dependency/storage tests and full pytest.
**Commit boundary:** feat(invalidation-publication): publish snapshots atomically.
### Checkpoint C — Safe local publication

- [ ] Synthetic revision change proves transitive stale propagation and selective rebuild closure.
- [ ] Failed candidate cannot affect the current pair.
- [ ] Full pytest passes. No remote candidate store exists yet.


### Task 12 — Vector-store adapter and guarded capability preflight

**Module:** derived-orientation-retrieval
**Purpose:** Isolate remote store operations and turn API uncertainty into an explicit stop condition.
**Dependencies:** Checkpoint C.
**Files/components:** src/mentor/vector_stores.py; src/mentor/config.py; tests/test_vector_stores.py; docs/phase-3-api-preflight.md.
**Consumes/produces:** OpenAI client plus snapshot artifacts -> fakeable create/attach/batch-status/search/detach adapter and preflight report.
**Failing-test-first:** Test statuses, filters, search mapping, detach-without-delete, and same-file/multiple-store unsupported outcome with fakes.
**Acceptance criteria:** No remote call in pytest; live preflight uses disposable non-corpus data only with Theo approval; contradiction stops work.
**Verification:** Focused fake-adapter tests and full pytest; live preflight is a separate manual checkpoint.
**Commit boundary:** feat(derived-orientation): isolate vector-store operations.
### Task 13 — Bounded published-snapshot orientation service

**Module:** derived-orientation-retrieval
**Purpose:** Retrieve compact derived orientation without raw citations or raw transcript text.
**Dependencies:** Task 12.
**Files/components:** src/mentor/orientation.py; src/mentor/vector_stores.py; src/mentor/derived_records.py; src/mentor/storage.py; tests/test_orientation.py.
**Consumes/produces:** Published snapshot/question/scope -> OrientationResult with typed records, anchors/source areas, snapshot, count, and budget result.
**Failing-test-first:** Test stale/wrong snapshot, duplicate record/concept, budget, invalid local record, and raw-dump rejection.
**Acceptance criteria:** Searches only current derived store with current filters; deduplicates before hard budget; exposes truncation; never fabricates citations.
**Verification:** Focused orientation tests and full pytest.
**Commit boundary:** feat(derived-orientation): bound current snapshot context.
### Task 14 — Mentor integration, diagnostics, and replay safety

**Module:** mentor-knowledge-orchestration
**Purpose:** Add server-owned orientation function/tool loop while preserving Phase 2 source safeguards.
**Dependencies:** Task 13.
**Files/components:** src/mentor/chat_service.py; src/mentor/prompts.py; src/mentor/orientation.py; src/mentor/storage.py; tests/test_chat_service.py.
**Consumes/produces:** OrientationService -> bounded function output and KnowledgeContext audit: used, snapshot ID, record IDs/count, budget state, separate raw File Search metrics.
**Failing-test-first:** Use Responses-shaped fixtures for broad orient-first, narrow raw-first, exact timestamp, tool failure, stale snapshot, citation repair, and compaction replay.
**Acceptance criteria:** Broad categories orient when snapshot exists; narrow/exact need not; Direct teaching still raw verifies/cites; raw overrides orientation; full payload is excluded from replay/browser display.
**Verification:** Chat/streaming/citation/compaction tests and full pytest.
**Commit boundary:** feat(mentor-orchestration): add derived orientation safely.
### Checkpoint D — Mentor regression gate

- [ ] Phase 2 citations, exact timestamps, repair, compaction, streaming, persistence, and loopback behavior remain green.
- [ ] Broad fixtures orient; narrow fixtures do not add unnecessary calls.
- [ ] Full pytest passes before Inspector work.


### Task 15 — Read-only Knowledge Inspector API

**Module:** knowledge-inspection
**Purpose:** Expose inspectable Phase 3 state without source-management actions.
**Dependencies:** Checkpoint D.
**Files/components:** src/mentor/server.py; src/mentor/storage.py; src/mentor/knowledge.py; tests/test_server.py.
**Consumes/produces:** Snapshots/records/anchors/dependencies/turn audit -> loopback browser-safe read-only JSON.
**Failing-test-first:** Test current/pending/failed status, details, anchor metadata, orientation audit, missing ID, and non-loopback access.
**Acceptance criteria:** Exposes coverage/failures, typed states, anchors, relations, evolution/conflicts, stale/dependencies, and turn record IDs; never raw corpus, hidden reasoning, secrets, or mutation routes.
**Verification:** Focused server tests and full pytest.
**Commit boundary:** feat(knowledge-inspection): expose read-only audit API.
### Task 16 — Minimal static Assimilation Inspector

**Module:** knowledge-inspection
**Purpose:** Let Theo inspect assimilation while the chat remains primary.
**Dependencies:** Task 15.
**Files/components:** src/mentor/static/index.html; src/mentor/static/app.js; src/mentor/static/style.css; tests/test_server.py.
**Consumes/produces:** Inspector JSON -> secondary read-only snapshot/record/anchor/evolution/conflict/stale/turn-audit surface.
**Failing-test-first:** Add static/API contract assertions before markup changes.
**Acceptance criteria:** No upload/edit/delete/source-manager actions; raw/derived are distinct; desktop and compact UI have no console errors.
**Verification:** Full pytest plus desktop and compact-width loopback browser smoke.
**Commit boundary:** feat(knowledge-inspection): add read-only inspector UI.
### Task 17 — Deterministic regression suite and evaluation harness

**Module:** phase-3-evaluation
**Purpose:** Prove structural invariants and prepare a local-only baseline/assimilated measurement schema.
**Dependencies:** Task 16.
**Files/components:** tests/test_phase3_regression.py; tests/fixtures/; src/mentor/evaluation.py; docs/phase-3-evaluation.md.
**Consumes/produces:** Synthetic fixtures -> cross-module regression coverage and evaluation metric schema.
**Failing-test-first:** Add cross-module regression cases before evaluation aggregation.
**Acceptance criteria:** Covers migration, anchors, records, validation, synthesis, absence/conflict, DAG, publication, budgets, citations, replay, Inspector, and Phase 2 regression; tracks quality/citations/connections/evolution/correction/orientation/raw searches/passages/tokens/latency/cost.
**Verification:** Full pytest, diff/secret checks, browser smoke.
**Commit boundary:** test(phase-3): add deterministic assimilation regressions.
### Task 18 — Six-source pilot manifest and measured-cost protocol

**Module:** phase-3-evaluation
**Purpose:** Select the smallest representative real-corpus pilot without running it or guessing full-corpus cost.
**Dependencies:** Task 17.
**Files/components:** docs/phase-3-evaluation.md; .gitignore; src/mentor/evaluation.py; tests/test_evaluation.py.
**Consumes/produces:** Migrated inventory -> ignored manifest of exactly six unique source-revision IDs and cost protocol.
**Failing-test-first:** Test six-unique-source and structural-role validation first.
**Acceptance criteria:** Six sources cover foundational teaching, detailed procedure, one concept across 2025/2026, exception/condition, synthesis/evolution material, and possible conflict/uncertainty; names come from inventory not product logic; protocol captures calls, records, audits, tokens, latency, cost, remote counts.
**Verification:** Manifest/evaluation tests and full pytest. No pilot API call.
**Commit boundary:** docs: define Phase 3 pilot measurement protocol.
## Gate 1 — Paid six-source pilot

**Requires:** Tasks 1-18 complete; deterministic/browser checks green; guarded disposable-data API preflight passed; reviewed six-source manifest; Theo's explicit paid-pilot approval.

**Run:** Compile/validate only the six selected source revisions with GPT-5.6 Sol; build a pilot candidate; audit anchors/validation, relationships, one evolution result, and one unresolved/conflict case where evidence permits; test derived retrieval/orientation and a small Phase 2 baseline comparison.

**Record locally:** revision IDs, compiler/prompt/schema versions, metrics, record counts/families, audit results, retrieval traces, comparison results, and measured full-corpus forecast. Never commit private raw outputs.

**STOP:** Theo reviews semantic representation, evidence discipline, record volume, UX, and cost before full assimilation.


### Task 19 — Record Gate 1 pilot decision

**Module:** phase-3-evaluation
**Purpose:** Preserve pilot pass/fail without granting automatic permission to scale.
**Dependencies:** Gate 1.
**Files/components:** docs/phase-3-evaluation.md; tasks/todo.md.
**Consumes/produces:** Private pilot evidence -> concise non-private decision and explicit full-assimilation authorization state.
**Failing-test-first:** Not applicable; this is human-gate documentation.
**Acceptance criteria:** Failed/undecided blocks scaling; pass records measured forecast and still requires a separate Theo approval.
**Verification:** Theo review and staged-private-data check.
**Commit boundary:** docs: record Phase 3 pilot decision.
## Gate 2 — Full Jacob assimilation

Task 20 requires the separate Theo approval recorded by Task 19. A pilot pass is not enough.


### Task 20 — Full 150-source assimilation after separate approval

**Module:** phase-3-evaluation
**Purpose:** Build and validate full candidate with the pilot-proven schema and measured budget.
**Dependencies:** Task 19 plus explicit Theo approval.
**Files/components:** src/mentor/compilation.py; src/mentor/vector_stores.py; src/mentor/evaluation.py; ignored local runtime/evaluation artifacts.
**Consumes/produces:** Selected revisions -> full candidate, coverage/failure audit, stores, records, dependencies, actual metrics.
**Failing-test-first:** Not applicable; deterministic error paths are already covered.
**Acceptance criteria:** Every intended source is processed or visibly failed; structural/anchor/validation/dependency/coverage gates pass before publication; actual cost compares to pilot forecast.
**Verification:** Paid local audit, sampled human anchor audit, full pytest.
**Commit boundary:** Commit only source/docs/test changes; never corpus/database/private output.
## Gate 3 — Mentor evaluation and final human acceptance

Task 22 is the final Theo pass/fail decision. Automated or paid results are
evidence, never a substitute.


### Task 21 — Baseline-versus-assimilated Mentor evaluation

**Module:** phase-3-evaluation
**Purpose:** Determine whether the complete Mentor feels as though it studied Jacob while preserving raw authority.
**Dependencies:** Task 20 published full snapshot.
**Files/components:** docs/phase-3-evaluation.md; src/mentor/evaluation.py; tasks/todo.md; ignored local evaluation records.
**Consumes/produces:** Phase 2 baseline plus full snapshot -> private metrics and concise safe report.
**Failing-test-first:** Not applicable; deterministic instrumentation is Task 17.
**Acceptance criteria:** Measures correctness, completeness, citations, conceptual links, evolution, correction, orientation/raw searches/passages, tokens, latency, cost; includes adversarial fake claims/conflicts/refined years/obscure/exhaustive/timestamp/mixed prompts.
**Verification:** Paid evaluation, raw citation sampling, and full pytest.
**Commit boundary:** docs: record Phase 3 evaluation results, after Theo approves what is safe to commit.

### Task 22 — Theo's final Phase 3 human acceptance

**Module:** phase-3-evaluation
**Purpose:** Keep the final pass/fail decision human-owned.
**Dependencies:** Task 21.
**Files/components:** docs/phase-3-evaluation.md; tasks/todo.md.
**Consumes/produces:** Live behavior and evaluation evidence -> Theo's pass/fail record only.
**Failing-test-first:** Not applicable; mandatory human gate.
**Acceptance criteria:** Acceptance confirms raw authority, inspectability,
invalidation, broad-course understanding, citation discipline, and Phase 2
compatibility. No Phase 4 begins automatically.
**Verification:** Theo's explicit live decision.
**Commit boundary:** docs: record Phase 3 human acceptance, if accepted.
## ADR decision

No standalone ADR is created in this planning commit. The approved Phase 3 design already records the durable decisions for immutable raw/derived store pairs, server-owned orientation, and source-extracted records remaining derived. If the guarded API preflight contradicts one, stop and create a focused ADR only with Theo's approval.

## Plan self-review

- [x] All ten approved module IDs are represented in dependency order.
- [x] Every task has purpose, dependencies, files, interfaces, failing-test-first work where applicable, acceptance, verification, and commit boundary.
- [x] Pilot, full corpus, and final Mentor evaluation are separate gates.
- [x] Routine pytest is mocked/local, and strict Phase 3 boundaries remain.
