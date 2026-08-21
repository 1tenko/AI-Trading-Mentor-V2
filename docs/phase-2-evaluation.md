# Phase 2 Human Evaluation

**Status:** Technical hardening, long-thread recovery, and model comparison are
complete; prepared for Theo's evaluation. This worksheet does not mark Phase 2
passed.

Run the deterministic checks first:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m mentor
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The real questions below
make paid OpenAI requests, so they are deliberately outside pytest.

## Persistent conversation flow

1. Start a new conversation and ask `What is SMT?` with Research set to Auto.
2. Ask a follow-up: `I understand SMT, but I don't understand why that matters.`
3. Confirm that the sidebar title is useful, select another conversation, then
   return to the SMT conversation. Reload the page and confirm both turns,
   Markdown, evidence, and each turn's original diagnostics remain visible.
4. Create a second conversation. Delete it through its sidebar Delete control,
   approve the confirmation, reload, and confirm only that conversation is gone.
   The original conversation must remain available and source links must still open.

## Research and evidence flow

Use High / Standard first. Research controls apply only to the next message;
historical diagnostics must not change when controls change.

| Prompt | Research control | Check |
|---|---|---|
| `What is TPD?` | Auto | Normal research is economical, teaching is clear, and source claims are cited. |
| `What are ALL the timeframe alignments for reversion levels?` | Auto | Effective depth is Exhaustive; evidence includes the 6H -> 1H teaching; the answer makes no unsupported completeness claim. |
| `Teach me everything Jacob teaches about SMT.` | Exhaustive | Diagnostics show complementary research rather than a one-search conclusion. |
| `Jacob teaches that SMT guarantees reversals, right?` | Normal | The Mentor corrects the unsupported attribution and distinguishes direct teaching from inference. |
| `Compare Jacob's 2025 and 2026 SMT teaching.` | Deep | The Mentor differentiates years only where evidence supports it. |

For every Mentor turn, verify:

- the answer remains primary; Evidence and diagnostics are collapsed initially;
- cited evidence is shown first, while all retained evidence can be expanded;
- displayed model, effort/mode, and requested/effective depth are that turn's
  historical settings;
- text-token estimates separately show cached input and cache-write tokens;
- File Search shows its known $0.0025-per-call fee separately from text-token
  cost; vector-storage and other platform charges remain explicitly excluded;
- Markdown lists and tables render normally; no in-app `NaN.` text appears;
- the browser console has no errors and no request exposes an API key or
  encrypted reasoning content.

## Technical hardening record — 2026-08-21

This is an engineering record, not Theo's acceptance decision.

- Root cause of the rising same-thread input cost: the Phase 1 replay sent all
  retained raw Responses output back to the API. In the measured thread, native
  File Search result payloads accounted for 466,921 of 534,239 stored bytes
  (87.4%). The API reports whole-request token usage, so the byte split is a
  local attribution, while the reported input-token totals are exact API data.
- The failed manual `responses.compact` attempt was replaced with native
  Responses context management at a 50,000-token threshold. Raw local history
  and browser display history remain unchanged; only encrypted model replay is
  replaced by OpenAI's opaque compaction item. The replay sanitizer removes the
  server-only `created_by` field before the next request.
- A controlled paid sequence produced native compaction on a Deep year
  comparison (32,745 input tokens, 3 File Search calls, 60 results, 7
  citations). Its following narrow turn replayed successfully at 24,241 input
  tokens. The comparable pre-hardening narrow follow-up had 134,822 input
  tokens. Model output varies, so this is an observed comparison, not a cost
  guarantee.
- Prompt caching remains OpenAI's automatic implicit behavior. Diagnostics now
  retain cached-input and cache-write tokens; no cache key or breakpoint was
  added because the measured cache writes did not establish a net benefit.
- Native File Search result limits are 8 for Normal and 20 for Deep or
  Exhaustive. A controlled Normal comparison retained cited, grounded teaching
  with 16 results while reducing input from 34,614 to 15,294 tokens versus the
  20-result run.
- Auto depth now classifies “difference(s)” questions as Deep. This fixes the
  classifier mismatch that previously allowed a multi-search Deep response to
  be recorded as Normal.

Automated verification after this hardening: `32 passed` from
`python -m pytest -q`, JavaScript syntax check passed, and desktop/mobile
browser checks confirmed no console errors, a left sidebar, collapsed evidence
with cited results first, and horizontally scrollable tables without page-level
overflow.

## Long-thread recovery and model comparison — 2026-08-21

The technical record is [phase-2-long-thread-and-model-evaluation.md](phase-2-long-thread-and-model-evaluation.md).
It documents the stale-server blank-response diagnosis, native-compaction
continuity proof, retryable-stream behavior, and the measured Terra/Sol
evaluation. It does not change the production default or introduce routing.

## Source-citation integrity — 2026-08-21

The final technical provenance fix is recorded in
[phase-2-citation-integrity.md](phase-2-citation-integrity.md). It adds the
native-citation repair safeguard, corrects cited-source versus retrieved-passage
semantics, and records a controlled paid three-turn continuation. It does not
pass Phase 2; Theo's browser decision remains required.

## Decision

Record one outcome outside Git if it contains private conversation content:

- [ ] Pass — Phase 2 persistent-chat foundation is acceptable.
- [ ] Fail — record the prompt, settings, evidence, and observed gap.

Theo alone makes this decision. A pass does not authorize Phase 3.
