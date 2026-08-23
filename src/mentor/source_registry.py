"""Discover immutable local transcript files for the Phase 1 source library."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import time

from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import SourceChange, Storage


JACOB_COLLECTION_ID = "collection_jacob_2025_2026"
_JACOB_COLLECTION = Collection(
    collection_id=JACOB_COLLECTION_ID,
    display_name="Jacob Speculates 2025-2026",
    domain="trading",
    enabled=True,
    scope="2025-2026",
)


@dataclass(frozen=True)
class Transcript:
    path: Path
    relative_path: str
    filename: str
    year: int
    modified_at: float


def discover_transcripts(root: Path) -> list[Transcript]:
    """Return raw `.txt` transcripts without modifying their contents."""
    if not root.is_dir():
        raise ValueError(f"Transcript directory does not exist: {root}")

    transcripts: list[Transcript] = []
    for path in sorted(root.rglob("*.txt")):
        relative_path = path.relative_to(root).as_posix()
        year = 2025 if "2025" in path.relative_to(root).parts else 2026
        transcripts.append(
            Transcript(
                path=path,
                relative_path=relative_path,
                filename=path.name,
                year=year,
                modified_at=path.stat().st_mtime,
            )
        )
    return transcripts


def backfill_jacob_registry(transcript_root: Path, storage: Storage) -> None:
    """Mirror legacy Jacob registrations into immutable local revisions only."""
    if not transcript_root.is_dir():
        raise ValueError(f"Transcript directory does not exist: {transcript_root}")

    storage.store_collection(_JACOB_COLLECTION)
    for legacy_source in storage.legacy_sources():
        local_path = transcript_root / Path(legacy_source.relative_path)
        source = Source.create(
            collection_id=JACOB_COLLECTION_ID,
            identity_key=f"legacy:{legacy_source.file_id}",
            source_type="transcript",
            author="Jacob Speculates",
            course="Jacob Speculates 2025-2026",
            lesson_title=Path(legacy_source.filename).stem,
            year=legacy_source.year,
            original_filename=legacy_source.filename,
            local_provenance=legacy_source.local_path,
        )
        storage.store_source(source)
        try:
            content = local_path.read_bytes()
            modified_at = local_path.stat().st_mtime
        except FileNotFoundError:
            _record_unavailable_source(storage, source.source_id, "removed", local_path)
            continue
        except OSError:
            _record_unavailable_source(storage, source.source_id, "unreadable", local_path)
            continue

        content_sha256 = sha256(content).hexdigest()
        revisions = storage.source_revisions(source.source_id)
        matching_revision = next(
            (revision for revision in revisions if revision.content_sha256 == content_sha256), None
        )
        if matching_revision is None:
            lifecycle_state = (
                "active"
                if not revisions and legacy_source.modified_at == modified_at
                else "replacement_pending"
            )
            matching_revision = SourceRevision.create(
                source=source,
                content_sha256=content_sha256,
                byte_size=len(content),
                local_locator=str(local_path.resolve()),
                observed_at=time.time(),
                lifecycle_state=lifecycle_state,
                remote_file_id=legacy_source.file_id if lifecycle_state == "active" else None,
                remote_vector_store_file_id=(
                    legacy_source.vector_store_file_id if lifecycle_state == "active" else None
                ),
            )
            storage.store_source_revision(matching_revision)

        if matching_revision.lifecycle_state == "active":
            storage.clear_source_change(source.source_id)
        else:
            storage.store_source_change(
                SourceChange(
                    source_id=source.source_id,
                    lifecycle_state="replacement_pending",
                    revision_id=matching_revision.revision_id,
                    local_locator=str(local_path.resolve()),
                    observed_at=time.time(),
                )
            )


def _record_unavailable_source(
    storage: Storage, source_id: str, lifecycle_state: str, local_path: Path
) -> None:
    storage.store_source_change(
        SourceChange(
            source_id=source_id,
            lifecycle_state=lifecycle_state,
            revision_id=None,
            local_locator=str(local_path.resolve()),
            observed_at=time.time(),
        )
    )
