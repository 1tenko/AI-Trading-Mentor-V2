# Phase 6 Implementation Plan: Strategy Projects, Multi-Mentor Knowledge & Persistent Coaching Workspace

**Status:** Approved for autonomous Phase 6A + Phase 6B implementation on 2026-09-05
**Specification:** [Phase 6 design](../specs/2026-09-03-phase-6-strategy-projects-multi-mentor-design.md)
**Current baseline:** `70e49ff` on `feature/phase-5-backtest-analysis`
**Intended implementation branch:** `feature/phase-6-strategy-projects`

## Repository assessment

The application is a Python 3.12 loopback server with a static HTML/CSS/JS
browser client, SQLite (`Storage`), and the OpenAI Python SDK `>=2,<3`. It has
one global `threads` table, a legacy global Jacob `sources` registry and one
`settings.vector_store_id`. `ChatService._request` currently builds one native
File Search tool using that store, uses `store: false`, preserves encrypted
reasoning/compaction replay, and has proven source, profile, analysis and
ephemeral qualitative-data boundaries.

Phase 6 therefore extends existing `Storage`, `ChatService`, `server.py`,
`import_jacob.py`, `source_registry.py`, and the static app. It does not need
React, Next.js, a new service, a graph database, an embeddings pipeline, or an
analytics/dashboard framework. The approved build reconciled the historical
Phase 5 human gate and appended the Phase 6 checklist without overwriting its
implementation record.

## Architecture decisions

| Decision | Chosen design | Reason |
|---|---|---|
| Project boundary | Nullable `project_id` plus explicit thread source behavior | Existing chats become `LEGACY_JACOB`; new General Mentor chats are `GENERAL_NEUTRAL`; project chats are `PROJECT`. |
| Source boundary | One local library + one native vector store per corpus/authority | Library keys are `gxt.garrett`, `gxt.afyz`, `gxt.erik`, `gxt.splash`, `gxt.zay`, and `gxt.theo_notes`; a disabled library's store ID is absent. |
| Legacy Jacob | Register it as a system library while preserving legacy tables/settings during migration | No breaking rewrite; General Mentor stays compatible. |
| Cross-mentor research | Bounded authority-aware native File Search passes, then Sol reconciliation | Keeps raw citations and avoids custom RAG/ranking. |
| Garrett canonical lineage | Path-derived `canonical_role` inside `gxt.garrett` only | Identifies Garrett's current formulation and history without ranking another mentor's evidence, globally downranking older Garrett material, or deciding Theo's strategy. |
| Overrides | Validated one-turn source-scope snapshot | Natural request affects one answer and cannot change saved settings. |
| Project memory | Typed SQLite records plus immutable event/lineage links | Inspectable continuity without treating chat text as the database. |
| Dataset connection | Safe project-owned snapshot of existing `AnalysisEvidence` envelope only | Preserves Phase 5 raw-data/qualitative privacy and thread deletion semantics. |
| Promotion | Stable pending ID plus UI approval; exact chat approval fallback | UI is primary; only `approve promotion #<id>` for the one pending request shown in the prior turn can adopt. |
| UI | Existing static client; chat-first project navigation plus a compact Roadmap | Matches the product and avoids a dashboard rewrite. |

## Dependency graph and checkpoints

```text
T1 contracts + migration proof
  -> T2 project/thread foundation
  -> T3 library/source-revision foundation
  -> T4 import staging + remote-index adapter
  -> Checkpoint A: local source/project isolation
  -> T5 source-selection and Responses contract
  -> T6 project-aware chat/citations/replay
  -> T7 project-state service + project tools
  -> T8 research ledger + safe empirical links
  -> T9 promotion/playbook
  -> Checkpoint B: project-memory/provenance integrity
  -> T10 project navigation/source-settings UI
  -> T11 Roadmap + ledger/playbook UI
  -> T12 behavioral evals + browser/regression proof
  -> Checkpoint C: final review and Theo human gate
```

Tasks 1–4 are Phase 6A foundation. Tasks 5–6 complete Phase 6A Mentor
behavior. Tasks 7–9 form Phase 6B state integrity. Tasks 10–11 expose it.
Task 12 is the integrated gate. Migration, source import, remote resource
creation and paid/live behavioral runs are sequential, never parallelized.

## Data and schema migrations

The migration is additive and follows `Storage.initialize()`'s existing
idempotent SQLite pattern. It must run against a copied runtime before the real
local database. Existing Phase 1–5 tables/data remain untouched.

| Table/field | Purpose | Core constraints |
|---|---|---|
| `strategy_projects` | `id`, name, status, created/updated metadata | Stable local IDs; archived is not deleted. |
| `threads.project_id` | General (`NULL`) versus one project | FK; old rows stay `NULL`. |
| `source_libraries` | authority name/key, kind, state, local configuration | Unique key; no source content. |
| `project_source_libraries` | saved enabled state per project/library | Unique pair; no implicit global default. |
| `mentor_library_sources` / `mentor_library_source_revisions` | Phase 6 logical source + immutable content revision | Physical names avoid the accepted Phase 3 `library_sources` table; hash, metadata, local path, remote IDs, indexed status; revisions never mutate. |
| `library_import_batches` | staged confirmation/import/reconciliation | Explicit lifecycle and error status. |
| `library_vector_stores` | vector-store ID, lifecycle and retention metadata | One active store per library; remote IDs private runtime only. |
| `thread_source_scopes` | saved effective source scope per turn | Historic fidelity; safe labels/IDs only. |
| `project_state_events` | append-only validated coaching-state mutations | Origin turn and idempotency key. |
| `project_state_snapshot` | current objective/experiment/blocker/next action | Derived transactionally from events. |
| `project_mastery_items` | concept/status/reason/evidence reference | Controlled status vocabulary. |
| `project_research_records` | typed observation/hypothesis/experiment/finding/rule record | Provenance and lifecycle checks. |
| `project_empirical_evidence_refs` | safe frozen reference to Phase 5 envelope | No rows, headers, notes or qualitative payload. |
| `project_playbook_versions` / `project_playbook_rules` | immutable adopted rules | Rule is only created through approved promotion. |
| `project_promotion_requests` | proposed/approved/rejected promotion | Explicit approval turn/action, one decision only. |

The migration maps legacy Jacob source metadata to a `jacob` system library only
after a dry-run report confirms a one-to-one source mapping. Any collision,
missing local source or conflicting remote attachment stops migration. The old
`sources` table and settings value remain as compatibility read paths until all
Phase 2/5 tests and migration assertions prove safe retirement; Phase 6 does
not delete them. Runtime database, file hashes linked to private content,
corpus files and remote IDs remain untracked.

## Source-library and OpenAI design

### Source import lifecycle

```text
local configured folder
 -> deterministic discovery (library identity from selected folder)
 -> SHA-256 + metadata staging
 -> confirmation summary / duplicate & conflict report
 -> immutable source-revision registration
 -> upload and attach to that library vector store
 -> wait for completed/failed indexing
 -> locally record exact remote attachment status
```

The importer reads transcripts only for hashing/uploading after confirmation.
It does not infer mentor identity, combine files, synthesize material, or expose
transcript body in logs. Exact hash duplicate in the same library is skipped;
cross-library duplicate is a blocking attribution conflict, not copied into two
authorities. Source revisions include title/filename, relative category, source
type, optional date, timestamp availability, revision hash and optional
`canonical_role`. For Garrett, the confirmed path maps Advanced to
`CURRENT_CANONICAL_ADVANCED`, Beginner to `CURRENT_CANONICAL_FOUNDATION`, and
all other Garrett material to `GARRETT_ARCHIVAL_AND_COMPLEMENTARY`; non-Garrett
libraries omit the role. This is an origin/current-formulation hierarchy within
Garrett's corpus, not a cross-library relevance, quality, performance or
recommendation score. Older Garrett sources remain first-class teaching
evidence; absence from Anomaly does not imply obsolescence. Missing timestamp is
represented honestly.

OpenAI's current reference documents vector-store file attributes, `completed`
readiness, `vector_store_ids` on the File Search tool and multi-file batches.
The implementation will verify those exact SDK contracts in a disposable
synthetic-store test before it touches a real mentor corpus. The selected
library set drives the native tool's `vector_store_ids`; no disabled ID appears.
Library identity is additionally checked by local `file_id -> source revision ->
library` mapping before evidence is displayed. [File Search/Responses
reference](https://developers.openai.com/api/reference/typescript/resources/beta/subresources/responses),
[vector-store file attributes](https://developers.openai.com/api/reference/resources/vector_stores/subresources/files),
and [vector-store search](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/search)
are the implementation references.

### Native-citation contract gate

Before implementing cross-mentor final answers, Task 5 must prove against the
installed SDK/API version that native citations remain present after the chosen
multi-library research sequence. It must validate:

- single library is the only reachable source when selected;
- separate source calls retain `file_id`/result metadata;
- the final answer can retain native citations for Direct mentor teaching;
- a cross-library answer can identify each authority without merging claims;
- stateless replay excludes File Search result payloads as Phase 2 requires.

If an API limitation prevents native citations after serial multi-pass search,
the bounded fallback is one native File Search tool with only the explicit
enabled IDs, plus library ownership resolved locally from returned `file_id`.
It does not permit a custom vector-search answer path. If neither meets citation
integrity, stop for Theo before building source behavior.

## Detailed implementation tasks

### Task 1 — Phase 6 contracts, migration fixture and OpenAI synthetic-spike plan

**Goal:** Freeze Python dataclasses/enums, table constraints, API error shape,
and the no-paid synthetic-vector-store contract before production code.

**Modules/files:** `src/mentor/storage.py`, new project/source contract module,
`tests/test_storage.py`, new source/project contract tests, Phase 6 task ledger
after reconciliation.

**Tests first:** Legacy database initializes unchanged; each new enum rejects an
unknown state; a legacy thread remains General Mentor; a project record cannot
reference an unknown library; public errors use `{error: ...}`; dry-run has no
filesystem or OpenAI mutation.

**Implementation:** Define compact typed records and explicit input/output
shapes; add only additive tables/indexes/migrations; create a dry-run migration
report; document a disposable synthetic OpenAI contract test that enforces the
approved cumulative $5 cap before API calls.

**Verify:** `\.venv\Scripts\python.exe -m pytest tests\test_storage.py -q`.

**Depends on:** none. **Complete when:** migration is idempotent on a copied
Phase 5 database and all new contracts are rejected/accepted at the boundary.

### Task 2 — Strategy Project and conversation scope vertical slice

**Goal:** Make General Mentor and project-local threads coexist without changing
existing chat behavior.

**Modules/files:** `storage.py`, `server.py`, `chat_service.py`, `app.js`,
`index.html`, `app.css`, storage/server/chat tests.

**Tests first:** Existing thread lists/loads/deletes exactly as before; project
thread belongs to one project; another project's thread cannot load/append;
project deletion/archive does not delete General Mentor data; normal thread
deletion still removes its Phase 5 evidence/replay scope transactionally.

**Implementation:** Add project CRUD/list/read endpoints with strict request
validation; extend thread list/read/create only additively with safe scope
fields; add a minimal project selector and project-local conversation list; keep
the current General Mentor default.

**Verify:** focused storage/server/chat tests, loopback browser smoke for new
project -> new chat -> refresh -> restore -> delete chat.

**Depends on:** Task 1. **Complete when:** no project content leaks through
General Mentor thread retrieval and no existing Phase 4/5 UI flow changes.

### Task 3 — Mentor-library registry and legacy Jacob compatibility

**Goal:** Establish independent authorities without reimporting or breaking
accepted Jacob chat.

**Modules/files:** `storage.py`, `source_registry.py`, `import_jacob.py`, the
new `source_libraries.py`, and source/storage tests.

**Tests first:** library key uniqueness; immutable revision registration; same
library hash dedupe; cross-library duplicate conflict; legacy Jacob migration
dry run/count parity; failed source has no active retrieval identity; source
metadata is never transcript body; Garrett path roles are exact and immutable;
non-Garrett sources cannot receive a Garrett canonical role.

**Implementation:** Generalize discovery around a declared library root and
metadata, retain legacy wrapper command for Jacob; record source/revision and
remote attachment lifecycle separate from current source records; add a safe
library listing API with no paths/remote IDs.

**Verify:** focused source/storage/import tests plus an ignored fixture folder;
inspect Git diff for corpus/runtime leakage.

**Depends on:** Tasks 1–2. **Complete when:** synthetic Garrett, Afyz, Erik,
Splash, Zay, and Theo Notes libraries can be staged independently while Jacob
retains its current registry and response behavior.

### Task 4 — Folder confirmation, immutable import and remote-index adapter

**Goal:** Let Theo stage known folders, inspect a compact summary, explicitly
confirm and incrementally import a mentor library.

**Modules/files:** importer/registry module, `storage.py`, `server.py`, static
library settings UI, importer/server tests.

**Tests first:** confirmation required before upload; folder identity wins over
file text; Garrett/Afyz/Erik/Splash/Zay/Theo Notes remain separate in the safe
summary; duplicate retry reuses revision; index failure stays visible and is not
searchable; no raw path/body in API/log/error; remote operations mocked; cleanup
record is private.

**Implementation:** Build an idempotent staged-import state machine; create or
reuse the library store only after confirmation; upload/attach in documented
batch or bounded sequential form; wait for `completed`; store only safe status
for UI. A failed item cannot reach active source scope.

**Verify:** importer/server tests with fake OpenAI client; no live import until
Theo provides a representative corpus and confirms paid/remote work.

**Depends on:** Task 3. **Complete when:** staged summary, approval, retry,
dedupe and error recovery are transactional and privacy-safe.

### Checkpoint A — Source and project foundation

- Migrate a disposable Phase 5 runtime and confirm legacy General Mentor/
  Jacob behavior remains intact.
- Use only mocked remote calls to prove folder confirmation, dedupe and indexed
  state transitions.
- Verify a project lists only its enabled library records and project threads.
- Run focused source/project tests, full deterministic suite, Git/privacy diff
  inspection, and independent architecture review before any model integration.

### Task 5 — Source-scope policy and native File Search contract

**Goal:** Compute and enforce the saved-plus-one-turn source scope before every
project request.

**Modules/files:** `chat_service.py`, new source-scope policy module,
`storage.py`, `prompts.py`, tests and synthetic Responses fixtures.

**Tests first:** saved toggle off means absent vector store ID; `Afyz only` and
`compare Garrett and Erik, ignore Afyz` resolve only if explicit names/actions
are user-present; override leaves saved settings unchanged; General Mentor has
no project stores; disabled Jacob is unreachable; source scope survives history
view; malformed override is refused rather than broadened; a normal GxT teaching
question with all five mentor libraries enabled schedules relevant research for
Garrett, Afyz, Erik, Splash, and Zay; Garrett's current-formulation ordering
applies only inside `gxt.garrett` and does not suppress complementary old
Garrett material.

**Implementation:** Add deterministic scope resolver and per-turn snapshot;
issue source tools only from its output; add a bounded cross-mentor coverage
policy that requires a relevant pass per enabled library before collective
claims. Run the approved disposable API contract spike before choosing serial
versus multi-ID native File Search implementation.

**Verify:** source-scope unit/integration tests; synthetic Responses contract
fixture; approved live synthetic-store test only if required.

**Depends on:** Checkpoint A. **Complete when:** observed request payloads prove
architectural isolation, not merely prompt instruction.

### Task 6 — Project-aware Mentor behavior, citation ownership and replay

**Goal:** Give Sol project coaching context and enforce mentor-specific claims
while preserving the Phase 2 replay/citation contract.

**Modules/files:** `chat_service.py`, `prompts.py`, `storage.py`, existing chat
tests plus model-fixture tests.

**Tests first:** Afyz-only Y is not attributed to Garrett; real conflicting
passages remain conflict; missing Garrett evidence becomes absence-not-found;
exact timestamp comes only from evidence; cross-mentor claim requires all
eligible libraries; old turns preserve their source scope; file-search result
payloads are not replayed; Phase 4 profile and Phase 5 tools retain their
existing source/privacy boundaries; creator/canonical status is never phrased as
empirical superiority, automatic recommendation or adopted playbook status;
useful Afyz/Erik/Splash/Zay explanations and complementary older Garrett
teaching remain available in normal enabled-scope teaching.

**Implementation:** Extend prompt/context with compact project identity and
source rules; map returned `file_id` to safe mentor/source labels; use existing
citation/evidence persistence; add source-scope and per-mentor search
diagnostics. Do not inject all project history or raw source passages.

**Verify:** `tests/test_chat_service.py` focused selection plus full suite;
manual synthetic answer review.

**Depends on:** Task 5. **Complete when:** direct, synthesis, uncertainty and
source-specific citations display honestly under saved and temporary scope.

### Task 7 — Project coaching state and constrained local tools

**Goal:** Persist a small live coaching whiteboard through normal chat without
letting the model make unconstrained project changes.

**Modules/files:** new `project_tools.py`, `project_service.py`, `storage.py`,
`chat_service.py`, `prompts.py`, server endpoints and tests.

**Tests first:** a typed tool may update only current objective, active
experiment, blockers, next action or a controlled mastery status; event and
snapshot commit atomically; duplicate tool call is idempotent; model cannot
cross project boundary or fabricate user approval; direct chat prose does not
silently create an adopted rule.

**Implementation:** Add a restricted project-state tool schema and server
dispatcher modeled on existing profile/analysis boundary; append safe events,
rebuild current snapshot transactionally and supply a bounded snapshot to only
the owning project chat. Add General Mentor's compact status-summary reader.

**Verify:** focused state/chat tests and restart/refresh integration test.

**Depends on:** Task 6. **Complete when:** “what do I do today?” can reliably
read the persisted next action, and state remains project-local.

### Task 8 — Research ledger and safe Phase 5 empirical linkage

**Goal:** Make hypotheses, experiments, findings and limitations durable without
recreating Phase 7 or leaking raw Phase 5 data.

**Modules/files:** `storage.py`, project-state service, `chat_service.py`,
project API/UI read models, storage/chat/analysis regression tests.

**Tests first:** valid provenance transitions; project record cannot refer to
another project/thread; a research record may link only validated
`AnalysisEvidence`; linked safe snapshot contains no raw rows/headers/notes or
qualitative function output; deleting origin thread leaves deliberate
project-owned safe finding but deletes all thread-owned evidence; stale/missing
reference is visible, not silently repaired.

**Implementation:** Use fixed record kinds and concise fields rather than an
unbounded notebook; create a safe project-evidence snapshot at the intentional
research-record boundary; retain an origin availability state and limitations.
Do not add a full experiment runner or automatic methodology state machine.

**Verify:** focused ledger/privacy/replay tests, Phase 5 privacy suite and full
suite.

**Depends on:** Task 7. **Complete when:** project research continuity works
without altering Phase 5 fresh-consent or raw-data boundaries.

### Task 9 — Playbook promotion and immutable lineage

**Goal:** Enforce Theo's approval before a provisional/validated finding becomes
an adopted playbook rule.

**Modules/files:** project state/ledger service, `storage.py`, `chat_service.py`,
`server.py`, minimal approval UI, tests.

**Tests first:** model cannot promote directly; explicit proposal alone does not
adopt; only exact approval tied to an eligible promotion adopts; duplicate
approval is idempotent; reject/cancel does not alter playbook; updated rule
creates a new version; lineage shows source/experiment/finding/approval links.

**Implementation:** Add `promotion_request` plus user-confirmation endpoint or
validated confirmation message; freeze adopted rule text and links in a version;
give Sol read-only playbook context. Preserve user decisions distinctly from
source teaching and AI recommendation.

**Verify:** focused promotion/replay tests and project browser flow.

**Depends on:** Task 8. **Complete when:** every adopted rule has an explicit
user-decision record and answerable lineage without folklore.

### Checkpoint B — Persistent project integrity

- Project roadmap, state, ledger, empirical link and playbook survive restart
  and project-chat switch.
- Cross-project references, implicit promotions and raw Phase 5 leakage fail
  closed.
- Complete Phase 4/5 profile, dataset, consent, diagnostics, deletion and
  replay regressions; independent privacy/architecture review has no P0/P1.

### Task 10 — Chat-first project navigation and source controls

**Goal:** Make General Mentor, projects, library settings and one-turn scope
understandable without moving the product into an admin UI.

**Modules/files:** `index.html`, `app.js`, `app.css`, `server.py`, static/server
tests, browser tests.

**Tests first:** General vs GxT selection; project conversations stack only
inside project; toggle persists and disabled source is absent on next request;
one-turn override is visibly temporary; library settings show safe counts/status
not paths/IDs; all controls usable at desktop and 390px.

**Implementation:** Reuse existing sidebar/composer patterns, add compact scope
selector and Sources popover, source-scope chip and project-specific empty
state. No Data workspace restoration, dashboard grid or new client framework.

**Verify:** browser smoke at desktop/390px, keyboard navigation and existing
attachment -> ask flow.

**Depends on:** Checkpoint B. **Complete when:** Theo can open GxT, manage
sources and ask an override question without technical vocabulary.

### Task 11 — Compact Roadmap, research history and playbook inspection UI

**Goal:** Expose useful coaching continuity secondary to chat.

**Modules/files:** static app/CSS/HTML, server read endpoints, storage views,
browser/server tests.

**Tests first:** roadmap reflects current transactionally saved state; updates
after a tool/event; empty states are honest; history preserves provenance label;
playbook shows approval/lineage rather than a generated claim; General Mentor
shows summary only; narrow layout remains readable.

**Implementation:** Add one compact Roadmap panel/view with Current Focus, Next
Action, Experiment/progress, Blockers, mastery list and recent research. Add
read-only ledger/playbook drill-down; do not make Theo manually manage cards.

**Verify:** browser checks, refresh/restart restoration and accessibility review.

**Depends on:** Task 10. **Complete when:** the product remains chat-first and
the Roadmap answers “what am I working on and why?” in one scan.

### Task 12 — Behavioral evaluations, full regression and human-gate package

**Goal:** Prove Phase 6 mechanics and prepare Theo's final product judgement.

**Modules/files:** synthetic corpus fixtures, model-fixture evaluator, test
modules, acceptance documentation/checklist only after results.

**Tests first and evaluation matrix:**

| Scenario | Required outcome |
|---|---|
| Garrett: X; Afyz: X+Y; Erik: X; Splash: X+application nuance Z; Zay: X | X shared; Y Afyz-only; Z Splash-only; no collective overclaim |
| Garrett: A; Afyz: do not A/use B | Explicit disagreement, not forced synthesis |
| Afyz teaches Y; Garrett has no result | Absence scoped to search, not Garrett rejection |
| Garrett old: A; Advanced: B | B is Garrett's current formulation when the evidence establishes revision; A remains attributed archival/complementary context, not silently erased |
| Garrett Beginner: X; Advanced: X+condition Y | Current formulation preserves the Foundation core and the attributed Advanced qualification |
| Normal “teach me X in GxT” with five enabled mentors | Relevant Garrett, Afyz, Erik, Splash, and Zay searches occur; shared core, nuances and disagreements remain attributed |
| Garrett current: A; Afyz refinement: B | Canonical lineage remains Garrett; B remains first-class teaching evidence and may be the clearer explanation |
| Empirical evidence favors a non-Garrett-derived B | Candidate recommendation may favor B; it is not canonicalized or adopted without promotion approval |
| Afyz disabled / Afyz-only override | Request and evidence exclude every other library; saved toggle unchanged |
| Two projects | GxT source/finding/playbook unavailable in QT without explicit import |
| Existing unfinished experiment | Coach recommends frozen next action and explains pushback; Theo can override |
| Validated finding | No adopted rule until explicit approval |
| Exact timestamp | Mentor/source/passage/timestamp only when evidence supports it |

**Implementation:** Add deterministic unit/integration fixtures first, then the
authorized small synthetic-only behavioral run within the cumulative $5 cap.
Measure citations, scope/tool payloads, latency/token/cost, project-state
accuracy and source-claim correctness. Run browser flows, full suite and fresh
independent review; fix all P0/P1, then push. Never upload real GxT material in
this task.

**Verify:** `\.venv\Scripts\python.exe -m pytest`; local browser smoke;
explicit Git/privacy inspection; independent review; Theo human gate.

**Depends on:** Tasks 1–11. **Complete when:** every acceptance scenario is
verified and implementation stops for Theo rather than starting Phase 7.

## AI behavioral-evaluation plan

Use a deterministic synthetic transcript corpus with timestamped files and
unambiguous expected labels before any paid real-corpus testing. The harness
must inspect both answer text/citations and request diagnostics: selected store
IDs, per-library searches, effective scope, retrieved file ownership, source
provenance labels, no unsafe replay payload and no prompt-only isolation claim.

The live evaluation uses only the approved non-sensitive synthetic timestamped
fixtures for all five mentors within the $5 cumulative cap. It compares:

- single authority teaching;
- shared core with mentor-specific nuance;
- affirmative disagreement;
- no-evidence/absence wording;
- chronological refinement vs explicit replacement;
- broad/exhaustive cross-mentor request;
- normal enabled-scope GxT teaching that researches relevant Garrett, Afyz,
  Erik, Splash, and Zay evidence rather than stopping after Garrett;
- Garrett historical versus current Advanced and Foundation versus Advanced;
- canonical lineage versus an empirically favored non-Garrett-derived variant;
- source-specific timestamp question;
- coaching continuity and project isolation;
- research pushback and manual override;
- promotion refusal/approval.

The rubric scores attribution correctness, citation/source precision,
epistemic honesty, project continuity, next-action usefulness, scope isolation,
privacy and UX clarity. It also asks whether the answer materially helps Theo
understand, operationalize, practice or test the model toward an evidence-backed
personal playbook. It never awards a longer answer merely for using more
sources, and it never equates canonical authorship with empirical superiority.

## Privacy, isolation and Phase 4/5 regression plan

- Continue loopback-only server, `store: false`, native compaction and encrypted
  replay semantics.
- Persist no raw GxT transcript body in Git, diagnostics, display state or new
  project records. Existing citation/evidence projection remains the maximum
  persisted raw-source reference.
- Never expose Phase 5 raw spreadsheet rows, raw headers, paths, note text or
  ephemeral `function_call_output` via a project record. Existing one-turn
  consent and qualitative diagnostics are regression-tested unchanged.
- Add project ID to every new query and write; use SQL FK/unique constraints and
  service-level validation to reject cross-project access.
- Add source scope to each request and turn, then assert source IDs are a subset
  of the enabled/temporary scope in tests.
- Retain current permanent thread-deletion transaction for thread-owned records;
  project-owned safe research snapshot has explicit origin availability instead
  of silently keeping raw thread evidence.

Full regression includes all existing `test_profile.py`, `test_datasets.py`,
`test_analysis.py`, `test_chat_service.py`, `test_server.py`, `test_storage.py`,
source/import tests and browser smoke for dark chat, attachment flow and consent.

## Risks and stop conditions

| Risk | Impact | Mitigation / stop condition |
|---|---|---|
| Current SDK cannot retain native citations through cross-library sequence | High | Run disposable contract spike first; use only native supported fallback or stop for architecture decision. |
| Legacy Jacob migration does not map one-to-one | High | Dry run/copy; preserve legacy data and stop on discrepancy. |
| Cross-library duplicate confuses authority | High | Block and request Theo's attribution decision; never silently duplicate. |
| Project record leaks Phase 5 notes/rows | High | Typed safe evidence snapshot, negative tests, independent privacy review; stop on P0/P1. |
| Model changes settings or adopts a rule | High | Server-owned tools, explicit user gate, idempotent DB state, adversarial tests. |
| Roadmap becomes dashboard bloat | Medium | One compact read model; no manual card management or analytics panels. |
| Full corpus/remote costs expand unknowingly | Medium | Representative corpus first, confirmation prior to upload, per-library status/retention inventory, explicit cost authorization. |
| Phase 4/5 regressions | High | Per-task focused tests and Checkpoints A/B/C; no merge/push as accepted Phase 6 without full suite and review. |

## Human acceptance plan

Theo's final gate, after all automation, is intentionally compact:

1. Open General Mentor: it sees a brief GxT status but cannot quote/use detailed
  GxT material without an explicit project request.
2. Open GxT Mastery: switch Garrett/Afyz/Erik/Splash/Zay; ask shared, unique and conflict
   questions. Verify clear mentor attribution, citation/timestamps and no
   fabricated collective doctrine.
3. Disable Afyz, ask the same question; then say “Afyz only for this answer.”
   Confirm source evidence changes for that turn and saved setting remains off.
4. Start/continue an experiment; refresh/restart and ask “what should I do
   today?” Confirm the specific next action and appropriate coaching pushback.
5. Record a finding, inspect its provenance, propose a rule and confirm that it
   does not become adopted until Theo explicitly approves it.
6. Recheck a Phase 5 attachment question and a fresh qualitative-note question
   to confirm the existing privacy and consent behavior remain unchanged.

## Estimated autonomous build shape

**12 implementation tasks, 3 checkpoints, then one human gate.** Build in
small commits after completed coherent vertical slices; push each significant
verified milestone to the dedicated Phase 6 branch. Stop immediately for an
unresolved high-risk OpenAI citation contract, source migration discrepancy,
privacy/source-isolation breach, or P0/P1 independent-review finding. No Phase
7, web research or full external corpus import is implicit.

## Approval questions resolved by this plan

- Historic Phase 5 checklist is retained and its human-gate state was reconciled
  at Phase 6 build start; no implementation progress record was overwritten.
- A project-owned empirical record stores a deliberately frozen **safe** evidence
  envelope, not the originating conversation's raw evidence. Thus deletion stays
  complete for thread-owned data while an intentional project finding remains
  explainable.
- Cross-library native citation behavior is a high-risk contract gate, not an
  assumption. No paid/live source work is run in this planning session.

## Plan approval condition

Theo approved this plan on 2026-09-05 and authorized the branch/ledger
reconciliation, additive migration, and up to $5 cumulative OpenAI spend for
synthetic contract/behavioral checks only. Real GxT upload and production GxT
vector stores remain unauthorized.

## Authoritative autonomous-execution addendum

This addendum controls wherever an earlier task left an implementation choice.
A worker must not add a module, endpoint, table, source route, or approval
method not named below without stopping for Theo.

### Fixed module and interface map

| File | Responsibility | Public interface |
|---|---|---|
| `src/mentor/project_models.py` **new** | Enums/dataclasses/input validation | `ThreadSourceBehavior`, `StrategyProject`, `ThreadContext`, `SourceLibrary`, `ProjectSourceScope`, `SearchBudget`, `PendingPromotion` |
| `src/mentor/project_service.py` **new** | Project ownership, thread creation, events/snapshot/summary | `ProjectService.create_project`, `create_project_thread`, `project_context`, `apply_state_event`, `roadmap`, `general_summaries` |
| `src/mentor/source_libraries.py` **new** | Browser staging, hashes, revisions/imports, File ownership | `SourceImportService.create_staging_import`, `stage_browser_file`, `finalize_manifest`, `confirm_import`, `import_status`, `register_legacy_jacob_library`, `library_for_file` |
| `src/mentor/source_scope.py` **new** | Explicit source override parsing/budgets/tool selection | `resolve_source_scope`, `source_tools_for_scope`, `search_budget_for`, `is_explicit_authority_request` |
| `src/mentor/project_tools.py` **new** | Restricted local Responses functions | `PROJECT_TOOLS`, `ProjectToolDispatcher.dispatch` |
| `src/mentor/project_ledger.py` **new** | Research/empirical records, promotions/playbook | `ProjectLedgerService.record_research`, `link_analysis_evidence`, `create_promotion_request`, `approve_promotion`, `playbook` |

`import_jacob.py` remains the compatibility CLI: it delegates to
`SourceImportService.register_legacy_jacob_library`. No split importer and no
additional generic importer module are permitted.

### General Mentor and legacy Jacob compatibility

- Existing rows receive `thread_source_behavior='LEGACY_JACOB'` during
  `Storage.initialize()` migration and retain all historic display/replay items.
- `Storage.create_thread(title)` creates `GENERAL_NEUTRAL`.
- `ProjectService.create_project_thread(project_id, title)` creates `PROJECT`.
- `ChatService._request` dispatches before building source tools:
  `LEGACY_JACOB` gets legacy Jacob; `GENERAL_NEUTRAL` gets no File Search unless
  `is_explicit_authority_request(question)` detects Jacob; `PROJECT` gets
  `resolve_source_scope`.
- Explicit General Jacob use records one safe temporary scope for that turn and
  cannot affect the next turn.
- General summaries may include only project name/status, objective, experiment
  label/progress, next action and one unresolved-question summary.

### Exact project, library, Roadmap and promotion HTTP contracts

All JSON errors use the existing `{"error":"human-safe message"}` shape.
Unknown input keys, invalid IDs and cross-project IDs are `400`; missing records
are `404`; idempotency/state conflicts are `409`; internal failures are generic
`503` without source content, paths or remote IDs.

| Method/path | Request | Response |
|---|---|---|
| `GET /api/projects` | — | `{"projects":[{"id","name","status","summary"}]}`; summary is General-safe only |
| `POST /api/projects` | `{"name":"GxT Mastery"}` | `201 {"id","name","status":"ACTIVE"}` |
| `GET /api/projects/{id}` | — | safe project, Roadmap, libraries and project-local thread summaries |
| `PATCH /api/projects/{id}` | `{"status":"ARCHIVED"}` | updated safe record |
| `POST /api/projects/{id}/threads` | `{"title":"…"}` | `201 {"id","title","project_id","thread_source_behavior":"PROJECT"}` |
| `GET /api/projects/{id}/libraries` | — | library key/display/enabled/source count/index status only |
| `PUT /api/projects/{id}/libraries/{library_key}` | `{"enabled":true}` | saved library setting only |
| `GET /api/projects/{id}/roadmap` | — | objective, experiment, blockers, next action, mastery and safe recent ledger summaries |
| `GET /api/projects/{id}/ledger` | — | safe paginated research records and provenance labels |
| `GET /api/projects/{id}/playbook` | — | adopted playbook version/rules/lineage only |
| `POST /api/projects/{id}/promotion-requests/{request_id}/approve` | `{"expected_status":"PENDING"}` plus `X-Idempotency-Key` | `201 {"promotion_id","playbook_version","rule"}` |
| `POST /api/projects/{id}/promotion-requests/{request_id}/reject` | `{"expected_status":"PENDING"}` | rejected request; playbook unchanged |

Existing `POST /api/threads` accepts optional `{"title":"…","mode":"general"}`
and creates `GENERAL_NEUTRAL`. The legacy browser payload without `mode` also
creates neutral new General Mentor threads. Existing `GET /api/threads/{id}`
adds `project_id` and `thread_source_behavior` but removes no historic field.

### Exact schema additions

All additions are idempotent in `Storage.initialize()`. Every project write
uses an FK and service-level project ownership validation.

| Table / column | Required fields |
|---|---|
| `threads.thread_source_behavior` | `TEXT NOT NULL DEFAULT 'LEGACY_JACOB' CHECK(... IN ('LEGACY_JACOB','GENERAL_NEUTRAL','PROJECT'))` |
| `threads.project_id` | nullable `INTEGER REFERENCES strategy_projects(id)`; service requires it only for `PROJECT` |
| `strategy_projects` | `id`, unique `name`, `status CHECK('ACTIVE','ARCHIVED')`, timestamps |
| `source_libraries` | `id`, unique `library_key`, `corpus_key`, `authority_name`, `authority_kind CHECK('MENTOR','USER_NOTES','SYSTEM')`, `display_name`, `status` |
| `project_source_libraries` | `project_id`, `library_id`, `enabled CHECK(0,1)`, primary key pair |
| `library_vector_stores` | `library_id` primary key, unique `vector_store_id`, `state CHECK('NONE','CREATING','READY','FAILED','DELETING','DELETED')`, `cleanup_audit_id` |
| `mentor_library_sources` | `library_id`, `source_key`, `display_title`, `source_type`, `relative_category`, optional `source_date`, `timestamps_available`, unique library/source key; physically separate from accepted Phase 3 `library_sources` |
| `mentor_library_source_revisions` | `source_id`, `sha256`, `byte_size`, `relative_path`, `staged_path`, optional `canonical_role CHECK(canonical_role IS NULL OR canonical_role IN ('CURRENT_CANONICAL_ADVANCED','CURRENT_CANONICAL_FOUNDATION','GARRETT_ARCHIVAL_AND_COMPLEMENTARY'))`, remote IDs, `index_state CHECK('STAGED','UPLOADING','INDEXING','READY','FAILED','SUPERSEDED')`, unique source/hash |
| `library_import_batches` | `project_id`, `state CHECK('STAGING','READY_FOR_CONFIRMATION','IMPORTING','COMPLETE','FAILED','CANCELLED')`, safe `manifest_json`, `error_code`, timestamps |
| `thread_source_scopes` | `thread_id`, `turn_number`, safe `scope_json`, primary key pair |
| `project_state_events` | `project_id`, unique `event_key`, `kind CHECK('OBJECTIVE','EXPERIMENT','BLOCKER','NEXT_ACTION','MASTERY')`, payload/origin/timestamp |
| `project_state_snapshots` | `project_id` primary key, objective, experiment, blockers JSON, next action, updated event/timestamp |
| `project_mastery_items` | project/concept, `status CHECK('NOT_STARTED','LEARNING','OPERATIONALIZING','TESTING','PROVISIONAL','VALIDATED')`, reason/evidence, unique pair |
| `project_research_records` | project, `kind CHECK('OBSERVATION','HYPOTHESIS','OPERATIONAL_DEFINITION','EXPERIMENT','EMPIRICAL_FINDING','PROJECT_FINDING','LIMITATION','PROVISIONAL_RULE','USER_DECISION')`, status, summary, provenance, origin/supersession |
| `project_empirical_evidence_refs` | project/research IDs, original evidence ID, `safe_envelope_json`, `origin_available CHECK(0,1)` |
| `project_promotion_requests` | project/provisional rule, proposed text, `status CHECK('PENDING','APPROVED','REJECTED','CANCELLED')`, proposed/shown/decision turns; one pending request per provisional rule |
| `project_playbook_versions` | project, version, approval thread/turn, unique project/version |
| `project_playbook_rules` | playbook version, unique promotion request, rule text, lineage JSON |

`Storage.migrate_legacy_jacob_dry_run()` compares every existing Jacob relative
path, filename, year, local availability, uploaded File ID and vector-store File
ID. A discrepancy stops. The old `sources` table and `settings.vector_store_id`
remain compatibility read paths through Phase 6.

### Exact folder-import API and browser contract

The only selector is:

`<input id="source-directory" type="file" webkitdirectory multiple accept=".txt,text/plain">`

The browser gives `File.webkitRelativePath`; the server accepts a normalized
POSIX relative path only: no absolute/rooted path, `..`, empty segment, control
character, non-`.txt` suffix, unknown root, or file above 10 MiB.

`GxT/Garrett/** -> gxt.garrett`
`GxT/Afyz/** -> gxt.afyz`
`GxT/Erik/** -> gxt.erik`
`GxT/Splash/** -> gxt.splash`
`GxT/Zay/** -> gxt.zay`
`GxT/Theo Notes/** -> gxt.theo_notes`

The pre-upload confirmation summary reports separate safe transcript counts for
Garrett, Afyz, Erik, Splash, Zay, and Theo Notes. It never returns local paths.

Within `GxT/Garrett`, the normalized confirmed path deterministically assigns:

`Anomaly Mentorship/GxT Advanced/** -> CURRENT_CANONICAL_ADVANCED`
`Anomaly Mentorship/Beginner/** -> CURRENT_CANONICAL_FOUNDATION`
`** -> GARRETT_ARCHIVAL_AND_COMPLEMENTARY`

No other library accepts these roles. They may order Garrett-internal research
for “what does Garrett currently teach?” but never order libraries against one
another for normal GxT teaching.

Nested segments become `relative_category`. Staging bytes live only in ignored
`data/source-imports/<batch-id>/`. There is no filesystem picker or absolute-path
fallback. Unsupported browsers receive a Chrome/Edge directory-upload message.

| Endpoint | Request | Response |
|---|---|---|
| `POST /api/source-imports` | `{"project_id":1}` | `201 {"id":7,"state":"STAGING","accepted_root":"GxT"}` |
| `POST /api/source-imports/7/files` | binary `text/plain`; headers `X-Source-Relative-Path`/`X-Source-Import-Ordinal` | safe accepted relative path |
| `POST /api/source-imports/7/finalize` | `{}` | counts/new/duplicates/conflicts grouped by exact library key |
| `POST /api/source-imports/7/confirm` | `{"confirm":true}` | `202 {"id":7,"state":"IMPORTING"}` |
| `GET /api/source-imports/7` | — | safe status/count/error fields only |

### Exact source-scope budget

| Depth | Per-library passes | Overall passes | Results/pass |
|---|---:|---:|---:|
| `NORMAL` | 1 | 6 | 8 |
| `DEEP` | 2 | 12 | 12 |
| `EXHAUSTIVE` | 3 | 18 | 16 |

Phase 6 permits six effective libraries per cross-mentor turn. “all mentors”,
“every mentor”, and “complete comparison” escalate to `EXHAUSTIVE`. Trivial
questions need not search every library mechanically; collective claims require
coverage of every implicated enabled authority. More than six enabled libraries
returns a local subset clarification—never a silent skip. Each relevant library
receives the initial search plus its complementary passes; a no-result library
is `NO_RELEVANT_EVIDENCE` and may only support scoped absence wording. Project
override grammar is exactly:

`<authority> only [for this answer]`
`compare <authority> and <authority> [ignore <authority>]`
`use all enabled mentors again`

Only known library labels/actions are accepted. Other text uses saved scope.

### Exact promotion-approval contract

A proposal creates one `PENDING` `Promotion #<id>` card with exact proposed rule
text. Primary approval is:

`POST /api/projects/{project_id}/promotion-requests/{id}/approve`
`X-Idempotency-Key: promotion:{id}`
`{"expected_status":"PENDING"}`

The endpoint checks project ownership/state/idempotency payload and creates a
playbook version/rule atomically. Retry returns the first result. The only chat
approval is case-insensitive `approve promotion #<positive integer>`, and only
when that request is the sole pending proposal shown in the immediately prior
assistant turn. “sure”, “approve this”, stale/multiple IDs, or delayed approval
are ordinary chat text and cannot adopt.

### Disposable OpenAI citation contract

Create `scripts/phase6_openai_contract.py` and only non-sensitive timestamped
fixtures in `tests/fixtures/phase6/`:

`Garrett: A requires X.`
`Afyz: A requires X and Y.`
`Erik: A requires X.`
`Splash: A requires X and adds application nuance Z.`
`Zay: A requires X.`

The script refuses to run without `RUN_OPENAI_PHASE6_CONTRACT_TEST=1` and an API
key. It creates one disposable File/vector store per library with `library_key`,
`source_revision_key`, optional `canonical_role` and `timestamps_available`
attributes. It requests
`include=["file_search_call.results"]` and asserts correct `file_id` ownership,
native Direct-teaching citations, absent disabled-store ID/results, shared X,
Afyz-only Y, Splash-only Z, and File Search-result-free replay candidate.

A `try/finally` deletes every created store and File in reverse dependency order
for both success and failure. Cleanup failure fails the script. Ignored
`data/phase6-contract-audits/<run-id>.json` holds only resource IDs/status,
model, latency, token/cost projection and cleanup outcome. No real GxT corpus,
path or text body is used or retained. Failure blocks Task 6; the sole fallback
is one native File Search call with exactly selected IDs plus local file/library
verification. Custom retrieval is forbidden.

### Mandatory live behavioral proof

Create `scripts/phase6_live_behavioral_eval.py`. It refuses to run without
`RUN_OPENAI_PHASE6_LIVE_EVAL=1`, an API key, and an explicit cumulative budget
at or below the approved $5 ceiling. It uses `gpt-5.6-sol` and the same
synthetic corpus/cleanup
implementation. It fails on any failure of these exact cases:

1. X shared across the five mentors; Y Afyz-only; Z Splash-only; never
   universal GxT X+Y or X+Z.
2. Garrett Use A/Afyz Do not use A/use B remains explicit disagreement.
3. No Garrett result is scoped absence, not rejection.
4. Disabled mentor has no request/result/citation/authority claim.
5. Afyz-only one-turn override does not change saved toggle.
6. All-enabled comparison searches Garrett, Afyz, Erik, Splash, and Zay
   initially and complementarily within the six-library cap.
7. Timestamp is accurate or honestly unavailable.
8. GxT source/finding/playbook is unavailable to another project and General
   Mentor detail context.
9. Normal enabled-scope GxT teaching researches relevant Garrett, Afyz, Erik,
   Splash, and Zay evidence and retains useful attributed explanations from
   each.
10. Garrett old/current and Foundation/Advanced questions apply canonical role
    only within Garrett, with no “later means better” inference.
11. Canonical lineage never becomes empirical superiority, automatic coaching
    recommendation or adopted playbook status.
12. A synthetic empirical result favoring a non-Garrett-derived variant can
    support a provisional recommendation but cannot adopt it without approval.

Ignored `data/phase6-live-evals/<run-id>.json` records pass/fail, model, scope,
queries, result ownership, citations, latency, tokens, estimated cost and
cleanup. A failed live case blocks Theo's final gate.

### Fully executable task matrix

#### Task 1 — Contracts, migration and ledger

- **Create:** `project_models.py`, `tests/test_projects.py`.
- **Modify:** `storage.py`, `tests/test_storage.py`, `tasks/todo.md` (append only).
- **Tests first:** copied legacy DB counts/replay/display unchanged; existing
  threads are `LEGACY_JACOB`; fresh General is neutral; project requires ID;
  invalid enum/FK rejects; dry-run has no write/OpenAI client call.
- **Expected failure:** columns/tables/methods absent.
- **Steps:** define fixed models; add exact schema; implement
  `create_project`/`create_thread`/`thread_context`/dry-run; add copied DB test;
  append checklist preserving Phase 5.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_storage.py tests\test_projects.py -q`.
- **Commit:** `feat(phase6): add project and library storage contracts`.
- **Depends:** none.
- **Complete:** fresh and legacy databases pass with no remote/source mutation.

#### Task 2 — Neutral General and project chats

- **Create:** `project_service.py`.
- **Modify:** `chat_service.py`, `server.py`, static app files,
  `tests/test_projects.py`, `tests/test_server.py`, `tests/test_chat_service.py`.
- **Endpoints:** `GET/POST /api/projects`, `GET/PATCH /api/projects/{id}`,
  `POST /api/projects/{id}/threads`; thread read adds project/behavior safely.
- **Tests first:** legacy Jacob unchanged; new generic General has no File Search;
  explicit Jacob is one turn; project cannot cross scope; General summary has no
  source/rule/finding detail.
- **Expected failure:** new chats use global Jacob.
- **Steps:** implement ProjectService; route behavior before tools; endpoints;
  compact scope selector/project chat grouping.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_projects.py tests\test_server.py tests\test_chat_service.py -q`.
- **Commit:** `feat(phase6): add neutral general mentor and project chats`.
- **Depends:** Task 1.
- **Complete:** neutral General, isolated project chats, preserved legacy history.

#### Task 3 — Corpus-scoped libraries and Jacob compatibility

- **Create:** `source_libraries.py`, `tests/test_source_libraries.py`.
- **Modify:** `storage.py`, `source_registry.py`, `import_jacob.py`,
  `tests/test_import_jacob.py`, `tests/test_projects.py`.
- **Tests first:** `gxt.afyz` differs from future `other.afyz`; same-library hash
  dedupe; cross-library conflict; exact Jacob dry-run; no body/path/remote IDs;
  exact Garrett canonical-role mapping and non-Garrett role refusal.
- **Expected failure:** only global source registry exists.
- **Steps:** implement library/revision methods; dry-run/register Jacob
  transaction; compatibility CLI delegation.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_source_libraries.py tests\test_import_jacob.py tests\test_storage.py -q`.
- **Commit:** `feat(phase6): add corpus-scoped source libraries`.
- **Depends:** Tasks 1–2.
- **Complete:** private immutable library identities and exact Jacob mapping.

#### Task 4 — Folder staging and confirmed import

- **Create:** `tests/fixtures/phase6/import/`.
- **Modify:** `source_libraries.py`, `storage.py`, `server.py`, static app files,
  `tests/test_source_libraries.py`, `tests/test_server.py`.
- **Tests first:** exact root mapping; invalid path/file refusal; finalize has
  correct counts/no remote call; false confirmation no store; fake index failure
  inactive; retry dedupe.
- **Expected failure:** no staged folder endpoint.
- **Steps:** hidden picker/sequential upload; validate/stage/finalize; injected
  fake import job; safe summary/Cancel/Import display.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_source_libraries.py tests\test_server.py -q`.
- **Commit:** `feat(phase6): stage mentor folders before import`.
- **Depends:** Task 3.
- **Complete:** browser folder -> confirmation works before any remote action.

#### Checkpoint A

- [ ] Copied-runtime legacy parity.
- [ ] Tasks 1–4 focused tests plus `.\.venv\Scripts\python.exe -m pytest`.
- [ ] Git privacy diff review.
- [ ] Independent review of General neutrality, library identity, paths/staging.
- [ ] Stop for migration/attribution/privacy discrepancy; no remote store yet.

#### Task 5 — Scope, budgets and citation gate

- **Create:** `source_scope.py`, `scripts/phase6_openai_contract.py`,
  synthetic fixture transcripts, `tests/test_source_scope.py`.
- **Modify:** `chat_service.py`, `prompts.py`, `storage.py`,
  `tests/test_chat_service.py`.
- **Tests first:** behavior routing, disabled ID omission, exact grammar, caps,
  exhaustive coverage, no-result absence, over-six clarification, normal GxT
  teaching coverage across relevant enabled mentors, Garrett-internal canonical
  ordering only for Garrett-current questions.
- **Expected failure:** global Jacob and no caps.
- **Steps:** resolver/budget/scope persistence; replace direct store selection;
  contract cleanup runner; run it under the explicit synthetic-only $5
  authorization.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_source_scope.py tests\test_chat_service.py -q`.
- **Conditional verify:** `$env:RUN_OPENAI_PHASE6_CONTRACT_TEST='1'; .\.venv\Scripts\python.exe scripts\phase6_openai_contract.py`.
- **Commit:** `feat(phase6): enforce source scope and citation contract`.
- **Depends:** Checkpoint A.
- **Complete:** deterministic scope passes; authorized live contract/correct cleanup
  passes. Stop before Task 6 if citations fail.

#### Task 6 — Attribution, citations and replay

- **Create:** none.
- **Modify:** `chat_service.py`, `prompts.py`, `storage.py`,
  `tests/test_chat_service.py`, `tests/test_source_scope.py`.
- **Tests first:** Afyz-only attribution, Splash-only nuance, conflict, absence,
  timestamps, historic
  scope, no File Search replay payload, Phase 4/5 regression, no canonical-to-
  empirical/recommendation/adoption leap, no non-Garrett suppression, and no
  global downranking of complementary older Garrett evidence.
- **Expected failure:** Jacob-only ownership.
- **Steps:** fixed prompt blocks; safe library label from `library_for_file`;
  collective-claim counters; preserve compaction/replay.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_chat_service.py tests\test_source_scope.py -q`.
- **Commit:** `feat(phase6): preserve mentor attribution in project chat`.
- **Depends:** successful Task 5 gate.
- **Complete:** native citations retain exact authority/provenance distinction.

#### Task 7 — Coaching state/local tools

- **Create:** `project_tools.py`, `tests/test_project_tools.py`.
- **Modify:** `project_service.py`, `storage.py`, `chat_service.py`,
  `prompts.py`, `server.py`, `tests/test_projects.py`.
- **Interfaces:** `PROJECT_TOOLS` exposes only `update_project_state` and
  `update_project_mastery`; dispatcher and `apply_state_event` use stable key.
- **Tests first:** allowed fields only; idempotence; event/snapshot atomicity; no
  cross-project/ledger/playbook mutation; General summary bound.
- **Expected failure:** no safe project dispatcher.
- **Steps:** strict schemas; existing continuation integration; atomic event/
  snapshot; roadmap endpoint.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_project_tools.py tests\test_projects.py tests\test_chat_service.py -q`.
- **Commit:** `feat(phase6): add persistent project coaching state`.
- **Depends:** Task 6.
- **Complete:** project next action survives restart; model cannot escape state.

#### Task 8 — Ledger/safe empirical snapshots

- **Create:** `project_ledger.py`, `tests/test_project_ledger.py`.
- **Modify:** `storage.py`, `project_service.py`, `project_tools.py`,
  `chat_service.py`, `server.py`, `tests/test_analysis.py`, `tests/test_storage.py`.
- **Tests first:** valid transitions; same-project deterministic evidence; safe
  snapshot rejects raw/qualitative content; deleted origin marked unavailable;
  cross-project refusal.
- **Expected failure:** no ledger/safe snapshot boundary.
- **Steps:** fixed research types; allowlisted evidence envelope; dispatcher
  branch; safe ledger API.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_project_ledger.py tests\test_analysis.py tests\test_chat_service.py -q`.
- **Commit:** `feat(phase6): retain safe project research records`.
- **Depends:** Task 7.
- **Complete:** durable research preserves Phase 5 privacy/deletion/consent.

#### Task 9 — Promotion/playbook lineage

- **Create:** none.
- **Modify:** `project_ledger.py`, `project_tools.py`, `storage.py`,
  `chat_service.py`, `server.py`, static app files,
  `tests/test_project_ledger.py`, `tests/test_server.py`.
- **Tests first:** no model promotion; card/ID; UI atomic retry; generic/ambiguous
  refusal; exact prior-turn chat approval; reject/cancel/versioning.
- **Expected failure:** no user-bound adoption state.
- **Steps:** transaction/lineage; Approve/Reject card; exact parser; playbook API.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_project_ledger.py tests\test_server.py tests\test_chat_service.py -q`.
- **Commit:** `feat(phase6): require explicit playbook promotion approval`.
- **Depends:** Task 8.
- **Complete:** every adopted rule maps to exact approval request/turn.

#### Checkpoint B

- [ ] Tasks 5–9 focused/full tests.
- [ ] Project/thread/state/ledger/evidence/playbook/source isolation.
- [ ] General neutral, legacy Jacob, profile, Phase 5 attachment/analysis/consent/
  diagnostics/replay/deletion regression.
- [ ] Independent privacy/architecture review; stop on P0/P1 before UI.

#### Task 10 — Project/source chat UI

- **Create:** none.
- **Modify:** static app files, `server.py`, `tests/test_server.py`,
  `tests/test_projects.py`.
- **Tests first:** local project list; saved toggle vs temporary chip; safe folder
  error; Phase 5 attachment/consent/theme; desktop/390px keyboard route.
- **Expected failure:** no scope/source controls.
- **Steps:** scope selector; Sources popover/toggle/import; scope chip; preserve
  existing stream/composer behavior.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_server.py tests\test_projects.py -q`.
- **Browser verify:** desktop/390px plus attach -> ask.
- **Commit:** `feat(phase6): add project source controls to chat`.
- **Depends:** Checkpoint B.
- **Complete:** Theo needs no technical path/ID/vector-store knowledge.

#### Task 11 — Roadmap/inspection UI

- **Create:** none.
- **Modify:** static app files, `server.py`, `project_service.py`,
  `project_ledger.py`, `tests/test_projects.py`, `tests/test_project_ledger.py`.
- **Tests first:** roadmap persistence, honest empty state, provenance display,
  General summary-only, mobile readability.
- **Expected failure:** no Roadmap/read models.
- **Steps:** compact Roadmap and read-only ledger/playbook; no cards/charts.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_projects.py tests\test_project_ledger.py tests\test_server.py -q`.
- **Browser verify:** desktop/390px refresh/restart.
- **Commit:** `feat(phase6): show persistent coaching roadmap`.
- **Depends:** Task 10.
- **Complete:** compact panel explains focus/next action; detail is project-local.

#### Task 12 — Live proof/final gate package

- **Create:** `scripts/phase6_live_behavioral_eval.py` and
  `docs/phase-6-evaluation.md` only after results.
- **Modify:** behavioral tests/evaluation documentation only.
- **Tests first:** deterministic mirrors, no-opt-in refusal, cleanup test double,
  browser flows.
- **Expected failure:** no live rubric/evaluator.
- **Steps:** evaluator; enforce the existing cumulative $5 synthetic-only spend
  authorization; live/focused/full/browser/review; P0/P1 remediation; safe
  results doc; push then stop.
- **Focused verify:** `.\.venv\Scripts\python.exe -m pytest tests\test_projects.py tests\test_source_libraries.py tests\test_source_scope.py tests\test_project_tools.py tests\test_project_ledger.py -q`.
- **Full verify:** `.\.venv\Scripts\python.exe -m pytest`.
- **Conditional verify:** `$env:RUN_OPENAI_PHASE6_LIVE_EVAL='1'; .\.venv\Scripts\python.exe scripts\phase6_live_behavioral_eval.py`.
- **Commit:** `test(phase6): verify strategy project behavior and safety`, then
  final safe evaluation-doc commit.
- **Depends:** Tasks 1–11.
- **Complete:** all deterministic/live/browser/privacy/review work passes,
  cleanup confirmed, branch pushed, stop for Theo—not Phase 7.

### Final acceptance and mandatory stop conditions

Theo verifies neutral new General Mentor, explicit Jacob temporary sourcing,
GxT attribution/disagreement/timestamps across Garrett, Afyz, Erik, Splash, and
Zay, disabled/one-turn source behavior, Garrett archival/current and
Foundation/Advanced distinctions without global mentor ranking, normal teaching
across every relevant enabled mentor, roadmap
continuity/coaching pushback, promotion safety and Phase 5 qualitative consent
regression. A recommendation may follow empirical evidence toward a derived
variant, but only Theo can adopt it.

Stop and report instead of improvising on inexact Jacob migration, ambiguous
folder authority, cross-library duplicate hash, failed citation/cleanup gate,
raw Phase 5 persistence, isolation breach, unresolved P0/P1 or missing paid
authorization. Phase 7 is never permitted.
