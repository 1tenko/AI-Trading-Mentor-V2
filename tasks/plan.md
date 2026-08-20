# Implementation Plan: Phase 2 Unified Trading Mentor Foundation

**Status:** Proposed — requires Theo's approval before implementation.

## Scope

Implement only the approved Phase 2 design at
[2026-08-20-trading-mentor-phase-2-design.md](../docs/superpowers/specs/2026-08-20-trading-mentor-phase-2-design.md).

The work makes the proven mentor a reliable personal chat application. It does
not alter the Phase 1 intelligence architecture and does not begin Phase 3 or
later capabilities.

## Phase 1 Closure and Branch

- Phase 1 human Intelligence Proof: **passed** by Theo.
- Acceptance record: [phase-1-acceptance.md](../docs/phase-1-acceptance.md).
- Implementation branch: feature/phase-2-unified-mentor.
- Base contract: local replay state, GPT-5.6 Sol, Responses API, store=false,
  native File Search, raw Jacob source authority, and loopback-only serving.

## Verified Integration Constraints

- GPT-5.6 Sol remains OpenAI's frontier model. It supports high, xhigh, and
  max reasoning effort; Pro is an independent reasoning mode. The plan retains
  the existing evaluated controls instead of automatically escalating them.
  [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- When local code manages conversation state with store=false, OpenAI documents
  preserving previous user inputs and every response output item, including
  encrypted reasoning items, for later turns. This remains the raw replay
  record; it must never be returned to the browser.
  [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- Native File Search results are absent by default unless
  file_search_call.results is requested through include. The response item
  contains the model's File Search queries, and the tool supports a
  max_num_results setting. Phase 2 retains all returned results without custom
  ranking.
  [OpenAI File Search guide](https://developers.openai.com/api/docs/guides/tools-file-search)

## Architecture Decisions

1. **Dual local records:** retain raw thread_items for stateless replay and add
   a browser-safe display-turn projection. Do not make the browser parse or
   receive encrypted reasoning state.
2. **Physical local deletion:** delete a thread and all thread-owned rows in
   one SQLite transaction. Never touch sources, source registry settings, local
   transcripts, OpenAI Files, or the shared vector store.
3. **Minimal unified boundary:** add one private request-composition seam for
   an active Jacob-source capability. It supplies request policy/context only;
   it does not create a capability registry, future tool, or another bot.
4. **Two independent axes:** research depth controls source-research policy;
   reasoning effort/mode controls GPT-5.6 configuration. Auto depth resolves
   via transparent local intent rules and is recorded. No automatic effort or
   Pro escalation occurs.
5. **Static frontend retained:** the existing static UI can satisfy the
   approved flows. No framework or dependency is planned.

## Dependency Graph

    Task 1: migrate storage + atomic deletion primitive
      -> Task 2: persist safe display turns and historical configuration
          -> Task 3: safe restore/delete HTTP API
              -> Checkpoint A
                  -> Task 4: unified turn composition + research depth
                      -> Task 5: evidence and diagnostics aggregation
                          -> Checkpoint B
                              -> Task 6: restored conversation UI
                                  -> Task 7: controls and compact disclosure UI
                                      -> Checkpoint C
                                          -> Task 8: deterministic regressions
                                              -> Task 9: explicit human quality checkpoint

Tasks are deliberately sequential because the shared storage and API contracts
must settle before static UI work begins. No parallel implementation is planned.

## Task List

### Module: conversation-lifecycle

## Task 1: Add an idempotent display-turn migration and deletion primitive

**Purpose:** Extend SQLite with the minimum display-turn structure needed to
restore Phase 1 conversations, backfill legacy threads without losing raw replay
items, and provide one atomic local deletion operation.

**Dependencies:** None.

**Files/components likely affected:**

- src/mentor/storage.py
- tests/test_storage.py
- tests/fixtures/phase1_thread_state.json

**Acceptance criteria:**

- [ ] Initializing a Phase 1-shaped database creates/backfills display turns
  idempotently while retaining the original chronological raw item sequence.
- [ ] A display turn identifies its user content, answer Markdown, evidence,
  diagnostics/configuration, completion state, and raw replay-item positions.
- [ ] One storage deletion operation removes every thread-owned row
  transactionally while source registry rows and vector-store settings survive.

**Automated verification:**

- [ ] Focused storage tests cover fresh initialization, legacy migration rerun,
  transaction rollback/failure behavior, and source/settings survival.
- [ ] Full suite: .\.venv\Scripts\python -m pytest -q.

**Browser verification:** None; this is a local storage contract.

**Estimated scope:** Medium.

## Task 2: Persist new turns as safe display projections without changing replay

**Purpose:** Make each new completed or incomplete response write both its
existing raw replay state and a safe display projection containing the actual
historical configuration that produced it.

**Dependencies:** Task 1.

**Files/components likely affected:**

- src/mentor/chat_service.py
- src/mentor/storage.py
- tests/test_chat_service.py

**Acceptance criteria:**

- [ ] A streamed completed or incomplete response stores a matching display
  turn with citations, all returned evidence, diagnostics, and exact historical
  model/reasoning/research settings.
- [ ] Replay input remains the complete raw output sequence required by the
  Responses API, including encrypted reasoning items when present.
- [ ] No browser-facing representation includes encrypted reasoning content or
  opaque raw response items.

**Automated verification:**

- [ ] API-shaped fixtures assert display projection, incomplete handling, and
  unchanged raw replay input.
- [ ] Full suite: .\.venv\Scripts\python -m pytest -q.

**Browser verification:** None; HTTP exposure follows Task 3.

**Estimated scope:** Medium.

## Task 3: Expose safe thread restoration and permanent-delete routes

**Purpose:** Add the smallest loopback API surface for loading a thread's safe
timeline and permanently deleting one local conversation.

**Dependencies:** Tasks 1–2.

**Files/components likely affected:**

- src/mentor/server.py
- src/mentor/storage.py
- tests/test_server.py

**Acceptance criteria:**

- [ ] GET /api/threads/{id} returns chronological display turns only and never
  contacts OpenAI or reveals raw/encrypted replay state.
- [ ] DELETE /api/threads/{id} uses the storage transaction, returns a clear
  success/not-found result, and does not affect sources or vector-store state.
- [ ] Existing list/create/message/source routes and loopback-only binding
  continue to behave as before.

**Automated verification:**

- [ ] HTTP tests cover restore, malformed/missing IDs, delete persistence, and
  absence of encrypted reasoning/API keys in JSON.
- [ ] Full suite: .\.venv\Scripts\python -m pytest -q.

**Browser verification:** Request one existing thread endpoint from the local
browser and confirm it returns no raw reasoning content.

**Estimated scope:** Medium.

### Checkpoint A: Conversation storage and API contract

- [ ] All tests pass.
- [ ] A representative Phase 1 thread migrates and can be read through the
  safe timeline route.
- [ ] Deleting a representative thread removes it after reread/reload while
  the Jacob source registry and vector-store setting remain intact.
- [ ] No browser route exposes encrypted reasoning state.

### Module: mentor-orchestration

## Task 4: Add minimal unified turn composition and research-depth policy

**Purpose:** Separate current Jacob source-research policy from future
capability attachment without implementing a generic registry or any future
capability. Add Auto, Normal, Deep, and Exhaustive depth handling independently
of existing reasoning controls.

**Dependencies:** Checkpoint A.

**Files/components likely affected:**

- src/mentor/chat_service.py
- src/mentor/prompts.py
- tests/test_chat_service.py

**Acceptance criteria:**

- [ ] One server-owned turn-composition path builds the existing Jacob
  instructions, native File Search tool, include fields, and research-depth
  policy; no user-facing bot or future capability is created.
- [ ] Auto deterministically resolves to Normal, Deep, or Exhaustive from
  transparent intent criteria; an explicit manual depth cannot be downgraded.
- [ ] Research depth is stored separately from reasoning effort/mode and does
  not automatically raise effort or enable Pro.

**Automated verification:**

- [ ] Fixtures assert policy resolution, request composition, preserved native
  File Search configuration, and unchanged provenance/exhaustive safeguards.
- [ ] Full suite: .\.venv\Scripts\python -m pytest -q.

**Browser verification:** None; UI control follows Task 7.

**Estimated scope:** Medium.

## Task 5: Retain compact evidence and truthful usage diagnostics

**Purpose:** Record the native research details needed for compact display:
returned evidence count, cited count, File Search calls/queries/results, and
known response usage—without inventing total platform cost.

**Dependencies:** Task 4.

**Files/components likely affected:**

- src/mentor/chat_service.py
- src/mentor/storage.py
- tests/test_chat_service.py

**Acceptance criteria:**

- [ ] Each display turn retains all returned source evidence and enough
  aggregate metadata to show research/citation counts.
- [ ] Historical diagnostics retain requested/effective depth, model,
  effort/mode, status, latency, available tokens, and clearly labelled
  text-token estimate.
- [ ] Unknown File Search/platform charges remain unknown; no custom ranking,
  summarization, or quote rewriting is introduced.

**Automated verification:**

- [ ] Fixtures cover multiple File Search calls, absent usage fields, and
  historical diagnostics fidelity.
- [ ] Full suite: .\.venv\Scripts\python -m pytest -q.

**Browser verification:** Inspect the safe timeline JSON for counts and all
evidence records; verify no raw reasoning item is present.

**Estimated scope:** Medium.

### Checkpoint B: Unified mentor policy and observability contract

- [ ] All tests pass.
- [ ] The existing Phase 1 exhaustive-query policy still requires a
  complementary omission/falsification search.
- [ ] Auto/manual depth, effort, and mode are independently persisted.
- [ ] Evidence and usage metadata are complete enough for the approved UI but
  do not expose reasoning state or misstate costs.

### Module: chat-foundation-ui

## Task 6: Restore conversations, switching, reload, titles, and delete flow

**Purpose:** Use the safe timeline API in the existing static chat page so
saved conversations faithfully reappear and can be deleted through a restrained
confirmed action.

**Dependencies:** Checkpoint B.

**Files/components likely affected:**

- src/mentor/static/index.html
- src/mentor/static/app.js
- src/mentor/static/app.css

**Acceptance criteria:**

- [ ] Selecting a saved thread and reloading the page render historical Theo
  and Mentor messages, Markdown, citations/evidence, diagnostics, and
  incomplete state in chronological order.
- [ ] Sidebar titles use the persisted first meaningful question and switch
  reliably without clearing historical content.
- [ ] A keyboard-accessible delete affordance confirms intent, removes the
  conversation immediately on success, and leaves sources untouched.

**Automated verification:**

- [ ] Existing server tests continue to pass; add browser-safe route fixtures
  needed by the static flow.
- [ ] Full suite: .\.venv\Scripts\python -m pytest -q.

**Browser verification:** Create two chats, add a follow-up to one, switch both
ways, reload, delete one with confirmation, reload again, and verify the other
still restores.

**Estimated scope:** Medium.

## Task 7: Add research-depth control and compact historical disclosures

**Purpose:** Extend the static UI—not the intelligence architecture—with an
advanced research-depth control, historical configuration display, compact
evidence disclosure, and the approved NaN. diagnosis.

**Dependencies:** Task 6.

**Files/components likely affected:**

- src/mentor/static/index.html
- src/mentor/static/app.js
- src/mentor/static/app.css
- tests/test_server.py

**Acceptance criteria:**

- [ ] Auto/Normal/Deep/Exhaustive is sent only for future turns and does not
  relabel historical turns; the existing effort/mode controls remain separate.
- [ ] Evidence is collapsed by default with cited results first, a compact
  researched/cited count, and an explicit way to reveal all retained evidence.
- [ ] Live UI and a saved fixture determine whether NaN. is an application
  defect; only a reproducible in-app defect receives a minimal code fix and
  regression.

**Automated verification:**

- [ ] HTTP/static asset tests pass and fixture coverage protects any confirmed
  NaN. rendering fix.
- [ ] Full suite: .\.venv\Scripts\python -m pytest -q.

**Browser verification:** At desktop and mobile widths, verify controls,
streaming, Markdown tables, collapsed evidence/diagnostics, no console errors,
and no horizontal sidebar-button pile.

**Estimated scope:** Medium.

### Checkpoint C: Persistent-chat user flow

- [ ] All tests pass.
- [ ] A new streamed answer and a restored historical answer display their own
  distinct historical settings and evidence.
- [ ] Browser deletion persists through reload without affecting a different
  conversation or shared source access.
- [ ] The UI remains a static, responsive personal chat—not a dashboard.

### Module: phase-2-regression

## Task 8: Complete deterministic Phase 2 regression coverage

**Purpose:** Consolidate migration, lifecycle, provenance, security, and
semantic behavior into small deterministic fixtures so routine tests do not
make paid model requests.

**Dependencies:** Checkpoint C.

**Files/components likely affected:**

- tests/test_storage.py
- tests/test_chat_service.py
- tests/test_server.py
- tests/fixtures/

**Acceptance criteria:**

- [ ] Tests cover create/list/title/restore/reload/switch/delete, shared-source
  survival, raw replay continuity, historical configuration fidelity, and
  incomplete responses.
- [ ] Tests retain the Phase 1 semantic fixtures: SMT, TPD, all
  reversion-level alignments, exhaustive SMT teaching, false attribution, and
  correction/follow-up.
- [ ] Tests cover loopback-only routing, API-key secrecy, source restrictions,
  provenance, Auto/manual depth, and the resolved NaN. finding.

**Automated verification:**

- [ ] Full suite: .\.venv\Scripts\python -m pytest -q.
- [ ] Diff check and secret scan before the task commit.

**Browser verification:** Run the defined smoke flow once against a local
server; do not send paid model requests during ordinary regression runs.

**Estimated scope:** Medium.

## Task 9: Run the explicit Phase 2 human quality checkpoint

**Purpose:** Prepare and run a small paid browser evaluation only after all
deterministic checks pass. Theo, not the agent, decides whether Phase 2 passes.

**Dependencies:** Task 8.

**Files/components likely affected:**

- docs/phase-2-evaluation.md
- README.md

**Acceptance criteria:**

- [ ] The worksheet combines persistent-chat lifecycle checks with the compact
  Phase 1 semantic regression prompts and records configuration/observability.
- [ ] It explicitly checks normal versus exhaustive research behavior, restored
  history fidelity, permanent deletion boundaries, and evidence/diagnostics UX.
- [ ] Private full transcripts, runtime data, and API secrets remain outside
  Git; Theo records the final pass/fail decision.

**Automated verification:**

- [ ] Full suite: .\.venv\Scripts\python -m pytest -q before the real run.
- [ ] No paid API request is added to pytest or a routine browser smoke test.

**Browser verification:** Theo performs the approved paid/local human
checkpoint and decides Phase 2 pass/fail.

**Estimated scope:** Small.

### Final checkpoint: Await Theo's Phase 2 decision

- [ ] All deterministic tests and browser smoke checks pass.
- [ ] The explicit paid quality checkpoint is complete.
- [ ] The completed branch is committed and pushed.
- [ ] Theo has made the human acceptance decision.
- [ ] Stop. Do not start Phase 3, merge to main, or add future capabilities.

## Migration and Data Risks

| Risk | Mitigation |
|---|---|
| Legacy thread grouping cannot associate all historical diagnostics | Preserve raw items; associate diagnostics in recorded response order; explicitly label genuinely absent fields unavailable. |
| Partial deletion causes a deleted chat to reappear | Use one SQLite transaction, enabled foreign keys, explicit dependent-row deletes, and reread/reload tests. |
| Browser-safe display data drifts from replay data | Persist both in one service finalization path and test exact historical configuration/evidence. |
| Auto depth creates surprise cost or weak research | Use transparent deterministic baselines, manual override, stored effective depth, and actual native-search counts. |
| Evidence disclosure hides critical sources | Keep all original returned results; show cited results first; expose explicit expand-all. |
| A planned UI change becomes a framework migration | Stop and ask Theo; the static UI is the approved approach. |

## ADRs

No standalone ADR is created at planning time. The approved Phase 2 design is
the binding decision record for dual records, physical deletion, independent
research depth, the unified capability seam, and retention of the static UI.
Creating duplicate ADR files now would add documentation without a new decision.

## Approval Gate

Do not implement any task until Theo approves this plan and
[todo.md](todo.md). On approval, work tasks in order, commit/push each
significant coherent slice, and stop at Task 9 for Theo's human decision.
