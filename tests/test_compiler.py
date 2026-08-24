import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai.lib._pydantic import _ensure_strict_json_schema

from mentor.compiler import SourceExtractor
from mentor.compilation import CompilationRun, CorpusSnapshot, TokenPricing
from mentor.compiler_prompts import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_RESPONSE_SCHEMA,
    EXTRACTION_SCHEMA_VERSION,
)
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.derived_records import ProcedureRecordBranch, ProcedureSequenceHierarchy, Relationship
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


def test_extracted_strategy_implication_remains_raw_taught_only_pending_independent_validation():
    payload = {
        "candidates": [{
            "family": "claim",
            "anchors": ["anc_strategy"],
            "qualification": "The synthetic source explicitly teaches this implication.",
            "subject": "context",
            "predicate": "implies",
            "object": "a bounded action",
            "semantic_subtype": "strategy_implication",
            "concept_hints": [],
        }]
    }
    extractor, _responses = extractor_for(payload)

    [candidate] = extractor.extract(
        revision=revision_for(),
        snapshot_id="snap_synthetic",
        transcript="Synthetic source text.",
    ).candidates

    assert candidate.derived_kind == "source_extracted_claim"
    assert candidate.evidence_state == "raw_taught"
    assert candidate.validation_state == "pending"


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
                "relation": "anticipates",
                "right": "Context filter",
                "concept_hints": [
                    {
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
                "prerequisites": ["Market context"],
                "conditions": ["Only after confirmation"],
                "branches": [
                    {"condition": "If confirmation fails", "steps": ["Observe"]}
                ],
                "concept_hints": [
                    {
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
    assert result.candidates[0].relation == "anticipates"
    assert result.candidates[1].prerequisites == ("Market context",)
    assert result.candidates[1].conditions == ("Only after confirmation",)
    assert result.candidates[1].branches == (
        ProcedureRecordBranch("If confirmation fails", ("Observe",)),
    )
    assert result.hints[0].aliases == ("PS",)
    assert result.hints[1].role == "term"
    assert result.hints[1].position == 1


def test_extraction_derives_a_hint_label_from_its_typed_record_selector():
    payload = {
        "candidates": [{
            "family": "claim",
            "anchors": ["anc_selector"],
            "qualification": "Synthetic selector claim.",
            "subject": "Compact concept",
            "predicate": "guides",
            "object": "bounded context",
            "semantic_subtype": "statement",
            "concept_hints": [{
                "aliases": ["CC"],
                "scope": "entry",
                "role": "subject",
                "position": None,
            }],
        }]
    }
    extractor, _responses = extractor_for(payload)

    result = extractor.extract(
        revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text."
    )

    assert len(result.hints) == 1
    assert result.hints[0].label == "Compact concept"
    assert result.hints[0].role == "subject"
    assert result.hints[0].position is None


def test_extraction_rejects_a_hint_selector_outside_the_typed_record_shape():
    payload = {
        "candidates": [{
            "family": "claim",
            "anchors": ["anc_selector"],
            "qualification": "Synthetic selector claim.",
            "subject": "Compact concept",
            "predicate": "guides",
            "object": "bounded context",
            "semantic_subtype": "statement",
            "concept_hints": [{
                "aliases": [],
                "scope": None,
                "role": "term",
                "position": 0,
            }],
        }]
    }
    extractor, _responses = extractor_for(payload)

    with pytest.raises(ValueError, match="concept hint selector does not identify one typed record occurrence"):
        extractor.extract(revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text.")


def test_extraction_rejects_an_overlong_record_term_before_semantic_validation():
    payload = {
        "candidates": [{
            "family": "claim",
            "anchors": ["anc_selector"],
            "qualification": "Synthetic selector claim.",
            "subject": "x" * 121,
            "predicate": "guides",
            "object": "bounded context",
            "semantic_subtype": "statement",
            "concept_hints": [],
        }]
    }
    extractor, _responses = extractor_for(payload)

    with pytest.raises(ValueError, match="concept label exceeds its maximum length"):
        extractor.extract(revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text.")


def test_allows_a_source_to_yield_zero_candidates():
    extractor, responses = extractor_for(FIXTURES["empty"])

    result = extractor.extract(revision=revision_for(), snapshot_id="snap_synthetic", transcript="Synthetic source text.")

    assert result.candidates == ()
    assert len(responses.requests) == 1


def test_schema_and_parser_share_hard_anchor_bounds():
    candidate_schemas = EXTRACTION_RESPONSE_SCHEMA["properties"]["candidates"]["items"]["anyOf"]
    anchor_schema = candidate_schemas[0]["properties"]["anchors"]
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


def test_extraction_schema_is_already_strict_under_the_installed_openai_sdk_contract():
    schema = deepcopy(EXTRACTION_RESPONSE_SCHEMA)
    converted = _ensure_strict_json_schema(schema, path=(), root=schema)

    assert converted == EXTRACTION_RESPONSE_SCHEMA

    pending = [converted]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                assert value.get("additionalProperties") is False
                assert set(value.get("required", ())) == set(properties)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


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
