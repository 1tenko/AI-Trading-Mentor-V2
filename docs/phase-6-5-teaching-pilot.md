# Phase 6.5 Source-Grounded Teaching Pilot — Local Preparation Record

## Status

Local repair and pilot preparation only. No paid model call, upload, index operation, vector-store creation, live activation, or Phase 7 work was performed.

## Confirmed defects

- A questionnaire-field match could suppress project source research even when the current turn was a source lesson, learner-answer evaluation, or challenge. In the real failed thread, the two challenged turns therefore performed zero File Search calls.
- Short follow-ups were planned from the latest sentence alone and did not carry bounded lesson context or the preceding source scope.
- Mentor names inside quoted prior prose could be interpreted as a new exclusive source override.
- Literal citation-looking text produced by the model could survive in displayed Markdown despite having no native citation annotation.
- Exact-timestamp validation accepted only numeric-second transcript ranges, while the real GxT transcripts use `HH:MM:SS.mmm` ranges.
- The shared mentor prompt still described a Phase 1/Jacob-only product, which conflicted with project-aware GxT teaching.
- The prior exercise guidance did not require explicit timeframe roles and did not state that the app cannot inspect a chart screenshot.

## Repairs

- A bounded conversation research context now separates the current request from quoted prior text, treats historic assistant prose only as query-disambiguation context, and requires fresh raw-source verification for source challenges and contextual exercise questions.
- A still-enabled prior lesson scope can be continued for a contextual follow-up; disabled sources are removed by the current project access boundary.
- Profile state remains available for explicit personal/profile requests, but an incidental questionnaire match no longer overrides a project source lesson or challenge.
- Literal `filecite` markers are removed from final text and never counted as native citations.
- Exact timestamp checks now parse both numeric seconds and `HH:MM:SS.mmm` ranges and still require the range to belong to a natively cited source file.
- The common mentor policy now refers to the enabled mentor corpus while retaining the same raw-source, provenance, completeness, and citation rules for legacy Jacob conversations.
- Project exercise instructions require clear timeframe roles, distinguish source procedure from an AI-selected practice constraint, forbid invented prior details, and offer a text observation exercise instead of unsupported screenshot assessment.

## Real failure evidence transport

The copied runtime shows that the successful broad lesson used 10 mentor-research calls, 120 retrieved evidence rows, and 40 citations attached to intermediate research digests. The research response payload was approximately 368,084 characters (about 92,021 estimated tokens). Of the 120 stored evidence rows, 92 were unique by file-and-passage hash and 28 were duplicates. Those intermediate citations were incorrectly exposed as though they supported the final teaching text; the repaired boundary counts only annotations attached to the final response as final citations. Retrieved passages remain separate display evidence and intermediate citation counts remain visible in research diagnostics. The raw research outputs were intentionally absent from future model replay, so the later challenged turns needed a fresh search; treating the prior answer as evidence would have been incorrect.

No new deduplication architecture is introduced in this stage. Variant A retains the accepted native File Search pipeline so the paid comparison can determine whether lesson preparation adds enough value to justify more complexity.

## Private source review

Deep review was limited to the two approved topic areas in selected Garrett and Afyz lessons. Related lessons were inventoried without expanding into full-corpus interpretation. Supporting passages, source identities, timestamps, qualifications, contrasting passages, transcript uncertainties, human-review questions, coverage logs, lesson packages, and benchmark reference answers remain in the ignored local pilot directory. No lesson-derived finding or transcript detail is committed here.

## A/B/C comparison

- **A — repaired retrieval:** accepted per-library native File Search plus the repaired multi-turn intent, scope, evidence, and citation behavior.
- **B — direct original lessons:** manually selected coherent original sections are supplied to the same teacher. This tests whether fuller evidence improves teaching and is not represented as an autonomous retrieval test.
- **C — prepared lesson hybrid:** concise derived, source-linked teaching notes plus the same original sections. Notes are marked as derived aids and cannot replace raw-source verification.

All variants use the same model, reasoning setting, teacher policy, learner dialogue, current source permissions, lesson-continuity fields, output/tool/retry limits, and critical-failure rubric. The harness is not imported by the application and live dispatch fails closed unless explicitly enabled with a pre-dispatch budget reservation.

## Frozen benchmark

The tracked content-free fixture freezes 12 cases across the two topics, including correct and incorrect learner answers, ambiguity, exact source/timestamp, a source challenge, contextual “Which timeframe?”, a concrete exercise, reload continuity, mentor differences, source isolation, and capability honesty. Four cases are held out and two are multi-turn.

Critical failures are evaluated independently of the average score:

- unsupported correction;
- fabricated or mismatched source/timestamp;
- omission of a meaning-reversing condition;
- cross-scope leakage;
- an unusable exercise presented as ready.

Private source-linked reference expectations remain outside Git and are never inserted into ordinary model prompts.

## Proposed first paid batch — not authorized or run

Use three cases (four conversation turns) before expanding the matrix:

1. source-supported correct continuation answer;
2. exact Garrett source/timestamp request;
3. top-down lesson followed by contextual “Which timeframe?”.

Run all three variants with GPT-5.6 Sol, high reasoning, a 120,000-estimated-input-token cap, a 4,000-output-token cap, at most two tool calls per teacher turn, and at most one retry. Variant A may use at most one normal Garrett pass and one normal Afyz pass per turn. Allow at most one citation repair per terminal answer. Do not use a paid grader in the first batch; score against the frozen private rubric and Theo's review.

This produces 12 primary teacher-turn evaluations, up to 8 Luna evidence calls for Variant A, and a worst-case reservation for 12 citation repairs. Using the pricing already configured in the application and conservative bounded inputs, request an explicit **USD $5.00 cumulative ceiling** for this first batch. Before each dispatch, the budget guard reserves the entire remaining ceiling; a successful usage report releases the unused balance, while missing usage leaves the reservation locked. Preparation cost and recurring teaching cost must be reported separately.

## Not yet tested

- No A/B/C model answer quality, latency, token use, or cost has been measured.
- No prepared variant has been connected to or activated in Theo's live project.
- No claim has been promoted into a playbook or Trader Profile.
- No full-corpus preparation, embedding, upload, or remote-resource operation has occurred.
- Video-dependent source details remain transcript-grounded unless explicitly flagged for visual verification.
