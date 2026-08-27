# Phase 5 Design: Backtest / Empirical Data Analysis Foundation

**Status:** Proposed for implementation planning approval
**Date:** 2026-08-27
**Builds on:** accepted Phase 4, commit `50a7cd1cf2658f4b51baf10fa5f7c5d69907ef25`
**Branch:** `feature/phase-5-backtest-analysis`

## Objective and strict boundary

Phase 5 lets Theo import a local CSV or XLSX backtest and ask the Mentor for
analysis without asking a model to calculate trading statistics. The product
flow is:

```text
local file -> deterministic import and validation -> typed local analysis
-> bounded reproducible result -> GPT-5.6 Sol interpretation -> Theo
```

This is reusable data-analysis capability, not a scientific strategy project.
Phase 5 does **not** create strategy versions, hypothesis/experiment histories,
adopted rules, project-scoped source scopes, web research, Google Sheets, a
spreadsheet editor, automatic market-regime classification, arbitrary Python or
SQL execution, or a source-assimilation system.

Phase 4 passing means the Mentor understands Theo's trader context. It does not
mean the final scientific strategy-development workflow already exists; that
work belongs to Phases 6 and 7.

## Principles

1. **Deterministic facts first.** Python calculates counts, rates,
   distributions, filters, groups, and comparisons. Sol explains, critiques,
   and proposes a next question; it is never the arithmetic authority.
2. **Local-first and immutable.** Original uploads never leave this machine,
   are never sent to OpenAI, and are never modified. A changed upload is a new
   dataset, not a mutation of past analysis.
3. **Schema-flexible, semantics-explicit.** Any table can be inspected. A
   capability exists only when a visible user-confirmed mapping makes it valid.
4. **Evidence is not causation.** A measured association is
   `USER_EMPIRICAL_EVIDENCE`; it is not direct source teaching, an AI
   hypothesis, or proof that a filter causes better performance.
5. **Bounded context.** Large rows, raw files, and unbounded group output stay
   local. Sol receives deliberately bounded metadata and results.
6. **No hidden scope.** The active dataset is visible and thread-local. The
   app never silently analyzes the last uploaded file.

## Capability map

The existing local app already has SQLite storage, a static browser UI,
server-owned Responses function calls, persisted chat history, and strict
raw-Jacob File Search. Phase 5 extends those patterns without changing source
authority or Phase 4 profile semantics.

| Module id | Responsibility | Depends on |
|---|---|---|
| dataset-foundation | Immutable dataset metadata, local file lifecycle, safe import preflight | — |
| dataset-schema | Preview, type inspection, visible semantic mapping and validation | dataset-foundation |
| deterministic-analysis | Typed calculation operations and reproducible result envelope | dataset-schema |
| empirical-mentor | Server-owned analysis functions, provenance and bounded Sol context | deterministic-analysis |
| data-workspace | Data upload/preview/mapping UI and visible thread-local active scope | dataset-schema, empirical-mentor |
| phase5-evaluation | Deterministic, privacy, chat, and browser acceptance coverage | all above |

Build order: `dataset-foundation -> dataset-schema -> deterministic-analysis ->
empirical-mentor -> data-workspace -> phase5-evaluation`.

## Data-engine decision

| Option | Decision | Reason |
|---|---|---|
| Python stdlib plus hand-written CSV/XLSX and metrics | Reject | XLSX handling, type inference, grouping and statistical edge cases would recreate mature tools. |
| **pandas + openpyxl, SQLite metadata, immutable local files** | **Adopt** | Smallest reliable stack for CSV/XLSX inspection, DataFrame filtering/grouping and deterministic retail-scale metrics. |
| DuckDB plus pandas | Defer | Strong future option for much larger or cross-dataset/project queries, but adds a second durable analytical database and SQL surface before either is needed. |
| Polars | Defer | Capable, but adds a competing DataFrame model with no demonstrated Phase 5 advantage. |

Implementation will add only `pandas` and `openpyxl` as direct runtime
dependencies after checking their then-current supported Python 3.12 releases.
No data engine, database service, embeddings, or hosted storage is introduced.

The choice uses the documented pandas Excel import and GroupBy APIs; DuckDB was
evaluated rather than assumed. See [pandas `read_excel`](https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.read_excel.html), [pandas GroupBy](https://pandas.pydata.org/pandas-docs/stable/user_guide/groupby.html), and [DuckDB Python ingestion](https://duckdb.org/docs/stable/clients/python/data_ingestion.html).

## Dataset domain model and lifecycle

SQLite stores metadata only. Private originals live in an ignored local path
such as `data/datasets/<dataset-id>/original.<extension>`.

| Record | Key fields | Purpose |
|---|---|---|
| `Dataset` | local ID, display filename, SHA-256, imported-at, extension, byte size, source row count, status | Stable identity for one immutable upload. |
| `DatasetImportSpec` | dataset ID, selected sheet/header row, CSV encoding/delimiter/quoting, parser versions, original row ordering and time parse policy | Immutable source locator and parse contract. |
| `DatasetColumn` | dataset ID, ordinal, original header, inferred type, null/invalid counts | Preserves the inspectable raw tabular shape. |
| `DatasetMappingVersion` | dataset ID, version, `draft`/`confirmed`, created-at, confirmed-at | An immutable complete mapping snapshot once confirmed. |
| `DatasetMappingEntry` | mapping-version ID, original column, optional semantic role/unit, optional user-approved analysis label, mapping source | One entry of the parent snapshot; a role maps to at most one column. |
| `AnalysisEvidence` | local ID, thread ID, origin turn, display turn, dataset ID/hash, import spec ID, mapping version, operation/schema version, arguments, bounded result, created-at | Reconstructs an empirical result and its historic evidence without creating a Phase 6 experiment record. |
| `ThreadDatasetScope` | thread ID, dataset ID or null, selected-at | Makes a chat's active dataset explicit and local. |

The original is read into an in-memory DataFrame during import and later when an
analysis runs. A process-local cache keyed by file hash and mapping version is
allowed but disposable. Phase 5 deliberately does not persist another raw-row
copy or a derived Parquet/DuckDB store. A later phase may add one only after
measured need.

```text
select local CSV/XLSX
  -> preflight size/type/encoding/workbook safety
  -> copy original immutably + compute SHA-256
  -> inspect headers/types/row counts + bounded preview
  -> offer deterministic header-alias suggestions
  -> Theo confirms or edits semantic roles
  -> enable only analyses supported by that mapping
  -> select dataset for this chat
  -> deterministic analysis + retained reproducibility envelope
```

Initial guards are CSV/XLSX only, one selected worksheet, at most 50 MiB,
250,000 data rows, 100 columns, and 2,000,000 cells after parsing. XLSX must
pass ZIP/OOXML signature and archive preflight; CSV extension selects the
candidate parser, which must then pass strict decoding, dialect, and tabular
parse validation. XLSX archive member count and compressed/uncompressed sizes
are capped before parsing;
macro-enabled files, external workbook links, and formula cells are rejected so
the app never executes or trusts a cached formula result. Theo can export static
values first. CSV decoding failures are reported; they never receive a plausible
replacement-character conversion. Failed imports atomically clean up temporary
files and leave no usable partial dataset. Duplicate complete rows are reported,
not automatically removed, because they can be genuine trades.

Source row ordinal is preserved. Streak, cumulative return, drawdown, and
recovery use that import order unless a confirmed timestamp ordering is
explicitly selected and recorded in the analysis arguments. Date parsing never
guesses ambiguous day/month order; such cells are invalid until an explicit
import policy resolves them. Datetimes retain their supplied timezone state;
chronological grouping either has compatible known timezone data or reports the
limitation rather than silently converting it.

## Import, validation, and semantic mapping

The importer records strings, numeric values, percentages, dates/datetimes,
booleans, categories, blanks, and invalid cells. It preserves raw values for
local inspection. No malformed value silently becomes a plausible trade result.

Every analysis reports source rows, rows satisfying filters, valid rows used,
excluded rows, and per-role exclusion reasons. Each operation declares the
roles it requires.

Semantic mapping is optional and compact. V1 roles are `trade_return` with a
user-confirmed unit (`R`, currency, points, or percentage), `trade_outcome`,
`trade_timestamp`, `session`, `direction`, `mfe` and `mae` with their declared
units, `instrument`, and `setup`. Any original header remains usable as a
generic categorical/numeric filter or group without a semantic role.

Simple local aliases may suggest that `Result`, `R`, `PnL_R`, or `Return in R`
could be a return column and suggest `R` as its unit. A suggestion is never
canonical: Theo can inspect, edit, clear, and explicitly confirm the role and
unit. For a generic column used through chat, Theo must similarly confirm a
short analysis-safe label. The model receives that label and an opaque field ID,
not the raw header; the server resolves the ID locally. The confirmed mapping
version is the analysis authority and is shown with results.

`trade_outcome` uses a documented controlled vocabulary (`win`, `loss`,
`breakeven`) with visible normalisation. When absent but a numeric
`trade_return` is valid, outcome may be deterministically derived as
positive/negative/zero and labelled *derived*. Without either, win rate is
unavailable.

## Deterministic analysis contract

Sol receives only server-owned function tools. There is no arbitrary Python,
SQL, filter expression, or file-access tool. The service validates each call
against the thread's active dataset, allowed columns, and typed JSON-schema
arguments; it calculates locally and returns a bounded structured result.

| Tool | Required capability | Result |
|---|---|---|
| `inspect_dataset` | active dataset | schema, mapping state, row-health summary and available analyses; no raw cell preview |
| `summarize_results` | numeric return and/or outcome | overall sample, outcome, return, streak, equity and distribution metrics available |
| `group_results` | one/two existing columns | standard metrics per group, each with N |
| `compare_groups` | column plus two disjoint values | side-by-side metrics, deltas, N and limitations |
| `analyze_mfe_mae` | MFE and/or MAE role | valid N and unit-labelled location/distribution metrics |
| `analyze_over_time` | valid timestamp role | chronological buckets, halves or a declared rolling window |

Every operation accepts typed filters whose `field_id` is an approved semantic
role or user-approved analysis field, never an arbitrary raw header:

```text
column + operator + typed value(s)
operators: eq, neq, in, not_in, is_blank, not_blank, gt, gte, lt, lte, between
```

At most two grouping columns, bounded filters, and 50 returned groups are
allowed; omitted groups are explicit. There is no standalone free-form
`filter_results` tool: the validated filter model is an argument to each
relevant operation.

### Tool orchestration safety

The current Phase 4 dispatcher handles only one profile mutation. Task 8 must
replace that narrow internal boundary with one bounded generic local-function
dispatcher, not bolt analysis calls onto the profile continuation. It supports
the existing profile function and the six approved analysis functions, preserves
native raw File Search, and preserves citation-repair behavior.

- A response may make one **analysis batch** of at most three parallel approved
  analysis calls, followed by exactly one terminal continuation. A single shared
  8,000-character serialized-result budget applies across the entire batch
  before persistence, replay, or model continuation; deterministic per-result
  truncation records every omission. It cannot enter an arbitrary local-tool
  loop or make sequential extra analysis calls.
- A profile mutation retains its existing single-call idempotence and terminal
  continuation contract. A response that mixes a profile mutation with analysis
  calls is rejected as `mixed_local_tool_batch_not_supported`; the terminal
  answer may explain that the actions need separate turns.
- Every call is independently schema/scope validated; unsupported names,
  duplicate call IDs, malformed arguments, wrong active dataset, and over-cap
  batches receive structured local rejections.
- The continuation retains the permitted File Search configuration and all
  approved custom tools needed for a faithful terminal response. It does not
  re-execute a successful mutation or analysis after a citation repair/retry.
- Bounded `AnalysisEvidence` is linked to the originating user turn and, once
  rendered, its display turn. That link lets a reopened thread show the original
  empirical basis and configuration faithfully rather than applying current
  dataset selection/settings to old text.

Every result includes `USER_EMPIRICAL_EVIDENCE`, local dataset ID/hash, mapping
and analysis versions, operation, filters, grouping, source/filtered/valid/
excluded N, metric definitions, results, and limitations. It is reproducible
but is not a future project, hypothesis, or decision record.

This follows the existing Responses custom-function pattern. Current official
documentation describes custom functions as application-defined code with typed
arguments and outputs. See the [OpenAI function-calling guide](https://developers.openai.com/api/docs/guides/function-calling/) and [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

## Metrics and analysis semantics

| Area | Deterministic v1 outputs |
|---|---|
| Sample | source, filtered, valid and excluded N; explicit exclusion reasons |
| Outcome | wins, losses, breakeven, win/loss rates, maximum consecutive win/loss streaks |
| R / return | total, mean/expectancy, median, mean winning/losing R, realised reward:risk, best/worst R |
| Equity / risk | cumulative-R sequence, peak-to-trough maximum drawdown in R, drawdown indices and recovery observations when recovered |
| Distribution | min/max, mean, median, sample standard deviation, quartiles, IQR outlier count and quantiles |
| MFE / MAE | valid N, mean/median and distribution summaries when mapped |

Expectancy is the arithmetic mean of valid `trade_return` values and always
carries the confirmed native unit. R-specific wording, cumulative R, R
drawdown, and realised reward:risk are available only when that unit is R.
Realised reward:risk is `mean(winning R) / abs(mean(losing R))` and is
unavailable when there are no valid wins or losses. Metrics never substitute a
different P&L unit.

Grouping applies the same standard set to every valid group and always shows N.
Comparison returns both sides and defined deltas, never a causal conclusion.
Temporal analysis requires a valid mapped date/time and supports month buckets,
chronological halves, and user-declared fixed rolling windows. It preserves N
and dates; it creates no regime label, train/test decision, or strategy rule.
MFE/MAE cannot be calculated without mapped fields and never invents a unit.

## Statistical discipline and overfitting guardrails

Phase 5 reports effect and evidence quality before rhetoric:

- all groups/comparisons include N, valid N, exclusions, and applicable spread;
- `win_rate = wins / (wins + losses + breakevens)` and `loss_rate` use that same
  valid-outcome denominator; the result labels this convention explicitly;
- win-rate results include a deterministic Wilson 95% interval for `N >= 1`
  plus a prominent small-sample warning below 30 outcomes;
- sample standard deviation is unavailable below two valid values; quantiles use
  a documented linear interpolation rule, and IQR outlier count is unavailable
  below four values;
- R comparisons include N, mean/median differences, standard deviation and
  quantiles, rather than a p-value used as an edge detector;
- v1 has no bootstrap, significance test, causal claim, regime classifier, or
  automatic rule adoption.

If Theo says “my last five losses were in Q1,” the Mentor must request or use
all-Q1 versus non-Q1 comparison. Its interpretation must state this is an
in-sample association, show N, ask whether the condition was specified before
inspection, and identify out-of-sample confirmation as future work. It must not
turn a post-hoc observation into a filter.

## Provenance, Mentor behavior, and bounded context

Phase 5 introduces `USER_EMPIRICAL_EVIDENCE`: deterministic results from
Theo's local dataset. The taxonomy is:

1. Direct source teaching
2. Source synthesis / inference
3. AI research hypothesis
4. **User empirical evidence**
5. User decision

The Mentor labels empirical observations as user empirical evidence and keeps
them distinct from Jacob teaching, source synthesis, AI hypotheses, and future
user decisions. A source lookup is not needed to restate a local metric. If
Jacob methodology matters, native File Search remains the raw authority and its
claims retain Phase 2 citation rules.

Sol may call several local analyses in one Mentor turn but uses a normal
function-call continuation rather than a paid model request per calculation.
Existing stateless replay, compaction, failure recovery, citation integrity,
and profile boundaries remain unchanged.

| Context content | Limit / rule |
|---|---|
| Active dataset context | ~2,000 characters: opaque local dataset label, hash prefix, semantic roles, capability and row health; no raw filename, headers, or values by default |
| Analysis batch result | 8,000 characters shared across all calls after deterministic sanitizer/truncation; max 50 groups per call plus omission metadata |
| Representative raw rows | Never supplied by default. A future per-column explicit model-disclosure allowlist is required before any bounded value samples are supported. |
| Raw spreadsheet | Never sent to Sol, File Search, vector store, Git, logs, or external storage |
| Chat replay | Bounded sanitized evidence envelope/metadata only; no file contents, DataFrame, raw identifiers, or free-text cells |

The local UI may show the complete bounded preview because it remains loopback
only. The Mentor sees aggregates by default. No row samples reach the model in
Phase 5. A categorical/boolean mapping entry may separately and explicitly
check **Allow aggregate labels to Mentor** during confirmation; this is the
only Phase-5 path for a group label/value to leave the local analysis engine.
It is eligible only for at most 20 distinct labels with each label at most 80
characters; arbitrary long/free-text and high-cardinality columns are ineligible
and not model-groupable. The sanitizer still applies redaction, cardinality,
batch-length, persistence, and replay limits. Limits are measured with
deterministic fixtures. A result that cannot fit says what was omitted; it never
silently invents a conclusion.

## Dataset UI and scope

The static app gains a restrained **Data** area:

```text
Data list -> Upload CSV/XLSX -> Preview and row health
          -> Review/confirm mapping -> Select for this conversation
```

It is not a spreadsheet editor. Chat shows `Active dataset: <filename>` with
select/change/clear controls. The selection persists only on that local
conversation; a new thread starts with no dataset. Each analysis call receives
an explicit dataset ID and is rejected if it does not match the active scope.
This prevents hidden global state and leaves simple dataset identity for Phase
6 to associate with a project later.

### Ownership, deletion, and historic fidelity

`Dataset` is a shared local library object; `ThreadDatasetScope` and
`AnalysisEvidence` are thread-owned. Extending the existing permanent
conversation-deletion transaction must remove that thread's dataset scope,
evidence/result rows, retained analysis tool-output references, and any
turn-specific empirical metadata in the same transaction. It must never delete
a shared Dataset, original file, mapping version, or another thread's evidence.

Phase 5 intentionally offers **no Dataset delete control**. That avoids an
ambiguous conflict between a permanent data deletion and faithful historic chat
evidence. A future deletion design must decide its explicit redaction semantics
first; it may not silently orphan analyses or leave raw/evidence data behind.
When a normal thread is retained, a historic Mentor turn retains its bounded,
sanitized empirical evidence reference and the dataset/import/mapping/analysis
versions that produced it. The current active selection affects future calls
only.

## Evaluation and human gate

Deterministic coverage must prove import/hash/immutability/signature/archive/
formula preflight and atomic cleanup; preview and immutable confirmed mapping
snapshots; type/exclusion/duplicate/ambiguous-date behavior; exact metrics,
row-order streak/drawdown/distributions; groups/filters/comparisons; time/MFE/
MAE/intervals; strict bounded generic tool dispatch; thread deletion/foreign-key
cleanup; bounded disclosure sanitization/replay; privacy; and Phase 1–4
regressions. Model fixtures must prove tool use, no fabricated arithmetic,
provenance separation, and no unsupported MFE/MAE or causality claim.

Human examples are:

1. 200 `Result_R` trades: win rate and expectancy are deterministic first;
   currency/points/percentage datasets retain their own labelled unit.
2. `SMT=true` versus `SMT=false`: returns N/effect information, not causality.
3. Five recent Q1 losses: compares all Q1 to non-Q1 and flags post-hoc risk.
4. Session question: grouped deterministic results with N.
5. Time question: chronological evidence only when a valid date exists.
6. Missing MFE: says it cannot calculate average MFE.

Theo alone decides pass/fail after deterministic and browser checks. Passing
Phase 5 does not authorize Phase 6 or 7.

## Explicitly deferred future compatibility

Phase 6 may associate `Strategy Project -> Dataset -> Experiment -> Analysis
Result -> Empirical Conclusion`; Phase 5 implements only the dataset/result
identity needed for that relationship.

Phase 6/7 must introduce a distinct **External web research** provenance for
public web, accessible X/Twitter discussion, specific URLs, and explicitly
requested bounded same-site crawling. Such claims must remain distinct from
direct Jacob teaching, source synthesis, AI hypotheses, user empirical evidence
and user decisions. Phase 5 neither implements nor enables web/URL/X research.

## Commands and boundaries

After plan approval, implementation verifies with:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest tests\test_dataset*.py tests\test_chat_service.py
.\.venv\Scripts\python.exe -m mentor.server
```

Always: validate at file/tool boundaries, preserve provenance, test before
commit, and keep data local. Ask first: raising import limits, adding analytics
dependencies, changing retention, or changing meaningful metric definitions.
Never: commit private uploads/runtime data, send a raw dataset externally, give
Sol arbitrary execution, implement Phase 6+, or treat association as proof.

## Success criteria

Phase 5 succeeds only when Theo can inspect a local CSV/XLSX without a fixed
schema; mapping is visible, editable and confirmed; every statistic is
deterministic and reproducible from dataset hash/mapping version; N/exclusions
and unavailable inputs are clear; Sol interprets bounded results without
fabricated arithmetic or causal proof; provenance is distinct; raw uploads
remain local; scope is clear per thread; and Theo passes the human quality gate.

## Open implementation choices

- Pin exact pandas/openpyxl releases against current supported Python 3.12
  versions before adding them.
- Confirm the existing static-server upload constraints before finalizing exact
  endpoint shape and keep 50 MiB/250,000 rows unless fixtures prove a safe need
  to adjust.
- Apply the established Phase 4 visual pattern rather than redesigning chat.
