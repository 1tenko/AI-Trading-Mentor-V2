import json
import sqlite3
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentor.anchors import SourceAnchor, normalize_transcript
from mentor.compiler import SourceExtractor
from mentor.compiler_prompts import SEMANTIC_VALIDATION_PROMPT_VERSION
from mentor.compilation import CompilationRun, CorpusSnapshot
from mentor.derived_records import ConflictUnresolved, Evolution, ProcedureSequenceHierarchy, RecordDependency, Relationship
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import Storage
from mentor.validation import SemanticValidator, ValidationResult, can_publish_source_extracted


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


def candidate_and_anchor(snapshot_id="snap_synthetic"):
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
    candidate = extractor.extract(revision=revision, snapshot_id=snapshot_id, transcript=TRANSCRIPT).candidates[0]
    return revision, candidate, anchor


def validation_storage(tmp_path):
    source, revision = source_and_revision()
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.store_collection(Collection(source.collection_id, "Synthetic", "test", True, "test"))
    storage.store_source(source)
    storage.store_source_revision(revision)
    run = CompilationRun("run_validation", "synthetic", "prompt", "schema", 1.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=[revision],
        raw_store_id="raw_synthetic",
        derived_store_id="derived_synthetic",
        created_at=1.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    return storage, snapshot


@pytest.mark.parametrize("outcome", list(FIXTURES))
def test_semantic_validation_only_allows_affirmatively_supported_source_extracted_claims(outcome):
    revision, candidate, anchor = candidate_and_anchor()
    responses = FakeResponses(FIXTURES[outcome])

    result = SemanticValidator(SimpleNamespace(responses=responses)).validate(
        candidate=candidate, revision=revision, transcript=TRANSCRIPT, anchors={anchor.anchor_id: anchor}
    )

    assert result.outcome == outcome
    assert result.audit == FIXTURES[outcome]["audit"]
    if outcome == "affirmatively_supported":
        assert result.source_extracted is not None
        assert result.source_extracted.record_id != candidate.record_id
        assert result.source_extracted.validation_state == "validated"
    else:
        assert result.source_extracted is None
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


@pytest.mark.parametrize("outcome", [name for name in FIXTURES if name != "affirmatively_supported"])
def test_normal_storage_excludes_each_nonaffirmative_source_candidate_and_persists_its_audit(tmp_path, outcome):
    storage, snapshot = validation_storage(tmp_path)
    revision, candidate, anchor = candidate_and_anchor(snapshot.snapshot_id)
    responses = FakeResponses(FIXTURES[outcome])

    with pytest.raises(ValueError, match="semantic validation"):
        storage.store_derived_record(candidate)
    result = SemanticValidator(SimpleNamespace(responses=responses)).validate(
        candidate=candidate, revision=revision, transcript=TRANSCRIPT, anchors={anchor.anchor_id: anchor}
    )
    storage.store_validation_result(result)

    assert storage.derived_records(snapshot.snapshot_id) == []
    assert storage.validation_audits(snapshot.snapshot_id) == [
        (candidate.record_id, outcome, FIXTURES[outcome]["audit"], None)
    ]


def test_normal_storage_persists_only_the_validated_affirmative_replacement(tmp_path):
    storage, snapshot = validation_storage(tmp_path)
    revision, candidate, anchor = candidate_and_anchor(snapshot.snapshot_id)
    result = SemanticValidator(SimpleNamespace(responses=FakeResponses(FIXTURES["affirmatively_supported"]))).validate(
        candidate=candidate, revision=revision, transcript=TRANSCRIPT, anchors={anchor.anchor_id: anchor}
    )

    with pytest.raises(ValueError, match="semantic validation result"):
        storage.store_derived_record(result.source_extracted)
    storage.store_validation_result(result)

    assert storage.derived_records(snapshot.snapshot_id) == [result.source_extracted]
    assert storage.derived_records(snapshot.snapshot_id)[0].compiler_provenance == candidate.compiler_provenance
    assert storage.validation_audits(snapshot.snapshot_id) == [
        (candidate.record_id, "affirmatively_supported", FIXTURES["affirmatively_supported"]["audit"], result.source_extracted.record_id)
    ]


def test_semantic_validator_rejects_sol_before_any_mock_client_call():
    responses = FakeResponses(FIXTURES["affirmatively_supported"])

    with pytest.raises(ValueError, match="injected mock"):
        SemanticValidator(SimpleNamespace(responses=responses), model="gpt-5.6-sol")

    assert responses.requests == []


def test_semantic_validator_rejects_non_claim_records_before_any_mock_client_call():
    _, revision = source_and_revision()
    non_claim = Relationship.create(
        snapshot_id="snap_synthetic",
        anchors=("anc_synthetic",),
        dependencies=(RecordDependency("source_revision", revision.revision_id),),
        validation_state="pending",
        lifecycle_state="candidate",
        qualification="Synthetic.",
        left="one",
        relation="supports",
        right="two",
    )
    responses = FakeResponses(FIXTURES["affirmatively_supported"])

    with pytest.raises(ValueError, match="Claim candidates"):
        SemanticValidator(SimpleNamespace(responses=responses)).validate(
            candidate=non_claim, revision=revision, transcript=TRANSCRIPT, anchors={}
        )

    assert responses.requests == []


@pytest.mark.parametrize("family", ["claim", "relationship", "procedure", "evolution", "conflict"])
def test_storage_rejects_forged_validation_results_for_every_derived_family(tmp_path, family):
    storage, snapshot = validation_storage(tmp_path)
    revision, candidate, _ = candidate_and_anchor(snapshot.snapshot_id)
    common = {
        "snapshot_id": snapshot.snapshot_id,
        "anchors": ("anc_synthetic",),
        "dependencies": (RecordDependency("source_revision", revision.revision_id),),
        "validation_state": "validated",
        "lifecycle_state": "candidate",
        "qualification": "Synthetic.",
    }
    record = {
        "claim": candidate,
        "relationship": Relationship.create(**common, left="one", relation="supports", right="two"),
        "procedure": ProcedureSequenceHierarchy.create(**common, kind="procedure", terms=("one", "two")),
        "evolution": Evolution.create(**common, subject="one", previous="old", current="new"),
        "conflict": ConflictUnresolved.create(
            **common, kind="conflict", subject="one", alternatives=("first", "second")
        ),
    }[family]
    forged = ValidationResult(
        candidate.record_id,
        snapshot.snapshot_id,
        "affirmatively_supported",
        "Forged audit.",
        (),
        record,
    )

    with pytest.raises(ValueError, match="validator-issued"):
        storage.store_validation_result(forged)

    assert storage.derived_records(snapshot.snapshot_id) == []
    assert storage.validation_audits(snapshot.snapshot_id) == []


def test_validation_result_storage_rolls_back_a_validated_record_when_its_audit_insert_fails(tmp_path):
    storage, snapshot = validation_storage(tmp_path)
    revision, candidate, anchor = candidate_and_anchor(snapshot.snapshot_id)
    partial = SemanticValidator(SimpleNamespace(responses=FakeResponses(FIXTURES["partially_supported"]))).validate(
        candidate=candidate, revision=revision, transcript=TRANSCRIPT, anchors={anchor.anchor_id: anchor}
    )
    affirmative = SemanticValidator(SimpleNamespace(responses=FakeResponses(FIXTURES["affirmatively_supported"]))).validate(
        candidate=candidate, revision=revision, transcript=TRANSCRIPT, anchors={anchor.anchor_id: anchor}
    )
    storage.store_validation_result(partial)

    with pytest.raises(sqlite3.IntegrityError):
        storage.store_validation_result(affirmative)

    assert storage.derived_records(snapshot.snapshot_id) == []
