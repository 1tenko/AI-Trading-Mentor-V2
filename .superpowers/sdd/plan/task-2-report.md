# Task 2 report — Idempotent Jacob registry migration and change detection

## Scope

Implemented only Phase 3 Task 2 in the `knowledge-library` module. All test data
used temporary SQLite databases and synthetic temporary files. No normal Theo
database, source transcript, OpenAI client, vector store, or remote file was
used or changed.

## Implementation

- Added `backfill_jacob_registry()` to mirror legacy `sources` records into the
  Jacob collection, source, and immutable revision tables.
- Uses `legacy:<OpenAI file ID>` as source identity, so duplicate filenames and
  lesson metadata cannot collide.
- Hashes only readable current files. The first readable migration revision is
  `active` and retains the legacy OpenAI file and vector-store-file IDs.
- Creates a hash-identified `replacement_pending` revision without remote IDs
  when bytes differ. The existing active revision and linkage remain unchanged.
- Added local `source_changes` records for pending replacement, removed, and
  unreadable inputs. A file that becomes readable and matches the active
  revision clears its visible change flag.
- Calls the local backfill before and after the existing importer flow so a new
  legacy registration is immediately mirrored, without changing the importer’s
  existing remote operations.

## TDD evidence

RED was recorded before implementation:

```text
.\\.venv\\Scripts\\python -m pytest tests\\test_source_registry.py -q
ImportError: cannot import name 'JACOB_COLLECTION_ID' from 'mentor.source_registry'
```

The new synthetic fixture tests cover byte-identical linkage preservation,
changed-byte pending revisions, removed/unreadable visibility, and same-name
source identity. The importer test verifies a newly registered remote file is
immediately represented by a revision with that remote file ID.

## Verification

Focused:

```text
.\\.venv\\Scripts\\python -m pytest tests\\test_source_registry.py tests\\test_import_jacob.py tests\\test_storage.py -q
14 passed
```

Full:

```text
.\\.venv\\Scripts\\python -m pytest -q
55 passed

.\\.venv\\Scripts\\python -m compileall -q src
exit 0

git diff --check
exit 0
```

## Self-review

- **Correctness:** Initial, changed, removed, unreadable, duplicate-name, and
  rerun behavior are covered with synthetic fixtures. Unchanged reruns retain
  one active revision and the original remote linkage; changed reruns retain
  one pending revision and no remote linkage.
- **Compatibility:** The Phase 2 `sources` table remains unchanged. The new
  `source_changes` table is additive, and the full Phase 2 test suite passed.
- **Safety/security:** Content is read locally in binary form; SQLite uses
  parameterized statements; no credentials, network calls, or dependencies
  were added.
- **Simplicity/performance:** Uses standard-library hashing and the existing
  SQLite storage pattern. No abstraction or dependency was added beyond the
  minimum persisted pending-state row required for missing/unreadable visibility.

## Scope exclusions

Did not begin Task 3, amend the plan, create/delete remote resources, use a
real corpus or runtime database, push, or alter raw transcript bytes.
