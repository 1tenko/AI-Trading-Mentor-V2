"""Revision-specific transcript anchors with deterministic drift detection."""

from dataclasses import dataclass
from hashlib import sha256
import re
import unicodedata

from mentor.knowledge import SourceRevision


LOCATOR_VERSION = "transcript-v1"
_TIMESTAMP = re.compile(r"^\[(?:(\d{2}):)?(\d{2}):(\d{2})\]", re.MULTILINE)


@dataclass(frozen=True)
class SourceAnchor:
    anchor_id: str
    source_id: str
    revision_id: str
    revision_sha256: str
    start_offset: int
    end_offset: int
    timestamp_seconds: float | None
    span_fingerprint: str
    locator_version: str

    @classmethod
    def create(
        cls,
        *,
        revision: SourceRevision,
        transcript: str,
        start_offset: int,
        end_offset: int,
        locator_version: str = LOCATOR_VERSION,
    ) -> "SourceAnchor":
        if sha256(transcript.encode()).hexdigest() != revision.content_sha256:
            raise ValueError("transcript does not match revision SHA-256")
        normalized = normalize_transcript(transcript)
        _validate_offsets(start_offset, end_offset, normalized)
        span_fingerprint = _fingerprint(normalized[start_offset:end_offset])
        return cls(
            anchor_id=_anchor_id(revision.revision_id, start_offset, end_offset, locator_version),
            source_id=revision.source_id,
            revision_id=revision.revision_id,
            revision_sha256=revision.content_sha256,
            start_offset=start_offset,
            end_offset=end_offset,
            timestamp_seconds=_timestamp_at(normalized, start_offset) if locator_version == LOCATOR_VERSION else None,
            span_fingerprint=span_fingerprint,
            locator_version=locator_version,
        )


def normalize_transcript(transcript: str) -> str:
    return unicodedata.normalize("NFC", transcript.replace("\r\n", "\n").replace("\r", "\n"))


def validate_anchor(anchor: SourceAnchor, revision: SourceRevision, transcript: str) -> None:
    if anchor.locator_version != LOCATOR_VERSION:
        raise ValueError("unsupported locator version")
    if revision.revision_id != _revision_id(revision.source_id, revision.content_sha256):
        raise ValueError("revision_id does not match its source_id and SHA-256")
    if anchor.source_id != revision.source_id:
        raise ValueError("anchor source_id does not match revision source_id")
    if anchor.revision_id != revision.revision_id:
        raise ValueError("anchor revision_id does not match revision_id")
    if anchor.revision_sha256 != revision.content_sha256:
        raise ValueError("anchor revision SHA-256 does not match revision")
    if sha256(transcript.encode()).hexdigest() != revision.content_sha256:
        raise ValueError("transcript does not match revision SHA-256")

    normalized = normalize_transcript(transcript)
    _validate_offsets(anchor.start_offset, anchor.end_offset, normalized)
    if anchor.span_fingerprint != _fingerprint(normalized[anchor.start_offset:anchor.end_offset]):
        raise ValueError("span fingerprint does not match normalized transcript")
    if anchor.timestamp_seconds != _timestamp_at(normalized, anchor.start_offset):
        raise ValueError("timestamp does not match normalized transcript")
    if anchor.anchor_id != _anchor_id(anchor.revision_id, anchor.start_offset, anchor.end_offset, anchor.locator_version):
        raise ValueError("anchor_id does not match locator")


def _revision_id(source_id: str, content_sha256: str) -> str:
    return f"rev_{source_id}_{content_sha256}"


def _anchor_id(revision_id: str, start_offset: int, end_offset: int, locator_version: str) -> str:
    value = f"{revision_id}\0{start_offset}\0{end_offset}\0{locator_version}"
    return f"anc_{sha256(value.encode()).hexdigest()}"


def _fingerprint(span: str) -> str:
    return sha256(span.encode()).hexdigest()


def _validate_offsets(start_offset: int, end_offset: int, transcript: str) -> None:
    if not 0 <= start_offset < end_offset <= len(transcript):
        raise ValueError("invalid normalized offsets")


def _timestamp_at(transcript: str, offset: int) -> float | None:
    timestamp = None
    for match in _TIMESTAMP.finditer(transcript):
        if match.start() > offset:
            break
        hours, minutes, seconds = match.groups(default="0")
        timestamp = float(int(hours) * 3600 + int(minutes) * 60 + int(seconds))
    return timestamp
