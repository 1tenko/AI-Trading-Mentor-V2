"""Import raw Jacob transcripts into one OpenAI vector store."""

import os
import time
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from mentor.config import load_config
from mentor.source_libraries import LegacyJacobImportResult as ImportResult, SourceImportService
from mentor.storage import Storage


def import_transcripts(
    transcript_root: Path,
    storage: Storage,
    client: Any,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> ImportResult:
    """Compatibility wrapper for the corpus-scoped Jacob importer."""
    return SourceImportService(storage).register_legacy_jacob_library(
        transcript_root,
        client,
        sleep=sleep,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Import raw Jacob transcripts")
    parser.add_argument("transcript_root", type=Path)
    arguments = parser.parse_args()

    config = load_config(os.environ, Path(".env"))
    storage = Storage(Path("data") / "mentor.sqlite3")
    storage.initialize()
    result = import_transcripts(arguments.transcript_root, storage, OpenAI(api_key=config.api_key))
    print(
        f"Registered {result.uploaded_count} files and skipped {result.skipped_count}. "
        f"Vector store: {result.vector_store_id}"
    )
    counts = storage.source_counts_by_year()
    print(f"Registered sources: {counts[2025]} from 2025, {counts[2026]} from 2026.")
    print(
        "Remote cleanup: delete vector store "
        f"{result.vector_store_id}, then delete the uploaded file IDs recorded in "
        "data/mentor.sqlite3 (the sources.file_id column)."
    )


if __name__ == "__main__":
    main()
