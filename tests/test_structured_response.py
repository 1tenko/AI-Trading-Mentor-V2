from types import SimpleNamespace

import pytest

from mentor.structured_response import (
    ResponseEnvelopeError,
    StructuredOutputContractError,
    structured_json_payload,
    structured_output_text,
)


def response(*, status="completed", output_text='{"ok":true}', output=(), error=None, incomplete_details=None):
    return SimpleNamespace(
        status=status,
        output_text=output_text,
        output=list(output),
        error=error,
        incomplete_details=incomplete_details,
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
    )


def message(*content):
    return SimpleNamespace(type="message", content=list(content))


def content(type_, **values):
    return SimpleNamespace(type=type_, **values)


def test_completed_single_output_text_reaches_the_structured_parser_boundary():
    value = response(output=[message(content("output_text", text='{"ok":true}'))])

    assert structured_output_text(value, stage="extraction") == '{"ok":true}'


def test_paid_boundary_never_falls_back_to_output_text_without_a_completed_envelope():
    value = SimpleNamespace(output_text='{"ok":true}')

    with pytest.raises(ResponseEnvelopeError, match="response_not_completed"):
        structured_output_text(value, stage="extraction")
    assert structured_output_text(value, stage="extraction", allow_synthetic=True) == '{"ok":true}'


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (response(status="failed", error=SimpleNamespace(code="server_error", message="failed")), "response_failed"),
        (response(status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens")), "response_incomplete_max_output_tokens"),
        (response(output=[message(content("refusal", refusal="no"))]), "response_refusal"),
        (response(output_text=None, output=[]), "response_missing_output_text"),
        (response(output=[SimpleNamespace(type="function_call")]), "response_unexpected_output"),
    ],
)
def test_non_success_envelopes_never_reach_structured_json(value, code):
    with pytest.raises(ResponseEnvelopeError, match=code):
        structured_output_text(value, stage="extraction")


def test_completed_non_refusal_output_without_json_is_distinct_contract_failure():
    value = response(output_text="not json", output=[message(content("output_text", text="not json"))])

    with pytest.raises(StructuredOutputContractError, match="completed_non_json"):
        structured_json_payload(value, stage="extraction")


def test_private_diagnostic_contains_envelope_metadata_without_reasoning():
    from mentor.structured_response import private_response_diagnostic

    value = response(
        output=[message(content("refusal", refusal="no"))],
        error=SimpleNamespace(code="x", message="y"),
    )

    diagnostic = private_response_diagnostic(value, stage="validation", call_index=2, model="synthetic", prompt_version="p", schema_version="s")

    assert diagnostic["stage"] == "validation"
    assert diagnostic["status"] == "completed"
    assert diagnostic["output_item_types"] == ["message"]
    assert diagnostic["content_item_types"] == ["refusal"]
    assert diagnostic["refusal"] == "no"
    assert "reasoning" not in diagnostic
