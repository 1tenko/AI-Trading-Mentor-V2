"""End-to-end construction of validated, unpublished Phase 3 candidates."""

from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from mentor.anchors import (
    SourceAnchor,
    bounded_transcript_anchors,
    resolve_anchor_span,
    validate_anchor,
)
from mentor.compilation import (
    CallUsage,
    CandidateGateResult,
    CompilationMetric,
    CompilationRun,
    CorpusSnapshot,
    SourceProcessingResult,
    TokenPricing,
)
from mentor.compiler import ExtractionResult, SourceExtractor
from mentor.compiler_prompts import (
    SEMANTIC_VALIDATION_PROMPT_VERSION,
    SEMANTIC_VALIDATION_SCHEMA_VERSION,
)
from mentor.dependencies import DependencyGraph
from mentor.derived_records import (
    Claim,
    CompilerProvenance,
    ConflictUnresolved,
    DerivedRecord,
    Evolution,
    ProcedureSequenceHierarchy,
    Relationship,
    validate_record,
)
from mentor.knowledge import SourceRevision
from mentor.orientation import OrientationBudget, concept_summaries, render_orientation_artifact
from mentor.synthesis import (
    ConceptHint,
    ReconciliationSource,
    ReconciliationCoverage,
    SynthesisCandidate,
    SynthesisResult,
    source_coverage,
)


_TERMINAL_REMOTE_FAILURES = frozenset({"cancelled", "expired", "failed"})
MAX_PREPARED_ANCHOR_CHARS = 4_000


class ArtifactScope(Enum):
    PILOT = "pilot"
    PRODUCTION = "production"


@dataclass(frozen=True)
class CandidateSource:
    revision: SourceRevision
    transcript: str
    anchors: Mapping[str, SourceAnchor]


class CandidateSourcePreparer:
    """Resolve immutable revision IDs into hash-verified, bounded compiler inputs."""

    def __init__(self, storage: Any, *, max_anchor_chars: int = MAX_PREPARED_ANCHOR_CHARS):
        if not isinstance(max_anchor_chars, int) or isinstance(max_anchor_chars, bool) or max_anchor_chars < 1:
            raise ValueError("anchor character budget must be positive")
        self._storage = storage
        self._max_anchor_chars = max_anchor_chars

    def prepare(self, revision_ids: tuple[str, ...]) -> tuple[CandidateSource, ...]:
        if (
            not isinstance(revision_ids, tuple)
            or not revision_ids
            or len(set(revision_ids)) != len(revision_ids)
            or any(not isinstance(revision_id, str) or not revision_id for revision_id in revision_ids)
        ):
            raise ValueError("candidate preparation requires unique immutable revision IDs")
        revisions = []
        for revision_id in revision_ids:
            revision = self._storage.source_revision(revision_id)
            if revision is None:
                raise ValueError("candidate preparation found an unknown source revision")
            if revision.lifecycle_state not in {"active", "replacement_pending"}:
                raise ValueError("source revision is not candidate eligible")
            if not revision.remote_file_id:
                raise ValueError("source revision is not remote eligible")
            revisions.append(revision)
        if len({revision.source_id for revision in revisions}) != len(revisions):
            raise ValueError("candidate preparation cannot select multiple revisions of one logical source")
        sources = []
        for revision in revisions:
            path = Path(revision.local_locator)
            try:
                raw = path.read_bytes()
            except OSError as error:
                raise ValueError("source revision bytes are unavailable") from error
            if len(raw) != revision.byte_size:
                raise ValueError("source revision byte size does not match inventory")
            if sha256(raw).hexdigest() != revision.content_sha256:
                raise ValueError("source revision hash does not match inventory")
            try:
                transcript = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("source revision is not valid UTF-8") from error
            anchors = bounded_transcript_anchors(
                revision, transcript, max_chars=self._max_anchor_chars
            )
            sources.append(
                CandidateSource(
                    revision,
                    transcript,
                    {anchor.anchor_id: anchor for anchor in anchors},
                )
            )
        return tuple(sources)


@dataclass(frozen=True)
class BuildRequest:
    run: CompilationRun
    sources: tuple[CandidateSource, ...]
    artifact_scope: ArtifactScope
    stale_revision_ids: tuple[str, ...] = ()
    revision_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrientationArtifact:
    record_id: str
    concept_id: str
    content: str
    attributes: dict[str, str | int]
    concept_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemoteArtifact:
    kind: str
    scope: ArtifactScope
    store_id: str | None
    file_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateBuildResult:
    snapshot: CorpusSnapshot
    ready: bool
    gate: CandidateGateResult | None
    records: tuple[DerivedRecord, ...]
    dependency_graph: DependencyGraph
    orientation_artifacts: tuple[OrientationArtifact, ...]
    raw_artifact: RemoteArtifact
    derived_artifact: RemoteArtifact
    stage_metrics: tuple[CompilationMetric, ...]
    total_metric: CompilationMetric
    failures: tuple[str, ...]
    excluded_record_ids: tuple[str, ...] = ()
    reconciliation_coverage: ReconciliationCoverage | None = None


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
        source_preparer: CandidateSourcePreparer | None = None,
        validation_pricing: TokenPricing | None = None,
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
        self._validation_pricing = validation_pricing
        self._live_mode = live_mode
        self._readiness_checks = readiness_checks
        self._sleep = sleep
        self._clock = clock
        self._now = now
        self._source_preparer = source_preparer or CandidateSourcePreparer(storage)

    def build(self, request: BuildRequest) -> CandidateBuildResult:
        requested_sources = _validate_request(request)
        scope = request.artifact_scope.value
        if getattr(self._storage, "runtime_scope", None) != scope:
            raise ValueError("candidate artifact scope does not match runtime scope")
        self._preflight_live_pricing()
        request = replace(request, run=self._bound_compilation_run(request.run))
        if request.revision_ids:
            requested_sources = self._source_preparer.prepare(request.revision_ids)
        sources = _canonical_sources(self._storage, requested_sources)
        for source in sources:
            _validate_candidate_source(source)
        revision_ids, _fingerprint, snapshot_id = CorpusSnapshot.identity_for(
            request.run.run_id,
            [source.revision.revision_id for source in sources],
            compiler_versions=(
                request.run.model_version,
                request.run.prompt_version,
                request.run.schema_version,
            ),
        )
        existing_run = self._storage.compilation_run(request.run.run_id)
        if existing_run is not None:
            existing = self._storage.snapshot(snapshot_id)
            same_versions = (
                existing_run.model_version,
                existing_run.prompt_version,
                existing_run.schema_version,
                existing_run.started_at,
            ) == (
                request.run.model_version,
                request.run.prompt_version,
                request.run.schema_version,
                request.run.started_at,
            )
            same_scope = existing is not None and self._storage.candidate_artifact_scope(
                existing.snapshot_id
            ) == scope
            if existing is not None and existing.status == "failed" and same_versions and same_scope:
                return self._existing_failed_result(request, existing)
            raise ValueError("compilation run ID already belongs to another or unfinished candidate")
        previous_snapshot = self._storage.current_snapshot()
        snapshot = CorpusSnapshot.create(
            run=request.run,
            selected_revisions=[source.revision for source in sources],
            raw_store_id=None,
            derived_store_id=None,
            created_at=self._now(),
        )
        if snapshot.selected_revision_ids != revision_ids:
            raise ValueError("candidate revision selection is not canonical")
        self._storage.create_compilation_candidate(request.run, snapshot)
        self._storage.record_candidate_artifact_scope(snapshot.snapshot_id, scope)
        self._storage.store_source_anchors(
            tuple(anchor for source in sources for anchor in source.anchors.values())
        )

        stage_metrics: list[CompilationMetric] = []
        failures: list[str] = []
        empty_graph = DependencyGraph(())
        empty_raw = RemoteArtifact("raw", request.artifact_scope, None, ())
        empty_derived = RemoteArtifact("derived", request.artifact_scope, None, ())

        source_results: list[SourceProcessingResult] = []
        reused_records: tuple[DerivedRecord, ...] = ()
        reused_hints: tuple[ConceptHint, ...] = ()
        reused_revision_ids: set[str] = set()
        reconciliation_revision_ids = set(snapshot.selected_revision_ids)
        if previous_snapshot is not None and _compiler_config_matches(previous_snapshot, request.run):
            stale_predecessor_revisions = _stale_predecessor_revisions(
                self._storage, previous_snapshot, sources, request.stale_revision_ids
            )
            reconciliation_revision_ids = _selective_reconciliation_revision_ids(
                self._storage,
                previous_snapshot,
                sources,
                stale_predecessor_revisions,
            )
            record_mapping, reused_records = self._storage.clone_reusable_records(
                previous_snapshot.snapshot_id,
                snapshot.snapshot_id,
                selected_revision_ids=snapshot.selected_revision_ids,
                stale_revision_ids=stale_predecessor_revisions,
            )
            reused_hints = _remapped_concept_hints(
                self._storage, previous_snapshot.snapshot_id, record_mapping
            )
            for record in reused_records:
                if record.derived_kind != "source_extracted_claim":
                    continue
                reused_revision_ids.update(
                    dependency.identifier
                    for dependency in record.dependencies
                    if dependency.kind == "source_revision"
                )

        extraction_usage = CallUsage()
        validation_usage = CallUsage()
        extraction_calls = validation_calls = 0
        extraction_failures = validation_failures = 0
        extracted_candidate_count = extraction_latency_ms = validation_latency_ms = 0

        extraction_hints: list[ConceptHint] = []
        for source in sources:
            if source.revision.revision_id in reused_revision_ids:
                reused_count = sum(
                    record.derived_kind == "source_extracted_claim"
                    and any(
                        dependency.kind == "source_revision"
                        and dependency.identifier == source.revision.revision_id
                        for dependency in record.dependencies
                    )
                    for record in reused_records
                )
                source_results.append(
                    SourceProcessingResult(source.revision.revision_id, "processed", reused_count)
                )
                continue
            accepted = 0
            source_failed = False
            try:
                extraction_started = self._clock()
                extraction_calls += 1
                extracted = self._extractor.extract(
                    revision=source.revision,
                    snapshot_id=snapshot.snapshot_id,
                    transcript=source.transcript,
                    anchor_spans={
                        anchor_id: resolve_anchor_span(anchor, source.revision, source.transcript)
                        for anchor_id, anchor in source.anchors.items()
                    },
                )
                _validate_extraction_result(extracted, source)
                extraction_usage = _sum_usage(extraction_usage, extracted.usage)
                extracted_candidate_count += len(extracted.candidates)
                extraction_latency_ms += _elapsed_ms(extraction_started, self._clock())
            except Exception as error:
                extraction_latency_ms += _elapsed_ms(extraction_started, self._clock())
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
                            pricing=self._validation_pricing,
                            concept_hints=tuple(
                                hint for hint in extracted.hints
                                if hint.record_id == candidate.record_id
                            ),
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
                            extraction_hints.extend(outcome.validated_hints)
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
                extracted_candidate_count,
                extraction_calls,
                extraction_usage,
                extraction_latency_ms,
                extraction_calls if self._live_mode else 0,
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
                validation_calls if self._live_mode else 0,
                validation_failures,
            )
        )

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
        synthesized_count = 0
        synthesis_invoked = False
        synthesized: SynthesisResult | None = None
        hints: tuple[ConceptHint, ...] = reused_hints + tuple(extraction_hints)
        try:
            extracted_records = tuple(self._storage.derived_records(snapshot.snapshot_id, include_stale=True))
            source_metadata = _reconciliation_sources(self._storage, sources)
            all_source_extracted = tuple(
                record for record in extracted_records
                if record.derived_kind == "source_extracted_claim"
            )
            synthesis_inputs = (
                all_source_extracted
                if extraction_calls == 0
                else tuple(
                    record for record in all_source_extracted
                    if any(
                        dependency.kind == "source_revision"
                        and dependency.identifier in reconciliation_revision_ids
                        for dependency in record.dependencies
                    )
                )
            )
            if extraction_calls == 0 and reused_records:
                synthesized = SynthesisResult(
                    (), self._synthesis_provenance(),
                    coverage=ReconciliationCoverage(
                        len(synthesis_inputs),
                        tuple(sorted(record.record_id for record in synthesis_inputs)),
                        0,
                        0,
                    ),
                )
            else:
                synthesis_invoked = True
                synthesis_sources = tuple(
                    source for source in sources
                    if source.revision.revision_id in reconciliation_revision_ids
                )
                synthesis_metadata = tuple(
                    item for item in source_metadata
                    if item.revision_id in reconciliation_revision_ids
                )
                synthesis_hints = tuple(
                    hint for hint in hints
                    if hint.record_id in {record.record_id for record in synthesis_inputs}
                )
                synthesized = self._synthesizer.synthesize(
                    snapshot_id=snapshot.snapshot_id,
                    records=synthesis_inputs,
                    revisions=tuple(source.revision for source in synthesis_sources),
                    source_metadata=synthesis_metadata,
                    anchor_spans=_source_anchor_spans(synthesis_sources),
                    hints=synthesis_hints,
                )
            if not isinstance(synthesized, SynthesisResult):
                raise ValueError("synthesis stage must return SynthesisResult")
            synthesis_usage = synthesized.usage
            synthesized_count = len(synthesized.records)
            hints += tuple(synthesized.hints)
            _validate_synthesis_result(
                synthesized,
                snapshot.snapshot_id,
                {anchor_id for source in sources for anchor_id in source.anchors},
                source_metadata,
            )
            _validate_reconciliation_coverage(synthesized, synthesis_inputs)
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
        synthesis_call_count = (
            synthesized.call_count
            if isinstance(synthesized, SynthesisResult) and synthesized.call_count > 0
            else int(synthesis_invoked)
        )
        stage_metrics.append(
            self._record_metric(
                request.run,
                "synthesis",
                len(sources),
                synthesized_count,
                synthesis_call_count,
                synthesis_usage,
                _elapsed_ms(synthesis_started, self._clock()),
                synthesis_call_count if self._live_mode else 0,
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

        setup_started = self._clock()
        setup_calls = 0
        raw_store = derived_store = None
        try:
            setup_calls += 1
            raw_operation = self._storage.begin_candidate_remote_operation(
                snapshot.snapshot_id, "remote_setup", "create_store"
            )
            try:
                raw_store = self._vector_stores.create_store(
                    f"Phase 3 {scope} raw {request.run.run_id}",
                    {"snapshot_id": snapshot_id, "artifact_scope": scope, "artifact_kind": "raw"},
                )
                snapshot = self._storage.record_candidate_store(snapshot_id, "raw", raw_store.store_id)
                _reject_failed_store(raw_store)
            except Exception:
                self._storage.finish_candidate_remote_operation(
                    raw_operation,
                    "failed",
                    store_id=getattr(raw_store, "store_id", None),
                )
                raise
            self._storage.finish_candidate_remote_operation(
                raw_operation, "succeeded", store_id=raw_store.store_id
            )

            setup_calls += 1
            derived_operation = self._storage.begin_candidate_remote_operation(
                snapshot.snapshot_id, "remote_setup", "create_store"
            )
            try:
                derived_store = self._vector_stores.create_store(
                    f"Phase 3 {scope} derived {request.run.run_id}",
                    {"snapshot_id": snapshot_id, "artifact_scope": scope, "artifact_kind": "derived"},
                )
                snapshot = self._storage.record_candidate_store(
                    snapshot_id, "derived", derived_store.store_id
                )
                _reject_failed_store(derived_store)
            except Exception:
                self._storage.finish_candidate_remote_operation(
                    derived_operation,
                    "failed",
                    store_id=getattr(derived_store, "store_id", None),
                )
                raise
            self._storage.finish_candidate_remote_operation(
                derived_operation, "succeeded", store_id=derived_store.store_id
            )
        except Exception as error:
            failures.append(f"remote setup failed: {error}")
        stage_metrics.append(
            self._record_metric(
                request.run,
                "remote_setup",
                len(sources),
                0,
                setup_calls,
                CallUsage(),
                _elapsed_ms(setup_started, self._clock()),
                setup_calls,
                int(bool(failures)),
            )
        )
        empty_raw = RemoteArtifact("raw", request.artifact_scope, snapshot.raw_store_id, ())
        empty_derived = RemoteArtifact("derived", request.artifact_scope, snapshot.derived_store_id, ())
        if failures:
            return self._failed_result(
                request,
                gate,
                failures,
                stage_metrics,
                graph,
                orientation_artifacts,
                empty_raw,
                empty_derived,
            )

        raw_started = self._clock()
        raw_file_ids = tuple(source.revision.remote_file_id for source in sources)
        raw_calls = 0
        try:
            for source in sources:
                raw_calls += 1
                file_id = source.revision.remote_file_id
                operation = self._storage.begin_candidate_remote_operation(
                    snapshot.snapshot_id,
                    "raw_store",
                    "attach_file",
                    store_id=raw_store.store_id,
                    file_id=file_id,
                )
                attachment = None
                try:
                    attachment = self._vector_stores.attach_file(
                        raw_store.store_id,
                        file_id,
                        _raw_file_attributes(
                            self._storage, snapshot, request.artifact_scope, source
                        ),
                    )
                    raw_calls += self._await_attachment(
                        snapshot.snapshot_id,
                        raw_store.store_id,
                        file_id,
                        attachment,
                        artifact_kind="raw_store",
                    )
                except Exception:
                    self._storage.finish_candidate_remote_operation(
                        operation,
                        "failed",
                        attachment_id=getattr(attachment, "attachment_id", None),
                    )
                    raise
                self._storage.finish_candidate_remote_operation(
                    operation,
                    "succeeded",
                    attachment_id=getattr(attachment, "attachment_id", None),
                )
        except Exception as error:
            failures.append(f"raw store readiness failed: {error}")
        raw_artifact = RemoteArtifact("raw", request.artifact_scope, raw_store.store_id, raw_file_ids)
        stage_metrics.append(
            self._record_metric(
                request.run,
                "raw_store",
                len(sources),
                0,
                raw_calls,
                CallUsage(),
                _elapsed_ms(raw_started, self._clock()),
                raw_calls,
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
        derived_calls = 0
        try:
            for artifact in orientation_artifacts:
                derived_calls += 1
                upload_operation = self._storage.begin_candidate_remote_operation(
                    snapshot.snapshot_id, "derived_store", "upload_file"
                )
                file_id = None
                try:
                    file_id = self._vector_stores.upload_text(
                        f"{artifact.record_id}.json",
                        artifact.content,
                    )
                except Exception:
                    self._storage.finish_candidate_remote_operation(upload_operation, "failed")
                    raise
                self._storage.finish_candidate_remote_operation(
                    upload_operation, "succeeded", file_id=file_id
                )
                derived_file_ids.append(file_id)
                derived_calls += 1
                attach_operation = self._storage.begin_candidate_remote_operation(
                    snapshot.snapshot_id,
                    "derived_store",
                    "attach_file",
                    store_id=derived_store.store_id,
                    file_id=file_id,
                )
                attachment = None
                try:
                    attachment = self._vector_stores.attach_file(
                        derived_store.store_id,
                        file_id,
                        artifact.attributes,
                    )
                    derived_calls += self._await_attachment(
                        snapshot.snapshot_id, derived_store.store_id, file_id, attachment
                    )
                except Exception:
                    self._storage.finish_candidate_remote_operation(
                        attach_operation,
                        "failed",
                        attachment_id=getattr(attachment, "attachment_id", None),
                    )
                    raise
                self._storage.finish_candidate_remote_operation(
                    attach_operation,
                    "succeeded",
                    attachment_id=attachment.attachment_id,
                )
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
            reconciliation_coverage=(
                synthesized.coverage if isinstance(synthesized, SynthesisResult) else None
            ),
        )

    def _preflight_live_pricing(self) -> None:
        for stage in (self._extractor, self._synthesizer):
            preflight = getattr(stage, "preflight_live_pricing", None)
            if callable(preflight):
                preflight()
        if self._validation_model == "gpt-5.6-sol":
            if not self._live_mode or self._validation_pricing is None:
                raise ValueError(
                    "GPT-5.6 Sol live validation requires complete caller-supplied pricing"
                )
            self._validation_pricing.require_complete("validation")

    def _bound_compilation_run(self, run: CompilationRun) -> CompilationRun:
        provenances = (
            self._extractor.provenance,
            CompilerProvenance(
                self._validation_model,
                SEMANTIC_VALIDATION_PROMPT_VERSION,
                SEMANTIC_VALIDATION_SCHEMA_VERSION,
            ),
            self._synthesis_provenance(),
        )
        if any(not isinstance(provenance, CompilerProvenance) for provenance in provenances):
            raise ValueError("candidate compiler stages require immutable provenance")
        return replace(
            run,
            model_version=_aggregate_stage_version(
                "models", tuple(provenance.model_version for provenance in provenances)
            ),
            prompt_version=_aggregate_stage_version(
                "prompts", tuple(provenance.prompt_version for provenance in provenances)
            ),
            schema_version=_aggregate_stage_version(
                "schemas", tuple(provenance.schema_version for provenance in provenances)
            ),
        )

    def _synthesis_provenance(self) -> CompilerProvenance:
        provenance = getattr(self._synthesizer, "provenance", None)
        if not isinstance(provenance, CompilerProvenance):
            raise ValueError("synthesis stage requires immutable provenance")
        return provenance

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
            record.record_id: candidate.concept_ids_for_record(record.record_id)
            for record in records
        }
        primary_concepts = {
            record.record_id: candidate.primary_concept_id_for_record(record.record_id)
            for record in records
        }
        self._storage.store_orientation_concept_ids(
            snapshot.snapshot_id,
            record_concepts,
            concepts=candidate.concepts,
            primary_concept_ids=primary_concepts,
            concept_occurrences=candidate.concept_occurrences,
        )
        artifacts = []
        for record in records:
            concept_ids = record_concepts[record.record_id]
            concept_id = primary_concepts[record.record_id]
            collection_id, year, record_scope = self._storage.orientation_source_area(
                snapshot.snapshot_id, record
            )
            content = render_orientation_artifact(
                record,
                concept_id,
                (collection_id, year, record_scope),
                max_bytes=self._orientation_budget.max_tokens,
                concepts=concept_summaries(
                    candidate.concepts,
                    candidate.concept_occurrences,
                    concept_ids,
                    record_id=record.record_id,
                ),
            )
            attributes: dict[str, str | int] = {
                "snapshot_id": snapshot.snapshot_id,
                "status": "published",
                "artifact_scope": artifact_scope.value,
                "record_id": record.record_id,
                "concept_id": concept_id,
                "family": record.family,
                "derived_kind": record.derived_kind,
                "semantic_subtype": record.semantic_subtype,
                "schema_version": snapshot.schema_version,
            }
            if collection_id is not None:
                attributes["collection_id"] = collection_id
            if year is not None:
                attributes["year"] = year
            if record_scope is not None:
                attributes["scope"] = record_scope
            artifacts.append(OrientationArtifact(record.record_id, concept_id, content, attributes, concept_ids))
        return tuple(artifacts)

    def _await_batch(self, snapshot_id: str, store_id: str, batch: Any) -> int:
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
                calls += 1
                operation = self._storage.begin_candidate_remote_operation(
                    snapshot_id,
                    "raw_store",
                    "batch_status",
                    store_id=store_id,
                    batch_id=batch.batch_id,
                )
                try:
                    current = self._vector_stores.batch_status(store_id, batch.batch_id)
                except Exception:
                    self._storage.finish_candidate_remote_operation(operation, "failed")
                    raise
                self._storage.finish_candidate_remote_operation(operation, "succeeded")
        raise ValueError("batch did not become ready within the configured checks")

    def _await_attachment(
        self,
        snapshot_id: str,
        store_id: str,
        file_id: str,
        attachment: Any,
        *,
        artifact_kind: str = "derived_store",
    ) -> int:
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
                calls += 1
                operation = self._storage.begin_candidate_remote_operation(
                    snapshot_id,
                    artifact_kind,
                    "attachment_status",
                    store_id=store_id,
                    file_id=file_id,
                    attachment_id=getattr(current, "attachment_id", None),
                )
                try:
                    current = self._vector_stores.attachment_status(store_id, file_id)
                except Exception:
                    self._storage.finish_candidate_remote_operation(operation, "failed")
                    raise
                self._storage.finish_candidate_remote_operation(
                    operation,
                    "succeeded",
                    attachment_id=getattr(current, "attachment_id", None),
                )
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
                stage=stage,
                source_count=source_count,
                record_count=record_count,
                call_count=call_count,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=latency_ms,
                cost_usd=usage.cost_usd,
                remote_calls=remote_calls,
                failure_count=failure_count,
                reasoning_tokens=usage.reasoning_tokens,
            ),
        )

    def _total_metric(
        self, run: CompilationRun, metrics: list[CompilationMetric]
    ) -> CompilationMetric:
        total = CompilationMetric(
            stage="total",
            source_count=max((metric.source_count for metric in metrics), default=0),
            record_count=max((metric.record_count for metric in metrics), default=0),
            call_count=sum(metric.call_count for metric in metrics),
            input_tokens=sum(metric.input_tokens for metric in metrics),
            output_tokens=sum(metric.output_tokens for metric in metrics),
            latency_ms=sum(metric.latency_ms for metric in metrics),
            cost_usd=sum(metric.cost_usd for metric in metrics),
            remote_calls=sum(metric.remote_calls for metric in metrics),
            failure_count=sum(metric.failure_count for metric in metrics),
            reasoning_tokens=sum(metric.reasoning_tokens for metric in metrics),
        )
        return self._storage.record_compilation_metric(run.run_id, total)

    def _failed_result(
        self,
        request: BuildRequest,
        gate: CandidateGateResult | None,
        failures: list[str],
        stage_metrics: list[CompilationMetric],
        graph: DependencyGraph,
        orientation_artifacts: tuple[OrientationArtifact, ...],
        raw_artifact: RemoteArtifact,
        derived_artifact: RemoteArtifact,
        excluded_record_ids: tuple[str, ...] = (),
    ) -> CandidateBuildResult:
        snapshot_id = gate.snapshot_id if gate is not None else CorpusSnapshot.identity_for(
            request.run.run_id,
            [source.revision.revision_id for source in request.sources],
            compiler_versions=(
                request.run.model_version,
                request.run.prompt_version,
                request.run.schema_version,
            ),
        )[2]
        snapshot = self._storage.snapshot(snapshot_id)
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

    def _existing_failed_result(
        self, request: BuildRequest, snapshot: CorpusSnapshot
    ) -> CandidateBuildResult:
        metrics = self._storage.compilation_metrics(snapshot.run_id)
        stage_metrics = tuple(metric for metric in metrics if metric.stage != "total")
        total = next((metric for metric in reversed(metrics) if metric.stage == "total"), None)
        if total is None:
            total = self._total_metric(request.run, list(stage_metrics))
        records = tuple(self._storage.derived_records(snapshot.snapshot_id, include_stale=True))
        try:
            graph = self._storage.dependency_graph(snapshot.snapshot_id)
        except ValueError:
            graph = DependencyGraph(())
        return CandidateBuildResult(
            snapshot,
            False,
            self._storage.candidate_gate(snapshot.snapshot_id),
            records,
            graph,
            (),
            RemoteArtifact("raw", request.artifact_scope, snapshot.raw_store_id, ()),
            RemoteArtifact("derived", request.artifact_scope, snapshot.derived_store_id, ()),
            stage_metrics,
            total,
            (snapshot.failure_reason or "existing candidate failed",),
        )


def _validate_request(request: BuildRequest) -> tuple[CandidateSource, ...]:
    if not isinstance(request, BuildRequest):
        raise ValueError("candidate build requires BuildRequest")
    if not isinstance(request.run, CompilationRun) or request.run.status != "building":
        raise ValueError("candidate build requires a building compilation run")
    if not isinstance(request.artifact_scope, ArtifactScope):
        raise ValueError("candidate build requires an explicit artifact scope")
    if not isinstance(request.sources, tuple) or not isinstance(request.revision_ids, tuple):
        raise ValueError("candidate build selections must be immutable tuples")
    if bool(request.sources) == bool(request.revision_ids):
        raise ValueError("candidate build requires sources or revision IDs, not both")
    if any(not isinstance(source, CandidateSource) for source in request.sources):
        raise ValueError("candidate build sources must be typed")
    revision_ids = (
        tuple(source.revision.revision_id for source in request.sources)
        if request.sources
        else request.revision_ids
    )
    if len(set(revision_ids)) != len(revision_ids):
        raise ValueError("candidate build revisions must be unique")
    if request.sources and len({source.revision.source_id for source in request.sources}) != len(
        request.sources
    ):
        raise ValueError("candidate build cannot select multiple revisions of one logical source")
    if (
        not isinstance(request.stale_revision_ids, tuple)
        or len(set(request.stale_revision_ids)) != len(request.stale_revision_ids)
    ):
        raise ValueError("stale revision IDs must be unique")
    return request.sources


def _stale_predecessor_revisions(
    storage: Any,
    previous_snapshot: CorpusSnapshot,
    sources: tuple[CandidateSource, ...],
    explicit_stale_revision_ids: tuple[str, ...],
) -> tuple[str, ...]:
    selected_by_source = {source.revision.source_id: source.revision.revision_id for source in sources}
    stale = set(explicit_stale_revision_ids)
    for revision_id in previous_snapshot.selected_revision_ids:
        revision = storage.source_revision(revision_id)
        if revision is None:
            raise ValueError("published predecessor references an unknown source revision")
        if selected_by_source.get(revision.source_id) != revision_id:
            stale.add(revision_id)
    return tuple(sorted(stale))


def _selective_reconciliation_revision_ids(
    storage: Any,
    previous_snapshot: CorpusSnapshot,
    sources: tuple[CandidateSource, ...],
    stale_revision_ids: tuple[str, ...],
) -> set[str]:
    """Map the predecessor's affected synthesis closure onto current revisions."""
    current_by_source = {
        source.revision.source_id: source.revision.revision_id for source in sources
    }
    previous_by_source: dict[str, str] = {}
    for revision_id in previous_snapshot.selected_revision_ids:
        revision = storage.source_revision(revision_id)
        if revision is None:
            raise ValueError("published predecessor references an unknown source revision")
        previous_by_source[revision.source_id] = revision_id
    added_source_ids = set(current_by_source).difference(previous_by_source)
    if added_source_ids:
        # A newly introduced source has no predecessor dependency closure; its
        # possible cross-corpus effects must be reconciled conservatively.
        return set(current_by_source.values())

    affected_source_ids = {
        revision.source_id
        for revision_id in stale_revision_ids
        if (revision := storage.source_revision(revision_id)) is not None
    }
    if stale_revision_ids:
        affected_record_ids = set(
            storage.rebuild_record_ids(previous_snapshot.snapshot_id, stale_revision_ids)
        )
        for record in storage.derived_records(previous_snapshot.snapshot_id, include_stale=True):
            if record.record_id not in affected_record_ids:
                continue
            for dependency in record.dependencies:
                if dependency.kind != "source_revision":
                    continue
                revision = storage.source_revision(dependency.identifier)
                if revision is None:
                    raise ValueError("affected synthesis references an unknown source revision")
                affected_source_ids.add(revision.source_id)
    return {
        revision_id
        for source_id, revision_id in current_by_source.items()
        if source_id in affected_source_ids
    }


def _remapped_concept_hints(
    storage: Any, previous_snapshot_id: str, record_id_mapping: Mapping[str, str]
) -> tuple[ConceptHint, ...]:
    if not record_id_mapping:
        return ()
    concepts = {
        concept.concept_id: concept for concept in storage.orientation_concepts(previous_snapshot_id)
    }
    hints = []
    for occurrence in storage.orientation_concept_occurrences(previous_snapshot_id):
        record_id = record_id_mapping.get(occurrence.record_id)
        concept = concepts.get(occurrence.concept_id)
        if record_id is None or concept is None:
            continue
        aliases = tuple(
            label for label in (concept.canonical_label, *concept.aliases)
            if _hint_label_key(label) != occurrence.label_key
        )
        hints.append(
            ConceptHint(
                record_id, occurrence.label_key, aliases, occurrence.scope,
                occurrence.role, occurrence.position,
            )
        )
    return tuple(hints)


def _hint_label_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _canonical_sources(storage: Any, sources: tuple[CandidateSource, ...]) -> tuple[CandidateSource, ...]:
    canonical_sources = []
    for source in sources:
        canonical = storage.source_revision(source.revision.revision_id)
        if canonical is None:
            raise ValueError("unknown source revision")
        if canonical != source.revision:
            raise ValueError("requested source revision does not match canonical stored identity")
        canonical_sources.append(CandidateSource(canonical, source.transcript, source.anchors))
    if len({source.revision.source_id for source in canonical_sources}) != len(canonical_sources):
        raise ValueError("candidate build cannot select multiple revisions of one logical source")
    return tuple(canonical_sources)


def _validate_candidate_source(source: CandidateSource) -> None:
    if source.revision.lifecycle_state not in {"active", "replacement_pending"}:
        raise ValueError("candidate sources must use eligible revisions")
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


def _source_anchor_spans(sources: tuple[CandidateSource, ...]) -> dict[str, str]:
    return {
        anchor_id: resolve_anchor_span(anchor, source.revision, source.transcript)
        for source in sources
        for anchor_id, anchor in source.anchors.items()
    }


def _raw_file_attributes(
    storage: Any,
    snapshot: CorpusSnapshot,
    scope: ArtifactScope,
    candidate_source: CandidateSource,
) -> dict[str, str | int]:
    source = storage.library_source(candidate_source.revision.source_id)
    if source is None or source.collection_id != candidate_source.revision.collection_id:
        raise ValueError("raw candidate file has no canonical source metadata")
    relative_path = f"{source.year}/{source.original_filename}"
    return {
        "snapshot_id": snapshot.snapshot_id,
        "artifact_scope": scope.value,
        "status": "published",
        "collection_id": source.collection_id,
        "source_id": source.source_id,
        "source": source.original_filename,
        "year": source.year,
        "course": source.course,
        "lesson": source.lesson_title,
        "relative_path": relative_path,
    }


def _validate_extraction_result(result: ExtractionResult, source: CandidateSource) -> None:
    if not isinstance(result, ExtractionResult) or result.revision_id != source.revision.revision_id:
        raise ValueError("extraction result does not match its source revision")
    known_anchor_ids = set(source.anchors)
    for candidate in result.candidates:
        if not set(candidate.anchors) <= known_anchor_ids:
            raise ValueError("extraction returned an unknown deterministic anchor")
    candidate_ids = {candidate.record_id for candidate in result.candidates}
    if len(candidate_ids) != len(result.candidates):
        raise ValueError("extraction returned duplicate candidate identities")
    for hint in result.hints:
        if not isinstance(hint, ConceptHint) or hint.record_id not in candidate_ids:
            raise ValueError("extraction concept hint does not belong to an extracted candidate")


def _compiler_config_matches(snapshot: CorpusSnapshot, run: CompilationRun) -> bool:
    """Only identical compiler provenance may participate in selective reuse."""
    return (
        snapshot.model_version,
        snapshot.prompt_version,
        snapshot.schema_version,
    ) == (
        run.model_version,
        run.prompt_version,
        run.schema_version,
    )


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


def _validate_synthesis_result(
    result: SynthesisResult,
    snapshot_id: str,
    known_anchor_ids: set[str],
    source_metadata: tuple[ReconciliationSource, ...],
) -> None:
    if not isinstance(result.provenance, CompilerProvenance):
        raise ValueError("source synthesis requires compiler provenance")
    allowed = (Claim, Relationship, ProcedureSequenceHierarchy, Evolution, ConflictUnresolved)
    for record in result.records:
        validate_record(record)
        if not isinstance(record, allowed):
            raise ValueError("source synthesis returned an unsupported record family")
        if record.snapshot_id != snapshot_id or record.validation_state != "validated" or record.lifecycle_state != "active":
            raise ValueError("source synthesis records must be validated, active, and candidate-owned")
        if record.evidence_state != "cross_source_synthesis":
            raise ValueError("source synthesis cannot claim raw authority")
        if isinstance(record, Claim) and record.semantic_subtype != "strategy_implication":
            raise ValueError("source synthesis claims must remain strategy implications")
        if record.derived_kind not in {"cross_source_synthesis", "unresolved_or_conflicting"}:
            raise ValueError("source synthesis requires synthesis provenance")
        if record.compiler_provenance != result.provenance:
            raise ValueError("source synthesis records require matching compiler provenance")
        if not any(dependency.kind == "source_revision" for dependency in record.dependencies):
            raise ValueError("source synthesis records require source revision provenance")
        if not set(record.anchors) <= known_anchor_ids:
            raise ValueError("source synthesis records require canonical source anchors")
        if isinstance(record, Evolution):
            earlier = source_coverage(record.earlier_source_set, source_metadata)
            later = source_coverage(record.later_source_set, source_metadata)
            if (
                (record.earlier_coverage_id, record.earlier_observed_years) != earlier
                or (record.later_coverage_id, record.later_observed_years) != later
            ):
                raise ValueError("evolution coverage is not grounded in canonical source metadata")


def _validate_reconciliation_coverage(
    result: SynthesisResult, input_records: tuple[DerivedRecord, ...]
) -> None:
    coverage = result.coverage
    expected = tuple(sorted(record.record_id for record in input_records))
    if (
        not isinstance(coverage, ReconciliationCoverage)
        or not coverage.complete
        or coverage.input_record_count != len(expected)
        or coverage.covered_record_ids != expected
    ):
        raise ValueError("reconciliation coverage must exactly cover every input record")


def _aggregate_stage_version(kind: str, values: tuple[str, ...]) -> str:
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("candidate compiler stage provenance is incomplete")
    digest = sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()
    return f"phase3-{kind}-v1-{digest}"


def _reconciliation_sources(
    storage: Any, sources: tuple[CandidateSource, ...]
) -> tuple[ReconciliationSource, ...]:
    result = []
    for candidate_source in sources:
        source = storage.library_source(candidate_source.revision.source_id)
        if (
            source is None
            or source.collection_id != candidate_source.revision.collection_id
        ):
            raise ValueError("candidate source has no canonical library metadata")
        result.append(ReconciliationSource(
            revision_id=candidate_source.revision.revision_id,
            collection_id=source.collection_id,
            source_id=source.source_id,
            author=source.author,
            course=source.course,
            lesson_title=source.lesson_title,
            year=source.year,
            original_filename=source.original_filename,
        ))
    return tuple(result)


def _sum_usage(first: CallUsage, second: CallUsage) -> CallUsage:
    if not isinstance(second, CallUsage):
        raise ValueError("compiler stages require typed call usage")
    return CallUsage(
        first.input_tokens + second.input_tokens,
        first.output_tokens + second.output_tokens,
        first.cost_usd + second.cost_usd,
        first.reasoning_tokens + second.reasoning_tokens,
    )


def _elapsed_ms(started: float, ended: float) -> int:
    return max(0, int((ended - started) * 1000))


def _remote_failure(kind: str, result: Any) -> str:
    last_error = getattr(result, "last_error", None)
    message = getattr(last_error, "message", None)
    return f"{kind} failed: {message or getattr(result, 'status', 'unknown')}"


def _reject_failed_store(store: Any) -> None:
    if getattr(store, "status", None) in _TERMINAL_REMOTE_FAILURES:
        raise ValueError(_remote_failure("store", store))
