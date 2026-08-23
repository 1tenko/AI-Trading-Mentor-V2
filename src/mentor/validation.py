"""Deterministic anchors plus separately prompted semantic claim validation."""

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence

from mentor.anchors import SourceAnchor, resolve_anchor_span
from mentor.compiler_prompts import semantic_validation_request
from mentor.derived_records import Claim
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
    source_extracted: Claim | None


class SemanticValidator:
    """Validate a candidate through a caller-owned Responses-compatible client."""

    def __init__(self, client: Any, *, model: str = "synthetic-validator"):
        if model == "gpt-5.6-sol":
            raise ValueError("semantic validation requires an injected mock Responses client")
        self._client = client
        self._model = model

    def validate(
        self,
        *,
        candidate: Claim,
        revision: SourceRevision,
        transcript: str,
        anchors: Mapping[str, SourceAnchor],
    ) -> ValidationResult:
        if not isinstance(candidate, Claim):
            raise ValueError("semantic validation applies only to Claim candidates")
        spans = _validated_spans(candidate, revision, transcript, anchors)
        response = self._client.responses.create(
            **semantic_validation_request(
                claim={"subject": candidate.subject, "predicate": candidate.predicate, "object": candidate.object},
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
        )


def can_publish_source_extracted(results: Sequence[ValidationResult]) -> bool:
    return all(result.source_extracted is not None for result in results)


def _validated_spans(
    candidate: Claim, revision: SourceRevision, transcript: str, anchors: Mapping[str, SourceAnchor]
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


def _validated_replacement(candidate: Claim) -> Claim:
    return Claim.create(
        snapshot_id=candidate.snapshot_id,
        anchors=candidate.anchors,
        dependencies=candidate.dependencies,
        validation_state="validated",
        lifecycle_state=candidate.lifecycle_state,
        qualification=candidate.qualification,
        subject=candidate.subject,
        predicate=candidate.predicate,
        object=candidate.object,
        derived_kind=candidate.derived_kind,
        evidence_state=candidate.evidence_state,
        compiler_provenance=candidate.compiler_provenance,
        facets=candidate.facets,
    )
