# Theo's AI Trading Mentor

## Product North Star

Build Theo his own ChatGPT for retail trading: a frontier-intelligence mentor
with expandable, user-controlled trading and mindset knowledge; persistent,
inspectable understanding of Theo as a trader; and a scientific workspace for
developing and validating his own strategy.

The product exists to improve Theo's learning and research process. It does not
promise profitability and is not an automated trading system.

## One Unified Mentor

There is one user-facing mentor, not separate Jacob, strategy, and mindset
bots. Its capabilities grow through tools and user-enabled knowledge.

```text
                    Unified Mentor
                         |
        +----------------+----------------+
        |                |                |
     Sources        Strategy data     Theo's memory
```

The frontier model is the teacher and reasoning engine. Knowledge search locates
raw evidence; it does not replace the model with a custom retrieval-answering
system. The previous `D:\\projects\\ai-trading-mentor` repository is archive and
reference material only. Its SOURCE/retrieval architecture must not be reused.

## Knowledge Library

Theo controls which sources are enabled. The initial source is Jacob Speculates
(2025–2026 transcripts); later sources may include Trader Daye, other trading
material, psychology books, and Theo's own notes and research. Source metadata
must retain useful provenance such as author, domain, course, year, lesson,
original filename, and timestamps.

Raw source text remains the evidence authority. The system does not convert the
corpus into an AI-written canonical glossary or handcrafted knowledge base.

## Provenance Model

The mentor must keep these categories visibly distinct:

1. **Direct source teaching** — a source explicitly teaches it.
2. **Source synthesis/inference** — an interpretation across source teachings.
3. **AI research hypothesis** — an idea proposed for consideration or testing.
4. **User empirical evidence** — Theo's data supports or contradicts it.
5. **User decision** — Theo deliberately adopted a rule or preference.

It must never silently present categories 2–5 as direct source teaching. When
a requested source does not establish a claim, the mentor says so. It may reason
and challenge Theo, but it labels that reasoning honestly.

## Persistent Understanding of Theo

Later phases add user-editable structured profile data: markets, sessions,
trading style, risk constraints, strengths, weaknesses, goals, preferences,
learning progress, psychological tendencies, and current research focus.

Long-term memory complements the profile with durable research history,
decisions, failed hypotheses, and unresolved questions. Theo can inspect and
edit this state; an unbounded chat transcript is not the long-term memory
system.

## Strategy Research Workspace

The mentor will help Theo perform disciplined research rather than fabricate a
finished strategy. The intended loop is:

```text
observation -> research -> hypothesis -> test -> analyze
            -> critique/falsify -> revise, reject, or retain
```

Strategy Projects retain objectives, versions, rules, provenance, hypotheses,
experiments, datasets, results, rejected ideas, decision history, weaknesses,
and next questions. Versioning prevents hindsight from silently rewriting why a
rule existed.

Backtest data arrives as CSV, XLSX, or tables. Deterministic analysis tools
calculate statistics; the model interprets results and proposes the next sound
question. Large-table arithmetic is not delegated to the language model.

The mentor should actively challenge overfitting, hindsight fitting, tiny
samples, multiple-variable changes, and confusing correlation with causation.

## Future Mindset Capability

User-provided psychology sources become another knowledge domain available to
the same mentor. Mindset is not a separate chatbot: it can be considered beside
Theo's profile, strategy work, and evidence when relevant.

## Product Boundaries

- No screenshot or chart-image requirement is planned.
- No broker integration, order execution, high-frequency infrastructure, or
  profitability guarantee is in scope unless explicitly added in a future
  approved design.
- Privacy, source licensing, retention, deletion, and export requirements are
  treated as product decisions when those capabilities are introduced.

## Roadmap and Quality Gates

Each phase has its own approved design and must pass its human quality gate
before the next phase starts:

1. Intelligence proof — Jacob teaching quality in private browser chat.
2. Base unified mentor — polished conversational foundation.
3. Extensible knowledge/assimilation experiment — completed and abandoned after
   Phase 3B showed no material benefit over GPT-5.6 Sol with scoped raw source
   search. The archived record remains on
   `feature/phase-3-knowledge-assimilation`; it is not continuing product
   architecture.
4. Trader profile and editable memory — passed.
5. Backtest and empirical data analysis foundation — current design/planning
   phase; structured import and deterministic analysis only.
6. Strategy Projects plus source/research scope. The future GxT project must
   support multiple independently attributable mentor libraries (for example,
   Garrett, Afyz, Erik, and later approved GxT mentors) teaching one strategy;
   Jacob must remain independently switchable off. The mentor must preserve
   per-mentor provenance and disagreements, while the later research supervisor
   guides concept learning through hypothesis, operational definition, data
   collection, deterministic analysis, falsification, and the next experiment.
7. Scientific strategy-development supervisor.
8. Mindset and development sources and coaching.
9. Evals, hardening, and productization.

Phase 5 design/planning does not authorize Phase 5 implementation. Later phases
remain product direction, not permission to implement them now.
