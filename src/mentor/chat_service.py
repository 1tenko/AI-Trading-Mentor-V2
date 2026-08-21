"""Grounded, stateless Responses API chat for the Phase 1 proof."""

from dataclasses import dataclass
import logging
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from mentor.prompts import MENTOR_INSTRUCTIONS
from mentor.storage import Storage


LOGGER = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 25_000
COMPACTION_TOKEN_THRESHOLD = 50_000
FILE_SEARCH_RESULT_BUDGETS = {"normal": 8, "deep": 20, "exhaustive": 20}
FILE_SEARCH_CALL_COST_USD = 0.0025
SUPPORTED_REASONING_EFFORTS = frozenset({"high", "xhigh", "max"})
SUPPORTED_REASONING_MODES = frozenset({"standard", "pro"})
SUPPORTED_RESEARCH_DEPTHS = frozenset({"auto", "normal", "deep", "exhaustive"})


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
        return self._finalize(thread_id, user_item, response, evaluation, effective_depth, started_at)

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
                    answer = self._finalize(
                        thread_id, user_item, event.response, evaluation, effective_depth, started_at
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
        return user_item, {
            "model": self.model,
            "instructions": f"{MENTOR_INSTRUCTIONS}\n\n{_research_instruction(effective_depth)}",
            "input": [
                *(_input_item(item) for item in replay_items),
                user_item,
            ],
            "tools": [{
                "type": "file_search",
                "vector_store_ids": [vector_store_id],
                "max_num_results": FILE_SEARCH_RESULT_BUDGETS[effective_depth],
            }],
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
    ) -> Answer:
        output = [_as_dict(item) for item in response.output]
        raw_positions = self.storage.append_thread_items(thread_id, [user_item, *output])
        compaction_index = next((index for index, item in enumerate(output) if item.get("type") == "compaction"), None)
        if compaction_index is None:
            self.storage.append_replay_items(thread_id, [user_item, *output])
        else:
            self.storage.replace_replay_items(
                thread_id,
                output[compaction_index:],
            )
        answer = _answer(output, incomplete_reason=_incomplete_reason(response))
        diagnostics = _diagnostics(
            response, self.model, evaluation, effective_depth, output, answer, started_at,
            native_compaction_applied=compaction_index is not None,
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


def _as_dict(item: Any) -> dict:
    if isinstance(item, dict):
        return item
    return item.model_dump(mode="json")


def _input_item(item: dict) -> dict:
    """Keep full API output locally but omit fields the input endpoint rejects."""
    return {key: value for key, value in item.items() if key not in {"status", "created_by"}}


def _answer(
    output: list[dict],
    diagnostics: ResponseDiagnostics | None = None,
    incomplete_reason: str | None = None,
) -> Answer:
    text_parts: list[str] = []
    citations: list[Citation] = []
    evidence: list[Evidence] = []
    for item in output:
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
) -> ResponseDiagnostics:
    usage = _field(response, "usage")
    input_details = _field(usage, "input_tokens_details")
    output_details = _field(usage, "output_tokens_details")
    input_tokens = _int_or_none(_field(usage, "input_tokens"))
    cached_input_tokens = _int_or_none(_field(input_details, "cached_tokens"))
    cache_write_tokens = _int_or_none(_field(input_details, "cache_write_tokens"))
    output_tokens = _int_or_none(_field(usage, "output_tokens"))
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
        reasoning_tokens=_int_or_none(_field(output_details, "reasoning_tokens")),
        total_tokens=_int_or_none(_field(usage, "total_tokens")),
        estimated_text_cost_usd=estimate,
        known_file_search_call_cost_usd=round(file_search_calls * FILE_SEARCH_CALL_COST_USD, 6),
        native_compaction_applied=native_compaction_applied,
    )


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
