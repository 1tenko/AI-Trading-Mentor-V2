# Phase 4 Design: Trader Profile / Editable Memory

**Status:** Proposed design and plan; implementation requires Theo's separate
approval.
**Base:** Phase 2 acceptance closure `f809193ef2749dbe53c0af14e5d3196420c896f9`.

## Objective

Make the one local Trading Mentor understand Theo's relevant trading context
across conversations through a small, inspectable, editable profile. The profile
must materially improve relevant mentorship and research guidance without
becoming a hidden chatbot memory system, methodology authority, or future
strategy-project database.

Phase 4 succeeds when Theo can see and control the current profile, change or
remove it deterministically, and receive appropriately personalised guidance in
another thread while Direct source teaching remains verified against raw Jacob
transcripts.

## Phase 3 Archive and Phase 4 Boundary

Phase 3/3B is an archived experiment, not a dependency. Its final branch and
commit are recorded in [phase-3-archive-decision.md](../../phase-3-archive-decision.md).
This branch starts at the Phase 2 closure and contains none of the assimilation
compiler, derived-record graph, orientation layer, Gate 1 runner, candidate
stores, synthesis pipeline, or Phase 3 accounting code.

Included:

- Local, global Trader Profile records and their user-facing controls.
- Conservative profile writes, explicit provenance, supersession, conflict, and
  deletion semantics.
- Bounded request-time profile context for the existing Sol mentor.
- Deterministic and human/model-level evaluation of the personalised flow.

Excluded:

- Long-term research history beyond the profile; Strategy Projects; hypotheses;
  backtests; empirical evidence; deterministic statistics; source-library UI;
  additional sources; model routing; accounts/sharing; external memory services;
  and any Phase 3 runtime or data.

## Capability Map

| Module id | Responsibility | Depends on |
| --- | --- | --- |
| profile-storage | Versioned local profile records, provenance, state transitions, and safe origin links | — |
| profile-context | Deterministically select bounded current profile context for one mentor turn | profile-storage |
| profile-writes | Conservative model-assisted explicit commands and direct UI mutations | profile-storage |
| mentor-profile-integration | Add profile context and controlled write tool beside existing raw File Search | profile-context, profile-writes |
| profile-api | Loopback-only safe profile read/mutation endpoints | profile-storage, profile-writes |
| profile-ui | Lightweight Profile panel and chat update affordances | profile-api |
| profile-evaluation | Deterministic, browser, and Theo-controlled model-level acceptance evidence | all above |

Build order: `profile-storage` -> `profile-context` and `profile-writes` ->
`mentor-profile-integration` and `profile-api` -> `profile-ui` ->
`profile-evaluation`.

## Domain Model

The canonical profile is a collection of small local records, not a single
AI-written biography. Each profile record has a constrained semantic shape:

| Field | Meaning |
| --- | --- |
| `id` | Stable local identifier. |
| `category` | One of: goals/research, markets/instruments, schedule/horizon, style/methodology, execution/risk/constraints, experience/learning, preferences/discretion, or strengths/difficulties/principles. |
| `subject` | A concise, user-readable topic within its category, such as `holding period` or `available session`. |
| `subject_key` | Stable, normalised identity within `category`, used to find the one record that an unambiguous replacement may supersede. |
| `value` | The short, displayable statement currently asserted about Theo. It is bounded text, not arbitrary JSON or a model essay. |
| `kind` | `fact`, `preference`, `constraint`, `goal`, `principle`, or `learning-state`. |
| `provenance` | `USER_STATED`, `USER_CONFIRMED`, `AI_INFERRED`, or `USER_DECISION`. |
| `state` | `confirmed`, `tentative`, `superseded`, `conflicting`, or `archived`. No numeric confidence field exists. |
| `origin_kind` | `chat`, `profile-editor`, or `confirmation`; it explains how the record was created without copying chat text. |
| `origin_thread_id`, `origin_turn_number`, `origin_available` | Nullable thread/turn coordinates plus an availability flag. They give the UI a safe optional “where/when” link; they are not a cascading foreign key. |
| `supersedes_item_id` | Optional predecessor for a resolved change. |

`confirmed` records are the only canonical active profile context. A tentative
AI inference is visible for review but is never silently injected. Superseded,
conflicting, and archived records are history/inspection state and are excluded
from active context.

The storage migration creates `trader_profile_items` and the minimum indexed
fields required for current records, `category + subject_key` identity, and
origin lookups. It uses SQLite CHECK constraints for the controlled
vocabularies and one transaction for every replacement, confirmation, conflict
resolution, archive, or permanent delete. During existing Phase 2 thread
deletion, the same transaction sets `origin_available = false` for matching
profile rows but never deletes the global profile record. It must not alter the
existing source registry, vector-store setting, raw replay items, display
turns, diagnostics, or Phase 2 conversation behaviour.

## Provenance, Writes, and User Control

`USER_STATED` is an unambiguous profile-relevant fact explicitly supplied by
Theo. `USER_DECISION` is an explicit, intentional rule or preference (for
example, a stated choice of trading style). `USER_CONFIRMED` is a previously
tentative AI interpretation that Theo accepts. `AI_INFERRED` is a clearly
labelled, tentative proposal only.

No ordinary casual statement becomes durable memory automatically. The mentor
may create at most one tentative proposal from a turn only when it is durable,
profile-relevant, and unambiguous enough to state concisely. The proposed item
appears in the Profile panel for Confirm or Reject and does not influence a
future prompt until confirmed.

The normal direct path is deliberate:

1. Theo can add, edit, confirm, reject, archive, or permanently delete an item
   in the Profile panel.
2. In chat, explicit memory language (for example “remember,” “forget,” “my
   goal changed,” or “I have decided”) permits Sol to call one server-owned,
   schema-validated profile mutation tool.
3. The tool may act directly only when the target and durable meaning are
   unambiguous. Otherwise Sol asks a short clarification or creates a tentative
   proposal; it never guesses a destructive replacement.

The tool accepts only a structured create/propose/confirm/supersede/archive/
delete operation, a controlled category/kind, bounded subject/value, and an
existing target or predecessor ID where required. `subject_key` must resolve to
one active predecessor before automatic supersession; otherwise the operation
becomes a tentative/conflict proposal and Sol asks Theo to distinguish it.

The Responses protocol is bounded: one profile mutation/proposal tool call at
most per user turn, one local transactional execution with an idempotency key,
then one terminal continuation call carrying only a minimal safe tool result.
The server preserves only the API-required tool exchange for replay. Citation
repair and a stream retry never execute a profile mutation again; they operate
on the already-recorded result. The Profile service validates every operation
and never writes the final mentoring answer.

Permanent delete removes the profile record and its value from local profile
storage, then reselects future context. It removes canonical profile memory and
all future profile injection—including a later turn in the same thread—but does
not redact the original text from retained Phase 2 chat/replay history. Removing
that historic chat requires the existing Phase 2 conversation deletion. Mentor
instructions must treat a historic personal statement as historical chat, not
current profile truth. If a profile item originated in a thread later deleted
under Phase 2 semantics, the profile survives as global user state but its
source link is marked unavailable; deleting a thread must never resurrect or
implicitly re-ingest a profile item.

## Contradictions and History

An unambiguous new assertion about the same subject supersedes the old
confirmed record in one transaction: the new record becomes `confirmed`, the
old record becomes `superseded`, and the predecessor link is retained for
inspection. A direct edit follows the same versioned path rather than mutating
history in place.

Where two claims may both apply in different contexts, or the intended target
is ambiguous, the system does not choose. It marks the competing candidate(s)
`conflicting` or leaves the new one `tentative`, excludes them from active
context, and asks Theo to resolve the distinction. `archived` retains an
intentional historical record without treating it as current. This prevents a
past preference from invisibly influencing later advice.

## Request-Time Context and Source Authority

Before composing a new Responses request, a local deterministic selector takes
the current question and active confirmed items. It scores the controlled
category and normalised subject against a small, explicit question-intent map;
for example, research/backtest questions can select goals, constraints,
markets, style, and available time, while an exact source/timestamp question
normally selects none. It deduplicates and caps orientation at **six items or
1,200 characters**, whichever is reached first. It records only selected item
IDs/count/character count in safe per-turn diagnostics; it does not replay a
full profile into historical Responses state.

The selected records enter Sol's instruction context in a marked `Trader
Profile — user context, not source evidence` block. Profile values are
untrusted data, never instructions. The mentor may use them to personalise its
reasoning but must not cite them as Jacob, call them Direct source teaching, or
let a user belief establish methodology. The existing raw Jacob vector store,
native File Search, research-depth policy, citation repair, opaque compaction,
and source provenance rules remain unchanged. Direct source teaching still
requires native raw citations.

## User Experience

The static chat remains the main product. Add one clear **Trader Profile**
control that opens a restrained panel, not an admin dashboard. The panel shows:

- current confirmed records, grouped by the controlled categories;
- a separate visible **Needs confirmation** section for tentative AI inferences;
- provenance, state, and a concise source-thread/time link when available;
- Add, Edit, Confirm, Reject, Archive, and Delete controls with clear
  confirmation for destructive actions; and
- a collapsed history/conflicts section, so current truth remains obvious.

The chat shows a compact per-turn “saved to profile” or “profile update needs
confirmation” acknowledgement when a mutation/proposal occurs. It does not
display profile data on every answer or force confirmation after every message.
Keyboard access, existing safe DOM rendering, loopback-only requests, and the
current responsive layout remain required.

## API and Integration Contract

The loopback server adds only safe local profile routes:

- `GET /api/profile` returns browser-safe current, tentative, history, and
  conflict projections; never raw replay/reasoning data.
- `POST /api/profile/items` creates an explicitly user-authored item.
- `PATCH /api/profile/items/{id}` performs a validated edit, confirmation,
  rejection, archive, or conflict-resolution action.
- `DELETE /api/profile/items/{id}` permanently removes that one profile item.

The existing message route may return safe profile-change metadata with its
terminal response. It neither exposes the model tool transcript nor alters the
historical configuration/evidence contract. All mutations reject malformed,
oversized, or unknown state/provenance values and return clear local errors.

## Privacy and Future Compatibility

Canonical profile state remains in the existing local SQLite database. There is
no external memory service, background upload, new vector store, custom
embeddings, custom ranking, or hidden background model call. Only the bounded
selected context is sent on a relevant mentor request.

The profile is global. A future Strategy Project may consume selected global
profile records alongside project-scoped state, but Phase 4 creates no project
tables and stores no strategy hypotheses, rules, experiments, or empirical
results in the profile. The profile also has no Jacob-specific schema: it can
say that Theo is studying Jacob without making Jacob globally mandatory or
authoritative through profile data.

## Commands and Project Structure

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m mentor
npx --yes agent-browser open http://127.0.0.1:8765
```

Planned implementation stays in the existing small application:

```text
src/mentor/storage.py       SQLite schema, transactional profile lifecycle
src/mentor/profile.py       Profile validation, state transitions, selection
src/mentor/chat_service.py  Sol request composition and controlled tool loop
src/mentor/server.py        Loopback-only profile API
src/mentor/static/          Existing static UI plus Profile panel
tests/                      Deterministic storage/service/server fixtures
```

No dependency, frontend framework, database technology, or OpenAI API change
is approved by this design. Implementation must recheck the current official
Responses tool schema before adding the local function tool; any incompatibility
that changes the product contract requires Theo's approval.

## Evaluation and Success Criteria

Deterministic tests use mocked Responses-shaped tool-call fixtures and no paid
API traffic. They must establish:

1. a confirmed preference saved in Thread A is selected for a relevant Thread B
   request but not an unrelated request;
2. direct edit, archive, permanent delete, supersession, and conflict
   resolution deterministically change active context;
3. a tentative AI inference is visibly distinct and excluded until confirmed;
4. profile data cannot create a Direct Jacob teaching or bypass native citations;
5. thread deletion does not accidentally delete global profile state, and
   profile deletion is never reintroduced from historical chat/replay; and
6. selected context remains within the stated item/character budget and does
   not enter opaque replay history.

After deterministic checks, Theo performs the model-level human gate using the
real private app. It covers cross-thread recall, relevance, edit/delete,
contradiction, provenance, source-authority separation, personalised
research/backtest guidance, context efficiency, the Profile panel, and Phase 2
regression behaviour. Theo alone decides pass/fail. No paid evaluation is part
of ordinary pytest or browser smoke tests.

## Boundaries

- **Always:** keep profile local, explicit, inspectable, bounded, and separate
  from source authority; retain Sol as the reasoning brain; preserve Phase 2
  source/citation/replay/compaction guarantees; test migrations and destructive
  profile actions before commit.
- **Ask first:** add a dependency, external memory service, new model/tool
  retention behaviour, source scope, automatic model routing, project state,
  empirical evidence, or any paid model evaluation.
- **Never:** silently store casual chat as durable fact; inject tentative,
  superseded, conflicting, archived, or deleted items; expose reasoning/replay
  state; treat a user belief as a Jacob source; reuse Phase 3 runtime; or start
  Phase 5+ work.

## Approval Gate

This document and its implementation plan define Phase 4 only. Do not implement
a schema migration, API route, local tool, UI panel, or paid evaluation until
Theo explicitly approves the plan and task list.
