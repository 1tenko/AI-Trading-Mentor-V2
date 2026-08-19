"""Import raw Jacob transcripts into one OpenAI vector store."""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from mentor.config import load_config
from mentor.source_registry import discover_transcripts
from mentor.storage import Storage


@dataclass(frozen=True)
class ImportResult:
    vector_store_id: str
    uploaded_count: int
    skipped_count: int


def import_transcripts(
    transcript_root: Path,
    storage: Storage,
    client: Any,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> ImportResult:
    """Upload each unregistered raw transcript and wait for indexing."""
    transcripts = discover_transcripts(transcript_root)
    vector_store_id = storage.vector_store_id()
    if vector_store_id is None:
        vector_store = client.vector_stores.create(
            name="Jacob Speculates 2025-2026", metadata={"source": "jacob"}
        )
        vector_store_id = vector_store.id
        storage.set_vector_store(vector_store_id)

    uploaded_count = 0
    skipped_count = 0
    for transcript in transcripts:
        if storage.has_source(transcript.relative_path):
            skipped_count += 1
            continue

        with transcript.path.open("rb") as file_content:
            uploaded = client.files.create(file=file_content, purpose="assistants")
        vector_file = client.vector_stores.files.create(
            vector_store_id,
            file_id=uploaded.id,
            attributes={
                "source": "jacob",
                "year": str(transcript.year),
                "relative_path": transcript.relative_path,
            },
        )
        _wait_for_indexing(client, vector_store_id, uploaded.id, sleep)
        storage.register_source(
            relative_path=transcript.relative_path,
            filename=transcript.filename,
            year=transcript.year,
            local_path=str(transcript.path.resolve()),
            file_id=uploaded.id,
            vector_store_file_id=vector_file.id,
        )
        uploaded_count += 1

    return ImportResult(
        vector_store_id=vector_store_id,
        uploaded_count=uploaded_count,
        skipped_count=skipped_count,
    )


def _wait_for_indexing(
    client: Any,
    vector_store_id: str,
    file_id: str,
    sleep: Callable[[float], None],
) -> None:
    for _ in range(120):
        vector_file = client.vector_stores.files.retrieve(
            file_id, vector_store_id=vector_store_id
        )
        if vector_file.status == "completed":
            return
        if vector_file.status in {"cancelled", "failed"}:
            raise RuntimeError(f"OpenAI indexing failed for {file_id}: {vector_file.status}")
        sleep(0.5)
    raise TimeoutError(f"OpenAI indexing did not finish for {file_id}")


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
