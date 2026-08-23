"""Generic, immutable identifiers for the local knowledge library."""

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class Collection:
    collection_id: str
    display_name: str
    domain: str
    enabled: bool
    scope: str


@dataclass(frozen=True)
class Source:
    source_id: str
    collection_id: str
    identity_key: str
    source_type: str
    author: str
    course: str
    lesson_title: str
    year: int
    original_filename: str
    local_provenance: str

    @classmethod
    def create(
        cls,
        *,
        collection_id: str,
        identity_key: str,
        source_type: str,
        author: str,
        course: str,
        lesson_title: str,
        year: int,
        original_filename: str,
        local_provenance: str,
    ) -> "Source":
        source_id = sha256(f"{collection_id}\0{identity_key}".encode()).hexdigest()
        return cls(
            source_id=f"src_{source_id}",
            collection_id=collection_id,
            identity_key=identity_key,
            source_type=source_type,
            author=author,
            course=course,
            lesson_title=lesson_title,
            year=year,
            original_filename=original_filename,
            local_provenance=local_provenance,
        )


@dataclass(frozen=True)
class SourceRevision:
    revision_id: str
    source_id: str
    content_sha256: str
    byte_size: int
    local_locator: str
    observed_at: float
    lifecycle_state: str
    remote_file_id: str | None = None
    remote_vector_store_file_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        source_id: str,
        content_sha256: str,
        byte_size: int,
        local_locator: str,
        observed_at: float,
        lifecycle_state: str,
        remote_file_id: str | None = None,
        remote_vector_store_file_id: str | None = None,
    ) -> "SourceRevision":
        content_sha256 = content_sha256.lower()
        return cls(
            revision_id=f"rev_{source_id}_{content_sha256}",
            source_id=source_id,
            content_sha256=content_sha256,
            byte_size=byte_size,
            local_locator=local_locator,
            observed_at=observed_at,
            lifecycle_state=lifecycle_state,
            remote_file_id=remote_file_id,
            remote_vector_store_file_id=remote_vector_store_file_id,
        )
