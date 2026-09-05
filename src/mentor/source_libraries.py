"""Corpus-scoped mentor libraries and immutable local source revisions."""

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import time
from typing import Any, Callable

from mentor.project_models import AuthorityKind, CanonicalRole, SourceLibrary
from mentor.source_registry import discover_transcripts
from mentor.storage import Storage


JACOB_LIBRARY_KEY = "jacob.speculates"


@dataclass(frozen=True)
class LibraryDefinition:
    library_key: str
    corpus_key: str
    authority_name: str
    authority_kind: AuthorityKind
    display_name: str


@dataclass(frozen=True)
class RegisteredRevision:
    id: int
    sha256: str
    byte_size: int
    relative_path: str
    canonical_role: CanonicalRole | None
    file_id: str | None
    vector_store_file_id: str | None
    index_state: str


@dataclass(frozen=True)
class LegacyJacobImportResult:
    vector_store_id: str
    uploaded_count: int
    skipped_count: int


class CrossLibraryDuplicateError(ValueError):
    pass


LIBRARIES = {
    "gxt.garrett": LibraryDefinition("gxt.garrett", "gxt", "Garrett", AuthorityKind.MENTOR, "Garrett — GxT"),
    "gxt.afyz": LibraryDefinition("gxt.afyz", "gxt", "Afyz", AuthorityKind.MENTOR, "Afyz — GxT"),
    "gxt.erik": LibraryDefinition("gxt.erik", "gxt", "Erik", AuthorityKind.MENTOR, "Erik — GxT"),
    "gxt.splash": LibraryDefinition("gxt.splash", "gxt", "Splash", AuthorityKind.MENTOR, "Splash — GxT"),
    "gxt.zay": LibraryDefinition("gxt.zay", "gxt", "Zay", AuthorityKind.MENTOR, "Zay — GxT"),
    "gxt.theo_notes": LibraryDefinition("gxt.theo_notes", "gxt", "Theo", AuthorityKind.USER_NOTES, "Theo Notes — GxT"),
    JACOB_LIBRARY_KEY: LibraryDefinition(
        JACOB_LIBRARY_KEY, "jacob-speculates", "Jacob Speculates", AuthorityKind.MENTOR, "Jacob Speculates"
    ),
}

_GXT_FOLDERS = {
    "Garrett": "gxt.garrett",
    "Afyz": "gxt.afyz",
    "Erik": "gxt.erik",
    "Splash": "gxt.splash",
    "Zay": "gxt.zay",
    "Theo Notes": "gxt.theo_notes",
}


def library_definition_for_browser_path(path: str) -> LibraryDefinition:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if len(parts) < 3 or parts[0] != "GxT" or parts[1] not in _GXT_FOLDERS or ".." in parts:
        raise ValueError("source path does not match an approved GxT library")
    return LIBRARIES[_GXT_FOLDERS[parts[1]]]


def garrett_canonical_role(relative_path: str) -> CanonicalRole:
    normalized = PurePosixPath(relative_path.replace("\\", "/")).as_posix().lstrip("/")
    if normalized.startswith("Garrett/"):
        normalized = normalized[len("Garrett/"):]
    if normalized.startswith("Anomaly Mentorship/GxT Advanced/"):
        return CanonicalRole.CURRENT_CANONICAL_ADVANCED
    if normalized.startswith("Anomaly Mentorship/Beginner/"):
        return CanonicalRole.CURRENT_CANONICAL_FOUNDATION
    return CanonicalRole.GARRETT_ARCHIVAL_AND_COMPLEMENTARY


class SourceImportService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def ensure_library(self, library_key: str) -> SourceLibrary:
        definition = LIBRARIES.get(library_key)
        if definition is None:
            raise ValueError("unknown source library")
        existing = self.storage.source_library(library_key)
        if existing is not None:
            return existing
        return self.storage.create_source_library(
            definition.library_key,
            definition.corpus_key,
            definition.authority_name,
            definition.authority_kind,
            definition.display_name,
        )

    def register_local_revision(
        self,
        library_key: str,
        path: Path,
        relative_path: str,
        *,
        canonical_role: CanonicalRole | None = None,
        file_id: str | None = None,
        vector_store_file_id: str | None = None,
        index_state: str = "STAGED",
    ) -> RegisteredRevision:
        if not path.is_file():
            raise ValueError("source file is unavailable")
        relative = _relative_source_path(relative_path)
        library = self.ensure_library(library_key)
        if library_key == "gxt.garrett":
            expected_role = garrett_canonical_role(relative)
            if canonical_role is not None and canonical_role is not expected_role:
                raise ValueError("Garrett canonical role does not match the source path")
            canonical_role = expected_role
        elif canonical_role is not None:
            raise ValueError("Garrett canonical role is valid only inside gxt.garrett")
        sha256, byte_size = _file_identity(path)
        existing = self.storage.revision_for_hash(sha256)
        if existing is not None and existing[2] != library_key:
            raise CrossLibraryDuplicateError("source content is already assigned to another library")
        revision_id = self.storage.register_library_revision(
            library_id=library.id,
            source_key=relative.casefold(),
            display_title=PurePosixPath(relative).name,
            source_type="transcript",
            relative_category=PurePosixPath(relative).parent.as_posix(),
            source_date=None,
            timestamps_available=True,
            sha256=sha256,
            byte_size=byte_size,
            relative_path=relative,
            staged_path=str(path.resolve()),
            canonical_role=canonical_role,
            file_id=file_id,
            vector_store_file_id=vector_store_file_id,
            index_state=index_state,
        )
        return _registered_revision(self.storage.library_revision(revision_id))

    def library_for_file(self, file_id: str) -> SourceLibrary | None:
        return self.storage.source_library_for_file(file_id)

    def register_legacy_jacob_library(
        self,
        transcript_root: Path,
        client: Any,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> LegacyJacobImportResult:
        transcripts = discover_transcripts(transcript_root)
        self.ensure_library(JACOB_LIBRARY_KEY)
        vector_store_id = self.storage.vector_store_id()
        if vector_store_id is None:
            vector_store = client.vector_stores.create(
                name="Jacob Speculates 2025-2026", metadata={"source": "jacob"}
            )
            vector_store_id = vector_store.id
            self.storage.set_vector_store(vector_store_id)

        uploaded_count = 0
        skipped_count = 0
        for transcript in transcripts:
            if self.storage.has_source(transcript.relative_path):
                self.storage.update_source_modified_at(transcript.relative_path, transcript.modified_at)
                skipped_count += 1
            else:
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
                self.storage.register_source(
                    relative_path=transcript.relative_path,
                    filename=transcript.filename,
                    year=transcript.year,
                    local_path=str(transcript.path.resolve()),
                    modified_at=transcript.modified_at,
                    file_id=uploaded.id,
                    vector_store_file_id=vector_file.id,
                )
                uploaded_count += 1

        legacy = {row[0]: row for row in self.storage.legacy_source_records()}
        for transcript in transcripts:
            row = legacy.get(transcript.relative_path)
            if row is None:
                raise RuntimeError("legacy Jacob registration is incomplete")
            self.register_local_revision(
                JACOB_LIBRARY_KEY,
                transcript.path,
                transcript.relative_path,
                file_id=row[4],
                vector_store_file_id=row[5],
                index_state="READY",
            )
        return LegacyJacobImportResult(vector_store_id, uploaded_count, skipped_count)


def _relative_source_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("source relative path is invalid")
    return path.as_posix()


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _registered_revision(row: tuple | None) -> RegisteredRevision:
    if row is None:
        raise RuntimeError("source revision was not registered")
    return RegisteredRevision(
        id=int(row[0]),
        sha256=str(row[1]),
        byte_size=int(row[2]),
        relative_path=str(row[3]),
        canonical_role=None if row[4] is None else CanonicalRole(row[4]),
        file_id=None if row[5] is None else str(row[5]),
        vector_store_file_id=None if row[6] is None else str(row[6]),
        index_state=str(row[7]),
    )


def _wait_for_indexing(
    client: Any,
    vector_store_id: str,
    file_id: str,
    sleep: Callable[[float], None],
) -> None:
    for _ in range(120):
        vector_file = client.vector_stores.files.retrieve(file_id, vector_store_id=vector_store_id)
        if vector_file.status == "completed":
            return
        if vector_file.status in {"cancelled", "failed"}:
            raise RuntimeError("OpenAI indexing failed for a Jacob source")
        sleep(0.5)
    raise TimeoutError("OpenAI indexing did not finish for a Jacob source")
