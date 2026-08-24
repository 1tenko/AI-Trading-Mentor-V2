# Phase 3 evaluation

## Status and boundary

Task 18 provides a deterministic, synthetic evaluation harness and the isolated
pilot runtime. No Jacob transcript is read, no OpenAI request is made, and no
paid pilot is run by this task. Task 19 will define the private six-source
manifest and measured-cost protocol separately.

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

Each baseline or assimilated case records explicit quality states for overall
quality, conceptual connections, evolution, and correction behavior, plus:

- native citation count;
- orientation calls and admitted record count;
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
marked `pilot`; outputs and traces stay inside the same per-run directory.

Only a pilot-scoped candidate may publish through the pilot runtime. Normal
production storage rejects pilot-scoped publication and excludes pilot-scoped
current pointers from resolution. A pilot server also rejects a chat service
bound to a different database/runtime scope.

The harness has no copy-back or automatic remote-cleanup operation. Pilot
database rows, outputs, and traces remain local to the ignored run directory;
remote cleanup is an explicit later action.
