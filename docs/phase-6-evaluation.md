# Phase 6 automated evaluation

Date: 2026-09-05
Branch: `feature/phase-6-strategy-projects`
Status: automated Checkpoint C passed; Theo human quality gate remains open.

## Scope and authority

The evaluation used only the tracked synthetic Phase 6 fixtures. No real Garrett,
Afyz, Erik, Splash, Zay, Theo-note, Jacob, or backtest content was uploaded. Raw
sources remain the factual authority. Garrett's canonical role was evaluated only
as lineage/currentness inside Garrett's library; it was not treated as empirical
superiority, an automatic recommendation, or Theo's adopted strategy.

Current cost projections use OpenAI's standard GPT-5.6 Sol promotional API rates
of $4/M input tokens, $0.40/M cached input tokens, and $20/M output tokens, with
cache writes at 1.25x uncached input. File Search is accounted at $2.50/1,000 tool
calls. Sources: [GPT-5.6 Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
and [OpenAI File Search announcement/pricing](https://openai.com/index/new-tools-for-building-agents/).

## Behavioral matrix

| Scenario | Result | Evidence |
|---|---|---|
| Shared X; Afyz-only Y; Splash-only Z | Pass | Live normal and Deep five-mentor comparisons retained `SHARED_X`, `AFYZ_ONLY_Y`, and `SPLASH_ONLY_Z` with mentor-specific attribution. |
| Garrett A versus Afyz B | Pass | Live comparison kept the Beta disagreement explicit and scoped rather than flattening it into Alpha. |
| Afyz evidence with no Garrett result | Pass | Live scoped comparison said the Garrett search found no teaching; it did not convert absence into rejection. |
| Disabled mentor isolation | Pass | Deterministic request/result/citation ownership tests exclude disabled libraries. |
| Afyz-only one-turn override | Pass | Deterministic scope and chat tests prove the turn scope changes without mutating the saved toggle. |
| All-enabled initial and complementary research | Pass | Deep live run made at least two model research passes for each of Garrett, Afyz, Erik, Splash, and Zay; all five citation owners survived reconciliation. |
| Exact timestamp | Pass | Live Garrett-only answer identified `TIMESTAMP_RULE` at 00:01:00-00:01:08 from the cited `[60.0 --> 68.0]` passage. |
| Two-project and General isolation | Pass | Deterministic ownership tests and browser General view expose safe summaries only; project sources/findings/playbook remain local. |
| Normal five-mentor teaching | Pass | Live normal comparison searched all five mentors and preserved useful attributed explanations from each. |
| Garrett archival/Foundation/Advanced | Pass | Live currentness answer retained Foundation continuity, Advanced qualification, and archival context without claiming later means empirically better. |
| Canonical lineage versus empirical performance | Pass | Prompt-policy and ledger tests keep source lineage, AI recommendation, empirical evidence, and adopted strategy separate. |
| Non-Garrett-derived candidate | Pass | Deterministic ledger/promotion tests permit a supported provisional recommendation but require exact user approval before adoption. |
| Unfinished experiment coaching | Pass | Live answer preserved the recorded “label 20 examples” action, explained contamination from switching early, and made Theo's explicit override available. |
| Promotion safety | Pass | Generic acknowledgement cannot approve; only the sole immediately preceding validated promotion can create a new immutable playbook version. |

## Live synthetic results and hardening

The first combined behavioral run passed five-mentor teaching, scoped absence,
and Garrett currentness, then exposed that exact project timestamp wording did
not enter source research. After the deterministic routing fix, the next run
exposed an invalid citation-repair request: project mode had serial evidence but
no direct File Search tool. A further run proved the passage was correct but the
integrity guard parsed `MM:SS` only, not the model's `HH:MM:SS` range. Tests were
added before each correction. The final exact-timestamp run passed with a native
Garrett citation and source-verifiable range.

The Deep all-five run passed with 11 Responses calls and 15 actual native File
Search tool calls, cost $0.400050, and complete cleanup. The final exact timestamp
run used 2 Responses calls and 1 File Search call, cost $0.031936. The coaching
turn used 1 Responses call and no source search, cost $0.011431. Its original
automated mark was a rubric defect: the response correctly rendered the internal
next-action token as “label 20 examples.” The corrected deterministic rubric
re-evaluates that preserved answer as passing; no duplicate paid call was made.

All paid Phase 6 contract and behavioral attempts, including failed diagnostic
runs, total a conservative $1.231593 against the authorized cumulative $5 cap.
Every ignored audit reports remote cleanup complete. The synthetic resources were
well below the first free vector-storage GB and were deleted after each run.

## Checkpoint C verification

- Focused Phase 6 project/source/ledger/chat suite before live execution: 209 passed.
- Post-hardening targeted protocol, timestamp, budget, rubric, and privacy suite: 12 passed.
- Full deterministic suite: 470 passed in 565.76 seconds.
- Browser: desktop and 390px passed with zero console/page errors, no horizontal
  overflow, keyboard focus, safe General summaries, project-local Roadmap detail,
  and refresh restoration.
- Privacy/replay: raw File Search result payloads remain absent from persistent
  replay; Phase 5 qualitative evidence remains ephemeral; no raw spreadsheet row,
  note, private transcript, API key, path, runtime database, remote ID, or ignored
  audit is tracked by Git.
- Independent review: no remaining P0/P1 source-ownership, project-isolation,
  citation-integrity, replay/privacy, spend-control, cleanup, or accessibility blocker.

## Human gate

Theo still decides whether Phase 6 passes. Human acceptance should verify:

1. General Mentor shows only a brief GxT project summary and no detailed project evidence.
2. GxT Mastery teaches across enabled Garrett/Afyz/Erik/Splash/Zay sources with attribution and citations.
3. Disabling Afyz and using a one-turn Afyz override changes only that turn's source scope.
4. An unfinished experiment and exact next action survive refresh/restart and receive appropriate coaching pushback.
5. A finding remains provisional until Theo explicitly approves its playbook promotion.
6. Phase 5 numerical attachment and fresh qualitative-consent flows still behave as accepted.

Phase 7 is not authorized by this automated result.
