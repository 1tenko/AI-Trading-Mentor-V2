"""One strict Responses envelope boundary for paid compiler stages."""

import json
from typing import Any


class ResponseEnvelopeError(ValueError):
    """A terminal Responses state that is not usable structured output."""


class StructuredOutputContractError(ValueError):
    @classmethod
    def completed_non_json(cls, stage: str) -> "StructuredOutputContractError":
        return cls(f"{stage} response_completed_non_json")


def structured_output_text(response: Any, *, stage: str, allow_synthetic: bool = False) -> str:
    """Return exactly one completed, non-refusal structured text payload."""
    status = _field(response, "status")
    if status == "failed":
        raise ResponseEnvelopeError(f"{stage} response_failed")
    if status == "incomplete":
        details = _field(response, "incomplete_details")
        reason = _field(details, "reason")
        suffix = f"_{reason}" if isinstance(reason, str) and reason else ""
        raise ResponseEnvelopeError(f"{stage} response_incomplete{suffix}")
    if status != "completed":
        if status is None and allow_synthetic:
            return _synthetic_output_text(response, stage)
        raise ResponseEnvelopeError(f"{stage} response_not_completed")
    if _field(response, "error") is not None:
        raise ResponseEnvelopeError(f"{stage} response_error")

    output = _field(response, "output")
    if output is None:
        raise ResponseEnvelopeError(f"{stage} response_malformed_output")
    if not isinstance(output, (list, tuple)):
        raise ResponseEnvelopeError(f"{stage} response_malformed_output")

    text_items: list[str] = []
    for item in output:
        item_type = _field(item, "type")
        if item_type == "reasoning":
            continue
        if item_type != "message":
            raise ResponseEnvelopeError(f"{stage} response_unexpected_output")
        content = _field(item, "content")
        if not isinstance(content, (list, tuple)):
            raise ResponseEnvelopeError(f"{stage} response_malformed_message")
        for part in content:
            part_type = _field(part, "type")
            if part_type == "refusal":
                raise ResponseEnvelopeError(f"{stage} response_refusal")
            if part_type != "output_text":
                raise ResponseEnvelopeError(f"{stage} response_unexpected_content")
            text = _field(part, "text")
            if not isinstance(text, str):
                raise ResponseEnvelopeError(f"{stage} response_missing_output_text")
            text_items.append(text)
    if len(text_items) != 1:
        raise ResponseEnvelopeError(f"{stage} response_missing_output_text")
    output_text = _field(response, "output_text")
    if not isinstance(output_text, str) or output_text != text_items[0]:
        raise ResponseEnvelopeError(f"{stage} response_malformed_output")
    return output_text


def structured_json_payload(response: Any, *, stage: str, allow_synthetic: bool = False) -> object:
    text = structured_output_text(response, stage=stage, allow_synthetic=allow_synthetic)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise StructuredOutputContractError.completed_non_json(stage) from error


def private_response_diagnostic(
    response: Any,
    *,
    stage: str,
    call_index: int,
    model: str | None,
    prompt_version: str | None,
    schema_version: str | None,
    requested_max_output_tokens: int | None = None,
) -> dict[str, object]:
    """Private, non-reasoning audit material for an ignored pilot artifact."""
    output = _field(response, "output")
    output_items = output if isinstance(output, (list, tuple)) else ()
    content = [part for item in output_items if _field(item, "type") == "message"
               for part in (_field(item, "content") or ())]
    error = _field(response, "error")
    incomplete = _field(response, "incomplete_details")
    usage = _field(response, "usage")
    refusals = [_field(part, "refusal") for part in content if _field(part, "type") == "refusal"]
    usage_details = _details(usage)
    output_details = _details(_field(usage, "output_tokens_details"))
    if usage_details is not None and output_details is not None:
        usage_details["output_tokens_details"] = output_details
    return {
        "stage": stage,
        "call_index": call_index,
        "model": model,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "requested_max_output_tokens": requested_max_output_tokens,
        "response_id": _field(response, "id"),
        "status": _field(response, "status"),
        "error": _details(error),
        "incomplete_details": _details(incomplete),
        "output_item_types": [_field(item, "type") for item in output_items],
        "content_item_types": [_field(part, "type") for part in content],
        "refusal": refusals[0] if refusals else None,
        "usage": usage_details,
        "output_text": _field(response, "output_text"),
    }


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _synthetic_output_text(response: Any, stage: str) -> str:
    text = _field(response, "output_text")
    if isinstance(text, str):
        return text
    raise ResponseEnvelopeError(f"{stage} response_missing_output_text")


def _details(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        key: _field(value, key)
        for key in ("code", "type", "message", "reason", "input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")
        if _field(value, key) is not None
    }
