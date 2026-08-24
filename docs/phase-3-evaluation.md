# Phase 3 evaluation

## Status and boundary

Task 18 provides a deterministic, synthetic evaluation harness and the isolated
pilot runtime. No Jacob transcript is read, no OpenAI request is made, and no
paid pilot is run by this task.

## Deterministic gates

The full test suite is the Phase 3 structural regression gate. Its focused
coverage is:

| Invariant | Deterministic coverage |
|---|---|
| library migration, source revisions, visible failures | `test_knowledge.py`, `test_source_registry.py`, `test_import_jacob.py` |
| anchors and raw authority | `test_anchors.py` |
| typed records, provenance, independent validation | `test_derived_records.py`, `test_compiler.py`, `test_validation.py` |
| synthesis, aliases, concept identity, evolution, absence/conflict | `test_synthesis.py` |
| dependency DAG, invalidation, atomic publication | `test_dependencies.py`, `test_compilation.py` |
| candidate orchestration and failure isolation | `test_candidate_compiler.py` |
| bounded orientation and raw citation boundary | `test_orientation.py`, `test_chat_service.py` |
| replay, citations, Inspector, loopback server, Phase 2 behavior | `test_chat_service.py`, `test_server.py`, `test_storage.py` |
| evaluation aggregation and pilot isolation | `test_evaluation.py`, `test_phase3_regression.py` |

The synthetic evaluation fixture covers source authority, provenance, coverage,
anchor precision, evolution, conflict, orientation, baseline comparison,
exhaustive research, exact timestamps, and adversarial correction. It contains
no private corpus text.

## Evaluation metric contract

Each baseline or assimilated case records separate categorical states for
correctness, completeness, source discipline, conceptual connections,
evolution, and correction behavior, plus:

- native citation count;
- orientation calls and deduplicated admitted record IDs/count;
- raw File Search calls and retrieved passage count;
- input/output/reasoning tokens where reported;
- latency; and
- estimated cost.

Live GPT-5.6 Sol compiler stages require caller-supplied token pricing. The
recorded cost is therefore reproducible from persisted token counts and the
explicit rates rather than silently defaulting to zero.

## Pre-Gate 1 final review closure

The final deterministic review wave remains inside Tasks 1-19 and does not run
the pilot. It proves that manifest revision IDs resolve through a production
seam into byte/hash-verified bounded timestamp anchors; extraction,
independent validation, and reconciliation handle typed claims,
relationships, and procedure/sequence/hierarchy records with aliases; and
reconciliation uses bounded primary clusters plus bounded cross-cluster bridge
passes.

Candidate concepts are persisted with canonical labels, aliases, scopes,
supporting records/anchors, term occurrences, every record association, and an
explicit semantic primary. Source replacement tests prove that an unchanged
source is cloned into the next candidate without re-extraction, a remote-ready
replacement remains pending while the candidate is reviewed, and only
publication promotes it while superseding its predecessor. Derived provenance
kind and semantic subtype remain separate in storage, orientation, and the
Inspector. Compact-width CSS contracts keep controls, messages, and the
composer within the viewport while retaining the intentional horizontal thread
strip.

All of this coverage is synthetic/local. It performs no OpenAI request, vector
mutation, paid compilation, or production-runtime migration.

## Pre-Gate 1 remediation round 2

The second deterministic review closure remains inside Tasks 1-19. The strict
source-extraction schema now uses explicit closed unions whose object fields are
all required (nullable where absence is meaningful). A local check through the
installed OpenAI Python SDK 2.54.0 strict-schema conversion leaves the schema
unchanged, so the request shape is tested against the SDK behavior used by this
workspace without making a network request.

Typed extraction, validation, persistence, and reconciliation now carry a
generic expanded relationship vocabulary plus explicit procedure prerequisites,
conditions, and branches. Extracted aliases are sent through the independent
raw-span semantic validator and are retained only after affirmative validation;
unvalidated extractor aliases cannot enter a candidate concept.

Reconciliation no longer has a 64-record whole-candidate bottleneck. It builds
deterministic semantic/alias affinity components, bounded primary batches,
affinity bridges for related concepts split across batches, and global boundary
bridges that connect the complete batch graph. Each call remains bounded, the
candidate and call-plan safety ceilings remain explicit, every input record is
accounted for, and no whole corpus is placed in one prompt.

Validated concept labels, aliases, scopes, support counts, and occurrence
summaries are included in bounded derived orientation artifacts and in the
transient Mentor orientation tool output. The read-only Inspector renders the
same safe summaries and human-verifiable anchor locations while hiding concept,
record, revision, snapshot, hash, and dependency identifiers from its visible
presentation. Raw transcript text and native citation authority remain outside
the derived orientation layer.

Candidate input now rejects two revisions for one logical source before file or
model work. Reuse is permitted only when the published snapshot's compiler
model, prompt, and schema versions exactly match the new run. Live Sol pricing
for extraction, validation, and reconciliation is preflighted before source
preparation, candidate reservation, or the first paid stage call.

The final browser smoke also found and fixed a compact diagnostics grid that
could expand the chat beyond the viewport. At desktop width the 248px left
sidebar and centered 760px composer remain intact. At 390px the chat,
diagnostics, controls, composer, and Inspector remain within the viewport; the
Inspector concept summaries are readable, static assets return HTTP 200, and
the isolated browser reports no console errors or failed requests.

This round used only synthetic fixtures, temporary SQLite runtimes, the local
SDK, and an isolated loopback browser. It did not read the Jacob corpus, modify
the real migrated runtime, call OpenAI, mutate a vector store, or run Gate 1.

A failed case is retained as a failure type while its exception message and
private output are discarded. Baseline comparison requires the same ordered
case IDs/categories on both sides.

## Pilot isolation contract

`PilotRuntime.create` uses SQLite's backup API to make a transactionally
consistent copy under ignored `data/pilots/<run-id>/`. The copied database is
marked `pilot` and its inherited current/raw/derived pointers are cleared in
one local transaction; outputs and traces stay inside the same per-run
directory. SQLite connections are explicitly closed on every path. Failed copy
setup removes only the new per-run directory.

Only a pilot-scoped candidate may publish through the pilot runtime. Normal
production storage rejects pilot-scoped publication and excludes pilot-scoped
current pointers from resolution. Candidate compilation rejects a mismatched
runtime/artifact scope before reserving local state or making model/vector
calls. A pilot server also rejects a chat service bound to a different
database/runtime scope.

The harness has no copy-back or automatic remote-cleanup operation. Pilot
database rows, outputs, and traces remain local to the ignored run directory;
remote cleanup is an explicit later action.

## Gate 1 manifest boundary

The Gate 1 manifest is a private, ignored artifact containing exactly six
**real active `SourceRevision` IDs**, each tagged with one or more structural
roles. The deterministic `PilotManifest` contract rejects any manifest that
does not have six unique revisions or lacks coverage for foundation,
procedure, 2025/2026 comparison, exception/condition, synthesis/evolution,
and conflict/uncertainty material.

The manifest must be selected from the migrated `SourceRevision` inventory;
lesson filenames or legacy upload IDs are not substitutes. On 2026-08-24, Theo
authorized the local-only immutable revision migration needed for Task 19. The
migration was proven on an ignored runtime copy before being applied to the
normal local database: all 150 legacy registrations remained intact, all 150
mapped to one active immutable revision with preserved remote linkage, no
exceptions or duplicates were found, and a second run was idempotent. Phase 2
conversation, replay, display, citation/evidence, diagnostic, and settings row
counts and content fingerprints remained unchanged. A socket-level guard
recorded zero network attempts and the OpenAI SDK was not imported.

The resulting six-revision Gate 1 manifest validated successfully and remains
private under the ignored `data/pilots/` tree. It contains no transcript bodies
and has not been compiled. The pre-migration backup and disposable verification
runtime are also ignored local artifacts.

## Measured-cost protocol

After Theo explicitly authorizes Gate 1, run the six-source candidate only in
the isolated pilot runtime. Keep the following per-stage and total data in
ignored pilot artifacts, never in Git:

- model, compiler prompt, and schema versions;
- selected revision IDs and candidate/source processing outcomes;
- extraction, validation, reconciliation, vector-store, and audit call counts;
- input, output, and reasoning-token usage where the API supplies it;
- wall-clock latency per call and per stage;
- provider-reported or reproducibly calculated cost by model/stage;
- raw and derived remote-store/file counts plus cleanup status;
- candidate record counts by family, anchor-validation/audit results, and
  baseline-versus-assimilated evaluation metrics.

Do not guess a full-corpus price before the pilot. Derive its local forecast
from the measured fixed setup cost plus the observed per-source/per-stage cost,
call, token, and latency distribution; record the active source count and the
explicit extrapolation assumptions. Keep uncertainty as a range or scenario
table rather than inventing a single precise number. The pilot result still
requires Theo's separate review before any full-corpus assimilation.

## Candidate readiness correction

Candidate readiness distinguishes a rejected extracted record from a failed
candidate. Only an affirmatively supported semantic-validation outcome creates
an active `source_extracted_claim`; partial, ambiguous, unsupported, and
needs-broader-context outcomes remain in the audit and are excluded by default.
They do not create artificial unresolved records or fail a candidate merely by
existing. Extraction or validation errors, coverage/anchor/dependency/lineage
failures, an empty validated candidate, remote setup failures, and later
synthesis/publication failures remain candidate-level blockers.

## Gate 1 synthesis structured-output hardening

The first fresh Gate 1 candidate reached reconciliation after 71 extracted
candidates and 65 affirmatively supported source-extracted records, but stopped
at the response boundary before any candidate store, publication, orientation,
or Mentor evaluation. The response was intentionally non-persistent
(`store=False`), so its exact private malformed JSON cannot be reconstructed;
the retained parser failure established that a returned record was not a typed
object with a string family.

The prior reconciliation response contract used a loose `array<object>` schema
with non-strict output despite the local parser requiring four closed typed
families. The corrected, versioned contract is strict and contains only
relationship, procedure/sequence/hierarchy, evolution, and conflict/unresolved
objects. Every object has explicit required fields, `additionalProperties:
false`, bounded payloads, explicit lineage fields, and the exact concept-hint
shape; nullable scope, role, and position remain explicit properties.

The installed OpenAI SDK strict-schema conversion leaves the extraction,
semantic-validation, and synthesis schemas unchanged. Deterministic tests cover
all four synthesis families through the local parser, closed family and hint
shapes, lineage fields, and the synthetic candidate compiler path. The failed
candidate remains incompatible with the new synthesis provenance and is never
reused or published. Cumulative prior Gate 1 spend is recorded as `$3.607125`
against the fixed `$25.00` ceiling before the second extraction attempt.

The second extraction-only attempt added `$1.135595`, bringing cumulative Gate
1 spend to `$4.742720` against that unchanged `$25.00` ceiling. It did not
create vector stores, publish a candidate, or change production pointers.

## Gate 1 inline extraction-occurrence hardening

A later fresh pilot failed before synthesis when the former extraction
`concept_hints` selector did not resolve to one typed record occurrence. The
source response was intentionally non-persistent (`store=False`), so its exact
private role/position cannot be reconstructed; the failure demonstrates the
inherent gap between a model-authored cross-reference and the parsed sibling
record that it must identify.

Extraction schema v5 removes that indirection. Claim subject/object,
relationship left/right, and each procedure term, prerequisite, and branch step
now carry a small closed inline occurrence object: typed text, aliases, and
optional scope. The compiler derives the canonical occurrence, family role, and
ordered position after parsing the same typed record. There is no model-authored
selector, role, position, concept ID, or free-form label to dangle.

Aliases remain model-proposed and are still independently validated against raw
support before entering candidate concept clustering. The parser now requires
the exact fields supplied by the closed extraction schema and validates the
typed record before accepting its inline occurrences. Failed v4 candidates are
immutable incompatible history, not migration input. The new compiler prompt
and schema versions are `source-extraction-v5` and
`source-extraction-schema-v5`.

## Gate 1 Responses envelope hardening

The next isolated pilot reached six extraction calls and 57 extracted
candidates, then stopped before synthesis because one returned response was
handed directly to JSON parsing. Its raw non-persistent response was not kept,
so the existing artifact cannot establish whether it was incomplete, refused,
or a completed non-JSON structured response.

Compiler stages now share a strict Responses envelope boundary. It rejects
failed, incomplete (including output-limit), refusal, missing-content, and
unexpected-output states before JSON or typed-domain parsing. A completed,
non-refusal payload that cannot decode as JSON remains a distinct contract
failure; it is never repaired or silently retried. Extraction, validation, and
synthesis retain their independent strict schema and domain parsers afterward.

Gate 1 writes ignored, pilot-only response-envelope diagnostics for every
paid attempt: stage/call/model/version metadata, status, error or incomplete
details, item types, refusal, usage, response ID, and structured payload. It
does not retain reasoning content or enable server-side storage. Cumulative
spend after the stopped run is `$6.163620` of the fixed `$25.00` ceiling.

A one-call synthetic envelope probe then confirmed the installed SDK returned a
completed message with one output-text item and a parseable strict JSON object.
It used no corpus, files, vector stores, or server-side response storage and
cost `$0.000815`, bringing cumulative Gate 1 spend to `$6.164435`.

The subsequent captured six-source replay attempt brought cumulative Gate 1
spend to `$8.502370`. The runner ledger must retain that actual cumulative
amount; it must not reset to the earlier envelope-probe total.

## Final six-source Gate 1 rerun

Theo authorized a fixed cumulative Gate 1 ceiling of `$30.00`; the prior
`$8.502370` remains permanent ledger spend. On 2026-08-24, the unchanged
approved six-source manifest passed local preflight with a conservative
remaining-run upper bound of `$18.548850` and current standard GPT-5.6 Sol
pricing of `$5.00` input / `$30.00` output per million tokens.

The fresh isolated candidate stopped at readiness after five completed source
extractions, one capped extraction response, and 57 independent validation
calls. The capped response was correctly classified as
`response_incomplete_max_output_tokens`. The strict candidate gate marked that
source failed, retained the diagnostic privately, and prevented concept
construction, synthesis, vector-store creation, publication, orientation, and
Mentor evaluation. Production pointers remained unchanged.

The retained partial audit contains 57 extraction candidates: 47
affirmatively supported source-extracted records, nine partially supported
candidates, and one unsupported candidate. The candidate has 17 claims, 27
procedures/sequences/hierarchies, and three relationships; it has no published
concepts, aliases, synthesis, evolution, conflict, orientation artifacts, or
remote stores. Actual incremental spend was `$1.477240`, for cumulative Gate
1 spend of `$9.979610` and `$20.020390` remaining under the approved ceiling.

This was not a downstream structural failure: the completed-response replay
cannot exercise a newly generated model response that terminates at its output
limit. The immutable failed candidate is not reused or patched, and no retry
was performed.
