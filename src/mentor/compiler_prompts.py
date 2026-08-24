"""Versioned, bounded source-extraction request definitions."""

import json
from typing import Mapping

from mentor.knowledge import SourceRevision


EXTRACTION_PROMPT_VERSION = "source-extraction-v2"
EXTRACTION_SCHEMA_VERSION = "source-extraction-schema-v2"
SEMANTIC_VALIDATION_PROMPT_VERSION = "source-semantic-validation-v2"
SEMANTIC_VALIDATION_SCHEMA_VERSION = "source-semantic-validation-schema-v1"
MAX_CANDIDATES_PER_SOURCE = 12
MAX_ANCHORS_PER_CANDIDATE = 8
MAX_ANCHOR_ID_LENGTH = 128

EXTRACTION_INSTRUCTIONS = """Extract at most 12 compact claim, relationship, or procedure/sequence/hierarchy records from this one source revision.
Use only proposed anchor IDs already supplied by the source-processing pipeline. Include bounded concept hints when an alias or scope is explicit.
Use strategy_implication only when the raw passage explicitly teaches that implication; otherwise leave it for cross-source synthesis.
An empty candidate list is valid. Do not approve, validate, score, or explain candidates.
"""

EXTRACTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": MAX_CANDIDATES_PER_SOURCE,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["family", "anchors", "qualification"],
                "properties": {
                    "family": {
                        "enum": ["claim", "relationship", "procedure_sequence_hierarchy"]
                    },
                    "anchors": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_ANCHORS_PER_CANDIDATE,
                        "items": {"type": "string", "minLength": 1, "maxLength": MAX_ANCHOR_ID_LENGTH},
                    },
                    "qualification": {"type": "string", "minLength": 1, "maxLength": 280},
                    "subject": {"type": "string", "minLength": 1, "maxLength": 240},
                    "predicate": {"type": "string", "minLength": 1, "maxLength": 240},
                    "object": {"type": "string", "minLength": 1, "maxLength": 240},
                    "semantic_subtype": {
                        "enum": ["statement", "definition", "recommendation", "strategy_implication"]
                    },
                    "left": {"type": "string", "minLength": 1, "maxLength": 240},
                    "relation": {"enum": ["supports", "contrasts", "depends_on", "causes"]},
                    "right": {"type": "string", "minLength": 1, "maxLength": 240},
                    "kind": {"enum": ["procedure", "sequence", "hierarchy"]},
                    "terms": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 8,
                        "items": {"type": "string", "minLength": 1, "maxLength": 240},
                    },
                    "concept_hints": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["label", "aliases", "scope", "role", "position"],
                            "properties": {
                                "label": {"type": "string", "minLength": 1, "maxLength": 120},
                                "aliases": {
                                    "type": "array",
                                    "maxItems": 8,
                                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                                },
                                "scope": {"type": ["string", "null"], "maxLength": 160},
                                "role": {"type": ["string", "null"], "maxLength": 120},
                                "position": {"type": ["integer", "null"], "minimum": 0},
                            },
                        },
                    },
                },
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


SEMANTIC_VALIDATION_INSTRUCTIONS = """Independently assess whether the supplied raw support spans affirmatively teach the proposed typed record.
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
