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
    CandidateSourcePreparer,
    MAX_PREPARED_ANCHOR_CHARS,
    ReconciliationCoverage,
    SynthesisResult,
)
from mentor.compilation import CompilationRun, TokenPricing
from mentor.compiler import SourceExtractor
from mentor.derived_records import (
    Claim,
    CompilerProvenance,
    ConflictUnresolved,
    Evolution,
    RecordDependency,
    Relationship,
)
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
        if callable(output):
            output = output(request)
        return SimpleNamespace(
            output_text=json.dumps(output),
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        )


class FakeVectorClient:
    def __init__(
        self,
        *,
        attachment_status="completed",
        batch_status="completed",
        create_failure_at=None,
        attach_exception=None,
    ):
        self.calls = []
        self.attachment_status = attachment_status
        self.batch_result_status = batch_status
        self.next_store = 0
        self.next_file = 0
        self.create_failure_at = create_failure_at
        self.attach_exception = attach_exception
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
        if self.next_store == self.create_failure_at:
            raise RuntimeError("Synthetic store setup failure.")
        return SimpleNamespace(id=f"vs_synthetic_{self.next_store}", status="completed")

    def upload(self, **kwargs):
        self.next_file += 1
        filename, content, media_type = kwargs["file"]
        self.calls.append(("upload", filename, content.decode(), media_type, kwargs["purpose"]))
        return SimpleNamespace(id=f"file_derived_{self.next_file}")

    def attach(self, store_id, **kwargs):
        self.calls.append(("attach", store_id, kwargs))
        if self.attach_exception is not None and kwargs["file_id"].startswith("file_derived_"):
            raise self.attach_exception
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
        self.provenance = CompilerProvenance(
            "synthetic-synthesizer", "synthesis-prompt-v1", "synthesis-schema-v1"
        )

    def synthesize(self, *, snapshot_id, records, revisions, source_metadata, anchor_spans, hints=()):
        self.calls.append((snapshot_id, tuple(record.record_id for record in records)))
        assert anchor_spans
        provenance = self.provenance
        coverage = ReconciliationCoverage(
            len(records), tuple(sorted(record.record_id for record in records)), 1, 0
        )
        if len(records) < 2:
            return SynthesisResult((), provenance, coverage=coverage)
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
            "compiler_provenance": provenance,
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
            earlier_coverage_id=source_metadata[0].coverage_id,
            later_coverage_id=source_metadata[1].coverage_id,
            earlier_observed_years=(source_metadata[0].year,),
            later_observed_years=(source_metadata[1].year,),
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
        return SynthesisResult((relationship, evolution, conflict), provenance, coverage=coverage)


class IncompleteCoverageSynthesizer(SyntheticSynthesizer):
    def synthesize(self, **kwargs):
        result = super().synthesize(**kwargs)
        return replace(result, coverage=None)


class ClusterSynthesizer:
    provenance = CompilerProvenance(
        "synthetic-cluster", "cluster-prompt-v1", "cluster-schema-v1"
    )

    def __init__(self):
        self.calls = []

    def synthesize(self, *, snapshot_id, records, revisions, source_metadata, **_kwargs):
        self.calls.append(tuple(record.subject for record in records))
        by_revision = {revision.revision_id: revision for revision in revisions}
        groups = {}
        for record in records:
            groups.setdefault(record.subject.split()[0], []).append(record)
        outputs = []
        for members in groups.values():
            if len(members) < 2:
                continue
            left, right = members[:2]
            revision_ids = tuple(dict.fromkeys(
                dependency.identifier
                for record in (left, right)
                for dependency in record.dependencies
                if dependency.kind == "source_revision"
            ))
            assert set(revision_ids) <= set(by_revision)
            outputs.append(Relationship.create(
                snapshot_id=snapshot_id,
                anchors=tuple(dict.fromkeys(left.anchors + right.anchors)),
                dependencies=tuple(
                    [*(RecordDependency("source_revision", revision_id) for revision_id in revision_ids),
                     RecordDependency("derived_record", left.record_id),
                     RecordDependency("derived_record", right.record_id)]
                ),
                validation_state="validated",
                lifecycle_state="active",
                qualification="Synthetic cluster relationship.",
                evidence_state="cross_source_synthesis",
                compiler_provenance=self.provenance,
                left=left.subject,
                relation="supports",
                right=right.subject,
            ))
        coverage = ReconciliationCoverage(
            len(records), tuple(sorted(record.record_id for record in records)), 1, 0
        )
        return SynthesisResult(tuple(outputs), self.provenance, coverage=coverage)


class MissingPricingSynthesizer(SyntheticSynthesizer):
    def preflight_live_pricing(self):
        raise ValueError("GPT-5.6 Sol live synthesis requires complete caller-supplied pricing")


class RawClaimBypassSynthesizer:
    provenance = CompilerProvenance("synthetic", "prompt", "schema")

    def synthesize(self, *, snapshot_id, records, revisions, **_kwargs):
        first = records[0]
        provenance = self.provenance
        return SynthesisResult((Claim.create(
            snapshot_id=snapshot_id,
            anchors=first.anchors,
            dependencies=(RecordDependency("source_revision", revisions[0].revision_id),),
            validation_state="validated",
            lifecycle_state="active",
            qualification="Synthetic bypass attempt.",
            subject="Injected",
            predicate="claims",
            object="raw authority",
            compiler_provenance=provenance,
        ),), provenance)


def synthesis_response(request):
    supplied = json.loads(request["input"])
    records = supplied["records"]
    record_ids = [record["record_id"] for record in records]
    anchor_ids = list(dict.fromkeys(anchor for record in records for anchor in record["anchors"]))
    revision_ids = supplied["revision_ids"]
    common = {
        "qualification": "Synthetic reconciled evidence.",
        "anchors": anchor_ids,
        "input_record_ids": record_ids,
        "source_revision_ids": revision_ids,
    }
    return {"records": [
        common | {
            "family": "relationship",
            "left": "Synthetic 1",
            "relation": "supports",
            "right": "Synthetic 2",
        },
        common | {
            "family": "evolution",
            "subject": "Synthetic framework",
            "previous": "Earlier bounded form",
            "current": "Later qualified form",
            "earlier_source_set": revision_ids[:1],
            "later_source_set": revision_ids[1:],
            "classification": "refined",
            "negative_evidence_state": "positive_teaching",
            "earlier_coverage_id": "coverage_earlier",
            "later_coverage_id": "coverage_later",
            "earlier_observed_years": [2025],
            "later_observed_years": [2026],
        },
        common | {
            "family": "conflict_unresolved",
            "kind": "unresolved",
            "subject": "Synthetic context",
            "alternatives": ["Earlier condition", "Later condition"],
            "competing_record_ids": record_ids,
            "reconciliation_state": "unresolved",
            "relevant_scopes": ["synthetic scope"],
            "conditions": [],
            "unresolved_questions": ["Which synthetic condition applies?"],
        },
    ]}


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


def test_manifest_revision_preparation_verifies_bytes_and_builds_bounded_anchors(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3", runtime_scope="pilot")
    storage.initialize()
    transcript = "".join(
        f"[00:00:{second:02d}] Synthetic timestamped section {second} with bounded content.\n"
        for second in range(1, 9)
    )
    path = tmp_path / "timestamped.txt"
    path.write_text(transcript, encoding="utf-8", newline="")
    collection = Collection("collection_synthetic", "Synthetic", "trading", True, "test")
    source = Source.create(
        collection_id=collection.collection_id,
        identity_key="timestamped",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Timestamped",
        year=2026,
        original_filename=path.name,
        local_provenance=str(path),
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256=sha256(path.read_bytes()).hexdigest(),
        byte_size=path.stat().st_size,
        local_locator=str(path),
        observed_at=1.0,
        lifecycle_state="active",
        remote_file_id="file_timestamped",
    )
    storage.store_collection(collection)
    storage.store_source(source)
    storage.store_source_revision(revision)

    prepared = CandidateSourcePreparer(storage, max_anchor_chars=120).prepare((revision.revision_id,))

    assert len(prepared) == 1
    assert prepared[0].revision == revision
    assert prepared[0].transcript == transcript
    assert len(prepared[0].anchors) > 1
    assert max(anchor.end_offset - anchor.start_offset for anchor in prepared[0].anchors.values()) <= 120
    assert all(anchor.timestamp_start_ms is not None for anchor in prepared[0].anchors.values())
    assert MAX_PREPARED_ANCHOR_CHARS >= 120

    path.write_text(transcript + "tampered", encoding="utf-8", newline="")
    with pytest.raises(ValueError, match="byte size|hash"):
        CandidateSourcePreparer(storage).prepare((revision.revision_id,))


def test_replacement_reuses_unaffected_records_and_promotes_only_after_candidate_publication(tmp_path):
    storage, first_compiler, first_request, _ = compiler(tmp_path)
    first_result = first_compiler.build(first_request)
    assert first_result.ready is True
    storage.transition_snapshot(first_result.snapshot.snapshot_id, "published", transitioned_at=2.0)

    unchanged = first_request.sources[0]
    replaced = first_request.sources[1]
    source = storage.library_source(replaced.revision.source_id)
    replacement_text = "[00:00:01] Synthetic replacement teaching for deterministic tests."
    replacement = SourceRevision.create(
        source=source,
        content_sha256=sha256(replacement_text.encode()).hexdigest(),
        byte_size=len(replacement_text.encode()),
        local_locator="C:/synthetic/replacement.txt",
        observed_at=3.0,
        lifecycle_state="replacement_pending",
    )
    storage.store_source_revision(replacement)
    replacement = storage.mark_source_revision_remote_ready(
        replacement.revision_id, remote_file_id="file_raw_replacement"
    )
    replacement_anchor = SourceAnchor.create(
        revision=replacement, transcript=replacement_text,
        start_offset=0, end_offset=len(replacement_text),
    )
    extraction_responses = QueueResponses([{"candidates": [{
        "family": "claim", "anchors": [replacement_anchor.anchor_id],
        "qualification": "Synthetic replacement claim.", "subject": "Synthetic replacement",
        "predicate": "guides", "object": "bounded context",
    }]}])
    validation_responses = QueueResponses([
        {"outcome": "affirmatively_supported", "audit": "Replacement span supports the claim."}
    ])
    second_compiler = CandidateCompiler(
        storage=storage,
        extractor=SourceExtractor(SimpleNamespace(responses=extraction_responses)),
        validation_client=SimpleNamespace(responses=validation_responses),
        synthesizer=SyntheticSynthesizer(),
        vector_stores=VectorStoreAdapter(FakeVectorClient()),
        orientation_budget=OrientationBudget(max_records=12, max_tokens=10_000),
        readiness_checks=2,
        sleep=lambda _seconds: None,
    )
    second_request = BuildRequest(
        run=CompilationRun("run_replacement", "synthetic", "prompt-v1", "schema-v1", 3.0),
        sources=(unchanged, CandidateSource(replacement, replacement_text, {replacement_anchor.anchor_id: replacement_anchor})),
        artifact_scope=ArtifactScope.PILOT,
    )

    second_result = second_compiler.build(second_request)

    assert second_result.ready is True
    assert len(extraction_responses.calls) == 1
    assert any(
        isinstance(record, Claim) and record.subject == "Synthetic 1"
        for record in second_result.records
    )
    reused = storage.derived_record_reuse(second_result.snapshot.snapshot_id)
    assert reused
    assert all(previous_snapshot_id == first_result.snapshot.snapshot_id for previous_snapshot_id, _ in reused.values())
    assert storage.source_revision(replaced.revision.revision_id).lifecycle_state == "active"
    assert storage.source_revision(replacement.revision_id).lifecycle_state == "replacement_pending"

    storage.transition_snapshot(second_result.snapshot.snapshot_id, "published", transitioned_at=4.0)

    assert storage.source_revision(replaced.revision.revision_id).lifecycle_state == "superseded"
    assert storage.source_revision(replacement.revision_id).lifecycle_state == "active"


def test_selective_rebuild_reconciles_only_affected_cluster_and_keeps_unaffected_synthesis_once(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3", runtime_scope="pilot")
    storage.initialize()
    sources = tuple(
        source_bundle(storage, identity, year)
        for identity, year in (("A one", 2025), ("A two", 2026), ("B one", 2025), ("B two", 2026))
    )
    extraction_outputs = [{"candidates": [{
        "family": "claim",
        "anchors": [next(iter(source.anchors))],
        "qualification": "Synthetic cluster claim.",
        "subject": source.revision.source_id.split("_")[-1].replace("%20", " "),
        "predicate": "guides",
        "object": "bounded context",
    }]} for source in sources]
    # Stable explicit labels keep the test independent of source ID encoding.
    for output, label in zip(extraction_outputs, ("A one", "A two", "B one", "B two")):
        output["candidates"][0]["subject"] = label
    first_synthesizer = ClusterSynthesizer()
    first_compiler = CandidateCompiler(
        storage=storage,
        extractor=SourceExtractor(SimpleNamespace(responses=QueueResponses(extraction_outputs))),
        validation_client=SimpleNamespace(responses=QueueResponses([
            {"outcome": "affirmatively_supported", "audit": "Synthetic cluster support."}
            for _source in sources
        ])),
        synthesizer=first_synthesizer,
        vector_stores=VectorStoreAdapter(FakeVectorClient()),
        orientation_budget=OrientationBudget(max_records=20, max_tokens=10_000),
        readiness_checks=2,
        sleep=lambda _seconds: None,
    )
    first = first_compiler.build(BuildRequest(
        CompilationRun("run_clusters_1", "caller", "caller", "caller", 1.0),
        sources,
        ArtifactScope.PILOT,
    ))
    assert first.ready is True
    storage.transition_snapshot(first.snapshot.snapshot_id, "published", transitioned_at=2.0)

    replaced = sources[0]
    library_source = storage.library_source(replaced.revision.source_id)
    replacement_text = "[00:00:02] Synthetic A replacement teaching."
    replacement = SourceRevision.create(
        source=library_source,
        content_sha256=sha256(replacement_text.encode()).hexdigest(),
        byte_size=len(replacement_text.encode()),
        local_locator="C:/synthetic/A-replacement.txt",
        observed_at=3.0,
        lifecycle_state="replacement_pending",
        remote_file_id="file_raw_A_replacement",
    )
    storage.store_source_revision(replacement)
    replacement_anchor = SourceAnchor.create(
        revision=replacement, transcript=replacement_text,
        start_offset=0, end_offset=len(replacement_text),
    )
    second_synthesizer = ClusterSynthesizer()
    second_compiler = CandidateCompiler(
        storage=storage,
        extractor=SourceExtractor(SimpleNamespace(responses=QueueResponses([{"candidates": [{
            "family": "claim",
            "anchors": [replacement_anchor.anchor_id],
            "qualification": "Synthetic replacement cluster claim.",
            "subject": "A replacement",
            "predicate": "guides",
            "object": "bounded context",
        }]}]))),
        validation_client=SimpleNamespace(responses=QueueResponses([{
            "outcome": "affirmatively_supported", "audit": "Synthetic replacement support."
        }])),
        synthesizer=second_synthesizer,
        vector_stores=VectorStoreAdapter(FakeVectorClient()),
        orientation_budget=OrientationBudget(max_records=20, max_tokens=10_000),
        readiness_checks=2,
        sleep=lambda _seconds: None,
    )
    second_sources = (
        CandidateSource(replacement, replacement_text, {replacement_anchor.anchor_id: replacement_anchor}),
        *sources[1:],
    )

    second = second_compiler.build(BuildRequest(
        CompilationRun("run_clusters_2", "caller", "caller", "caller", 3.0),
        second_sources,
        ArtifactScope.PILOT,
    ))

    assert second.ready is True
    assert set(second_synthesizer.calls[0]) == {"A replacement", "A two"}
    b_relationships = [
        record for record in second.records
        if isinstance(record, Relationship) and record.left.startswith("B ")
    ]
    assert len(b_relationships) == 1
    assert b_relationships[0].record_id in storage.derived_record_reuse(second.snapshot.snapshot_id)


def test_candidate_rejects_two_revisions_of_the_same_logical_source_before_any_stage_call(tmp_path):
    storage, candidate_compiler, request, vector_client = compiler(tmp_path)
    original = request.sources[0]
    source = storage.library_source(original.revision.source_id)
    replacement_text = "[00:00:02] Synthetic duplicate logical source revision."
    replacement = SourceRevision.create(
        source=source,
        content_sha256=sha256(replacement_text.encode()).hexdigest(),
        byte_size=len(replacement_text.encode()),
        local_locator="C:/synthetic/duplicate-revision.txt",
        observed_at=2.0,
        lifecycle_state="replacement_pending",
        remote_file_id="file_raw_duplicate_revision",
    )
    storage.store_source_revision(replacement)
    replacement_anchor = SourceAnchor.create(
        revision=replacement,
        transcript=replacement_text,
        start_offset=0,
        end_offset=len(replacement_text),
    )
    duplicate_request = replace(
        request,
        sources=(
            original,
            CandidateSource(
                replacement,
                replacement_text,
                {replacement_anchor.anchor_id: replacement_anchor},
            ),
        ),
    )

    with pytest.raises(ValueError, match="logical source"):
        candidate_compiler.build(duplicate_request)

    assert candidate_compiler._extractor._client.responses.calls == []
    assert vector_client.calls == []
    assert storage.compilation_run(request.run.run_id) is None


def test_changed_compiler_configuration_forces_reextraction_instead_of_record_reuse(tmp_path):
    storage, first_compiler, first_request, _ = compiler(tmp_path)
    first_result = first_compiler.build(first_request)
    storage.transition_snapshot(first_result.snapshot.snapshot_id, "published", transitioned_at=2.0)
    extraction_responses = QueueResponses([
        {"candidates": [{
            "family": "claim",
            "anchors": [next(iter(source.anchors))],
            "qualification": "Recompiled synthetic claim.",
            "subject": f"Recompiled {index}",
            "predicate": "guides",
            "object": "bounded context",
        }]}
        for index, source in enumerate(first_request.sources, start=1)
    ])
    validation_responses = QueueResponses([
        {"outcome": "affirmatively_supported", "audit": "Synthetic span supports recompilation."},
        {"outcome": "affirmatively_supported", "audit": "Synthetic span supports recompilation."},
    ])
    second_compiler = CandidateCompiler(
        storage=storage,
        extractor=SourceExtractor(SimpleNamespace(responses=extraction_responses)),
        validation_client=SimpleNamespace(responses=validation_responses),
        synthesizer=SyntheticSynthesizer(),
        vector_stores=VectorStoreAdapter(FakeVectorClient()),
        orientation_budget=OrientationBudget(max_records=12, max_tokens=10_000),
        readiness_checks=2,
        sleep=lambda _seconds: None,
        validation_model="synthetic-validator-v2",
    )
    changed_request = BuildRequest(
        run=CompilationRun("run_changed_compiler", "synthetic", "prompt-v1", "schema-v1", 3.0),
        sources=first_request.sources,
        artifact_scope=ArtifactScope.PILOT,
    )

    result = second_compiler.build(changed_request)

    assert result.ready is True
    assert len(extraction_responses.calls) == len(first_request.sources)
    assert storage.derived_record_reuse(result.snapshot.snapshot_id) == {}
    assert result.snapshot.model_version != changed_request.run.model_version


def test_candidate_rejects_missing_reconciliation_coverage_before_remote_setup(tmp_path):
    storage, candidate_compiler, request, vector_client = compiler(
        tmp_path, synthesizer=IncompleteCoverageSynthesizer()
    )

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert any("coverage" in failure for failure in result.failures)
    assert vector_client.calls == []
    assert storage.current_snapshot() is None


@pytest.mark.parametrize(
    "missing_stage",
    ("validation", "reconciliation"),
)
def test_live_pricing_preflight_fails_before_any_extraction_or_candidate_reservation(tmp_path, missing_stage):
    synthesizer = MissingPricingSynthesizer() if missing_stage == "reconciliation" else SyntheticSynthesizer()
    validation_pricing = TokenPricing(2.0, 4.0, 6.0) if missing_stage == "reconciliation" else None
    storage, candidate_compiler, request, vector_client = compiler(
        tmp_path,
        synthesizer=synthesizer,
        validation_model="gpt-5.6-sol",
        live_mode=True,
        validation_pricing=validation_pricing,
    )

    with pytest.raises(ValueError, match="pricing"):
        candidate_compiler.build(request)

    assert candidate_compiler._extractor._client.responses.calls == []
    assert vector_client.calls == []
    assert storage.compilation_run(request.run.run_id) is None


def test_validated_concept_aliases_are_searchable_in_bounded_derived_artifacts(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3", runtime_scope="pilot")
    storage.initialize()
    sources = (
        source_bundle(storage, "alias-earlier", 2025),
        source_bundle(storage, "alias-later", 2026),
    )
    extraction_outputs = [
        {"candidates": [{
            "family": "claim",
            "anchors": [next(iter(source.anchors))],
            "qualification": "Synthetic alias claim.",
            "subject": f"Canonical topic {index}",
            "predicate": "guides",
            "object": "bounded context",
            "concept_hints": [{
                "label": f"Canonical topic {index}",
                "aliases": [f"Search alias {index}"],
                "scope": "synthetic scope",
                "role": "subject",
                "position": None,
            }],
        }]}
        for index, source in enumerate(sources, start=1)
    ]
    extraction_responses = QueueResponses(extraction_outputs)
    validation_responses = QueueResponses([
        {"outcome": "affirmatively_supported", "audit": "Synthetic alias is supported."},
        {"outcome": "affirmatively_supported", "audit": "Synthetic alias is supported."},
    ])
    candidate_compiler = CandidateCompiler(
        storage=storage,
        extractor=SourceExtractor(SimpleNamespace(responses=extraction_responses)),
        validation_client=SimpleNamespace(responses=validation_responses),
        synthesizer=SyntheticSynthesizer(),
        vector_stores=VectorStoreAdapter(FakeVectorClient()),
        orientation_budget=OrientationBudget(max_records=12, max_tokens=10_000),
        readiness_checks=2,
        sleep=lambda _seconds: None,
    )
    request = BuildRequest(
        run=CompilationRun("run_alias_artifacts", "synthetic", "prompt-v1", "schema-v1", 1.0),
        sources=sources,
        artifact_scope=ArtifactScope.PILOT,
    )

    result = candidate_compiler.build(request)

    assert result.ready is True
    artifact_payloads = [json.loads(artifact.content) for artifact in result.orientation_artifacts]
    assert any(
        "Search alias 1" in concept["aliases"]
        for payload in artifact_payloads
        for concept in payload["concepts"]
    )
    assert all("record_id" not in payload and "anchor_ids" not in payload for payload in artifact_payloads)


def compiler(
    tmp_path,
    *,
    extraction_outputs=None,
    validation_outputs=None,
    synthesis_outputs=None,
    vector_client=None,
    synthesizer=None,
    runtime_scope="pilot",
    artifact_scope=ArtifactScope.PILOT,
    validation_model="synthetic-validator",
    live_mode=False,
    validation_pricing=None,
):
    storage = Storage(tmp_path / "mentor.sqlite3", runtime_scope=runtime_scope)
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
    if synthesis_outputs is not None:
        from mentor.synthesis import SynthesisReconciler

        synthesizer = SynthesisReconciler(
            SimpleNamespace(responses=QueueResponses(synthesis_outputs))
        )
    candidate_compiler = CandidateCompiler(
        storage=storage,
        extractor=SourceExtractor(SimpleNamespace(responses=extraction_responses)),
        validation_client=SimpleNamespace(responses=validation_responses),
        synthesizer=synthesizer or SyntheticSynthesizer(),
        vector_stores=VectorStoreAdapter(vector_client),
        orientation_budget=OrientationBudget(max_records=12, max_tokens=10_000),
        validation_model=validation_model,
        live_mode=live_mode,
        validation_pricing=validation_pricing,
        readiness_checks=2,
        sleep=lambda _seconds: None,
    )
    request = BuildRequest(
        run=CompilationRun("run_candidate", "synthetic", "prompt-v1", "schema-v1", 1.0),
        sources=sources,
        artifact_scope=artifact_scope,
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
    assert all("record_id" not in json.loads(artifact.content) for artifact in result.orientation_artifacts)
    assert all("concepts" in json.loads(artifact.content) for artifact in result.orientation_artifacts)
    assert all(artifact.attributes["artifact_scope"] == "pilot" for artifact in result.orientation_artifacts)
    assert result.total_metric.call_count == sum(metric.call_count for metric in result.stage_metrics)
    assert result.total_metric.input_tokens == 44
    assert result.total_metric.output_tokens == 28
    assert result.total_metric.remote_calls > 0
    assert next(iter(request.sources[0].anchors)) in candidate_compiler._extractor._client.responses.calls[0]["input"]
    stored_anchors = storage.source_anchor_metadata(
        tuple(anchor_id for source in request.sources for anchor_id in source.anchors)
    )
    assert {anchor["anchor_id"] for anchor in stored_anchors} == {
        anchor_id for source in request.sources for anchor_id in source.anchors
    }
    assert all("Synthetic earlier teaching" not in str(anchor) for anchor in stored_anchors)
    raw_attachments = [
        call for call in vector_client.calls
        if call[0] == "attach" and call[1] == result.raw_artifact.store_id
    ]
    assert [call[2]["file_id"] for call in raw_attachments] == [
        source.revision.remote_file_id for source in request.sources
    ]
    assert {call[2]["attributes"]["year"] for call in raw_attachments} == {2025, 2026}
    assert all(
        {
            "snapshot_id", "artifact_scope", "status", "collection_id",
            "source_id", "source", "year", "course", "lesson", "relative_path",
        } <= set(call[2]["attributes"])
        for call in raw_attachments
    )
    assert all("C:/synthetic" not in str(call[2]["attributes"]) for call in raw_attachments)
    assert len([call for call in vector_client.calls if call[0] == "upload"]) == len(result.orientation_artifacts)


def test_extraction_failure_is_visible_and_leaves_candidate_failed_and_unpublished(tmp_path):
    storage, candidate_compiler, request, vector_client = compiler(
        tmp_path,
        extraction_outputs=[RuntimeError("synthetic extraction failed"), {"candidates": []}],
    )

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    assert "extraction" in result.failures[0]
    assert storage.current_snapshot() is None
    assert result.total_metric.failure_count >= 1
    assert result.snapshot.raw_store_id is None
    assert result.snapshot.derived_store_id is None
    assert vector_client.calls == []


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
    metrics = {metric.stage: metric for metric in result.stage_metrics}
    assert metrics["extraction"].record_count == 2
    assert metrics["validation"].record_count == 1


def test_remote_readiness_failure_marks_candidate_failed_without_pointer_mutation(tmp_path):
    vector_client = FakeVectorClient(attachment_status="failed")
    storage, candidate_compiler, request, _ = compiler(tmp_path, vector_client=vector_client)

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    assert any("raw store" in failure for failure in result.failures)
    assert storage.current_snapshot() is None


def test_nonactive_synthesis_record_never_enters_an_orientation_artifact(tmp_path):
    storage, candidate_compiler, request, vector_client = compiler(
        tmp_path,
        synthesizer=SyntheticSynthesizer(stale=True),
    )

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    assert all(
        record.lifecycle_state == "active"
        for record in storage.derived_records(result.snapshot.snapshot_id, include_stale=True)
    )
    assert result.orientation_artifacts == ()
    assert not any(call[0] == "upload" for call in vector_client.calls)


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


@pytest.mark.parametrize(
    ("runtime_scope", "artifact_scope"),
    (
        ("production", ArtifactScope.PILOT),
        ("pilot", ArtifactScope.PRODUCTION),
    ),
)
def test_candidate_scope_must_match_runtime_before_reservation_or_remote_calls(
    tmp_path, runtime_scope, artifact_scope
):
    storage, candidate_compiler, request, vector_client = compiler(
        tmp_path,
        runtime_scope=runtime_scope,
        artifact_scope=artifact_scope,
    )

    with pytest.raises(ValueError, match="runtime scope"):
        candidate_compiler.build(request)

    assert storage.compilation_run(request.run.run_id) is None
    assert storage.snapshots() == []
    assert vector_client.calls == []
    assert candidate_compiler._extractor._client.responses.calls == []


@pytest.mark.parametrize("change", [
    {"remote_file_id": "file_substituted"},
    {"revision_id": "rev_unknown"},
])
def test_raw_authority_rejects_noncanonical_request_revisions_before_remote_or_model_calls(tmp_path, change):
    _storage, candidate_compiler, request, vector_client = compiler(tmp_path)
    source = request.sources[0]
    request = replace(
        request,
        sources=(replace(source, revision=replace(source.revision, **change)), request.sources[1]),
    )

    with pytest.raises(ValueError, match="canonical|unknown"):
        candidate_compiler.build(request)

    assert vector_client.calls == []
    assert candidate_compiler._extractor._client.responses.calls == []


def test_synthesis_cannot_bypass_storage_owned_source_claim_validation(tmp_path):
    storage, candidate_compiler, request, _ = compiler(
        tmp_path,
        synthesizer=RawClaimBypassSynthesizer(),
    )

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    assert any("source synthesis" in failure for failure in result.failures)
    assert all(record.subject != "Injected" for record in storage.derived_records(result.snapshot.snapshot_id))


def test_concrete_reconciliation_stage_builds_typed_provenanced_records_without_network(tmp_path):
    storage, candidate_compiler, request, _ = compiler(
        tmp_path,
        synthesis_outputs=[synthesis_response],
    )

    result = candidate_compiler.build(request)

    assert result.ready is True
    synthesized = [record for record in storage.derived_records(result.snapshot.snapshot_id) if record.evidence_state == "cross_source_synthesis"]
    assert {record.family for record in synthesized} == {"relationship", "evolution", "conflict_unresolved"}
    assert all(record.compiler_provenance is not None for record in synthesized)
    assert next(metric for metric in result.stage_metrics if metric.stage == "synthesis").input_tokens == 11
    assert result.reconciliation_coverage is not None
    assert result.reconciliation_coverage.complete is True
    assert result.reconciliation_coverage.input_record_count == 2


def test_concrete_reconciler_rejects_relationship_without_explicit_evidence_references(tmp_path):
    def unsupported_relationship(request):
        payload = synthesis_response(request)
        relationship = payload["records"][0]
        relationship.pop("anchors")
        return {"records": [relationship]}

    storage, candidate_compiler, request, vector_client = compiler(
        tmp_path,
        synthesis_outputs=[unsupported_relationship],
    )

    result = candidate_compiler.build(request)

    assert result.ready is False
    assert result.snapshot.status == "failed"
    assert any("synthesis" in failure and "anchors" in failure for failure in result.failures)
    assert vector_client.calls == []
    assert storage.current_snapshot() is None


def test_partial_remote_setup_is_auditable_and_retry_returns_the_same_failed_run(tmp_path):
    vector_client = FakeVectorClient(create_failure_at=2)
    storage, candidate_compiler, request, _ = compiler(tmp_path, vector_client=vector_client)

    first = candidate_compiler.build(request)
    call_count = len(vector_client.calls)
    second = candidate_compiler.build(request)

    assert first.ready is second.ready is False
    assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
    assert first.snapshot.status == second.snapshot.status == "failed"
    assert first.snapshot.raw_store_id == "vs_synthetic_1"
    assert first.snapshot.derived_store_id is None
    assert storage.compilation_run(request.run.run_id).status == "failed"
    assert storage.candidate_artifact_scope(first.snapshot.snapshot_id) == "pilot"
    assert storage.current_snapshot() is None
    assert len(vector_client.calls) == call_count == 2
    setup = next(metric for metric in first.stage_metrics if metric.stage == "remote_setup")
    assert (setup.call_count, setup.remote_calls, setup.failure_count) == (2, 2, 1)
    assert len(candidate_compiler._extractor._client.responses.calls) == 2
    with pytest.raises(ValueError, match="runtime scope"):
        candidate_compiler.build(replace(request, artifact_scope=ArtifactScope.PRODUCTION))
    assert len(vector_client.calls) == call_count


def test_upload_success_and_attach_failure_retain_partial_remote_audit_and_actual_metrics(tmp_path):
    vector_client = FakeVectorClient(attach_exception=RuntimeError("Synthetic attach call failure."))
    storage, candidate_compiler, request, _ = compiler(tmp_path, vector_client=vector_client)

    first = candidate_compiler.build(request)
    calls_after_failure = len(vector_client.calls)
    audit_after_failure = storage.candidate_remote_operations(first.snapshot.snapshot_id)
    second = candidate_compiler.build(request)

    assert first.ready is second.ready is False
    assert first.snapshot.status == second.snapshot.status == "failed"
    derived = next(metric for metric in first.stage_metrics if metric.stage == "derived_store")
    assert (derived.call_count, derived.remote_calls, derived.failure_count) == (2, 2, 1)
    upload = next(operation for operation in audit_after_failure if operation.operation == "upload_file")
    attach = next(
        operation for operation in audit_after_failure
        if operation.operation == "attach_file" and operation.file_id == upload.file_id
    )
    assert upload.status == "succeeded"
    assert upload.file_id == "file_derived_1"
    assert attach.status == "failed"
    assert attach.file_id == upload.file_id
    assert attach.store_id == first.snapshot.derived_store_id
    assert storage.candidate_remote_operations(first.snapshot.snapshot_id) == audit_after_failure
    assert len(vector_client.calls) == calls_after_failure
    assert storage.current_snapshot() is None
