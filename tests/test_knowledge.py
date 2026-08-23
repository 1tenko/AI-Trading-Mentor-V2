from dataclasses import FrozenInstanceError

import pytest

from mentor.knowledge import Collection, Source, SourceRevision


def test_source_identity_is_stable_when_its_filename_metadata_changes():
    collection = Collection(
        collection_id="collection_jacob",
        display_name="Synthetic mentor library",
        domain="trading",
        enabled=True,
        scope="2025-2026",
    )
    original = Source.create(
        collection_id=collection.collection_id,
        identity_key="legacy:file_42",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Opening range",
        year=2026,
        original_filename="opening-range.txt",
        local_provenance="C:/synthetic/opening-range.txt",
    )
    renamed = Source.create(
        collection_id=collection.collection_id,
        identity_key="legacy:file_42",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Renamed lesson",
        year=2026,
        original_filename="renamed.txt",
        local_provenance="C:/synthetic/renamed.txt",
    )

    assert original.source_id == renamed.source_id
    assert original.source_id.startswith("src_")


def test_source_revision_identity_contains_its_sha256_and_cannot_change():
    revision = SourceRevision.create(
        source_id="src_synthetic",
        content_sha256="a" * 64,
        byte_size=42,
        local_locator="C:/synthetic/opening-range.txt",
        observed_at=1_700_000_000.0,
        lifecycle_state="active",
    )

    assert revision.content_sha256 in revision.revision_id
    with pytest.raises(FrozenInstanceError):
        revision.lifecycle_state = "superseded"
