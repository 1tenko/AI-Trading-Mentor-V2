"""Versioned, bounded source-extraction request definitions."""

import json

from mentor.knowledge import SourceRevision


EXTRACTION_PROMPT_VERSION = "source-extraction-v1"
EXTRACTION_SCHEMA_VERSION = "source-extraction-schema-v1"
SEMANTIC_VALIDATION_PROMPT_VERSION = "source-semantic-validation-v1"
SEMANTIC_VALIDATION_SCHEMA_VERSION = "source-semantic-validation-schema-v1"
MAX_CANDIDATES_PER_SOURCE = 12
MAX_ANCHORS_PER_CANDIDATE = 8
MAX_ANCHOR_ID_LENGTH = 128

EXTRACTION_INSTRUCTIONS = """Extract at most 12 compact candidate records from this one source revision.
Return only claims with proposed anchor IDs already supplied by the source-processing pipeline.
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
                "required": ["family", "anchors", "qualification", "subject", "predicate", "object"],
                "properties": {
                    "family": {"const": "claim"},
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
                    "derived_kind": {
                        "enum": ["statement", "definition", "recommendation", "strategy_implication"]
                    },
                },
            },
        }
    },
}


def extraction_request(*, revision: SourceRevision, transcript: str, model: str) -> dict[str, object]:
    return {
        "model": model,
        "store": False,
        "instructions": f"Prompt version: {EXTRACTION_PROMPT_VERSION}\n{EXTRACTION_INSTRUCTIONS}",
        "input": f"Source revision: {revision.revision_id}\n\nTranscript:\n{transcript}",
        "text": {
            "format": {
                "type": "json_schema",
                "name": EXTRACTION_SCHEMA_VERSION,
                "schema": EXTRACTION_RESPONSE_SCHEMA,
                "strict": True,
            }
        },
    }


SEMANTIC_VALIDATION_INSTRUCTIONS = """Independently assess whether the supplied raw support spans affirmatively teach the proposed claim.
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


def semantic_validation_request(*, claim: dict[str, str], spans: tuple[tuple[str, str], ...], model: str) -> dict[str, object]:
    return {
        "model": model,
        "store": False,
        "instructions": f"Prompt version: {SEMANTIC_VALIDATION_PROMPT_VERSION}\n{SEMANTIC_VALIDATION_INSTRUCTIONS}",
        "input": json.dumps({"claim": claim, "supporting_spans": spans}, separators=(",", ":")),
        "text": {
            "format": {
                "type": "json_schema",
                "name": SEMANTIC_VALIDATION_SCHEMA_VERSION,
                "schema": SEMANTIC_VALIDATION_RESPONSE_SCHEMA,
                "strict": True,
            }
        },
    }
