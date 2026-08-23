"""Versioned, bounded source-extraction request definitions."""

from mentor.knowledge import SourceRevision


EXTRACTION_PROMPT_VERSION = "source-extraction-v1"
EXTRACTION_SCHEMA_VERSION = "source-extraction-schema-v1"
MAX_CANDIDATES_PER_SOURCE = 12

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
                    "anchors": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
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
