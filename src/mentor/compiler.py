"""Injected-client extraction of unvalidated, per-source candidate records."""

from dataclasses import dataclass
import json
from typing import Any

from mentor.compiler_prompts import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SCHEMA_VERSION,
    MAX_CANDIDATES_PER_SOURCE,
    extraction_request,
)
from mentor.derived_records import (
    Claim,
    DerivedRecord,
    RecordDependency,
)
from mentor.knowledge import SourceRevision


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
class ExtractionProvenance:
    model_version: str
    prompt_version: str
    schema_version: str


@dataclass(frozen=True)
class ExtractionResult:
    revision_id: str
    provenance: ExtractionProvenance
    candidates: tuple[DerivedRecord, ...]


class SourceExtractor:
    """Extract candidates through a caller-owned Responses-compatible client."""

    def __init__(self, client: Any, *, model: str = "synthetic-compiler", live_mode: bool = False):
        if model == SOL_MODEL and not live_mode:
            raise ValueError("GPT-5.6 Sol requires explicit live mode")
        self._client = client
        self._provenance = ExtractionProvenance(model, EXTRACTION_PROMPT_VERSION, EXTRACTION_SCHEMA_VERSION)

    def extract(
        self, *, revision: SourceRevision, snapshot_id: str, transcript: str
    ) -> ExtractionResult:
        response = self._client.responses.create(
            **extraction_request(revision=revision, transcript=transcript, model=self._provenance.model_version)
        )
        candidates = _parse_candidates(
            _response_output_text(response), revision=revision, snapshot_id=snapshot_id
        )
        return ExtractionResult(revision.revision_id, self._provenance, candidates)


def _response_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str):
        raise ValueError("extraction response requires output_text")
    return output_text


def _parse_candidates(output_text: str, *, revision: SourceRevision, snapshot_id: str) -> tuple[DerivedRecord, ...]:
    try:
        response = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise ValueError("extraction response must be JSON") from error
    if not isinstance(response, dict) or set(response) != {"candidates"} or not isinstance(response["candidates"], list):
        raise ValueError("extraction response requires only a candidates list")
    if len(response["candidates"]) > MAX_CANDIDATES_PER_SOURCE:
        raise ValueError("too many source candidates")
    return tuple(
        _candidate_from_payload(payload, revision=revision, snapshot_id=snapshot_id)
        for payload in response["candidates"]
    )


def _candidate_from_payload(
    payload: object, *, revision: SourceRevision, snapshot_id: str
) -> DerivedRecord:
    if not isinstance(payload, dict):
        raise ValueError("candidate must be an object")
    attempted_validation = _SELF_VALIDATION_FIELDS.intersection(payload)
    if attempted_validation:
        raise ValueError("candidate self-validation is forbidden")
    family = payload.get("family")
    if not isinstance(family, str):
        raise ValueError("unknown derived record family")
    if family != "claim":
        raise ValueError("unknown derived record family")
    common = {
        "snapshot_id": snapshot_id,
        "anchors": _anchors(payload.get("anchors")),
        "dependencies": (RecordDependency("source_revision", revision.revision_id),),
        "validation_state": "pending",
        "lifecycle_state": "candidate",
        "qualification": payload.get("qualification"),
    }
    _allow_fields(payload, {"family", "anchors", "qualification", "subject", "predicate", "object", "derived_kind"})
    return Claim.create(
        **common,
        subject=payload.get("subject"),
        predicate=payload.get("predicate"),
        object=payload.get("object"),
        derived_kind=payload.get("derived_kind", "statement"),
    )


def _allow_fields(payload: dict[str, object], allowed: set[str]) -> None:
    if set(payload).difference(allowed):
        raise ValueError("candidate contains unsupported fields")


def _anchors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(value)
