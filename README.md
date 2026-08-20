# Theo's AI Trading Mentor

Private Phase 2 foundation for a frontier Trading Mentor over Jacob Speculates'
raw transcripts. It keeps the Phase 1 Responses API/File Search intelligence
path while adding reliable local conversation restoration, deletion, historical
evidence/diagnostics, and independent research-depth controls.

## Local setup

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Put a real `OPENAI_API_KEY` in `.env`. Keep `.env`, runtime SQLite data, and
private transcript copies out of Git.

## Import and run

```powershell
.\.venv\Scripts\python -m mentor.import_jacob "D:\courses\Jacob Speculates 2026\Transcripts"
.\.venv\Scripts\python -m mentor
.\.venv\Scripts\python -m pytest
```

The importer sends the raw `.txt` files to one OpenAI vector store and records
the vector-store and file IDs in `data/mentor.sqlite3`. It neither changes nor
summarizes the local transcripts. The browser server listens only at
`http://127.0.0.1:8765`; your API key never reaches the browser.

OpenAI retains uploaded files and vector stores until they are deleted. The
import command prints the vector-store ID and the local database records each
uploaded file ID, so both remote resource types can be removed later from the
OpenAI dashboard or API. Local conversation data stays in `data/mentor.sqlite3`.

## Phase 2 conversation behavior

The browser restores saved conversations with their original Markdown, evidence,
diagnostics, and model/reasoning/research settings. The current controls govern
only a future response. Research depth is separate from model reasoning:

- **Auto** selects Normal, Deep, or Exhaustive from transparent question intent.
- **Normal** is for ordinary grounded questions.
- **Deep** encourages complementary source research.
- **Exhaustive** requires omission/falsification research before completeness
  claims, with a four-pass ceiling.

Deleting a conversation permanently removes only that thread's local messages,
display records, diagnostics, and raw replay items in one SQLite transaction.
It never deletes transcripts, source registrations, OpenAI files, or the shared
vector store. Encrypted reasoning replay content is never sent to the browser.

Run [the Phase 2 evaluation worksheet](docs/phase-2-evaluation.md) only when
you are ready to make the small paid human quality check. It is not part of
pytest and does not run automatically.
