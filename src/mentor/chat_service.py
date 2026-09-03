"""Grounded, stateless Responses API chat for the Phase 1 proof."""

from dataclasses import dataclass
import json
import logging
import re
import sqlite3
from time import perf_counter
from typing import Any
from types import SimpleNamespace
from uuid import uuid4

from mentor.profile import (
    ProfileService,
    ProfileValidationError,
    full_questionnaire_profile_context,
    questionnaire_field_state,
    select_profile_context,
    strategy_profile_context,
)
from mentor.analysis import (
    ANALYSIS_SCHEMA_VERSION,
    AnalysisFilter,
    analyze_mfe_mae,
    analyze_over_time,
    build_analysis_frame,
    compare_groups,
    group_results,
    read_text_evidence,
    summarize_results,
    validate_text_evidence_request,
)
from mentor.datasets import (
    QualitativeDisclosureCapability,
    QualitativeTransportError,
    continue_qualitative_model_transport,
    ensure_current_auto_mapping,
    model_mapping_context,
    safe_provider_error_details,
)
from mentor.prompts import ANALYSIS_TOOL_INSTRUCTIONS, MENTOR_INSTRUCTIONS, PROFILE_TOOL_INSTRUCTIONS
from mentor.storage import Storage


LOGGER = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 25_000
COMPACTION_TOKEN_THRESHOLD = 50_000
FILE_SEARCH_RESULT_BUDGETS = {"normal": 8, "deep": 20, "exhaustive": 20}
FILE_SEARCH_CALL_COST_USD = 0.0025
DIRECT_SOURCE_CLAIM = re.compile(
    r"(?:\*{1,2})?Direct source teaching"
    r"(?:\*{1,2})?\s*(?::|[-\u2013\u2014]|\n)\s*\S",
    re.IGNORECASE,
)
EXACT_SOURCE_REQUEST = re.compile(
    r"\bwhere\s+(?:exactly\s+)?(?:does|did|is)\b|\b(?:what|which)\s+(?:video|timestamp)\b"
    r"|\bgive me\b.*\b(?:video|timestamp)\b|\bexact source\b",
    re.IGNORECASE,
)
CLOCK_TIME = re.compile(
    r"(?<!\d)(\d{1,2}):(\d{2})(?:\s*[-\u2013\u2014]\s*(\d{1,2}):(\d{2}))?(?!\d)"
)
EVIDENCE_TIME_RANGE = re.compile(r"\[(\d+(?:\.\d+)?)\s*(?:-->|\u2192)\s*(\d+(?:\.\d+)?)\]")
CITATION_REPAIR_INSTRUCTION = """Citation repair: the immediately preceding draft contains
Direct source teaching claims but no native File Search citations. Reissue the same substantive
answer with the same uncertainty. Attach native File Search citations to relevant Direct source
teaching claims and, where reasonably possible, materially source-based synthesis. Use the
existing File Search context or one focused File Search if necessary. Do not add, alter, or
relabel claims merely to manufacture citations. For an exact timestamp request, give a timestamp
only when a retrieved passage from the cited source supports it; otherwise state that it cannot be
verified precisely."""
CITATION_WARNING = (
    "\n\n> **Citation warning:** Native source citations could not be attached to this "
    "source-derived answer. Treat its Direct source teaching labels as unverified and inspect "
    "the retrieved research results below."
)
EXACT_SOURCE_WARNING = (
    "> **Source-verification warning:** The mentor could not verify an exact timestamp against a "
    "retrieved passage from the cited source. No exact timestamp is being presented."
)
EXACT_TIMESTAMP_REPAIR_INSTRUCTION = (
    "For this exact timestamp request, you must perform one new focused native File Search before "
    "answering, even if the draft included other retrieved passages."
)
SUPPORTED_REASONING_EFFORTS = frozenset({"high", "xhigh", "max"})
SUPPORTED_REASONING_MODES = frozenset({"standard", "pro"})
SUPPORTED_RESEARCH_DEPTHS = frozenset({"auto", "normal", "deep", "exhaustive"})
PROFILE_TOOL_NAME = "update_trader_profile"
PROFILE_CONTEXT_NONE = "none"
PROFILE_CONTEXT_RELEVANT = "relevant"
PROFILE_CONTEXT_FULL_PROFILE = "full_profile"
PROFILE_CONTEXT_STRATEGY = "strategy"
PROFILE_TOOL = {
    "type": "function",
    "name": PROFILE_TOOL_NAME,
    "description": "Save or propose one explicit trader-profile item, or archive/delete one explicitly identified profile item.",
    "strict": True,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "category", "subject", "value", "kind", "provenance", "target_id"],
        "properties": {
            "operation": {"type": "string", "enum": ["save", "propose", "archive", "delete"]},
            "category": {
                "anyOf": [{"type": "string", "enum": [
                    "goals/research", "markets/instruments", "schedule/horizon", "style/methodology",
                    "execution/risk/constraints", "experience/learning", "preferences/discretion",
                    "strengths/difficulties/principles",
                ]}, {"type": "null"}],
            },
            "subject": {"type": ["string", "null"], "minLength": 1, "maxLength": 120},
            "value": {"type": ["string", "null"], "minLength": 1, "maxLength": 500},
            "kind": {
                "anyOf": [{"type": "string", "enum": [
                    "fact", "preference", "constraint", "goal", "principle", "learning-state",
                ]}, {"type": "null"}],
            },
            "provenance": {"anyOf": [{"type": "string", "enum": ["USER_STATED", "USER_DECISION"]}, {"type": "null"}]},
            "target_id": {"type": ["integer", "null"], "minimum": 1},
        },
    },
}
EXPLICIT_PROFILE_WRITE = re.compile(
    r"\b(?:remember|save this|save that|my goal changed|i have decided|i decided)\b", re.IGNORECASE
)
EXPLICIT_PROFILE_FORGET = re.compile(r"\b(?:forget|archive|delete|remove)\b", re.IGNORECASE)
EXPLICIT_PROFILE_TARGET = re.compile(r"\bprofile\s+item\s*#?\s*(\d+)\b", re.IGNORECASE)
PROFILE_TOOL_KEYS = frozenset({"operation", "category", "subject", "value", "kind", "provenance", "target_id"})
PROFILE_WRITE_FIELDS = frozenset({"category", "subject", "value", "kind", "provenance"})
ANALYSIS_TOOL_NAMES = frozenset({
    "inspect_dataset", "summarize_results", "group_results", "compare_groups",
    "analyze_mfe_mae", "analyze_over_time", "read_text_evidence",
})
QUALITATIVE_TOOL_NAME = "read_text_evidence"
MAX_ANALYSIS_CALLS = 7
MAX_DETERMINISTIC_ANALYSIS_CALLS = 6
MAX_DETERMINISTIC_OUTPUT_CHARS = 32_000


def _tool(name: str, description: str, properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required,
        },
    }


_FILTER_VALUE_SCHEMA = {
    "anyOf": [
        {"type": "string", "maxLength": 200},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
        {"type": "array", "items": {"anyOf": [{"type": "string", "maxLength": 200}, {"type": "number"}, {"type": "boolean"}, {"type": "null"}]}, "maxItems": 20},
    ],
}
_FILTER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "field_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "operator": {"type": "string", "enum": ["eq", "neq", "in", "not_in", "is_blank", "not_blank", "gt", "gte", "lt", "lte", "between"]},
        "value": _FILTER_VALUE_SCHEMA,
    },
    "required": ["field_id", "operator", "value"],
}
_FILTERS_SCHEMA = {"type": "array", "items": _FILTER_SCHEMA, "maxItems": 8}
_FIELD_IDS_SCHEMA = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 120}, "maxItems": 3}
ANALYSIS_TOOLS = [
    _tool("inspect_dataset", "Inspect the active local dataset's safe mapping and row-health summary; never returns raw cells.", {}, []),
    _tool("summarize_results", "Compute deterministic aggregate results for the active dataset.", {"filters": _FILTERS_SCHEMA}, ["filters"]),
    _tool("group_results", "Compute deterministic aggregate results by one or two approved fields.", {"group_field_ids": {**_FIELD_IDS_SCHEMA, "minItems": 1, "maxItems": 2}, "filters": _FILTERS_SCHEMA}, ["group_field_ids", "filters"]),
    _tool("compare_groups", "Compare two distinct values in one approved group field.", {"field_id": {"type": "string", "minLength": 1, "maxLength": 120}, "value_a": _FILTER_VALUE_SCHEMA, "value_b": _FILTER_VALUE_SCHEMA, "filters": _FILTERS_SCHEMA}, ["field_id", "value_a", "value_b", "filters"]),
    _tool("analyze_mfe_mae", "Compute deterministic MFE and MAE aggregates when mapped.", {"filters": _FILTERS_SCHEMA}, ["filters"]),
    _tool("analyze_over_time", "Compute deterministic chronological aggregates for the active dataset.", {"mode": {"type": "string", "enum": ["month", "halves", "rolling"]}, "window_size": {"type": ["integer", "null"], "minimum": 1, "maximum": 250}, "filters": _FILTERS_SCHEMA}, ["mode", "window_size", "filters"]),
    _tool("read_text_evidence", "Read one bounded, explicitly approved local note sample for this turn only.", {"text_field_ids": {**_FIELD_IDS_SCHEMA, "minItems": 1, "maxItems": 1}, "context_field_ids": _FIELD_IDS_SCHEMA, "filters": _FILTERS_SCHEMA, "order_by": {"type": "string", "enum": ["source", "timestamp"]}}, ["text_field_ids", "context_field_ids", "filters", "order_by"]),
]


@dataclass(frozen=True)
class EvaluationConfig:
    reasoning_effort: str = "high"
    reasoning_mode: str = "standard"
    research_depth: str = "auto"

    def __post_init__(self) -> None:
        if self.reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
            raise ValueError("Reasoning effort must be high, xhigh, or max.")
        if self.reasoning_mode not in SUPPORTED_REASONING_MODES:
            raise ValueError("Reasoning mode must be standard or pro.")
        if self.research_depth not in SUPPORTED_RESEARCH_DEPTHS:
            raise ValueError("Research depth must be auto, normal, deep, or exhaustive.")

    def request_value(self) -> dict[str, str]:
        value = {"effort": self.reasoning_effort}
        if self.reasoning_mode == "pro":
            value["mode"] = "pro"
        return value


DEFAULT_EVALUATION_CONFIG = EvaluationConfig()


@dataclass(frozen=True)
class Citation:
    file_id: str
    filename: str


@dataclass(frozen=True)
class Evidence:
    file_id: str
    filename: str
    excerpt: str
    year: str | None
    metadata: dict[str, str]


@dataclass(frozen=True)
class ResponseDiagnostics:
    response_id: str
    model: str
    status: str
    reasoning_effort: str
    reasoning_mode: str
    requested_research_depth: str
    effective_research_depth: str
    file_search_calls: int
    file_search_queries: list[str]
    returned_evidence_count: int
    cited_evidence_count: int
    file_search_cost_status: str
    latency_ms: int
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    estimated_text_cost_usd: float | None
    known_file_search_call_cost_usd: float
    native_compaction_applied: bool
    analysis_calls: dict[str, int]
    analysis_operations: list[str]
    deterministic_result_chars: int
    qualitative_calls: int
    analysis_batch_status: str
    prior_empirical_evidence_reused: bool
    auto_mapping_policy_upgraded: bool


@dataclass(frozen=True)
class AnalysisScope:
    dataset_id: str
    mapping_version_id: int
    auto_mapping_policy_upgraded: bool


@dataclass(frozen=True)
class Answer:
    text: str
    citations: list[Citation]
    evidence: list[Evidence]
    diagnostics: ResponseDiagnostics | None = None
    incomplete_reason: str | None = None
    profile_update: dict[str, str] | None = None


@dataclass(frozen=True)
class StreamEvent:
    type: str
    text: str = ""
    answer: Answer | None = None
    incomplete_reason: str | None = None
    error: str = ""
    error_classification: str = ""
    qualitative_field_count: int = 0
    qualitative_context_field_count: int = 0


class ChatService:
    def __init__(self, storage: Storage, client: Any, model: str = "gpt-5.6-sol"):
        self.storage = storage
        self.client = client
        self.model = model

    def _responses_create(self, request: dict, stage: str, *, stream: bool = False) -> Any:
        try:
            return self.client.responses.create(**request, **({"stream": True} if stream else {}))
        except Exception as error:
            _log_safe_responses_error(stage, error)
            raise _ResponsesRequestError(stage, error) from None

    def reply(
        self,
        thread_id: int,
        question: str,
        evaluation: EvaluationConfig = DEFAULT_EVALUATION_CONFIG,
        *,
        include_approved_notes: bool = False,
        dataset_attachment_id: str | None = None,
    ) -> Answer:
        user_item, request, effective_depth, prior_empirical_evidence_reused, auto_mapping_policy_upgraded = self._request(thread_id, question, evaluation)
        started_at = perf_counter()
        response = self._responses_create(request, "initial_response")
        response, leading_output, replay_leading_output, response_request, profile_update, qualitative_exchange = self._local_tools_continued_response(
            thread_id, request, response, user_item["content"][0]["text"], include_approved_notes=include_approved_notes
        )
        response, evidence_output, draft_response = self._citation_repaired_response(
            response_request, response, user_item["content"][0]["text"], qualitative_exchange=qualitative_exchange
        )
        return self._finalize(
            thread_id,
            user_item,
            response,
            evaluation,
            effective_depth,
            started_at,
            evidence_output=[*leading_output, *evidence_output],
            draft_response=draft_response,
            leading_output=leading_output,
            replay_leading_output=replay_leading_output,
            profile_update=profile_update,
            qualitative_exchange=qualitative_exchange,
            dataset_attachment_id=dataset_attachment_id,
            prior_empirical_evidence_reused=prior_empirical_evidence_reused,
            auto_mapping_policy_upgraded=auto_mapping_policy_upgraded,
        )

    def stream_reply(
        self,
        thread_id: int,
        question: str,
        evaluation: EvaluationConfig = DEFAULT_EVALUATION_CONFIG,
        *,
        include_approved_notes: bool = False,
        qualitative_consent_prompt: bool = True,
        dataset_attachment_id: str | None = None,
    ):
        try:
            user_item, request, effective_depth, prior_empirical_evidence_reused, auto_mapping_policy_upgraded = self._request(thread_id, question, evaluation)
            started_at = perf_counter()
            stream = self._responses_create(request, "initial_stream", stream=True)
            for event in stream:
                if event.type == "response.output_text.delta":
                    yield StreamEvent("delta", event.delta)
                elif event.type in {"response.completed", "response.incomplete"}:
                    response, leading_output, replay_leading_output, response_request, profile_update, qualitative_exchange = self._local_tools_continued_response(
                        thread_id,
                        request,
                        event.response,
                        user_item["content"][0]["text"],
                        include_approved_notes=include_approved_notes,
                        pause_for_qualitative_consent=qualitative_consent_prompt,
                    )
                    response, evidence_output, draft_response = self._citation_repaired_response(
                        response_request, response, user_item["content"][0]["text"], qualitative_exchange=qualitative_exchange
                    )
                    answer = self._finalize(
                        thread_id,
                        user_item,
                        response,
                        evaluation,
                        effective_depth,
                        started_at,
                        evidence_output=[*leading_output, *evidence_output],
                        draft_response=draft_response,
                        leading_output=leading_output,
                        replay_leading_output=replay_leading_output,
                        profile_update=profile_update,
                        qualitative_exchange=qualitative_exchange,
                        dataset_attachment_id=dataset_attachment_id,
                        prior_empirical_evidence_reused=prior_empirical_evidence_reused,
                        auto_mapping_policy_upgraded=auto_mapping_policy_upgraded,
                    )
                    if answer.incomplete_reason:
                        yield StreamEvent(
                            "incomplete",
                            answer=answer,
                            incomplete_reason=answer.incomplete_reason,
                        )
                    else:
                        yield StreamEvent("complete", answer=answer)
                    return
                elif event.type in {"response.failed", "response.cancelled", "error"}:
                    LOGGER.warning("OpenAI stream ended with %s", event.type)
                    yield StreamEvent(
                        "error", error="The mentor request failed. Try again.", error_classification="responses_continuation_error"
                    )
                    return
        except _QualitativeConsentRequired as consent:
            yield StreamEvent(
                "consent_required",
                qualitative_field_count=consent.field_count,
                qualitative_context_field_count=consent.context_field_count,
            )
            return
        except _ResponsesRequestError as error:
            yield StreamEvent(
                "error",
                error="The mentor request failed. Try again.",
                error_classification="qualitative_continuation_error" if error.stage == "qualitative_continuation" else "responses_continuation_error",
            )
            return
        except Exception as error:
            LOGGER.warning("OpenAI stream raised %s", type(error).__name__)
            yield StreamEvent(
                "error", error="The mentor request failed. Try again.", error_classification=_safe_error_classification(error)
            )
            return
        LOGGER.warning("OpenAI stream ended without a terminal response event")
        yield StreamEvent("error", error="The mentor stream ended before returning a usable response. Try again.")

    def _citation_repaired_response(
        self, request: dict, response: Any, question: str, *, qualitative_exchange: bool = False
    ) -> tuple[Any, list[dict], Any | None]:
        draft_output = [_as_dict(item) for item in response.output]
        draft = _answer(draft_output)
        needs_citation_repair = _has_direct_source_claim(draft.text) and not draft.citations
        needs_timestamp_repair = _has_unsupported_exact_timestamp(question, draft)
        if _field(response, "status") != "completed" or not (needs_citation_repair or needs_timestamp_repair):
            return response, draft_output, None
        repair_instructions = f"{request['instructions']}\n\n{CITATION_REPAIR_INSTRUCTION}"
        if needs_timestamp_repair:
            repair_instructions = f"{repair_instructions}\n\n{EXACT_TIMESTAMP_REPAIR_INSTRUCTION}"
        repair_history = _qualitative_historic_items(draft_output) if qualitative_exchange else draft_output
        repair_request = {
            **request,
            "instructions": repair_instructions,
            "input": [
                *request["input"],
                *(_input_item(item) for item in repair_history),
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Please repair the citations now."}],
                },
            ],
            "tools": _raw_file_search_tools(request["tools"]),
        }
        if needs_timestamp_repair:
            repair_request["tool_choice"] = {"type": "file_search"}
        repaired = self._responses_create(repair_request, "citation_repair")
        return repaired, [*draft_output, *(_as_dict(item) for item in repaired.output)], response

    def _local_tools_continued_response(
        self,
        thread_id: int,
        request: dict,
        response: Any,
        question: str,
        *,
        include_approved_notes: bool,
        pause_for_qualitative_consent: bool = False,
    ) -> tuple[Any, list[dict], list[dict], dict, dict[str, str] | None, bool]:
        output = [_as_dict(item) for item in response.output]
        calls = [item for item in output if item.get("type") == "function_call"]
        if not calls:
            return response, [], [], request, None, False
        if any(not isinstance(call.get("call_id"), str) or not call["call_id"] for call in calls):
            LOGGER.warning("Local function call missing a usable call id")
            safe_output = [item for item in output if item.get("type") != "function_call"]
            failure_text = (
                "I could not update that profile item. Please try again."
                if all(call.get("name") == PROFILE_TOOL_NAME for call in calls)
                else "I could not complete that local request. Please try again."
            )
            if not any(item.get("type") == "message" and item.get("role") == "assistant" for item in safe_output):
                safe_output.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": failure_text,
                            }
                        ],
                    }
                )
            return _response_with_output(response, safe_output), [], [], request, None, False

        names = {call.get("name") for call in calls}
        profile_calls = [call for call in calls if call.get("name") == PROFILE_TOOL_NAME]
        analysis_calls = [call for call in calls if call.get("name") in ANALYSIS_TOOL_NAMES]
        if len({call["call_id"] for call in calls}) != len(calls):
            results = [_profile_tool_rejection("duplicate_call_id") for _ in calls]
            return (*self._standard_local_continuation(request, output, calls, results), None, False)
        if profile_calls and analysis_calls:
            results = [_profile_tool_rejection("mixed_local_tool_batch_not_supported") for _ in calls]
            return (*self._standard_local_continuation(request, output, calls, results), None, False)
        if profile_calls:
            results = (
                [self._execute_profile_tool(thread_id, question, profile_calls[0])]
                if len(calls) == 1
                else [_profile_tool_rejection("multiple_function_calls") for _ in calls]
            )
            if len(calls) == 1 and names != {PROFILE_TOOL_NAME}:
                results = [_profile_tool_rejection("unsupported_tool")]
            continued = self._standard_local_continuation(request, output, calls, results)
            return (*continued, _profile_update(results[0]) if len(results) == 1 else None, False)
        if len(analysis_calls) != len(calls):
            return (*self._standard_local_continuation(
                request, output, calls, [_profile_tool_rejection("unsupported_tool") for _ in calls]
            ), None, False)
        scope = self._active_analysis_scope(thread_id)
        if scope is None:
            return (*self._standard_local_continuation(
                request, output, calls, [_profile_tool_rejection("no_active_dataset") for _ in calls]
            ), None, False)
        dataset_id, mapping_version_id = scope.dataset_id, scope.mapping_version_id
        if pause_for_qualitative_consent and not include_approved_notes and any(
            call["name"] == QUALITATIVE_TOOL_NAME for call in calls
        ):
            try:
                context_field_count = self._validate_qualitative_tool(
                    dataset_id, mapping_version_id, next(call for call in calls if call["name"] == QUALITATIVE_TOOL_NAME)
                )
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
            else:
                raise _QualitativeConsentRequired(
                    sum(
                        entry.semantic_role is None
                        and entry.value_type == "categorical"
                        and entry.mentor_access == "allow_row_values_when_analysing_notes"
                        for entry in self.storage.mapping_entries(mapping_version_id)
                    ),
                    context_field_count,
                )
        deterministic: list[tuple[dict, dict]] = []
        qualitative: tuple[dict, Any] | None = None
        rejection_results: dict[str, dict] = {}
        deterministic_size = 0
        capability = QualitativeDisclosureCapability() if include_approved_notes else None
        deterministic_calls = 0
        qualitative_calls = 0
        for index, call in enumerate(calls, start=1):
            try:
                if index > MAX_ANALYSIS_CALLS:
                    raise _LocalToolRejected("analysis_call_limit_exceeded")
                if call["name"] == QUALITATIVE_TOOL_NAME:
                    qualitative_calls += 1
                    if qualitative_calls > 1:
                        raise _LocalToolRejected("analysis_call_limit_exceeded")
                    if capability is None:
                        self._validate_qualitative_tool(dataset_id, mapping_version_id, call)
                        raise _LocalToolRejected("qualitative_consent_required")
                    evidence = self._execute_qualitative_tool(
                        dataset_id, mapping_version_id, call, capability
                    )
                    qualitative = (call, evidence)
                else:
                    deterministic_calls += 1
                    if deterministic_calls > MAX_DETERMINISTIC_ANALYSIS_CALLS:
                        raise _LocalToolRejected("analysis_call_limit_exceeded")
                    result = self._execute_analysis_tool(dataset_id, mapping_version_id, call)
                    encoded_size = len(json.dumps(result, separators=(",", ":"), allow_nan=False))
                    if deterministic_size + encoded_size > MAX_DETERMINISTIC_OUTPUT_CHARS:
                        raise _LocalToolRejected("deterministic_result_budget_exceeded")
                    deterministic_size += encoded_size
                    deterministic.append((call, result))
            except _LocalToolRejected as error:
                rejection_results[call["call_id"]] = _profile_tool_rejection(error.reason)
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                reason = (
                    _qualitative_rejection_reason(error)
                    if call["name"] == QUALITATIVE_TOOL_NAME
                    else "invalid_analysis_arguments"
                )
                rejection_results[call["call_id"]] = _profile_tool_rejection(reason)

        tool_outputs = [
            {
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": json.dumps(
                    next((result for candidate, result in deterministic if candidate is call), rejection_results.get(call["call_id"], _profile_tool_rejection("invalid_analysis_arguments"))),
                    separators=(",", ":"),
                ),
            }
            for call in calls
            if call.get("name") != QUALITATIVE_TOOL_NAME or call["call_id"] in rejection_results
        ]
        raw_continuation_request = {
            **request,
            "input": [
                *request["input"],
                *(_input_item(item) for item in output),
                *tool_outputs,
            ],
            "tools": _raw_file_search_tools(request["tools"]),
        }
        leading_output = [*output, *tool_outputs]
        qualitative_call_ids = {
            call["call_id"] for call in calls if call.get("name") == QUALITATIVE_TOOL_NAME
        }
        replay_leading_output = [
            item for item in leading_output if item.get("call_id") not in qualitative_call_ids
        ]
        _validate_replay_protocol(leading_output, replay=False)
        origin_turn_number = len(self.storage.display_turns(thread_id)) + 1
        for call, result in deterministic:
            if call["call_id"] in rejection_results:
                continue
            if result.get("operation") == "inspect_dataset":
                continue
            evidence = self.storage.record_analysis_evidence(
                thread_id=thread_id,
                origin_turn_number=origin_turn_number,
                display_turn_number=origin_turn_number,
                dataset_id=dataset_id,
                mapping_version_id=mapping_version_id,
                operation=str(result["operation"]),
                schema_version=str(result["schema_version"]),
                arguments={"dataset_id": dataset_id},
                result=result,
            )
            self.storage.record_analysis_tool_output(
                thread_id, call["call_id"], evidence.id, result,
                arguments={"dataset_id": dataset_id, "mapping_version_id": mapping_version_id, "operation": result["operation"]},
            )
        if qualitative is None:
            response = self._responses_create(raw_continuation_request, "local_tool_continuation")
            return response, leading_output, replay_leading_output, raw_continuation_request, None, False

        qualitative_call, qualitative_evidence = qualitative
        safe_request = {
            **request,
            # The qualitative exchange has no valid replayable tool protocol once
            # its ephemeral output is gone. Citation/timestamp repair therefore
            # starts from durable history plus the visible draft answer only.
            "input": [*request["input"]],
            "tools": _raw_file_search_tools(request["tools"]),
        }
        try:
            continuation = continue_qualitative_model_transport(
                client=self.client,
                request=raw_continuation_request,
                call_id=qualitative_call["call_id"],
                evidence=qualitative_evidence,
            )
        except QualitativeTransportError as error:
            raise _ResponsesRequestError("qualitative_continuation", error) from None
        self.storage.record_qualitative_metadata(thread_id, origin_turn_number, continuation.metadata)
        return (
            _response_with_output(continuation, list(continuation.output)),
            [item for item in output if item.get("type") == "file_search_call"],
            [],
            safe_request,
            None,
            True,
        )

    def _standard_local_continuation(
        self, request: dict, output: list[dict], calls: list[dict], results: list[dict]
    ) -> tuple[Any, list[dict], list[dict], dict]:
        tool_outputs = [
            {"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)}
            for call, result in zip(calls, results, strict=True)
        ]
        continuation_request = {
            **request,
            "input": [*request["input"], *(_input_item(item) for item in output), *tool_outputs],
            "tools": _raw_file_search_tools(request["tools"]),
        }
        leading_output = [*output, *tool_outputs]
        _validate_replay_protocol(leading_output, replay=False)
        return self._responses_create(continuation_request, "local_tool_continuation"), leading_output, leading_output, continuation_request

    def _active_analysis_scope(self, thread_id: int) -> AnalysisScope | None:
        scope = self.storage.thread_dataset_scope(thread_id)
        if scope is None or scope.dataset_id is None:
            return None
        mapping, upgraded = ensure_current_auto_mapping(self.storage, scope.dataset_id)
        return AnalysisScope(scope.dataset_id, mapping.id, upgraded)

    def _execute_analysis_tool(self, dataset_id: str, mapping_version_id: int, call: dict) -> dict:
        arguments = _analysis_arguments(call)
        name = call["name"]
        if name == "inspect_dataset":
            if arguments:
                raise _LocalToolRejected("invalid_analysis_arguments")
            return _inspect_dataset_payload(self.storage, dataset_id, mapping_version_id)
        filters = _analysis_filters(arguments.pop("filters"))
        if name == "summarize_results":
            _require_exact_keys(arguments, set())
            return summarize_results(build_analysis_frame(self.storage, dataset_id, mapping_version_id, required_roles=_analysis_roles(self.storage, mapping_version_id), filters=filters))
        if name == "group_results":
            group_field_ids = arguments.pop("group_field_ids", None)
            _require_exact_keys(arguments, set())
            return group_results(build_analysis_frame(self.storage, dataset_id, mapping_version_id, required_roles=_analysis_roles(self.storage, mapping_version_id), filters=filters), group_field_ids)
        if name == "compare_groups":
            field_id, value_a, value_b = arguments.pop("field_id", None), arguments.pop("value_a", None), arguments.pop("value_b", None)
            _require_exact_keys(arguments, set())
            return compare_groups(build_analysis_frame(self.storage, dataset_id, mapping_version_id, required_roles=_analysis_roles(self.storage, mapping_version_id), filters=filters), field_id, value_a, value_b)
        if name == "analyze_mfe_mae":
            _require_exact_keys(arguments, set())
            mfe_roles = tuple(role for role in ("mfe", "mae") if role in _mapping_roles(self.storage, mapping_version_id))
            if not mfe_roles:
                raise _LocalToolRejected("required_metric_unavailable")
            primary_role = "mfe" if "mfe" in mfe_roles else "mae"
            mfe_frame = build_analysis_frame(self.storage, dataset_id, mapping_version_id, required_roles=(primary_role,), filters=filters)
            mae_frame = (
                build_analysis_frame(self.storage, dataset_id, mapping_version_id, required_roles=("mae",), filters=filters)
                if primary_role == "mfe" and "mae" in mfe_roles else None
            )
            return analyze_mfe_mae(mfe_frame, mae_frame=mae_frame)
        if name == "analyze_over_time":
            mode, window_size = arguments.pop("mode", None), arguments.pop("window_size", None)
            _require_exact_keys(arguments, set())
            available_roles = _mapping_roles(self.storage, mapping_version_id)
            if "trade_timestamp" not in available_roles:
                raise _LocalToolRejected("required_metric_unavailable")
            temporal_roles = tuple(role for role in ("trade_return", "trade_outcome", "trade_timestamp") if role in available_roles)
            return analyze_over_time(build_analysis_frame(self.storage, dataset_id, mapping_version_id, required_roles=temporal_roles, filters=filters, order_by="timestamp"), mode=mode, window_size=window_size)
        raise _LocalToolRejected("unsupported_tool")

    def _execute_qualitative_tool(
        self, dataset_id: str, mapping_version_id: int, call: dict, capability: QualitativeDisclosureCapability
    ) -> Any:
        arguments = _analysis_arguments(call)
        text_field_ids = arguments.pop("text_field_ids", None)
        context_field_ids = arguments.pop("context_field_ids", None)
        order_by = arguments.pop("order_by", None)
        filters = _analysis_filters(arguments.pop("filters"))
        _require_exact_keys(arguments, set())
        return read_text_evidence(
            self.storage, dataset_id, mapping_version_id,
            text_field_ids=text_field_ids,
            context_field_ids=context_field_ids,
            filters=filters,
            order_by=order_by,
            include_approved_notes=True,
            use_guard=capability,
        )

    def _validate_qualitative_tool(self, dataset_id: str, mapping_version_id: int, call: dict) -> int:
        arguments = _analysis_arguments(call)
        text_field_ids = arguments.pop("text_field_ids", None)
        context_field_ids = arguments.pop("context_field_ids", None)
        order_by = arguments.pop("order_by", None)
        filters = _analysis_filters(arguments.pop("filters"))
        _require_exact_keys(arguments, set())
        validate_text_evidence_request(
            self.storage, dataset_id, mapping_version_id,
            text_field_ids=text_field_ids, context_field_ids=context_field_ids, filters=filters, order_by=order_by,
        )
        return len(context_field_ids)

    def _execute_profile_tool(self, thread_id: int, question: str, call: dict) -> dict:
        call_id = call.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return _profile_tool_rejection("invalid_call_id")
        try:
            arguments = json.loads(call.get("arguments", ""))
            if not isinstance(arguments, dict):
                return _profile_tool_rejection("invalid_arguments")
            operation = arguments.get("operation")
            profile = ProfileService(self.storage)
            origin = {
                "origin_kind": "chat",
                "origin_thread_id": thread_id,
                "origin_turn_number": len(self.storage.display_turns(thread_id)) + 1,
                "tool_call_id": call_id,
            }
            if self.storage.profile_mutation_exists_for_origin(
                origin["origin_thread_id"], origin["origin_turn_number"]
            ):
                return _profile_tool_rejection("already_processed_turn")
            if operation == "save":
                if (
                    not EXPLICIT_PROFILE_WRITE.search(question)
                    or set(arguments) != PROFILE_TOOL_KEYS
                    or arguments["target_id"] is not None
                    or any(arguments[field] is None for field in PROFILE_WRITE_FIELDS)
                ):
                    return _profile_tool_rejection("unpermitted_or_invalid_write")
                existing = self.storage.profile_item_for_tool_call(call_id)
                provenance = arguments["provenance"]
                item = profile.create_item(
                    category=arguments["category"],
                    subject=arguments["subject"],
                    value=arguments["value"],
                    kind=arguments["kind"],
                    provenance=provenance,
                    state="confirmed",
                    **origin,
                )
                status = "already_saved" if existing is not None else "saved"
            elif operation == "propose":
                if (
                    not EXPLICIT_PROFILE_WRITE.search(question)
                    or set(arguments) != PROFILE_TOOL_KEYS
                    or arguments["target_id"] is not None
                    or any(arguments[field] is None for field in PROFILE_WRITE_FIELDS)
                ):
                    return _profile_tool_rejection("unpermitted_or_invalid_write")
                existing = self.storage.profile_item_for_tool_call(call_id)
                item = profile.propose_item(
                    category=arguments["category"],
                    subject=arguments["subject"],
                    value=arguments["value"],
                    kind=arguments["kind"],
                    **origin,
                )
                status = "already_proposed" if existing is not None else "proposed"
            elif operation in {"archive", "delete"}:
                target_id = arguments.get("target_id")
                if (
                    not EXPLICIT_PROFILE_FORGET.search(question)
                    or set(arguments) != PROFILE_TOOL_KEYS
                    or any(arguments[field] is not None for field in PROFILE_WRITE_FIELDS)
                    or not _question_names_profile_target(question, target_id)
                ):
                    return _profile_tool_rejection("explicit_target_required")
                existing_status = self.storage.profile_operation_status(call_id)
                status = profile.forget_item(
                    item_id=target_id,
                    operation=operation,
                    tool_call_id=call_id,
                    origin_thread_id=origin["origin_thread_id"],
                    origin_turn_number=origin["origin_turn_number"],
                )
                if existing_status is not None:
                    status = f"already_{status}"
                return {"status": status, "item_id": target_id}
            else:
                return _profile_tool_rejection("invalid_operation")
        except (json.JSONDecodeError, KeyError, TypeError, ProfileValidationError):
            return _profile_tool_rejection("invalid_arguments")
        return {"status": status, "item_id": item.id, "state": item.state, "provenance": item.provenance}

    def _request(
        self, thread_id: int, question: str, evaluation: EvaluationConfig
    ) -> tuple[dict, dict, str, bool, bool]:
        question = _question(question)
        vector_store_id = self.storage.vector_store_id()
        if vector_store_id is None:
            raise RuntimeError("Import the Jacob transcripts before starting a chat.")
        user_item = {"role": "user", "content": [{"type": "input_text", "text": question}]}
        effective_depth = _effective_research_depth(question, evaluation.research_depth)
        scope = self._active_analysis_scope(thread_id)
        replay_items, prior_empirical_evidence_reused = _dataset_bound_empirical_replay(
            self.storage, thread_id, self.storage.replay_items(thread_id), scope
        )
        _validate_replay_protocol(replay_items)
        confirmed_profile = self.storage.current_confirmed_profile_items()
        profile_context = ""
        context_mode = _profile_context_mode(question)
        field_state = None
        if context_mode == PROFILE_CONTEXT_FULL_PROFILE:
            full_profile = full_questionnaire_profile_context(confirmed_profile)
            profile_context = f"\n\n{full_profile.context}\nUse this only to answer about Theo's profile; it is not Jacob source material."
        elif context_mode == PROFILE_CONTEXT_STRATEGY:
            strategy_profile = strategy_profile_context(confirmed_profile)
            profile_context = (
                f"\n\n{strategy_profile.context}\n"
                "Use this only to personalise relevant strategy design; it is not Jacob source material."
            )
        elif context_mode == PROFILE_CONTEXT_RELEVANT:
            selection = select_profile_context(question, confirmed_profile)
            if selection.context:
                profile_context = (
                    "\n\nTrader Profile — user context, not source evidence:\n"
                    f"{selection.context}\n"
                    "Use this only to personalise relevant advice; it is not Jacob source material."
                )
            field_state = questionnaire_field_state(question, confirmed_profile)
        if field_state is not None:
            profile_context += f"\n\n{field_state.context}"
        source_tools = [] if context_mode == PROFILE_CONTEXT_FULL_PROFILE or (
            field_state is not None and not _explicit_profile_source_request(question)
        ) else [
            {
                "type": "file_search",
                "vector_store_ids": [vector_store_id],
                "max_num_results": FILE_SEARCH_RESULT_BUDGETS[effective_depth],
            }
        ]
        analysis_context = ""
        analysis_tools: list[dict] = []
        if scope is not None:
            dataset_id, mapping_version_id = scope.dataset_id, scope.mapping_version_id
            analysis_context = (
                "\n\nLocal Backtest Dataset — server-owned user empirical context, not source evidence:\n"
                f"dataset_id={dataset_id}; mapping_version_id={mapping_version_id}; "
                f"fields={json.dumps(model_mapping_context(self.storage, mapping_version_id), separators=(',', ':'))}\n"
                "Use only the available local analysis tools for this dataset. Field identifiers are opaque; never request headers, paths, rows, SQL, Python, or unlisted fields. These mapping details are internal tool context: never mention mapping versions, field IDs, semantic roles, or access flags in a normal answer. Explain any unavailable analysis in plain language. Earlier chat text may describe a replaced dataset; it is not empirical evidence for this scope."
            )
            analysis_tools = ANALYSIS_TOOLS
        return user_item, {
            "model": self.model,
            "instructions": (
                f"{MENTOR_INSTRUCTIONS}\n\n{PROFILE_TOOL_INSTRUCTIONS}\n\n"
                f"{ANALYSIS_TOOL_INSTRUCTIONS}\n\n{_research_instruction(effective_depth)}{profile_context}{analysis_context}"
            ),
            "input": [
                *(_input_item(item) for item in replay_items),
                user_item,
            ],
            "tools": [*source_tools, PROFILE_TOOL, *analysis_tools],
            "include": ["reasoning.encrypted_content", "file_search_call.results"],
            "reasoning": evaluation.request_value(),
            "context_management": [{"type": "compaction", "compact_threshold": COMPACTION_TOKEN_THRESHOLD}],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }, effective_depth, prior_empirical_evidence_reused, bool(scope and scope.auto_mapping_policy_upgraded)

    def _finalize(
        self,
        thread_id: int,
        user_item: dict,
        response: Any,
        evaluation: EvaluationConfig,
        effective_depth: str,
        started_at: float,
        evidence_output: list[dict] | None = None,
        draft_response: Any | None = None,
        leading_output: list[dict] | None = None,
        replay_leading_output: list[dict] | None = None,
        profile_update: dict[str, str] | None = None,
        qualitative_exchange: bool = False,
        dataset_attachment_id: str | None = None,
        prior_empirical_evidence_reused: bool = False,
        auto_mapping_policy_upgraded: bool = False,
    ) -> Answer:
        response_output = [_as_dict(item) for item in response.output]
        historic_response_output = _qualitative_historic_items(response_output) if qualitative_exchange else response_output
        output = [*(leading_output or []), *historic_response_output]
        replay_output = [*(replay_leading_output if replay_leading_output is not None else (leading_output or [])), *historic_response_output]
        _validate_replay_protocol(replay_output)
        if dataset_attachment_id is not None:
            scope = self.storage.thread_dataset_scope(thread_id)
            if scope is None or scope.dataset_id != dataset_attachment_id:
                raise ValueError("The attached backtest is not active for this conversation.")
        raw_positions = self.storage.append_thread_items(thread_id, [user_item, *output])
        compaction_index = next((index for index, item in enumerate(replay_output) if item.get("type") == "compaction"), None)
        if compaction_index is None:
            if replay_output != output:
                self.storage.replace_replay_items(thread_id, [user_item, *replay_output])
            else:
                self.storage.append_replay_items(thread_id, [user_item, *replay_output])
        else:
            self.storage.replace_replay_items(
                thread_id,
                replay_output[compaction_index:],
            )
        answer = _answer(
            output,
            evidence_output=evidence_output,
            incomplete_reason=_incomplete_reason(response),
        )
        question = user_item["content"][0]["text"]
        if draft_response is not None and _has_unsupported_exact_timestamp(question, answer):
            answer = Answer(
                text=EXACT_SOURCE_WARNING,
                citations=answer.citations,
                evidence=answer.evidence,
                diagnostics=answer.diagnostics,
                incomplete_reason=answer.incomplete_reason,
            )
        elif draft_response is not None and not answer.citations:
            answer = Answer(
                text=f"{answer.text}{CITATION_WARNING}",
                citations=answer.citations,
                evidence=answer.evidence,
                diagnostics=answer.diagnostics,
                incomplete_reason=answer.incomplete_reason,
            )
        diagnostics = _diagnostics(
            response,
            self.model,
            evaluation,
            effective_depth,
            evidence_output or output,
            answer,
            started_at,
            native_compaction_applied=compaction_index is not None,
            draft_response=draft_response,
            prior_empirical_evidence_reused=prior_empirical_evidence_reused,
            auto_mapping_policy_upgraded=auto_mapping_policy_upgraded,
        )
        self.storage.record_response_diagnostics(
            thread_id, diagnostics.response_id, diagnostics.__dict__
        )
        answer = Answer(
            text=answer.text,
            citations=answer.citations,
            evidence=answer.evidence,
            diagnostics=diagnostics,
            incomplete_reason=answer.incomplete_reason,
            profile_update=profile_update,
        )
        self.storage.record_display_turn(
            thread_id,
            user_text=user_item["content"][0]["text"],
            answer_markdown=answer.text,
            citations=[citation.__dict__ for citation in answer.citations],
            evidence=[evidence.__dict__ for evidence in answer.evidence],
            diagnostics=diagnostics.__dict__,
            response_id=diagnostics.response_id,
            status=diagnostics.status,
            incomplete_reason=answer.incomplete_reason,
            profile_update=profile_update,
            dataset_attachment_id=dataset_attachment_id,
            raw_start_position=None if raw_positions is None else raw_positions[0],
            raw_end_position=None if raw_positions is None else raw_positions[1],
        )
        return answer


def _question(question: str) -> str:
    question = question.strip()
    if not question:
        raise ValueError("Question cannot be blank.")
    if len(question) > 8_000:
        raise ValueError("Question is too long.")
    return question


class _LocalToolRejected(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class _QualitativeConsentRequired(Exception):
    """A stream can safely pause before a raw qualitative disclosure."""

    def __init__(self, field_count: int = 1, context_field_count: int = 0):
        self.field_count = field_count
        self.context_field_count = context_field_count


class _ReplayProtocolError(ValueError):
    """Persisted local tool replay does not form a valid Responses protocol."""


class _ResponsesRequestError(RuntimeError):
    """A provider request failed after safe diagnostic metadata was logged."""

    def __init__(self, stage: str, cause: Exception):
        super().__init__(str(cause))
        self.stage = stage


def _analysis_arguments(call: dict) -> dict[str, object]:
    try:
        arguments = json.loads(call.get("arguments", ""))
    except json.JSONDecodeError as error:
        raise _LocalToolRejected("invalid_analysis_arguments") from error
    if not isinstance(arguments, dict):
        raise _LocalToolRejected("invalid_analysis_arguments")
    return dict(arguments)


def _analysis_filters(value: object) -> tuple[AnalysisFilter, ...]:
    if not isinstance(value, list) or len(value) > 8:
        raise _LocalToolRejected("invalid_analysis_arguments")
    filters: list[AnalysisFilter] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"field_id", "operator", "value"}:
            raise _LocalToolRejected("invalid_analysis_arguments")
        if not isinstance(item["field_id"], str) or not isinstance(item["operator"], str):
            raise _LocalToolRejected("invalid_analysis_arguments")
        filters.append(AnalysisFilter(item["field_id"], item["operator"], item["value"]))
    return tuple(filters)


def _require_exact_keys(arguments: dict[str, object], expected: set[str]) -> None:
    if set(arguments) != expected:
        raise _LocalToolRejected("invalid_analysis_arguments")


def _mapping_roles(storage: Storage, mapping_version_id: int) -> set[str]:
    return {entry.semantic_role for entry in storage.mapping_entries(mapping_version_id) if entry.semantic_role is not None}


def _analysis_roles(storage: Storage, mapping_version_id: int) -> tuple[str, ...]:
    roles = tuple(role for role in ("trade_return", "trade_outcome") if role in _mapping_roles(storage, mapping_version_id))
    if not roles:
        raise _LocalToolRejected("required_metric_unavailable")
    return roles


def _inspect_dataset_payload(storage: Storage, dataset_id: str, mapping_version_id: int) -> dict[str, object]:
    dataset = storage.dataset(dataset_id)
    mapping = storage.mapping_version(mapping_version_id)
    if dataset is None or mapping is None or mapping.status != "confirmed" or mapping.dataset_id != dataset_id:
        raise _LocalToolRejected("no_active_dataset")
    fields = model_mapping_context(storage, mapping_version_id)
    return {
        "provenance": "USER_EMPIRICAL_EVIDENCE",
        "operation": "inspect_dataset",
        "dataset_id": dataset_id,
        "mapping_version_id": mapping_version_id,
        "source_rows": dataset.source_row_count,
        "fields": fields,
        "available_operations": [
            "summarize_results", "group_results", "compare_groups", "analyze_mfe_mae",
            "analyze_over_time", "read_text_evidence",
        ],
        "limitations": ["schema_and_mapping_only_no_raw_cells"],
    }


def _is_strategy_design_question(question: str) -> bool:
    normalized = " ".join(question.casefold().replace("-", " ").split())
    action = re.search(r"\b(?:build|building|design|designing|develop|developing|development|create|creating|construct|constructing|work out|figure out)\b", normalized)
    artifact = re.search(r"\b(?:trading )?(?:strategy|system|model|playbook)\b", normalized)
    personal = re.search(r"\b(?:for me|around me|fits me|fit me|my |my profile|trader profile|from scratch|i should)\b", normalized)
    return bool(action and artifact and personal) or bool(
        re.search(r"\bwhat kind of (?:trading )?(?:strategy|system|model|playbook)\b.*\bfit(?:s)? me\b", normalized)
    )


def _profile_context_mode(question: str) -> str:
    if _is_strategy_design_question(question):
        return PROFILE_CONTEXT_STRATEGY
    if _is_full_profile_question(question):
        return PROFILE_CONTEXT_FULL_PROFILE
    normalized = question.casefold()
    if _explicit_profile_source_request(question) and not any(signal in normalized for signal in ("my ", "for me", "my profile", "trader profile")):
        return PROFILE_CONTEXT_NONE
    return PROFILE_CONTEXT_RELEVANT


def _is_full_profile_question(question: str) -> bool:
    normalized = " ".join(question.casefold().split())
    profile_reference = any(phrase in normalized for phrase in ("trader profile", "my profile", "profile say"))
    explicit_full_profile = "full trader profile" in normalized or "full profile" in normalized
    inspection = any(
        phrase in normalized
        for phrase in (
            "what do you know about me",
            "summarize my profile",
            "summarise my profile",
            "what have i answered",
            "left unanswered",
            "left blank",
            "profile questions are unresolved",
            "what did i put for my trading preferences",
            "what does my trader profile say",
            "what's in my trader profile",
            "what is in my trader profile",
        )
    )
    summary = profile_reference and ("summarize" in normalized or "summarise" in normalized)
    return explicit_full_profile or inspection or summary


def _explicit_profile_source_request(question: str) -> bool:
    normalized = question.casefold()
    return any(term in normalized for term in ("jacob", "source", "transcript", "citation", "according to"))


def _as_dict(item: Any) -> dict:
    if isinstance(item, dict):
        return item
    return item.model_dump(mode="json")


def _response_with_output(response: Any, output: list[dict]) -> Any:
    if hasattr(response, "model_copy"):
        return response.model_copy(update={"output": output})
    return SimpleNamespace(
        output=output,
        status=_field(response, "status"),
        id=_field(response, "id"),
        model=_field(response, "model"),
        usage=_field(response, "usage"),
        incomplete_details=_field(response, "incomplete_details"),
    )


def _input_item(item: dict) -> dict:
    """Keep full API output locally but omit fields the input endpoint rejects."""
    return {key: value for key, value in item.items() if key not in {"status", "created_by"}}


def _validate_replay_protocol(items: list[dict], *, replay: bool = True) -> None:
    """Reject malformed local tool replay without inspecting user-provided values."""
    calls: dict[str, str] = {}
    outputs: set[str] = set()
    for item in items:
        item_type = item.get("type")
        if item_type == "function_call":
            call_id = item.get("call_id")
            name = item.get("name")
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                raise _ReplayProtocolError("malformed_local_call")
            if call_id in calls:
                raise _ReplayProtocolError("duplicate_local_call")
            calls[call_id] = name
        elif item_type == "function_call_output":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise _ReplayProtocolError("malformed_local_output")
            if call_id in outputs:
                raise _ReplayProtocolError("duplicate_local_output")
            if call_id not in calls:
                raise _ReplayProtocolError("orphan_local_output")
            outputs.add(call_id)
    if replay and QUALITATIVE_TOOL_NAME in calls.values():
        raise _ReplayProtocolError("qualitative_call_in_replay")
    missing_outputs = set(calls) - outputs
    if replay and missing_outputs:
        raise _ReplayProtocolError("unanswered_local_call")
    if not replay and any(calls[call_id] != QUALITATIVE_TOOL_NAME for call_id in missing_outputs):
        raise _ReplayProtocolError("unanswered_local_call")


def _dataset_bound_empirical_replay(
    storage: Storage, thread_id: int, replay_items: list[dict], scope: AnalysisScope | None
) -> tuple[list[dict], bool]:
    """Keep only persisted empirical tool outputs that match the active immutable scope."""
    persisted = {
        json.dumps(item["output"], separators=(",", ":"), allow_nan=False)
        for item in storage.analysis_tool_outputs(thread_id)
    }
    stale_call_ids: set[str] = set()
    reused = False
    for item in replay_items:
        if item.get("type") != "function_call_output" or not isinstance(item.get("call_id"), str):
            continue
        encoded = item.get("output")
        try:
            result = json.loads(encoded) if isinstance(encoded, str) else None
        except json.JSONDecodeError:
            continue
        if not isinstance(result, dict) or result.get("provenance") != "USER_EMPIRICAL_EVIDENCE":
            continue
        current = (
            scope is not None
            and result.get("dataset_id") == scope.dataset_id
            and result.get("mapping_version_id") == scope.mapping_version_id
            and result.get("schema_version") == ANALYSIS_SCHEMA_VERSION
            and json.dumps(result, separators=(",", ":"), allow_nan=False) in persisted
        )
        if current:
            reused = True
        else:
            stale_call_ids.add(item["call_id"])
    if not stale_call_ids:
        return replay_items, reused
    return [
        item for item in replay_items
        if not (
            item.get("call_id") in stale_call_ids
            and (item.get("type") == "function_call_output" or item.get("name") in ANALYSIS_TOOL_NAMES)
        )
    ], reused


def _qualitative_historic_items(items: list[dict]) -> list[dict]:
    """Return the only response item eligible after a qualitative exchange.

    This is origin-based: callers use it only for a turn known to have disclosed
    qualitative evidence.  The visible terminal assistant message survives, but
    every hidden item from that exchange (reasoning and local tool protocol) is
    ephemeral and never enters a later Responses replay.
    """
    return [
        item for item in items
        if item.get("type") == "message" and item.get("role") == "assistant"
    ]


def _raw_file_search_tools(tools: list[dict]) -> list[dict]:
    return [tool for tool in tools if tool.get("type") == "file_search"]


def _profile_tool_rejection(reason: str) -> dict[str, str]:
    return {"status": "rejected", "reason": reason}


def _qualitative_rejection_reason(error: Exception) -> str:
    return {
        "qualitative text field is not eligible": "qualitative_text_not_eligible",
        "qualitative context field is not eligible": "qualitative_context_not_eligible",
    }.get(str(error), "invalid_analysis_arguments")


def _safe_error_classification(error: Exception) -> str:
    if isinstance(error, _ReplayProtocolError):
        return "invalid_tool_protocol"
    if isinstance(error, sqlite3.Error):
        return "local_persistence_error"
    if isinstance(error, (ValueError, TypeError, json.JSONDecodeError)):
        return "local_tool_validation"
    return "stream_error"


def _log_safe_responses_error(stage: str, error: Exception) -> None:
    """Log protocol-safe provider metadata, never prompt or tool payloads."""
    status, error_type, code, param, request_id = safe_provider_error_details(error)
    LOGGER.warning(
        "Responses request failed stage=%s class=%s status=%s type=%s code=%s param=%s request_id=%s",
        stage,
        type(error).__name__,
        status, error_type, code, param, request_id,
    )


def _profile_update(result: dict) -> dict[str, str] | None:
    """Expose only a completed local change, never profile content or tool data."""
    status = result.get("status")
    return {"kind": status} if status in {"saved", "proposed", "archived", "deleted"} else None


def _question_names_profile_target(question: str, target_id: Any) -> bool:
    if not isinstance(target_id, int) or target_id <= 0:
        return False
    match = EXPLICIT_PROFILE_TARGET.search(question)
    return match is not None and int(match.group(1)) == target_id


def _answer(
    output: list[dict],
    diagnostics: ResponseDiagnostics | None = None,
    evidence_output: list[dict] | None = None,
    incomplete_reason: str | None = None,
) -> Answer:
    text_parts: list[str] = []
    citations: list[Citation] = []
    evidence: list[Evidence] = []
    for item in evidence_output or output:
        if item.get("type") == "file_search_call":
            for result in item.get("results") or []:
                attributes = result.get("attributes") or {}
                evidence.append(
                    Evidence(
                        file_id=result["file_id"],
                        filename=result.get("filename", "Unknown source"),
                        excerpt=result.get("text", ""),
                        year=attributes.get("year"),
                        metadata={str(key): str(value) for key, value in attributes.items()},
                    )
                )
    for item in output:
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        for content in item.get("content") or []:
            if content.get("type") != "output_text":
                continue
            text_parts.append(content.get("text", ""))
            for annotation in content.get("annotations") or []:
                if annotation.get("type") == "file_citation":
                    citation = Citation(
                        file_id=annotation["file_id"],
                        filename=annotation.get("filename", "Unknown source"),
                    )
                    if citation not in citations:
                        citations.append(citation)
    return Answer(
        text="".join(text_parts),
        citations=citations,
        evidence=evidence,
        diagnostics=diagnostics,
        incomplete_reason=incomplete_reason,
    )


def _has_direct_source_claim(text: str) -> bool:
    return bool(DIRECT_SOURCE_CLAIM.search(text))


def _has_unsupported_exact_timestamp(question: str, answer: Answer) -> bool:
    if not EXACT_SOURCE_REQUEST.search(question):
        return False
    timestamps = []
    for match in CLOCK_TIME.finditer(answer.text):
        start = int(match.group(1)) * 60 + int(match.group(2))
        end = start if match.group(3) is None else int(match.group(3)) * 60 + int(match.group(4))
        timestamps.append((start, end))
    if not timestamps:
        return False
    cited_file_ids = {citation.file_id for citation in answer.citations}
    ranges = [
        (float(match.group(1)), float(match.group(2)))
        for evidence in answer.evidence
        if evidence.file_id in cited_file_ids
        for match in EVIDENCE_TIME_RANGE.finditer(evidence.excerpt)
    ]
    return any(
        not any(start >= evidence_start and end <= evidence_end for evidence_start, evidence_end in ranges)
        for start, end in timestamps
    )


def _incomplete_reason(response: Any) -> str | None:
    if _field(response, "status") != "incomplete":
        return None
    return _field(_field(response, "incomplete_details"), "reason") or "unknown"


def _diagnostics(
    response: Any,
    model: str,
    evaluation: EvaluationConfig,
    effective_depth: str,
    output: list[dict],
    answer: Answer,
    started_at: float,
    native_compaction_applied: bool,
    draft_response: Any | None = None,
    prior_empirical_evidence_reused: bool = False,
    auto_mapping_policy_upgraded: bool = False,
) -> ResponseDiagnostics:
    responses = [response] if draft_response is None else [draft_response, response]
    input_tokens = _usage_total(responses, "input_tokens")
    cached_input_tokens = _usage_total(responses, "cached_tokens", "input_tokens_details")
    cache_write_tokens = _usage_total(responses, "cache_write_tokens", "input_tokens_details")
    output_tokens = _usage_total(responses, "output_tokens")
    response_model = str(_field(response, "model") or model)
    file_search_calls, file_search_queries = _file_search_details(output)
    analysis = _analysis_tool_details(output)
    estimate = _estimate_text_cost(
        response_model, input_tokens, cached_input_tokens, cache_write_tokens or 0, output_tokens
    )
    return ResponseDiagnostics(
        response_id=str(_field(response, "id") or f"local-{uuid4()}"),
        model=response_model,
        status=str(_field(response, "status") or "completed"),
        reasoning_effort=evaluation.reasoning_effort,
        reasoning_mode=evaluation.reasoning_mode,
        requested_research_depth=evaluation.research_depth,
        effective_research_depth=effective_depth,
        file_search_calls=file_search_calls,
        file_search_queries=file_search_queries,
        returned_evidence_count=len(answer.evidence),
        cited_evidence_count=len(answer.citations),
        file_search_cost_status="known per-call charge; vector storage and other platform charges excluded",
        latency_ms=round((perf_counter() - started_at) * 1_000),
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=_usage_total(responses, "reasoning_tokens", "output_tokens_details"),
        total_tokens=_usage_total(responses, "total_tokens"),
        estimated_text_cost_usd=estimate,
        known_file_search_call_cost_usd=round(file_search_calls * FILE_SEARCH_CALL_COST_USD, 6),
        native_compaction_applied=native_compaction_applied,
        analysis_calls=analysis["calls"],
        analysis_operations=analysis["operations"],
        deterministic_result_chars=analysis["deterministic_result_chars"],
        qualitative_calls=analysis["qualitative_calls"],
        analysis_batch_status=analysis["status"],
        prior_empirical_evidence_reused=prior_empirical_evidence_reused,
        auto_mapping_policy_upgraded=auto_mapping_policy_upgraded,
    )


def _analysis_tool_details(output: list[dict]) -> dict[str, object]:
    calls = [item for item in output if item.get("type") == "function_call" and item.get("name") in ANALYSIS_TOOL_NAMES]
    if not calls:
        return {
            "calls": {"requested": 0, "executed": 0, "rejected": 0},
            "operations": [],
            "deterministic_result_chars": 0,
            "qualitative_calls": 0,
            "status": "not_requested",
        }
    outputs = {
        item.get("call_id"): item.get("output")
        for item in output
        if item.get("type") == "function_call_output" and isinstance(item.get("call_id"), str)
    }
    executed = 0
    rejected = 0
    deterministic_result_chars = 0
    for call in calls:
        encoded = outputs.get(call.get("call_id"))
        try:
            result = json.loads(encoded) if isinstance(encoded, str) else None
        except json.JSONDecodeError:
            result = None
        if isinstance(result, dict) and result.get("provenance") == "USER_EMPIRICAL_EVIDENCE":
            executed += 1
            if call.get("name") != QUALITATIVE_TOOL_NAME:
                deterministic_result_chars += len(encoded)
        elif isinstance(result, dict) and result.get("status") == "rejected":
            rejected += 1
    return {
        "calls": {"requested": len(calls), "executed": executed, "rejected": rejected},
        "operations": list(dict.fromkeys(str(call["name"]) for call in calls)),
        "deterministic_result_chars": deterministic_result_chars,
        "qualitative_calls": sum(call.get("name") == QUALITATIVE_TOOL_NAME for call in calls),
        "status": "complete" if rejected == 0 else "partial" if executed else "rejected",
    }


def _usage_total(responses: list[Any], field: str, parent: str | None = None) -> int | None:
    values = [
        _int_or_none(_field(_field(response, "usage") if parent is None else _field(_field(response, "usage"), parent), field))
        for response in responses
    ]
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _estimate_text_cost(
    model: str,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    cache_write_tokens: int,
    output_tokens: int | None,
) -> float | None:
    if model != "gpt-5.6-sol" or input_tokens is None or output_tokens is None:
        return None
    uncached_input = max(input_tokens - (cached_input_tokens or 0) - cache_write_tokens, 0)
    estimate = (
        uncached_input * 5 / 1_000_000
        + (cached_input_tokens or 0) * 0.5 / 1_000_000
        + cache_write_tokens * 6.25 / 1_000_000
        + output_tokens * 30 / 1_000_000
    )
    return round(estimate, 6)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _effective_research_depth(question: str, requested_depth: str) -> str:
    if requested_depth != "auto":
        return requested_depth
    normalized = question.casefold()
    if re.search(r"\b(all|every|everything|complete|exhaustive)\b|exact list|full mapping|compare all", normalized):
        return "exhaustive"
    if re.search(r"\b(verify|verification|compare|comparison|difference|differences|different|relationship|why)\b", normalized):
        return "deep"
    return "normal"


def _research_instruction(depth: str) -> str:
    if depth == "normal":
        policy = "Use one focused native File Search when fresh evidence is needed; continue only if evidence is insufficient."
    elif depth == "deep":
        policy = "Use multiple model-chosen native File Search passes when useful, varying queries or source angles."
    else:
        policy = (
            "Research a candidate answer, then use a complementary native File Search pass to find omissions, exceptions, "
            "alternate timeframes, or related lessons before claiming completeness. Do not exceed four passes."
        )
    return f"Research depth: {depth.title()}. {policy} This depth controls research only; it does not change reasoning effort or mode."


def _file_search_details(output: list[dict]) -> tuple[int, list[str]]:
    searches = [item for item in output if item.get("type") == "file_search_call"]
    return len(searches), [
        query
        for item in searches
        for query in item.get("queries") or []
        if isinstance(query, str)
    ]
