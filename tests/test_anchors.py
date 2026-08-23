from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from mentor.anchors import SourceAnchor, normalize_transcript, validate_anchor
from mentor.knowledge import Source, SourceRevision


FIXTURE_TEXT = (Path(__file__).parent / "fixtures" / "anchor_transcript.txt").read_text(encoding="utf-8")


def source_for(identity_key: str) -> Source:
    return Source.create(
        collection_id="collection_synthetic",
        identity_key=identity_key,
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Synthetic lesson",
        year=2026,
        original_filename="synthetic.txt",
        local_provenance="C:/synthetic/synthetic.txt",
    )


def revision_for(source: Source | str, transcript: str) -> SourceRevision:
    if isinstance(source, str):
        source = source_for(source)
    return SourceRevision.create(
        source=source,
        content_sha256=sha256(transcript.encode()).hexdigest(),
        byte_size=len(transcript.encode()),
        local_locator="C:/synthetic/duplicate-looking-lesson.txt",
        observed_at=1_700_000_000.0,
        lifecycle_state="active",
    )


def anchor_for(revision: SourceRevision, transcript: str = FIXTURE_TEXT) -> SourceAnchor:
    normalized = normalize_transcript(transcript)
    start_offset = normalized.index("Wait for the liquidity sweep")
    end_offset = start_offset + len("Wait for the liquidity sweep before entry.")
    return SourceAnchor.create(
        revision=revision,
        transcript=transcript,
        start_offset=start_offset,
        end_offset=end_offset,
    )


def test_valid_anchor_binds_a_normalized_span_to_its_specific_revision():
    transcript = FIXTURE_TEXT.replace("\n", "\r\n")
    revision = revision_for("src_synthetic", transcript)
    anchor = anchor_for(revision, transcript)

    validate_anchor(anchor, revision, transcript)

    assert anchor.revision_sha256 == revision.content_sha256
    assert anchor.span_fingerprint == sha256("Wait for the liquidity sweep before entry.".encode()).hexdigest()
    assert (anchor.timestamp_start_ms, anchor.timestamp_end_ms) == (90_000, 90_000)
    assert anchor.locator_version == "transcript-v1"


def test_anchor_rejects_a_changed_revision_or_span():
    revision = revision_for("src_synthetic", FIXTURE_TEXT)
    anchor = anchor_for(revision)
    changed_text = FIXTURE_TEXT.replace("liquidity sweep", "liquidity run")
    changed_revision = revision_for("src_synthetic", changed_text)

    with pytest.raises(ValueError, match="revision_id"):
        validate_anchor(anchor, changed_revision, changed_text)
    with pytest.raises(ValueError, match="span fingerprint"):
        validate_anchor(replace(anchor, span_fingerprint="0" * 64), revision, FIXTURE_TEXT)


def test_anchor_rejects_invalid_normalized_offsets():
    revision = revision_for("src_synthetic", FIXTURE_TEXT)
    anchor = anchor_for(revision)

    with pytest.raises(ValueError, match="normalized offsets"):
        validate_anchor(replace(anchor, start_offset=-1), revision, FIXTURE_TEXT)


def test_anchor_rejects_timestamp_drift_when_the_locator_supports_timestamps():
    revision = revision_for("src_synthetic", FIXTURE_TEXT)
    anchor = anchor_for(revision)
    drifted_text = FIXTURE_TEXT.replace("[00:01:30]", "[00:01:31]")
    drifted_revision = revision_for("src_synthetic", drifted_text)
    drifted_anchor = anchor_for(drifted_revision, drifted_text)

    with pytest.raises(ValueError, match="timestamp"):
        validate_anchor(
            replace(drifted_anchor, timestamp_start_ms=anchor.timestamp_start_ms), drifted_revision, drifted_text
        )


def test_anchor_rejects_a_duplicate_looking_name_with_a_different_source_id():
    original = Source.create(
        collection_id="collection_synthetic",
        identity_key="legacy:file_1",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Duplicate-looking lesson",
        year=2026,
        original_filename="duplicate-looking-lesson.txt",
        local_provenance="C:/synthetic/duplicate-looking-lesson.txt",
    )
    duplicate = Source.create(
        collection_id="collection_synthetic",
        identity_key="legacy:file_2",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Duplicate-looking lesson",
        year=2026,
        original_filename="duplicate-looking-lesson.txt",
        local_provenance="C:/synthetic/duplicate-looking-lesson.txt",
    )
    revision = revision_for(original, FIXTURE_TEXT)
    duplicate_name_revision = revision_for(duplicate, FIXTURE_TEXT)
    anchor = anchor_for(revision)

    with pytest.raises(ValueError, match="source_id"):
        validate_anchor(anchor, duplicate_name_revision, FIXTURE_TEXT)


def test_revision_and_anchor_retain_collection_identity_and_reject_tampering():
    source = Source.create(
        collection_id="collection_synthetic",
        identity_key="legacy:file_collection",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Collection-bound lesson",
        year=2026,
        original_filename="collection-bound.txt",
        local_provenance="C:/synthetic/collection-bound.txt",
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256=sha256(FIXTURE_TEXT.encode()).hexdigest(),
        byte_size=len(FIXTURE_TEXT.encode()),
        local_locator="C:/synthetic/collection-bound.txt",
        observed_at=1_700_000_000.0,
        lifecycle_state="active",
    )
    anchor = anchor_for(revision)

    validate_anchor(anchor, revision, FIXTURE_TEXT)

    assert revision.collection_id == source.collection_id
    assert anchor.collection_id == source.collection_id
    with pytest.raises(ValueError, match="collection_id"):
        validate_anchor(replace(anchor, collection_id="collection_tampered"), revision, FIXTURE_TEXT)


def test_anchor_records_timestamp_range_when_a_span_crosses_a_later_marker():
    revision = revision_for("src_synthetic", FIXTURE_TEXT)
    normalized = normalize_transcript(FIXTURE_TEXT)
    start_offset = normalized.index("Wait for the liquidity sweep")
    end_offset = normalized.index("Define risk before placing the trade.") + len("Define risk before placing the trade.")
    anchor = SourceAnchor.create(
        revision=revision,
        transcript=FIXTURE_TEXT,
        start_offset=start_offset,
        end_offset=end_offset,
    )

    validate_anchor(anchor, revision, FIXTURE_TEXT)

    assert (anchor.timestamp_start_ms, anchor.timestamp_end_ms) == (90_000, 120_000)


def test_anchor_uses_nfc_normalized_coordinates_for_decomposed_fixture_text():
    transcript = FIXTURE_TEXT.replace("caf\u00e9", "cafe\u0301")
    revision = revision_for("src_synthetic", transcript)
    normalized = normalize_transcript(transcript)
    start_offset = normalized.index("caf\u00e9")
    end_offset = start_offset + len("caf\u00e9 setup")
    anchor = SourceAnchor.create(
        revision=revision,
        transcript=transcript,
        start_offset=start_offset,
        end_offset=end_offset,
    )

    validate_anchor(anchor, revision, transcript)

    assert anchor.span_fingerprint == sha256("caf\u00e9 setup".encode()).hexdigest()


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("anchor_id", "anc_tampered", "anchor_id"),
        ("revision_id", "rev_tampered", "revision_id"),
        ("revision_sha256", "f" * 64, "revision SHA-256"),
        ("locator_version", "transcript-v0", "locator version"),
    ],
)
def test_anchor_rejects_tampered_identity_hash_or_locator_version(field, value, message):
    revision = revision_for("src_synthetic", FIXTURE_TEXT)
    anchor = anchor_for(revision)

    with pytest.raises(ValueError, match=message):
        validate_anchor(replace(anchor, **{field: value}), revision, FIXTURE_TEXT)
