"""Versioned, bounded source-extraction request definitions."""

import json
from typing import Mapping

from mentor.derived_records import RELATIONSHIP_TYPES
from mentor.knowledge import SourceRevision


EXTRACTION_PROMPT_VERSION = "source-extraction-v5"
EXTRACTION_SCHEMA_VERSION = "source-extraction-schema-v5"
SEMANTIC_VALIDATION_PROMPT_VERSION = "source-semantic-validation-v3"
SEMANTIC_VALIDATION_SCHEMA_VERSION = "source-semantic-validation-schema-v1"
MAX_CANDIDATES_PER_SOURCE = 12
MAX_ANCHORS_PER_CANDIDATE = 8
MAX_ANCHOR_ID_LENGTH = 128

EXTRACTION_INSTRUCTIONS = """Extract at most 12 compact claim, relationship, or procedure/sequence/hierarchy records from this one source revision.
Use only proposed anchor IDs already supplied by the source-processing pipeline. Attach aliases and optional scope directly to the typed concept occurrence that they describe. Do not emit separate concept selectors, labels, roles, positions, or concept IDs.
Every inline occurrence has its exact typed text plus aliases and scope. The compiler derives canonical concept identity, field role, and ordered position from that typed record structure.
Represent procedure prerequisites, conditions, and conditional branches structurally rather than folding them into prose.
Use strategy_implication only when the raw passage explicitly teaches that implication; otherwise leave it for cross-source synthesis.
An empty candidate list is valid. Do not approve, validate, score, or explain candidates.
"""

_ANCHORS_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "maxItems": MAX_ANCHORS_PER_CANDIDATE,
    "items": {"type": "string", "minLength": 1, "maxLength": MAX_ANCHOR_ID_LENGTH},
}
_CONCEPT_OCCURRENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "aliases", "scope"],
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 120},
        "aliases": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 120},
        },
        "scope": {"type": ["string", "null"], "maxLength": 160},
    },
}
_TEXT_LIST_SCHEMA = {
    "type": "array",
    "maxItems": 8,
    "items": {"type": "string", "minLength": 1, "maxLength": 240},
}
_CONCEPT_OCCURRENCE_LIST_SCHEMA = {
    "type": "array",
    "maxItems": 8,
    "items": _CONCEPT_OCCURRENCE_SCHEMA,
}
_PROCEDURE_BRANCH_SCHEMA = {
    "type": "array",
    "maxItems": 8,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["condition", "steps"],
        "properties": {
            "condition": {"type": "string", "minLength": 1, "maxLength": 160},
            "steps": _CONCEPT_OCCURRENCE_LIST_SCHEMA | {"minItems": 1},
        },
    },
}

EXTRACTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": MAX_CANDIDATES_PER_SOURCE,
            "items": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "family", "anchors", "qualification", "subject", "predicate",
                            "object", "semantic_subtype",
                        ],
                        "properties": {
                            "family": {"enum": ["claim"]},
                            "anchors": _ANCHORS_SCHEMA,
                            "qualification": {"type": "string", "minLength": 1, "maxLength": 280},
                            "subject": _CONCEPT_OCCURRENCE_SCHEMA,
                            "predicate": {"type": "string", "minLength": 1, "maxLength": 120},
                            "object": _CONCEPT_OCCURRENCE_SCHEMA,
                            "semantic_subtype": {
                                "enum": ["statement", "definition", "recommendation", "strategy_implication"]
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "family", "anchors", "qualification", "left", "relation", "right",
                        ],
                        "properties": {
                            "family": {"enum": ["relationship"]},
                            "anchors": _ANCHORS_SCHEMA,
                            "qualification": {"type": "string", "minLength": 1, "maxLength": 280},
                            "left": _CONCEPT_OCCURRENCE_SCHEMA,
                            "relation": {"enum": sorted(RELATIONSHIP_TYPES)},
                            "right": _CONCEPT_OCCURRENCE_SCHEMA,
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "family", "anchors", "qualification", "kind", "terms",
                            "prerequisites", "conditions", "branches",
                        ],
                        "properties": {
                            "family": {"enum": ["procedure_sequence_hierarchy"]},
                            "anchors": _ANCHORS_SCHEMA,
                            "qualification": {"type": "string", "minLength": 1, "maxLength": 280},
                            "kind": {"enum": ["procedure", "sequence", "hierarchy"]},
                            "terms": _CONCEPT_OCCURRENCE_LIST_SCHEMA | {"minItems": 2},
                            "prerequisites": _CONCEPT_OCCURRENCE_LIST_SCHEMA,
                            "conditions": _TEXT_LIST_SCHEMA,
                            "branches": _PROCEDURE_BRANCH_SCHEMA,
                        },
                    },
                ]
            },
        }
    },
}


def extraction_request(
    *,
    revision: SourceRevision,
    transcript: str,
    model: str,
    anchor_spans: Mapping[str, str] | None = None,
) -> dict[str, object]:
    anchors = dict(anchor_spans or {})
    return {
        "model": model,
        "store": False,
        "instructions": f"Prompt version: {EXTRACTION_PROMPT_VERSION}\n{EXTRACTION_INSTRUCTIONS}",
        "input": (
            f"Source revision: {revision.revision_id}\n\nTranscript:\n{transcript}\n\n"
            f"Candidate anchors:\n{json.dumps(anchors, sort_keys=True, separators=(',', ':'))}"
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": EXTRACTION_SCHEMA_VERSION,
                "schema": EXTRACTION_RESPONSE_SCHEMA,
                "strict": True,
            }
        },
    }


SEMANTIC_VALIDATION_INSTRUCTIONS = """Independently assess whether the supplied raw support spans affirmatively teach the proposed typed record and every supplied concept alias.
Return only the requested outcome and one concise audit sentence. Do not rely on extraction rationale or infer beyond the spans.
"""

SEMANTIC_VALIDATION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "audit"],
    "properties": {
        "outcome": {
            "enum": [
                "affirmatively_supported",
                "partially_supported",
                "unsupported",
                "ambiguous",
                "needs_broader_context",
            ]
        },
        "audit": {"type": "string", "minLength": 1, "maxLength": 280},
    },
}


def semantic_validation_request(*, record: dict[str, object], spans: tuple[tuple[str, str], ...], model: str) -> dict[str, object]:
    return {
        "model": model,
        "store": False,
        "instructions": f"Prompt version: {SEMANTIC_VALIDATION_PROMPT_VERSION}\n{SEMANTIC_VALIDATION_INSTRUCTIONS}",
        "input": json.dumps({"record": record, "supporting_spans": spans}, separators=(",", ":")),
        "text": {
            "format": {
                "type": "json_schema",
                "name": SEMANTIC_VALIDATION_SCHEMA_VERSION,
                "schema": SEMANTIC_VALIDATION_RESPONSE_SCHEMA,
                "strict": True,
            }
        },
    }
