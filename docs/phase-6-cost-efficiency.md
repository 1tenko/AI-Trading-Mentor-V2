# Phase 6 real-corpus cost and latency evaluation

Date: 2026-09-07

Branch: `feature/phase-6-strategy-projects`

Status: deterministic implementation complete; Theo human quality gate remains open.

## Decision

GPT-5.6 Luna at low reasoning is the evidence-digest model. GPT-5.6 Sol remains
the final Mentor, teacher, coach, synthesis, and judgment model with Theo's
selected reasoning configuration. The real-corpus quality samples did not
justify a Terra fallback.

Auto research now treats mentor breadth separately from corpus completeness:

- Normal: one focused pass per selected mentor.
- Deep: up to two complementary passes for critical comparisons, nuance, and
  important strategy research.
- Exhaustive: up to three passes per mentor only for explicit all/every,
  corpus-wide, exhaustive, or complete-inventory intent.

Natural single-mentor and two-mentor wording narrows the temporary source scope.
An explicit all-enabled request keeps every enabled mentor first-class. Missing
required coverage still blocks an all-mentor answer rather than silently omitting
a mentor.

## Benchmark matrix

The after measurements use the existing 176-file GxT corpus and existing remote
stores. No source was uploaded or reindexed. The old exact five-mentor human turn
is a measured baseline. Other before values are conservative same-workload
projections obtained by repricing the measured after-turn token profiles as the
former all-Sol pipeline; they are not presented as paid before runs.

| Case | Scope / effective depth | Before | After total | File Search | Citations | After latency |
|---|---|---:|---:|---:|---:|---:|
| A — single Afyz teaching | Afyz / Normal | $0.1274 projected; closest prior measured $0.1157 | $0.0833 | 1 | 5 | 50.8 s |
| B — Garrett versus Afyz | 2 mentors / Deep | $0.4788 projected | $0.2763 | 4 | 17 | 74.7 s |
| C — all mentors, normal teaching | 5 mentors / Normal | closest prior all-mentor measured $3.0702 | $0.2593 | 5 | 19 | 94.5 s |
| D — exact Garrett source | Garrett / Normal | $0.1606 projected | $0.1269 | 1 | 1 | 26.5 s |
| E — critical all-mentor comparison | 5 mentors / Deep | closest prior all-mentor measured $3.0702 | $0.1605 | 10 | 37 | 213.0 s |
| F — explicit exhaustive all-mentor search | 5 mentors / Exhaustive | $2.4647 projected | $1.3252 | 17 | 53 | 307.4 s |

The prior exact human turn used Auto -> Exhaustive, 21 File Search calls, 60
citations, about ten minutes, and approximately $3.0702. A later persisted
diagnostic of the same failure class recorded 22 calls, 61 citations, 639.6
seconds, and $3.2991. The new exact wording routes to Deep rather than Exhaustive.

## Stage costs and quality

| Case | Luna mentor research | File Search | Final Sol | Result |
|---|---:|---:|---:|---|
| A | $0.0024 | $0.0025 | $0.0784 | Accurate Afyz-only attribution and scoped absence |
| B | $0.0111 | $0.0100 | $0.2552 | Shared core and differences retained without superiority claims |
| C | $0.0122 | $0.0125 | $0.2346 | All five mentors distinct; shared core and tension preserved |
| D | $0.0018 | $0.0025 | $0.1226 | Unsupported exact timestamp withheld rather than fabricated |
| E | $0.0310 | $0.0250 | $0.1045 | Nuanced five-mentor disagreement and Garrett currentness qualified |
| F | $0.0624 | $0.0425 | $1.2203 | Broad coverage with conflicts, uncertainty, and provenance preserved |

The compact digest contract uses fixed sections for supported claims, nuances,
conditions, conflicts, and scoped absence; at most six key claims and three
items per other section; short bullets; and native citations attached to source
claims. The per-pass output cap fell from 5,000 to 2,500 tokens. Real outputs
averaged roughly 532–768 tokens by scenario and the largest observed digest was
1,029 tokens, so the cap retained material headroom without producing incomplete
research responses.

## Prompt caching

No explicit cache breakpoint was added. The stable evidence instruction is below
the current 1,024-token cacheable-prefix minimum, and top-level `instructions`
cannot hold an explicit input-text breakpoint. Moving it solely to force a
breakpoint would complicate the request protocol without measured benefit.
Automatic caching was retained and measured. Across the six turns, provider
usage reported cached-input tokens on five turns and cache-write tokens on every
turn. Diagnostics now split uncached, cached, cache-write, and output tokens for
mentor research and final synthesis.

## Spend control finding

The six real benchmark turns incurred an estimated $2.231597 in text and known
File Search call costs, exceeding the authorized $2.00 ceiling by $0.231597.
Paid work stopped immediately after reconciliation and no further real-corpus
call was made. The miss occurred because the final exhaustive Sol synthesis used
231,645 uncached input tokens and 13,915 output tokens, materially above the
projection derived from the Deep run. Future paid matrices must reserve against
the highest observed final-synthesis footprint before starting an exhaustive
case; a Deep result is not a safe upper bound for Exhaustive.

## Architecture assessment

Native per-library File Search remains the accepted evidence architecture. The
measured Luna digest pipeline materially reduces Normal/Deep all-mentor cost
while preserving ownership, attribution, native citations, retries, and final
Sol judgment. Direct Vector Store Search remains only a future experiment if a
separately approved evaluation shows that native File Search cannot meet a
specific quality, cost, or latency target; this work provides no reason to switch
architectures now.

## Verification

- Model-routing, source-scope, Auto-depth, project chat, server, replay, and
  privacy focused suite: 212 passed.
- Complete deterministic suite: 517 passed in 622.21 seconds.
- Browser: the real five-mentor Normal result rendered the Luna evidence model,
  one pass per mentor, separate mentor/File Search/final Sol costs, token-stage
  breakdown, citations, and completed synthesis without a page error.
- Independent review: no remaining P0/P1 finding after regressions for natural
  scope negation (including curly apostrophes), comparison precedence, generic
  all/every exhaustive intent, and multi-response final Sol cost accounting.
- The loopback server was restarted from the verified working tree and returned
  HTTP 200 on `127.0.0.1:8765`.
