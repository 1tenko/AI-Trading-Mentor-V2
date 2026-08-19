# Implementation Plan: Phase 1 Intelligence Proof

## Scope

Implement only the approved Phase 1 proof from
[`docs/superpowers/specs/2026-08-19-trading-mentor-phase-1-design.md`](../docs/superpowers/specs/2026-08-19-trading-mentor-phase-1-design.md).
`SPEC.md` supplies the permanent product direction but does not authorize
Phase 2+ work.

The only product question this plan answers is whether Theo can hold a
high-quality, source-grounded, multi-turn browser conversation about Jacob
Speculates' 2025–2026 transcripts.

## Verified Integration Decisions

- **Model:** start with `gpt-5.6-sol`, OpenAI's current flagship model for
  complex professional work, and use the Responses API. Test `xhigh` reasoning
  only if the quality gate shows that `high` is insufficient. [Model catalog](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- **Knowledge search:** use the hosted `file_search` Responses tool over one
  vector store containing raw transcripts. It performs semantic and keyword
  search; the model decides when to call it. [File Search guide](https://developers.openai.com/api/docs/guides/tools-file-search)
- **Citations:** parse the returned file-citation annotations and request file
  search results when a source excerpt is needed. [File Search citations](https://developers.openai.com/api/docs/guides/tools-file-search#file-citations)
- **Conversation state:** store threads and messages locally in SQLite and send
  the selected thread's explicit history on each turn with `store=false`.
  This avoids creating remote response history while retaining local control.
  [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- **Streaming:** use the Responses API's `stream=True` server-sent event stream
  and relay it to the local browser. [Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses)
- **No Agents SDK in Phase 1:** OpenAI recommends direct Responses calls when
  the workflow is short-lived and the application owns state/tool handling;
  Phase 1 has no multi-agent orchestration or custom tools. [Agents SDK guidance](https://openai.github.io/openai-agents-python/)
- **Retention:** API inputs are not used for training without opt-in, but files
  and vector stores persist until deleted. The importer must record remote IDs
  and document cleanup. [Data controls](https://developers.openai.com/api/docs/guides/your-data)

## Local Findings

- Python 3.14.7 is installed.
- The current transcript directory contains 150 `.txt` files: 22 under `2025`
  and 128 outside it, treated as 2026 source material.

## Deliberate Simplifications

- One local server bound to `127.0.0.1`; no accounts, deployment, or network
  sharing.
- Python standard library serves the browser page and SQLite handles local
  state. The only Phase 1 runtime package is the official `openai` SDK.
- Import is a command over the existing transcript directory, not an upload UI.
- A citation opens a read-only local transcript view; no document viewer,
  source-management screen, or custom retrieval ranking is added.

## Dependency Graph

```text
Project setup
  -> local storage + source importer
      -> grounded chat service
          -> local browser chat
              -> manual quality gate
```

## Commands After Task 1

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m mentor.import_jacob "D:\courses\Jacob Speculates 2026\Transcripts"
.\.venv\Scripts\python -m mentor
.\.venv\Scripts\python -m pytest
```

## Task List

### Task 1: Create the minimal local application foundation

**Description:** Add the Python package, isolated environment metadata, test
runner, and documented local configuration. Keep the API key in `.env` only;
the application must refuse to start without it.

**Acceptance criteria:**

- [ ] The project installs in a virtual environment with the official OpenAI
  Python SDK and a test dependency.
- [ ] `.env.example` documents the required API key without containing one.
- [ ] A focused test command runs successfully before any OpenAI call is made.

**Verification:**

- [ ] `python -m pytest` passes.
- [ ] Starting without `OPENAI_API_KEY` produces a clear local error.

**Dependencies:** None

**Files likely touched:** `pyproject.toml`, `.env.example`, `src/mentor/config.py`,
`tests/test_config.py`, `README.md`

**Estimated scope:** Medium

### Task 2: Register and import the raw Jacob transcript library

**Description:** Create the local SQLite schema and one repeatable command that
walks the supplied transcript directory, preserves original relative paths and
timestamps, assigns 2025/2026 metadata, uploads files to one vector store, and
records remote file/vector-store IDs locally. It must be safe to rerun without
duplicate registration.

**Acceptance criteria:**

- [ ] The import reports 22 2025 and 128 2026 registered `.txt` files from the
  current source directory.
- [ ] Each local registration preserves filename, source-relative path, year,
  and remote file ID.
- [ ] The import output records how to delete the created remote vector store
  and files if Theo wants to remove them.

**Verification:**

- [ ] Importer unit tests use a temporary fixture directory and make no network
  call.
- [ ] A single real import completes, records the expected counts, and the
  remote vector store reports ready before chat is enabled.

**Dependencies:** Task 1

**Files likely touched:** `src/mentor/storage.py`, `src/mentor/import_jacob.py`,
`src/mentor/source_registry.py`, `tests/test_import_jacob.py`, `README.md`

**Estimated scope:** Medium

### Checkpoint: Source library ready

- [ ] Test suite passes.
- [ ] All 150 transcripts are registered exactly once with the intended year.
- [ ] Remote IDs and deletion instructions are visible locally.
- [ ] Human verifies that no transcript text was rewritten or summarized.

### Task 3: Build the grounded multi-turn chat service

**Description:** Add a small service that reads a local thread, calls
`gpt-5.6-sol` through the Responses API with native File Search, persists the
new messages locally, and returns answer text plus provenance-labelled source
citations. Its fixed instructions enforce the Jacob-only teaching policy and
make unsupported claims explicit. Responses use `store=false`.

**Acceptance criteria:**

- [ ] A turn can use the registered vector store and returns file citations
  when it relies on Jacob material.
- [ ] Follow-up turns use the selected local thread history.
- [ ] The fixed instruction distinguishes direct teaching, source synthesis,
  AI hypothesis, and unsupported claims.

**Verification:**

- [ ] Unit tests cover message persistence and citation extraction using saved
  API-shaped fixtures.
- [ ] A manual smoke prompt returns a reply and at least one source citation.

**Dependencies:** Task 2

**Files likely touched:** `src/mentor/chat_service.py`, `src/mentor/prompts.py`,
`src/mentor/storage.py`, `tests/test_chat_service.py`, `tests/fixtures/response.json`

**Estimated scope:** Medium

### Task 4: Serve the private browser chat and original evidence

**Description:** Add a standard-library local HTTP server and a single static
chat page. It lists/creates local threads, streams a response, renders citation
links, and serves the registered original transcript read-only when a citation
is opened. Bind only to loopback and never send the API key to the browser.

**Acceptance criteria:**

- [ ] Theo can create a thread, ask a question, receive streamed text, and
  return to that thread for a follow-up.
- [ ] Clicking a citation opens its registered original transcript locally.
- [ ] The browser network requests contain no API key and the server listens
  only on `127.0.0.1`.

**Verification:**

- [ ] HTTP handler tests cover local-only routing and missing-source errors.
- [ ] Manual browser check completes one question, one follow-up, and one
  citation open.

**Dependencies:** Task 3

**Files likely touched:** `src/mentor/server.py`, `src/mentor/static/index.html`,
`src/mentor/static/app.js`, `tests/test_server.py`

**Estimated scope:** Medium

### Checkpoint: End-to-end proof available

- [ ] `python -m pytest` passes.
- [ ] Import → browser question → streamed answer → local source view works.
- [ ] No Phase 2 capability, external service, or custom retrieval system was
  introduced.

### Task 5: Run and record the human quality gate

**Description:** Add the approved manual evaluation worksheet and run it in the
browser. Record each prompt, answer, cited evidence, and Theo's pass/fail notes
outside Git if they contain private conversation content.

**Acceptance criteria:**

- [ ] The nine approved adversarial prompts are available in one worksheet.
- [ ] The worksheet checks clarity, correction handling, evidence relevance,
  year comparison, and unsupported attribution.
- [ ] Theo explicitly decides whether Phase 1 passes; no later phase begins
  automatically.

**Verification:**

- [ ] The worksheet is complete enough to run without developer knowledge.
- [ ] Theo completes the browser evaluation and records a pass/fail decision.

**Dependencies:** Task 4

**Files likely touched:** `docs/phase-1-evaluation.md`, `README.md`

**Estimated scope:** Small

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Fluent answer lacks source fidelity | High | Fixed provenance policy, visible citations, unsupported-attribution prompt, human gate |
| File search finds a mention but not a teaching explanation | High | Let the model search again; judge the actual answer before expanding scope |
| Remote source state persists | Medium | Record remote IDs and document deletion; use local SQLite for chat history and `store=false` for responses |
| Poor local UI masks a good mentor | Medium | Keep the chat page intentionally simple; assess conversational quality, not visual polish |
| Import errors distort year comparisons | High | Preserve original paths, assign year explicitly, assert the 22/128 import counts |

## Approval Gate

Do not implement this plan until Theo explicitly approves it. The next action
after approval is Task 1 only.
