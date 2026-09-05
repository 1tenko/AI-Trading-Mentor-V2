"""Corpus-scoped mentor libraries and immutable local source revisions."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import time
from typing import Any, Callable

from mentor.project_models import AuthorityKind, CanonicalRole, SourceLibrary
from mentor.source_registry import discover_transcripts
from mentor.storage import Storage


JACOB_LIBRARY_KEY = "jacob.speculates"
MAX_SOURCE_BYTES = 10 * 1024 * 1024


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
    def __init__(
        self,
        storage: Storage,
        client: Any | None = None,
        *,
        staging_root: Path | None = None,
    ):
        self.storage = storage
        self.client = client
        self.staging_root = staging_root or storage.database_path.parent / "source-imports"

    def create_staging_import(self, project_id: int) -> dict[str, object]:
        batch_id = self.storage.create_library_import_batch(project_id)
        return {"id": batch_id, "state": "STAGING", "accepted_root": "GxT"}

    def stage_browser_file(
        self, batch_id: int, relative_path: str, ordinal: int, content: bytes
    ) -> dict[str, object]:
        batch = self._batch(batch_id)
        if batch[2] != "STAGING":
            raise ValueError("source import is no longer accepting files")
        relative = _browser_source_path(relative_path)
        definition = library_definition_for_browser_path(relative)
        if type(ordinal) is not int or ordinal < 1:
            raise ValueError("source import ordinal is invalid")
        if not content or len(content) > MAX_SOURCE_BYTES:
            raise ValueError("Source files must be between 1 byte and 10 MiB.")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Source transcripts must be UTF-8 text.") from None
        manifest = batch[3]
        if any(item["ordinal"] == ordinal or item["relative_path"] == relative for item in manifest["files"]):
            raise ValueError("source file was already staged")
        destination = self.staging_root / str(batch_id) / f"{ordinal}.txt"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
        manifest["files"].append({
            "ordinal": ordinal,
            "relative_path": relative,
            "staged_name": destination.name,
            "library_key": definition.library_key,
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_size": len(content),
        })
        self.storage.update_library_import_batch(batch_id, state="STAGING", manifest=manifest)
        return {"relative_path": relative, "ordinal": ordinal, "accepted": True}

    def finalize_manifest(self, batch_id: int) -> dict[str, object]:
        batch = self._batch(batch_id)
        if batch[2] != "STAGING":
            raise ValueError("source import cannot be finalized in its current state")
        manifest = batch[3]
        if not manifest["files"]:
            raise ValueError("Select at least one transcript.")
        seen: dict[str, str] = {}
        counts: dict[str, dict[str, int]] = {}
        for item in sorted(manifest["files"], key=lambda value: value["ordinal"]):
            library_key = item["library_key"]
            bucket = counts.setdefault(library_key, {"total": 0, "new": 0, "duplicates": 0, "conflicts": 0})
            bucket["total"] += 1
            existing = self.storage.revision_for_hash(item["sha256"])
            prior_library = existing[2] if existing is not None else seen.get(item["sha256"])
            if prior_library is None:
                item["classification"] = "new"
                bucket["new"] += 1
                seen[item["sha256"]] = library_key
            elif prior_library == library_key:
                item["classification"] = "duplicate"
                bucket["duplicates"] += 1
            else:
                item["classification"] = "conflict"
                bucket["conflicts"] += 1
        manifest["summary"] = counts
        self.storage.update_library_import_batch(
            batch_id, state="READY_FOR_CONFIRMATION", manifest=manifest
        )
        return self.import_status(batch_id)

    def confirm_import(
        self,
        batch_id: int,
        *,
        confirm: bool,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict[str, object]:
        if confirm is not True:
            raise ValueError("Confirm the source import before uploading.")
        batch = self._batch(batch_id)
        if batch[2] == "COMPLETE":
            return self.import_status(batch_id)
        retryable = batch[2] == "FAILED" and batch[4] == "SOURCE_INDEXING_FAILED"
        if batch[2] != "READY_FOR_CONFIRMATION" and not retryable:
            raise ValueError("source import is not ready for confirmation")
        manifest = batch[3]
        if any(item.get("classification") == "conflict" for item in manifest["files"]):
            raise ValueError("The same source content is assigned to more than one mentor library.")
        if self.client is None:
            raise RuntimeError("Source import is unavailable.")
        self.storage.update_library_import_batch(batch_id, state="IMPORTING", manifest=manifest)
        imported = sum(item.get("result") == "IMPORTED" for item in manifest["files"])
        try:
            for item in sorted(manifest["files"], key=lambda value: value["ordinal"]):
                if item["classification"] == "duplicate" or item.get("result") == "IMPORTED":
                    continue
                definition = LIBRARIES[item["library_key"]]
                library = self.ensure_library(definition.library_key)
                vector_store_id = self._ready_vector_store(library)
                path = self.staging_root / str(batch_id) / item["staged_name"]
                with path.open("rb") as source:
                    uploaded = self.client.files.create(file=source, purpose="assistants")
                item["remote_file_id"] = uploaded.id
                item["remote_status"] = "UPLOADED"
                relative = _library_relative_path(item["relative_path"])
                role = garrett_canonical_role(relative) if library.library_key == "gxt.garrett" else None
                attributes = {
                    "library_key": library.library_key,
                    "source_revision_key": item["sha256"],
                    "timestamps_available": "true",
                }
                if role is not None:
                    attributes["canonical_role"] = role.value
                vector_file = self.client.vector_stores.files.create(
                    vector_store_id, file_id=uploaded.id, attributes=attributes
                )
                item["remote_vector_store_file_id"] = vector_file.id
                item["remote_status"] = "INDEXING"
                _wait_for_library_indexing(
                    self.client, vector_store_id, uploaded.id, sleep
                )
                item["remote_status"] = "READY"
                self.register_local_revision(
                    library.library_key,
                    path,
                    relative,
                    canonical_role=role,
                    file_id=uploaded.id,
                    vector_store_file_id=vector_file.id,
                    index_state="READY",
                )
                self.storage.set_project_library(batch[1], library.id, enabled=True)
                item["result"] = "IMPORTED"
                imported += 1
            manifest["imported"] = imported
            self.storage.update_library_import_batch(batch_id, state="COMPLETE", manifest=manifest)
        except Exception:
            if "item" in locals() and item.get("remote_status") != "READY":
                item["remote_status"] = "FAILED"
            manifest["imported"] = imported
            self.storage.update_library_import_batch(
                batch_id, state="FAILED", manifest=manifest, error_code="SOURCE_INDEXING_FAILED"
            )
            raise RuntimeError("Source indexing failed. You can retry the import.") from None
        return self.import_status(batch_id)

    def import_status(self, batch_id: int) -> dict[str, object]:
        batch = self._batch(batch_id)
        manifest = batch[3]
        libraries = []
        for key, counts in sorted(manifest.get("summary", {}).items()):
            libraries.append({
                "library_key": key,
                "display_name": LIBRARIES[key].display_name,
                **counts,
            })
        result: dict[str, object] = {
            "id": batch[0],
            "project_id": batch[1],
            "state": batch[2],
            "accepted_root": "GxT",
            "file_count": len(manifest["files"]),
            "libraries": libraries,
            "imported": int(manifest.get("imported", 0)),
        }
        if batch[4] == "SOURCE_INDEXING_FAILED":
            result["error"] = "A source could not be indexed. You can retry the import."
        return result

    def _batch(self, batch_id: int):
        batch = self.storage.library_import_batch(batch_id)
        if batch is None:
            raise LookupError("source import does not exist")
        return batch

    def _ready_vector_store(self, library: SourceLibrary) -> str:
        existing = self.storage.library_vector_store(library.id)
        if existing is not None and existing[0] and existing[1] == "READY":
            return existing[0]
        self.storage.set_library_vector_store(library.id, None, "CREATING")
        try:
            vector_store = self.client.vector_stores.create(
                name=library.display_name, metadata={"library_key": library.library_key}
            )
        except Exception:
            self.storage.set_library_vector_store(library.id, None, "FAILED")
            raise
        self.storage.set_library_vector_store(library.id, vector_store.id, "READY")
        return vector_store.id

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


def _browser_source_path(value: str) -> str:
    if not isinstance(value, str) or len(value) > 500 or any(ord(character) < 32 for character in value):
        raise ValueError("source relative path is invalid")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("source relative path is invalid")
    if not normalized.casefold().endswith(".txt"):
        raise ValueError("Only .txt source transcripts are accepted.")
    library_definition_for_browser_path(normalized)
    return normalized


def _library_relative_path(value: str) -> str:
    parts = PurePosixPath(value).parts
    return PurePosixPath(*parts[2:]).as_posix()


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


def _wait_for_library_indexing(
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
            raise RuntimeError("source indexing failed")
        sleep(0.5)
    raise TimeoutError("source indexing did not finish")
