# Phase 3 Gate 1 — Six-Source Pilot Evaluation

Status: completed; **REVISE before full-corpus assimilation**.  The six-source candidate remains published only in its isolated pilot runtime.  Production pointers and the normal Mentor runtime were not changed.

## Candidate and safety checks

- Six approved source revisions were compiled into 82 derived records: 66 independently affirmed source-extracted claims and 16 cross-source synthesis records.
- Record families: 28 claims, 13 relationships, 38 procedures/sequences/hierarchies, and 3 evolution records. No conflict/unresolved record was manufactured.
- The original raw and derived pilot stores were reused for the retry. They were retrievable, completed, bounded by expiry, and contained respectively 6 and 82 completed files.
- The full deterministic suite passed: **453 passed**.

## Orientation collection-boundary correction

The original comparison was not valid. A model-produced optional `collection_id` reached a server-owned orientation boundary; the retained diagnostic proves only that it was rejected as invalid, not the exact value, so no value is asserted here.

The correction removes collection/year identifiers from the model tool contract. The server now resolves the canonical collection identity from the request's already-resolved immutable snapshot. Raw and derived store selection therefore remain bound to that same snapshot. Evaluation telemetry fails closed: required orientation must be requested, successfully retrieved, admitted to context, and followed by raw verification.

The original cases were classified as follows:

| Case | Original status |
| --- | --- |
| Broad integration | Invalid: broad orientation was not requested by the prior heuristic. |
| Procedure | No orientation required; Phase 3 response was incomplete. |
| Conditions | No orientation required. |
| Cross-year comparison | Invalid: orientation returned no admitted record. |
| Relationships | Invalid: orientation failed validation and raw fallback masked it. |
| False attribution | Invalid: orientation returned no admitted record. |

## Valid evaluation-only retry

All evaluation work reused the existing pilot candidate and stores. It did not extract, validate, synthesize, publish, recreate stores, or alter production.

| Case | Orientation validity | Phase 2 → Phase 3 raw searches | Phase 2 → Phase 3 citations | Note |
| --- | --- | ---: | ---: | --- |
| Broad integration | Valid orientation used | 2 → 2 | 3 → 3 | Phase 3 was materially shorter and better structured. |
| Procedure | Valid orientation not required | 4 → 3 | 3 → 3 | Narrow source-grounded procedure remained raw-first. |
| Conditions | Valid orientation not required | 1 → 1 | 2 → 4 | Raw verification remained authoritative. |
| Cross-year comparison | Valid orientation used | 4 → 4 | 2 → 3 | Both answers were incomplete, so quality comparison is only partial. |
| Relationships | Valid orientation used | 3 → 2 | 3 → 4 | Phase 3 connected the concepts with fewer raw searches. |
| False attribution | Valid orientation used | 2 → 1 | 2 → 3 | Phase 3 rejected the false universal attribution while retaining conditions. |

Across all six cases, Phase 2 used 16 raw searches, 15 citations, 13,339 input tokens and 16,397 output tokens. Phase 3 used 13 raw searches, 20 citations, 28,788 input tokens and 15,879 output tokens. Every orientation-required Phase 3 case recorded successful retrieval, admitted context, and subsequent raw verification.

## Cost

- Successful six-source compilation model cost: $1.977725.
- Valid evaluation-only retry: $1.251415.
- Bounded pilot storage exposure: $0.000034080.
- Clean successful six-source experiment estimate: **$3.229174080** (excluding prior debugging/R&D).
- Cumulative Gate 1 R&D exposure after the retry: **$21.873589080**; $8.126410920 remains under the $30 ceiling.

An initial full-active-corpus forecast, based on per-source extraction/validation scaling plus increasing cross-source reconciliation rather than multiplying total pilot spend, is approximately:

| Scenario | Estimated compilation and evaluation cost |
| --- | ---: |
| Optimistic | $45–$55 |
| Expected | $60–$85 |
| Conservative | $100–$130 |

Runtime broad-question orientation added input context in this pilot; the measured cost/context trade-off must be controlled before scale.

## Decision

**REVISE.** Assimilation demonstrably supplied prior, bounded orientation and preserved raw-source verification. It improved organization and reduced raw-search work in some broad cases, but the margin over the accepted Phase 2 raw-search Mentor is not yet large enough to justify scaling by default. Before full-corpus authorization, improve orientation record selection/budgeting and resolve the cross-year output-limit behavior, then repeat a bounded comparison.

No Phase 3 full-corpus work or Phase 4 work is authorized by this record.
