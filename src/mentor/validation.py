"""Deterministic anchors plus separately prompted semantic claim validation."""

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence

from mentor.anchors import SourceAnchor, resolve_anchor_span
from mentor.compiler_prompts import semantic_validation_request
from mentor.compilation import CallUsage, TokenPricing, usage_from_response
from mentor.derived_records import Claim, DerivedRecord, ProcedureSequenceHierarchy, Relationship
from mentor.knowledge import SourceRevision


SEMANTIC_OUTCOMES = frozenset(
    {"affirmatively_supported", "partially_supported", "unsupported", "ambiguous", "needs_broader_context"}
)


@dataclass(frozen=True)
class ValidationResult:
    candidate_record_id: str
    snapshot_id: str
    outcome: str
    audit: str
    anchor_ids: tuple[str, ...]
    source_extracted: DerivedRecord | None
    usage: CallUsage = CallUsage()


class SemanticValidator:
    """Validate a candidate through a caller-owned Responses-compatible client."""

    def __init__(
        self, client: Any, *, model: str = "synthetic-validator", live_mode: bool = False,
        pricing: TokenPricing | None = None,
    ):
        if model == "gpt-5.6-sol" and not live_mode:
            raise ValueError("GPT-5.6 Sol requires explicit live mode; use an injected mock otherwise")
        if model == "gpt-5.6-sol" and pricing is None:
            raise ValueError("GPT-5.6 Sol live validation requires caller-supplied pricing")
        self._client = client
        self._model = model
        self._pricing = pricing

    def validate(
        self,
        *,
        candidate: DerivedRecord,
        revision: SourceRevision,
        transcript: str,
        anchors: Mapping[str, SourceAnchor],
    ) -> ValidationResult:
        if not isinstance(candidate, (Claim, Relationship, ProcedureSequenceHierarchy)):
            raise ValueError("semantic validation applies only to source-extracted candidates")
        spans = _validated_spans(candidate, revision, transcript, anchors)
        response = self._client.responses.create(
            **semantic_validation_request(
                record=_validation_payload(candidate),
                spans=spans,
                model=self._model,
            )
        )
        outcome, audit = _semantic_response(response)
        return ValidationResult(
            candidate.record_id,
            candidate.snapshot_id,
            outcome,
            audit,
            tuple(anchor_id for anchor_id, _ in spans),
            _validated_replacement(candidate) if outcome == "affirmatively_supported" else None,
            usage_from_response(response, pricing=self._pricing),
        )


def can_publish_source_extracted(results: Sequence[ValidationResult]) -> bool:
    return all(result.source_extracted is not None for result in results)


def _validated_spans(
    candidate: DerivedRecord, revision: SourceRevision, transcript: str, anchors: Mapping[str, SourceAnchor]
) -> tuple[tuple[str, str], ...]:
    if candidate.validation_state != "pending" or candidate.lifecycle_state != "candidate":
        raise ValueError("semantic validation requires a pending candidate claim")
    spans = []
    for anchor_id in candidate.anchors:
        anchor = anchors.get(anchor_id)
        if anchor is None:
            raise ValueError("candidate anchor is unavailable")
        spans.append((anchor_id, resolve_anchor_span(anchor, revision, transcript)))
    return tuple(spans)


def _semantic_response(response: Any) -> tuple[str, str]:
    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str):
        raise ValueError("semantic validation response requires output_text")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise ValueError("semantic validation response must be JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"outcome", "audit"}:
        raise ValueError("semantic validation response requires outcome and audit")
    outcome, audit = payload["outcome"], payload["audit"]
    if outcome not in SEMANTIC_OUTCOMES:
        raise ValueError("invalid semantic validation outcome")
    if not isinstance(audit, str) or not audit.strip() or len(audit) > 280:
        raise ValueError("semantic validation audit must be concise text")
    return outcome, audit


def _validated_replacement(candidate: DerivedRecord) -> DerivedRecord:
    common = {
        "snapshot_id": candidate.snapshot_id,
        "anchors": candidate.anchors,
        "dependencies": candidate.dependencies,
        "validation_state": "validated",
        "lifecycle_state": "active",
        "qualification": candidate.qualification,
        "derived_kind": candidate.derived_kind,
        "evidence_state": candidate.evidence_state,
        "compiler_provenance": candidate.compiler_provenance,
        "facets": candidate.facets,
    }
    if isinstance(candidate, Claim):
        return Claim.create(
            **common, semantic_subtype=candidate.semantic_subtype,
            subject=candidate.subject, predicate=candidate.predicate, object=candidate.object,
        )
    if isinstance(candidate, Relationship):
        return Relationship.create(**common, left=candidate.left, relation=candidate.relation, right=candidate.right)
    if isinstance(candidate, ProcedureSequenceHierarchy):
        return ProcedureSequenceHierarchy.create(**common, kind=candidate.kind, terms=candidate.terms)
    raise ValueError("unsupported source-extracted record family")


def _validation_payload(candidate: DerivedRecord) -> dict[str, object]:
    common: dict[str, object] = {
        "family": candidate.family,
        "semantic_subtype": candidate.semantic_subtype,
    }
    if isinstance(candidate, Claim):
        return common | {"subject": candidate.subject, "predicate": candidate.predicate, "object": candidate.object}
    if isinstance(candidate, Relationship):
        return common | {"left": candidate.left, "relation": candidate.relation, "right": candidate.right}
    if isinstance(candidate, ProcedureSequenceHierarchy):
        return common | {"kind": candidate.kind, "terms": candidate.terms}
    raise ValueError("unsupported source-extracted record family")
