# Phase 1 Design: Private AI Trading Mentor Proof

## Objective

Build a private, one-user browser chat for Theo that tests one question only:
can a frontier OpenAI model teach Jacob Speculates' 2025–2026 transcripts as a
thoughtful conversational mentor rather than as a document search engine?

The proof succeeds only when Theo judges the conversation to feel like
"ChatGPT has studied Jacob." It must answer follow-up questions, accept
corrections, show original evidence, and state when Jacob's transcripts do not
establish an answer.

## Scope

### Included

- A local browser chat served by a small private application server.
- Import of the existing Jacob 2025–2026 raw transcripts into an OpenAI-native
  searchable source library, preserving filenames, year, lesson metadata, and
  timestamps where present.
- Persistent local conversation threads.
- Streaming answers with clickable source evidence.
- A manual evaluation set covering definitions, follow-ups, corrections,
  year-comparisons, and unsupported attribution.

### Excluded

- Accounts, sharing, hosting, payments, or multi-user access.
- Other mentors, books, mindset material, and Theo's personal notes.
- Trader profile, long-term memory, strategy projects, backtest analysis,
  market data, image/chart analysis, broker access, and trade execution.
- Custom embeddings, custom ranking, local models, pre-written AI knowledge
  bases, or a polished production UI.

## Architecture

```text
Browser chat
  -> private local server
  -> frontier OpenAI model
  -> OpenAI-native search over raw Jacob transcripts
  -> response with source evidence
```

The model is the conversational teacher and reasoning engine. Source search is
only a locator for original evidence. The application does not attempt to
summarize retrieved passages or write answers itself.

The browser communicates only with the local server; the OpenAI API key remains
on the server. Conversation history and the local source registry are stored on
Theo's computer. Uploaded transcript files may be stored by OpenAI under the
selected API product's retention controls; transcript use must comply with the
material's licence/terms.

## Knowledge and Provenance Policy

For a question such as "What does Jacob teach?", factual methodology claims
must be supported by the enabled Jacob transcripts. The mentor must visibly
distinguish:

1. Direct source teaching.
2. Synthesis or inference across source teachings.
3. AI hypothesis or reasoning.
4. The source does not establish an answer.

It must not attribute generic model knowledge or an AI hypothesis to Jacob.
When the current evidence is inadequate, it should search again or explain the
limit rather than bluff. This policy is enforced through server-controlled
instructions and verified with adversarial manual prompts; it is not treated as
a guarantee supplied by a prompt alone.

## Tech Stack

- Python application server with a minimal static browser chat page.
- SQLite for local threads and source-registration metadata.
- OpenAI API for the frontier model and native file/vector search.
- No frontend framework or additional database for Phase 1.

Before implementation, the source-driven-development phase must verify the
current official OpenAI documentation for the exact supported model, API,
native search mechanism, citations, streaming, file retention, and SDK. This
spec intentionally does not lock in potentially stale model or product names.

## Commands

No application commands exist at design time. The implementation plan will add
the exact install, import, development, test, and evaluation commands after the
official OpenAI integration is verified.

## Project Structure

```text
docs/superpowers/specs/  Approved design records
src/                     Application source (created during implementation)
tests/                   Automated checks (created during implementation)
data/                    Local runtime data, ignored by Git
```

## Code Style

Keep the proof deliberately small: plain Python, explicit data structures, and
one responsibility per module. No abstraction is added until the first proof
needs it.

```python
if not evidence_found:
    return "I could not establish that from the enabled Jacob transcripts."
```

## Testing Strategy

Automated checks will cover local persistence, transcript metadata retention,
and provenance-policy formatting. Manual quality evaluation is the decisive
test because the product claim is conversational teaching quality.

The mandatory manual prompts are:

- What is SMT?
- What is TPD?
- What are the timeframe alignments for reversion levels?
- Teach me everything Jacob teaches about SMT.
- Why does Jacob care about cracks in correlation?
- I understand SMT, but I don't understand why that matters.
- No, that's not what I asked. I mean specifically which timeframe aligns with
  which timeframe.
- Compare Jacob's 2025 and 2026 SMT teaching.
- An unsupported claim presented as Jacob's teaching.

## Boundaries

- Always: keep the API key out of the browser and Git; preserve raw transcript
  provenance; show evidence; run the evaluation prompts before expanding scope.
- Ask first: introduce a major architectural dependency or external service not
  approved by this design; materially change privacy or retention; add recurring
  infrastructure or cost; upload new source types; add external access; or begin
  any Phase 2 capability. Normal implementation dependencies needed for this
  approved proof do not require repeated approval.
- Never: execute trades, provide profitability guarantees, silently attribute
  AI reasoning to Jacob, or reuse the old application's retrieval architecture.

## Success Criteria

1. Theo can use a local browser to hold a multi-turn conversation about Jacob.
2. Answers cite relevant original transcript evidence.
3. The mentor corrects course after a user clarification.
4. Unsupported attributions are rejected or explicitly marked unsupported.
5. Year comparisons use the appropriate 2025 and 2026 material when asked.
6. Theo manually approves the quality before Phase 2 is considered.

## Open Questions

None for Phase 1 product scope. Exact OpenAI integration details are a
source-verification task before implementation, not a reason to expand scope.
