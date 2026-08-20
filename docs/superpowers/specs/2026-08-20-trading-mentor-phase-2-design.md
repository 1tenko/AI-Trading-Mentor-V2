# Phase 2 Design: Unified Trading Mentor Foundation

**Status:** Proposed — requires Theo's approval before planning or implementation.

## Objective

Turn the proven Phase 1 intelligence proof into Theo's reliable, private,
persistent conversational Trading Mentor. Phase 2 improves the application
foundation without replacing its intelligence architecture:

    Theo
      -> private browser chat
      -> local application state and server-owned mentor orchestration
      -> GPT-5.6 Sol through the Responses API
      -> OpenAI-native File Search over raw Jacob transcripts

The frontier model remains the teacher and reasoning engine. Native File Search
locates original evidence. The raw Jacob corpus remains the factual authority.
Phase 2 is successful when Theo can reliably return to, inspect, and remove
private conversations while normal questions remain economical and deep source
research remains available when it is warranted.

## Approved Capability Map

| Module id | Responsibility | Depends on |
|---|---|---|
| conversation-lifecycle | Durable thread timeline, history restoration, titles, physical deletion, migration of existing Phase 1 chats | — |
| mentor-orchestration | Unified mentor turn boundary, research-depth policy, provenance-preserving diagnostics and raw evidence retention | conversation-lifecycle |
| chat-foundation-ui | Reliable sidebar switching, delete affordance, restored Markdown/evidence/diagnostics, compact research presentation | conversation-lifecycle, mentor-orchestration |
| phase-2-regression | Deterministic API-shaped tests, semantic regression set, browser/human verification checklist | all above |

Implementation order is conversation-lifecycle -> mentor-orchestration ->
chat-foundation-ui -> phase-2-regression. This is a design boundary, not an
implementation task list.

## Scope

### Included

- Faithful restoration of persisted conversations and their past-turn metadata.
- Permanent local deletion of a selected conversation and all state it owns.
- Useful deterministic conversation titles.
- One unified, server-owned mentor extension boundary for later knowledge and
  tools.
- A separate research-depth policy and advanced override, independent from
  model reasoning effort and mode.
- Per-turn and simple per-thread usage/research observability.
- Compact evidence disclosure that preserves all returned original evidence.
- Regression coverage for the existing Phase 1 intelligence behavior and the
  new persistent-chat behavior.

### Explicitly excluded

- Strategy Lab, strategy projects, experiments, backtest import/analysis, or
  deterministic statistics tools.
- Trader Profile, long-term memory, user empirical evidence, user decisions,
  or mindset knowledge.
- Jacob corpus assimilation or an AI-written compiled knowledge layer.
- Additional source libraries or user source-management UI.
- Custom embeddings, custom RAG/ranking, local models, model fine-tuning,
  Agents SDK migration, accounts, sharing, deployment, brokers, or execution.
- A frontend framework migration or visual redesign unrelated to the
  persistent-chat foundation.

## Preserved Phase 1 Contract

Phase 2 must preserve all of the following:

- GPT-5.6 Sol as the frontier path, subject only to an explicit later
  quality/cost decision;
- direct Responses API calls with store=false;
- local ownership and replay of response output items, including encrypted
  reasoning state when returned;
- OpenAI-native File Search and raw Jacob transcript authority;
- multi-pass native search for substantial/deep/exhaustive research;
- provenance labels: Direct source teaching, Source synthesis, AI hypothesis,
  Unsupported; and the reserved future categories User empirical evidence and
  User decision;
- streaming, citations, immutable local transcript access, loopback-only
  serving, local SQLite, reasoning controls, and usage diagnostics.

No Phase 2 feature may turn a synthesis, hypothesis, or future user evidence
into a direct Jacob teaching.

## Current Baseline and Design Direction

Phase 1 already stores each thread's raw user and Responses output items in
SQLite, in order. That is the continuity/replay record. It also stores
per-response diagnostics and returned File Search evidence.

The current browser sidebar only selects a thread and clears the displayed
conversation; it has no thread-history read route or client-side restoration
path. Phase 2 adds a display timeline derived from the persisted record. It
does not change how the model receives the raw continuation state.

The application remains a small Python server, SQLite database, and static
browser page. No framework is justified by this scope.

## Tech Stack

- Python 3 application server and standard-library HTTP/SSE serving.
- SQLite for private local conversations, raw replay state, display turns,
  diagnostics, and source registry metadata.
- Official OpenAI Python SDK, Responses API, GPT-5.6 Sol, and native File
  Search over the existing Jacob vector store.
- Static HTML, CSS, and JavaScript with the existing local Markdown sanitizer.

## Architecture

    Browser
      -> loopback HTTP/SSE server
          -> conversation lifecycle
              -> local SQLite: display turns + raw replay items + diagnostics
          -> unified mentor turn boundary
              -> active Phase 2 capability: Jacob source research
              -> Responses API / GPT-5.6 Sol / native File Search
          -> registered local transcript reader

### Unified mentor extension boundary

There is one user-facing mentor. Phase 2 defines, but does not yet implement,
a server-owned capability contribution boundary used when constructing a mentor
turn. A future capability may contribute only:

- server-controlled instructions or context;
- approved Responses tools and include fields;
- local state relevant to the current turn;
- provenance category and source-scope metadata;
- display metadata needed to explain which capability supplied evidence.

It may not independently write the final answer, replace the frontier model,
or bypass raw-source verification. The only active capability in Phase 2 is
the existing Jacob source-research path. Future compiled Jacob knowledge,
Trader Profile, Strategy Projects, Backtest Lab, and mindset sources attach at
this boundary rather than becoming separate bots or rewriting conversation
storage.

## Conversation Lifecycle

### Display and replay records

SQLite remains the local source of truth. Phase 2 retains the existing raw
thread_items sequence for stateless Responses replay and adds an explicit
display-turn projection for each completed or incomplete response.

Each display turn records, at minimum:

- owning thread and chronological turn number;
- user message text;
- rendered-answer source text (Markdown source, never pre-rendered HTML);
- citations and all returned evidence references/excerpts;
- response ID, completion/incomplete state, and incomplete reason when any;
- the exact historical model, reasoning effort, reasoning mode, requested and
  effective research depth, latency, token fields, and text-cost estimate;
- positions of the corresponding raw replay items.

The existing response diagnostics become associated with their specific display
turn. The browser never receives raw encrypted reasoning content or opaque
Responses items; these remain local server-side replay state.

The schema migration must backfill display turns from existing chronological
thread_items and diagnostics. Where an old response lacks a field that was not
retained in Phase 1, the restored UI must say it is unavailable rather than
inventing it. Migration must be idempotent and preserve the raw sequence.

### Restoration behavior

GET /api/threads/{id} returns one safe display timeline in chronological order:
Theo messages, Mentor Markdown, citations/evidence, diagnostics, and
incomplete state. It never triggers an OpenAI request.

Selecting a sidebar conversation or reloading the page must render that
timeline using the same safe Markdown path as a new streamed answer. Each
historical Mentor turn displays its own evidence disclosure and diagnostics.
Historical configuration is display-only: the currently selected controls
apply only to the next user message and must never relabel an old response.

### Titles

On the first non-empty user turn, persist a short, whitespace-normalized title
derived from that question. Do not spend an additional model call generating a
title. Preserve the title field as an editable future capability; title editing
is not required in Phase 2.

### Permanent deletion

DELETE /api/threads/{id} permanently removes only the selected thread's local
state in one SQLite transaction: the thread record, display turns, raw user and
assistant/Responses items, encrypted reasoning replay state, turn-owned
citations/evidence, diagnostics/usage records, and other thread-owned local
metadata. Foreign-key behavior must be enabled and the deletion path must also
explicitly cover migrated legacy rows, so a deleted thread cannot partially
reappear after a reload.

Deletion never removes local Jacob transcripts, source-registry rows, OpenAI
Files, the Jacob vector store, or another shared knowledge resource. The API
returns success only after the local transaction commits; a missing thread is a
clear not-found response. The static UI exposes a restrained per-thread delete
control that is reachable by keyboard and asks for confirmation before issuing
the request. On success it immediately removes the sidebar entry and opens a
safe empty/new-chat state.

## Research Depth and Reasoning Controls

Research depth controls source-research breadth. Reasoning effort/mode controls
model thinking configuration. They are separate inputs, separate persisted
historical fields, and separate diagnostics.

| Depth | Intended use | Native File Search policy |
|---|---|---|
| Normal | Definition, narrow question, ordinary follow-up | One focused search when fresh evidence is needed; further search only when evidence is insufficient. |
| Deep | Difficult explanation, relationship, verification, or comparison | Multiple model-chosen searches when useful, with different queries/source angles. |
| Exhaustive | “all”, “everything”, “complete”, corpus-wide comparison, serious source research | Candidate research plus complementary omission/falsification research before a completeness claim; no unbounded loop and a four-pass ceiling. |
| Strategy research | Future hypothesis/strategy decisions | Reserved only; no Phase 2 control or implementation. |

The UI defaults to **Auto** and also offers an advanced manual override for
Normal, Deep, or Exhaustive. In Auto, a small transparent intent policy gives
the model a minimum research policy: explicit exhaustive language and
corpus-wide comparisons select Exhaustive; verification, difficult
relationships, and comparisons select Deep; other questions select Normal.
The policy does not retrieve, rank, summarize, or answer from sources. The
frontier model still decides whether evidence is sufficient and formulates the
native File Search queries. An explicit user selection cannot be silently
downgraded.

The existing high / xhigh / max and standard / pro controls remain advanced
model controls. They default to the existing Phase 1 baseline. A higher
research depth must not silently increase reasoning effort or select Pro mode,
and a higher reasoning effort must not falsely claim deeper corpus research.
Implementation must recheck the current supported Responses API model controls
before changing request payloads.

## Evidence and Diagnostics Experience

The Mentor answer remains primary. Each past or newly completed Mentor turn
shows a collapsed evidence summary such as:

    56 evidence results researched · 4 cited

Expanding it shows cited evidence first, then a restrained initial subset of
other returned results, with a user-controlled way to reveal all retained
results. Every displayed item preserves original filename, year, returned
excerpt, source metadata, and the existing read-only full-transcript link.
Application code must not rewrite excerpts into AI-generated quotes or discard
the underlying returned evidence.

Diagnostics remain collapsed by default and show per response:

- model, reasoning effort and mode;
- requested and effective research depth;
- completion/incomplete status and reason;
- latency and available token fields;
- estimated text-token cost, explicitly marked as excluding File Search fees
  or any unavailable platform charges;
- native File Search call count, query count where returned, and result count.

If all necessary fields are retained, a thread-level summary may total response
count, available tokens, known text-cost estimates, elapsed latency, and native
search calls. It must label unknown charges as unknown rather than presenting a
false total cost.

## API, Server, and Frontend Implications

The loopback server keeps the existing create/list/message/source routes and
adds only the minimum persistent-chat routes:

- GET /api/threads/{id} — safe display timeline for restoration;
- DELETE /api/threads/{id} — confirmed-by-UI permanent local deletion.

The existing streaming message route persists a display turn with the raw
replay record on completion or incompletion. The browser requests a timeline
when selecting a thread and after reload, renders it through the existing safe
Markdown sanitizer, and uses the historical per-turn configuration for
historical diagnostics. It retains the current static UI, responsive layout,
composer, loading/error states, and visible distinction between Theo and
Mentor.

The reported NaN. list issue is a diagnosis requirement, not a presumed code
fix. Phase 2 must reproduce it in the live UI and with a saved Markdown/API
fixture. If it is a renderer defect, fix the smallest renderer/data conversion
cause and add a regression. If it occurs only in copying/exporting outside the
application, document that finding and leave working in-app Markdown unchanged.

## Commands

    # Install and test
    .\.venv\Scripts\python -m pip install -e ".[dev]"
    .\.venv\Scripts\python -m pytest -q

    # Run the private application
    .\.venv\Scripts\python -m mentor

    # Browser verification against the loopback server
    npx --yes agent-browser open http://127.0.0.1:8765

No paid live evaluation is part of the normal automated test command.

## Project Structure

    docs/superpowers/specs/  Approved Phase designs
    src/mentor/storage.py   SQLite lifecycle, migration, deletion, display records
    src/mentor/chat_service.py
                             Responses turn construction and persistence
    src/mentor/server.py    Loopback API/SSE and safe thread timeline routes
    src/mentor/prompts.py   Unified mentor and research-depth policy instructions
    src/mentor/static/      Static browser chat, safe Markdown, styles
    tests/                  Storage, service, server, and deterministic fixtures
    data/                   Ignored private SQLite runtime state

## Code Style

Keep the existing small, explicit Python and static-JavaScript style. Storage
mutations that own multiple records use one transaction; browser rendering uses
Markdown source and the existing sanitizer; server responses expose only
browser-safe display data.

    with self._connect() as connection:
        connection.execute("BEGIN")
        connection.execute("DELETE FROM thread_turns WHERE thread_id = ?", (thread_id,))
        connection.execute("DELETE FROM threads WHERE id = ?", (thread_id,))

The example illustrates the required atomic ownership boundary; the final
implementation must also cover raw items and diagnostics and use the selected
foreign-key/migration strategy.

## Testing and Quality Strategy

Automated tests use mocked API-shaped Responses fixtures; they do not make paid
OpenAI calls. Add deterministic coverage for:

- create, title, list, restore, reload, switch, and permanent delete flows;
- historical Markdown, citations/evidence, diagnostics, incomplete state, and
  historical reasoning/research configuration;
- raw replay continuity after restoration and absence of encrypted reasoning
  state from browser JSON;
- delete transaction integrity and the fact that sources/vector-store settings
  survive deletion;
- streaming, controls, Auto/manual research-depth selection, and provenance;
- exhaustive complementary-search policy, unsupported attribution, and the
  Phase 1 semantic set: SMT, TPD, all reversion-level alignments, exhaustive
  SMT teaching, false attribution, and correction/follow-up;
- loopback-only binding, no API-key exposure, source access restrictions, and
  the NaN. diagnosis/regression when applicable.

Browser checks cover restored thread selection, page reload, delete confirmation,
sidebar behavior at desktop width, responsive layout, streaming, collapsed
evidence/diagnostics, Markdown tables, controls, console errors, and network
requests. Real paid evaluations are reserved for explicit Phase 2 quality
checkpoints, not routine test runs.

## Boundaries

- **Always:** preserve Phase 1 intelligence architecture; keep raw transcript
  authority and provenance distinctions; persist historical response
  configuration; use transactional local deletion; retain evidence; run the
  automated suite before commits; keep the server loopback-only.
- **Ask first:** add a dependency or frontend framework; change the model,
  OpenAI retention behavior, source scope, privacy model, database technology,
  external service, or cost-bearing recurring infrastructure; add a new
  capability beyond the one extension boundary.
- **Never:** delete shared Jacob/source resources when deleting a chat; expose
  API keys or encrypted reasoning state to the browser or Git; introduce custom
  RAG/ranking or a local-model answer path; build Strategy Lab, profile,
  compiled corpus, Daye, or mindset features in Phase 2; silently reclassify
  provenance.

## Success Criteria

1. The approved Phase 1 intelligence behavior remains intact.
2. Existing and new conversations reopen after switching and reload with their
   chronology, Markdown, evidence, diagnostics, and historical configuration.
3. Deleting a conversation permanently and atomically removes all its local
   state while preserving shared sources and remote knowledge resources.
4. Conversation titles are useful and do not remain generic after a first
   substantive question.
5. Normal questions do not receive exhaustive corpus treatment by default;
   Deep and Exhaustive research retain appropriate native-search rigor.
6. Per-response research, usage, latency, and cost information is inspectable
   without dominating the chat.
7. Evidence remains faithful and accessible without dumping large research
   result sets into the main conversation.
8. One server-owned mentor extension boundary is defined without adding future
   capability implementations or separate bots.
9. Automated regressions and Theo's Phase 2 human checks pass.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A migration loses replay state or historical diagnostics | Preserve raw items, use idempotent migration, and test a real Phase 1-shaped database fixture. |
| Deletion leaves orphaned rows or removes shared knowledge | One transaction, enabled foreign keys, explicit ownership tests, and source/settings survival tests. |
| Auto depth becomes hidden cost behavior | Use transparent intent rules, store requested/effective depth, retain manual override, and show actual search counts. |
| Diagnostics imply a total cost that is not available | Label the text estimate and unavailable File Search/platform charges precisely. |
| Compact evidence hides important evidence | Preserve all returned results, cite first, and provide an explicit expand-all path. |
| Future tools cause another mentor split | Keep capability contributions server-side under the single unified mentor boundary. |

## Open Questions

None block approval. Before implementation, the planning/source-verification
step must confirm the then-current OpenAI support for the retained model
reasoning controls and File Search response fields. If that verification would
change the preserved Phase 1 contract, it requires Theo's approval first.

## ADR Candidates

1. Retain raw replay items plus a browser-safe display-turn projection.
2. Use permanent local, transactional conversation deletion with shared
   knowledge explicitly out of scope.
3. Keep research depth separate from model reasoning configuration, with Auto
   plus an advanced manual override.
4. Use server-owned capability contributions for future domains rather than
   separate user-facing bots.
5. Retain the static frontend unless a future approved architecture decision
   shows that it cannot meet a concrete requirement.

## Approval Gate

Do not create an implementation plan, task list, schema migration, API route,
UI change, or future capability until Theo explicitly approves this Phase 2
design. Approval authorizes planning only; it does not authorize Phase 3+
scope or merging to main.
