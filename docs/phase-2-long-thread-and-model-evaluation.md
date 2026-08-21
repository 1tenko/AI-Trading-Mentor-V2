# Phase 2 Long-Thread Recovery and Model Evaluation

**Status:** Technical recovery complete. This record does not pass Phase 2,
change the default model, or implement model routing. Task 9 remains Theo's
human quality decision.

## Long-thread blank-response diagnosis

The affected local thread contained three completed display turns and 31 raw
Responses items (about 510 KB), but no replay items and no native-compaction
diagnostic field. The process serving the browser had started before the
current Phase 2 hardening commit, so it was running older code.

That stale code attempted a manual compaction request after the SSE response
headers had already been sent. The API request failed at the long-context
boundary and the stream closed. The browser created a Mentor heading as soon
as the request began but had no terminal error event to render, producing the
blank response Theo observed.

The current checked-in code had a separate replay defect: after a native
compaction item, it prepended all historical user messages again. That defeats
the purpose of an opaque compaction item and can duplicate prior context.

## Applied recovery

- Native Responses context management remains enabled with the existing 50,000
  token compaction threshold and `store=False`.
- When the API returns a compaction item, local model replay is now exactly the
  returned items from that item onward. Raw local history, display turns,
  citations, and diagnostics remain unchanged.
- API `failed`, `cancelled`, and stream-error events, unexpected stream
  exceptions, and a stream that ends without a terminal response all emit a
  generic SSE error event. The browser renders a visible unavailable state and
  Retry action instead of a blank Mentor heading. Failed turns are not stored.
- The server logs the exception type for its final SSE safety net without
  writing prompts, API keys, or encrypted reasoning content to logs.

This follows OpenAI's guidance to preserve only the latest compaction item and
following output in a stateless replay window; compaction itself carries the
earlier context. See the [OpenAI compaction guide](https://developers.openai.com/api/docs/guides/compaction),
[conversation-state guide](https://developers.openai.com/api/docs/guides/conversation-state),
and [Responses streaming reference](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses).

## Controlled paid compaction proof

Using a copy of the affected thread and the real Jacob vector store, the first
follow-up compacted the model replay from 31 items / about 510 KB to four items
/ about 35 KB. The browser timeline retained all completed turns. Two further
source and provenance follow-ups completed with citations and preserved the
requested context.

| Turn | Replay before | Replay after | Input tokens | File Search | Result |
|---|---:|---:|---:|---:|---|
| Source/timestamp follow-up | 31 / 510 KB | 4 / 35 KB | 129,509 | 1 call, 8 results | Completed; 2 citations |
| Exact-source follow-up | 4 / 35 KB | 9 / 66 KB | 10,024 | 1 call, 8 results | Completed; 1 citation |
| Provenance follow-up | 9 / 66 KB | 14 / 98 KB | 17,286 | 1 call, 8 results | Completed; 1 citation |

The large first input is expected: compaction occurs while processing that
request. The next turns demonstrate that the compacted replay window, rather
than a duplicated raw history, supports continuity.

## Terra and Sol comparison

Twelve paid responses were run for each model against the same Jacob corpus,
instructions, High reasoning effort, and Standard reasoning mode. Each prompt
used a fresh local thread. The set covered six normal teaching/source prompts,
two Deep or Exhaustive research prompts, and four strategy-research prompts.
A separate blind evaluator saw anonymized answer pairs, evidence counts, and
diagnostics before labels were revealed. It was not asked to claim factual
corpus correctness beyond the supplied evidence; it saw each evaluation case
label rather than the full original prompt.

| Category | Model | Prompts | Mean latency | Mean input | Mean output | Mean reasoning | File Search calls | Citations | Estimated text cost | File Search cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Normal | Sol | 6 | 72.6 s | 12,049 | 3,968 | 2,068 | 8 | 11 | $0.1262 | $0.0200 |
| Normal | Terra | 6 | 39.4 s | 12,049 | 2,353 | 708 | 6 | 11 | $0.0446 | $0.0150 |
| Deep/exhaustive | Sol | 2 | 290.5 s | 14,930 | 13,325 | 6,657 | 8 | 15 | $0.4567 | $0.0200 |
| Deep/exhaustive | Terra | 2 | 157.2 s | 15,556 | 8,101 | 2,284 | 7 | 12 | $0.1629 | $0.0175 |
| Strategy research | Sol | 4 | 201.2 s | 8,105 | 12,063 | 6,308 | 5 | 20 | $0.3672 | $0.0125 |
| Strategy research | Terra | 4 | 219.9 s | 16,251 | 12,409 | 5,190 | 8 | 16 | $0.2091 | $0.0200 |

Costs are observed-token estimates using OpenAI's published standard
short-context text-token rates plus the documented $0.0025 per native File
Search call. They exclude vector-store storage and can differ by service tier
or future pricing. See [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model),
[model catalog](https://developers.openai.com/api/docs/models), and
[pricing](https://platform.openai.com/pricing).

### Quality review

- Normal teaching: the blind review preferred Terra on four prompts and tied
  two. Terra was materially faster and cheaper with comparable citation totals.
- Deep comparison: Sol was preferred on the year-comparison question because it
  made the documented new-versus-refinement distinction cautiously and cited
  the evidence; Terra returned no citations for that answer. The exhaustive
  result was a tie.
- Strategy research: Terra won two prompts, Sol won the SS/SMT claim because it
  returned five citations where Terra returned none, and one was a tie. The
  citation failure on a strategy-rule question should not be averaged away.

## Recommendation only

Do not change the current production default in this scope. If a later,
explicitly approved routing task is opened, the evidence supports: **Normal →
Terra; Deep/Exhaustive → Sol; future Strategy Research → Sol.** This uses Terra
where its quick, economical teaching was strong, and retains Sol where source
discipline matters most. A fuller evaluation is appropriate before making that
policy automatic.

## Verification

- Deterministic tests cover failed OpenAI stream events, no persistence after a
  failed turn, correct opaque-compaction replay, recoverable SSE errors, and
  the browser's error-event handling.
- Focused tests passed: `30 passed`.
- JavaScript syntax check passed.
- A real browser test of the failed SSE path confirmed HTTP 200, a visible
  “Mentor — unavailable” state, Retry, enabled composer, and no console errors.
- A browser reload test of compacted-thread history retained all six historical
  messages, evidence, and diagnostics without console errors.

Final verification: `35 passed` from `python -m pytest -q`; JavaScript syntax
check passed; and the current loopback server returned both `/app.css` and
`/app.js` with HTTP 200 and their expected content types.
