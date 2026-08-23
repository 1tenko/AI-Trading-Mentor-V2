import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentor.compiler import SourceExtractor
from mentor.compiler_prompts import EXTRACTION_PROMPT_VERSION, EXTRACTION_SCHEMA_VERSION
from mentor.knowledge import Source, SourceRevision


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "compiler_responses.json").read_text())


class FakeResponses:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(output_text=json.dumps(self.payload))


def revision_for(transcript="Synthetic source text."):
    source = Source.create(
        collection_id="collection_synthetic",
        identity_key="synthetic:compiler",
        source_type="transcript",
        author="Synthetic",
        course="Synthetic",
        lesson_title="Synthetic",
        year=2026,
        original_filename="synthetic.txt",
        local_provenance="C:/synthetic/synthetic.txt",
    )
    return SourceRevision.create(
        source=source,
        content_sha256=sha256(transcript.encode()).hexdigest(),
        byte_size=len(transcript.encode()),
        local_locator="C:/synthetic/synthetic.txt",
        observed_at=1.0,
        lifecycle_state="active",
    )


def extractor_for(payload):
    responses = FakeResponses(payload)
    return SourceExtractor(SimpleNamespace(responses=responses), model="synthetic-compiler"), responses


def test_extracts_bounded_pending_candidates_for_one_revision_with_versioned_request():
    extractor, responses = extractor_for(FIXTURES["claim"])
    revision = revision_for()

    result = extractor.extract(revision=revision, snapshot_id="snap_synthetic", transcript="Synthetic source text.")

    assert result.revision_id == revision.revision_id
    assert result.provenance.model_version == "synthetic-compiler"
    assert result.provenance.prompt_version == EXTRACTION_PROMPT_VERSION
    assert result.provenance.schema_version == EXTRACTION_SCHEMA_VERSION
    assert result.candidates[0].validation_state == "pending"
    assert result.candidates[0].lifecycle_state == "candidate"
    assert result.candidates[0].dependencies[0].identifier == revision.revision_id
    assert responses.requests[0]["model"] == "synthetic-compiler"
    assert EXTRACTION_PROMPT_VERSION in responses.requests[0]["instructions"]
    assert responses.requests[0]["text"]["format"]["name"] == EXTRACTION_SCHEMA_VERSION
    assert revision.revision_id in responses.requests[0]["input"]


def test_allows_a_source_to_yield_zero_candidates():
    extractor, responses = extractor_for(FIXTURES["empty"])

    result = extractor.extract(revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text.")

    assert result.candidates == ()
    assert len(responses.requests) == 1


@pytest.mark.parametrize(
    "fixture, message",
    [
        ("malformed_family", "unknown derived record family"),
        ("missing_anchors", "records require at least one anchor"),
        ("self_validated", "self-validation"),
    ],
)
def test_rejects_invalid_candidate_output(fixture, message):
    extractor, _ = extractor_for(FIXTURES[fixture])

    with pytest.raises(ValueError, match=message):
        extractor.extract(revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text.")


def test_requires_explicit_live_mode_for_sol_without_invoking_a_client():
    with pytest.raises(ValueError, match="live mode"):
        SourceExtractor(SimpleNamespace(responses=FakeResponses(FIXTURES["empty"])), model="gpt-5.6-sol")
