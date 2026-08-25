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
    _should_orient,
)
from mentor.prompts import MENTOR_INSTRUCTIONS
from mentor.storage import Storage
from mentor.orientation import (
    OrientationBudget,
    OrientationConceptOccurrence,
    OrientationConceptSummary,
    OrientationRecord,
    OrientationResult,
    OrientationSourceArea,
)


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


class FakeOrientation:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def consult(self, question, **kwargs):
        self.calls.append((question, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def published_snapshot():
    return SimpleNamespace(
        snapshot_id="snap_current",
        schema_version="derived-schema-v1",
        status="published",
        raw_store_id="vs_raw_current",
        derived_store_id="vs_derived_current",
    )


def orientation_result():
    return OrientationResult(
        snapshot_id="snap_current",
        snapshot_schema_version="derived-schema-v1",
        records=(
            OrientationRecord(
                record_id="rec_orientation",
                concept_id="con_" + "a" * 64,
                family="relationship",
                derived_kind="cross_source_synthesis",
                evidence_state="qualified",
                qualification="Verify against the raw source.",
                statement="Derived orientation cue that must not persist in replay.",
                anchor_ids=("anc_orientation",),
                source_area=OrientationSourceArea("collection_jacob", 2026, "timing"),
                concepts=(
                    OrientationConceptSummary(
                        "Canonical timing",
                        ("Timing alias",),
                        "timing",
                        2,
                        2,
                        (OrientationConceptOccurrence("left", None, "canonical timing"),),
                    ),
                ),
            ),
        ),
        used_tokens=96,
        budget=OrientationBudget(max_records=8, max_tokens=4_000),
        truncated=False,
        duplicate_result_count=0,
        discarded_result_count=0,
    )


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


def test_broad_question_injects_lean_server_owned_orientation_before_raw_search_and_persists_only_audit(
    tmp_path, monkeypatch
):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_legacy")
    monkeypatch.setattr(storage, "current_snapshot", published_snapshot)
    response = source_response(
        "Direct source teaching: The raw source controls this answer.",
        [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            input_tokens_details=SimpleNamespace(cached_tokens=5, cache_write_tokens=8),
            output_tokens_details=SimpleNamespace(reasoning_tokens=9),
        ),
    )
    responses = FakeResponses(response)
    orientation = FakeOrientation(orientation_result())

    answer = ChatService(
        storage,
        SimpleNamespace(responses=responses),
        orientation_service=orientation,
    ).reply(storage.create_thread("Compare"), "Compare SMT and TPD across 2025 and 2026.")

    assert answer.text == "Direct source teaching: The raw source controls this answer."
    assert len(responses.calls) == 1
    assert responses.calls[0]["tools"] == [
        {"type": "file_search", "vector_store_ids": ["vs_raw_current"], "max_num_results": 20}
    ]
    assert "tool_choice" not in responses.calls[0]
    assert orientation.calls == [
        ("Compare SMT and TPD across 2025 and 2026.", {"snapshot": published_snapshot()})
    ]
    orientation_context = responses.calls[0]["instructions"]
    assert "Derived orientation (non-authoritative; verify with raw File Search):" in orientation_context
    oriented_record = json.loads(orientation_context.rsplit("\n", 1)[-1])["records"][0]
    assert oriented_record["record_id"] == "rec_orientation"
    assert oriented_record["concept"] == "Canonical timing"
    assert "anchor_ids" not in oriented_record
    assert "input_record_ids" not in oriented_record
    assert "source_revision_ids" not in oriented_record
    diagnostics = storage.display_turns(1)[0]["diagnostics"]
    assert diagnostics["knowledge_context"] == {
        "status": "used",
        "requested": True,
        "attempted": True,
        "retrieval_succeeded": True,
        "used": True,
        "snapshot_id": "snap_current",
        "snapshot_schema_version": "derived-schema-v1",
        "record_ids": ["rec_orientation"],
        "record_count": 1,
        "budget": {"max_records": 8, "max_tokens": 4_000, "used_tokens": 96, "truncated": False},
    }
    assert (diagnostics["input_tokens"], diagnostics["output_tokens"], diagnostics["total_tokens"]) == (100, 20, 120)
    assert (diagnostics["cached_input_tokens"], diagnostics["cache_write_tokens"], diagnostics["reasoning_tokens"]) == (5, 8, 9)
    saved = json.dumps(storage.thread_items(1)) + json.dumps(storage.display_turns(1))
    assert "Derived orientation cue that must not persist in replay." not in saved
    assert "anc_orientation" not in saved


def test_direct_orientation_never_enters_thread_or_replay_state(
    tmp_path, monkeypatch
):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_legacy")
    monkeypatch.setattr(storage, "current_snapshot", published_snapshot)
    final = source_response("Raw answer.", [])
    responses = FakeResponses(final)

    ChatService(
        storage,
        SimpleNamespace(responses=responses),
        orientation_service=FakeOrientation(orientation_result()),
    ).reply(storage.create_thread("Question"), "How do SMT and TPD work together?")

    assert len(responses.calls) == 1
    assert "Derived orientation cue that must not persist in replay." in responses.calls[0]["instructions"]
    saved = json.dumps(storage.thread_items(1)) + json.dumps(storage.replay_items(1))
    assert "Derived orientation cue that must not persist in replay." not in saved


@pytest.mark.parametrize(
    ("question", "requested_depth", "expected"),
    [
        ("How do SMT, TPD, and narrative interact?", "auto", True),
        ("What changed between Jacob's 2025 and 2026 teaching?", "auto", True),
        ("What factors affect setup probability?", "auto", True),
        ("How does Jacob's whole system fit together?", "auto", True),
        ("Tell me everything Jacob teaches about timing.", "auto", True),
        ("Give me all of Jacob's teachings on timing.", "normal", True),
        ("Give me a complete mapping of Jacob's timeframes.", "deep", True),
        ("Teach me about SMT.", "auto", True),
        ("What does TPD stand for?", "deep", False),
        ("Compare the exact source quotes from 2025 and 2026.", "auto", False),
        ("Give me every exact source quote for SMT.", "normal", False),
        ("Show the exact location, then compare the teachings.", "auto", False),
        ("Quote Jacob's wording and compare it with 2026.", "auto", False),
        ("Give me the timestamp and compare the two lessons.", "auto", False),
    ],
)
def test_orientation_intent_uses_question_and_effective_research_depth(question, requested_depth, expected):
    assert _should_orient(question, _effective_research_depth(question, requested_depth)) is expected


def test_broad_system_question_requires_orientation_before_raw_research():
    assert _should_orient(
        "Explain how the major parts of Jacob's system fit together.", "normal"
    ) is True


def test_empty_orientation_is_audited_as_not_admitted_context(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_legacy")
    monkeypatch.setattr(storage, "current_snapshot", published_snapshot)
    empty = OrientationResult(
        snapshot_id="snap_current",
        snapshot_schema_version="derived-schema-v1",
        records=(),
        used_tokens=0,
        budget=OrientationBudget(max_records=8, max_tokens=4_000),
        truncated=False,
        duplicate_result_count=0,
        discarded_result_count=0,
    )
    responses = FakeResponses(source_response("Raw answer.", []))

    answer = ChatService(
        storage,
        SimpleNamespace(responses=responses),
        orientation_service=FakeOrientation(empty),
    ).reply(storage.create_thread("Question"), "Explain how the major parts fit together.")

    assert answer.diagnostics.knowledge_context == {
        "status": "empty",
        "requested": True,
        "attempted": True,
        "retrieval_succeeded": True,
        "used": False,
        "snapshot_id": "snap_current",
        "snapshot_schema_version": "derived-schema-v1",
        "record_ids": [],
        "record_count": 0,
        "budget": {"max_records": 8, "max_tokens": 4_000, "used_tokens": 0, "truncated": False},
    }


def test_snapshot_bound_raw_and_derived_stores_cannot_cross_runtime_boundaries(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    pilot_snapshot = SimpleNamespace(
        snapshot_id="snap_pilot",
        schema_version="derived-schema-v1",
        status="published",
        raw_store_id="vs_pilot_raw",
        derived_store_id="vs_pilot_derived",
    )
    monkeypatch.setattr(storage, "current_snapshot", lambda: pilot_snapshot)
    responses = FakeResponses(source_response("Raw answer.", []))
    orientation = FakeOrientation(orientation_result())

    ChatService(storage, SimpleNamespace(responses=responses), orientation_service=orientation).reply(
        storage.create_thread("Question"), "Explain how the major parts fit together."
    )

    assert orientation.calls == [
        ("Explain how the major parts fit together.", {"snapshot": pilot_snapshot})
    ]
    assert responses.calls[0]["tools"] == [
        {"type": "file_search", "vector_store_ids": ["vs_pilot_raw"], "max_num_results": 8}
    ]


@pytest.mark.parametrize("orientation_error", [None, RuntimeError("derived lookup failed")])
def test_orientation_latency_covers_the_orientation_attempt_and_raw_continuation(
    tmp_path, monkeypatch, orientation_error
):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_legacy")
    monkeypatch.setattr(storage, "current_snapshot", published_snapshot)
    clock = {"value": 0.0}
    monkeypatch.setattr("mentor.chat_service.perf_counter", lambda: clock["value"])

    class TimedOrientation(FakeOrientation):
        def consult(self, question, **kwargs):
            clock["value"] = 0.1
            return super().consult(question, **kwargs)

    class TimedResponses(FakeResponses):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if len(self.calls) == 1:
                clock["value"] = 0.25
            return response

    responses = TimedResponses(source_response("Raw answer.", []))
    answer = ChatService(
        storage,
        SimpleNamespace(responses=responses),
        orientation_service=TimedOrientation(orientation_result(), orientation_error),
    ).reply(storage.create_thread("Question"), "How do SMT and TPD work together?")

    assert answer.diagnostics.latency_ms == 250


@pytest.mark.parametrize(
    "question",
    [
        "What does TPD stand for?",
        "Where exactly does Jacob say this? Give me the video and timestamp.",
    ],
)
def test_narrow_and_exact_questions_do_not_add_an_orientation_call(tmp_path, monkeypatch, question):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_legacy")
    monkeypatch.setattr(storage, "current_snapshot", published_snapshot)
    responses = FakeResponses(
        source_response(
            "Direct source teaching: Raw File Search remains authoritative.",
            [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
        )
    )
    orientation = FakeOrientation(error=AssertionError("narrow questions must not orient"))

    ChatService(storage, SimpleNamespace(responses=responses), orientation_service=orientation).reply(
        storage.create_thread("Question"), question
    )

    assert orientation.calls == []
    assert len(responses.calls) == 1
    assert "tool_choice" not in responses.calls[0]
    assert all(tool["type"] != "function" for tool in responses.calls[0]["tools"])


def test_broad_question_with_no_valid_published_snapshot_stays_on_the_legacy_raw_store(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_legacy")
    monkeypatch.setattr(
        storage,
        "current_snapshot",
        lambda: SimpleNamespace(status="archived", raw_store_id="vs_stale", derived_store_id="vs_stale_derived"),
    )
    responses = FakeResponses(source_response("Raw answer.", []))
    orientation = FakeOrientation(error=AssertionError("stale snapshots must not orient"))

    ChatService(storage, SimpleNamespace(responses=responses), orientation_service=orientation).reply(
        storage.create_thread("Question"), "Compare the 2025 and 2026 teachings."
    )

    assert orientation.calls == []
    assert responses.calls[0]["tools"] == [
        {"type": "file_search", "vector_store_ids": ["vs_legacy"], "max_num_results": 20}
    ]


def test_orientation_tool_failure_is_auditable_and_recovers_with_raw_search(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_legacy")
    monkeypatch.setattr(storage, "current_snapshot", published_snapshot)
    responses = FakeResponses(source_response(
        "Direct source teaching: Raw File Search verified this.",
        [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
    ))

    answer = ChatService(
        storage,
        SimpleNamespace(responses=responses),
        orientation_service=FakeOrientation(error=RuntimeError("remote derived lookup failed")),
    ).reply(storage.create_thread("Question"), "How do SMT and TPD work together?")

    assert answer.citations[0].file_id == "file_jacob"
    assert len(responses.calls) == 1
    assert "Derived orientation" not in responses.calls[0]["instructions"]
    diagnostics = storage.response_diagnostics(1)[0]["knowledge_context"]
    assert diagnostics["status"] == "unavailable"
    assert diagnostics["used"] is False
    assert "remote derived lookup failed" not in json.dumps(diagnostics)


def test_broad_streaming_preserves_native_compaction_without_replaying_orientation_payload(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_legacy")
    monkeypatch.setattr(storage, "current_snapshot", published_snapshot)
    final = SimpleNamespace(
        status="completed",
        output=[
            {"type": "compaction", "id": "cmp_1", "encrypted_content": "opaque-state"},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Raw answer."}]},
        ],
    )
    responses = SequenceResponses([
        SimpleNamespace(type="response.output_text.delta", delta="Raw answer."),
        SimpleNamespace(type="response.completed", response=final),
    ])

    events = list(
        ChatService(
            storage,
            SimpleNamespace(responses=responses),
            orientation_service=FakeOrientation(orientation_result()),
        ).stream_reply(storage.create_thread("Question"), "How does Jacob's system fit together?")
    )

    assert [event.type for event in events] == ["delta", "complete"]
    assert responses.calls[0]["stream"] is True
    assert storage.replay_items(1) == final.output
    replay = json.dumps(storage.replay_items(1))
    assert "Derived orientation cue that must not persist in replay." not in replay
    assert "anc_orientation" not in replay


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
                "knowledge_context": None,
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
    assert "raw source evidence\noverride any derived orientation" in MENTOR_INSTRUCTIONS


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
    assert _effective_research_depth("What changed between Jacob's 2025 and 2026 teachings?", "auto") == "deep"
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
