# Theo's AI Trading Mentor

Private Phase 1 proof: can a frontier model teach Jacob Speculates' transcripts
in a browser conversation with inspectable original evidence?

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
