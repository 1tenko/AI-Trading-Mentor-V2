"""Injected-client extraction of unvalidated, per-source candidate records."""

from dataclasses import dataclass
import json
from typing import Any, Mapping

from mentor.compiler_prompts import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SCHEMA_VERSION,
    MAX_ANCHOR_ID_LENGTH,
    MAX_ANCHORS_PER_CANDIDATE,
    MAX_CANDIDATES_PER_SOURCE,
    extraction_request,
)
from mentor.compilation import CallUsage, TokenPricing, usage_from_response
from mentor.derived_records import (
    Claim,
    CompilerProvenance,
    DerivedRecord,
    ProcedureRecordBranch,
    ProcedureSequenceHierarchy,
    RecordDependency,
    Relationship,
)
from mentor.knowledge import SourceRevision
from mentor.synthesis import ConceptHint


SOL_MODEL = "gpt-5.6-sol"
_SELF_VALIDATION_FIELDS = frozenset(
    {
        "approved",
        "deterministic_validation",
        "lifecycle_state",
        "semantic_validation",
        "validated",
        "validation",
        "validation_state",
    }
)


@dataclass(frozen=True)
class ExtractionResult:
    revision_id: str
    provenance: CompilerProvenance
    candidates: tuple[DerivedRecord, ...]
    usage: CallUsage = CallUsage()
    hints: tuple[ConceptHint, ...] = ()


class SourceExtractor:
    """Extract candidates through a caller-owned Responses-compatible client."""

    def __init__(
        self, client: Any, *, model: str = "synthetic-compiler", live_mode: bool = False,
        pricing: TokenPricing | None = None,
    ):
        if model == SOL_MODEL and not live_mode:
            raise ValueError("GPT-5.6 Sol requires explicit live mode")
        if model == SOL_MODEL and pricing is None:
            raise ValueError("GPT-5.6 Sol live compilation requires caller-supplied pricing")
        self._client = client
        self._pricing = pricing
        self._live_mode = live_mode
        self._provenance = CompilerProvenance(model, EXTRACTION_PROMPT_VERSION, EXTRACTION_SCHEMA_VERSION)
        self.preflight_live_pricing()

    def preflight_live_pricing(self) -> None:
        if self._provenance.model_version == SOL_MODEL:
            if not self._live_mode or self._pricing is None:
                raise ValueError("GPT-5.6 Sol live extraction requires complete caller-supplied pricing")
            self._pricing.require_complete("extraction")

    def extract(
        self,
        *,
        revision: SourceRevision,
        snapshot_id: str,
        transcript: str,
        anchor_spans: Mapping[str, str] | None = None,
    ) -> ExtractionResult:
        response = self._client.responses.create(
            **extraction_request(
                revision=revision,
                transcript=transcript,
                model=self._provenance.model_version,
                anchor_spans=anchor_spans,
            )
        )
        candidates, hints = _parse_candidates(
            _response_output_text(response),
            revision=revision,
            snapshot_id=snapshot_id,
            provenance=self._provenance,
        )
        return ExtractionResult(
            revision.revision_id,
            self._provenance,
            candidates,
            usage_from_response(response, pricing=self._pricing),
            hints,
        )


def _response_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str):
        raise ValueError("extraction response requires output_text")
    return output_text


def _parse_candidates(
    output_text: str, *, revision: SourceRevision, snapshot_id: str, provenance: CompilerProvenance
) -> tuple[tuple[DerivedRecord, ...], tuple[ConceptHint, ...]]:
    try:
        response = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise ValueError("extraction response must be JSON") from error
    if not isinstance(response, dict) or set(response) != {"candidates"} or not isinstance(response["candidates"], list):
        raise ValueError("extraction response requires only a candidates list")
    if len(response["candidates"]) > MAX_CANDIDATES_PER_SOURCE:
        raise ValueError("too many source candidates")
    records = []
    hints = []
    for payload in response["candidates"]:
        record = _candidate_from_payload(
            payload, revision=revision, snapshot_id=snapshot_id, provenance=provenance
        )
        records.append(record)
        hints.extend(_concept_hints(payload.get("concept_hints", []), record.record_id))
    return tuple(records), tuple(hints)


def _candidate_from_payload(
    payload: object, *, revision: SourceRevision, snapshot_id: str, provenance: CompilerProvenance
) -> DerivedRecord:
    if not isinstance(payload, dict):
        raise ValueError("candidate must be an object")
    attempted_validation = _SELF_VALIDATION_FIELDS.intersection(payload)
    if attempted_validation:
        raise ValueError("candidate self-validation is forbidden")
    family = payload.get("family")
    if not isinstance(family, str):
        raise ValueError("unknown derived record family")
    common = {
        "snapshot_id": snapshot_id,
        "anchors": _anchors(payload.get("anchors")),
        "dependencies": (RecordDependency("source_revision", revision.revision_id),),
        "validation_state": "pending",
        "lifecycle_state": "candidate",
        "qualification": payload.get("qualification"),
        "compiler_provenance": provenance,
    }
    base_fields = {"family", "anchors", "qualification", "concept_hints"}
    if family == "claim":
        _allow_fields(
            payload,
            base_fields | {"subject", "predicate", "object", "semantic_subtype"},
        )
        return Claim.create(
            **common,
            subject=payload.get("subject"),
            predicate=payload.get("predicate"),
            object=payload.get("object"),
            semantic_subtype=payload.get("semantic_subtype", "statement"),
            derived_kind="source_extracted_claim",
            evidence_state="raw_taught",
        )
    if family == "relationship":
        _allow_fields(payload, base_fields | {"left", "relation", "right"})
        return Relationship.create(
            **common,
            left=payload.get("left"),
            relation=payload.get("relation"),
            right=payload.get("right"),
        )
    if family == "procedure_sequence_hierarchy":
        _allow_fields(
            payload,
            base_fields | {"kind", "terms", "prerequisites", "conditions", "branches"},
        )
        terms = payload.get("terms")
        if not isinstance(terms, list):
            raise ValueError("procedure terms must be an ordered list")
        return ProcedureSequenceHierarchy.create(
            **common,
            kind=payload.get("kind"),
            terms=tuple(terms),
            prerequisites=_procedure_texts(payload.get("prerequisites", []), "prerequisites"),
            conditions=_procedure_texts(payload.get("conditions", []), "conditions"),
            branches=_procedure_branches(payload.get("branches", [])),
        )
    raise ValueError("unknown derived record family")


def _concept_hints(payload: object, record_id: str) -> tuple[ConceptHint, ...]:
    if not isinstance(payload, list) or len(payload) > 8:
        raise ValueError("candidate concept hints must be a bounded list")
    result = []
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"label", "aliases", "scope", "role", "position"}:
            raise ValueError("candidate concept hints must be typed")
        aliases = item["aliases"]
        position = item["position"]
        if (
            not isinstance(item["label"], str)
            or not item["label"].strip()
            or not isinstance(aliases, list)
            or len(aliases) > 8
            or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
            or (item["scope"] is not None and not isinstance(item["scope"], str))
            or (item["role"] is not None and not isinstance(item["role"], str))
            or (position is not None and (not isinstance(position, int) or isinstance(position, bool) or position < 0))
        ):
            raise ValueError("candidate concept hints are invalid")
        result.append(
            ConceptHint(
                record_id,
                item["label"],
                tuple(aliases),
                item["scope"],
                item["role"],
                position,
            )
        )
    return tuple(result)


def _allow_fields(payload: dict[str, object], allowed: set[str]) -> None:
    if set(payload).difference(allowed):
        raise ValueError("candidate contains unsupported fields")


def _procedure_texts(value: object, label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError(f"procedure {label} must be a bounded ordered list")
    if (not allow_empty and not value) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"procedure {label} must contain non-empty text")
    return tuple(value)


def _procedure_branches(value: object) -> tuple[ProcedureRecordBranch, ...]:
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError("procedure branches must be a bounded ordered list")
    branches = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"condition", "steps"}:
            raise ValueError("procedure branches must be structured")
        condition = item["condition"]
        if not isinstance(condition, str) or not condition.strip():
            raise ValueError("procedure branch condition must be non-empty text")
        branches.append(
            ProcedureRecordBranch(
                condition,
                _procedure_texts(item["steps"], "branch steps", allow_empty=False),
            )
        )
    return tuple(branches)


def _anchors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    if len(value) > MAX_ANCHORS_PER_CANDIDATE:
        raise ValueError("too many candidate anchors")
    if any(not isinstance(anchor, str) or not anchor or len(anchor) > MAX_ANCHOR_ID_LENGTH for anchor in value):
        raise ValueError("invalid candidate anchor")
    return tuple(value)
