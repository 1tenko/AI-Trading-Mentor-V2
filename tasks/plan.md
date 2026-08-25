# Implementation Plan: Phase 4 Trader Profile / Editable Memory

**Status:** Proposed — requires Theo's approval before implementation.

## Scope and Base

Implement only [the Phase 4 design](../docs/superpowers/specs/2026-08-25-trading-mentor-phase-4-design.md) on `feature/phase-4-trader-profile`, based on
Phase 2 closure `f809193ef2749dbe53c0af14e5d3196420c896f9`. Preserve the
Phase 3 archive branch; no Phase 3 runtime, artifacts, or implementation is
part of this plan.

## Architecture Decisions

1. A small versioned SQLite profile record is canonical; no opaque summary,
   external memory, vector store, or custom retrieval system is needed.
2. Only confirmed records enter a bounded deterministic request selector. They
   are marked user context, never source evidence or instructions.
3. Explicit UI actions and unambiguous explicit chat memory requests are the
   write path. Tentative model proposals require confirmation before use.
4. An edit creates a successor and atomically supersedes its predecessor;
   conflicts stay out of active context and permanent delete removes the item.
5. The current Python/SQLite/static-browser architecture remains sufficient.

## Dependency Graph

```text
Task 1 profile schema/lifecycle
  -> Task 2 profile service + context selection
      -> Task 3 Sol tool/request integration
      -> Task 4 loopback profile API
          -> Checkpoint A
              -> Task 5 static Profile panel
                  -> Task 6 chat mutation affordances
                      -> Checkpoint B
                          -> Task 7 deterministic regression
                              -> Task 8 Theo human quality gate
```

## Task List

## Task 1: Add the local profile schema and transactional lifecycle

**Dependencies:** None.
**Files likely touched:** `src/mentor/storage.py`, `tests/test_storage.py`.

**Acceptance criteria:**

- [ ] Idempotent migration creates constrained versioned profile records without
  changing Phase 2 source, thread, replay, display-turn, or diagnostics data.
- [ ] Create, supersede, archive, conflict, and permanent delete are atomic;
  `category + subject_key` prevents ambiguous active duplicates; thread deletion
  preserves global profile state but sets its structured origin availability
  false in the same transaction.
- [ ] Tests prove migration, rollback, delete, and current-state filtering.

**Verification:** focused storage tests; `.\.venv\Scripts\python -m pytest -q`.

## Task 2: Build the profile service and bounded local selector

**Dependencies:** Task 1.
**Files likely touched:** `src/mentor/profile.py`, `src/mentor/storage.py`,
`tests/test_profile.py`.

**Acceptance criteria:**

- [ ] Validation enforces category/kind/provenance/state vocabularies, bounded
  fields, explicit provenance, and unambiguous successor targets.
- [ ] Only confirmed current records can be selected; conflicts, tentative,
  superseded, archived, and deleted values never appear in active context.
- [ ] Relevance selection is deterministic, deduplicated, and capped at six
  records or 1,200 characters.

**Verification:** focused profile tests; full pytest.

## Task 3: Integrate bounded profile context and controlled profile writes

**Dependencies:** Task 2.
**Files likely touched:** `src/mentor/chat_service.py`, `src/mentor/prompts.py`,
`src/mentor/profile.py`, `tests/test_chat_service.py`.

**Acceptance criteria:**

- [ ] Relevant confirmed records are added as marked user context beside the
  unchanged native raw File Search path; no selected profile payload enters
  historical replay items.
- [ ] A Responses function tool can only request validated explicit write or
  tentative-proposal operations; it allows one idempotent mutation/proposal and
  one terminal continuation per turn, and citation repair/retry never reruns it.
- [ ] Direct source citations, exhaustive research, streaming, and compaction
  retain their Phase 2 contracts.

**Verification:** API-shaped tool fixtures, profile budget assertions, citation
regressions, full pytest. Recheck current official Responses tool schema before
implementation; stop for Theo if it changes this contract.

## Task 4: Add safe local profile API projections and mutations

**Dependencies:** Tasks 1–3.
**Files likely touched:** `src/mentor/server.py`, `src/mentor/storage.py`,
`src/mentor/profile.py`, `tests/test_server.py`.

**Acceptance criteria:**

- [ ] Loopback-only GET/POST/PATCH/DELETE profile routes expose safe profile
  projections, validated user actions, clear errors, and no replay/reasoning
  state.
- [ ] A terminal chat response can safely disclose a saved/proposed profile
  mutation without exposing model tool transcripts.
- [ ] Malformed, oversized, unknown, and stale updates fail safely.

**Verification:** focused server tests; full pytest.

### Checkpoint A: Profile foundation

- [ ] Full pytest passes.
- [ ] A Thread A profile record influences a relevant Thread B request only.
- [ ] Edit/delete/supersede/conflict transitions leave no stale active context.
- [ ] Existing source/vector-store/replay/thread deletion boundaries survive.

## Task 5: Add the restrained static Trader Profile panel

**Dependencies:** Checkpoint A.
**Files likely touched:** `src/mentor/static/index.html`,
`src/mentor/static/app.js`, `src/mentor/static/app.css`, `tests/test_server.py`.

**Acceptance criteria:**

- [ ] One accessible Profile control opens grouped current items, separate
  tentative proposals, and collapsed history/conflicts.
- [ ] Theo can add/edit/confirm/reject/archive/delete with a destructive-action
  confirmation; provenance and origin availability are clear.
- [ ] The static responsive chat layout and existing controls remain intact.

**Verification:** static/server tests, full pytest, desktop/mobile local browser
smoke without paid model messages.

## Task 6: Surface compact chat profile-update affordances

**Dependencies:** Task 5.
**Files likely touched:** `src/mentor/static/app.js`, `src/mentor/static/app.css`,
`tests/test_server.py`.

**Acceptance criteria:**

- [ ] A saved or proposed mutation has one compact, actionable acknowledgement
  in chat; ordinary answers do not expose or repeat profile data.
- [ ] Reloading/switching threads does not turn historical chat text into a new
  profile write or reactivate deleted/superseded records; retained historic
  personal text is never treated as current profile truth.
- [ ] Keyboard and error states work without a framework or new dependency.

**Verification:** fixtures/static route tests, full pytest, two-thread browser
smoke with manual panel changes.

### Checkpoint B: User-controlled personalisation

- [ ] Full pytest passes.
- [ ] The Profile panel is inspectable, editable, and permanently deletable.
- [ ] A deleted item is absent from a subsequent relevant request.
- [ ] Raw source claims still require native citations.

## Task 7: Complete deterministic Phase 4 regression coverage

**Dependencies:** Checkpoint B.
**Files likely touched:** `tests/test_profile.py`, `tests/test_storage.py`,
`tests/test_chat_service.py`, `tests/test_server.py`, `tests/fixtures/`.

**Acceptance criteria:**

- [ ] Cover cross-thread recall, relevance/non-relevance, edit, delete,
  contradiction, provenance, source-authority separation, budget, migration,
  thread deletion, historic replay after profile deletion, tool idempotence,
  and all Phase 2 regressions without paid calls.
- [ ] Secret/diff checks confirm no local profile runtime, transcript, or API
  secret enters Git.

**Verification:** `.\.venv\Scripts\python -m pytest -q`; local browser smoke;
Git diff/secret review.

## Task 8: Run Theo's Phase 4 human quality gate

**Dependencies:** Task 7.
**Files likely touched:** `docs/phase-4-evaluation.md`,
`docs/phase-4-acceptance.md` only after Theo's decision.

**Acceptance criteria:**

- [ ] The private evaluation covers every Phase 4 scenario in the approved
  design, including personalised research guidance and source-authority
  separation.
- [ ] Theo records the pass/fail decision; routine tests and browser smoke do
  not make paid requests.

**Verification:** full pytest first; Theo performs the local human evaluation.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Casual language becomes hidden memory | Explicit command/UI writes; at most one visible tentative proposal; confirmed-only injection. |
| Old preference leaks into advice | Versioned supersession, conflict exclusion, permanent delete, and selector tests. |
| Profile turns into source authority | Mark data as user context; preserve raw File Search/citation path; add false-attribution regression. |
| Profile bloats Sol context | Local relevance selection, dedupe, six-item/1,200-character cap, and diagnostics. |
| Thread deletion damages global state | Separate ownership and atomic origin-unavailability update tests. |
| Phase 3 complexity leaks in | Base/diff review before each Phase 4 implementation checkpoint. |

## Approval Gate

Do not implement any task until Theo approves this plan and
[todo.md](todo.md). After approval, complete tasks in order, test and commit
each coherent slice, push the feature branch, and stop at Task 8 for Theo's
human decision.
