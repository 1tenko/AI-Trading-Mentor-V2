# Phase 2 Human Evaluation

**Status:** Prepared for Theo's evaluation. This worksheet does not mark Phase 2 passed.

Run the deterministic checks first:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m mentor
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The real questions below
make paid OpenAI requests, so they are deliberately outside pytest.

## Persistent conversation flow

1. Start a new conversation and ask `What is SMT?` with Research set to Auto.
2. Ask a follow-up: `I understand SMT, but I don't understand why that matters.`
3. Confirm that the sidebar title is useful, select another conversation, then
   return to the SMT conversation. Reload the page and confirm both turns,
   Markdown, evidence, and each turn's original diagnostics remain visible.
4. Create a second conversation. Delete it through its sidebar Delete control,
   approve the confirmation, reload, and confirm only that conversation is gone.
   The original conversation must remain available and source links must still open.

## Research and evidence flow

Use High / Standard first. Research controls apply only to the next message;
historical diagnostics must not change when controls change.

| Prompt | Research control | Check |
|---|---|---|
| `What is TPD?` | Auto | Normal research is economical, teaching is clear, and source claims are cited. |
| `What are ALL the timeframe alignments for reversion levels?` | Auto | Effective depth is Exhaustive; evidence includes the 6H -> 1H teaching; the answer makes no unsupported completeness claim. |
| `Teach me everything Jacob teaches about SMT.` | Exhaustive | Diagnostics show complementary research rather than a one-search conclusion. |
| `Jacob teaches that SMT guarantees reversals, right?` | Normal | The Mentor corrects the unsupported attribution and distinguishes direct teaching from inference. |
| `Compare Jacob's 2025 and 2026 SMT teaching.` | Deep | The Mentor differentiates years only where evidence supports it. |

For every Mentor turn, verify:

- the answer remains primary; Evidence and diagnostics are collapsed initially;
- cited evidence is shown first, while all retained evidence can be expanded;
- displayed model, effort/mode, and requested/effective depth are that turn's
  historical settings;
- text-token estimates explicitly exclude File Search/platform charges, which
  remain marked unknown where OpenAI did not return them;
- Markdown lists and tables render normally; no in-app `NaN.` text appears;
- the browser console has no errors and no request exposes an API key or
  encrypted reasoning content.

## Decision

Record one outcome outside Git if it contains private conversation content:

- [ ] Pass — Phase 2 persistent-chat foundation is acceptable.
- [ ] Fail — record the prompt, settings, evidence, and observed gap.

Theo alone makes this decision. A pass does not authorize Phase 3.
