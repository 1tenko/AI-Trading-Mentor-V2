"""Candidate-scoped concepts assembled from validated typed records."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from mentor.compilation import CallUsage, TokenPricing, usage_from_response

from mentor.derived_records import (
    Claim,
    CompilerProvenance,
    ConflictUnresolved,
    DerivedRecord,
    Evolution,
    ProcedureRecordBranch,
    ProcedureSequenceHierarchy,
    Relationship,
    RecordDependency,
    is_legacy_record,
    reject_private_or_raw_text,
    validate_record,
)


MAX_LABEL_LENGTH = 120
MAX_ALIASES = 8
MAX_SCOPE_LENGTH = 160
MAX_CONDITION_LENGTH = 160
MAX_JUSTIFICATION_LENGTH = 280
SYNTHESIS_PROMPT_VERSION = "cross-source-synthesis-v4"
SYNTHESIS_SCHEMA_VERSION = "cross-source-synthesis-schema-v5"
SOL_MODEL = "gpt-5.6-sol"
MAX_SYNTHESIS_RECORDS_PER_CALL = 64
MAX_SYNTHESIS_RECORDS_PER_CANDIDATE = 4_096
MAX_RECONCILIATION_CALLS = 1_024


@dataclass(frozen=True)
class ReconciliationCoverage:
    input_record_count: int
    covered_record_ids: tuple[str, ...]
    primary_call_count: int
    bridge_call_count: int

    @property
    def complete(self) -> bool:
        return self.input_record_count == len(self.covered_record_ids)


@dataclass(frozen=True)
class ReconciliationSource:
    """Safe registry metadata used to ground source-set and chronology claims."""

    revision_id: str
    collection_id: str
    source_id: str
    author: str
    course: str
    lesson_title: str
    year: int
    original_filename: str

    @property
    def coverage_id(self) -> str:
        return _source_coverage((self.revision_id,), {self.revision_id: self})[0]


@dataclass(frozen=True)
class _ReconciliationBatch:
    records: tuple[DerivedRecord, ...]
    kind: str


@dataclass(frozen=True)
class _ClusterSummary:
    representative: DerivedRecord
    covered_records: tuple[DerivedRecord, ...]
    lineage_records: tuple[DerivedRecord, ...]
    conclusions: tuple[str, ...]


@dataclass(frozen=True)
class SynthesisResult:
    records: tuple[DerivedRecord, ...]
    provenance: CompilerProvenance
    usage: CallUsage = CallUsage()
    hints: tuple[Any, ...] = ()
    call_count: int = 0
    coverage: ReconciliationCoverage | None = None


class SynthesisReconciler:
    """Create typed cross-source records through a caller-owned Responses client."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = "synthetic-synthesizer",
        live_mode: bool = False,
        max_records_per_call: int = 16,
        max_total_records: int = MAX_SYNTHESIS_RECORDS_PER_CANDIDATE,
        max_calls: int = MAX_RECONCILIATION_CALLS,
        pricing: TokenPricing | None = None,
    ):
        if model == SOL_MODEL and not live_mode:
            raise ValueError("GPT-5.6 Sol synthesis requires explicit live mode; use an injected mock otherwise")
        if model == SOL_MODEL and pricing is None:
            raise ValueError("GPT-5.6 Sol live synthesis requires caller-supplied pricing")
        if (
            not isinstance(max_records_per_call, int)
            or isinstance(max_records_per_call, bool)
            or max_records_per_call < 2
            or max_records_per_call > MAX_SYNTHESIS_RECORDS_PER_CALL
        ):
            raise ValueError("synthesis batch size must be bounded")
        if (
            not isinstance(max_total_records, int)
            or isinstance(max_total_records, bool)
            or not 1 <= max_total_records <= MAX_SYNTHESIS_RECORDS_PER_CANDIDATE
        ):
            raise ValueError("synthesis candidate size must be bounded")
        if (
            not isinstance(max_calls, int)
            or isinstance(max_calls, bool)
            or not 1 <= max_calls <= MAX_RECONCILIATION_CALLS
        ):
            raise ValueError("synthesis call count must be bounded")
        self._client = client
        self._provenance = CompilerProvenance(model, SYNTHESIS_PROMPT_VERSION, SYNTHESIS_SCHEMA_VERSION)
        self._max_records_per_call = max_records_per_call
        self._max_total_records = max_total_records
        self._max_calls = max_calls
        self._pricing = pricing
        self._live_mode = live_mode
        self.preflight_live_pricing()

    @property
    def provenance(self) -> CompilerProvenance:
        return self._provenance

    def preflight_live_pricing(self) -> None:
        if self._provenance.model_version == SOL_MODEL:
            if not self._live_mode or self._pricing is None:
                raise ValueError("GPT-5.6 Sol live synthesis requires complete caller-supplied pricing")
            self._pricing.require_complete("synthesis")

    def synthesize(
        self,
        *,
        snapshot_id: str,
        records: Sequence[DerivedRecord],
        revisions: Sequence[object],
        source_metadata: Sequence[ReconciliationSource],
        anchor_spans: dict[str, str],
        hints: Sequence[ConceptHint] = (),
        context_records: Sequence[DerivedRecord] = (),
    ) -> SynthesisResult:
        record_values = tuple(records)
        context_values = tuple(context_records)
        hint_values = tuple(hints)
        revision_values = tuple(revisions)
        for record in (*record_values, *context_values):
            validate_record(record)
            if record.snapshot_id != snapshot_id or record.validation_state != "validated":
                raise ValueError("synthesis requires validated candidate records")
        if {record.record_id for record in record_values} & {
            record.record_id for record in context_values
        }:
            raise ValueError("synthesis targets and context must be distinct")
        if not record_values:
            return SynthesisResult(
                (), self._provenance,
                coverage=ReconciliationCoverage(0, (), 0, 0),
            )
        if len(record_values) + len(context_values) > self._max_total_records:
            raise ValueError("reconciliation input exceeds the candidate safety limit")
        _validate_reconciliation_hints(record_values, hint_values)
        if not isinstance(anchor_spans, dict) or any(
            not isinstance(anchor_id, str) or not isinstance(span, str)
            for anchor_id, span in anchor_spans.items()
        ):
            raise ValueError("synthesis anchor spans must be an ID-to-text mapping")
        known_revisions = {
            getattr(revision, "revision_id", None): revision for revision in revision_values
        }
        if None in known_revisions or len(known_revisions) != len(revision_values):
            raise ValueError("synthesis revisions require unique immutable IDs")
        canonical_sources = _canonical_reconciliation_sources(
            revision_values, tuple(source_metadata)
        )
        primary_batches = _reconciliation_batches(
            record_values, self._max_records_per_call, hint_values
        )
        context_batches = _reconciliation_batches(
            context_values, self._max_records_per_call, pack_components=False
        ) if context_values else ()
        summary_count = len(primary_batches) + len(context_batches)
        planned_calls = (
            len(primary_batches)
            + _hierarchical_call_count(summary_count, self._max_records_per_call)
            - summary_count
        )
        if planned_calls > self._max_calls:
            raise ValueError("reconciliation call plan exceeds the bounded safety limit")
        synthesized_by_id: dict[str, DerivedRecord] = {}
        target_record_ids = frozenset(record.record_id for record in record_values)
        input_records_by_id = {
            record.record_id: record for record in (*record_values, *context_values)
        }
        hints: list[ConceptHint] = []
        usage = CallUsage()
        call_count = 0

        def reconcile_batch(
            planned_batch: _ReconciliationBatch,
            prior_summaries: tuple[_ClusterSummary, ...] = (),
        ) -> tuple[tuple[DerivedRecord, ...], tuple[ConceptHint, ...]]:
            nonlocal call_count, usage
            batch = planned_batch.records
            batch_anchor_ids = tuple(
                dict.fromkeys(anchor for record in batch for anchor in record.anchors)
            )
            batch_revision_ids = tuple(
                dict.fromkeys(
                    dependency.identifier
                    for record in batch
                    for dependency in record.dependencies
                    if dependency.kind == "source_revision"
                )
            )
            if not set(batch_revision_ids) <= set(known_revisions):
                raise ValueError("synthesis record depends on an unavailable source revision")
            missing_spans = set(batch_anchor_ids).difference(anchor_spans)
            if missing_spans:
                raise ValueError("synthesis is missing a required bounded anchor span")
            summaries_by_id = {
                _cluster_summary_id(summary): summary for summary in prior_summaries
            }
            response = self._client.responses.create(
                model=self._provenance.model_version,
                store=False,
                instructions=(
                    f"Prompt version: {SYNTHESIS_PROMPT_VERSION}\n"
                    "Return only small typed cross-source relationship, procedure/sequence/hierarchy, "
                    "evolution, or conflict records plus explicit alias-aware concept hints. "
                    "Keep procedure prerequisites, conditions, and conditional branches structured. "
                    "Use only supplied record, anchor, and revision IDs. Preserve uncertainty; do not claim raw authority. "
                    "Prior cluster summaries may contain unchanged validated context: connect them when relevant, "
                    "but do not restate their lower-level conclusions. Every output must identify only the direct "
                    "input_record_ids and input_summary_ids it actually uses; context-only outputs are discarded."
                ),
                input=json.dumps(
                    {
                        "snapshot_id": snapshot_id,
                        "reconciliation_batch": {"kind": planned_batch.kind},
                        "records": [asdict(record) for record in batch],
                        "revision_ids": list(batch_revision_ids),
                        "sources": [
                            asdict(canonical_sources[revision_id])
                            for revision_id in batch_revision_ids
                        ],
                        "prior_cluster_summaries": [
                            _cluster_summary_payload(
                                summary,
                                target_record_ids=target_record_ids,
                                records_by_id=input_records_by_id | synthesized_by_id,
                            )
                            for summary in prior_summaries
                        ],
                        "supporting_spans": {
                            anchor_id: anchor_spans[anchor_id] for anchor_id in batch_anchor_ids
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": SYNTHESIS_SCHEMA_VERSION,
                        "schema": {
                            "type": "object",
                            "required": ["records"],
                            "properties": {
                                "records": {
                                    "type": "array",
                                    "maxItems": MAX_SYNTHESIS_RECORDS_PER_CALL,
                                    "items": {"type": "object"},
                                },
                                "concept_hints": {"type": "array", "items": {"type": "object"}},
                            },
                        },
                        "strict": False,
                    }
                },
            )
            output, hint_payloads = _synthesis_output(response)
            parsed_records = tuple(
                _synthesis_record(
                    payload,
                    snapshot_id=snapshot_id,
                    provenance=self._provenance,
                    records_by_id={record.record_id: record for record in batch},
                    summaries_by_id=summaries_by_id,
                    source_metadata=canonical_sources,
                )
                for payload in output
            )
            lineage_records_by_id = input_records_by_id | synthesized_by_id | {
                record.record_id: record for record in parsed_records
            }
            parsed_hints = _synthesis_hints(hint_payloads, parsed_records)
            batch_records = tuple(
                record for record in parsed_records
                if record_reaches_any(record, target_record_ids, lineage_records_by_id)
            )
            new_records = tuple(
                record for record in batch_records if record.record_id not in synthesized_by_id
            )
            synthesized_by_id.update((record.record_id, record) for record in new_records)
            hints.extend(
                hint for hint in parsed_hints
                if hint.record_id in {record.record_id for record in new_records}
            )
            usage = _sum_usage(usage, usage_from_response(response, pricing=self._pricing))
            call_count += 1
            if len(synthesized_by_id) > self._max_total_records:
                raise ValueError("batched synthesis exceeded the candidate output limit")
            return batch_records, tuple(
                hint for hint in parsed_hints
                if hint.record_id in {record.record_id for record in batch_records}
            ) if hint_payloads else ()

        summaries: list[_ClusterSummary] = []
        for planned_batch in primary_batches:
            batch_records, _batch_hints = reconcile_batch(planned_batch)
            summaries.append(_cluster_summary(planned_batch.records, batch_records))
        summaries.extend(
            _cluster_summary(planned_batch.records, ()) for planned_batch in context_batches
        )

        reduction_round = 0
        bridge_call_count = 0
        while len(summaries) > 1:
            reduction_round += 1
            reduced: list[_ClusterSummary] = []
            for start in range(0, len(summaries), self._max_records_per_call):
                children = tuple(summaries[start : start + self._max_records_per_call])
                if len(children) == 1:
                    reduced.append(children[0])
                    continue
                planned = _ReconciliationBatch(
                    tuple(child.representative for child in children),
                    f"hierarchical_reduction_{reduction_round}",
                )
                batch_records, _batch_hints = reconcile_batch(planned, children)
                reduced.append(_merged_cluster_summary(children, batch_records))
                bridge_call_count += 1
            summaries = reduced

        covered_record_ids = tuple(sorted(record.record_id for record in record_values))
        coverage = ReconciliationCoverage(
            len(record_values), covered_record_ids, len(primary_batches), bridge_call_count
        )
        if not coverage.complete or set(covered_record_ids) != {
            record.record_id for record in record_values
        }:
            raise ValueError("reconciliation coverage is incomplete")
        return SynthesisResult(
            tuple(synthesized_by_id.values()), self._provenance, usage, tuple(hints),
            call_count=call_count, coverage=coverage,
        )


def _synthesis_output(response: Any) -> tuple[list[object], list[object]]:
    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str):
        raise ValueError("synthesis response requires output_text")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise ValueError("synthesis response must be JSON") from error
    if (
        not isinstance(payload, dict)
        or set(payload) not in ({"records"}, {"records", "concept_hints"})
        or not isinstance(payload["records"], list)
        or not isinstance(payload.get("concept_hints", []), list)
    ):
        raise ValueError("synthesis response requires records and optional concept hints")
    if len(payload["records"]) > MAX_SYNTHESIS_RECORDS_PER_CALL:
        raise ValueError("too many synthesis records")
    return payload["records"], payload.get("concept_hints", [])


def _synthesis_record(
    payload: object,
    *,
    snapshot_id: str,
    provenance: CompilerProvenance,
    records_by_id: Mapping[str, DerivedRecord],
    summaries_by_id: Mapping[str, _ClusterSummary],
    source_metadata: dict[str, ReconciliationSource],
) -> DerivedRecord:
    if not isinstance(payload, dict) or not isinstance(payload.get("family"), str):
        raise ValueError("synthesis record must be a typed object")
    family = payload["family"]
    inputs = _required_reference_ids(
        payload, "input_record_ids", tuple(records_by_id)
    )
    summary_ids = _optional_reference_ids(
        payload, "input_summary_ids", tuple(summaries_by_id)
    )
    direct_records = tuple(records_by_id[record_id] for record_id in inputs)
    summary_records = tuple(dict.fromkeys(
        record
        for summary_id in summary_ids
        for record in summaries_by_id[summary_id].lineage_records
    ))
    lineage_records = tuple(dict.fromkeys((*direct_records, *summary_records)))
    lineage_record_ids = tuple(record.record_id for record in lineage_records)
    direct_anchor_ids = tuple(dict.fromkeys(
        anchor for record in direct_records for anchor in record.anchors
    ))
    summary_anchor_ids = tuple(dict.fromkeys(
        anchor for record in summary_records for anchor in record.anchors
    ))
    lineage_anchor_ids = tuple(dict.fromkeys(
        anchor for record in lineage_records for anchor in record.anchors
    ))
    direct_revision_ids = tuple(dict.fromkeys(
        dependency.identifier
        for record in direct_records
        for dependency in record.dependencies
        if dependency.kind == "source_revision"
    ))
    summary_revision_ids = tuple(dict.fromkeys(
        dependency.identifier
        for record in summary_records
        for dependency in record.dependencies
        if dependency.kind == "source_revision"
    ))
    lineage_revision_ids = tuple(dict.fromkeys(
        dependency.identifier
        for record in lineage_records
        for dependency in record.dependencies
        if dependency.kind == "source_revision"
    ))
    declared_anchors = _required_reference_ids(
        payload, "anchors", direct_anchor_ids
    )
    declared_sources = _required_reference_ids(
        payload, "source_revision_ids", direct_revision_ids
    )
    if (
        set(declared_anchors) != set(direct_anchor_ids)
        or set(declared_sources) != set(direct_revision_ids)
    ):
        raise ValueError("synthesis evidence references must exactly match explicit input lineage")
    anchors = tuple(dict.fromkeys((*declared_anchors, *summary_anchor_ids)))
    sources = tuple(dict.fromkeys((*declared_sources, *summary_revision_ids)))
    common = {
        "snapshot_id": snapshot_id,
        "anchors": anchors,
        "dependencies": tuple(
            [*(RecordDependency("source_revision", value) for value in sources),
             *(RecordDependency("derived_record", value) for value in lineage_record_ids)]
        ),
        "validation_state": "validated",
        "lifecycle_state": "active",
        "qualification": payload.get("qualification"),
        "evidence_state": "cross_source_synthesis",
        "compiler_provenance": provenance,
    }
    base_fields = {
        "family", "qualification", "anchors", "input_record_ids", "input_summary_ids",
        "source_revision_ids"
    }
    if family == "relationship":
        _allow_synthesis_fields(payload, base_fields | {"left", "relation", "right"})
        return Relationship.create(
            **common,
            left=payload.get("left"),
            relation=payload.get("relation"),
            right=payload.get("right"),
        )
    if family == "procedure_sequence_hierarchy":
        _allow_synthesis_fields(
            payload,
            base_fields | {"kind", "terms", "prerequisites", "conditions", "branches"},
        )
        return ProcedureSequenceHierarchy.create(
            **common,
            kind=payload.get("kind"),
            terms=_payload_texts(payload.get("terms")),
            prerequisites=_payload_texts(payload.get("prerequisites", []), allow_empty=True),
            conditions=_payload_texts(payload.get("conditions", []), allow_empty=True),
            branches=_payload_procedure_branches(payload.get("branches", [])),
        )
    if family == "evolution":
        fields = base_fields | {
            "subject", "previous", "current", "earlier_source_set", "later_source_set",
            "classification", "negative_evidence_state", "competing_anchors",
            "earlier_coverage_id", "later_coverage_id", "earlier_observed_years",
            "later_observed_years", "deprecation_evidence_anchors",
        }
        _allow_synthesis_fields(payload, fields)
        earlier = _required_reference_ids(
            payload, "earlier_source_set", lineage_revision_ids
        )
        later = _required_reference_ids(
            payload, "later_source_set", lineage_revision_ids
        )
        earlier_coverage = _source_coverage(earlier, source_metadata)
        later_coverage = _source_coverage(later, source_metadata)
        if (
            payload.get("classification") not in {"apparently_contradictory", "uncertain_chronology", "no_supported_classification"}
            and max(earlier_coverage[1]) > min(later_coverage[1])
        ):
            raise ValueError("evolution chronology conflicts with canonical source years")
        dependencies = tuple(dict.fromkeys(common["dependencies"] + tuple(
            RecordDependency("source_revision", value) for value in earlier + later
        )))
        return Evolution.create(
            **(common | {"dependencies": dependencies}),
            subject=payload.get("subject"),
            previous=payload.get("previous"),
            current=payload.get("current"),
            earlier_source_set=earlier,
            later_source_set=later,
            classification=payload.get("classification"),
            negative_evidence_state=payload.get("negative_evidence_state"),
            competing_anchors=_optional_reference_ids(
                payload, "competing_anchors", lineage_anchor_ids
            ),
            earlier_coverage_id=earlier_coverage[0],
            later_coverage_id=later_coverage[0],
            earlier_observed_years=earlier_coverage[1],
            later_observed_years=later_coverage[1],
            deprecation_evidence_anchors=_optional_reference_ids(
                payload, "deprecation_evidence_anchors", lineage_anchor_ids
            ),
        )
    if family == "conflict_unresolved":
        fields = base_fields | {
            "kind", "subject", "alternatives", "competing_record_ids", "reconciliation_state",
            "relevant_scopes", "conditions", "unresolved_questions",
        }
        _allow_synthesis_fields(payload, fields)
        competing = _required_reference_ids(
            payload, "competing_record_ids", lineage_record_ids
        )
        dependencies = tuple(dict.fromkeys(common["dependencies"] + tuple(
            RecordDependency("derived_record", value) for value in competing
        )))
        return ConflictUnresolved.create(
            **(common | {"dependencies": dependencies}),
            kind=payload.get("kind"),
            subject=payload.get("subject"),
            alternatives=_payload_texts(payload.get("alternatives")),
            competing_record_ids=competing,
            reconciliation_state=payload.get("reconciliation_state"),
            relevant_scopes=_payload_texts(payload.get("relevant_scopes")),
            conditions=_payload_texts(payload.get("conditions"), allow_empty=True),
            unresolved_questions=_payload_texts(payload.get("unresolved_questions"), allow_empty=True),
        )
    raise ValueError("synthesis returned an unsupported typed family")


def _canonical_reconciliation_sources(
    revisions: tuple[object, ...], sources: tuple[ReconciliationSource, ...]
) -> dict[str, ReconciliationSource]:
    revision_ids = {getattr(revision, "revision_id", None) for revision in revisions}
    if None in revision_ids or len(sources) != len(revision_ids):
        raise ValueError("synthesis requires canonical metadata for every revision")
    result: dict[str, ReconciliationSource] = {}
    for source in sources:
        if not isinstance(source, ReconciliationSource) or source.revision_id not in revision_ids:
            raise ValueError("synthesis source metadata does not match its revisions")
        if source.revision_id in result:
            raise ValueError("synthesis source metadata requires unique revisions")
        if (
            not all(
                isinstance(value, str) and value.strip()
                for value in (
                    source.collection_id,
                    source.source_id,
                    source.author,
                    source.course,
                    source.lesson_title,
                    source.original_filename,
                )
            )
            or isinstance(source.year, bool)
            or not isinstance(source.year, int)
            or source.year < 1
        ):
            raise ValueError("synthesis source metadata is incomplete")
        result[source.revision_id] = source
    if set(result) != revision_ids:
        raise ValueError("synthesis requires canonical metadata for every revision")
    return result


def _source_coverage(
    revision_ids: tuple[str, ...], sources: dict[str, ReconciliationSource]
) -> tuple[str, tuple[int, ...]]:
    values = tuple(sources[revision_id] for revision_id in sorted(revision_ids))
    payload = json.dumps(
        [asdict(value) for value in values], sort_keys=True, separators=(",", ":")
    )
    years = tuple(sorted({value.year for value in values}))
    return f"cov_{sha256(payload.encode()).hexdigest()}", years


def source_coverage(
    revision_ids: tuple[str, ...], sources: Sequence[ReconciliationSource]
) -> tuple[str, tuple[int, ...]]:
    """Return the deterministic coverage identity for a registry-grounded source set."""
    source_map = {source.revision_id: source for source in sources}
    if len(source_map) != len(tuple(sources)) or not set(revision_ids) <= set(source_map):
        raise ValueError("coverage source set is not present in canonical metadata")
    return _source_coverage(revision_ids, source_map)


def _synthesis_hints(
    payloads: list[object], records: tuple[DerivedRecord, ...]
) -> tuple[ConceptHint, ...]:
    if len(payloads) > MAX_SYNTHESIS_RECORDS_PER_CALL:
        raise ValueError("too many synthesis concept hints")
    result = []
    for payload in payloads:
        if not isinstance(payload, dict) or set(payload) != {
            "record_index", "label", "aliases", "scope", "role", "position"
        }:
            raise ValueError("synthesis concept hints must be typed")
        index = payload["record_index"]
        aliases = payload["aliases"]
        position = payload["position"]
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(records)
            or not isinstance(payload["label"], str)
            or not payload["label"].strip()
            or not isinstance(aliases, list)
            or len(aliases) > MAX_ALIASES
            or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
            or (payload["scope"] is not None and not isinstance(payload["scope"], str))
            or (payload["role"] is not None and not isinstance(payload["role"], str))
            or (position is not None and (not isinstance(position, int) or isinstance(position, bool) or position < 0))
        ):
            raise ValueError("synthesis concept hints are invalid")
        result.append(
            ConceptHint(
                records[index].record_id,
                payload["label"],
                tuple(aliases),
                payload["scope"],
                payload["role"],
                position,
            )
        )
    return tuple(result)


def _reconciliation_batches(
    records: tuple[DerivedRecord, ...],
    max_records: int,
    hints: tuple[ConceptHint, ...] = (),
    *,
    pack_components: bool = True,
) -> tuple[_ReconciliationBatch, ...]:
    """Build complete bounded primary coverage grouped by semantic affinity."""
    components = _affinity_components(records, hints)
    units: list[tuple[int, tuple[DerivedRecord, ...]]] = []
    for component_index, component in enumerate(components):
        units.extend(
            (component_index, component[start : start + max_records])
            for start in range(0, len(component), max_records)
        )
    if not pack_components:
        return tuple(_ReconciliationBatch(unit, "primary") for _component, unit in units)

    primary_records: list[tuple[DerivedRecord, ...]] = []
    primary_components: list[set[int]] = []
    current: list[DerivedRecord] = []
    current_components: set[int] = set()
    for component_index, unit in units:
        if current and len(current) + len(unit) > max_records:
            primary_records.append(tuple(current))
            primary_components.append(set(current_components))
            current = []
            current_components = set()
        current.extend(unit)
        current_components.add(component_index)
    if current:
        primary_records.append(tuple(current))
        primary_components.append(current_components)

    return tuple(_ReconciliationBatch(batch, "primary") for batch in primary_records)


def _hierarchical_call_count(primary_count: int, width: int) -> int:
    calls = primary_count
    pending = primary_count
    while pending > 1:
        full_groups, remainder = divmod(pending, width)
        calls += full_groups + int(remainder > 1)
        pending = full_groups + int(bool(remainder))
    return calls


def _cluster_summary(
    covered_records: tuple[DerivedRecord, ...], outputs: tuple[DerivedRecord, ...]
) -> _ClusterSummary:
    conclusions = _compact_conclusions(outputs or covered_records)
    return _ClusterSummary(
        covered_records[0], covered_records, outputs or covered_records, conclusions
    )


def _merged_cluster_summary(
    children: tuple[_ClusterSummary, ...], outputs: tuple[DerivedRecord, ...]
) -> _ClusterSummary:
    covered = tuple(dict.fromkeys(
        record for child in children for record in child.covered_records
    ))
    if outputs:
        selected_record_ids = {
            dependency.identifier
            for output in outputs
            for dependency in output.dependencies
            if dependency.kind == "derived_record"
        }
        selected_children = tuple(
            child
            for child in children
            if {record.record_id for record in child.lineage_records} <= selected_record_ids
        )
        conclusions = _bounded_unique_strings((
            *(_compact_conclusions(outputs)),
            *(conclusion for child in selected_children for conclusion in child.conclusions),
        ))
        lineage_records = outputs
    else:
        conclusions = _bounded_unique_strings(tuple(
            conclusion for child in children for conclusion in child.conclusions
        ))
        lineage_records = tuple(dict.fromkeys(
            record for child in children for record in child.lineage_records
        ))
    return _ClusterSummary(
        children[0].representative, covered, lineage_records, conclusions
    )


def _cluster_summary_id(summary: _ClusterSummary) -> str:
    payload = json.dumps(
        {
            "covered": sorted(record.record_id for record in summary.covered_records),
            "lineage": sorted(record.record_id for record in summary.lineage_records),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sum_{sha256(payload.encode()).hexdigest()}"


def _cluster_summary_payload(
    summary: _ClusterSummary,
    *,
    target_record_ids: frozenset[str],
    records_by_id: Mapping[str, DerivedRecord],
) -> dict[str, object]:
    covered_ids = tuple(sorted(record.record_id for record in summary.covered_records))
    return {
        "summary_id": _cluster_summary_id(summary),
        "lineage_role": (
            "target"
            if any(
                record_reaches_any(record, target_record_ids, records_by_id)
                for record in summary.lineage_records
            )
            else "context"
        ),
        "covered_record_count": len(covered_ids),
        "coverage_digest": sha256("\n".join(covered_ids).encode()).hexdigest(),
        "conclusions": list(summary.conclusions),
    }


def record_reaches_any(
    record: DerivedRecord,
    target_record_ids: frozenset[str] | set[str],
    records_by_id: Mapping[str, DerivedRecord],
    visiting: frozenset[str] = frozenset(),
) -> bool:
    """Return whether explicit derived-record lineage reaches any target record."""
    if record.record_id in target_record_ids:
        return True
    if record.record_id in visiting:
        return False
    next_visiting = visiting | {record.record_id}
    return any(
        dependency.kind == "derived_record"
        and dependency.identifier in records_by_id
        and record_reaches_any(
            records_by_id[dependency.identifier],
            target_record_ids,
            records_by_id,
            next_visiting,
        )
        for dependency in record.dependencies
    )


def _compact_conclusions(records: Sequence[DerivedRecord]) -> tuple[str, ...]:
    return _bounded_unique_strings(
        " | ".join(label for _role, _position, label in _record_occurrence_terms(record))
        for record in records
    )


def _bounded_unique_strings(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value[:280] for value in values if value.strip()))[:16]


def _affinity_components(
    records: tuple[DerivedRecord, ...], hints: tuple[ConceptHint, ...]
) -> tuple[tuple[DerivedRecord, ...], ...]:
    by_id = {record.record_id: record for record in records}
    keys = {record.record_id: _record_affinity_keys(record) for record in records}
    for hint in hints:
        keys[hint.record_id].update(
            _semantic_affinity_keys((hint.label, *hint.aliases))
        )
    parents = {record.record_id: record.record_id for record in records}

    def find(record_id: str) -> str:
        while parents[record_id] != record_id:
            parents[record_id] = parents[parents[record_id]]
            record_id = parents[record_id]
        return record_id

    def union(first: str, second: str) -> None:
        left, right = find(first), find(second)
        if left != right:
            parents[max(left, right)] = min(left, right)

    inverted: dict[str, list[str]] = {}
    for record_id, record_keys in keys.items():
        for key in record_keys:
            inverted.setdefault(key, []).append(record_id)
    for record_ids in inverted.values():
        for record_id in record_ids[1:]:
            union(record_ids[0], record_id)

    grouped: dict[str, list[DerivedRecord]] = {}
    for record_id in sorted(by_id):
        grouped.setdefault(find(record_id), []).append(by_id[record_id])
    return tuple(
        tuple(sorted(component, key=lambda record: record.record_id))
        for _root, component in sorted(grouped.items())
    )


_AFFINITY_STOPWORDS = frozenset(
    {"a", "an", "and", "bounded", "concept", "context", "distinct", "meaning", "record", "synthetic", "the", "topic"}
)


def _record_affinity_keys(record: DerivedRecord) -> set[str]:
    return _semantic_affinity_keys(
        tuple(label for _role, _position, label in _record_occurrence_terms(record))
    )


def _semantic_affinity_keys(labels: Sequence[str]) -> set[str]:
    result = set()
    for label in labels:
        normalized = _label_key(label)
        tokens = tuple(
            token for token in normalized.replace("-", " ").split()
            if len(token) > 2 and token not in _AFFINITY_STOPWORDS
        )
        if tokens:
            result.add(f"label:{normalized}")
            result.update(f"token:{token}" for token in tokens)
    return result


def _validate_reconciliation_hints(
    records: tuple[DerivedRecord, ...], hints: tuple[ConceptHint, ...]
) -> None:
    record_ids = {record.record_id for record in records}
    for hint in hints:
        if not isinstance(hint, ConceptHint) or hint.record_id not in record_ids:
            raise ValueError("reconciliation hints require candidate-owned records")
        _label_key(hint.label)
        _require_labels(hint.aliases, _label_key(hint.label))
        if hint.scope is not None:
            _scope_key(hint.scope)
        if hint.role is not None:
            _role_key(hint.role)
        _require_position(hint.position)


def _sum_usage(first: CallUsage, second: CallUsage) -> CallUsage:
    return CallUsage(
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        cost_usd=first.cost_usd + second.cost_usd,
        reasoning_tokens=first.reasoning_tokens + second.reasoning_tokens,
    )


def _required_reference_ids(
    payload: dict[str, object], field: str, allowed: tuple[str, ...]
) -> tuple[str, ...]:
    if field not in payload or not isinstance(payload[field], list) or not payload[field]:
        raise ValueError(f"synthesis {field} must be an explicit non-empty list")
    values = tuple(payload[field])
    if any(not isinstance(item, str) or not item for item in values):
        raise ValueError(f"synthesis {field} must contain unique non-empty IDs")
    if len(set(values)) != len(values):
        raise ValueError(f"synthesis {field} must contain unique non-empty IDs")
    if not set(values) <= set(allowed):
        raise ValueError(f"synthesis {field} contains an unsupported reference")
    return values


def _optional_reference_ids(
    payload: dict[str, object], field: str, allowed: tuple[str, ...]
) -> tuple[str, ...]:
    if field not in payload:
        return ()
    value = payload[field]
    if not isinstance(value, list):
        raise ValueError(f"synthesis {field} must be a list")
    if not value:
        return ()
    return _required_reference_ids(payload, field, allowed)


def _payload_texts(value: object, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("synthesis text values must be a list")
    values = tuple(value)
    if (not values and not allow_empty) or any(not isinstance(item, str) or not item for item in values):
        raise ValueError("synthesis text values must be non-empty text")
    return values


def _payload_years(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ValueError("synthesis years must be a list")
    return tuple(value)


def _payload_procedure_branches(value: object) -> tuple[ProcedureRecordBranch, ...]:
    if not isinstance(value, list) or len(value) > MAX_SYNTHESIS_RECORDS_PER_CALL:
        raise ValueError("synthesis procedure branches must be a bounded list")
    branches = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"condition", "steps"}:
            raise ValueError("synthesis procedure branches must be structured")
        condition = item["condition"]
        if not isinstance(condition, str) or not condition.strip():
            raise ValueError("synthesis procedure branch condition must be non-empty text")
        branches.append(
            ProcedureRecordBranch(
                condition,
                _payload_texts(item["steps"]),
            )
        )
    return tuple(branches)


def _allow_synthesis_fields(payload: dict[str, object], allowed: set[str]) -> None:
    if set(payload).difference(allowed):
        raise ValueError("synthesis record contains unsupported fields")


@dataclass(frozen=True)
class Concept:
    concept_id: str
    snapshot_id: str
    canonical_label: str
    aliases: tuple[str, ...]
    scope: str | None
    supporting_record_ids: tuple[str, ...]
    supporting_anchor_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        canonical_label: str,
        aliases: tuple[str, ...],
        scope: str | None,
        supporting_record_ids: tuple[str, ...],
        supporting_anchor_ids: tuple[str, ...],
    ) -> "Concept":
        label_key = _label_key(canonical_label)
        _require_bounded_text(snapshot_id, "snapshot_id", MAX_LABEL_LENGTH)
        _require_labels(aliases, label_key)
        if scope is not None:
            _require_bounded_text(scope, "scope", MAX_SCOPE_LENGTH)
        _require_identifiers(supporting_record_ids, "supporting record")
        _require_identifiers(supporting_anchor_ids, "supporting anchor")
        return cls(
            concept_id=_concept_id(snapshot_id, label_key, scope),
            snapshot_id=snapshot_id,
            canonical_label=canonical_label,
            aliases=aliases,
            scope=scope,
            supporting_record_ids=supporting_record_ids,
            supporting_anchor_ids=supporting_anchor_ids,
        )


@dataclass(frozen=True)
class ConceptHint:
    record_id: str
    label: str
    aliases: tuple[str, ...] = ()
    scope: str | None = None
    role: str | None = None
    position: int | None = None


@dataclass(frozen=True)
class ConceptOccurrence:
    record_id: str
    role: str
    position: int | None
    label_key: str
    scope: str | None
    concept_id: str


@dataclass(frozen=True)
class RelationshipSynthesis:
    synthesis_id: str
    snapshot_id: str
    source_record_id: str
    input_record_ids: tuple[str, ...]
    anchor_ids: tuple[str, ...]
    evidence_state: str
    left_concept_id: str
    relation: str
    right_concept_id: str
    justification: str
    left_scope: str | None
    right_scope: str | None


@dataclass(frozen=True)
class ProcedureStep:
    position: int
    concept_id: str


@dataclass(frozen=True)
class ProcedureBranch:
    condition: str
    step_concept_ids: tuple[str, ...]
    positions: tuple[int, ...] = ()
    condition_index: int | None = None


@dataclass(frozen=True)
class ProcedureSynthesis:
    synthesis_id: str
    snapshot_id: str
    source_record_id: str
    input_record_ids: tuple[str, ...]
    anchor_ids: tuple[str, ...]
    evidence_state: str
    steps: tuple[ProcedureStep, ...]
    prerequisite_concept_ids: tuple[str, ...]
    branches: tuple[ProcedureBranch, ...]
    conditions: tuple[str, ...]
    justification: str
    step_scopes: tuple[str | None, ...]


@dataclass(frozen=True)
class PublishedSynthesis:
    snapshot_id: str
    concepts: tuple[Concept, ...]
    relationships: tuple[RelationshipSynthesis, ...]
    procedures: tuple[ProcedureSynthesis, ...]


@dataclass(frozen=True)
class SynthesisCandidate:
    snapshot_id: str
    records: tuple[DerivedRecord, ...]
    concepts: tuple[Concept, ...]
    concept_occurrences: tuple[ConceptOccurrence, ...] = ()

    @classmethod
    def from_records(
        cls,
        *,
        snapshot_id: str,
        records: Sequence[DerivedRecord],
        hints: Sequence[ConceptHint] = (),
    ) -> "SynthesisCandidate":
        _require_bounded_text(snapshot_id, "snapshot_id", MAX_LABEL_LENGTH)
        valid_records = []
        for record in records:
            validate_record(record)
            if record.snapshot_id != snapshot_id:
                raise ValueError("record snapshot_id does not match candidate")
            if record.validation_state == "validated" and not is_legacy_record(record):
                valid_records.append(record)
        record_map = {record.record_id: record for record in valid_records}
        if len(record_map) != len(valid_records):
            raise ValueError("duplicate validated record")
        for record in valid_records:
            if isinstance(record, ConflictUnresolved):
                if not set(record.competing_record_ids) <= set(record_map):
                    raise ValueError("conflict requires valid competing record inputs")
                required_anchors = {
                    anchor for record_id in record.competing_record_ids for anchor in record_map[record_id].anchors
                }
                if not required_anchors <= set(record.anchors):
                    raise ValueError("conflict anchors must include competing record anchors")
        ordered_records = tuple(sorted(record_map.values(), key=lambda record: record.record_id))
        concepts, occurrences = _cluster_concepts(snapshot_id, ordered_records, hints)
        ordered_concepts = tuple(sorted(concepts, key=lambda concept: concept.concept_id))
        _validate_concepts(snapshot_id, ordered_concepts, record_map)
        _validate_concept_occurrences(occurrences, ordered_records, ordered_concepts)
        return cls(snapshot_id, ordered_records, ordered_concepts, occurrences)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(record.record_id for record in self.records)

    def concept_id_for(self, label: str, *, scope: str | None = None) -> str:
        requested_scope = _scope_key(scope)
        matches = [
            concept
            for concept in self.concepts
            if _label_key(label) in {_label_key(concept.canonical_label), *(_label_key(alias) for alias in concept.aliases)}
        ]
        scoped_matches = [
            concept
            for concept in matches
            if (scope is None and concept.scope is None)
            or (scope is not None and _scope_key(concept.scope) == requested_scope)
        ]
        if len(scoped_matches) == 1:
            return scoped_matches[0].concept_id
        if scope is None and matches:
            raise ValueError("scope is required for this concept")
        if len(scoped_matches) != 1:
            raise ValueError("unknown or ambiguous concept label")

    def concept_ids_for_record(self, record_id: str) -> tuple[str, ...]:
        """Return every explicit concept occurrence in semantic record order."""
        record = self._record(record_id)
        occurrences = {
            (item.role, item.position): item.concept_id
            for item in self.concept_occurrences
            if item.record_id == record_id
        }
        return tuple(
            dict.fromkeys(
                occurrences[(role, position)]
                for role, position, _label in _record_occurrence_terms(record)
            )
        )

    def primary_concept_id_for_record(self, record_id: str) -> str:
        concept_ids = self.concept_ids_for_record(record_id)
        if not concept_ids:
            raise ValueError("record has no concept occurrence")
        return concept_ids[0]

    def synthesize_relationship(
        self, record_id: str, *, left_scope: str | None = None, right_scope: str | None = None
    ) -> RelationshipSynthesis:
        record = self._record(record_id)
        if not isinstance(record, Relationship):
            raise ValueError("relationship synthesis requires a relationship record")
        inputs = self._supporting_records(record)
        left_concept_id = self._concept_id_for_occurrence(
            record.record_id, "left", None, record.left, scope=left_scope
        )
        right_concept_id = self._concept_id_for_occurrence(
            record.record_id, "right", None, record.right, scope=right_scope
        )
        justification = _justification(record.relation)
        synthesis = RelationshipSynthesis(
            synthesis_id=_synthesis_id(
                "relationship",
                self.snapshot_id,
                tuple(item.record_id for item in inputs),
                _anchors(inputs),
                (left_concept_id, record.relation, right_concept_id, left_scope, right_scope),
            ),
            snapshot_id=self.snapshot_id,
            source_record_id=record.record_id,
            input_record_ids=tuple(item.record_id for item in inputs),
            anchor_ids=_anchors(inputs),
            evidence_state=_evidence_state(inputs),
            left_concept_id=left_concept_id,
            relation=record.relation,
            right_concept_id=right_concept_id,
            justification=justification,
            left_scope=left_scope,
            right_scope=right_scope,
        )
        _validate_justification(synthesis.justification)
        return synthesis

    def synthesize_procedure(
        self,
        record_id: str,
        *,
        prerequisite_concept_ids: tuple[str, ...] = (),
        branches: tuple[ProcedureBranch, ...] = (),
        step_scopes: tuple[str | None, ...] = (),
    ) -> ProcedureSynthesis:
        record = self._record(record_id)
        if not isinstance(record, ProcedureSequenceHierarchy) or record.kind != "procedure":
            raise ValueError("procedure synthesis requires a procedure record")
        if not prerequisite_concept_ids and record.prerequisites:
            prerequisite_concept_ids = tuple(
                self._concept_id_for_occurrence(
                    record.record_id, "prerequisite", position, prerequisite, scope=None
                )
                for position, prerequisite in enumerate(record.prerequisites)
            )
        _require_concept_ids(prerequisite_concept_ids, self.concepts, allow_empty=True)
        if not isinstance(step_scopes, tuple) or (step_scopes and len(step_scopes) != len(record.terms)):
            raise ValueError("procedure step scopes must match ordered steps")
        step_scopes = step_scopes or (None,) * len(record.terms)
        conditions = self._procedure_conditions(record)
        if not branches and record.branches:
            flat_position = 0
            derived_branches = []
            for branch in record.branches:
                branch_concept_ids = []
                for step in branch.steps:
                    branch_concept_ids.append(
                        self._concept_id_for_occurrence(
                            record.record_id,
                            "branch_step",
                            flat_position,
                            step,
                            scope=None,
                        )
                    )
                    flat_position += 1
                derived_branches.append(
                    ProcedureBranch(branch.condition, tuple(branch_concept_ids))
                )
            branches = tuple(derived_branches)
        branches = _normalized_branches(branches, self.concepts, conditions)
        inputs = self._supporting_records(record)
        steps = tuple(
            ProcedureStep(
                position,
                self._concept_id_for_occurrence(record.record_id, "term", position, term, scope=step_scopes[position]),
            )
            for position, term in enumerate(record.terms)
        )
        justification = _justification("procedure")
        synthesis = ProcedureSynthesis(
            synthesis_id=_synthesis_id(
                "procedure",
                self.snapshot_id,
                tuple(item.record_id for item in inputs),
                _anchors(inputs),
                (
                    tuple(step.concept_id for step in steps),
                    prerequisite_concept_ids,
                    tuple(
                        (branch.condition, branch.step_concept_ids, branch.positions, branch.condition_index)
                        for branch in branches
                    ),
                    conditions,
                    step_scopes,
                ),
            ),
            snapshot_id=self.snapshot_id,
            source_record_id=record.record_id,
            input_record_ids=tuple(item.record_id for item in inputs),
            anchor_ids=_anchors(inputs),
            evidence_state=_evidence_state(inputs),
            steps=steps,
            prerequisite_concept_ids=prerequisite_concept_ids,
            branches=branches,
            conditions=conditions,
            justification=justification,
            step_scopes=step_scopes,
        )
        _validate_justification(synthesis.justification)
        return synthesis

    def publish(
        self,
        *,
        relationships: Sequence[RelationshipSynthesis],
        procedures: Sequence[ProcedureSynthesis],
    ) -> PublishedSynthesis:
        known_concept_ids = {concept.concept_id for concept in self.concepts}
        for relationship in relationships:
            if not isinstance(relationship, RelationshipSynthesis):
                raise ValueError("published relationships must be typed")
            if any(
                not isinstance(concept_id, str) or concept_id not in known_concept_ids
                for concept_id in (relationship.left_concept_id, relationship.right_concept_id)
            ):
                raise ValueError("published records require valid concept IDs")
            _validate_justification(relationship.justification)
            expected = self.synthesize_relationship(
                relationship.source_record_id,
                left_scope=relationship.left_scope,
                right_scope=relationship.right_scope,
            )
            if relationship != expected:
                raise ValueError("published relationship is not canonical")
        for procedure in procedures:
            if not isinstance(procedure, ProcedureSynthesis):
                raise ValueError("published procedures must be typed")
            _validate_justification(procedure.justification)
            _require_concept_ids(procedure.prerequisite_concept_ids, self.concepts, allow_empty=True)
            _require_ordered_concept_ids(tuple(step.concept_id for step in procedure.steps), self.concepts)
            source_record = self._record(procedure.source_record_id)
            if not isinstance(source_record, ProcedureSequenceHierarchy) or source_record.kind != "procedure":
                raise ValueError("procedure synthesis requires a procedure record")
            _normalized_branches(procedure.branches, self.concepts, self._procedure_conditions(source_record))
            expected = self.synthesize_procedure(
                procedure.source_record_id,
                prerequisite_concept_ids=procedure.prerequisite_concept_ids,
                branches=procedure.branches,
                step_scopes=procedure.step_scopes,
            )
            if procedure != expected:
                raise ValueError("published procedure is not canonical")
        if len({item.synthesis_id for item in relationships}) != len(relationships) or len(
            {item.synthesis_id for item in procedures}
        ) != len(procedures):
            raise ValueError("published synthesis IDs must be unique")
        return PublishedSynthesis(
            self.snapshot_id,
            self.concepts,
            tuple(sorted(relationships, key=lambda relationship: relationship.synthesis_id)),
            tuple(sorted(procedures, key=lambda procedure: procedure.synthesis_id)),
        )

    def _record(self, record_id: str) -> DerivedRecord:
        for record in self.records:
            if record.record_id == record_id:
                return record
        raise ValueError("unknown validated record")

    def _concept_id_for_occurrence(
        self, record_id: str, role: str, position: int | None, label: str, *, scope: str | None
    ) -> str:
        label_key = _label_key(label)
        role_key = _role_key(role)
        _require_position(position)
        matches = [
            occurrence
            for occurrence in self.concept_occurrences
            if occurrence.record_id == record_id
            and occurrence.role == role_key
            and occurrence.position == position
            and occurrence.label_key == label_key
        ]
        if len(matches) != 1:
            raise ValueError("record occurrence does not resolve to one concept")
        occurrence = matches[0]
        if _scope_key(scope) != _scope_key(occurrence.scope):
            if occurrence.scope is not None and scope is None:
                raise ValueError("scope is required for this record occurrence")
            raise ValueError("scope does not match this record occurrence")
        return occurrence.concept_id

    def _procedure_conditions(self, record: ProcedureSequenceHierarchy) -> tuple[str, ...]:
        conditions = tuple(dict.fromkeys((
            *record.conditions,
            *(facet.value for facet in record.facets if facet.name == "condition"),
            *(branch.condition for branch in record.branches),
        )))
        for condition in conditions:
            _require_condition(condition)
        return conditions

    def _supporting_records(self, record: DerivedRecord) -> tuple[DerivedRecord, ...]:
        record_map = {item.record_id: item for item in self.records}
        result: list[DerivedRecord] = []
        active: set[str] = set()

        def visit(current: DerivedRecord) -> None:
            if current.record_id in active:
                raise ValueError("derived record dependencies cannot cycle")
            if current in result:
                return
            active.add(current.record_id)
            result.append(current)
            for dependency in current.dependencies:
                if dependency.kind == "derived_record":
                    dependent = record_map.get(dependency.identifier)
                    if dependent is None:
                        raise ValueError("synthesis requires valid dependent records")
                    visit(dependent)
            active.remove(current.record_id)

        visit(record)
        return tuple(result)


def _validate_concepts(
    snapshot_id: str, concepts: tuple[Concept, ...], records: dict[str, DerivedRecord]
) -> None:
    concept_ids = set()
    labels = set()
    for concept in concepts:
        if concept.snapshot_id != snapshot_id:
            raise ValueError("concept snapshot_id does not match candidate")
        label_key = _label_key(concept.canonical_label)
        _require_labels(concept.aliases, label_key)
        if concept.scope is not None:
            _require_bounded_text(concept.scope, "scope", MAX_SCOPE_LENGTH)
        if concept.concept_id != _concept_id(snapshot_id, label_key, concept.scope):
            raise ValueError("concept identity is not canonical")
        if concept.concept_id in concept_ids:
            raise ValueError("duplicate concept")
        concept_ids.add(concept.concept_id)
        _require_identifiers(concept.supporting_record_ids, "supporting record")
        _require_identifiers(concept.supporting_anchor_ids, "supporting anchor")
        supporting_records = []
        for record_id in concept.supporting_record_ids:
            record = records.get(record_id)
            if record is None:
                raise ValueError("concept requires a valid supporting record")
            supporting_records.append(record)
        valid_anchors = {anchor_id for record in supporting_records for anchor_id in record.anchors}
        if not set(concept.supporting_anchor_ids) <= valid_anchors:
            raise ValueError("concept requires a valid supporting anchor")
        for label in (concept.canonical_label, *concept.aliases):
            key = (_label_key(label), _scope_key(concept.scope))
            if key in labels:
                raise ValueError("concept labels must resolve uniquely")
            labels.add(key)


def _validate_concept_occurrences(
    occurrences: tuple[ConceptOccurrence, ...], records: Sequence[DerivedRecord], concepts: Sequence[Concept]
) -> None:
    expected = {
        (record.record_id, role, position)
        for record in records
        for role, position, _ in _record_occurrence_terms(record)
    }
    occurrence_keys = {(occurrence.record_id, occurrence.role, occurrence.position) for occurrence in occurrences}
    if occurrence_keys != expected or len(occurrence_keys) != len(occurrences):
        raise ValueError("concept occurrences must cover validated record terms exactly once")
    records_by_id = {record.record_id: record for record in records}
    concepts_by_id = {concept.concept_id: concept for concept in concepts}
    for occurrence in occurrences:
        record = records_by_id[occurrence.record_id]
        _role_key(occurrence.role)
        _require_position(occurrence.position)
        concept = concepts_by_id.get(occurrence.concept_id)
        if concept is None or record.record_id not in concept.supporting_record_ids:
            raise ValueError("concept occurrence requires a valid supporting concept")
        if _scope_key(occurrence.scope) != _scope_key(concept.scope):
            raise ValueError("concept occurrence scope does not match concept")
        labels = {_label_key(concept.canonical_label), *(_label_key(alias) for alias in concept.aliases)}
        if occurrence.label_key not in labels:
            raise ValueError("concept occurrence label does not match concept")
        if (occurrence.role, occurrence.position, occurrence.label_key) not in {
            (role, position, _label_key(label)) for role, position, label in _record_occurrence_terms(record)
        }:
            raise ValueError("concept occurrence does not match a validated record term")


def _cluster_concepts(
    snapshot_id: str, records: Sequence[DerivedRecord], hints: Sequence[ConceptHint]
) -> tuple[tuple[Concept, ...], tuple[ConceptOccurrence, ...]]:
    occurrences: dict[
        tuple[str, str, int | None], tuple[DerivedRecord, str, int | None, str, str | None, tuple[str, ...]]
    ] = {}
    for record in records:
        for role, position, label in _record_occurrence_terms(record):
            occurrences[(record.record_id, role, position)] = (record, role, position, label, None, ())
    hinted = set()
    for hint in hints:
        if not isinstance(hint, ConceptHint):
            raise ValueError("concept hints must be typed")
        role = _role_key(hint.role) if hint.role is not None else None
        _require_position(hint.position)
        candidates = [
            key
            for key, (_, occurrence_role, position, label, _, _) in occurrences.items()
            if key[0] == hint.record_id
            and _label_key(label) == _label_key(hint.label)
            and (role is None or occurrence_role == role)
            and (hint.position is None or position == hint.position)
        ]
        if not candidates:
            raise ValueError("concept hints require a valid supporting record reference")
        if len(candidates) != 1:
            raise ValueError("concept hint is ambiguous; role or position is required")
        key = candidates[0]
        if key in hinted:
            raise ValueError("duplicate concept hint")
        hinted.add(key)
        record, occurrence_role, position, label, _, _ = occurrences[key]
        _require_labels(hint.aliases, _label_key(hint.label))
        if hint.scope is not None:
            _scope_key(hint.scope)
        occurrences[key] = (record, occurrence_role, position, label, hint.scope, hint.aliases)

    nodes: dict[tuple[str | None, str], list[tuple[DerivedRecord, str]]] = {}
    edges: dict[tuple[str | None, str], set[tuple[str | None, str]]] = {}
    display_labels: dict[tuple[str | None, str], str] = {}
    scope_values: dict[str | None, str | None] = {None: None}
    for record, _, _, label, scope, aliases in occurrences.values():
        scope_key = _scope_key(scope)
        scope_values.setdefault(scope_key, scope)
        node = (scope_key, _label_key(label))
        nodes.setdefault(node, []).append((record, label))
        edges.setdefault(node, set())
        display_labels.setdefault(node, label)
        for alias in aliases:
            alias_node = (scope_key, _label_key(alias))
            edges.setdefault(alias_node, set()).add(node)
            edges[node].add(alias_node)
            display_labels.setdefault(alias_node, alias)

    concepts = []
    concept_occurrences = []
    visited = set()
    for start in sorted(nodes, key=lambda node: ((node[0] or ""), node[1])):
        if start in visited:
            continue
        component = set()
        pending = [start]
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            pending.extend(edges[node])
        visited.update(component)
        labels = [item for node in component for item in nodes.get(node, ())]
        canonical_label = min((label for _, label in labels), key=lambda label: (_label_key(label), label))
        canonical_key = _label_key(canonical_label)
        alias_nodes = sorted(
            (node for node in component if node[1] != canonical_key),
            key=lambda node: (node[1], display_labels[node]),
        )
        aliases = tuple(display_labels[node] for node in alias_nodes)
        supporting_records = tuple(dict.fromkeys(record.record_id for record, _ in labels))
        supporting_anchors = tuple(dict.fromkeys(anchor for record, _ in labels for anchor in record.anchors))
        concept = Concept.create(
            snapshot_id=snapshot_id,
            canonical_label=canonical_label,
            aliases=aliases,
            scope=scope_values[start[0]],
            supporting_record_ids=supporting_records,
            supporting_anchor_ids=supporting_anchors,
        )
        concepts.append(concept)
        for (record_id, role, position), (_, _, _, label, scope, _) in occurrences.items():
            label_key = _label_key(label)
            if (_scope_key(scope), label_key) in component:
                concept_occurrences.append(ConceptOccurrence(record_id, role, position, label_key, scope, concept.concept_id))
    return (
        tuple(concepts),
        tuple(
            sorted(
                concept_occurrences,
                key=lambda occurrence: (occurrence.record_id, occurrence.role, occurrence.position or -1),
            )
        ),
    )


def _record_occurrence_terms(record: DerivedRecord) -> tuple[tuple[str, int | None, str], ...]:
    if isinstance(record, Claim):
        return ("subject", None, record.subject), ("object", None, record.object)
    if isinstance(record, Relationship):
        return ("left", None, record.left), ("right", None, record.right)
    if isinstance(record, ProcedureSequenceHierarchy):
        branch_steps = tuple(step for branch in record.branches for step in branch.steps)
        return (
            *(('term', position, term) for position, term in enumerate(record.terms)),
            *(('prerequisite', position, term) for position, term in enumerate(record.prerequisites)),
            *(('branch_step', position, term) for position, term in enumerate(branch_steps)),
        )
    if isinstance(record, Evolution):
        return (
            ("subject", None, record.subject),
            ("previous", None, record.previous),
            ("current", None, record.current),
        )
    if isinstance(record, ConflictUnresolved):
        return (
            ("subject", None, record.subject),
            *(("alternative", position, term) for position, term in enumerate(record.alternatives)),
        )
    raise ValueError("unknown validated record")


def _anchors(records: Sequence[DerivedRecord]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(anchor for record in records for anchor in record.anchors))


def _evidence_state(records: Sequence[DerivedRecord]) -> str:
    return "cross_source_synthesis" if any(record.evidence_state == "cross_source_synthesis" for record in records) else "raw_taught"


def _justification(kind: str) -> str:
    return f"{kind} is supported by validated input records."


def _synthesis_id(
    kind: str,
    snapshot_id: str,
    input_record_ids: tuple[str, ...],
    anchor_ids: tuple[str, ...],
    structure: object,
) -> str:
    value = json.dumps(
        (kind, snapshot_id, input_record_ids, anchor_ids, structure), sort_keys=True, separators=(",", ":")
    )
    return f"syn_{sha256(value.encode()).hexdigest()}"


def _require_concept_ids(
    concept_ids: tuple[str, ...], concepts: Sequence[Concept], *, allow_empty: bool = False
) -> None:
    if not isinstance(concept_ids, tuple) or (not allow_empty and not concept_ids) or len(set(concept_ids)) != len(concept_ids):
        raise ValueError("concept IDs must be a unique tuple")
    known_concept_ids = {concept.concept_id for concept in concepts}
    if any(not isinstance(concept_id, str) or concept_id not in known_concept_ids for concept_id in concept_ids):
        raise ValueError("published records require valid concept IDs")


def _require_ordered_concept_ids(concept_ids: tuple[str, ...], concepts: Sequence[Concept]) -> None:
    if not isinstance(concept_ids, tuple) or not concept_ids:
        raise ValueError("ordered concept IDs must be a non-empty tuple")
    known_concept_ids = {concept.concept_id for concept in concepts}
    if any(not isinstance(concept_id, str) or concept_id not in known_concept_ids for concept_id in concept_ids):
        raise ValueError("published records require valid concept IDs")


def _normalized_branches(
    branches: tuple[ProcedureBranch, ...], concepts: Sequence[Concept], allowed_conditions: tuple[str, ...]
) -> tuple[ProcedureBranch, ...]:
    if not isinstance(branches, tuple):
        raise ValueError("procedure branches must be a tuple")
    normalized = []
    for branch in branches:
        if not isinstance(branch, ProcedureBranch):
            raise ValueError("procedure branches must be structured")
        _require_condition(branch.condition)
        condition_index = branch.condition_index
        if condition_index is None:
            matches = [
                index for index, condition in enumerate(allowed_conditions) if condition == branch.condition
            ]
            if len(matches) != 1:
                raise ValueError("procedure branch condition requires structured provenance")
            condition_index = matches[0]
        if (
            isinstance(condition_index, bool)
            or not isinstance(condition_index, int)
            or condition_index < 0
            or condition_index >= len(allowed_conditions)
            or branch.condition != allowed_conditions[condition_index]
        ):
            raise ValueError("procedure branch condition requires structured provenance")
        _require_ordered_concept_ids(branch.step_concept_ids, concepts)
        positions = branch.positions or tuple(range(len(branch.step_concept_ids)))
        if not isinstance(positions, tuple) or positions != tuple(range(len(branch.step_concept_ids))):
            raise ValueError("procedure branch positions must be ordered")
        normalized.append(ProcedureBranch(branch.condition, branch.step_concept_ids, positions, condition_index))
    return tuple(normalized)


def _require_condition(condition: object) -> None:
    if not isinstance(condition, str) or not condition.strip():
        raise ValueError("procedure condition must be non-empty text")
    if len(condition) > MAX_CONDITION_LENGTH:
        raise ValueError("raw text dump is not allowed")
    _reject_private_text(condition, "procedure condition")


def _validate_justification(justification: object) -> None:
    if not isinstance(justification, str) or not justification.strip() or len(justification) > MAX_JUSTIFICATION_LENGTH:
        raise ValueError("synthesis justification must be concise")
    _reject_private_text(justification, "synthesis justification")


def _reject_private_text(value: str, label: str) -> None:
    reject_private_or_raw_text(value, label)


def _concept_id(snapshot_id: str, label_key: str, scope: str | None) -> str:
    scope_key = _scope_key(scope) or ""
    return f"con_{sha256(f'{snapshot_id}\0{label_key}\0{scope_key}'.encode()).hexdigest()}"


def _label_key(value: str) -> str:
    _require_bounded_text(value, "concept label", MAX_LABEL_LENGTH)
    return " ".join(value.split()).casefold()


def _scope_key(scope: str | None) -> str | None:
    if scope is None:
        return None
    _require_bounded_text(scope, "scope", MAX_SCOPE_LENGTH)
    return " ".join(scope.split()).casefold()


def _role_key(role: str) -> str:
    _require_bounded_text(role, "concept occurrence role", MAX_LABEL_LENGTH)
    return " ".join(role.split()).casefold()


def _require_position(position: object) -> None:
    if position is not None and (isinstance(position, bool) or not isinstance(position, int) or position < 0):
        raise ValueError("concept occurrence position must be a non-negative integer")


def _require_labels(aliases: tuple[str, ...], canonical_key: str) -> None:
    if not isinstance(aliases, tuple) or len(aliases) > MAX_ALIASES:
        raise ValueError("aliases must be a bounded tuple")
    alias_keys = tuple(_label_key(alias) for alias in aliases)
    if canonical_key in alias_keys or len(set(alias_keys)) != len(alias_keys):
        raise ValueError("aliases must resolve uniquely")


def _require_identifiers(values: tuple[str, ...], label: str) -> None:
    if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
        raise ValueError(f"{label} IDs must be non-empty and unique")
    for value in values:
        _require_bounded_text(value, f"{label} ID", MAX_LABEL_LENGTH)


def _require_bounded_text(value: object, label: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds its maximum length")
