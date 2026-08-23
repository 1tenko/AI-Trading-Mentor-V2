from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from mentor.anchors import SourceAnchor
from mentor.candidate_compiler import (
    ArtifactScope,
    BuildRequest,
    CandidateCompiler,
    CandidateSource,
    SynthesisResult,
)
from mentor.compilation import CompilationRun
from mentor.compiler import SourceExtractor
from mentor.derived_records import ConflictUnresolved, Evolution, RecordDependency, Relationship
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.orientation import OrientationBudget
from mentor.storage import Storage
from mentor.vector_stores import VectorStoreAdapter


class QueueResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(
            output_text=json.dumps(output),
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        )


class FakeVectorClient:
    def __init__(self, *, attachment_status="completed", batch_status="completed"):
        self.calls = []
        self.attachment_status = attachment_status
        self.batch_result_status = batch_status
        self.next_store = 0
        self.next_file = 0
        self.files = SimpleNamespace(create=self.upload)
        self.vector_store_files = SimpleNamespace(
            create=self.attach,
            retrieve=self.retrieve_attachment,
            delete=lambda *_args, **_kwargs: None,
        )
        self.file_batches = SimpleNamespace(create=self.batch, retrieve=self.retrieve_batch)
        self.vector_stores = SimpleNamespace(
            create=self.create_store,
            files=self.vector_store_files,
            file_batches=self.file_batches,
        )

    def create_store(self, **kwargs):
        self.next_store += 1
        self.calls.append(("create_store", kwargs))
        return SimpleNamespace(id=f"vs_synthetic_{self.next_store}", status="completed")

    def upload(self, **kwargs):
        self.next_file += 1
        filename, content, media_type = kwargs["file"]
        self.calls.append(("upload", filename, content.decode(), media_type, kwargs["purpose"]))
        return SimpleNamespace(id=f"file_derived_{self.next_file}")

    def attach(self, store_id, **kwargs):
        self.calls.append(("attach", store_id, kwargs))
        return SimpleNamespace(
            id=f"vsf_{kwargs['file_id']}",
            status=self.attachment_status,
            last_error=(
                SimpleNamespace(code="attachment_failed", message="Synthetic attachment failure.")
                if self.attachment_status == "failed"
                else None
            ),
        )

    def retrieve_attachment(self, file_id, **kwargs):
        self.calls.append(("retrieve_attachment", file_id, kwargs))
        return SimpleNamespace(id=f"vsf_{file_id}", status=self.attachment_status)

    def batch(self, store_id, **kwargs):
        self.calls.append(("batch", store_id, kwargs))
        return SimpleNamespace(
            id="batch_raw",
            status=self.batch_result_status,
            file_counts={self.batch_result_status: len(kwargs["file_ids"])},
            last_error=(
                SimpleNamespace(code="batch_failed", message="Synthetic raw batch failure.")
                if self.batch_result_status == "failed"
                else None
            ),
        )

    def retrieve_batch(self, batch_id, **kwargs):
        self.calls.append(("retrieve_batch", batch_id, kwargs))
        return SimpleNamespace(
            id=batch_id,
            status=self.batch_result_status,
            file_counts={self.batch_result_status: 1},
        )


class SyntheticSynthesizer:
    def __init__(self, *, stale=False):
        self.stale = stale
        self.calls = []

    def synthesize(self, *, snapshot_id, records, revisions):
        self.calls.append((snapshot_id, tuple(record.record_id for record in records)))
        if len(records) < 2:
            return SynthesisResult(())
        first, second = records[:2]
        source_dependencies = tuple(
            RecordDependency("source_revision", revision.revision_id) for revision in revisions
        )
        record_dependencies = (
            RecordDependency("derived_record", first.record_id),
            RecordDependency("derived_record", second.record_id),
        )
        common = {
            "snapshot_id": snapshot_id,
            "anchors": tuple(dict.fromkeys(first.anchors + second.anchors)),
            "dependencies": source_dependencies + record_dependencies,
            "validation_state": "validated",
            "lifecycle_state": "active",
            "qualification": "Synthetic cross-source comparison.",
            "evidence_state": "cross_source_synthesis",
        }
        relationship = Relationship.create(
            **(common | ({"lifecycle_state": "superseded"} if self.stale else {})),
            left=first.subject,
            relation="supports",
            right=second.subject,
        )
        evolution = Evolution.create(
            **common,
            subject="Synthetic framework",
            previous="Earlier bounded form",
            current="Later qualified form",
            earlier_source_set=(revisions[0].revision_id,),
            later_source_set=(revisions[1].revision_id,),
            classification="refined",
            negative_evidence_state="positive_teaching",
            earlier_coverage_id="coverage_earlier",
            later_coverage_id="coverage_later",
            earlier_observed_years=(2025,),
            later_observed_years=(2026,),
        )
        conflict = ConflictUnresolved.create(
            **common,
            kind="unresolved",
            subject="Synthetic context",
            alternatives=("Earlier condition", "Later condition"),
            competing_record_ids=(first.record_id, second.record_id),
            reconciliation_state="unresolved",
            relevant_scopes=("synthetic scope",),
            unresolved_questions=("Which synthetic condition applies?",),
        )
        return SynthesisResult((relationship, evolution, conflict))


def source_bundle(storage, identity, year):
    transcript = f"[00:00:01] Synthetic {identity} teaching for deterministic tests."
    collection = Collection("collection_synthetic", "Synthetic", "trading", True, "test")
    source = Source.create(
        collection_id=collection.collection_id,
        identity_key=identity,
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title=f"Synthetic {identity}",
        year=year,
        original_filename=f"{identity}.txt",
        local_provenance=f"C:/synthetic/{identity}.txt",
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256=sha256(transcript.encode()).hexdigest(),
        byte_size=len(transcript.encode()),
        local_locator=f"C:/synthetic/{identity}.txt",
        observed_at=float(year),
        lifecycle_state="active",
        remote_file_id=f"file_raw_{identity}",
    )
    storage.store_collection(collection)
    storage.store_source(source)
    storage.store_source_revision(revision)
    anchor = SourceAnchor.create(
        revision=revision,
        transcript=transcript,
        start_offset=0,
        end_offset=len(transcript),
    )
    return CandidateSource(revision, transcript, {anchor.anchor_id: anchor})


def compiler(tmp_path, *, extraction_outputs=None, validation_outputs=None, vector_client=None, synthesizer=None):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    sources = (
        source_bundle(storage, "earlier", 2025),
        source_bundle(storage, "later", 2026),
    )
    extraction_outputs = extraction_outputs or [
        {"candidates": [{
            "family": "claim",
            "anchors": [next(iter(source.anchors))],
            "qualification": "Synthetic anchored claim.",
            "subject": f"Synthetic {index}",
            "predicate": "guides",
            "object": "bounded context",
        }]}
        for index, source in enumerate(sources, start=1)
    ]
    validation_outputs = validation_outputs or [
        {"outcome": "affirmatively_supported", "audit": "Synthetic span supports the claim."}
        for _source in sources
    ]
    extraction_responses = QueueResponses(extraction_outputs)
    validation_responses = QueueResponses(validation_outputs)
    vector_client = vector_client or FakeVectorClient()
    candidate_compiler = CandidateCompiler(
        storage=storage,
        extractor=SourceExtractor(SimpleNamespace(responses=extraction_responses)),
        validation_client=SimpleNamespace(responses=validation_responses),
        synthesizer=synthesizer or SyntheticSynthesizer(),
        vector_stores=VectorStoreAdapter(vector_client),
        orientation_budget=OrientationBudget(max_records=12, max_tokens=10_000),
        readiness_checks=2,
        sleep=lambda _seconds: None,
    )
    request = BuildRequest(
        run=CompilationRun("run_candidate", "synthetic", "prompt-v1", "schema-v1", 1.0),
        sources=sources,
        artifact_scope=ArtifactScope.PILOT,
    )
    return storage, candidate_compiler, request, vector_client


def test_build_composes_a_ready_unpublished_candidate_with_typed_bounded_artifacts_and_metrics(tmp_path):
    storage, candidate_compiler, request, vector_client = compiler(tmp_path)

    result = candidate_compiler.build(request)

    assert result.ready is True
    assert result.snapshot.status == "validating"
    assert storage.current_snapshot() is None
    assert {record.family for record in result.records} == {
        "claim", "relationship", "evolution", "conflict_unresolved"
    }
    assert result.dependency_graph.edges
    assert result.raw_artifact.scope is ArtifactScope.PILOT
    assert result.derived_artifact.scope is ArtifactScope.PILOT
    assert len(result.orientation_artifacts) <= 12
    assert all(len(artifact.content.encode()) <= 10_000 for artifact in result.orientation_artifacts)
    assert all("Synthetic earlier teaching" not in artifact.content for artifact in result.orientation_artifacts)
    assert all(json.loads(artifact.content)["record_id"] == artifact.record_id for artifact in result.orientation_artifacts)
    assert all(artifact.attributes["artifact_scope"] == "pilot" for artifact in result.orientation_artifacts)
    assert result.total_metric.call_count == sum(metric.call_count for metric in result.stage_metrics)
    assert result.total_metric.input_tokens == 44
    assert result.total_metric.output_tokens == 28
    assert result.total_metric.remote_calls > 0
    assert any(call[0] == "batch" for call in vector_client.calls)
    assert len([call for call in vector_client.calls if call[0] == "upload"]) == len(result.orientation_artifacts)


def test_extraction_failure_is_visible_and_leaves_candidate_failed_and_unpublished(tmp_path):
    storage, candidate_compiler, request, _ = compiler(
        tmp_path,
        extraction_outputs=[RuntimeError("synthetic extraction failed"), {"candidates": []}],
    )

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    assert "extraction" in result.failures[0]
    assert storage.current_snapshot() is None
    assert result.total_metric.failure_count >= 1


def test_nonaffirmative_validation_blocks_candidate_readiness_and_excludes_the_claim(tmp_path):
    storage, candidate_compiler, request, _ = compiler(
        tmp_path,
        validation_outputs=[
            {"outcome": "unsupported", "audit": "Synthetic span does not support the claim."},
            {"outcome": "affirmatively_supported", "audit": "Synthetic span supports the claim."},
        ],
    )

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    assert any("validation" in failure for failure in result.failures)
    assert len(storage.derived_records(result.snapshot.snapshot_id)) == 1
    assert storage.current_snapshot() is None


def test_remote_readiness_failure_marks_candidate_failed_without_pointer_mutation(tmp_path):
    vector_client = FakeVectorClient(attachment_status="failed")
    storage, candidate_compiler, request, _ = compiler(tmp_path, vector_client=vector_client)

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    assert any("derived store" in failure for failure in result.failures)
    assert storage.current_snapshot() is None


def test_nonactive_synthesis_record_never_enters_an_orientation_artifact(tmp_path):
    storage, candidate_compiler, request, vector_client = compiler(
        tmp_path,
        synthesizer=SyntheticSynthesizer(stale=True),
    )

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    stale = next(
        record
        for record in storage.derived_records(result.snapshot.snapshot_id, include_stale=True)
        if record.lifecycle_state == "superseded"
    )
    assert stale.record_id not in {artifact.record_id for artifact in result.orientation_artifacts}
    assert all(stale.record_id not in call[2] for call in vector_client.calls if call[0] == "upload")


def test_explicit_stale_revision_invalidation_blocks_artifacts_and_readiness(tmp_path):
    storage, candidate_compiler, request, vector_client = compiler(tmp_path)
    request = replace(request, stale_revision_ids=(request.sources[0].revision.revision_id,))

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    stale_ids = set(result.excluded_record_ids)
    assert stale_ids
    assert stale_ids.isdisjoint(artifact.record_id for artifact in result.orientation_artifacts)
    assert all(not stale_ids.intersection(call[2]) for call in vector_client.calls if call[0] == "upload")


def test_artifact_scope_is_required_and_cannot_be_an_untracked_string(tmp_path):
    _storage, candidate_compiler, request, _ = compiler(tmp_path)

    with pytest.raises(ValueError, match="artifact scope"):
        candidate_compiler.build(replace(request, artifact_scope="pilot"))
