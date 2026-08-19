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

## Planned commands

```powershell
.\.venv\Scripts\python -m mentor.import_jacob "D:\courses\Jacob Speculates 2026\Transcripts"
.\.venv\Scripts\python -m mentor
.\.venv\Scripts\python -m pytest
```

The import and browser commands are added by later approved Phase 1 tasks.
