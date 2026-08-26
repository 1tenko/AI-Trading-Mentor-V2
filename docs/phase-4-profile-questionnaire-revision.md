# Phase 4 Human-Gate UX Revision

## Finding

The first Task 8 human evaluation failed on the technical profile editor. The
canonical local profile model is retained; only its normal user-facing workflow
is replaced by a trader-facing questionnaire.

## Design

`/profile` is a real loopback route. It has 20 fixed numbered questions plus
Additional Information, grouped into human sections. It does not expose
category, subject, kind, provenance, state, or internal IDs. Every field is
optional. Blank means no current answer; an explicit uncertainty phrase such as
`idk` or `I don't know` is a saved, current answer whose rendered context says
the user is unresolved rather than asserting a preference.

The fixed mapping reuses `trader_profile_items` with `USER_STATED`,
`confirmed`, and `profile-editor`:

| Field | Category | Subject | Kind |
| --- | --- | --- | --- |
| Q1 | goals/research | trading objective | goal |
| Q2 | markets/instruments | markets willing to trade | preference |
| Q3 | schedule/horizon | trading availability | constraint |
| Q4 | style/methodology | preferred trading style | preference |
| Q5 | schedule/horizon | preferred holding duration | preference |
| Q6 | execution/risk/constraints | preferred trade frequency | preference |
| Q7 | preferences/discretion | discretion preference | preference |
| Q8 | strengths/difficulties/principles | trading strengths | fact |
| Q9 | strengths/difficulties/principles | recurring trading difficulties | fact |
| Q10 | style/methodology | concepts/models to build from | learning-state |
| Q11 | experience/learning | trusted and uncertain concepts | learning-state |
| Q12 | style/methodology | ideal setup | preference |
| Q13 | execution/risk/constraints | risk and funding constraints | constraint |
| Q14 | goals/research | strategy priorities | preference |
| Q15 | execution/risk/constraints | strategy deal-breakers | constraint |
| Q16 | goals/research | backtesting commitment | constraint |
| Q17 | goals/research | suspected edge | learning-state |
| Q18 | strengths/difficulties/principles | current trading uncertainties | learning-state |
| Q19 | style/methodology | ideal strategy | preference |
| Q20 | strengths/difficulties/principles | optimisation principles | principle |
| Additional Information | strengths/difficulties/principles | additional trader context | fact |

The form validates every submitted field before beginning one SQLite transaction.
For each mapped subject, unchanged answers remain current; changed answers use
the existing supersession record path; clearing archives the current
questionnaire record; blank new answers create nothing. Any validation failure
rolls back the complete batch. Unrelated legacy/manual records remain untouched
and may appear only in collapsed advanced/history inspection.

Ordinary mentor requests retain the existing relevant-only selector and its
six-item/1,200-character budget. Explicit strategy-design requests use a
separate `Trader Strategy Profile` block built only from current questionnaire
records. It is deterministic, marks unknowns unresolved, prioritises goals,
markets, availability, style, holding duration, discretion, risk, priorities,
deal-breakers, ideal strategy, and uncertainties, and is capped at 6,000
characters. It remains user context, not source evidence.

Tentative AI suggestions and history remain secondary sections on `/profile`.
The former technical drawer is removed from normal navigation; its backend API
remains compatible for chat and tests.

## Gate

Task 8 is reset: the original human gate failed on profile-building UX. After
this revision passes deterministic/browser review, Theo alone re-runs the human
quality gate.
