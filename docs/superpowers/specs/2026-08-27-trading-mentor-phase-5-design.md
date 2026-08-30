# Phase 5 Design: Backtest / Empirical Data Analysis Foundation

**Status:** Amended for Theo review; implementation paused at the architecture breaker
**Date:** 2026-08-27
**Amendment:** 2026-08-30 architecture-breaker amendment
**Builds on:** accepted Phase 4, commit `50a7cd1cf2658f4b51baf10fa5f7c5d69907ef25`
**Branch:** `feature/phase-5-backtest-analysis`

## Objective and strict boundary

Phase 5 lets Theo import a local CSV or XLSX backtest and ask the Mentor for
analysis without asking a model to calculate trading statistics. The product
flow is:

```text
local file -> deterministic import and validation -> typed local analysis
-> bounded reproducible numeric evidence
-> optional bounded, user-approved qualitative text disclosure
-> GPT-5.6 Sol interpretation -> Theo
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
5. **Bounded, explicit disclosure.** Raw files, unapproved cells, and
   unbounded group/text output stay local. Sol receives deliberately bounded
   numeric evidence and, only after an explicit per-field opt-in, bounded
   qualitative text evidence.
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
| qualitative-evidence | Explicit text-field disclosure policy and bounded local text retrieval | dataset-schema, deterministic-analysis |
| empirical-mentor | Server-owned analysis functions, provenance and bounded Sol context | deterministic-analysis, qualitative-evidence |
| data-workspace | Data upload/preview/mapping UI and visible thread-local active scope | dataset-schema, qualitative-evidence, empirical-mentor |
| phase5-evaluation | Deterministic, privacy, chat, and browser acceptance coverage | all above |

Build order: `dataset-foundation -> dataset-schema -> deterministic-analysis ->
qualitative-evidence -> empirical-mentor -> data-workspace ->
phase5-evaluation`.

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

### Text-field and row-disclosure policy (2026-08-30 amendment)

The mapping can identify any user-selected text column as a **qualitative
analysis field**. It is not a special `notes` role: its original header remains
local and Theo supplies a short safe display/analysis label. Every mapped field
has one visible immutable `Mentor access` choice in its mapping version:

| Choice | Meaning |
|---|---|
| **Aggregates only** (default) | The field can support the existing approved aggregate/group behavior where eligible; its individual cell values never leave the local process. For a text field, this means no text is available to Sol. |
| **Allow row values when analysing notes** | Only this approved text field, or separately approved structured context field, may be returned by the bounded qualitative-evidence tool for an explicit notes-analysis request. |

The local-only mapping UI shows Theo the original header needed to select a
column, plus its safe display label. The Mentor-facing context and every tool
payload contain only the safe label and opaque field ID, never the raw header.
A change to either label, type, or access choice creates a new confirmed
immutable mapping version. Revoking access therefore prevents future disclosure;
it does not rewrite historic Mentor answers. Unapproved text, unapproved
structured context, hidden workbook data, raw headers, paths, and filenames
never reach Sol.

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
| `read_text_evidence` | explicitly approved qualitative text field(s) | bounded, ordered user-supplied text plus only explicitly approved row context and completeness metadata |

Every operation accepts typed filters whose `field_id` is an approved semantic
role or user-approved analysis field, never an arbitrary raw header:

```text
column + operator + typed value(s)
operators: eq, neq, in, not_in, is_blank, not_blank, gt, gte, lt, lte, between
```

At most two grouping columns, bounded filters, and 50 returned groups are
allowed. There is no standalone free-form `filter_results` tool: the validated
filter model is an argument to each relevant operation.

### Authoritative grouped-evidence partition (2026-08-30 amendment)

Every grouped result owns exactly one normalized `GroupEvidencePartition`.
It is the only persisted/replayed/tool-exposed representation of group
population accounting; presentation totals and omission metadata are derived
from it and are never independently writable.

```text
filtered population
  = returned groups + omitted groups + ungrouped rows

each returned group
  = valid analysis rows + excluded analysis rows
```

`returned_groups` contains real group keys from the filtered population for
which the grouping key is valid. Each group has only its authoritative
`filtered_rows`, `valid_rows`, `excluded_rows`, metric payload, and explicit
limitations. A zero-valid group remains visible: for example, an Asia group
with 8 filtered rows, 0 valid rows, 8 excluded rows, unavailable metrics, and
`no_valid_rows` is a real returned group, not an omission.

`omitted` is one aggregate for real groups outside the bounded returned-group
limit: `group_count`, `filtered_rows`, `valid_rows`, and `excluded_rows`, plus
only safe contract-required metadata. It cannot stand for a returned group
whose metric rows were invalid. `ungrouped` is a separate aggregate for rows
whose grouping key itself cannot produce a valid group; it is never used for a
metric exclusion, an omitted group, or a filter exclusion.

The following exact equalities are required when the partition is produced,
before persistence, on replay, and before tool exposure:

```text
filtered_rows = sum(returned.filtered_rows) + omitted.filtered_rows + ungrouped.filtered_rows
valid_rows    = sum(returned.valid_rows)    + omitted.valid_rows    + ungrouped.valid_rows
excluded_rows = sum(returned.excluded_rows) + omitted.excluded_rows + ungrouped.excluded_rows

for every returned group:
  filtered_rows = valid_rows + excluded_rows

omitted.filtered_rows = omitted.valid_rows + omitted.excluded_rows
ungrouped.filtered_rows = ungrouped.valid_rows + ungrouped.excluded_rows
```

No zero-row synthetic population or overlap between returned, omitted, and
ungrouped populations is valid. Production must route every filtered source-row
identity into exactly one returned group, the omitted aggregate, or ungrouped;
returned group keys must be unique. Replay validates the serialized structural
equalities and uniqueness. A failed validation rejects the complete envelope
rather than repairing or guessing at persisted evidence.

### Qualitative text evidence contract (2026-08-30 amendment)

`read_text_evidence` is a future server-owned typed custom function, not a
filesystem/DataFrame capability for Sol. Its arguments are opaque approved text
field ID(s), the same canonical typed filters used by deterministic analysis,
optional separately approved structured-context field ID(s), and an approved
deterministic ordering request. The server validates active dataset and mapping
version, per-field permission, filter compatibility, row eligibility, ordering,
sanitization, and every bound before returning data.

Mapping permission is necessary but not sufficient for a disclosure. Each chat
submission has a server-validated, one-turn **Include approved notes in this
answer** consent signal, defaulting to false. The static UI sets it deliberately
for the current message; the model cannot set, broaden, or replay it. The text
tool rejects a request without this signal, even for an approved field, and the
server records only safe consent/mapping metadata. This makes it clear when a
turn may send approved note values to Sol and prevents an incidental model tool
call from becoming user consent.

The local engine never interprets prose. Phase 5 does not add local NLP,
embeddings, vector/text-index storage, topic modelling, sentiment analysis,
automatic text classification, or theme counting. It only filters, orders,
bounds, sanitizes, and returns approved text. Sol may interpret that bounded
material, but a theme or theme count remains model-coded qualitative content,
not deterministic empirical evidence.

Initial limits are deliberately separate from the numeric aggregate envelope:

| Evidence class | Initial bound per Mentor turn |
|---|---|
| Deterministic aggregate evidence | 8,000 sanitized characters across at most two deterministic analysis calls |
| Qualitative text evidence | one text call; at most 3 text fields, 3 context fields, 100 usable rows, 1,200 characters per cell, and 24,000 sanitized characters in total |

The text tool orders by a valid approved timestamp then stable source-row
ordinal, or by stable source-row ordinal when no valid time order is requested.
Its `matching_rows` population is the rows accepted by the canonical filters;
it does not require a valid return/MFE/MAE merely because a combined Mentor turn
also has numeric evidence. `usable_text_rows` is the matching population with
nonblank approved text. An approved structured context value that is invalid or
blank is absent for that item and reported as unavailable context; it does not
silently remove the approved text row.
It returns no path, filename, raw header, unapproved field, hidden cell, or
arbitrary row identity. Its envelope reports matching rows, rows with usable
text, rows returned, rows omitted, characters returned, deterministic order,
and whether any cell or row was truncated. When all usable text fits, all is
returned; otherwise the result is explicitly partial. The Mentor must call a
partial qualitative review partial, never exhaustive, and may suggest a narrow
filter or another bounded review.

The initial limits are to be exercised against privacy-safe fixtures of 50,
100, and 200 short notes plus long journal entries before Task 8. They bound
external disclosure and Sol input cost without making normal retail-journal
analysis useless; any future increase is a user-approved privacy/cost change.

### Tool orchestration safety

The current Phase 4 dispatcher handles only one profile mutation. Task 8 must
replace that narrow internal boundary with one bounded generic local-function
dispatcher, not bolt analysis calls onto the profile continuation. It supports
the existing profile function, deterministic analysis functions, and the one
approved qualitative-evidence function, preserves native raw File Search, and
preserves citation-repair behavior.

- A response may make one **analysis batch** of at most three approved calls,
  followed by exactly one terminal continuation: at most two deterministic
  aggregate calls and at most one `read_text_evidence` call. The separate
  numeric and qualitative budgets above apply before model continuation. It
  cannot enter an arbitrary local-tool loop or make sequential extra analysis
  calls.
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

Deterministic results include `USER_EMPIRICAL_EVIDENCE`, local dataset ID/hash,
mapping and analysis versions, operation, filters, grouping, source/filtered/
valid/excluded N, metric definitions, results, and limitations. Qualitative
results identify `USER_SUPPLIED_QUALITATIVE_DATA`, approved safe field labels,
filter/mapping identity, disclosure bounds, and completeness metadata, but do
not turn a model theme into a measured fact. Neither is a future project,
hypothesis, or decision record.

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
Theo's local dataset. The existing product taxonomy remains:

1. Direct source teaching
2. Source synthesis / inference
3. AI research hypothesis
4. **User empirical evidence**
5. User decision

The following Phase-5 evidence labels refine that taxonomy without changing
source authority:

| Label | Authority and permitted claim |
|---|---|
| `USER_EMPIRICAL_EVIDENCE` | Deterministic Python/pandas result from the validated frame; counts, metrics, filters, and comparisons. |
| `USER_SUPPLIED_QUALITATIVE_DATA` | Bounded raw text from an explicitly approved field; it is user-provided material, not a deterministic theme/category. |
| `AI_QUALITATIVE_INTERPRETATION` | Sol's qualified reading of disclosed notes, such as a pattern that appears repeatedly in the reviewed material. Never present it as a deterministic count or empirical fact. |
| `AI_RESEARCH_HYPOTHESIS` / `AI_RECOMMENDATION` | A proposed test or next analysis; neither is a measured conclusion or user decision. |

The Mentor labels deterministic observations as user empirical evidence and
keeps them distinct from disclosed notes, Jacob teaching, source synthesis,
AI qualitative interpretation, AI hypotheses, recommendations, and future user
decisions. A source lookup is not needed to restate a local metric or profile
of disclosed notes. If Jacob methodology matters, native File Search remains
the raw authority and its claims retain Phase 2 citation rules.

Sol may call several local analyses in one Mentor turn but uses a normal
function-call continuation rather than a paid model request per calculation.
Existing stateless replay, compaction, failure recovery, citation integrity,
and profile boundaries remain unchanged.

| Context content | Limit / rule |
|---|---|
| Active dataset context | ~2,000 characters: opaque local dataset label, hash prefix, semantic roles, capability and row health; no raw filename, headers, or values by default |
| Deterministic aggregate evidence | 8,000 characters across at most two calls; max 50 groups per call; normalized partition omission metadata is mandatory |
| Qualitative text evidence | One explicit approved-field call only; 100 usable rows, 1,200 characters/cell, 24,000 characters total, and mandatory completeness metadata |
| Representative raw rows | Never supplied by default. Only explicitly approved row values may appear in a qualitative-evidence call. |
| Raw spreadsheet and all non-disclosed content | Never sent to Sol, File Search, vector store, Git, logs, or external storage |
| Chat replay | Deterministic evidence plus safe qualitative-disclosure metadata only; no raw text payload, file contents, DataFrame, raw identifiers, or free-text cells |

The local UI may show the complete bounded preview because it remains loopback
only. The Mentor sees aggregates by default. A categorical/boolean mapping entry
may separately and explicitly check **Allow aggregate labels to Mentor** during
confirmation; it is eligible only for at most 20 distinct labels with each label
at most 80 characters. Separately, the visible `Mentor access` choice permits
bounded row values only through `read_text_evidence`. Arbitrary long/free-text
and high-cardinality columns remain ineligible for grouping. The sanitizer still
applies disclosure permission, redaction, cardinality, batch-length,
persistence, and replay limits. A result that cannot fit says what was omitted;
it never silently invents a conclusion.

Raw qualitative text uses **ephemeral disclosure semantics**. It is supplied to
Sol only for the terminal turn that explicitly requested it. `AnalysisEvidence`,
tool diagnostics, and stateless replay retain safe field/filter/budget/
completeness metadata, not raw excerpts or text payloads. The terminal Mentor
answer remains normal conversation content; reopening a thread must not
automatically retrieve notes again. Because the immutable original dataset is
local, a later explicit request can re-read currently approved text
deterministically under its current mapping version.

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

Mapping confirmation visibly shows each field's safe label, semantic/type use,
and `Mentor access` choice. A text field defaults to **Aggregates only** and
requires Theo to choose **Allow row values when analysing notes** before a
future notes-analysis request can disclose it. The same choice is required for
any structured row-context field. The UI makes clear that this permits bounded
values from that one field to be sent to the Mentor only when Theo asks for
notes analysis; it does not disclose the workbook or other columns.

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
MAE/intervals; normalized group-partition reconciliation and replay rejection;
strict bounded generic tool dispatch; explicit text permission/default denial;
text filters/order/truncation/completeness; ephemeral text replay metadata;
thread deletion/foreign-key cleanup; privacy; and Phase 1–4 regressions. Model
fixtures must prove tool use, no fabricated arithmetic, provenance separation,
no unsupported MFE/MAE or causality claim, and that AI thematic interpretation
is not rendered as deterministic empirical evidence.

Human examples are:

1. 200 `Result_R` trades: win rate and expectancy are deterministic first;
   currency/points/percentage datasets retain their own labelled unit.
2. `SMT=true` versus `SMT=false`: returns N/effect information, not causality.
3. Five recent Q1 losses: compares all Q1 to non-Q1 and flags post-hoc risk.
4. Session question: grouped deterministic results with N.
5. Time question: chronological evidence only when a valid date exists.
6. Missing MFE: says it cannot calculate average MFE.
7. `Date | Session | Setup | Result_R | SMT | Notes`: a combined request shows
   deterministic evidence, disclosed-note completeness, AI qualitative
   interpretation, and an explicitly labelled next research question without
   treating a theme as a measured category.

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
fabricated arithmetic or causal proof; grouped results are a self-validating
partition; explicitly approved text can be read only through a bounded,
complete-or-explicitly-partial disclosure; deterministic metrics and AI thematic
interpretation remain distinct; raw uploads remain local; scope is clear per
thread; and Theo passes the human quality gate.

## Open implementation choices

- Pin exact pandas/openpyxl releases against current supported Python 3.12
  versions before adding them.
- Confirm the existing static-server upload constraints before finalizing exact
  endpoint shape and keep 50 MiB/250,000 rows unless fixtures prove a safe need
  to adjust.
- Apply the established Phase 4 visual pattern rather than redesigning chat.
