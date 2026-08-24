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
- input/output tokens;
- latency; and
- estimated cost.

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
conversation, replay, display, citation/evidence, diagnostic, and settings data
remained byte-for-byte unchanged. A socket-level guard recorded zero network
attempts and the OpenAI SDK was not imported.

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
