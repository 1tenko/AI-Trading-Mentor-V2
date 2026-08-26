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
from mentor.profile import ProfileService
from mentor.storage import Storage


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class SequenceResponses:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def source_response(text, annotations, *, status="completed", usage=None):
    return SimpleNamespace(
        status=status,
        usage=usage,
        output=[
            {
                "type": "file_search_call",
                "queries": ["Jacob source"],
                "results": [
                    {
                        "file_id": "file_jacob",
                        "filename": "lesson.txt",
                        "text": "[730.0 --> 756.0] Jacob's original words.",
                        "attributes": {"year": "2026", "relative_path": "2026/January 20th.txt"},
                    }
                ],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": annotations}],
            },
        ],
    )


def profile_tool_call(*, call_id="call_profile", name="update_trader_profile", arguments=None):
    return SimpleNamespace(
        status="completed",
        output=[
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments if arguments is not None else {}),
            }
        ],
    )


def profile_write_arguments(*, operation="save"):
    return {
        "operation": operation,
        "category": "markets/instruments",
        "subject": "Primary market",
        "value": "ES",
        "kind": "fact",
        "provenance": "USER_STATED",
        "target_id": None,
    }


def profile_forget_arguments(*, operation, target_id):
    return {
        "operation": operation,
        "category": None,
        "subject": None,
        "value": None,
        "kind": None,
        "provenance": None,
        "target_id": target_id,
    }


def test_selected_profile_context_is_marked_on_only_the_new_request(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    profile = ProfileService(storage)
    profile.create_item(
        category="goals/research",
        subject="Research goal",
        value="I am building a backtest.",
        kind="goal",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    thread_id = storage.create_thread("Question")
    responses = SequenceResponses(SimpleNamespace(status="completed", output=[]), SimpleNamespace(status="completed", output=[]))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "How should I research this setup?")
    service.reply(thread_id, "What should I research next?")

    first, second = responses.calls
    marker = "Trader Profile — user context, not source evidence"
    assert marker in first["instructions"]
    assert "Research goal: I am building a backtest." in first["instructions"]
    assert all("Research goal" not in json.dumps(item) for item in first["input"])
    assert all("Research goal" not in json.dumps(item) for item in second["input"])
    assert all(marker not in json.dumps(item) for item in storage.replay_items(thread_id))


def test_explicit_profile_tool_call_writes_once_then_uses_one_terminal_continuation(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    terminal = SimpleNamespace(
        status="completed",
        output=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Saved."}]}],
    )
    responses = SequenceResponses(profile_tool_call(arguments=profile_write_arguments()), terminal)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "Remember that I only trade ES.")

    assert answer.text == "Saved."
    assert len(storage.current_confirmed_profile_items()) == 1
    assert len(responses.calls) == 2
    continuation = responses.calls[1]
    assert any(item.get("type") == "function_call" for item in continuation["input"])
    output = next(item for item in continuation["input"] if item.get("type") == "function_call_output")
    assert output["call_id"] == "call_profile"
    assert json.loads(output["output"])["status"] == "saved"
    assert all(tool["type"] != "function" for tool in continuation["tools"])


def test_citation_repair_after_profile_mutation_keeps_only_raw_search_and_no_dangling_call(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    uncited = source_response("Direct source teaching: Jacob teaches this.", [])
    repaired = source_response(
        "Direct source teaching: Jacob teaches this.",
        [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
    )
    responses = SequenceResponses(profile_tool_call(arguments=profile_write_arguments()), uncited, repaired)

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "Remember that I only trade ES.")

    assert len(storage.current_confirmed_profile_items()) == 1
    assert len(responses.calls) == 3
    assert all(tool["type"] != "function" for tool in responses.calls[1]["tools"])
    assert all(tool["type"] != "function" for tool in responses.calls[2]["tools"])
    replay = storage.replay_items(thread_id)
    call_index = next(index for index, item in enumerate(replay) if item.get("type") == "function_call")
    assert replay[call_index + 1]["type"] == "function_call_output"


def test_ordinary_citation_repair_strips_profile_mutation_tool(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    uncited = source_response("Direct source teaching: Jacob teaches this.", [])
    repaired = source_response(
        "Direct source teaching: Jacob teaches this.",
        [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
    )
    responses = SequenceResponses(uncited, repaired)

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "What does Jacob teach?")

    assert len(responses.calls) == 2
    assert all(tool["type"] != "function" for tool in responses.calls[1]["tools"])


@pytest.mark.parametrize(
    "response, expected_call_ids",
    [
        (profile_tool_call(name="unknown_tool", arguments=profile_write_arguments()), ("call_profile",)),
        (profile_tool_call(arguments=None), ("call_profile",)),
        (
            SimpleNamespace(
                status="completed",
                output=[
                    {
                        "type": "function_call",
                        "call_id": "call_one",
                        "name": "update_trader_profile",
                        "arguments": json.dumps(profile_write_arguments()),
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_two",
                        "name": "update_trader_profile",
                        "arguments": json.dumps(profile_write_arguments()),
                    },
                ],
            ),
            ("call_one", "call_two"),
        ),
    ],
)
def test_invalid_or_duplicate_profile_tool_calls_receive_outputs_and_a_terminal_answer(
    tmp_path, response, expected_call_ids
):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    terminal = SimpleNamespace(
        status="completed",
        output=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I could not update that profile item."}]}],
    )
    responses = SequenceResponses(response, terminal)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Remember that I only trade ES."
    )

    assert storage.current_confirmed_profile_items() == []
    assert answer.text == "I could not update that profile item."
    assert len(responses.calls) == 2
    outputs = [item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output"]
    assert tuple(item["call_id"] for item in outputs) == expected_call_ids
    assert all(json.loads(item["output"])["status"] == "rejected" for item in outputs)
    assert all(tool["type"] != "function" for tool in responses.calls[1]["tools"])
    replay = storage.replay_items(thread_id)
    assert sum(item.get("type") == "function_call" for item in replay) == len(expected_call_ids)
    assert sum(item.get("type") == "function_call_output" for item in replay) == len(expected_call_ids)


@pytest.mark.parametrize("call_id", [None, ""])
def test_profile_tool_call_without_a_usable_id_is_not_persisted_or_replayed(tmp_path, call_id):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    malformed_call = {
        "type": "function_call",
        "name": "update_trader_profile",
        "arguments": json.dumps(profile_write_arguments()),
    }
    if call_id is not None:
        malformed_call["call_id"] = call_id
    response = SimpleNamespace(status="completed", output=[malformed_call])
    responses = FakeResponses(response)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Remember that I only trade ES."
    )

    assert answer.text == "I could not update that profile item. Please try again."
    assert len(responses.calls) == 1
    assert storage.current_confirmed_profile_items() == []
    assert all(item.get("type") != "function_call" for item in storage.thread_items(thread_id))
    assert all(item.get("type") != "function_call" for item in storage.replay_items(thread_id))
    assert storage.display_turns(thread_id)[0]["answer_markdown"] == answer.text


def test_malformed_profile_tool_call_keeps_an_existing_cited_answer(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    answer_item = source_response(
        "Direct source teaching: Jacob teaches patience.",
        [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
    ).output[1]
    response = SimpleNamespace(
        status="completed",
        output=[
            profile_tool_call(call_id="").output[0],
            answer_item,
        ],
    )

    answer = ChatService(storage, SimpleNamespace(responses=FakeResponses(response))).reply(
        thread_id, "Remember that I only trade ES."
    )

    assert answer.text == "Direct source teaching: Jacob teaches patience."
    assert answer.citations[0].file_id == "file_jacob"
    assert all(item.get("type") != "function_call" for item in storage.replay_items(thread_id))


def test_explicit_forget_archives_only_the_exact_profile_item_once(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    profile = ProfileService(storage)
    target = profile.create_item(
        category="markets/instruments",
        subject="Primary market",
        value="ES",
        kind="fact",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    thread_id = storage.create_thread("Question")
    terminal = SimpleNamespace(
        status="completed",
        output=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Archived the requested profile item."}]}],
    )
    responses = SequenceResponses(
        profile_tool_call(arguments=profile_forget_arguments(operation="archive", target_id=target.id)), terminal
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, f"Forget profile item {target.id}."
    )

    assert answer.text == "Archived the requested profile item."
    assert storage.profile_item(target.id).state == "archived"
    output = next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")
    assert json.loads(output["output"])["status"] == "archived"


def test_explicit_forget_deletes_only_the_exact_profile_item_once(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    target = ProfileService(storage).create_item(
        category="markets/instruments",
        subject="Primary market",
        value="ES",
        kind="fact",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    thread_id = storage.create_thread("Question")
    terminal = SimpleNamespace(
        status="completed",
        output=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Deleted the requested profile item."}]}],
    )
    responses = SequenceResponses(
        profile_tool_call(
            call_id="delete_profile", arguments=profile_forget_arguments(operation="delete", target_id=target.id)
        ),
        terminal,
    )

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, f"Delete profile item {target.id}.")

    assert storage.profile_item(target.id) is None
    output = next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")
    assert json.loads(output["output"])["status"] == "deleted"


@pytest.mark.parametrize(
    "question, arguments",
    [
        ("Forget that preference.", profile_forget_arguments(operation="archive", target_id=1)),
        ("Forget profile item one.", profile_forget_arguments(operation="delete", target_id="one")),
    ],
)
def test_ambiguous_or_malformed_forget_never_mutates_and_still_terminates(tmp_path, question, arguments):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    profile = ProfileService(storage)
    target = profile.create_item(
        category="markets/instruments",
        subject="Primary market",
        value="ES",
        kind="fact",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    thread_id = storage.create_thread("Question")
    terminal = SimpleNamespace(
        status="completed",
        output=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Please identify the profile item explicitly."}]}],
    )
    responses = SequenceResponses(profile_tool_call(arguments=arguments), terminal)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, question)

    assert answer.text == "Please identify the profile item explicitly."
    assert storage.profile_item(target.id).state == "confirmed"
    output = next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")
    assert json.loads(output["output"])["status"] == "rejected"


def test_profile_tool_call_id_is_idempotent_across_replayed_requests(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    first_thread = storage.create_thread("First")
    second_thread = storage.create_thread("Second")
    terminal = SimpleNamespace(status="completed", output=[])
    responses = SequenceResponses(
        profile_tool_call(arguments=profile_write_arguments()),
        terminal,
        profile_tool_call(arguments=profile_write_arguments()),
        terminal,
    )
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(first_thread, "Remember that I only trade ES.")
    service.reply(second_thread, "Remember that I only trade ES.")

    assert len(storage.current_confirmed_profile_items()) == 1


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
    assert request["tools"][0] == {
        "type": "file_search", "vector_store_ids": ["vs_jacob"], "max_num_results": 8
    }
    assert request["tools"][1]["name"] == "update_trader_profile"
    assert request["tools"][1]["strict"] is True
    assert request["max_output_tokens"] == 25_000
    assert request["reasoning"] == {"effort": "high"}
    assert request["context_management"] == [{"type": "compaction", "compact_threshold": 50_000}]


def test_direct_source_answer_with_native_citations_does_not_retry(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    responses = SequenceResponses(
        source_response(
            "Direct source teaching: Jacob teaches this.",
            [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
        )
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "What does Jacob teach?")

    assert len(responses.calls) == 1
    assert [citation.file_id for citation in answer.citations] == ["file_jacob"]


def test_direct_source_answer_without_citations_is_repaired_before_persistence(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    draft = source_response("**Direct source teaching – 2025:** Jacob teaches this.", [])
    repaired = source_response(
        "**Direct source teaching – 2025:** Jacob teaches this.",
        [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
    )
    responses = SequenceResponses(draft, repaired)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "What does Jacob teach?")

    assert len(responses.calls) == 2
    assert "Citation repair" in responses.calls[1]["instructions"]
    assert any(item.get("type") == "file_search_call" for item in responses.calls[1]["input"])
    assert answer.text == repaired.output[-1]["content"][0]["text"]
    assert [citation.file_id for citation in answer.citations] == ["file_jacob"]
    assert storage.display_turns(thread_id)[0]["answer_markdown"] == answer.text
    assert storage.thread_items(thread_id)[-1] == repaired.output[-1]
    assert draft.output[-1] not in storage.thread_items(thread_id)


def test_stream_replaces_an_uncited_direct_source_draft_with_the_repaired_answer(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    draft = source_response("Direct source teaching: Jacob teaches this.", [])
    repaired = source_response(
        "Direct source teaching: Jacob teaches this.",
        [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
    )
    responses = SequenceResponses(
        [
            SimpleNamespace(type="response.output_text.delta", delta="Direct source teaching: Jacob teaches this."),
            SimpleNamespace(type="response.completed", response=draft),
        ],
        repaired,
    )

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(thread_id, "What does Jacob teach?"))

    assert [event.type for event in events] == ["delta", "complete"]
    assert len(responses.calls) == 2
    assert events[-1].answer.citations[0].file_id == "file_jacob"
    assert draft.output[-1] not in storage.thread_items(thread_id)


def test_direct_source_answer_warns_when_one_repair_still_has_no_citations(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    responses = SequenceResponses(
        source_response("Direct source teaching: Jacob teaches this.", []),
        source_response("Direct source teaching: Jacob teaches this.", []),
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "What does Jacob teach?")

    assert len(responses.calls) == 2
    assert answer.citations == []
    assert "Citation warning" in answer.text
    assert "could not be attached" in answer.text


def test_direct_source_label_inside_a_table_cell_is_repaired(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    text = "| Topic | **Direct source teaching:** Jacob teaches this. |"
    responses = SequenceResponses(
        source_response(text, []),
        source_response(
            text,
            [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
        ),
    )

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "What does Jacob teach?")

    assert len(responses.calls) == 2


def test_ai_hypothesis_without_citations_is_not_retried(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    responses = SequenceResponses(source_response("AI hypothesis: This may be useful.", []))

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "What might work?")

    assert len(responses.calls) == 1
    assert answer.text == "AI hypothesis: This may be useful."


def test_exact_source_timestamp_is_repaired_when_no_retrieved_passage_supports_it(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    citation = [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}]
    draft = source_response("Direct source teaching: Jacob says this at 12:10–12:36.", citation)
    draft.output[0]["results"][0]["text"] = "[609.0 --> 616.0] An unrelated passage."
    repaired = source_response("Direct source teaching: Jacob says this at 12:10–12:36.", citation)
    responses = SequenceResponses(draft, repaired)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id,
        "Where exactly does Jacob say this? Give me the video and timestamp.",
    )

    assert len(responses.calls) == 2
    assert "must perform one new focused native file search" in responses.calls[1]["instructions"].casefold()
    assert responses.calls[1]["tool_choice"] == {"type": "file_search"}
    assert answer.text == "Direct source teaching: Jacob says this at 12:10–12:36."


def test_exact_source_timestamp_is_withheld_after_one_unsupported_repair(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    citation = [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}]
    draft = source_response("Direct source teaching: Jacob says this at 12:10–12:36.", citation)
    repaired = source_response("Direct source teaching: Jacob says this at 12:10–12:36.", citation)
    for response in (draft, repaired):
        response.output[0]["results"][0]["text"] = "[609.0 --> 616.0] An unrelated passage."
    responses = SequenceResponses(draft, repaired)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id,
        "Where exactly does Jacob say this? Give me the video and timestamp.",
    )

    assert len(responses.calls) == 2
    assert "Source-verification warning" in answer.text
    assert "12:10" not in answer.text


def test_exact_source_timestamp_can_use_a_later_range_in_one_retrieved_passage(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    citation = [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}]
    response = source_response("Direct source teaching: Jacob says this at 12:10–12:36.", citation)
    response.output[0]["results"][0]["text"] = (
        "[609.0 --> 616.0] An unrelated passage.\n"
        "[730.0 --> 756.0] The supporting passage."
    )
    responses = SequenceResponses(response)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id,
        "Where exactly does Jacob say this? Give me the video and timestamp.",
    )

    assert len(responses.calls) == 1
    assert answer.text == "Direct source teaching: Jacob says this at 12:10–12:36."


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


def test_stream_reply_reports_a_failed_response_without_persisting_a_turn(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    failed_response = SimpleNamespace(
        status="failed",
        error=SimpleNamespace(message="context window exceeded"),
    )
    responses = FakeResponses([SimpleNamespace(type="response.failed", response=failed_response)])

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(thread_id, "Question"))

    assert [(event.type, event.error) for event in events] == [
        ("error", "The mentor request failed. Try again."),
    ]
    assert storage.thread_items(thread_id) == []
    assert storage.display_turns(thread_id) == []


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
    assert storage.replay_items(thread_id) == response.output
