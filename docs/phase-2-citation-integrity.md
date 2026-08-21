# Phase 2 Source-Citation Integrity Fix

**Status:** Technical fix complete. This record does not pass Phase 2 or begin
Phase 3; Task 9 remains Theo's human acceptance decision.

## Root cause and policy

The Mentor's provenance policy required an affirmative source claim before it
could use the `Direct source teaching` label, but did not require a native
`file_citation` annotation in the completed response. A File Search result was
therefore sometimes treated by the model as sufficient research even when its
answer carried no native citation.

The policy now requires native File Search citations for substantive Direct
source teaching and, where practical, materially source-based synthesis. It
does not relabel hypotheses or unsupported claims to create citations.

If a completed answer contains a substantive Direct source teaching label with
no native citation, the server makes exactly one citation-repair request. The
initial draft is not persisted as the successful turn. The repair receives the
existing native research output and may perform a focused File Search. If it
still has no citations, the returned answer displays an explicit warning rather
than silently implying verification.

For exact-source or timestamp questions, the same one-repair ceiling applies.
The repair forces one focused native File Search. A timestamp is retained only
when it falls within at least one returned transcript range from a cited source;
otherwise a visible source-verification warning replaces the unsupported
timestamp claim. The guard checks every range in a returned passage, not only
the first one.

OpenAI documents File Search results as separately included
`file_search_call.results`, while assistant `output_text` annotations contain
native `file_citation` file metadata. The app deliberately does not invent an
exact result-chunk mapping from these separate response fields. [Responses API
reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)

## Evidence UI semantics

The prior UI grouped every result sharing a cited `file_id` as though it were
the exact cited excerpt. That was false: the native citation identifies a file,
not a uniquely mapped included result chunk.

The disclosure now presents:

- **Cited sources** — one entry per native cited file;
- **Retrieved passages from this source** — returned original File Search
  passages grouped beneath that cited source, without calling them cited
  excerpts; and
- **Additional research results** — retrieved results from uncited files.

The summary counts cited sources separately from retrieved passages. Source
links remain available, timestamps remain human-readable, and no custom RAG,
ranking, embeddings, or application-generated citations were added.

## Verification record

Deterministic coverage includes normal cited teaching; zero-citation Direct
source repair; a repair that still has no citation; no unnecessary retry for an
AI hypothesis; streaming replacement of an uncited draft; table-form Direct
labels; supported/unsupported exact timestamps; and a supporting timestamp
range later in one retrieved passage.

The controlled paid High / Standard / GPT-5.6 Sol retest used an isolated copy
of the existing compacted thread. No private transcript content or runtime data
was committed.

| Prompt | Effective depth | Native citations | Retrieved passages | File Search calls | Result |
|---|---:|---:|---:|---:|---|
| 2025 vs 2026 SMT comparison | Deep | 7 | 80 | 4 | Direct-source claims cited; native compaction occurred. |
| Exact source for symmetrical timing | Normal | 2 | 16 | 2 | January 20th, 12:10.84 question and 12:16.52–12:36.04 answer, supported by returned cited-source evidence. |
| 2025 absence vs new probability filter | Exhaustive | 4 | 60 | 3 | Cited, qualified answer; native compaction occurred on the continuing thread. |

The observed text-token estimates were $0.181929, $0.444262, and $0.289615
respectively. These exclude vector storage and other platform charges. The nine
observed File Search calls add $0.0225 at the existing known per-call estimate.

The local server served `/`, `/app.css`, and `/app.js` successfully with the
expected content types, and JavaScript syntax/static UI checks passed. A local
Edge desktop-width smoke screenshot confirmed the left sidebar, controls,
conversation/table layout, and composer render correctly. The dedicated browser
automation CLI and desktop browser bridge were unavailable, so the cited-source
grouping interaction is additionally protected by deterministic static UI
checks. Theo's Task 9 browser evaluation remains the acceptance gate.
