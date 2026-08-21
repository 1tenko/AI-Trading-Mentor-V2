import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentor.chat_service import (
    ChatService,
    EvaluationConfig,
    FILE_SEARCH_RESULT_BUDGETS,
    _input_item,
    _effective_research_depth,
)
from mentor.prompts import MENTOR_INSTRUCTIONS
from mentor.storage import Storage


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_reply_persists_continuation_state_and_extracts_evidence(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("First question")
    response = SimpleNamespace(
        output=[
            {
                "type": "reasoning",
                "encrypted_content": "encrypted-state",
            },
            {
                "type": "file_search_call",
                "queries": ["Jacob patience"],
                "results": [
                    {
                        "file_id": "file_2025",
                        "filename": "lesson.txt",
                        "text": "Jacob explains the setup here.",
                        "attributes": {"year": "2025", "relative_path": "2025/lesson.txt"},
                    }
                ],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Jacob teaches patience.",
                        "annotations": [
                            {
                                "type": "file_citation",
                                "file_id": "file_2025",
                                "filename": "lesson.txt",
                            }
                        ],
                    }
                ],
            },
        ]
    )
    responses = FakeResponses(response)
    client = SimpleNamespace(responses=responses)

    answer = ChatService(storage, client).reply(thread_id, "What does Jacob teach?")

    assert answer.text == "Jacob teaches patience."
    assert answer.citations[0].file_id == "file_2025"
    assert answer.evidence[0].excerpt == "Jacob explains the setup here."
    assert answer.evidence[0].metadata == {"year": "2025", "relative_path": "2025/lesson.txt"}
    assert storage.thread_items(thread_id) == [
        {"role": "user", "content": [{"type": "input_text", "text": "What does Jacob teach?"}]},
        *response.output,
    ]
    assert storage.display_turns(thread_id) == [
        {
            "turn_number": 1,
            "user_text": "What does Jacob teach?",
            "answer_markdown": "Jacob teaches patience.",
            "citations": [{"file_id": "file_2025", "filename": "lesson.txt"}],
            "evidence": [
                {
                    "file_id": "file_2025",
                    "filename": "lesson.txt",
                    "excerpt": "Jacob explains the setup here.",
                    "year": "2025",
                    "metadata": {"year": "2025", "relative_path": "2025/lesson.txt"},
                }
            ],
            "diagnostics": {
                "response_id": storage.response_diagnostics(thread_id)[0]["response_id"],
                "model": "gpt-5.6-sol",
                "status": "completed",
                "reasoning_effort": "high",
                "reasoning_mode": "standard",
                "requested_research_depth": "auto",
                "effective_research_depth": "normal",
                "file_search_calls": 1,
                "file_search_queries": ["Jacob patience"],
                "returned_evidence_count": 1,
                "cited_evidence_count": 1,
                "file_search_cost_status": "known per-call charge; vector storage and other platform charges excluded",
                "latency_ms": storage.response_diagnostics(thread_id)[0]["latency_ms"],
                "input_tokens": None,
                "cached_input_tokens": None,
                "cache_write_tokens": None,
                "output_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
                "estimated_text_cost_usd": None,
                "known_file_search_call_cost_usd": 0.0025,
                "native_compaction_applied": False,
            },
            "response_id": storage.response_diagnostics(thread_id)[0]["response_id"],
            "status": "completed",
            "incomplete_reason": None,
        }
    ]
    request = responses.calls[0]
    assert request["store"] is False
    assert request["include"] == [
        "reasoning.encrypted_content",
        "file_search_call.results",
    ]
    assert request["tools"] == [
        {"type": "file_search", "vector_store_ids": ["vs_jacob"], "max_num_results": 8}
    ]
    assert request["max_output_tokens"] == 25_000
    assert request["reasoning"] == {"effort": "high"}
    assert request["context_management"] == [{"type": "compaction", "compact_threshold": 50_000}]


def test_reply_replays_prior_response_items_and_rejects_blank_questions(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    storage.append_thread_items(
        thread_id,
        [
            {"role": "user", "content": [{"type": "input_text", "text": "First"}]},
            {"type": "reasoning", "encrypted_content": "prior-reasoning"},
        ],
    )
    response = SimpleNamespace(output=[])
    responses = FakeResponses(response)
    service = ChatService(storage, SimpleNamespace(responses=responses))

    try:
        service.reply(thread_id, "   ")
    except ValueError as error:
        assert str(error) == "Question cannot be blank."
    else:
        raise AssertionError("Blank questions must be rejected.")

    service.reply(thread_id, "Second")

    assert responses.calls[0]["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "First"}]},
        {"type": "reasoning", "encrypted_content": "prior-reasoning"},
        {"role": "user", "content": [{"type": "input_text", "text": "Second"}]},
    ]


def test_stream_reply_relays_deltas_then_persists_completed_response(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    response = SimpleNamespace(
        output=[
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Completed."}],
            }
        ]
    )
    responses = FakeResponses(
        [
            SimpleNamespace(type="response.output_text.delta", delta="Complete"),
            SimpleNamespace(type="response.output_text.delta", delta="d."),
            SimpleNamespace(type="response.completed", response=response),
        ]
    )
    service = ChatService(storage, SimpleNamespace(responses=responses))

    events = list(service.stream_reply(thread_id, "Question"))

    assert [(event.type, event.text) for event in events] == [
        ("delta", "Complete"),
        ("delta", "d."),
        ("complete", ""),
    ]
    assert events[-1].answer.text == "Completed."
    assert storage.thread_items(thread_id)[-1] == response.output[0]
    assert responses.calls[0]["stream"] is True


def test_stream_reply_marks_an_output_limit_response_incomplete_and_records_diagnostics(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    response = SimpleNamespace(
        id="resp_incomplete",
        model="gpt-5.6-sol",
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=200,
            total_tokens=300,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=150),
        ),
        output=[
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Partial answer."}],
            }
        ],
    )
    responses = FakeResponses(
        [
            SimpleNamespace(type="response.output_text.delta", delta="Partial answer."),
            SimpleNamespace(type="response.incomplete", response=response),
        ]
    )

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(thread_id, "Question"))

    assert [event.type for event in events] == ["delta", "incomplete"]
    assert events[-1].incomplete_reason == "max_output_tokens"
    assert events[-1].answer.text == "Partial answer."
    assert storage.thread_items(thread_id)[-1] == response.output[0]
    assert storage.response_diagnostics(thread_id)[0]["status"] == "incomplete"
    assert storage.display_turns(thread_id)[0]["answer_markdown"] == "Partial answer."
    assert storage.display_turns(thread_id)[0]["status"] == "incomplete"
    assert storage.display_turns(thread_id)[0]["incomplete_reason"] == "max_output_tokens"


def test_evaluation_configuration_validates_and_reaches_the_responses_request(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    responses = FakeResponses(SimpleNamespace(output=[]))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "Question", evaluation=EvaluationConfig("xhigh", "pro"))

    assert responses.calls[0]["reasoning"] == {"effort": "xhigh", "mode": "pro"}
    with pytest.raises(ValueError, match="Reasoning effort"):
        EvaluationConfig("low", "standard")
    with pytest.raises(ValueError, match="Reasoning mode"):
        EvaluationConfig("high", "fast")


def test_research_depth_is_independent_of_reasoning_and_persists_requested_and_effective_values(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    responses = FakeResponses(SimpleNamespace(output=[]))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "What are ALL the timeframe alignments?", EvaluationConfig("high", "standard", "auto"))
    service.reply(thread_id, "What is SMT?", EvaluationConfig("xhigh", "pro", "deep"))

    assert "Research depth: Exhaustive" in responses.calls[0]["instructions"]
    assert responses.calls[0]["reasoning"] == {"effort": "high"}
    assert "Research depth: Deep" in responses.calls[1]["instructions"]
    assert responses.calls[1]["reasoning"] == {"effort": "xhigh", "mode": "pro"}
    assert [
        (row["requested_research_depth"], row["effective_research_depth"])
        for row in storage.response_diagnostics(thread_id)
    ] == [("auto", "exhaustive"), ("deep", "deep")]
    with pytest.raises(ValueError, match="Research depth"):
        EvaluationConfig("high", "standard", "fast")


def test_diagnostics_retains_multiple_native_file_search_queries_and_truthful_cost_status(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    response = SimpleNamespace(
        output=[
            {
                "type": "file_search_call",
                "queries": ["SMT 2025", "SMT 2026"],
                "results": [{"file_id": "file_1", "filename": "one.txt", "text": "One", "attributes": {}}],
            },
            {
                "type": "file_search_call",
                "queries": ["SMT exceptions"],
                "results": [{"file_id": "file_2", "filename": "two.txt", "text": "Two", "attributes": {}}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Answer",
                        "annotations": [{"type": "file_citation", "file_id": "file_2", "filename": "two.txt"}],
                    }
                ],
            },
        ]
    )

    ChatService(storage, SimpleNamespace(responses=FakeResponses(response))).reply(thread_id, "Question")

    diagnostics = storage.display_turns(thread_id)[0]["diagnostics"]
    assert diagnostics["file_search_calls"] == 2
    assert diagnostics["file_search_queries"] == ["SMT 2025", "SMT 2026", "SMT exceptions"]
    assert diagnostics["returned_evidence_count"] == 2
    assert diagnostics["cited_evidence_count"] == 1
    assert diagnostics["file_search_cost_status"] == "known per-call charge; vector storage and other platform charges excluded"
    assert diagnostics["known_file_search_call_cost_usd"] == 0.005


def test_reply_replays_complete_state_without_response_only_status_fields(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    stored_item = {
        "type": "reasoning",
        "id": "rs_123",
        "summary": [],
        "content": [],
        "encrypted_content": "encrypted-state",
        "status": "completed",
    }
    storage.append_thread_items(thread_id, [stored_item])
    responses = FakeResponses(SimpleNamespace(output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "Second")

    assert storage.thread_items(thread_id)[0] == stored_item
    assert "status" not in responses.calls[0]["input"][0]
    assert responses.calls[0]["input"][0]["encrypted_content"] == "encrypted-state"


def test_policy_reserves_direct_teaching_for_affirmative_source_claims():
    assert "Direct source teaching requires" in MENTOR_INSTRUCTIONS
    assert "affirmative source claim." in MENTOR_INSTRUCTIONS
    assert "Do not label missing evidence or an unsupported claim" in MENTOR_INSTRUCTIONS
    assert "all, every, exact, exhaustive" in MENTOR_INSTRUCTIONS
    assert "each material subquestion" in MENTOR_INSTRUCTIONS
    assert "each requested year independently" in MENTOR_INSTRUCTIONS


def test_policy_requires_a_complementary_search_before_claiming_completeness():
    assert "candidate answer" in MENTOR_INSTRUCTIONS
    assert "complementary File Search query" in MENTOR_INSTRUCTIONS
    assert "not merely repeat" in MENTOR_INSTRUCTIONS
    assert "cap the research at four" in MENTOR_INSTRUCTIONS
    assert "never call an\nanswer exhaustive" in MENTOR_INSTRUCTIONS
    assert "gaps,\nintermediate categories" in MENTOR_INSTRUCTIONS
    assert "underlying mechanism" in MENTOR_INSTRUCTIONS


def test_semantic_regression_fixtures_preserve_phase_1_research_and_provenance_policy():
    fixtures = json.loads((Path(__file__).parent / "fixtures" / "phase2_semantic_prompts.json").read_text())
    assert [fixture["name"] for fixture in fixtures] == [
        "SMT definition",
        "TPD definition",
        "all reversion-level alignments",
        "exhaustive SMT teaching",
        "false attribution",
        "correction follow-up",
    ]
    assert [
        _effective_research_depth(fixture["prompt"], "auto") for fixture in fixtures
    ] == [fixture["effective_depth"] for fixture in fixtures]
    assert "Direct source teaching requires" in MENTOR_INSTRUCTIONS
    assert "complementary File Search query" in MENTOR_INSTRUCTIONS


def test_auto_research_depth_distinguishes_definitions_research_comparisons_and_exhaustive_requests():
    assert _effective_research_depth("What is TPD?", "auto") == "normal"
    assert _effective_research_depth("Research this again and verify it.", "auto") == "deep"
    assert _effective_research_depth("What are the differences between 2025 and 2026 teachings?", "auto") == "deep"
    assert _effective_research_depth("Tell me everything Jacob teaches about SMT.", "auto") == "exhaustive"


def test_request_uses_a_smaller_native_result_budget_only_for_normal_research(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    responses = FakeResponses(SimpleNamespace(output=[]))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "What is TPD?")
    service.reply(thread_id, "Compare the 2025 and 2026 teachings.")
    service.reply(thread_id, "What are all the TPD alignments?")

    assert responses.calls[0]["tools"][0]["max_num_results"] == FILE_SEARCH_RESULT_BUDGETS["normal"]
    assert responses.calls[1]["tools"][0]["max_num_results"] == FILE_SEARCH_RESULT_BUDGETS["deep"]
    assert responses.calls[2]["tools"][0]["max_num_results"] == FILE_SEARCH_RESULT_BUDGETS["exhaustive"]


def test_replay_omits_server_only_compaction_fields():
    assert _input_item({"type": "compaction", "created_by": "server", "status": "completed"}) == {
        "type": "compaction"
    }


def test_native_context_management_replaces_only_model_replay_when_openai_returns_a_compaction_item(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    storage.append_thread_items(
        thread_id,
        [
            {"role": "user", "content": [{"type": "input_text", "text": "Original question"}]},
            {
                "type": "file_search_call",
                "results": [{"file_id": "file_1", "filename": "large.txt", "text": "x" * 450_000}],
            },
        ],
    )
    response = SimpleNamespace(
        output=[
            {"type": "compaction", "id": "cmp_1", "encrypted_content": "opaque-state"},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Follow-up."}]},
        ],
        usage=SimpleNamespace(
            input_tokens=3_000,
            output_tokens=100,
            total_tokens=3_100,
            input_tokens_details=SimpleNamespace(cached_tokens=2_000, cache_write_tokens=500),
            output_tokens_details=SimpleNamespace(reasoning_tokens=50),
        ),
    )
    responses = FakeResponses(response)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "Where did that come from?")

    assert responses.calls[0]["context_management"] == [{"type": "compaction", "compact_threshold": 50_000}]
    assert any(item.get("type") == "file_search_call" for item in responses.calls[0]["input"])
    assert responses.calls[0]["input"][-1]["content"][0]["text"] == "Where did that come from?"
    assert answer.diagnostics.cache_write_tokens == 500
    assert answer.diagnostics.native_compaction_applied is True
    assert storage.thread_items(thread_id)[1]["type"] == "file_search_call"
    replay_items = storage.replay_items(thread_id)
    assert any(item.get("type") == "compaction" for item in replay_items)
    assert not any(item.get("type") == "file_search_call" for item in replay_items)
