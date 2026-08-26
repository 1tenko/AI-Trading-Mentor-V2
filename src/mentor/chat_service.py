"""Grounded, stateless Responses API chat for the Phase 1 proof."""

from dataclasses import dataclass
import json
import logging
import re
from time import perf_counter
from typing import Any
from types import SimpleNamespace
from uuid import uuid4

from mentor.profile import ProfileService, ProfileValidationError, select_profile_context, strategy_profile_context
from mentor.prompts import MENTOR_INSTRUCTIONS, PROFILE_TOOL_INSTRUCTIONS
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


class ChatService:
    def __init__(self, storage: Storage, client: Any, model: str = "gpt-5.6-sol"):
        self.storage = storage
        self.client = client
        self.model = model

    def reply(
        self,
        thread_id: int,
        question: str,
        evaluation: EvaluationConfig = DEFAULT_EVALUATION_CONFIG,
    ) -> Answer:
        user_item, request, effective_depth = self._request(thread_id, question, evaluation)
        started_at = perf_counter()
        response = self.client.responses.create(**request)
        response, leading_output, response_request, profile_update = self._profile_continued_response(
            thread_id, request, response, user_item["content"][0]["text"]
        )
        response, evidence_output, draft_response = self._citation_repaired_response(
            response_request, response, user_item["content"][0]["text"]
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
            profile_update=profile_update,
        )

    def stream_reply(
        self,
        thread_id: int,
        question: str,
        evaluation: EvaluationConfig = DEFAULT_EVALUATION_CONFIG,
    ):
        user_item, request, effective_depth = self._request(thread_id, question, evaluation)
        started_at = perf_counter()
        try:
            stream = self.client.responses.create(**request, stream=True)
            for event in stream:
                if event.type == "response.output_text.delta":
                    yield StreamEvent("delta", event.delta)
                elif event.type in {"response.completed", "response.incomplete"}:
                    response, leading_output, response_request, profile_update = self._profile_continued_response(
                        thread_id, request, event.response, user_item["content"][0]["text"]
                    )
                    response, evidence_output, draft_response = self._citation_repaired_response(
                        response_request, response, user_item["content"][0]["text"]
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
                        profile_update=profile_update,
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
                    yield StreamEvent("error", error="The mentor request failed. Try again.")
                    return
        except Exception as error:
            LOGGER.warning("OpenAI stream raised %s", type(error).__name__)
            yield StreamEvent("error", error="The mentor request failed. Try again.")
            return
        LOGGER.warning("OpenAI stream ended without a terminal response event")
        yield StreamEvent("error", error="The mentor stream ended before returning a usable response. Try again.")

    def _citation_repaired_response(
        self, request: dict, response: Any, question: str
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
        repair_request = {
            **request,
            "instructions": repair_instructions,
            "input": [
                *request["input"],
                *(_input_item(item) for item in draft_output),
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Please repair the citations now."}],
                },
            ],
            "tools": _raw_file_search_tools(request["tools"]),
        }
        if needs_timestamp_repair:
            repair_request["tool_choice"] = {"type": "file_search"}
        repaired = self.client.responses.create(**repair_request)
        return repaired, [*draft_output, *(_as_dict(item) for item in repaired.output)], response

    def _profile_continued_response(
        self, thread_id: int, request: dict, response: Any, question: str
    ) -> tuple[Any, list[dict], dict, dict[str, str] | None]:
        output = [_as_dict(item) for item in response.output]
        calls = [item for item in output if item.get("type") == "function_call"]
        if not calls:
            return response, [], request, None
        if any(not isinstance(call.get("call_id"), str) or not call["call_id"] for call in calls):
            LOGGER.warning("Profile function call missing a usable call id")
            safe_output = [item for item in output if item.get("type") != "function_call"]
            if not any(item.get("type") == "message" and item.get("role") == "assistant" for item in safe_output):
                safe_output.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "I could not update that profile item. Please try again.",
                            }
                        ],
                    }
                )
            return _response_with_output(response, safe_output), [], request, None
        if len(calls) > 1:
            results = [_profile_tool_rejection("multiple_function_calls") for _ in calls]
        else:
            call = calls[0]
            results = [
                self._execute_profile_tool(thread_id, question, call)
                if call.get("name") == PROFILE_TOOL_NAME
                else _profile_tool_rejection("unsupported_tool")
            ]
        tool_outputs = [
            {"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)}
            for call, result in zip(calls, results, strict=True)
        ]
        continuation_request = {
            **request,
            "input": [
                *request["input"],
                *(_input_item(item) for item in output),
                *tool_outputs,
            ],
            "tools": _raw_file_search_tools(request["tools"]),
        }
        return (
            self.client.responses.create(**continuation_request),
            [*output, *tool_outputs],
            continuation_request,
            _profile_update(results[0]) if len(results) == 1 else None,
        )

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
    ) -> tuple[dict, dict, str]:
        question = _question(question)
        vector_store_id = self.storage.vector_store_id()
        if vector_store_id is None:
            raise RuntimeError("Import the Jacob transcripts before starting a chat.")
        user_item = {"role": "user", "content": [{"type": "input_text", "text": question}]}
        effective_depth = _effective_research_depth(question, evaluation.research_depth)
        replay_items = self.storage.replay_items(thread_id)
        confirmed_profile = self.storage.current_confirmed_profile_items()
        profile_context = ""
        if _is_strategy_design_question(question):
            strategy_profile = strategy_profile_context(confirmed_profile)
            if strategy_profile.context:
                profile_context = (
                    f"\n\n{strategy_profile.context}\n"
                    "Use this only to personalise relevant strategy design; it is not Jacob source material."
                )
        else:
            selection = select_profile_context(question, confirmed_profile)
            if selection.context:
                profile_context = (
                    "\n\nTrader Profile — user context, not source evidence:\n"
                    f"{selection.context}\n"
                    "Use this only to personalise relevant advice; it is not Jacob source material."
                )
        return user_item, {
            "model": self.model,
            "instructions": (
                f"{MENTOR_INSTRUCTIONS}\n\n{PROFILE_TOOL_INSTRUCTIONS}\n\n"
                f"{_research_instruction(effective_depth)}{profile_context}"
            ),
            "input": [
                *(_input_item(item) for item in replay_items),
                user_item,
            ],
            "tools": [
                {
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id],
                    "max_num_results": FILE_SEARCH_RESULT_BUDGETS[effective_depth],
                },
                PROFILE_TOOL,
            ],
            "include": ["reasoning.encrypted_content", "file_search_call.results"],
            "reasoning": evaluation.request_value(),
            "context_management": [{"type": "compaction", "compact_threshold": COMPACTION_TOKEN_THRESHOLD}],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }, effective_depth

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
        profile_update: dict[str, str] | None = None,
    ) -> Answer:
        output = [*(leading_output or []), *(_as_dict(item) for item in response.output)]
        raw_positions = self.storage.append_thread_items(thread_id, [user_item, *output])
        compaction_index = next((index for index, item in enumerate(output) if item.get("type") == "compaction"), None)
        if compaction_index is None:
            self.storage.append_replay_items(thread_id, [user_item, *output])
        else:
            self.storage.replace_replay_items(
                thread_id,
                output[compaction_index:],
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


def _is_strategy_design_question(question: str) -> bool:
    normalized = question.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "build my strategy",
            "build a strategy",
            "design my strategy",
            "design a strategy",
            "develop my strategy",
            "develop a strategy",
            "what kind of system fits me",
        )
    )


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


def _raw_file_search_tools(tools: list[dict]) -> list[dict]:
    return [tool for tool in tools if tool.get("type") == "file_search"]


def _profile_tool_rejection(reason: str) -> dict[str, str]:
    return {"status": "rejected", "reason": reason}


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
) -> ResponseDiagnostics:
    responses = [response] if draft_response is None else [draft_response, response]
    input_tokens = _usage_total(responses, "input_tokens")
    cached_input_tokens = _usage_total(responses, "cached_tokens", "input_tokens_details")
    cache_write_tokens = _usage_total(responses, "cache_write_tokens", "input_tokens_details")
    output_tokens = _usage_total(responses, "output_tokens")
    response_model = str(_field(response, "model") or model)
    file_search_calls, file_search_queries = _file_search_details(output)
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
    )


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
