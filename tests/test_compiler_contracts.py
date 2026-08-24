"""Deterministic parity checks for every paid compiler-stage response contract."""

from copy import deepcopy

from openai.lib._pydantic import _ensure_strict_json_schema

from mentor.compiler_prompts import (
    EXTRACTION_RESPONSE_SCHEMA,
    SEMANTIC_VALIDATION_RESPONSE_SCHEMA,
)
from mentor.synthesis import SYNTHESIS_RESPONSE_SCHEMA
from mentor.validation import SEMANTIC_OUTCOMES


def _assert_openai_strict_schema(schema: dict[str, object]) -> None:
    candidate = deepcopy(schema)
    assert _ensure_strict_json_schema(candidate, path=(), root=candidate) == schema

    pending = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                assert value.get("additionalProperties") is False
                assert set(value.get("required", ())) == set(properties)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def test_paid_compiler_output_contracts_are_closed_strict_and_match_parser_enums():
    _assert_openai_strict_schema(EXTRACTION_RESPONSE_SCHEMA)
    _assert_openai_strict_schema(SEMANTIC_VALIDATION_RESPONSE_SCHEMA)
    _assert_openai_strict_schema(SYNTHESIS_RESPONSE_SCHEMA)

    validation_outcomes = SEMANTIC_VALIDATION_RESPONSE_SCHEMA["properties"]["outcome"]["enum"]
    assert set(validation_outcomes) == SEMANTIC_OUTCOMES
    synthesis_families = {
        family_schema["properties"]["family"]["enum"][0]
        for family_schema in SYNTHESIS_RESPONSE_SCHEMA["properties"]["records"]["items"]["anyOf"]
    }
    assert synthesis_families == {
        "relationship", "procedure_sequence_hierarchy", "evolution", "conflict_unresolved",
    }
