import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentor.compiler import SourceExtractor
from mentor.compilation import CompilationRun, CorpusSnapshot, TokenPricing
from mentor.compiler_prompts import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_RESPONSE_SCHEMA,
    EXTRACTION_SCHEMA_VERSION,
)
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.derived_records import ProcedureSequenceHierarchy, Relationship
from mentor.storage import Storage


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "compiler_responses.json").read_text())


class FakeResponses:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(output_text=json.dumps(self.payload))


def source_for():
    return Source.create(
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


def revision_for(transcript="Synthetic source text."):
    source = source_for()
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


def test_live_sol_extraction_requires_pricing_and_records_reasoning_cost():
    response = SimpleNamespace(
        output_text=json.dumps(FIXTURES["empty"]),
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=40,
            output_tokens_details=SimpleNamespace(reasoning_tokens=25),
        ),
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_request: response))
    with pytest.raises(ValueError, match="pricing"):
        SourceExtractor(client, model="gpt-5.6-sol", live_mode=True)
    extractor = SourceExtractor(
        client, model="gpt-5.6-sol", live_mode=True,
        pricing=TokenPricing(2.0, 4.0, 6.0),
    )

    result = extractor.extract(
        revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text."
    )

    assert result.usage.reasoning_tokens == 25
    assert result.usage.cost_usd == pytest.approx((100 * 2 + 15 * 4 + 25 * 6) / 1_000_000)


def test_concrete_extraction_emits_relationship_procedure_and_alias_hints():
    transcript = "Synthetic source text."
    revision = revision_for(transcript)
    payload = {
        "candidates": [
            {
                "family": "relationship",
                "anchors": ["anc_relationship"],
                "qualification": "Synthetic relationship.",
                "left": "Primary signal",
                "relation": "depends_on",
                "right": "Context filter",
                "concept_hints": [
                    {
                        "label": "Primary signal",
                        "aliases": ["PS"],
                        "scope": "entry",
                        "role": "left",
                        "position": None,
                    }
                ],
            },
            {
                "family": "procedure_sequence_hierarchy",
                "anchors": ["anc_procedure"],
                "qualification": "Synthetic ordered process.",
                "kind": "procedure",
                "terms": ["Observe", "Validate", "Act"],
                "concept_hints": [
                    {
                        "label": "Validate",
                        "aliases": ["Confirm"],
                        "scope": None,
                        "role": "term",
                        "position": 1,
                    }
                ],
            },
        ]
    }
    extractor, _responses = extractor_for(payload)

    result = extractor.extract(
        revision=revision,
        snapshot_id="snap_synthetic",
        transcript=transcript,
        anchor_spans={"anc_relationship": "relationship", "anc_procedure": "procedure"},
    )

    assert isinstance(result.candidates[0], Relationship)
    assert isinstance(result.candidates[1], ProcedureSequenceHierarchy)
    assert all(record.derived_kind == "source_extracted_claim" for record in result.candidates)
    assert result.hints[0].aliases == ("PS",)
    assert result.hints[1].role == "term"
    assert result.hints[1].position == 1


def test_allows_a_source_to_yield_zero_candidates():
    extractor, responses = extractor_for(FIXTURES["empty"])

    result = extractor.extract(revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text.")

    assert result.candidates == ()
    assert len(responses.requests) == 1


def test_schema_and_parser_share_hard_anchor_bounds():
    anchor_schema = EXTRACTION_RESPONSE_SCHEMA["properties"]["candidates"]["items"]["properties"]["anchors"]
    assert anchor_schema["maxItems"] == 8
    assert anchor_schema["items"]["maxLength"] == 128
    payload = json.loads(json.dumps(FIXTURES["claim"]))
    payload["candidates"][0]["anchors"] = ["a" * 128] * 9
    extractor, _ = extractor_for(payload)

    with pytest.raises(ValueError, match="too many candidate anchors"):
        extractor.extract(revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text.")


def test_parser_rejects_an_anchor_id_past_the_schema_limit():
    payload = json.loads(json.dumps(FIXTURES["claim"]))
    payload["candidates"][0]["anchors"] = ["a" * 129]
    extractor, _ = extractor_for(payload)

    with pytest.raises(ValueError, match="invalid candidate anchor"):
        extractor.extract(revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text.")


def test_pending_extracted_candidate_cannot_bypass_semantic_validation_storage(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = revision_for()
    storage.store_collection(Collection("collection_synthetic", "Synthetic", "test", True, "test"))
    storage.store_source(source_for())
    storage.store_source_revision(revision)
    run = CompilationRun("run_compiler", "synthetic-compiler", EXTRACTION_PROMPT_VERSION, EXTRACTION_SCHEMA_VERSION, 1.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=[revision],
        raw_store_id="raw_synthetic",
        derived_store_id="derived_synthetic",
        created_at=1.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    extractor, _ = extractor_for(FIXTURES["claim"])

    candidate = extractor.extract(
        revision=revision, snapshot_id=snapshot.snapshot_id, transcript="Synthetic source text."
    ).candidates[0]
    with pytest.raises(ValueError, match="semantic validation"):
        storage.store_derived_record(candidate)

    assert storage.derived_records(snapshot.snapshot_id) == []


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
