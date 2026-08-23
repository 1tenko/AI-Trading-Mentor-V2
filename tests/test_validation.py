import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentor.anchors import SourceAnchor, normalize_transcript
from mentor.compiler import SourceExtractor
from mentor.compiler_prompts import SEMANTIC_VALIDATION_PROMPT_VERSION
from mentor.knowledge import Source, SourceRevision
from mentor.validation import SemanticValidator, can_publish_source_extracted


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "validation_responses.json").read_text())
TRANSCRIPT = "[00:01:30] Wait for the liquidity sweep before entry.\n"


class FakeResponses:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(output_text=json.dumps(self.payload))


def source_and_revision():
    source = Source.create(
        collection_id="collection_synthetic",
        identity_key="synthetic:validation",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Synthetic lesson",
        year=2026,
        original_filename="synthetic.txt",
        local_provenance="C:/synthetic/synthetic.txt",
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256=sha256(TRANSCRIPT.encode()).hexdigest(),
        byte_size=len(TRANSCRIPT.encode()),
        local_locator="C:/synthetic/synthetic.txt",
        observed_at=1.0,
        lifecycle_state="active",
    )
    return source, revision


def candidate_and_anchor():
    _, revision = source_and_revision()
    normalized = normalize_transcript(TRANSCRIPT)
    start = normalized.index("Wait for")
    anchor = SourceAnchor.create(
        revision=revision,
        transcript=TRANSCRIPT,
        start_offset=start,
        end_offset=start + len("Wait for the liquidity sweep before entry."),
    )
    extractor = SourceExtractor(SimpleNamespace(responses=FakeResponses({"candidates": [{
        "family": "claim",
        "anchors": [anchor.anchor_id],
        "qualification": "EXTRACTOR-RATIONALE-MUST-NOT-REACH-VALIDATOR",
        "subject": "liquidity sweep",
        "predicate": "precedes",
        "object": "entry",
    }]})))
    candidate = extractor.extract(revision=revision, snapshot_id="snap_synthetic", transcript=TRANSCRIPT).candidates[0]
    return revision, candidate, anchor


@pytest.mark.parametrize("outcome", list(FIXTURES))
def test_semantic_validation_only_allows_affirmatively_supported_source_extracted_claims(outcome):
    revision, candidate, anchor = candidate_and_anchor()
    responses = FakeResponses(FIXTURES[outcome])

    result = SemanticValidator(SimpleNamespace(responses=responses)).validate(
        candidate=candidate, revision=revision, transcript=TRANSCRIPT, anchors={anchor.anchor_id: anchor}
    )

    assert result.outcome == outcome
    assert result.audit == FIXTURES[outcome]["audit"]
    assert result.source_extracted is (candidate if outcome == "affirmatively_supported" else None)
    assert can_publish_source_extracted([result]) is (outcome == "affirmatively_supported")


def test_semantic_request_has_its_own_versioned_prompt_and_never_receives_extractor_rationale():
    revision, candidate, anchor = candidate_and_anchor()
    responses = FakeResponses(FIXTURES["affirmatively_supported"])

    SemanticValidator(SimpleNamespace(responses=responses)).validate(
        candidate=candidate, revision=revision, transcript=TRANSCRIPT, anchors={anchor.anchor_id: anchor}
    )

    request = responses.requests[0]
    assert SEMANTIC_VALIDATION_PROMPT_VERSION in request["instructions"]
    assert "EXTRACTOR-RATIONALE-MUST-NOT-REACH-VALIDATOR" not in request["input"]
    assert "Wait for the liquidity sweep before entry." in request["input"]


@pytest.mark.parametrize(
    "anchor_change, message",
    [
        (lambda anchor: replace(anchor, revision_sha256="0" * 64), "SHA-256"),
        (lambda anchor: replace(anchor, end_offset=len(TRANSCRIPT) + 1), "normalized offsets"),
    ],
)
def test_deterministic_anchor_failures_block_semantic_validation(anchor_change, message):
    revision, candidate, anchor = candidate_and_anchor()
    responses = FakeResponses(FIXTURES["affirmatively_supported"])

    with pytest.raises(ValueError, match=message):
        SemanticValidator(SimpleNamespace(responses=responses)).validate(
            candidate=candidate,
            revision=revision,
            transcript=TRANSCRIPT,
            anchors={anchor.anchor_id: anchor_change(anchor)},
        )

    assert responses.requests == []
