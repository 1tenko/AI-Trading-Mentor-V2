# Phase 3B — Assimilation Value Proof

Status: completed; **ABANDON full-corpus assimilation**. This is a product
decision, not a compiler or provenance failure. The six-source semantic
candidate remains private and isolated; production remains on the accepted
raw-source Mentor path.

## Controlled comparison

The experiment reused the existing immutable six-source pilot snapshot and its
raw and derived stores. It created no source revisions, candidate snapshots,
vector stores, or production pointers.

Each of six research-supervisor prompts used GPT-5.6 Sol, high reasoning,
normal research depth, the same scoped raw File Search corpus, and the same
concise-answer request. The only intended difference was the derived
orientation available to Phase 3B:

- research-question generation;
- hypothesis formation;
- operational definition;
- experiment design;
- confound/overfitting critique; and
- cross-year/cross-concept research.

The raw-only baseline was retained for the one hypothesis case that initially
missed orientation due to an intent-plumbing gap. A deterministic fix made
falsifiable-hypothesis/backtest/experiment prompts eligible for orientation;
the assimilated half of that one case was then rerun. It used two admitted
derived records and one raw verification call. No other result was rerun.

## Orientation context diagnosis and lean correction

The valid Gate 1 retry admitted only two or three orientation records, but
recorded 3,387–3,895 orientation budget units per oriented turn. The dominant
cause was not excessive record count: the model-facing function payload
included local record/concept identities, source anchors, dependency lineage,
revision identifiers, aliases, occurrence details, and support counts; it also
required a separate Responses tool-selection round-trip.

The Phase 3B deterministic correction:

- resolves orientation directly on the server against the turn's immutable
  published snapshot;
- keeps native raw File Search as the only model tool and citation authority;
- passes only a compact non-authoritative navigation map to the final request;
- keeps exact per-conclusion anchor/dependency/revision lineage in the local
  record/audit/Inspector path; and
- bounds model-facing orientation to four records and 1,400 payload units.

The six Phase 3B orientation calls each admitted two records, for 12 records
total and 8,158 recorded payload units (about 1,360 per call). All were bounded
before the 1,400-unit ceiling; no raw transcript text was inserted.

## Results

| Metric | Raw-search Sol | Lean orientation + raw-search Sol |
| --- | ---: | ---: |
| Cases completed | 6 | 6 |
| Orientation searches / records | 0 / 0 | 6 / 12 |
| Raw File Search calls | 7 | 9 |
| Retrieved raw passages | 56 | 72 |
| Native citations | 14 | 15 |
| Input tokens | 13,567 | 15,732 |
| Output tokens | 9,672 | 14,699 |
| Reasoning tokens | 5,923 | 10,667 |
| Latency | 164,589 ms | 268,000 ms |
| Estimated model + known raw File Search cost | $0.330022 | $0.561578 |

The lean design materially reduced the *orientation payload* versus Gate 1 and
removed the extra model tool-selection request. It did not produce a material
end-to-end efficiency win: the assimilated variant still searched more,
produced longer answers, and cost/latency more.

Qualitatively, orientation sometimes improved framing: it surfaced a more
integrated catch-up/synchronization moderator, added useful regime distinctions
to the overfitting critique, and kept the year comparison cautious. But the
raw-search baseline already produced strong falsifiable hypotheses, operational
definitions, experiment designs, provenance separation, and correction
discipline. The operational-definition baseline was in places more specific.
The improvement is real but marginal, not the clear research-supervisor step
change required to justify a full Jacob compilation program.

## Output-limit diagnosis

The prior Gate 1 cross-year responses both reached their output limit—raw-only
and assimilated alike. That rules out orientation context as the primary cause.
The bounded Phase 3B prompts completed without an output-limit response. The
evidence points to the former comparison's answer/reasoning demand and configured
response cap, rather than a need to raise the global output budget.

## Cost

- Phase 3B six-case paired evaluation: $0.964150000.
- Orientation-enabled hypothesis rerun: $0.069790000.
- Phase 3B paid evaluation total: **$1.033940000**.
- Cumulative Gate 1/Phase 3B effective exposure: **$22.907529080**.
- Remaining under the unchanged $30 ceiling: **$7.092470920**.

The accounting includes measured model usage and known native File Search
charges. The direct derived-store searches did not report a separate per-query
provider charge to this ledger.

## Decision

**ABANDON.** Do not assimilate the full Jacob corpus. GPT-5.6 Sol plus the
accepted scoped raw File Search Mentor is already a capable research supervisor,
and the marginal value of this orientation layer does not justify the expected
full-corpus compilation cost, maintenance, context overhead, and added
complexity.

Retain the accepted Phase 2 raw-source/citation/provenance architecture. The
generic source-revision and anchor work may still be useful if a future approved
source-library feature needs it, but the compiler, derived stores, orientation
runtime, and full-corpus assimilation should not be expanded on this result.

Deterministic verification after the final bounded intent fix: **453 passed**.
