"""Grounded, stateless Responses API chat for Theo's private Mentor."""

from dataclasses import dataclass
import json
import logging
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from mentor.prompts import MENTOR_INSTRUCTIONS
from mentor.orientation import OrientationBudget, OrientationResult, OrientationService
from mentor.storage import Storage
from mentor.vector_stores import VectorStoreAdapter


LOGGER = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 25_000
COMPACTION_TOKEN_THRESHOLD = 50_000
FILE_SEARCH_RESULT_BUDGETS = {"normal": 8, "deep": 20, "exhaustive": 20}
FILE_SEARCH_CALL_COST_USD = 0.0025
ORIENTATION_FUNCTION_NAME = "consult_assimilated_knowledge"
ORIENTATION_BUDGET = OrientationBudget(max_records=8, max_tokens=4_000)
ORIENTATION_TOOL = {
    "type": "function",
    "name": ORIENTATION_FUNCTION_NAME,
    "description": (
        "Return bounded derived orientation for a broad question. This is not raw source text "
        "and cannot establish a factual teaching or citation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The bounded topic or question to orient."},
            "collection_id": {"type": "string", "description": "Optional active collection scope."},
            "year": {"type": "integer", "description": "Optional active year scope."},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}
DIRECT_SOURCE_CLAIM = re.compile(
    r"(?:\*{1,2})?Direct source teaching"
    r"(?:\*{1,2})?\s*(?::|[-\u2013\u2014]|\n)\s*\S",
    re.IGNORECASE,
)
EXACT_SOURCE_REQUEST = re.compile(
    r"\bwhere\s+(?:exactly\s+)?(?:does|did|is)\b|\b(?:what|which)\s+(?:video|timestamp)\b"
    r"|\bgive me\b.*\b(?:video|timestamp)\b|\bexact source\b"
    r"|\b(?:exact|verbatim)\s+(?:source|location|quote|wording|timestamp)\b|\bquote(?:d)?\b",
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
    knowledge_context: dict[str, Any] | None


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


@dataclass(frozen=True)
class KnowledgeContext:
    """Compact persisted audit for request-scoped derived orientation."""

    status: str
    used: bool
    snapshot_id: str | None
    snapshot_schema_version: str | None
    record_ids: tuple[str, ...]
    budget: OrientationBudget
    used_tokens: int
    truncated: bool

    @classmethod
    def from_result(cls, result: OrientationResult) -> "KnowledgeContext":
        return cls(
            status="used",
            used=True,
            snapshot_id=result.snapshot_id,
            snapshot_schema_version=result.snapshot_schema_version,
            record_ids=tuple(record.record_id for record in result.records),
            budget=result.budget,
            used_tokens=result.used_tokens,
            truncated=result.truncated,
        )

    @classmethod
    def unavailable(cls, snapshot: Any) -> "KnowledgeContext":
        return cls(
            status="unavailable",
            used=False,
            snapshot_id=_snapshot_identifier(snapshot, "snapshot_id"),
            snapshot_schema_version=_snapshot_identifier(snapshot, "schema_version"),
            record_ids=(),
            budget=ORIENTATION_BUDGET,
            used_tokens=0,
            truncated=False,
        )

    def audit(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "used": self.used,
            "snapshot_id": self.snapshot_id,
            "snapshot_schema_version": self.snapshot_schema_version,
            "record_ids": list(self.record_ids),
            "record_count": len(self.record_ids),
            "budget": {
                "max_records": self.budget.max_records,
                "max_tokens": self.budget.max_tokens,
                "used_tokens": self.used_tokens,
                "truncated": self.truncated,
            },
        }


class ChatService:
    def __init__(
        self,
        storage: Storage,
        client: Any,
        model: str = "gpt-5.6-sol",
        orientation_service: Any | None = None,
    ):
        self.storage = storage
        self.client = client
        self.model = model
        self.orientation_service = (
            OrientationService(storage, VectorStoreAdapter(client), budget=ORIENTATION_BUDGET)
            if orientation_service is None
            else orientation_service
        )

    def reply(
        self,
        thread_id: int,
        question: str,
        evaluation: EvaluationConfig = DEFAULT_EVALUATION_CONFIG,
    ) -> Answer:
        user_item, request, effective_depth, snapshot = self._request(thread_id, question, evaluation)
        started_at = perf_counter()
        request, knowledge_context, orientation_response = self._orientation_request(request, snapshot)
        response = self.client.responses.create(**request)
        response, evidence_output, draft_response = self._citation_repaired_response(
            request, response, user_item["content"][0]["text"]
        )
        return self._finalize(
            thread_id,
            user_item,
            response,
            evaluation,
            effective_depth,
            started_at,
            evidence_output=evidence_output,
            draft_response=draft_response,
            knowledge_context=knowledge_context,
            orientation_response=orientation_response,
        )

    def stream_reply(
        self,
        thread_id: int,
        question: str,
        evaluation: EvaluationConfig = DEFAULT_EVALUATION_CONFIG,
    ):
        try:
            user_item, request, effective_depth, snapshot = self._request(thread_id, question, evaluation)
            started_at = perf_counter()
            request, knowledge_context, orientation_response = self._orientation_request(request, snapshot)
            stream = self.client.responses.create(**request, stream=True)
            for event in stream:
                if event.type == "response.output_text.delta":
                    yield StreamEvent("delta", event.delta)
                elif event.type in {"response.completed", "response.incomplete"}:
                    response, evidence_output, draft_response = self._citation_repaired_response(
                        request, event.response, user_item["content"][0]["text"]
                    )
                    answer = self._finalize(
                        thread_id,
                        user_item,
                        response,
                        evaluation,
                        effective_depth,
                        started_at,
                        evidence_output=evidence_output,
                        draft_response=draft_response,
                        knowledge_context=knowledge_context,
                        orientation_response=orientation_response,
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
        }
        if needs_timestamp_repair:
            repair_request["tool_choice"] = {"type": "file_search"}
        repaired = self.client.responses.create(**repair_request)
        return repaired, [*draft_output, *(_as_dict(item) for item in repaired.output)], response

    def _request(
        self, thread_id: int, question: str, evaluation: EvaluationConfig
    ) -> tuple[dict, dict, str, Any | None]:
        question = _question(question)
        snapshot = _published_snapshot(self.storage.current_snapshot())
        vector_store_id = (
            _snapshot_identifier(snapshot, "raw_store_id") if snapshot is not None else self.storage.vector_store_id()
        )
        if vector_store_id is None:
            raise RuntimeError("Import the Jacob transcripts before starting a chat.")
        user_item = {"role": "user", "content": [{"type": "input_text", "text": question}]}
        effective_depth = _effective_research_depth(question, evaluation.research_depth)
        replay_items = self.storage.replay_items(thread_id)
        should_orient = snapshot is not None and _should_orient(question, effective_depth)
        request = {
            "model": self.model,
            "instructions": f"{MENTOR_INSTRUCTIONS}\n\n{_research_instruction(effective_depth)}",
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
                *([ORIENTATION_TOOL] if should_orient else []),
            ],
            "include": ["reasoning.encrypted_content", "file_search_call.results"],
            "reasoning": evaluation.request_value(),
            "context_management": [{"type": "compaction", "compact_threshold": COMPACTION_TOKEN_THRESHOLD}],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        if should_orient:
            request["tool_choice"] = {"type": "function", "name": ORIENTATION_FUNCTION_NAME}
        return user_item, request, effective_depth, snapshot if should_orient else None

    def _orientation_request(
        self, request: dict, snapshot: Any | None
    ) -> tuple[dict, KnowledgeContext | None, Any | None]:
        if snapshot is None:
            return request, None, None
        initial = self.client.responses.create(**request)
        initial_output = [_as_dict(item) for item in initial.output]
        function_call = _orientation_function_call(initial_output)
        if function_call is None:
            LOGGER.warning("Orientation tool was not called for a broad Mentor request")
            return _raw_only_request(request), KnowledgeContext.unavailable(snapshot), initial
        try:
            arguments = _orientation_arguments(function_call)
            result = self.orientation_service.consult(
                arguments["question"],
                snapshot=snapshot,
                **{key: value for key, value in arguments.items() if key != "question"},
            )
            if result.snapshot_id != _snapshot_identifier(snapshot, "snapshot_id"):
                raise ValueError("orientation result did not match the turn snapshot")
            context = KnowledgeContext.from_result(result)
            output = json.dumps(_orientation_output(result), separators=(",", ":"))
        except Exception:
            LOGGER.warning("Derived orientation was unavailable", exc_info=True)
            context = KnowledgeContext.unavailable(snapshot)
            output = json.dumps(
                {
                    "status": "unavailable",
                    "message": "Derived orientation was unavailable; verify with raw File Search.",
                },
                separators=(",", ":"),
            )
        continuation = _raw_only_request(request)
        continuation["input"] = [
            *request["input"],
            *(_input_item(item) for item in initial_output),
            {"type": "function_call_output", "call_id": function_call["call_id"], "output": output},
        ]
        return continuation, context, initial

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
        knowledge_context: KnowledgeContext | None = None,
        orientation_response: Any | None = None,
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
            knowledge_context=knowledge_context,
            orientation_response=orientation_response,
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


def _published_snapshot(snapshot: Any) -> Any | None:
    if (
        _field(snapshot, "status") == "published"
        and _snapshot_identifier(snapshot, "snapshot_id") is not None
        and _snapshot_identifier(snapshot, "raw_store_id") is not None
        and _snapshot_identifier(snapshot, "derived_store_id") is not None
    ):
        return snapshot
    return None


def _snapshot_identifier(snapshot: Any, name: str) -> str | None:
    value = _field(snapshot, name)
    return value if isinstance(value, str) and value else None


def _should_orient(question: str, effective_depth: str) -> bool:
    normalized = question.casefold()
    if EXACT_SOURCE_REQUEST.search(normalized):
        return False
    if effective_depth == "exhaustive":
        return True
    return bool(
        re.search(
            r"\b(all|every|everything|complete|exhaustive)\b|\bteach me\b"
            r"|\b(compare|comparison|difference|differences|changed|change|evolution|evolved|refined)\b"
            r"|\b(relationship|relationships|factor|factors|condition|conditions|affect|affects|influence|influences|interaction|interact)\b"
            r"|\b(whole|overall|entire)\s+system\b"
            r"|\b(how do|how does).{0,160}\b(work together|fit together|relate|interact)\b",
            normalized,
        )
    )


def _orientation_function_call(output: list[dict]) -> dict | None:
    for candidate in output:
        if (
            candidate.get("type") == "function_call"
            and candidate.get("name") == ORIENTATION_FUNCTION_NAME
            and isinstance(candidate.get("call_id"), str)
            and candidate["call_id"]
        ):
            return candidate
    return None


def _orientation_arguments(function_call: dict) -> dict[str, Any]:
    raw_arguments = function_call.get("arguments")
    if isinstance(raw_arguments, str):
        raw_arguments = json.loads(raw_arguments)
    if not isinstance(raw_arguments, dict) or set(raw_arguments) - {"question", "collection_id", "year"}:
        raise ValueError("orientation function arguments are invalid")
    question = _question(raw_arguments.get("question", ""))
    arguments: dict[str, Any] = {"question": question}
    if "collection_id" in raw_arguments:
        collection_id = raw_arguments["collection_id"]
        if not isinstance(collection_id, str) or not collection_id.strip():
            raise ValueError("orientation collection_id is invalid")
        arguments["collection_id"] = collection_id
    if "year" in raw_arguments:
        year = raw_arguments["year"]
        if isinstance(year, bool) or not isinstance(year, int):
            raise ValueError("orientation year is invalid")
        arguments["year"] = year
    return arguments


def _raw_only_request(request: dict) -> dict:
    return {
        key: value
        for key, value in request.items()
        if key != "tool_choice"
    } | {
        "tools": [tool for tool in request["tools"] if tool.get("type") != "function"],
    }


def _orientation_output(result: OrientationResult) -> dict[str, Any]:
    return {
        "type": "derived_orientation",
        "snapshot_id": result.snapshot_id,
        "snapshot_schema_version": result.snapshot_schema_version,
        "records": [
            {
                "record_id": record.record_id,
                "concept_id": record.concept_id,
                "family": record.family,
                "derived_kind": record.derived_kind,
                "evidence_state": record.evidence_state,
                "qualification": record.qualification,
                "statement": record.statement,
                "anchor_ids": list(record.anchor_ids),
                "source_area": {
                    "collection_id": record.source_area.collection_id,
                    "year": record.source_area.year,
                    "scope": record.source_area.scope,
                },
                "concepts": [
                    {
                        "canonical_label": concept.canonical_label,
                        "aliases": list(concept.aliases),
                        "scope": concept.scope,
                        "supporting_record_count": concept.supporting_record_count,
                        "supporting_anchor_count": concept.supporting_anchor_count,
                        "occurrences": [
                            {
                                "role": occurrence.role,
                                "position": occurrence.position,
                                "label": occurrence.label,
                            }
                            for occurrence in concept.occurrences
                        ],
                    }
                    for concept in record.concepts
                ],
            }
            for record in result.records
        ],
        "budget": {
            "max_records": result.budget.max_records,
            "max_tokens": result.budget.max_tokens,
            "used_tokens": result.used_tokens,
            "truncated": result.truncated,
        },
    }


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
    knowledge_context: KnowledgeContext | None = None,
    orientation_response: Any | None = None,
) -> ResponseDiagnostics:
    responses = [
        *([] if orientation_response is None else [orientation_response]),
        *([] if draft_response is None else [draft_response]),
        response,
    ]
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
        knowledge_context=None if knowledge_context is None else knowledge_context.audit(),
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
    if re.search(r"\b(verify|verification|compare|comparison|difference|differences|different|relationship|changed|change|evolution|why)\b", normalized):
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
