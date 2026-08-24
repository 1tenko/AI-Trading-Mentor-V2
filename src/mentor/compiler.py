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
    validate_record,
)
from mentor.knowledge import SourceRevision
from mentor.synthesis import ConceptHint, concept_hint_from_record_selector, validate_record_concept_terms


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


@dataclass(frozen=True)
class _InlineHint:
    aliases: tuple[str, ...]
    scope: str | None
    role: str
    position: int | None


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

    @property
    def provenance(self) -> CompilerProvenance:
        return self._provenance

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
        record, record_hints = _candidate_from_payload(
            payload, revision=revision, snapshot_id=snapshot_id, provenance=provenance
        )
        records.append(record)
        validate_record(record)
        validate_record_concept_terms(record)
        hints.extend(_inline_concept_hints(record, record_hints))
    return tuple(records), tuple(hints)


def _candidate_from_payload(
    payload: object, *, revision: SourceRevision, snapshot_id: str, provenance: CompilerProvenance
) -> tuple[DerivedRecord, tuple[_InlineHint, ...]]:
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
    base_fields = {"family", "anchors", "qualification"}
    if family == "claim":
        _allow_fields(
            payload,
            base_fields | {"subject", "predicate", "object", "semantic_subtype"},
        )
        subject, subject_hint = _inline_occurrence(payload.get("subject"), "subject")
        object_, object_hint = _inline_occurrence(payload.get("object"), "object")
        return Claim.create(
            **common,
            subject=subject,
            predicate=payload.get("predicate"),
            object=object_,
            semantic_subtype=payload.get("semantic_subtype", "statement"),
            derived_kind="source_extracted_claim",
            evidence_state="raw_taught",
        ), (subject_hint, object_hint)
    if family == "relationship":
        _allow_fields(payload, base_fields | {"left", "relation", "right"})
        left, left_hint = _inline_occurrence(payload.get("left"), "left")
        right, right_hint = _inline_occurrence(payload.get("right"), "right")
        return Relationship.create(
            **common,
            left=left,
            relation=payload.get("relation"),
            right=right,
        ), (left_hint, right_hint)
    if family == "procedure_sequence_hierarchy":
        _allow_fields(
            payload,
            base_fields | {"kind", "terms", "prerequisites", "conditions", "branches"},
        )
        terms, term_hints = _inline_occurrences(payload.get("terms"), "terms", "term", allow_empty=False)
        prerequisites, prerequisite_hints = _inline_occurrences(
            payload.get("prerequisites", []), "prerequisites", "prerequisite"
        )
        branches, branch_hints = _procedure_branches(payload.get("branches", []))
        return ProcedureSequenceHierarchy.create(
            **common,
            kind=payload.get("kind"),
            terms=terms,
            prerequisites=prerequisites,
            conditions=_procedure_texts(payload.get("conditions", []), "conditions"),
            branches=branches,
        ), (*term_hints, *prerequisite_hints, *branch_hints)
    raise ValueError("unknown derived record family")


def _inline_occurrence(value: object, role: str, position: int | None = None) -> tuple[str, _InlineHint]:
    if not isinstance(value, dict) or set(value) != {"text", "aliases", "scope"}:
        raise ValueError("inline concept occurrence must be typed")
    text, aliases, scope = value["text"], value["aliases"], value["scope"]
    if (
        not isinstance(text, str)
        or not text.strip()
        or not isinstance(aliases, list)
        or len(aliases) > 8
        or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
        or (scope is not None and not isinstance(scope, str))
    ):
        raise ValueError("inline concept occurrence is invalid")
    return text, _InlineHint(tuple(aliases), scope, role, position)


def _inline_occurrences(
    value: object, label: str, role: str, *, allow_empty: bool = True, start_position: int = 0,
) -> tuple[tuple[str, ...], tuple[_InlineHint, ...]]:
    if not isinstance(value, list) or len(value) > 8 or (not allow_empty and not value):
        raise ValueError(f"procedure {label} must be a bounded ordered list")
    occurrences = tuple(
        _inline_occurrence(item, role, start_position + position)
        for position, item in enumerate(value)
    )
    return tuple(item[0] for item in occurrences), tuple(item[1] for item in occurrences)


def _inline_concept_hints(record: DerivedRecord, hints: tuple[_InlineHint, ...]) -> tuple[ConceptHint, ...]:
    return tuple(
        concept_hint_from_record_selector(
            record,
            aliases=hint.aliases,
            scope=hint.scope,
            role=hint.role,
            position=hint.position,
        )
        for hint in hints
        if hint.aliases or hint.scope is not None
    )


def _allow_fields(payload: dict[str, object], allowed: set[str]) -> None:
    if set(payload).difference(allowed):
        raise ValueError("candidate contains unsupported fields")
    if set(payload) != allowed:
        raise ValueError("candidate is missing required fields")


def _procedure_texts(value: object, label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError(f"procedure {label} must be a bounded ordered list")
    if (not allow_empty and not value) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"procedure {label} must contain non-empty text")
    return tuple(value)


def _procedure_branches(value: object) -> tuple[tuple[ProcedureRecordBranch, ...], tuple[_InlineHint, ...]]:
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError("procedure branches must be a bounded ordered list")
    branches = []
    hints = []
    position = 0
    for item in value:
        if not isinstance(item, dict) or set(item) != {"condition", "steps"}:
            raise ValueError("procedure branches must be structured")
        condition = item["condition"]
        if not isinstance(condition, str) or not condition.strip():
            raise ValueError("procedure branch condition must be non-empty text")
        steps, step_hints = _inline_occurrences(
            item["steps"], "branch steps", "branch_step", allow_empty=False, start_position=position,
        )
        branches.append(ProcedureRecordBranch(condition, steps))
        hints.extend(step_hints)
        position += len(steps)
    return tuple(branches), tuple(hints)


def _anchors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("records require at least one anchor")
    if len(value) > MAX_ANCHORS_PER_CANDIDATE:
        raise ValueError("too many candidate anchors")
    if any(not isinstance(anchor, str) or not anchor or len(anchor) > MAX_ANCHOR_ID_LENGTH for anchor in value):
        raise ValueError("invalid candidate anchor")
    return tuple(value)
