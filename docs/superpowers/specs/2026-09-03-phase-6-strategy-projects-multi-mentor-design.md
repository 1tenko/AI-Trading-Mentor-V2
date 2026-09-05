# Phase 6 Design: Strategy Projects, Multi-Mentor Knowledge & Persistent Coaching Workspace

**Status:** Approved for Phase 6A + Phase 6B implementation on 2026-09-05
**Date:** 2026-09-03
**Builds on:** Phase 4 accepted and Phase 5 human acceptance reported by Theo
**Planned branch:** `feature/phase-6-strategy-projects` (create only when implementation is authorized)

## Objective

Phase 6 turns the existing single-methodology chat into a project-based trading
learning and research system while preserving the product's chat-first identity.
The first project, **GxT Mastery**, is a persistent coach for learning and
researching one methodology through multiple distinct mentor authorities.

```text
General Mentor (Theo as trader)
  ├── global Trader Profile
  └── compact project summaries only

Strategy Project: GxT Mastery
  ├── project-local chats and coaching state
  ├── independently enabled mentor libraries
  ├── roadmap, mastery map, and research ledger
  └── explicit adopted-playbook approval
```

GPT-5.6 Sol remains the teacher and reasoning engine. Native File Search locates
the original mentor evidence; it is not a replacement intelligence layer.

The product north star is not corpus accumulation or source comparison for its
own sake. GxT Mastery must help Theo learn the model, distinguish mentor-specific
explanations, expose gaps, operationalize concepts, choose the next useful
practice or test, collect and falsify evidence, and build an explicitly adopted
personal playbook that improves execution. Consistent profitability is the
direction of coaching and empirical development, never a promised outcome.

## Scope and non-goals

### Phase 6A — Strategy Projects and multi-mentor knowledge

- General Mentor and project-scoped conversations.
- GxT Mastery seeded with Garrett, Afyz, Erik, Splash, Zay, and optional
  Theo — GxT Notes.
- Independent source-library registry, folder confirmation/import, immutable
  source revisions, content-hash deduplication, source metadata and timestamps.
- Saved mentor toggles, temporary one-turn source overrides, source-aware
  retrieval, comparison and attribution.
- Hard project and source isolation.

### Phase 6B — Persistent coaching workspace

- Project-local chat history, compact Roadmap, current objective/experiment,
  blockers, exact next action, mastery map, research ledger and findings.
- Provisional-rule and adopted-playbook records with explicit Theo promotion.
- Bounded project status summaries visible to the General Mentor.

### Explicit non-goals

No video/chart understanding, transcription, broker/execution, market feeds,
web/X/URL ingestion, crawling, full dashboard, arbitrary code/SQL, custom
embedding/RAG stack, or Phase 7 scientific-supervisor automation. Phase 6
prepares structured project state for Phase 7; Sol remains the coach.

## Capability map and dependency order

| Module id | Responsibility | Depends on |
|---|---|---|
| `project-foundation` | Project identity, thread scope, project-local deletion boundaries | — |
| `mentor-libraries` | Library/source/revision metadata, safe folder staging and import | `project-foundation` |
| `source-scope` | Saved library toggles, per-turn source scope, native File Search contract | `mentor-libraries` |
| `project-chat` | Project-aware prompts, retrieval, citations, replay and source diagnostics | `source-scope` |
| `coaching-state` | Roadmap, mastery map, objective, experiment, blockers and next action | `project-foundation` |
| `research-ledger` | Hypotheses, experiments, evidence summaries, findings and provisional rules | `coaching-state` |
| `playbook-promotion` | Immutable rule lineage, explicit approval and playbook versions | `research-ledger` |
| `project-ui` | Navigation, source settings, project chat and compact Roadmap | `project-chat`, `coaching-state`, `playbook-promotion` |
| `phase6-evaluation` | Isolation, model behavior, regression and human-gate proof | all above |

Build order: `project-foundation -> mentor-libraries -> source-scope ->
project-chat -> coaching-state -> research-ledger -> playbook-promotion ->
project-ui -> phase6-evaluation`.

## Scope and isolation contract

### Global scope

The Trader Profile, General Mentor conversations, and a bounded project-status
summary are global. New General Mentor conversations are methodology-neutral:
they receive no mentor library, including Jacob, by default. They must not
automatically receive project transcripts, detailed source claims, research
records, or adopted playbook rules. An explicit request for Jacob uses Jacob
only for that turn and records a temporary scope. Historic accepted Phase 2–5
Jacob conversations retain their legacy Jacob source behavior and historic
replay fidelity; migration must not silently convert them to neutral chats.

### Project scope

A Strategy Project owns its chats, saved library configuration, coaching state,
mastery/research/playbook records and project-source links. A query resolves a
single `project_id` or no project. Project data cannot be selected through an
ordinary General Mentor thread.

### Library scope

Each mentor is an authority, not a tag in an anonymous corpus. A project only
receives raw source tools for the exact enabled library set. An off library's
vector-store ID is absent from that request. A project cannot retrieve another
project's libraries, findings, or playbook merely through prompt text.

Jacob is represented as an independently selectable library when made available
to a project; disabling it removes its store from the request. Existing General
Mentor history keeps its accepted legacy Jacob behavior; new General Mentor
conversations remain neutral unless Theo explicitly asks for Jacob that turn.

## Multi-mentor source contract

Each source library has its own physical native vector store and local immutable
source registry. A library is a corpus/methodology plus authority, not merely a
person: GxT begins with `gxt.garrett`, `gxt.afyz`, `gxt.erik`, `gxt.splash`,
`gxt.zay`, and `gxt.theo_notes`. A future `other-method.afyz` is a distinct
library, not a merge with Afyz's GxT material. Folder identity sets the author;
source text never does.

Every source revision retains local library/project identity, title/filename,
relative category, descriptive source type, optional date, timestamp-available
flag, SHA-256, an optional canonical role, and remote File/vector-store
attachment IDs. Original videos may be external references only. A source type
is descriptive, never an automatic truth hierarchy.

Garrett is the creator/origin authority for GxT. Within `gxt.garrett`, source
metadata assigns exactly one of these roles from the confirmed relative path:

- `CURRENT_CANONICAL_ADVANCED` — `Garrett/Anomaly Mentorship/GxT Advanced/**`;
- `CURRENT_CANONICAL_FOUNDATION` — `Garrett/Anomaly Mentorship/Beginner/**`;
- `GARRETT_ARCHIVAL_AND_COMPLEMENTARY` — every other confirmed Garrett source.

This hierarchy answers questions about GxT authorship, historical baseline, and
what Garrett currently teaches. It does not assert empirical superiority,
evidence quality, or suitability for Theo. It must not globally downrank
`gxt.afyz`, `gxt.erik`, `gxt.splash`, or `gxt.zay`, suppress their explanations,
make Garrett's variation the default recommendation, or convert canonical
lineage into an adopted strategy. Afyz, Erik, Splash, and Zay remain first-class
teaching authorities for their own GxT material. Older Garrett material is also
first-class teaching evidence: Anomaly resolves Garrett currentness when that
question matters, but absence from Anomaly does not make an older teaching
obsolete. Non-Garrett libraries carry no Garrett canonical role.

Repeated import uses a browser directory picker (`webkitdirectory`) and only
browser-provided relative paths. For a selected `GxT` root, `Garrett`, `Afyz`,
`Erik`, `Splash`, `Zay`, and `Theo Notes` establish the six exact library keys;
lower folders are descriptive categories. The server never asks for or returns
an absolute path. It stages files locally, displays library counts/new
files/duplicates and conflicts, then requires Theo confirmation before remote
upload. Identical
content hashes already registered for that library are skipped. A duplicate
detected in a second library is not silently reattributed; it is flagged for
Theo because folder identity and authority would conflict. Raw corpus files,
paths and remote IDs remain ignored runtime data.

## Attribution, disagreement and evidence

Raw transcript text is the authority. A Direct mentor teaching must identify the
mentor and retain a native raw citation. A cross-mentor conclusion is Source
synthesis, not a collective doctrine. The Mentor must distinguish:

1. Direct mentor teaching;
2. Cross-mentor source synthesis;
3. AI interpretation, research hypothesis or recommendation;
4. User empirical evidence and project finding;
5. User decision and adopted playbook rule.

For a normal GxT learning or teaching question, such as “teach me how X works in
GxT,” the default enabled scope researches relevant Garrett, Afyz, Erik, Splash,
and Zay evidence before reconciliation. The answer identifies the shared core,
mentor-specific formulations or refinements, and genuine disagreements, then
uses whichever attributed explanation best teaches the concept. Garrett's
internal canonical ordering does not end cross-mentor research once a Garrett
passage is found. Explicit source toggles and one-turn overrides still narrow
this behavior exactly as requested.

For questions about what Garrett currently teaches, Anomaly Advanced and
Foundation are the primary currentness evidence. Garrett's archival and
complementary material remains first-class for ordinary teaching, lineage,
nuance, edge cases, Q&A, practical application, and concepts not repeated in
Anomaly. “Later” still does not automatically mean “replaced,” and the Mentor
must not infer a change or obsolescence without affirmative evidence.

An absence claim is limited to the scope searched: “I found Afyz teaching Y; I
did not find it in the Garrett sources searched.” Disagreement requires
affirmative competing passages. Later material can repeat, refine, expand,
explicitly replace, conflict with, or have an uncertain relation to earlier
material; later never automatically means newer doctrine.

For a cross-mentor/exhaustive claim, the orchestrator must require a bounded
native research pass across each relevant enabled authority before the model may
say “all enabled mentors.” Phase 6 supports up to six enabled libraries per
turn: Normal is one pass per relevant library (six overall, eight results/pass),
Deep is two passes per relevant library (12 overall, 12 results/pass), and
Exhaustive is three passes per relevant library (18 overall, 16 results/pass).
“All mentors” and complete comparisons use Exhaustive. Trivial questions do not
mechanically search every library, but collective claims require sufficient
coverage of every implicated enabled authority. More than six enabled libraries
must fail or request a narrower scope rather than silently skipping one. A
no-result authority is a scoped absence, never disagreement. Results retain
library identity. Exact
source/timestamp questions remain raw-first and present an exact timestamp only
when the retrieved transcript supplies it.

Canonical source lineage, empirical performance, and Theo's adopted strategy
are independent states. User empirical evidence may favor an Afyz, Erik, Splash,
or Zay refinement over Garrett's current formulation. The Mentor may recommend
that derived variant as an AI coaching/research recommendation, but it becomes
Theo's playbook only through the normal explicit promotion and approval flow.

## OpenAI File Search architecture

Phase 6 retains native Responses File Search and creates one vector store per
mentor library. File attributes carry bounded operational metadata such as
`library_key`, `source_revision_key`, `source_type`, `canonical_role`, `date`
and `timestamps_available`; the local source registry is the authority for
access control and human labels. `canonical_role` is absent outside Garrett.

For an ordinary single-library question, the request has native File Search
access only to that library's store. For an enabled cross-library question, the
orchestrator uses a bounded per-library raw-search plan and a final Sol
reconciliation with the retrieved evidence. Before implementation, a non-
production contract test must prove the current Responses SDK preserves native
citations through that multi-pass continuation. If the API cannot preserve that
property, the allowed fallback is one native File Search tool scoped to the
selected store IDs plus local evidence-to-library verification; no custom
embedding, ranking or anonymous merged store is allowed.

The current `store: false`, encrypted-reasoning replay, native compaction, and
no-historic-File-Search-payload replay rules remain unchanged. Project turns
persist a safe source-scope snapshot for historic fidelity, never raw source
search payload beyond the existing citation/evidence projection.

OpenAI documents File Search as accepting `vector_store_ids`, supports bounded
file attributes and filters, and exposes vector-store file readiness/status.
The implementation must follow the then-current Python SDK reference, use file
batches for multi-file attachment where appropriate, wait for `completed`, and
record a private retention/cleanup inventory. See
[Responses tools](https://developers.openai.com/api/reference/typescript/resources/beta/subresources/responses),
[vector-store files](https://developers.openai.com/api/reference/resources/vector_stores/subresources/files),
and [file batches](https://developers.openai.com/api/reference/cli/resources/vector_stores/subresources/file_batches).

## Source selection

Saved project settings contain the explicit enabled/disabled state for each
project-library link. They are edited in a simple Sources settings surface.
One-turn overrides are resolved before model invocation, shown beside that turn,
stored with the turn diagnostics, and never mutate saved settings. The server
accepts only known library labels and the explicit actions `only`, `compare`,
`ignore`, and `use all enabled mentors again`; malformed language falls back to
saved scope rather than broadening access. The model cannot write saved toggles
or manufacture access to an off library.

## Persistent coaching and project memory

The project state is a small typed, inspectable local model rather than inferred
from transcripts. It includes a current objective, current experiment (if any),
blockers/unresolved questions, exact next action, mastery entries, research
records and a read-only adopted playbook. Factual workflow updates may be
proposed by Sol through constrained server-owned project tools; state-changing
actions are validated, idempotent, attributed to a project/turn, and rendered
for Theo. Theo may override the roadmap.

Research records distinguish observation, hypothesis, operational definition,
experiment, empirical finding, project finding, limitation, provisional rule
and user decision. A dataset-derived project record references a safe immutable
Phase 5 analysis-evidence projection rather than raw rows or qualitative tool
payload. The project owns that snapshot, so deleting its origin conversation
does not silently erase an intentionally recorded research finding; ordinary
thread deletion still removes all thread-owned replay/evidence exactly as Phase
2/5 require.

The mastery vocabulary is: `NOT_STARTED`, `LEARNING`, `OPERATIONALIZING`,
`TESTING`, `PROVISIONAL`, `VALIDATED`. It changes only with a concise recorded
reason/evidence reference and may move backward. Phase 6 does not claim an
automatic “mastered” score.

## Playbook and promotion

An adopted rule is never inferred from a successful answer, direct teaching,
or empirical result. It follows:

```text
observation -> hypothesis -> experiment -> empirical finding
-> provisional rule -> validated finding -> explicit Theo approval -> adopted rule
```

Sol may update research states through restricted project tools. Moving an
eligible validated finding into an adopted playbook requires a separate explicit
Theo approval action. A pending promotion has a stable ID and visible proposed
rule text. The primary route is a UI **Approve rule** action bound to that ID;
the only chat route is the exact phrase `approve promotion #<id>` when that ID
is the sole pending request shown in the immediately preceding assistant turn.
Generic acknowledgements such as “sure” never promote. The server rejects all
other promotion attempts. An adopted rule stores immutable version, concise
wording, origin/source/experiment/finding references and approval turn/date.
Superseding a rule creates a new playbook version; it never rewrites historical
lineage.

## UI

Chat remains central. The existing static browser application gains a compact
scope switcher: General Mentor and Strategy Projects. A project opens its own
conversation list, source-settings popover, source-scope chip, and Roadmap
view. The Roadmap is a readable whiteboard: current focus, next action, active
experiment/progress, blockers, mastery map and recent research/findings. It is
not a dashboard or manual task manager. A narrow/mobile layout keeps project
switching and composer usable.

## Privacy and compatibility

Phase 4 Profile semantics and Phase 5 local-data boundaries are unchanged.
Raw datasets, raw qualitative notes, temporary qualitative outputs, transcript
bodies, folders/paths, API keys, remote IDs and runtime SQLite data stay out of
Git. Project state may retain only the approved safe Phase 5 evidence envelope;
fresh qualitative-note access still needs fresh per-turn consent. No project
source, data scope, research record or playbook leaks into another project or
General Mentor except the bounded summary contract.

## Evaluation and completion

Deterministic tests cover schema/migration/backward compatibility, source
staging/deduplication, toggles/overrides, routing, source ownership, project
deletion, state transitions, promotion approval and Phase 4/5 regressions.
Synthetic mentor corpora and mocked Responses fixtures cover attribution,
agreement, true disagreement, absence discipline, timestamps, cross-library
coverage and enabled-store-only request payloads. Before Theo's final gate, an
explicitly authorized small live GPT-5.6 Sol behavioral suite runs against the
same non-sensitive synthetic sources and records attribution rubric, selected
scope, searches, latency, token/cost metrics and cleanup outcome. Every
disposable File/vector-store resource must be deleted in success and failure
paths, with a private cleanup audit; no real GxT corpus is required.

The behavioral matrix additionally proves: Anomaly resolves Garrett's current
formulation without globally downranking complementary older Garrett material;
Foundation and Advanced differences remain attributed; normal enabled-scope GxT
teaching researches relevant Garrett, Afyz, Erik, Splash, and Zay material; a
useful non-Garrett explanation is not suppressed by creator status; Splash's
application nuance remains Splash-specific; canonical lineage is never
presented as empirical proof or automatic recommendation; and empirical
evidence can support a non-Garrett-derived candidate without silently adopting
it.

Theo's final acceptance checks coaching continuity, source trustworthiness,
natural disagreement handling, useful next action, simple UX and promotion
safety. Phase 6 passes only when all stated 6A/6B outcomes work, no P0/P1
finding remains, and Theo accepts it. Passing does not authorize Phase 7.

## Boundaries

- **Always:** preserve raw-source authority, state provenance, explicit source
  scope, source/project isolation, Phase 5 privacy, tests and Git hygiene.
- **Ask first:** OpenAI spend beyond the approved $5 synthetic-test cap, any
  real mentor corpus import, changes to source retention/cleanup, a new
  dependency, meaningful schema/data-retention policy change, or any
  promotion-policy exception.
- **Never:** flatten mentors into a shared anonymous source store, let a model
  mutate saved source settings or adopt playbook rules without Theo, expose raw
  datasets/notes/transcripts in Git, weaken Phase 5 consent/replay guarantees,
  or begin Phase 7 without approval.
