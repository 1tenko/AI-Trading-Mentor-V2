"""End-to-end construction of validated, unpublished Phase 3 candidates."""

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Callable, Mapping

from mentor.anchors import SourceAnchor, validate_anchor
from mentor.compilation import (
    CallUsage,
    CandidateGateResult,
    CompilationMetric,
    CompilationRun,
    CorpusSnapshot,
    SourceProcessingResult,
)
from mentor.compiler import ExtractionResult, SourceExtractor
from mentor.dependencies import DependencyGraph
from mentor.derived_records import (
    DerivedRecord,
    ProcedureSequenceHierarchy,
    Relationship,
    validate_record,
)
from mentor.knowledge import SourceRevision
from mentor.orientation import OrientationBudget, render_orientation_artifact
from mentor.synthesis import ConceptHint, SynthesisCandidate


_TERMINAL_REMOTE_FAILURES = frozenset({"cancelled", "expired", "failed"})


class ArtifactScope(Enum):
    PILOT = "pilot"
    PRODUCTION = "production"


@dataclass(frozen=True)
class CandidateSource:
    revision: SourceRevision
    transcript: str
    anchors: Mapping[str, SourceAnchor]


@dataclass(frozen=True)
class BuildRequest:
    run: CompilationRun
    sources: tuple[CandidateSource, ...]
    artifact_scope: ArtifactScope
    stale_revision_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SynthesisResult:
    records: tuple[DerivedRecord, ...]
    hints: tuple[ConceptHint, ...] = ()
    usage: CallUsage = CallUsage()


@dataclass(frozen=True)
class OrientationArtifact:
    record_id: str
    concept_id: str
    content: str
    attributes: dict[str, str | int]


@dataclass(frozen=True)
class RemoteArtifact:
    kind: str
    scope: ArtifactScope
    store_id: str
    file_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateBuildResult:
    snapshot: CorpusSnapshot
    ready: bool
    gate: CandidateGateResult
    records: tuple[DerivedRecord, ...]
    dependency_graph: DependencyGraph
    orientation_artifacts: tuple[OrientationArtifact, ...]
    raw_artifact: RemoteArtifact
    derived_artifact: RemoteArtifact
    stage_metrics: tuple[CompilationMetric, ...]
    total_metric: CompilationMetric
    failures: tuple[str, ...]
    excluded_record_ids: tuple[str, ...] = ()


class CandidateCompiler:
    """Compose approved compiler stages without publishing the candidate."""

    def __init__(
        self,
        *,
        storage: Any,
        extractor: SourceExtractor,
        validation_client: Any,
        synthesizer: Any,
        vector_stores: Any,
        orientation_budget: OrientationBudget,
        validation_model: str = "synthetic-validator",
        live_mode: bool = False,
        readiness_checks: int = 60,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
        now: Callable[[], float] = time.time,
    ):
        if not isinstance(readiness_checks, int) or isinstance(readiness_checks, bool) or readiness_checks < 1:
            raise ValueError("readiness checks must be positive")
        self._storage = storage
        self._extractor = extractor
        self._validation_client = validation_client
        self._synthesizer = synthesizer
        self._vector_stores = vector_stores
        self._orientation_budget = orientation_budget
        self._validation_model = validation_model
        self._live_mode = live_mode
        self._readiness_checks = readiness_checks
        self._sleep = sleep
        self._clock = clock
        self._now = now

    def build(self, request: BuildRequest) -> CandidateBuildResult:
        sources = _validate_request(request)
        revision_ids, _fingerprint, snapshot_id = CorpusSnapshot.identity_for(
            request.run.run_id, [source.revision.revision_id for source in sources]
        )
        scope = request.artifact_scope.value
        raw_store = self._vector_stores.create_store(
            f"Phase 3 {scope} raw {request.run.run_id}",
            {"snapshot_id": snapshot_id, "artifact_scope": scope, "artifact_kind": "raw"},
        )
        derived_store = self._vector_stores.create_store(
            f"Phase 3 {scope} derived {request.run.run_id}",
            {"snapshot_id": snapshot_id, "artifact_scope": scope, "artifact_kind": "derived"},
        )
        snapshot = CorpusSnapshot.create(
            run=request.run,
            selected_revisions=[source.revision for source in sources],
            raw_store_id=raw_store.store_id,
            derived_store_id=derived_store.store_id,
            created_at=self._now(),
        )
        if snapshot.selected_revision_ids != revision_ids:
            raise ValueError("candidate revision selection is not canonical")
        self._storage.create_compilation_candidate(request.run, snapshot)

        stage_metrics: list[CompilationMetric] = []
        failures: list[str] = []
        source_results: list[SourceProcessingResult] = []
        extraction_usage = CallUsage()
        validation_usage = CallUsage()
        extraction_calls = validation_calls = 0
        extraction_failures = validation_failures = 0
        extraction_started = self._clock()
        validation_latency_ms = 0

        for source in sources:
            accepted = 0
            source_failed = False
            try:
                _validate_candidate_source(source)
                extraction_calls += 1
                extracted = self._extractor.extract(
                    revision=source.revision,
                    snapshot_id=snapshot.snapshot_id,
                    transcript=source.transcript,
                )
                _validate_extraction_result(extracted, source)
                extraction_usage = _sum_usage(extraction_usage, extracted.usage)
            except Exception as error:
                extraction_failures += 1
                source_failed = True
                failures.append(f"extraction failed for {source.revision.revision_id}: {error}")
                extracted = None
            if extracted is not None:
                for candidate in extracted.candidates:
                    validation_started = self._clock()
                    try:
                        validation_calls += 1
                        outcome = self._storage.validate_and_store_source_extracted(
                            client=self._validation_client,
                            candidate=candidate,
                            revision=source.revision,
                            transcript=source.transcript,
                            anchors=source.anchors,
                            model=self._validation_model,
                            live_mode=self._live_mode,
                        )
                        validation_usage = _sum_usage(validation_usage, outcome.usage)
                        if outcome.source_extracted is None:
                            validation_failures += 1
                            source_failed = True
                            failures.append(
                                f"validation {outcome.outcome} for {outcome.candidate_record_id}"
                            )
                        else:
                            accepted += 1
                    except Exception as error:
                        validation_failures += 1
                        source_failed = True
                        failures.append(f"validation failed for {candidate.record_id}: {error}")
                    validation_latency_ms += _elapsed_ms(validation_started, self._clock())
            source_results.append(
                SourceProcessingResult(
                    source.revision.revision_id,
                    "failed" if source_failed else "processed",
                    accepted,
                )
            )

        stage_metrics.append(
            self._record_metric(
                request.run,
                "extraction",
                len(sources),
                sum(result.record_count for result in source_results),
                extraction_calls,
                extraction_usage,
                _elapsed_ms(extraction_started, self._clock()),
                extraction_calls,
                extraction_failures,
            )
        )
        stage_metrics.append(
            self._record_metric(
                request.run,
                "validation",
                len(sources),
                sum(result.record_count for result in source_results),
                validation_calls,
                validation_usage,
                validation_latency_ms,
                validation_calls,
                validation_failures,
            )
        )

        empty_graph = DependencyGraph(())
        empty_raw = RemoteArtifact("raw", request.artifact_scope, raw_store.store_id, ())
        empty_derived = RemoteArtifact("derived", request.artifact_scope, derived_store.store_id, ())
        if failures:
            gate = self._storage.record_candidate_gate(snapshot.snapshot_id, tuple(source_results), checked_at=self._now())
            return self._failed_result(
                request,
                gate,
                failures,
                stage_metrics,
                empty_graph,
                (),
                empty_raw,
                empty_derived,
            )

        synthesis_started = self._clock()
        synthesis_usage = CallUsage()
        hints: tuple[ConceptHint, ...] = ()
        try:
            extracted_records = tuple(self._storage.derived_records(snapshot.snapshot_id, include_stale=True))
            synthesized = self._synthesizer.synthesize(
                snapshot_id=snapshot.snapshot_id,
                records=extracted_records,
                revisions=tuple(source.revision for source in sources),
            )
            if not isinstance(synthesized, SynthesisResult):
                raise ValueError("synthesis stage must return SynthesisResult")
            synthesis_usage = synthesized.usage
            hints = synthesized.hints
            _validate_candidate_dependencies(
                extracted_records + synthesized.records,
                set(snapshot.selected_revision_ids),
            )
            for record in synthesized.records:
                validate_record(record)
                if record.snapshot_id != snapshot.snapshot_id or record.validation_state != "validated":
                    raise ValueError("synthesis records must be validated and candidate-owned")
                self._storage.store_derived_record(record)
        except Exception as error:
            failures.append(f"synthesis failed: {error}")
        records = tuple(self._storage.derived_records(snapshot.snapshot_id, include_stale=True))
        stage_metrics.append(
            self._record_metric(
                request.run,
                "synthesis",
                len(sources),
                len(records),
                1,
                synthesis_usage,
                _elapsed_ms(synthesis_started, self._clock()),
                1 if self._live_mode else 0,
                1 if failures else 0,
            )
        )

        graph = empty_graph
        excluded_record_ids: tuple[str, ...] = ()
        try:
            graph = self._storage.dependency_graph(snapshot.snapshot_id)
            excluded = set(graph.stale_record_ids(request.stale_revision_ids))
            excluded.update(record.record_id for record in records if record.lifecycle_state != "active")
            excluded_record_ids = tuple(sorted(excluded))
            if excluded_record_ids:
                raise ValueError("stale or nonactive records cannot enter orientation artifacts")
        except Exception as error:
            failures.append(f"dependency readiness failed: {error}")

        gate = self._storage.record_candidate_gate(snapshot.snapshot_id, tuple(source_results), checked_at=self._now())
        if gate.status != "passed":
            failures.append(f"candidate gate failed: {gate.failure_reason}")
        if failures:
            return self._failed_result(
                request,
                gate,
                failures,
                stage_metrics,
                graph,
                (),
                empty_raw,
                empty_derived,
                excluded_record_ids,
            )

        try:
            orientation_artifacts = self._build_orientation_artifacts(
                snapshot, records, hints, request.artifact_scope
            )
        except Exception as error:
            failures.append(f"orientation rendering failed: {error}")
            return self._failed_result(
                request,
                gate,
                failures,
                stage_metrics,
                graph,
                (),
                empty_raw,
                empty_derived,
            )

        raw_started = self._clock()
        raw_file_ids = tuple(source.revision.remote_file_id for source in sources)
        raw_calls = 1
        try:
            batch = self._vector_stores.create_batch(
                raw_store.store_id,
                list(raw_file_ids),
                {"snapshot_id": snapshot.snapshot_id, "artifact_scope": scope, "status": "published"},
            )
            raw_calls += self._await_batch(raw_store.store_id, batch)
        except Exception as error:
            failures.append(f"raw store readiness failed: {error}")
        raw_artifact = RemoteArtifact("raw", request.artifact_scope, raw_store.store_id, raw_file_ids)
        stage_metrics.append(
            self._record_metric(
                request.run,
                "raw_store",
                len(sources),
                0,
                raw_calls + 1,
                CallUsage(),
                _elapsed_ms(raw_started, self._clock()),
                raw_calls + 1,
                int(bool(failures)),
            )
        )
        if failures:
            return self._failed_result(
                request,
                gate,
                failures,
                stage_metrics,
                graph,
                orientation_artifacts,
                raw_artifact,
                empty_derived,
            )

        derived_started = self._clock()
        derived_file_ids: list[str] = []
        derived_calls = 1
        try:
            for artifact in orientation_artifacts:
                file_id = self._vector_stores.upload_text(
                    f"{artifact.record_id}.json",
                    artifact.content,
                )
                derived_file_ids.append(file_id)
                attachment = self._vector_stores.attach_file(
                    derived_store.store_id,
                    file_id,
                    artifact.attributes,
                )
                derived_calls += 2 + self._await_attachment(derived_store.store_id, file_id, attachment)
        except Exception as error:
            failures.append(f"derived store readiness failed: {error}")
        derived_artifact = RemoteArtifact(
            "derived", request.artifact_scope, derived_store.store_id, tuple(derived_file_ids)
        )
        stage_metrics.append(
            self._record_metric(
                request.run,
                "derived_store",
                len(sources),
                len(orientation_artifacts),
                derived_calls,
                CallUsage(),
                _elapsed_ms(derived_started, self._clock()),
                derived_calls,
                int(bool(failures)),
            )
        )
        if failures:
            return self._failed_result(
                request,
                gate,
                failures,
                stage_metrics,
                graph,
                orientation_artifacts,
                raw_artifact,
                derived_artifact,
            )

        snapshot = self._storage.transition_snapshot(snapshot.snapshot_id, "validating", transitioned_at=self._now())
        total = self._total_metric(request.run, stage_metrics)
        return CandidateBuildResult(
            snapshot,
            True,
            gate,
            records,
            graph,
            orientation_artifacts,
            raw_artifact,
            derived_artifact,
            tuple(stage_metrics),
            total,
            (),
        )

    def _build_orientation_artifacts(
        self,
        snapshot: CorpusSnapshot,
        records: tuple[DerivedRecord, ...],
        hints: tuple[ConceptHint, ...],
        artifact_scope: ArtifactScope,
    ) -> tuple[OrientationArtifact, ...]:
        candidate = SynthesisCandidate.from_records(
            snapshot_id=snapshot.snapshot_id,
            records=records,
            hints=hints,
        )
        relationships = tuple(
            candidate.synthesize_relationship(record.record_id)
            for record in records
            if isinstance(record, Relationship)
        )
        procedures = tuple(
            candidate.synthesize_procedure(record.record_id)
            for record in records
            if isinstance(record, ProcedureSequenceHierarchy) and record.kind == "procedure"
        )
        candidate.publish(relationships=relationships, procedures=procedures)
        record_concepts = {
            record.record_id: min(
                concept.concept_id
                for concept in candidate.concepts
                if record.record_id in concept.supporting_record_ids
            )
            for record in records
        }
        self._storage.store_orientation_concept_ids(
            snapshot.snapshot_id,
            record_concepts,
            concepts=candidate.concepts,
        )
        artifacts = []
        for record in records:
            concept_id = record_concepts[record.record_id]
            collection_id, year, record_scope = self._storage.orientation_source_area(
                snapshot.snapshot_id, record
            )
            content = render_orientation_artifact(
                record,
                concept_id,
                (collection_id, year, record_scope),
                max_bytes=self._orientation_budget.max_tokens,
            )
            attributes: dict[str, str | int] = {
                "snapshot_id": snapshot.snapshot_id,
                "status": "published",
                "artifact_scope": artifact_scope.value,
                "record_id": record.record_id,
                "concept_id": concept_id,
                "family": record.family,
                "derived_kind": record.derived_kind,
                "schema_version": snapshot.schema_version,
            }
            if collection_id is not None:
                attributes["collection_id"] = collection_id
            if year is not None:
                attributes["year"] = year
            if record_scope is not None:
                attributes["scope"] = record_scope
            artifacts.append(OrientationArtifact(record.record_id, concept_id, content, attributes))
        return tuple(artifacts)

    def _await_batch(self, store_id: str, batch: Any) -> int:
        calls = 0
        current = batch
        for attempt in range(self._readiness_checks):
            status = getattr(current, "status", None)
            if status == "completed":
                return calls
            if status in _TERMINAL_REMOTE_FAILURES:
                raise ValueError(_remote_failure("batch", current))
            if attempt + 1 < self._readiness_checks:
                self._sleep(1.0)
                current = self._vector_stores.batch_status(store_id, batch.batch_id)
                calls += 1
        raise ValueError("batch did not become ready within the configured checks")

    def _await_attachment(self, store_id: str, file_id: str, attachment: Any) -> int:
        calls = 0
        current = attachment
        for attempt in range(self._readiness_checks):
            status = getattr(current, "status", None)
            if status == "completed":
                return calls
            if status in _TERMINAL_REMOTE_FAILURES:
                raise ValueError(_remote_failure("attachment", current))
            if attempt + 1 < self._readiness_checks:
                self._sleep(1.0)
                current = self._vector_stores.attachment_status(store_id, file_id)
                calls += 1
        raise ValueError("attachment did not become ready within the configured checks")

    def _record_metric(
        self,
        run: CompilationRun,
        stage: str,
        source_count: int,
        record_count: int,
        call_count: int,
        usage: CallUsage,
        latency_ms: int,
        remote_calls: int,
        failure_count: int,
    ) -> CompilationMetric:
        return self._storage.record_compilation_metric(
            run.run_id,
            CompilationMetric(
                stage,
                source_count,
                record_count,
                call_count,
                usage.input_tokens,
                usage.output_tokens,
                latency_ms,
                usage.cost_usd,
                remote_calls,
                failure_count,
            ),
        )

    def _total_metric(
        self, run: CompilationRun, metrics: list[CompilationMetric]
    ) -> CompilationMetric:
        total = CompilationMetric(
            "total",
            max((metric.source_count for metric in metrics), default=0),
            max((metric.record_count for metric in metrics), default=0),
            sum(metric.call_count for metric in metrics),
            sum(metric.input_tokens for metric in metrics),
            sum(metric.output_tokens for metric in metrics),
            sum(metric.latency_ms for metric in metrics),
            sum(metric.cost_usd for metric in metrics),
            sum(metric.remote_calls for metric in metrics),
            sum(metric.failure_count for metric in metrics),
        )
        return self._storage.record_compilation_metric(run.run_id, total)

    def _failed_result(
        self,
        request: BuildRequest,
        gate: CandidateGateResult,
        failures: list[str],
        stage_metrics: list[CompilationMetric],
        graph: DependencyGraph,
        orientation_artifacts: tuple[OrientationArtifact, ...],
        raw_artifact: RemoteArtifact,
        derived_artifact: RemoteArtifact,
        excluded_record_ids: tuple[str, ...] = (),
    ) -> CandidateBuildResult:
        snapshot = self._storage.snapshot(gate.snapshot_id)
        if snapshot.status == "building":
            snapshot = self._storage.transition_snapshot(
                snapshot.snapshot_id, "validating", transitioned_at=self._now()
            )
        if snapshot.status == "validating":
            snapshot = self._storage.transition_snapshot(
                snapshot.snapshot_id,
                "failed",
                failure_reason="; ".join(failures)[:1000],
                transitioned_at=self._now(),
            )
        total = self._total_metric(request.run, stage_metrics)
        records = tuple(self._storage.derived_records(snapshot.snapshot_id, include_stale=True))
        return CandidateBuildResult(
            snapshot,
            False,
            gate,
            records,
            graph,
            orientation_artifacts,
            raw_artifact,
            derived_artifact,
            tuple(stage_metrics),
            total,
            tuple(failures),
            excluded_record_ids,
        )


def _validate_request(request: BuildRequest) -> tuple[CandidateSource, ...]:
    if not isinstance(request, BuildRequest):
        raise ValueError("candidate build requires BuildRequest")
    if not isinstance(request.run, CompilationRun) or request.run.status != "building":
        raise ValueError("candidate build requires a building compilation run")
    if not isinstance(request.artifact_scope, ArtifactScope):
        raise ValueError("candidate build requires an explicit artifact scope")
    if not isinstance(request.sources, tuple) or not request.sources:
        raise ValueError("candidate build requires selected sources")
    if any(not isinstance(source, CandidateSource) for source in request.sources):
        raise ValueError("candidate build sources must be typed")
    revision_ids = tuple(source.revision.revision_id for source in request.sources)
    if len(set(revision_ids)) != len(revision_ids):
        raise ValueError("candidate build revisions must be unique")
    if (
        not isinstance(request.stale_revision_ids, tuple)
        or len(set(request.stale_revision_ids)) != len(request.stale_revision_ids)
        or not set(request.stale_revision_ids) <= set(revision_ids)
    ):
        raise ValueError("stale revision IDs must be selected by the candidate")
    return request.sources


def _validate_candidate_source(source: CandidateSource) -> None:
    if source.revision.lifecycle_state != "active":
        raise ValueError("candidate sources must use active revisions")
    if not isinstance(source.revision.remote_file_id, str) or not source.revision.remote_file_id:
        raise ValueError("candidate source requires an uploaded raw File")
    if not isinstance(source.transcript, str) or not source.transcript:
        raise ValueError("candidate source transcript is required")
    if not isinstance(source.anchors, Mapping) or not source.anchors:
        raise ValueError("candidate source requires deterministic anchors")
    for anchor_id, anchor in source.anchors.items():
        if anchor_id != getattr(anchor, "anchor_id", None):
            raise ValueError("candidate source anchor key does not match anchor identity")
        validate_anchor(anchor, source.revision, source.transcript)


def _validate_extraction_result(result: ExtractionResult, source: CandidateSource) -> None:
    if not isinstance(result, ExtractionResult) or result.revision_id != source.revision.revision_id:
        raise ValueError("extraction result does not match its source revision")
    known_anchor_ids = set(source.anchors)
    for candidate in result.candidates:
        if not set(candidate.anchors) <= known_anchor_ids:
            raise ValueError("extraction returned an unknown deterministic anchor")


def _validate_candidate_dependencies(
    records: tuple[DerivedRecord, ...], selected_revision_ids: set[str]
) -> None:
    graph = DependencyGraph.from_records(records)
    record_ids = {record.record_id for record in records}
    for edge in graph.edges:
        if edge.dependency.kind == "source_revision" and edge.dependency.identifier not in selected_revision_ids:
            raise ValueError("derived record dependency is outside the candidate raw snapshot")
        if edge.dependency.kind == "derived_record" and edge.dependency.identifier not in record_ids:
            raise ValueError("derived record dependency is outside the candidate snapshot")


def _sum_usage(first: CallUsage, second: CallUsage) -> CallUsage:
    if not isinstance(second, CallUsage):
        raise ValueError("compiler stages require typed call usage")
    return CallUsage(
        first.input_tokens + second.input_tokens,
        first.output_tokens + second.output_tokens,
        first.cost_usd + second.cost_usd,
    )


def _elapsed_ms(started: float, ended: float) -> int:
    return max(0, int((ended - started) * 1000))


def _remote_failure(kind: str, result: Any) -> str:
    last_error = getattr(result, "last_error", None)
    message = getattr(last_error, "message", None)
    return f"{kind} failed: {message or getattr(result, 'status', 'unknown')}"
