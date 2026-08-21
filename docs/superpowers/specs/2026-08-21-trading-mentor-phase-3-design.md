# Phase 3 Design: Extensible Knowledge Foundation + Jacob Corpus Assimilation

**Status:** Proposed for Theo's approval.
**Phase boundary:** Design only. Approval of this document authorizes a later
implementation plan, not implementation.

## 1. Objective

Phase 3 establishes the reusable knowledge-library foundation for the Unified
Mentor and proves it with the first fully assimilated corpus: Jacob Speculates
2025–2026.

The Mentor should feel as though it has thoroughly studied and organized the
Jacob mentorship before a conversation begins. It must retain the Phase 1 and
Phase 2 contract: the frontier model teaches and reasons; raw Jacob transcripts
are the factual authority; OpenAI-native File Search locates original evidence;
and Direct source teaching is supported by native raw-source citations.

Assimilation is not a canonical AI glossary or an alternate corpus. It is a
small, structured, inspectable, versioned, source-linked set of *derived*
orientation records. It helps the model find the relevant concepts, relations,
exceptions, and source areas quickly. It never independently establishes a
factual teaching.

The reusable library model must later accommodate additional authors, books,
mindset sources, and Theo's own material without a redesign. Phase 3 imports
and assimilates **only Jacob 2025–2026**.

## 2. Binding decisions and assumptions

| Decision | Phase 3 rule |
|---|---|
| Raw versus derived | Raw source revisions are authoritative. Every compiled record, including a source-extracted claim, is derived and non-authoritative. |
| Raw evidence | A substantive Direct source teaching answer still needs fresh verification in the active raw corpus and native raw citation(s). |
| Derived retrieval | Derived records live in local structured storage and a distinct OpenAI vector store, accessed only through a server-owned orientation function. |
| Compiler model | GPT-5.6 Sol performs high-value extraction, independent validation, and reconciliation. No automatic Terra/Sol routing is added. |
| Compilation | Compilation is explicit/on-demand. Detection of a source change starts a candidate snapshot; it never silently recompiles or silently promotes. |
| Publication | Raw and derived candidate stores are immutable for the run. A local transactional pointer swap publishes a fully validated pair together. |
| Superseded material | Historical revisions may be retained for provenance, but cannot be included in normal active raw or derived retrieval after publication. |
| User interface | Include a minimal read-only Assimilation Inspector. Defer editable library/source-management UI until a later approved phase. |
| Phase 2 compatibility | Preserve local-only operation, native compaction/replay semantics, raw citation integrity, evidence distinction, stream recovery, and historical settings fidelity. |

## 3. Scope

### Included

- A generic local Knowledge Library hierarchy: collection, source, revision,
  corpus snapshot, and remote retrieval artifacts.
- Migration of the present Jacob-only source registry/import records into that
  hierarchy without changing the raw transcript authority.
- Durable source anchors and a source-revision model.
- A systematic Jacob compiler: extraction, independent validation,
  clustering/synthesis, evolution/conflict analysis, dependency tracking,
  selective rebuild, audit, and atomic publication.
- Separate current raw and derived remote search scopes.
- Query-time derived orientation plus preserved raw native File Search for
  factual verification and citations.
- A small read-only inspector and Phase 3 structural, semantic, and human
  acceptance evaluation.

### Excluded

- Trader Daye or any other new trading source.
- Psychology/mindset content, Theo's notes, Trader Profile, or long-term memory.
- Strategy Lab, backtests, deterministic statistics, Strategy Projects, or a
  scientific strategy workflow.
- Automatic model routing, broker/execution integrations, chart/screenshot
  vision, public hosting, authentication, or multi-user features.
- A broad source-management dashboard, custom embeddings, custom ranking,
  custom RAG answer generation, or a monolithic AI-written “Jacob bible.”

## 4. Capability map and order

The approved module identifiers remain stable throughout Phase 3.

| Module ID | Responsibility | Depends on |
|---|---|---|
| `knowledge-library` | Generic collections, sources, revisions, and current-snapshot records. | — |
| `source-anchors` | Revision-specific raw evidence locations and deterministic validation. | `knowledge-library` |
| `compilation-lifecycle` | Candidate run state, versioning, coverage, and publication gates. | `knowledge-library`, `source-anchors` |
| `source-extraction` | Per-source derived extraction. | `source-anchors`, `compilation-lifecycle` |
| `concept-synthesis` | Concepts, relationships, evolution, tensions, and uncertainty. | `source-extraction` |
| `invalidation-publication` | Dependency closure, stale marking, selective rebuild, and atomic activation. | `compilation-lifecycle`, `concept-synthesis` |
| `derived-orientation-retrieval` | Separate derived-store search and bounded server tool result. | `invalidation-publication` |
| `mentor-knowledge-orchestration` | Query-time policy, raw verification, citations, and replay safety. | `derived-orientation-retrieval` |
| `knowledge-inspection` | Read-only snapshot and record inspection. | `knowledge-library`, `source-anchors`, `invalidation-publication` |
| `phase-3-evaluation` | Deterministic, paid semantic, adversarial, baseline, and human gates. | All modules |

Build order is the dependency order above. `knowledge-inspection` may follow
publication once its data model is stable, but it never controls publication.

## 5. Terminology

- **Collection:** A user-controllable grouping such as a mentor/course corpus.
  Phase 3 has one active collection: Jacob Speculates 2025–2026.
- **Source:** A logical work within a collection, independent of filename. A
  source has stable identity, author/domain/course metadata, and revisions.
- **Revision:** Immutable observed bytes for one source, identified by content
  hash. Replacing a transcript creates a new revision; it never overwrites the
  old identity.
- **Raw corpus snapshot:** The complete set of active raw revisions selected
  for one published or candidate knowledge state.
- **Source anchor:** A revision-specific pointer to a precise raw span that a
  derived record claims supports it.
- **Derived record:** A non-authoritative structured result of assimilation.
- **Derived orientation artifact:** A bounded, remotely searchable rendering of
  one or more derived records. It is not a source document.
- **Candidate snapshot:** An immutable, un-published raw/derived snapshot owned
  by one compilation run.
- **Published snapshot:** The one validated raw/derived snapshot pair used for
  normal Mentor requests.
- **Stale:** A derived record or retrieval artifact outside the currently
  published snapshot, or dependent on a revision no longer selected by it.

## 6. Knowledge Library and raw-source model

### 6.1 Generic hierarchy

```text
Knowledge Library
  Collection
    Source
      Source Revision
  Corpus Snapshot
    selected Source Revisions
    raw retrieval artifact
    derived retrieval artifact
```

The library stores, at minimum:

- collection ID, display name, domain, enabled state, and scope;
- source ID, collection ID, source type, author, course/book, lesson title,
  year/version, original filename, and local provenance metadata;
- immutable revision ID, source ID, content SHA-256, byte size, local path or
  archive locator, observed/imported time, and lifecycle state;
- remote OpenAI File and vector-store attachment identifiers where applicable;
- snapshot ID, selected revision fingerprint, compiler/model/prompt/schema
  versions, status, timestamps, and active/published pointer.

Filename, lesson name, and year are useful metadata, not identity. The current
`sources` registry is migrated into the generic hierarchy while preserving its
known raw OpenAI file/vector-store linkage and the original transcript files.

### 6.2 Active versus archival raw retrieval

Normal raw File Search receives **only** the raw vector store named by the
published snapshot's `active_raw_store_id`. It contains exactly one selected
revision for each active logical source. It is the sole raw search scope for
Direct teaching, exact-source questions, and citation repair.

When a correction/replacement/removal is discovered, it is recorded as a
pending candidate change. The prior published snapshot remains internally
consistent while the new candidate is built and clearly appears as
“replacement pending” in the inspector. It is not silently treated as already
updated. Once the candidate passes and the pointer swaps, the old raw store is
archival only and can no longer be passed to normal File Search.

This is intentionally stricter than placing old and new revisions in one store
and hoping prompts or filenames distinguish them. OpenAI documents that a file
can be detached from a vector store without deleting the underlying File, which
supports retention without active retrieval. [Delete vector store file](https://developers.openai.com/api/reference/python/resources/vector_stores/subresources/files/methods/delete)

Historical raw revisions remain locally inspectable through their revision and
anchor identity. Remote archival retention is an explicit cost/retention policy,
never an active-search fallback. Historical chat displays retain their original
citations and diagnostics; they do not make an archived revision current.

## 7. Durable source anchors

An anchor is immutable and revision-specific. It contains:

- `anchor_id`, `collection_id`, `source_id`, and `revision_id`;
- the complete source content SHA-256;
- normalized transcript character start/end offsets;
- timestamp start/end in integer milliseconds when the transcript supports
  timestamps, otherwise a null timestamp with the character range retained;
- SHA-256 of the normalized supporting span;
- a locator version describing normalization and timestamp parsing;
- denormalized display metadata: author, course/domain, year, lesson title,
  original filename; and
- optional minimal excerpt for inspection, never as the source of truth.

An anchor resolves only if the selected revision exists, its full hash matches,
the range is in bounds, parsing reaches the same timestamp/range, and the
normalized raw span has the recorded fingerprint. This detects transcript drift
even where filenames, lesson titles, or timestamps are duplicated.

## 8. Derived-record model and provenance

Every derived record has a stable record ID, candidate/published snapshot ID,
record schema version, one typed semantic family, related concept identifiers,
supporting anchor IDs, dependencies, validation/evidence state, lifecycle state,
and compiler provenance (model, prompt version, run ID, timestamps).

The record is not an unconstrained `content` blob. Its payload is exactly one
of the small typed families below. A family may contain bounded canonical text
for a statement or label, but not a long AI-written essay. Shared fields are
explicit and machine-checkable; optional extensions use typed facets (for
example an alias, example, exception, invalidation, or strategy implication)
with a facet type, compact value, related record IDs, and anchor IDs. A new
facet does not require a new table for every concept, but it must still conform
to the record schema and validation rules.

### 8.1 Typed semantic families

**Claim** represents one bounded proposition or teaching candidate:

- canonical statement;
- subject/concept identity;
- scope and context;
- conditions or `applies_when` facets;
- qualifiers/modality and polarity;
- evidence state and derived kind;
- temporal/year/course scope;
- supporting anchor IDs; and
- validation state and concise qualification.

**Relationship** represents an explicit connection:

- source concept/record ID;
- relationship type;
- target concept/record ID;
- conditions, exceptions, and qualifiers;
- supporting anchor IDs; and
- derived kind and evidence state.

**Procedure/sequence/hierarchy** represents ordered structure rather than
prose:

- ordered step/item IDs or compact typed items;
- optional prerequisites;
- branches and conditions where required;
- scope; and
- supporting anchor IDs.

**Evolution** represents a comparison across source sets:

- earlier scope/source-set ID;
- later scope/source-set ID;
- evolution classification;
- evidence state;
- supporting and competing anchor IDs; and
- concise qualification.

**Conflict/unresolved** represents uncertainty that must remain visible:

- competing claim/record IDs;
- relevant scopes and conditions;
- reconciliation state;
- evidence basis through anchor IDs and dependency IDs; and
- unresolved questions where applicable.

These families are generic and support aliases, examples, exceptions,
invalidations, and strategy implications through typed facets and relationships.
They do not encode Jacob-specific concepts. A strategy implication is
`source_extracted_claim` only when the active raw source explicitly teaches the
implication. If it is reasoned from Jacob material, it is
`cross_source_synthesis` and must remain labelled as Source synthesis/inference.
It must never become an extracted fact merely because it sounds reasonable.

Its `derived_kind` is exactly one of:

| Kind | Meaning | Final-answer treatment |
|---|---|---|
| `source_extracted_claim` | The compiler believes specified raw passages affirmatively support the claim. | Still derived orientation. Never automatically Direct source teaching. |
| `cross_source_synthesis` | A model interpretation or reconciliation across anchored teachings. | Source synthesis/inference only. |
| `unresolved_or_conflicting` | Evidence is incomplete, contradictory, or not cleanly reconcilable. | Must remain qualified; never silently flattened. |

A record may express concepts, aliases, relationships, sequences, procedures,
conditions, filters, prerequisites, exceptions, invalidations, examples,
hierarchies, or strategy implications. The relation vocabulary is generic (for
example `depends_on`, `applies_when`, `exception_to`, `refines`,
`contrasts_with`, `anticipates`, and `uses_internal_structure`), extensible by
schema version, and not tailored to any named Jacob concept.

### 8.2 Product provenance remains separate

Assimilation provenance is not final-answer provenance:

1. **Direct source teaching** is available only after query-time verification in
   active raw material and native raw citation(s).
2. **Source synthesis/inference** may use a compiled synthesis record, but the
   answer must identify it as synthesis and expose its raw anchor basis.
3. **AI research hypothesis** may use orientation as background but remains an
   AI hypothesis.
4. **User empirical evidence** and **User decision** remain future categories;
   Phase 3 does not create either.

The Inspector labels a source-extracted record as “derived; independently
validated against raw anchors,” never as Direct source teaching.

## 9. Compiler and validation pipeline

The compiler never submits the entire corpus to one prompt. It operates on an
explicit source-revision set and produces immutable candidate outputs:

```text
selected raw revisions
  -> deterministic parsing and anchor candidates
  -> per-source Sol extraction
  -> deterministic anchor validation
  -> independent Sol semantic claim validation
  -> concept clustering and relationship/evolution synthesis
  -> conflict and absence analysis
  -> dependency graph construction
  -> candidate raw/derived retrieval artifacts
  -> coverage, structural, and semantic gates
  -> atomic publication or failed candidate
```

### 9.1 Source-level extraction

Extraction operates on one source revision at a time and produces small,
structured candidate records with proposed support anchors. It is allowed to
find zero records. It must not infer a claim merely because a term occurs.
Source-level output is limited to claims that can be expressed with precise
anchors, plus explicitly uncertain candidates for later reconciliation.

### 9.2 Independent validation

The extraction pass cannot approve its own claim.

**Deterministic validation** confirms source/revision identity, source hash,
range bounds, parsed timestamp validity, raw-span fingerprint, and that the
supporting raw text can be reloaded.

**Semantic validation** is a separate GPT-5.6 Sol pass with independent prompt
and context. It receives the proposed claim and the actual raw support spans,
not the extractor's rationale as authority. It returns one of: affirmatively
supported, partially supported, unsupported, ambiguous, or needs broader
context. A source-extracted claim can publish only when deterministic validation
passes and semantic validation is affirmatively supported. Partial, ambiguous,
or unsupported items are excluded or recast as unresolved; they cannot be
silently promoted.

Human/sample anchor auditing is an additional gate, not a substitute for this
independent validation.

### 9.3 Cross-source synthesis

Clustering starts from validated derived records and anchored raw context. It
creates concepts and relations only when their inputs are explicit. A synthesis
record stores its input record IDs, all transitive raw anchor IDs needed for
inspection, a concise **auditable justification**, and qualification level. The
justification is a short evidence-based explanation such as “These teachings
apply to different cycle contexts, so they are compatible.” It may state that
teachings are related or appear to differ; it cannot relabel that result as raw
Direct teaching.

Persistent derived records must never store or expose hidden chain-of-thought,
model scratchpads, long private reasoning traces, encrypted reasoning converted
into prose, or internal deliberation logs. They store only the conclusion,
concise auditable justification, raw anchor basis, qualification, and the
reconciliation/evolution outcome. Numeric model confidence is not a schema
field; use the defined evidence/qualification vocabulary instead.

## 10. Evolution, absence, and conflict discipline

### 10.1 Year/evolution model

An evolution record compares explicitly selected, coverage-described source
sets. Its classification is one of:

- `introduced`, `repeated`, `refined`, `expanded`, `reframed`;
- `deprecated_or_deemphasized` only where source evidence directly supports it;
- `apparently_contradictory`;
- `uncertain_chronology`; or
- `no_supported_classification`.

Each classification includes its supporting anchors, observed-year coverage,
   competing evidence, and evidence state/qualification. Later material is not assumed
to be new, better, or a replacement for earlier material.

### 10.2 Negative-claim vocabulary

The compiler and Mentor use these distinct states:

| State | Permitted meaning |
|---|---|
| `positive_teaching` | Identified raw passages affirmatively teach the item. |
| `not_found_in_observed_evidence` | The defined compilation/search scope did not find it; this is not proof of absence. |
| `source_asserted_absence` | A source expressly states the absence/previous non-teaching. |
| `coverage_supported_synthesis` | Broad, recorded coverage suggests a difference; it remains synthesis. |
| `unresolved` | Evidence cannot justify the stronger conclusion. |

Terms such as “never taught,” “first taught,” “new,” “removed,” or “deprecated”
must meet the evidence strength implied by the wording. Exhaustive answers must
retain the Phase 2 complementary-search discipline and qualify completeness when
the evidence does not support it.

### 10.3 Conflict representation

Conflict records retain each competing claim/synthesis, its anchors, scope,
conditions, relevant year/context, and reconciliation outcome: compatible under
different conditions, unresolved, or genuinely contradictory. Publication does
not require every tension to disappear; it requires that uncertainty not be
hidden.

## 11. Dependencies, invalidation, and selective recompilation

The local database records a directed acyclic dependency graph:

```text
source revision -> extracted record -> synthesis/relationship -> higher synthesis
```

Each edge includes dependency type and the consuming snapshot/run. A record may
depend on many anchors/records, but a record cannot depend on itself or a later
record in the same candidate graph. Cycle detection is a publication failure.

When source bytes change, are removed, or are corrected:

1. A new/removed revision state is recorded and a candidate raw snapshot is
   formed; the published pair remains unchanged until publication.
2. The reverse-reachable dependency closure is computed from the affected raw
   revision(s).
3. Only affected source extraction, validation, synthesis, relationships,
   evolution, conflict analysis, and orientation artifacts are rebuilt.
4. Every affected candidate predecessor is unavailable for candidate retrieval;
   no stale record may be inserted into the candidate derived store.
5. The candidate receives the ordinary validation gates before it can replace
   the published pair.

After publication, previous derived records are archived/stale and excluded
from normal orientation retrieval. The old snapshot remains provenance history,
not an eligible current answer input.

## 12. Snapshots and atomic publication

### 12.1 Candidate immutability

A compilation run owns one immutable candidate snapshot containing:

- the complete selected raw revision fingerprint;
- raw candidate vector-store ID;
- derived candidate vector-store ID;
- all candidate derived records and dependency edges;
- compiler/model/prompt/schema versions;
- coverage, validation, and evaluation results; and
- an explicit status: building, validating, failed, published, or archived.

No normal request uses a building, failed, stale, or archived candidate.

### 12.2 Publication transaction

Publication changes one local `current_snapshot_id` in the same transaction as
the active raw-store ID and active derived-store ID. A Mentor request resolves
that pointer once at request start, so it uses one consistent raw/derived pair.
The swap occurs only after every required gate passes. If a run fails, the prior
published snapshot remains active and the failed candidate remains auditable.

### 12.3 Remote-store strategy

The selected design is **one raw candidate store and one derived candidate store
per snapshot**, followed by local pointer swap. It is stronger than mixing old
and new documents in a shared store with filters: no partially rebuilt or
superseded artifact is addressable by the normal current-store identifiers.

OpenAI supports attaching a File to a vector store, polling attachment status,
and attaching files in batches. It also exposes file attributes and filtered
vector-store search. [Create vector-store file](https://developers.openai.com/api/reference/java/resources/vector_stores/subresources/files/methods/create) [File batches](https://developers.openai.com/api/reference/cli/resources/vector_stores/subresources/file_batches) [Search vector store](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/search)

The implementation must prove against the installed SDK/current API whether one
existing OpenAI File can be attached to both the current and candidate stores.
If not, it uploads a byte-identical candidate File, records its hash, and still
uses the same pointer-swap design. This is a storage/cost fallback, not a change
to source identity or provenance.

Old remote stores are never used for normal retrieval after the swap. Their
retention/deletion is an explicit later cleanup policy after the local archive
and historical-display requirements have been checked; Phase 3 never deletes a
raw OpenAI File merely to reclaim space.

## 13. Derived orientation retrieval

### 13.1 Storage boundary

Local structured derived records are the inspectable system of record. The
separate derived vector store contains only compact, bounded orientation
artifacts derived from current published records. Each carries minimal searchable
attributes such as collection ID, snapshot ID, record ID, derived kind, status,
year/scope, and schema version, within the platform's attribute limits.

OpenAI Vector Store Search returns matching content, source file identity,
attributes, and score, and supports attribute filtering with one to fifty
results. [Search vector store](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/search)

No derived artifact is placed in the authoritative Jacob raw File Search store.
No custom embedding model, vector database, ranking algorithm, or custom RAG
answer engine is added.

### 13.2 Server-owned orientation function

The Mentor receives one server-defined function, conceptually
`consult_assimilated_knowledge`. It accepts a bounded topic/question and
optional active collection/year scope. The server resolves the published
derived-store pointer, applies fixed current-snapshot filters, performs native
Vector Store Search, validates returned record IDs/status locally, and returns
a bounded orientation object:

- derived record IDs and kinds;
- concise relationship/evolution/uncertainty cues;
- source-anchor IDs and source areas to verify; and
- snapshot/version identity.

Orientation is deliberately bounded. The server must deduplicate results by
record ID and concept identity, enforce a strict per-turn record and token
budget, return concise typed records rather than raw transcript dumps, and omit
the wholesale concept graph. The exact numeric budget is selected and measured
during implementation planning/evaluation; the design requires the budget and
deduplication behavior, not a prematurely fixed number. Raw source text belongs
in raw verification, never in the orientation payload.

It does not return a raw-source citation, does not fabricate a file citation,
and does not present itself as a Jacob transcript. The Responses API supports
built-in tools and application-defined function calls; the function boundary is
what keeps this derived retrieval separate from native raw File Search.
[Responses create](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)

Function output is request-scoped, bounded, and excluded from historical replay
payloads. The application persists only compact audit metadata sufficient to
explain which derived records/snapshot oriented a turn. Later turns consult the
then-current published snapshot again. This preserves Phase 2's opaque native
compaction semantics and avoids reintroducing historical search-result payload
replay.

## 14. Query-time Mentor policy

1. Resolve one published snapshot pair at the start of the turn.
2. When a valid published derived snapshot exists, broad, comparative,
   evolutionary, multi-concept, relationship-heavy, and exhaustive questions
   **must normally consult assimilated orientation before broad raw-source
   research**, unless the model has a concrete reason the derived layer is
   irrelevant. This includes questions such as “teach me everything about X,”
   “compare X and Y,” “how do A, B, and C work together,” “what changed between
   years,” “what concepts affect X,” and “how does Jacob's system fit together?”
3. Narrow direct-source questions do not require orientation. Definitions,
   exact source locations, and timestamp requests may go directly to raw search.
4. For substantive factual claims, Direct source teaching, corrections, source
   comparisons, exhaustive assertions, or exact-source/timestamp requests, the
   model must verify against the active raw store using native File Search.
5. The model produces the answer with Phase 2 provenance labels and native raw
   citations where Direct source teaching is claimed.
6. The UI keeps raw cited sources, raw retrieved passages, and derived
   orientation/audit information visibly distinct.

Specific rules:

- A `source_extracted_claim` is an orientation hint, never sufficient by itself
  for Direct source teaching.
- An exact source or timestamp request bypasses any reliance on derived output:
  raw verification is authoritative, and the cited raw evidence must support
  the reported source/timestamp.
- A source synthesis may use compiled records to connect teachings, but must be
  labelled synthesis and remain inspectable through anchors.
- An AI hypothesis may use compiled knowledge as background but is still an AI
  hypothesis.
- If raw evidence conflicts with a compiled record, the raw source controls the
  answer; the record is flagged for correction/staleness, not defended.
- If a required raw verification fails, the Mentor says the source does not
  establish the claim rather than citing a derived artifact.
- Orientation is not forced into trivial questions solely to increase calls.
- The Phase 3 evaluation records whether broad questions actually invoked
  orientation and which records informed them; a Phase 3 answer that merely
  reproduces Phase 2 raw-search behavior does not demonstrate assimilation.

The current research-depth controls continue to govern future turns only.
After orientation, exhaustive requests still require complementary raw-search
passes, substantive claims still require raw verification and native raw
citations, and raw disagreement overrides compiled knowledge. Derived
orientation can suggest omissions but cannot prove completeness.

## 15. Assimilation Inspector

Phase 3 adds a small read-only inspector, not an administration dashboard. It
shows:

- Jacob collection and published snapshot status, revision fingerprint, pending
  source changes, compiler versions, and coverage/failure counts;
- concept/record content with derived kind and validation status;
- source anchors with source/revision identity, file/lesson/year, and readable
  timestamp/range where available;
- relationships, year/evolution classifications, conflicts, uncertainty, and
  dependency/stale status; and
- the snapshot and orientation-record IDs recorded for a Mentor turn.

The Inspector must never render a derived record as an original source or turn
it into a raw citation. It should link an anchor to the local raw-source display
when available. Editable source controls, uploads, collection toggles, and
library management are deferred.

## 16. Security, privacy, and retention

- The app remains loopback-only and single-user. No authentication or public
  deployment is added.
- Raw transcripts, compiled records, source anchors, audit outputs, API keys,
  local SQLite runtime data, and private evaluation transcripts remain ignored
  by Git unless an explicit future policy says otherwise.
- Assimilation is a paid OpenAI operation and sends derived records plus the
  raw material needed for extraction/validation to OpenAI, consistent with the
  existing raw File Search architecture. It must require configured credentials
  and an explicit user action.
- Do not log secrets or full private corpus/evaluation content in application
  logs or committed documentation.
- Remote deletion is never implied by local source removal. It is a separate,
  explicit retention action with a verified target and local provenance
  preservation.

## 17. API, model, and cost policy

The project currently uses Python 3.12+, `openai>=2,<3`, Responses API native
File Search, `store=False`, and native context compaction. Phase 3 retains that
foundation.

- Use GPT-5.6 Sol for extraction, independent semantic validation,
  reconciliation, and paid semantic evaluation. Terra remains a future routing
  recommendation only.
- Use direct Responses calls for final Mentor answers so native raw citations
  and artifacts remain available. OpenAI advises direct tool calling when final
  output must preserve citations or other native artifacts. [Latest-model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- Use native vector-store search only for derived orientation; server code does
  no custom vector ranking.
- Record compilation input/output/reasoning tokens, model, elapsed time,
  vector-store/file-search operations, estimated cost, failures, and snapshot
  identifiers. Cost reduction is not success if it harms semantic quality.
- Candidate raw/derived stores temporarily increase remote storage. The Inspector
  must surface this and publication cleanup must be explicit; quality and safe
  atomicity take precedence over minimum spend.

## 18. Migration and Phase 2 compatibility

The Phase 3 migration is additive and reversible until successful publication:

- Preserve existing threads, display turns, diagnostics, replay items, source
  IDs, current raw vector-store association, and historical citations.
- Backfill generic collection/source/revision rows from the existing Jacob
  registry, calculate content hashes, and flag any missing/unreadable source
  rather than guessing a revision.
- Do not alter the Phase 2 local permanent thread-deletion semantics. Derived
  knowledge is shared collection state, never thread-owned state.
- Historical responses retain the model/reasoning/research settings and raw
  citation/evidence presentation that produced them. They do not acquire new
  Phase 3 orientation claims retroactively.
- Continue to store native compaction items for replay as Phase 2 requires; do
  not store/replay raw File Search or derived-orientation result payloads.
- Preserve stream failure recovery and display partial/incomplete results
  honestly.

## 19. Project conventions and future verification commands

This design does not run or add commands. The later implementation must retain
the existing project conventions:

| Purpose | Command |
|---|---|
| Full deterministic suite | `python -m pytest` |
| Focused test file | `python -m pytest tests/<test_file>.py -q` |
| Local private server | existing documented project server command; do not introduce a framework/build tool solely for Phase 3 |

Source belongs under `src/mentor/`; deterministic tests under `tests/`; durable
design and acceptance documents under `docs/`; local corpus/runtime/import and
paid semantic-evaluation artifacts remain untracked. Implementation should use
the existing small Python modules, dataclasses, SQLite transaction patterns, and
pytest fixtures rather than introduce a graph database, ORM, worker framework,
or new frontend stack.

## 20. Evaluation and acceptance

### 20.1 Deterministic structural tests

Use synthetic fixtures, not private raw transcript text, to prove:

- stable library/source/revision IDs and SHA-256 revision detection;
- no silent source disappearance and visible import/compilation failures;
- anchor range/hash/timestamp validation and rejection on drift;
- derived-kind/provenance enforcement;
- independent-validator failure blocks extracted-claim publication;
- absence/evolution classifications cannot claim stronger evidence than their
  state allows;
- dependency-DAG cycle rejection, transitive stale propagation, and selective
  rebuild closure;
- candidate immutability, failed-candidate isolation, and atomic raw/derived
  pointer swap;
- active retrieval excludes archived/superseded raw revisions and all stale
  derived artifacts;
- orientation result is bounded, labelled derived, filtered to the current
  snapshot, and omitted from replay payloads;
- Phase 2 citation repair, compaction, streaming, conversation persistence,
  loopback binding, and deletion behavior do not regress.

### 20.2 Paid semantic evaluation

Run against the real Jacob corpus with results kept local/private:

1. **Coverage:** every intended source/revision is processed or has a visible
   failure; the published fingerprint is reproducible.
2. **Anchor audit:** sample derived extracted claims across the corpus; a human
   or independent evaluator confirms each raw span actually supports the claim.
3. **Synthesis discipline:** test direct claim versus synthesis, conditions,
   exceptions, conflicts, uncertainty, evolution, and negative-claim wording.
4. **Gold set:** maintain representative questions across broad Jacob topics,
   including but not limited to correlations/SMT, TPD, reversion, narrative,
   cycles/timing, filters, synchronization, gaps, weekly frameworks, entries,
   management, and obscure lessons.
5. **Baseline comparison:** run the accepted Phase 2 Mentor and the Phase 3
   Mentor on the same representative set. Record correctness, completeness,
   source discipline, conceptual connection, year distinctions, correction
   behavior, orientation calls/record IDs, raw-search calls/passages, token use,
   latency, and estimated cost.
6. **Adversarial set:** include fake Jacob claims, incompatible passages,
   refined-year cases, obscure details, exhaustive requests, exact timestamps,
   and prompts mixing direct teaching with AI inference.

The key qualitative comparison is: *does Phase 3 feel more like the Mentor
already studied the course, without weakening factual/source discipline?*

### 20.3 Human gate

Theo alone decides Phase 3 pass/fail after live testing. Automated and paid
semantic results are evidence, not a substitute for this gate.

## 21. Success criteria

Phase 3 passes only when:

- Jacob raw sources remain the factual/citation authority.
- All intended Jacob material is represented in a fully auditable published
  corpus snapshot or visibly reported as failed/excluded.
- Every published derived record is inspectable, versioned, source-linked, and
  correctly classified as derived.
- Source-extracted records never bypass final raw verification for Direct source
  teaching.
- Source changes generate a safe candidate, transitive invalidation/rebuild
  closure, and never leave old/new revisions together in active search.
- A failed candidate cannot corrupt or partially replace the active pair.
- Broad questions use compiled orientation to reduce rediscovery while retaining
  or improving quality, correction behavior, and raw source discipline.
- Evolution, uncertainty, conflicts, and absence claims are honest about their
  evidence strength.
- Direct-source claims keep native raw citations and exact-source/timestamp
  behavior remains source-verifiable.
- The design can add a later collection without redesigning the library.
- Phase 2 behavior does not regress.

## 22. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Compiled claim overstates its passage | Independent deterministic and semantic validation; raw verification remains mandatory for Direct teaching. |
| Old and new teaching appear together | Immutable active raw snapshot store and pointer swap; archive is never normal search scope. |
| Candidate partially leaks into chat | Requests resolve one published pair; candidate IDs are not exposed to normal tools. |
| Derived layer becomes a second authority | Separate store/function/UI treatment; no derived native citations; raw controls conflicts. |
| Negative claim becomes “never taught” | Explicit negative-evidence states and wording rules. |
| One source correction forces full Sol rebuild | Reverse dependency closure and selective re-extraction/synthesis. |
| Candidate-store cost | Explicit on-demand runs, metrics, cleanup policy, and human decision before destructive remote cleanup. |
| Long-thread/context regression | Request-scoped bounded orientation, no historical payload replay, preserve native compaction. |
| Private corpus leaks into repository | Keep corpus, runtime DB, paid eval outputs, and secrets ignored; commit only source/docs/tests with synthetic fixtures. |

## 23. ADR candidates and implementation-time questions

1. **ADR: immutable raw and derived vector-store pairs.** Adopted by this
   design for atomic active retrieval. Revisit only with an equally safe,
   officially verified alternative.
2. **ADR: derived orientation is a server-owned function, not a second native
   File Search tool.** Adopted to prevent derived files from receiving raw-like
   citation treatment.
3. **ADR: source-extracted remains derived.** Adopted; no compiled record can
   itself establish Direct source teaching.
4. **Implementation verification required:** prove the installed SDK/API permits
   reattaching the same OpenAI File to current and candidate stores. The
   documented fallback is a byte-identical uploaded candidate file with the
   same recorded source hash.
5. **Implementation verification required:** measure candidate-store storage and
   attachment behavior for the 150-source Jacob corpus before selecting archive
   retention duration. This affects cost, not authority or publication safety.
6. **Deferred product decision:** editable collection/source controls await a
   later approved phase with a second collection or upload workflow.

## 24. Boundaries

- **Always:** preserve raw authority, require raw native citations for
  substantive Direct teaching, keep derived records inspectable/versioned,
  validate independently, run the full deterministic suite before commits, and
  leave private corpus/runtime data untracked.
- **Ask first:** change the raw/derived snapshot architecture, add a dependency
  or external service, delete remote raw files/stores, alter data retention,
  introduce automatic model routing, or expand source scope beyond Jacob.
- **Never:** mix derived artifacts into authoritative raw search; represent a
  compiled item as a raw citation; hard-code Jacob teachings; reintroduce custom
  RAG/embeddings as the answer engine; begin Phase 4 or later work in Phase 3.

## 25. Approval checkpoint

This document is the proposed Phase 3 specification. On Theo's approval, the
next permitted deliverable is an implementation plan for these approved module
IDs. No implementation work, task breakdown, or Phase 4 work is authorized by
this document alone.
