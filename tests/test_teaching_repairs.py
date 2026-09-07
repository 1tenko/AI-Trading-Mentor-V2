from types import SimpleNamespace

import pytest

from mentor.chat_service import Answer, ChatService, Citation, Evidence, _answer, _has_unsupported_exact_timestamp
from mentor.project_models import AuthorityKind, PedagogicalRole, ThreadSourceBehavior
from mentor.source_scope import (
    conversation_research_context,
    research_plan,
    resolve_source_scope,
)
from mentor.storage import Storage


def _project(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT Mastery")
    thread_id = storage.create_thread(
        "Lesson", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id
    )
    for key, role in (
        ("gxt.garrett", PedagogicalRole.FULL_MODEL_CREATOR),
        ("gxt.afyz", PedagogicalRole.FULL_MODEL_EDUCATOR),
    ):
        name = key.rsplit(".", 1)[-1].title()
        library = storage.create_source_library(
            key, "gxt", name, AuthorityKind.MENTOR, name, role
        )
        storage.set_project_library(project.id, library.id, enabled=True)
        storage.set_library_vector_store(library.id, f"vs_{name.casefold()}", "READY")
        storage.register_library_revision(
            library_id=library.id,
            source_key=f"{name.casefold()}.txt",
            display_title=f"{name}.txt",
            source_type="transcript",
            relative_category="Synthetic",
            source_date=None,
            timestamps_available=True,
            sha256=(str(library.id) * 64)[:64],
            byte_size=10,
            relative_path=f"Synthetic/{name}.txt",
            staged_path=f"ignored/{name}.txt",
            canonical_role=None,
            file_id=f"file_{name.casefold()}",
            vector_store_file_id=f"vsf_{name.casefold()}",
            index_state="READY",
        )
    return storage, thread_id


def _recent_source_turns():
    return [{
        "user_text": "Teach me phases of price using Garrett and Afyz.",
        "answer_markdown": "We are studying phases of price and continuation signatures.",
        "citations": [{"file_id": "file_example", "filename": "lesson.txt"}],
        "source_scope": {
            "library_keys": ["gxt.garrett", "gxt.afyz"],
            "temporary": True,
            "override": "compare",
        },
        "diagnostics": {"project_source_research": {"final_synthesis": "completed"}},
    }]


def _source_response(name):
    file_id = f"file_{name}"
    return SimpleNamespace(
        id=f"resp_{name}",
        model="gpt-5.6-luna",
        status="completed",
        usage=None,
        incomplete_details=None,
        output=[
            {
                "type": "file_search_call",
                "id": f"search_{name}",
                "status": "completed",
                "queries": ["synthetic query"],
                "results": [{
                    "file_id": file_id,
                    "filename": f"{name}.txt",
                    "text": "[00:05:03.000 --> 00:05:19.500] synthetic evidence",
                    "attributes": {"library_key": f"gxt.{name}"},
                }],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": f"{name.title()} synthetic digest.",
                    "annotations": [{
                        "type": "file_citation",
                        "file_id": file_id,
                        "filename": f"{name}.txt",
                    }],
                }],
            },
        ],
    )


def _terminal(text):
    return SimpleNamespace(
        id="resp_final",
        model="gpt-5.6-sol",
        status="completed",
        usage=None,
        incomplete_details=None,
        output=[{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
    )


def _terminal_with_citation(text, file_id="file_garrett"):
    response = _terminal(text)
    response.output[0]["content"][0]["annotations"] = [{
        "type": "file_citation",
        "file_id": file_id,
        "filename": "garrett.txt",
    }]
    return response


class _Responses:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        return self.responses.pop(0)


def test_contextual_exercise_question_inherits_source_lesson_instead_of_becoming_profile_only(tmp_path):
    storage, thread_id = _project(tmp_path)
    question = "What timeframe should I use for the exercise?"

    context = conversation_research_context(question, _recent_source_turns())
    scope = resolve_source_scope(
        storage,
        storage.thread_context(thread_id),
        context.scope_text,
        inherited_library_keys=context.inherited_library_keys,
    )

    assert context.source_required is True
    assert "continuation signatures" in context.query
    assert scope.library_keys == ("gxt.garrett", "gxt.afyz")
    assert research_plan(scope, context.query, "normal")


def test_source_challenge_with_questionnaire_wording_still_requires_fresh_research():
    question = (
        "You said my answer was wrong. Which video and timestamp support your correction? "
        "What timeframe should I use?"
    )

    context = conversation_research_context(question, _recent_source_turns())

    assert context.source_required is True
    assert context.inherited_library_keys == ("gxt.garrett", "gxt.afyz")


@pytest.mark.parametrize("question", (
    "Consolidation guarantees continuation, so no other confirmation matters. Is that correct?",
    "Keep Garrett and Afyz distinct: how does each connect consolidation, continuation, and confirmation?",
    "Give me one concrete beginner top-down chart exercise.",
))
def test_first_turn_teaching_benchmark_intents_require_source_research(tmp_path, question):
    storage, thread_id = _project(tmp_path)
    context = conversation_research_context(question)
    scope = resolve_source_scope(storage, storage.thread_context(thread_id), context.scope_text)

    assert context.source_required is True
    assert research_plan(scope, context.query, "normal")


def test_visual_capability_question_does_not_search_sources_to_pretend_it_can_see_a_chart(tmp_path):
    storage, thread_id = _project(tmp_path)
    question = "Can you verify that the chart on my screen shows the required confirmation?"
    context = conversation_research_context(question, _recent_source_turns())
    scope = resolve_source_scope(storage, storage.thread_context(thread_id), context.scope_text)

    assert context.source_required is False
    assert research_plan(scope, context.query, "normal") == ()


def test_mixed_source_and_visual_question_researches_the_source_but_keeps_visual_limit(tmp_path):
    storage, thread_id = _project(tmp_path)
    question = (
        "According to Garrett, what confirmation should I look for, "
        "and can you inspect this screenshot?"
    )
    context = conversation_research_context(question, _recent_source_turns())
    scope = resolve_source_scope(storage, storage.thread_context(thread_id), context.scope_text)

    assert context.source_required is True
    assert scope.library_keys == ("gxt.garrett",)
    assert research_plan(scope, context.query, "normal")


@pytest.mark.parametrize("question", (
    "What risk should I use?",
    "Should I trade more often?",
))
def test_personal_recommendation_does_not_become_source_research_merely_from_should_i(tmp_path, question):
    storage, thread_id = _project(tmp_path)
    context = conversation_research_context(question)
    scope = resolve_source_scope(storage, storage.thread_context(thread_id), context.scope_text)

    assert context.source_required is False
    assert research_plan(scope, context.query, "normal") == ()


def test_full_profile_request_with_source_teaching_still_runs_source_research(tmp_path):
    storage, thread_id = _project(tmp_path)
    responses = _Responses(
        _source_response("garrett"),
        _terminal("Profile-aware, source-grounded explanation."),
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id,
        "Using my full Trader Profile, explain Garrett's timeframe selection.",
    )

    assert answer.text == "Profile-aware, source-grounded explanation."
    assert len(responses.calls) == 2
    assert responses.calls[0]["tool_choice"] == {"type": "file_search"}


def test_exact_human_mixed_profile_teaching_turn_runs_fresh_project_research(tmp_path):
    storage, thread_id = _project(tmp_path)
    responses = _Responses(
        _source_response("garrett"),
        _source_response("afyz"),
        _terminal("Initial source-grounded lesson."),
        _source_response("garrett"),
        _source_response("afyz"),
        _terminal("Source-grounded feedback and exercise."),
    )
    service = ChatService(storage, SimpleNamespace(responses=responses))
    service.reply(thread_id, "Teach me phases of price using Garrett and Afyz.")

    answer = service.reply(
        thread_id,
        "My analysis is that price is consolidating, so consolidation is a continuation "
        "signature. Tell me what I got right or wrong and give me the next chart exercise.",
    )

    assert answer.text == "Source-grounded feedback and exercise."
    assert answer.citations == []
    assert answer.diagnostics.project_source_research["mentor_research"]["Garrett"]["citations"] == 1
    assert answer.diagnostics.project_source_research["mentor_research"]["Afyz"]["citations"] == 1
    assert len(responses.calls) == 6
    assert all(call["tool_choice"] == {"type": "file_search"} for call in responses.calls[3:5])
    assert "Recent lesson context" in responses.calls[3]["input"]
    final_instructions = responses.calls[5]["instructions"]
    assert "enabled mentor methodology" in final_instructions
    assert "Phase 1 proof" not in final_instructions
    assert "state the timeframe roles" in final_instructions
    assert "inspect the chart and report" in final_instructions
    assert "Start with: You have not answered this in your Trader Profile" not in final_instructions


def test_generic_i_want_source_lesson_does_not_force_an_unanswered_profile_opening(tmp_path):
    storage, thread_id = _project(tmp_path)
    responses = _Responses(
        _source_response("garrett"),
        _source_response("afyz"),
        _terminal("Source-grounded ideal-setup lesson."),
    )

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "I want you to teach me how GxT defines the ideal setup."
    )

    assert len(responses.calls) == 3
    assert "Start with: You have not answered this in your Trader Profile" not in responses.calls[-1]["instructions"]


def test_intermediate_research_citations_do_not_satisfy_final_direct_claim_citations(tmp_path):
    storage, thread_id = _project(tmp_path)
    responses = _Responses(
        _source_response("garrett"),
        _terminal("Direct source teaching: Garrett teaches the synthetic point."),
        _terminal_with_citation(
            "Direct source teaching: Garrett teaches the synthetic point."
        ),
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "What does Garrett teach about the synthetic point?"
    )

    assert len(responses.calls) == 3
    assert "Citation repair" in responses.calls[-1]["instructions"]
    assert [citation.file_id for citation in answer.citations] == ["file_garrett"]


def test_mentor_name_inside_quoted_prior_text_does_not_create_a_new_exclusive_scope(tmp_path):
    storage, thread_id = _project(tmp_path)
    question = 'Earlier you wrote: "Garrett says this is not a continuation signature." Check that claim.'
    context = conversation_research_context(question, _recent_source_turns())

    scope = resolve_source_scope(
        storage,
        storage.thread_context(thread_id),
        context.scope_text,
        inherited_library_keys=context.inherited_library_keys,
    )

    assert scope.library_keys == ("gxt.garrett", "gxt.afyz")
    assert scope.override == "continued"


@pytest.mark.parametrize("quoted", (
    '"Garrett says this is not a\ncontinuation signature."',
    "'Garrett says this is not a continuation signature.'",
))
def test_multiline_or_single_quoted_mentor_name_does_not_override_scope(tmp_path, quoted):
    storage, thread_id = _project(tmp_path)
    question = f"Earlier you wrote:\n{quoted} Check that claim."
    context = conversation_research_context(question, _recent_source_turns())

    scope = resolve_source_scope(
        storage,
        storage.thread_context(thread_id),
        context.scope_text,
        inherited_library_keys=context.inherited_library_keys,
    )

    assert scope.library_keys == ("gxt.garrett", "gxt.afyz")
    assert scope.override == "continued"


def test_literal_filecite_text_is_not_rendered_or_counted_as_a_native_citation():
    answer = _answer([{
        "type": "message",
        "role": "assistant",
        "content": [{
            "type": "output_text",
            "text": "Claim. fileciteturn0search0",
            "annotations": [],
        }],
    }])

    assert answer.text == "Claim."
    assert answer.citations == []


def test_literal_filecite_text_is_removed_before_history_and_replay_persistence(tmp_path):
    storage, thread_id = _project(tmp_path)
    marker = "\ue200filecite\ue202turn0search0\ue201"
    responses = _Responses(_terminal(f"Claim. {marker}"))

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Give me a brief coaching observation."
    )

    assert answer.text == "Claim."
    assert marker not in str(storage.thread_items(thread_id))
    assert marker not in str(storage.replay_items(thread_id))


def test_real_hh_mm_ss_millisecond_evidence_range_supports_exact_timestamp():
    answer = Answer(
        text="The lesson states this at 00:05:03-00:05:19.",
        citations=[Citation("file_garrett", "phases.txt")],
        evidence=[Evidence(
            "file_garrett",
            "phases.txt",
            "[00:05:03.000 --> 00:05:19.500] synthetic supporting passage",
            None,
            {},
        )],
    )

    assert _has_unsupported_exact_timestamp(
        "Which video and timestamp support that?", answer
    ) is False


def test_exact_timestamp_is_not_supported_by_a_different_cited_source_range():
    answer = Answer(
        text="The lesson states this at 00:05:03-00:05:19.",
        citations=[Citation("file_garrett", "phases.txt")],
        evidence=[
            Evidence(
                "file_other",
                "other.txt",
                "[00:05:03.000 --> 00:05:19.500] unrelated synthetic passage",
                None,
                {},
            ),
            Evidence(
                "file_garrett",
                "phases.txt",
                "[00:07:00.000 --> 00:07:10.000] different synthetic passage",
                None,
                {},
            ),
        ],
    )

    assert _has_unsupported_exact_timestamp(
        "Which video and timestamp support that?", answer
    ) is True


def test_exact_timestamp_must_match_every_cited_source_in_a_multi_source_answer():
    answer = Answer(
        text="The named lesson states this at 00:05:03-00:05:19.",
        citations=[
            Citation("file_named", "named.txt"),
            Citation("file_other", "other.txt"),
        ],
        evidence=[
            Evidence(
                "file_named", "named.txt",
                "[00:07:00.000 --> 00:07:10.000] different passage",
                None, {},
            ),
            Evidence(
                "file_other", "other.txt",
                "[00:05:03.000 --> 00:05:19.500] matching but wrong source",
                None, {},
            ),
        ],
    )

    assert _has_unsupported_exact_timestamp(
        "Which video and timestamp support that?", answer
    ) is True


def test_exact_timestamps_pair_with_the_nearest_indexed_native_citation():
    text = (
        "Garrett gives the example at 00:05:03-00:05:19. "
        "Afyz gives another at 00:07:00-00:07:10."
    )
    answer = Answer(
        text=text,
        citations=[
            Citation("file_garrett", "garrett.txt", (text.index(" Afyz"),)),
            Citation("file_afyz", "afyz.txt", (len(text),)),
        ],
        evidence=[
            Evidence(
                "file_garrett", "garrett.txt",
                "[00:05:03.000 --> 00:05:19.500] first passage",
                None, {},
            ),
            Evidence(
                "file_afyz", "afyz.txt",
                "[00:07:00.000 --> 00:07:10.500] second passage",
                None, {},
            ),
        ],
    )

    assert _has_unsupported_exact_timestamp(
        "Which videos and timestamps support those claims?", answer
    ) is False
