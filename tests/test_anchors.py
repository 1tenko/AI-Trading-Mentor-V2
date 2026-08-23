from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from mentor.anchors import SourceAnchor, normalize_transcript, validate_anchor
from mentor.knowledge import Source, SourceRevision


FIXTURE_TEXT = (Path(__file__).parent / "fixtures" / "anchor_transcript.txt").read_text()


def revision_for(source_id: str, transcript: str) -> SourceRevision:
    return SourceRevision.create(
        source_id=source_id,
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
    assert anchor.timestamp_seconds == 90.0
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
        validate_anchor(replace(drifted_anchor, timestamp_seconds=anchor.timestamp_seconds), drifted_revision, drifted_text)


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
    revision = revision_for(original.source_id, FIXTURE_TEXT)
    duplicate_name_revision = revision_for(duplicate.source_id, FIXTURE_TEXT)
    anchor = anchor_for(revision)

    with pytest.raises(ValueError, match="source_id"):
        validate_anchor(anchor, duplicate_name_revision, FIXTURE_TEXT)


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
