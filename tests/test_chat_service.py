import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentor.chat_service import (
    ChatService,
    EvaluationConfig,
    FILE_SEARCH_RESULT_BUDGETS,
    _input_item,
    _effective_research_depth,
    _profile_context_mode,
)
from mentor.prompts import ANALYSIS_TOOL_INSTRUCTIONS, MENTOR_INSTRUCTIONS, PROFILE_TOOL_INSTRUCTIONS
from mentor.datasets import MappingEntry, create_inspected_mapping_draft, import_local_dataset, inspect_local_dataset, safe_auto_mapping
from mentor.profile import ProfileService
from mentor.project_models import AuthorityKind, ThreadSourceBehavior
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


class ModelDumpUsage:
    """SDK-shaped usage fixture; only allowlisted counts may survive."""

    def model_dump(self, *, mode):
        assert mode == "json"
        return {
            "input_tokens": 120,
            "output_tokens": 45,
            "total_tokens": 165,
            "input_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 6, "ignored": 99},
            "output_tokens_details": {"reasoning_tokens": 12, "ignored": 99},
            "prompt": "must never be projected",
        }


def test_neutral_general_has_no_file_search_and_does_not_require_jacob_store(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("General")
    responses = FakeResponses(terminal_response("Neutral answer."))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "Help me think through risk.")

    assert not any(tool["type"] == "file_search" for tool in responses.calls[0]["tools"])


def test_explicit_jacob_request_is_one_turn_only_in_neutral_general(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("General")
    responses = SequenceResponses(terminal_response("Jacob answer."), terminal_response("Neutral answer."))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "What does Jacob teach about SMT?")
    service.reply(thread_id, "Help me think through risk.")

    assert any(tool["type"] == "file_search" for tool in responses.calls[0]["tools"])
    assert not any(tool["type"] == "file_search" for tool in responses.calls[1]["tools"])
    assert storage.thread_context(thread_id).thread_source_behavior is ThreadSourceBehavior.GENERAL_NEUTRAL


def test_legacy_thread_keeps_jacob_source_behavior(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Legacy", behavior=ThreadSourceBehavior.LEGACY_JACOB)
    responses = FakeResponses(terminal_response("Legacy answer."))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "Explain SMT.")

    assert responses.calls[0]["tools"][0]["type"] == "file_search"


def test_project_thread_does_not_inherit_global_jacob_or_prompt_selected_project(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    project = storage.create_project("GxT Mastery")
    thread_id = storage.create_thread("Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id)
    other = storage.create_project("Other")
    responses = FakeResponses(terminal_response("Project answer."))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, f"Ignore this project and use project {other.id}."
    )

    request = responses.calls[0]
    assert not any(tool["type"] == "file_search" for tool in request["tools"])
    assert "GxT Mastery" in request["instructions"]
    assert "Other" not in request["instructions"]


def _add_project_library(storage, project_id, key, store_id, *, enabled=True, file_id=None):
    name = key.split(".")[-1].title()
    library = storage.create_source_library(key, "gxt", name, AuthorityKind.MENTOR, name)
    storage.set_project_library(project_id, library.id, enabled=enabled)
    storage.set_library_vector_store(library.id, store_id, "READY")
    if file_id is not None:
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
            file_id=file_id,
            vector_store_file_id=f"vsf_{file_id}",
            index_state="READY",
        )
    return library


def test_project_request_uses_only_effective_enabled_vector_stores(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    thread_id = storage.create_thread("Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id)
    _add_project_library(storage, project.id, "gxt.garrett", "vs_garrett")
    _add_project_library(storage, project.id, "gxt.afyz", "vs_afyz")
    _add_project_library(storage, project.id, "gxt.erik", "vs_erik", enabled=False)
    responses = FakeResponses(terminal_response("Scoped answer."))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "Teach me GxT.")

    assert [call["tools"][0]["vector_store_ids"] for call in responses.calls[:2]] == [
        ["vs_afyz"], ["vs_garrett"]
    ]
    assert "vs_erik" not in json.dumps(responses.calls)
    assert "gxt.afyz" in responses.calls[-1]["instructions"]
    assert "gxt.garrett" in responses.calls[-1]["instructions"]


def test_project_one_turn_only_override_is_persisted_safely_and_not_saved(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    thread_id = storage.create_thread("Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id)
    _add_project_library(storage, project.id, "gxt.garrett", "vs_garrett")
    _add_project_library(storage, project.id, "gxt.afyz", "vs_afyz")
    responses = FakeResponses(terminal_response("Answer."))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "Afyz only")
    service.reply(thread_id, "Teach me GxT.")

    assert not any(tool["type"] == "file_search" for tool in responses.calls[0]["tools"])
    assert [call["tools"][0]["vector_store_ids"] for call in responses.calls[1:3]] == [
        ["vs_afyz"], ["vs_garrett"]
    ]
    assert storage.display_turns(thread_id)[0]["source_scope"] == {
        "library_keys": ["gxt.afyz"], "temporary": True, "override": "only"
    }


def test_normal_project_teaching_instructions_require_enabled_mentor_coverage(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    thread_id = storage.create_thread("Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id)
    for label in ("garrett", "afyz", "erik", "splash", "zay"):
        _add_project_library(storage, project.id, f"gxt.{label}", f"vs_{label}")
    responses = FakeResponses(terminal_response("Teaching."))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Teach me how X works in GxT."
    )

    instructions = responses.calls[-1]["instructions"]
    assert "Research each planned mentor library" in instructions
    assert all(f"gxt.{label}" in instructions for label in ("garrett", "afyz", "erik", "splash", "zay"))
    assert "creator status is not empirical superiority" in instructions


def _project_source_response(key, file_id, statement):
    return SimpleNamespace(
        id=f"resp_{key}", model="gpt-5.6-sol", status="completed", usage=None,
        output=[
            {
                "type": "file_search_call", "queries": [f"{key} synthetic query"],
                "results": [{
                    "file_id": file_id, "filename": f"{key}.txt", "text": statement,
                    "attributes": {"library_key": key, "timestamps_available": "true"},
                }],
            },
            {
                "type": "message", "role": "assistant",
                "content": [{
                    "type": "output_text", "text": statement,
                    "annotations": [{"type": "file_citation", "file_id": file_id, "filename": f"{key}.txt"}],
                }],
            },
        ],
    )


def test_project_research_is_one_store_per_call_and_keeps_native_citations_out_of_replay(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    thread_id = storage.create_thread("Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id)
    keys = ("gxt.garrett", "gxt.afyz", "gxt.erik")
    for key in keys:
        _add_project_library(storage, project.id, key, f"vs_{key}", file_id=f"file_{key}")
    research = [_project_source_response(key, f"file_{key}", f"{key} supports X.") for key in sorted(keys)]
    responses = SequenceResponses(*research, terminal_response("Source synthesis: X is shared."))

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Teach me how X works in GxT."
    )

    assert [call["tools"][0]["vector_store_ids"] for call in responses.calls[:3]] == [
        [f"vs_{key}"] for key in sorted(keys)
    ]
    assert not any(tool["type"] == "file_search" for tool in responses.calls[-1]["tools"])
    assert {citation.file_id for citation in answer.citations} == {f"file_{key}" for key in keys}
    assert len(answer.evidence) == 3
    assert answer.diagnostics.mentor_search_calls == {key: 1 for key in sorted(keys)}
    assert answer.diagnostics.source_scope == {
        "library_keys": list(sorted(keys)), "temporary": False, "override": "saved"
    }
    assert all(item.get("type") != "file_search_call" for item in storage.replay_items(thread_id))
    assert all(item.get("type") != "file_search_call" for item in storage.thread_items(thread_id))


def test_project_research_rejects_a_result_owned_by_another_library_before_persistence(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    thread_id = storage.create_thread("Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id)
    _add_project_library(storage, project.id, "gxt.garrett", "vs_garrett", file_id="file_garrett")
    _add_project_library(storage, project.id, "gxt.afyz", "vs_afyz", file_id="file_afyz")
    responses = SequenceResponses(
        _project_source_response("gxt.afyz", "file_garrett", "Wrong-owner result."),
    )

    with pytest.raises(RuntimeError, match="ownership"):
        ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "Teach me GxT.")

    assert storage.display_turns(thread_id) == []
    assert storage.replay_items(thread_id) == []


def test_project_research_keeps_no_result_as_scoped_absence_not_a_fabricated_disagreement(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    thread_id = storage.create_thread("Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id)
    _add_project_library(storage, project.id, "gxt.garrett", "vs_garrett", file_id="file_garrett")
    _add_project_library(storage, project.id, "gxt.afyz", "vs_afyz", file_id="file_afyz")
    absent = SimpleNamespace(
        id="resp_absent", model="gpt-5.6-sol", status="completed", usage=None,
        output=[
            {"type": "file_search_call", "queries": ["gxt.afyz X"], "results": []},
            {"type": "message", "role": "assistant", "content": [{
                "type": "output_text", "text": "No relevant Afyz evidence was found in this scoped search.",
                "annotations": [],
            }]},
        ],
    )
    responses = SequenceResponses(
        absent,
        _project_source_response("gxt.garrett", "file_garrett", "Garrett supports X."),
        terminal_response("Garrett supports X; this search found no Afyz evidence."),
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Teach me how X works in GxT."
    )

    assert answer.text == "Garrett supports X; this search found no Afyz evidence."
    assert [citation.file_id for citation in answer.citations] == ["file_garrett"]
    assert answer.diagnostics.mentor_search_calls == {"gxt.afyz": 1, "gxt.garrett": 1}


def test_project_state_tool_updates_only_the_owning_project_and_replays_safely(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    thread_id = storage.create_thread("Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id)
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[{
            "type": "function_call", "call_id": "next-action-1", "name": "update_project_state",
            "arguments": json.dumps({
                "kind": "NEXT_ACTION", "operation": "SET", "value": "Define the entry condition."
            }),
        }]),
        terminal_response("Your next action is saved."),
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Set our next action to define the entry condition."
    )

    assert answer.text == "Your next action is saved."
    assert storage.project_roadmap(project.id)["next_action"] == "Define the entry condition."
    assert any(tool.get("name") == "update_project_state" for tool in responses.calls[0]["tools"])
    assert any(item.get("name") == "update_project_state" for item in storage.replay_items(thread_id))
    assert "Define the entry condition." in responses.calls[1]["input"][-1]["output"]


def test_general_request_receives_only_bounded_project_summary_not_project_tools(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    project_thread = storage.create_thread(
        "Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id
    )
    storage.apply_project_state_event(
        project_id=project.id, event_key="objective-1", kind="OBJECTIVE",
        payload={"operation": "SET", "value": "Operationalize one setup"},
        origin_thread_id=project_thread, origin_turn_number=1,
    )
    general_thread = storage.create_thread("General")
    responses = FakeResponses(terminal_response("GxT is focused on one setup."))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(general_thread, "What am I working on?")

    assert "Operationalize one setup" in responses.calls[0]["instructions"]
    assert "recent_research" not in responses.calls[0]["instructions"]
    assert not any(tool.get("name", "").startswith("update_project_") for tool in responses.calls[0]["tools"])


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


def _scoped_analysis_dataset(tmp_path, *, allow_notes=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    source = tmp_path / "trades.csv"
    source.write_text(
        "Result,Outcome,Session,Journal\n1,Win,London,waited for confirmation\n-1,Loss,New York,entered early\n",
        encoding="utf-8",
    )
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "chat-analysis").dataset
    draft = create_inspected_mapping_draft(
        storage,
        inspect_local_dataset(storage, dataset.id),
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="trade_outcome"),
            MappingEntry(2, analysis_label="Session", model_disclosure=True),
            MappingEntry(
                3,
                analysis_label="Journal",
                mentor_access="allow_row_values_when_analysing_notes" if allow_notes else "aggregates_only",
            ),
        ],
    )
    mapping = storage.confirm_mapping_version(draft.id)
    thread_id = storage.create_thread("Analysis")
    storage.set_thread_dataset_scope(thread_id, dataset.id)
    fields = {entry.analysis_label or entry.semantic_role: entry.field_id for entry in storage.mapping_entries(mapping.id)}
    return storage, thread_id, dataset, mapping, fields


def analysis_tool_call(name, arguments, *, call_id="call_analysis"):
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments),
    }


def terminal_response(text="The local evidence is bounded.", *, usage=None):
    return SimpleNamespace(
        status="completed",
        usage=usage,
        output=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}],
    )


def test_analysis_batch_runs_locally_and_persists_bounded_empirical_evidence(tmp_path):
    storage, thread_id, dataset, mapping, fields = _scoped_analysis_dataset(tmp_path)
    first = SimpleNamespace(
        status="completed",
        output=[
            analysis_tool_call("summarize_results", {"filters": []}, call_id="summary"),
            analysis_tool_call("group_results", {"group_field_ids": [fields["Session"]], "filters": []}, call_id="groups"),
        ],
    )
    responses = SequenceResponses(first, terminal_response())

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "How did this dataset perform by session?")

    assert answer.text == "The local evidence is bounded."
    assert len(responses.calls) == 2
    outputs = [item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output"]
    assert len(outputs) == 2
    assert all(json.loads(item["output"])["provenance"] == "USER_EMPIRICAL_EVIDENCE" for item in outputs)
    assert len(storage.analysis_evidence(thread_id)) == 2
    assert len(storage.analysis_tool_outputs(thread_id)) == 2
    assert all("Journal" not in item["output"] for item in outputs)
    assert all(tool.get("name") != "summarize_results" for tool in responses.calls[1]["tools"])
    assert answer.diagnostics.qualitative_calls == 0


def test_exact_smt_critical_prompt_persists_a_boolean_comparison_and_completes(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    source = tmp_path / "trades.csv"
    source.write_text(
        "Result_R,Outcome,SMT\n2,Win,true\n-1,Loss,true\n1,Win,false\n-1,Loss,false\n",
        encoding="utf-8",
    )
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "smt-critical").dataset
    inspection = inspect_local_dataset(storage, dataset.id)
    mapping = storage.confirm_mapping_version(create_inspected_mapping_draft(storage, inspection, safe_auto_mapping(inspection).entries).id)
    thread_id = storage.create_thread("SMT critical test")
    storage.set_thread_dataset_scope(thread_id, dataset.id)
    smt_field_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "SMT")
    prompt = (
        "You said SMT looks like the strongest consistency filter. Critically test that conclusion. "
        "Compare SMT vs non-SMT overall, not just by quarter, and tell me how strong the evidence actually is. "
        "Try to disprove the idea rather than confirm it."
    )
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[analysis_tool_call(
            "compare_groups", {"field_id": smt_field_id, "value_a": True, "value_b": False, "filters": []}, call_id="smt"
        )]),
        terminal_response("The comparison is descriptive and needs further testing."),
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, prompt)

    output = next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")
    comparison = json.loads(output["output"])["comparison"]
    assert answer.text == "The comparison is descriptive and needs further testing."
    assert comparison["a"]["value"] is True and comparison["b"]["value"] is False
    assert len(storage.analysis_evidence(thread_id)) == 1
    assert prompt in responses.calls[0]["input"][-1]["content"][0]["text"]


def test_exact_session_and_setup_prompt_runs_grouping_after_safe_auto_mapping(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    source = tmp_path / "trades.csv"
    source.write_text(
        "Result_R,Outcome,Session,Setup\n2,Win,London,Reversal\n-1,Loss,London,Continuation\n1,Win,New York,Reversal\n",
        encoding="utf-8",
    )
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "session-setup").dataset
    inspection = inspect_local_dataset(storage, dataset.id)
    mapping = storage.confirm_mapping_version(create_inspected_mapping_draft(storage, inspection, safe_auto_mapping(inspection).entries).id)
    thread_id = storage.create_thread("Session setup")
    storage.set_thread_dataset_scope(thread_id, dataset.id)
    fields = {entry.analysis_label: entry.field_id for entry in storage.mapping_entries(mapping.id)}
    prompt = "Now break down performance by session and by setup. Show N, win rate, expectancy, and exclusions for each. Which differences look meaningful and which are too small to trust?"
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[analysis_tool_call(
            "group_results", {"group_field_ids": [fields["Session"], fields["Setup"]], "filters": []}, call_id="session-setup"
        )]),
        terminal_response("The groups are descriptive; the small cells need cautious interpretation."),
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, prompt)

    result = json.loads(next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")["output"])
    assert answer.text == "The groups are descriptive; the small cells need cautious interpretation."
    assert result["operation"] == "group_results"
    assert result["grouping"]["field_ids"] == [fields["Session"], fields["Setup"]]


def test_existing_session_setup_mapping_upgrades_before_the_exact_grouping_prompt(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    source = tmp_path / "trades.csv"
    source.write_text(
        "Result_R,Outcome,Session,Setup\n2,Win,London,Reversal\n-1,Loss,New York,Continuation\n",
        encoding="utf-8",
    )
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "legacy-session-setup").dataset
    inspection = inspect_local_dataset(storage, dataset.id)
    legacy_entries = [
        replace(entry, analysis_label=None, model_disclosure=False)
        if entry.semantic_role in {"session", "setup"} else entry
        for entry in safe_auto_mapping(inspection).entries
    ]
    legacy = storage.confirm_mapping_version(create_inspected_mapping_draft(
        storage, inspection, legacy_entries, auto_mapping_policy_version=1
    ).id)
    thread_id = storage.create_thread("Existing Session setup")
    storage.set_thread_dataset_scope(thread_id, dataset.id)
    fields = {entry.semantic_role: entry.field_id for entry in storage.mapping_entries(legacy.id)}
    prompt = "Now break down performance by session and by setup. Show N, win rate, expectancy, and exclusions for each."
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[analysis_tool_call(
            "group_results", {"group_field_ids": [fields["session"], fields["setup"]], "filters": []}, call_id="session-setup"
        )]),
        terminal_response("The groups are descriptive."),
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, prompt)

    current = storage.confirmed_mapping_for_dataset(dataset.id)
    assert answer.diagnostics.auto_mapping_policy_upgraded is True
    assert current is not None and current.id != legacy.id
    assert all(entry.aggregate_labels_allowed for entry in storage.mapping_entries(current.id) if entry.semantic_role in {"session", "setup"})
    assert json.loads(next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")["output"])["operation"] == "group_results"
    assert "never mention mapping versions" in responses.calls[0]["instructions"]


def test_invalid_analysis_tool_arguments_return_a_structured_rejection_and_continue(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path)
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[analysis_tool_call(
            "compare_groups", {"field_id": fields["Session"], "value_a": "London", "value_b": "Atlantis", "filters": []}
        )]),
        terminal_response("I could not compare that unavailable group."),
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Compare performance by session, including Atlantis."
    )

    output = next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")
    assert answer.text == "I could not compare that unavailable group."
    assert json.loads(output["output"]) == {"status": "rejected", "reason": "invalid_analysis_arguments"}


def test_prior_dataset_bound_empirical_evidence_is_marked_as_reused(tmp_path):
    storage, thread_id, _dataset, _mapping, _fields = _scoped_analysis_dataset(tmp_path)
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[analysis_tool_call("summarize_results", {"filters": []}, call_id="summary")]),
        terminal_response("First deterministic summary."),
        terminal_response("The prior deterministic summary still applies."),
    )
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "Analyze this backtest and tell me what stands out.")
    answer = service.reply(
        thread_id,
        "What are my overall win rate and expectancy? Also tell me exactly how many trades were usable for each calculation, what was excluded, and why.",
    )

    assert answer.diagnostics.analysis_calls == {"requested": 0, "executed": 0, "rejected": 0}
    assert answer.diagnostics.prior_empirical_evidence_reused is True
    assert any(item.get("type") == "function_call_output" for item in responses.calls[2]["input"])


def test_replaced_dataset_cannot_replay_stale_empirical_evidence(tmp_path):
    storage, thread_id, _dataset, _mapping, _fields = _scoped_analysis_dataset(tmp_path)
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[analysis_tool_call("summarize_results", {"filters": []}, call_id="summary")]),
        terminal_response("First deterministic summary."),
        terminal_response("The replacement needs fresh analysis."),
    )
    service = ChatService(storage, SimpleNamespace(responses=responses))
    service.reply(thread_id, "Analyze this backtest and tell me what stands out.")

    source = tmp_path / "replacement.csv"
    source.write_text("Result,Outcome,Session\n2,Win,Asia\n", encoding="utf-8")
    replacement = import_local_dataset(source, storage, dataset_id_factory=lambda: "replacement-analysis").dataset
    replacement_mapping = storage.confirm_mapping_version(create_inspected_mapping_draft(
        storage,
        inspect_local_dataset(storage, replacement.id),
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="trade_outcome"),
            MappingEntry(2, analysis_label="Session", model_disclosure=True),
        ],
    ).id)
    storage.set_thread_dataset_scope(thread_id, replacement.id)
    assert storage.replay_items(thread_id) == []

    answer = service.reply(thread_id, "What are my overall win rate and expectancy?")

    assert answer.diagnostics.prior_empirical_evidence_reused is False
    assert all(item.get("type") != "function_call_output" for item in responses.calls[2]["input"])
    assert "First deterministic summary." not in json.dumps(responses.calls[2]["input"])
    assert "replaced dataset; it is not empirical evidence" in responses.calls[2]["instructions"]
    assert replacement_mapping.dataset_id == replacement.id


@pytest.mark.parametrize("prompt", [
    "Now break down performance by session and by setup. Show N, win rate, expectancy, and exclusions for each. Which differences look meaningful and which are too small to trust?",
    "I noticed Medium-adherence early entries performed badly. Should I just add a rule saying I never take those trades anymore?",
    "Did this edge stay stable through time, or were most of the profits concentrated in one part of the backtest? Analyze the performance chronologically.",
])
def test_remaining_human_gate_prompts_receive_the_empirical_policy(tmp_path, prompt):
    storage, thread_id, _dataset, _mapping, _fields = _scoped_analysis_dataset(tmp_path)
    responses = SequenceResponses(terminal_response("Use deterministic evidence before deciding."))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, prompt)

    assert responses.calls[0]["input"][-1]["content"][0]["text"] == prompt
    assert "User empirical evidence" in responses.calls[0]["instructions"]
    assert "candidate hypothesis" in responses.calls[0]["instructions"]


def test_empirical_provenance_and_post_hoc_guidance_are_explicit_in_the_prompt_policy():
    assert "Reserve Source synthesis" in MENTOR_INSTRUCTIONS
    assert "User empirical evidence" in MENTOR_INSTRUCTIONS
    assert "AI interpretation" in MENTOR_INSTRUCTIONS
    assert "AI research hypothesis" in MENTOR_INSTRUCTIONS
    assert "AI recommendation" in MENTOR_INSTRUCTIONS
    assert "candidate hypothesis" in ANALYSIS_TOOL_INSTRUCTIONS
    assert "unseen/out-of-sample confirmation" in ANALYSIS_TOOL_INSTRUCTIONS


def test_mixed_profile_and_analysis_batch_is_rejected_without_mutation_or_analysis(tmp_path):
    storage, thread_id, _dataset, _mapping, _fields = _scoped_analysis_dataset(tmp_path)
    first = SimpleNamespace(
        status="completed",
        output=[
            profile_tool_call(arguments=profile_write_arguments()).output[0],
            analysis_tool_call("summarize_results", {"filters": []}, call_id="summary"),
        ],
    )
    responses = SequenceResponses(first, terminal_response("Separate those actions."))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "Remember that I trade ES and summarize my data.")

    outputs = [item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output"]
    assert all(json.loads(item["output"])["reason"] == "mixed_local_tool_batch_not_supported" for item in outputs)
    assert storage.current_confirmed_profile_items() == []
    assert storage.analysis_evidence(thread_id) == []


def test_qualitative_tool_needs_current_turn_server_consent_and_never_replays_notes(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    first = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call("read_text_evidence", {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"}, call_id="notes")],
    )
    responses = SequenceResponses(first, terminal_response("The disclosed notes suggest checking entry timing."))

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Read my approved notes.", include_approved_notes=False
    )

    output = next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")
    assert json.loads(output["output"])["reason"] == "qualitative_consent_required"
    assert answer.diagnostics.analysis_calls == {"requested": 1, "executed": 0, "rejected": 1}
    assert answer.diagnostics.qualitative_calls == 1
    assert answer.diagnostics.qualitative_review is None
    assert "waited for confirmation" not in json.dumps(storage.thread_items(thread_id))


def test_rejected_qualitative_call_leaves_neither_call_nor_output_in_future_replay(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    first = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call(
            "read_text_evidence",
            {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"},
            call_id="notes",
        )],
    )
    responses = SequenceResponses(first, terminal_response("Numbers only."), terminal_response("The next turn is valid."))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "Read my notes.", include_approved_notes=False)

    assert not any(item.get("call_id") == "notes" for item in storage.replay_items(thread_id))
    service.reply(thread_id, "Continue without notes.")
    assert not any(item.get("call_id") == "notes" for item in responses.calls[2]["input"])


def test_orphaned_local_tool_output_is_rejected_before_any_responses_request(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Protocol")
    storage.replace_replay_items(thread_id, [{
        "type": "function_call_output",
        "call_id": "orphan",
        "output": json.dumps({"status": "rejected", "reason": "qualitative_consent_required"}),
    }])
    responses = FakeResponses([SimpleNamespace(type="response.completed", response=terminal_response())])

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(thread_id, "Continue."))

    assert [event.type for event in events] == ["error"]
    assert events[0].error_classification == "invalid_tool_protocol"
    assert responses.calls == []


@pytest.mark.parametrize("tool_name", ["summarize_results", "read_text_evidence"])
def test_unanswered_local_call_is_rejected_before_any_responses_request(tmp_path, tool_name):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Protocol")
    storage.replace_replay_items(thread_id, [{
        "type": "function_call",
        "call_id": "unanswered",
        "name": tool_name,
        "arguments": "{}",
    }])
    responses = FakeResponses([SimpleNamespace(type="response.completed", response=terminal_response())])

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(thread_id, "Continue."))

    assert [event.type for event in events] == ["error"]
    assert events[0].error_classification == "invalid_tool_protocol"
    assert responses.calls == []


def test_streaming_provider_failure_logs_only_safe_error_metadata(tmp_path, caplog):
    class ProviderError(Exception):
        status_code = 400
        type = "invalid_request_error"
        code = "invalid_tool_protocol"
        param = "input"
        request_id = "req_safe_123"

    class RaisingResponses:
        def __init__(self):
            self.calls = []

        def create(self, **request):
            self.calls.append(request)
            raise ProviderError("RAW_NOTE_SENTINEL_MUST_NOT_LOG")

    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Provider failure")
    responses = RaisingResponses()
    caplog.set_level("WARNING", logger="mentor.chat_service")

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(
        thread_id, "RAW_NOTE_SENTINEL_MUST_NOT_LOG"
    ))

    assert [event.type for event in events] == ["error"]
    assert events[0].error_classification == "responses_continuation_error"
    assert "stage=initial_stream" in caplog.text
    assert "class=ProviderError" in caplog.text
    assert "status=400" in caplog.text
    assert "type=invalid_request_error" in caplog.text
    assert "code=invalid_tool_protocol" in caplog.text
    assert "param=input" in caplog.text
    assert "request_id=req_safe_123" in caplog.text
    assert "RAW_NOTE_SENTINEL_MUST_NOT_LOG" not in caplog.text


def test_streaming_provider_error_metadata_rejects_untrusted_attribute_text(tmp_path, caplog):
    class ProviderError(Exception):
        status_code = 400
        type = "RAW_NOTE_SENTINEL_TYPE"
        code = "RAW_NOTE_SENTINEL_CODE"
        param = "RAW_NOTE_SENTINEL_PARAM"
        request_id = "RAW_NOTE_SENTINEL_REQUEST_ID"

    class RaisingResponses:
        def create(self, **request):
            raise ProviderError("RAW_NOTE_SENTINEL_MESSAGE")

    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Provider failure")
    caplog.set_level("WARNING", logger="mentor.chat_service")

    list(ChatService(storage, SimpleNamespace(responses=RaisingResponses())).stream_reply(thread_id, "Question"))

    assert "type=None" in caplog.text
    assert "code=None" in caplog.text
    assert "param=None" in caplog.text
    assert "request_id=None" in caplog.text
    assert "RAW_NOTE_SENTINEL" not in caplog.text


def test_streaming_provider_error_metadata_rejects_non_string_attributes(tmp_path, caplog):
    class ProviderError(Exception):
        status_code = 400
        type = []
        code = {}
        param = ()
        request_id = []

    class RaisingResponses:
        def create(self, **request):
            raise ProviderError("provider failure")

    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Provider failure")
    caplog.set_level("WARNING", logger="mentor.chat_service")

    events = list(ChatService(storage, SimpleNamespace(responses=RaisingResponses())).stream_reply(thread_id, "Question"))

    assert [event.type for event in events] == ["error"]
    assert "type=None" in caplog.text
    assert "code=None" in caplog.text
    assert "param=None" in caplog.text
    assert "request_id=None" in caplog.text


def test_qualitative_transport_failure_stays_in_the_safe_stream_error_path(tmp_path, monkeypatch):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    initial = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call(
            "read_text_evidence",
            {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"},
            call_id="notes",
        )],
    )
    responses = FakeResponses([SimpleNamespace(type="response.completed", response=initial)])

    def fail_transport(**_kwargs):
        from mentor.datasets import QualitativeTransportError
        raise QualitativeTransportError("qualitative model transport failed")

    monkeypatch.setattr("mentor.chat_service.continue_qualitative_model_transport", fail_transport)
    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(
        thread_id, "Read my notes.", include_approved_notes=True
    ))

    assert [event.type for event in events] == ["error"]
    assert events[0].error_classification == "qualitative_continuation_error"


def test_approved_qualitative_notes_use_only_the_ephemeral_transport_and_persist_safe_metadata(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    first = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call("read_text_evidence", {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"}, call_id="notes")],
    )
    responses = SequenceResponses(first, terminal_response("The notes show a possible entry-timing pattern."))

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Read my approved notes.", include_approved_notes=True
    )

    assert answer.text == "The notes show a possible entry-timing pattern."
    assert answer.diagnostics.analysis_calls == {"requested": 1, "executed": 1, "rejected": 0}
    assert answer.diagnostics.qualitative_calls == 1
    assert answer.diagnostics.analysis_batch_status == "complete"
    assert answer.diagnostics.qualitative_review == {
        "returned_rows": 2,
        "usable_text_rows": 2,
        "omitted_rows": 0,
        "complete": True,
        "context_field_count": 0,
    }
    assert answer.diagnostics.input_tokens is None
    assert "waited for confirmation" in responses.calls[1]["input"][-1]["output"]
    persisted = json.dumps({
        "thread": storage.thread_items(thread_id),
        "replay": storage.replay_items(thread_id),
        "metadata": storage.qualitative_metadata(thread_id),
        "diagnostics": storage.response_diagnostics(thread_id),
    })
    assert "waited for confirmation" not in persisted
    assert "entered early" not in persisted
    assert all(item.get("name") != "read_text_evidence" for item in storage.replay_items(thread_id))
    assert storage.qualitative_metadata(thread_id)[0]["returned_rows"] == 2


def test_qualitative_diagnostics_include_safe_context_counts_and_sdk_usage(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    first = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call(
            "read_text_evidence",
            {"text_field_ids": [fields["Journal"]], "context_field_ids": [fields["Session"]], "filters": [], "order_by": "source"},
            call_id="notes",
        )],
    )
    responses = SequenceResponses(first, terminal_response("The notes suggest an entry-timing pattern.", usage=ModelDumpUsage()))

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Read my approved notes with session context.", include_approved_notes=True
    )

    assert answer.diagnostics.qualitative_review == {
        "returned_rows": 2,
        "usable_text_rows": 2,
        "omitted_rows": 0,
        "complete": True,
        "context_field_count": 1,
    }
    assert (answer.diagnostics.input_tokens, answer.diagnostics.output_tokens, answer.diagnostics.total_tokens) == (120, 45, 165)
    assert (answer.diagnostics.cached_input_tokens, answer.diagnostics.cache_write_tokens, answer.diagnostics.reasoning_tokens) == (30, 6, 12)
    assert "must never be projected" not in json.dumps(storage.response_diagnostics(thread_id))


def test_qualitative_diagnostics_report_partial_review_without_note_text(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    source = tmp_path / "trades.csv"
    source.write_text(
        "Result,Outcome,Journal\n" + "\n".join(f"1,Win,synthetic note {index}" for index in range(101)),
        encoding="utf-8",
    )
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "partial-notes").dataset
    draft = create_inspected_mapping_draft(
        storage,
        inspect_local_dataset(storage, dataset.id),
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="trade_outcome"),
            MappingEntry(2, analysis_label="Journal", mentor_access="allow_row_values_when_analysing_notes"),
        ],
    )
    mapping = storage.confirm_mapping_version(draft.id)
    thread_id = storage.create_thread("Partial qualitative review")
    storage.set_thread_dataset_scope(thread_id, dataset.id)
    journal = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Journal")
    first = SimpleNamespace(status="completed", output=[analysis_tool_call(
        "read_text_evidence",
        {"text_field_ids": [journal], "context_field_ids": [], "filters": [], "order_by": "source"},
        call_id="notes",
    )])
    answer = ChatService(storage, SimpleNamespace(responses=SequenceResponses(first, terminal_response()))).reply(
        thread_id, "Read my approved notes.", include_approved_notes=True
    )

    assert answer.diagnostics.qualitative_review == {
        "returned_rows": 100,
        "usable_text_rows": 101,
        "omitted_rows": 1,
        "complete": False,
        "context_field_count": 0,
    }
    persisted = json.dumps({"replay": storage.replay_items(thread_id), "diagnostics": storage.response_diagnostics(thread_id)})
    assert "synthetic note 0" not in persisted


def test_analysis_batch_limit_and_wrong_thread_scope_are_rejected_locally(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("No scope")
    no_scope = SequenceResponses(
        SimpleNamespace(status="completed", output=[analysis_tool_call("summarize_results", {"filters": []})]),
        terminal_response(),
    )
    ChatService(storage, SimpleNamespace(responses=no_scope)).reply(thread_id, "Summarize my data.")
    output = next(item for item in no_scope.calls[1]["input"] if item.get("type") == "function_call_output")
    assert json.loads(output["output"])["reason"] == "no_active_dataset"

    scoped, scoped_thread, _dataset, _mapping, _fields = _scoped_analysis_dataset(tmp_path / "scoped")
    over_limit = SequenceResponses(
        SimpleNamespace(
            status="completed",
            output=[analysis_tool_call("summarize_results", {"filters": []}, call_id=f"call_{index}") for index in range(7)],
        ),
        terminal_response(),
    )
    answer = ChatService(scoped, SimpleNamespace(responses=over_limit)).reply(scoped_thread, "Analyze this backtest and tell me what stands out.")
    outputs = [json.loads(item["output"]) for item in over_limit.calls[1]["input"] if item.get("type") == "function_call_output"]
    assert len(outputs) == 7
    assert all(output["provenance"] == "USER_EMPIRICAL_EVIDENCE" for output in outputs[:6])
    assert outputs[6]["reason"] == "analysis_call_limit_exceeded"
    assert len(scoped.analysis_evidence(scoped_thread)) == 6
    assert answer.diagnostics.analysis_calls == {"requested": 7, "executed": 6, "rejected": 1}
    assert answer.diagnostics.analysis_batch_status == "partial"
    assert 0 < answer.diagnostics.deterministic_result_chars <= 32_000


def test_dataset_attachment_is_persisted_with_only_its_sent_user_turn(tmp_path):
    storage, thread_id, dataset, _mapping, _fields = _scoped_analysis_dataset(tmp_path)
    responses = SequenceResponses(terminal_response("The deterministic summary is ready."))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id,
        "Analyze this backtest and tell me what stands out.",
        dataset_attachment_id=dataset.id,
    )

    turns = storage.display_turns(thread_id)
    assert turns[0]["attachment"] == {
        "dataset_id": dataset.id,
        "original_name": "trades.csv",
        "source_row_count": 2,
    }
    assert storage.thread_dataset_scope(thread_id).dataset_id == dataset.id
    storage.initialize()
    assert storage.display_turns(thread_id)[0]["attachment"] == turns[0]["attachment"]
    other_thread = storage.create_thread("Separate")
    assert storage.display_turns(other_thread) == []
    assert storage.thread_dataset_scope(other_thread) is None


def test_inspect_dataset_exposes_only_safe_mapping_contract(tmp_path):
    storage, thread_id, _dataset, _mapping, _fields = _scoped_analysis_dataset(tmp_path)
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[analysis_tool_call("inspect_dataset", {}, call_id="inspect")]),
        terminal_response(),
    )

    ChatService(storage, SimpleNamespace(responses=responses)).reply(thread_id, "What local analysis is available?")

    output = next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")
    payload = json.loads(output["output"])
    assert payload["operation"] == "inspect_dataset"
    assert payload["source_rows"] == 2
    assert "waited for confirmation" not in json.dumps(payload)
    assert storage.analysis_evidence(thread_id) == []


def test_qualitative_citation_repair_uses_safe_replay_without_redisclosing_notes(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    first = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call("read_text_evidence", {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"}, call_id="notes")],
    )
    uncited = source_response("Direct source teaching: Jacob teaches patience.", [])
    repaired = source_response(
        "Direct source teaching: Jacob teaches patience.",
        [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
    )
    responses = SequenceResponses(first, uncited, repaired)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id, "Read my approved notes and relate them to Jacob.", include_approved_notes=True
    )

    assert len(responses.calls) == 3
    assert answer.citations[0].file_id == "file_jacob"
    assert "waited for confirmation" not in json.dumps(responses.calls[2]["input"])
    assert all(tool.get("type") != "function" for tool in responses.calls[2]["tools"])


def test_qualitative_exchange_reasoning_is_ephemeral_but_same_turn_citation_repair_succeeds(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    first = SimpleNamespace(
        status="completed",
        output=[
            {"type": "reasoning", "encrypted_content": "SECRET_NOTE_REPLAY_STATE_8"},
            analysis_tool_call(
                "read_text_evidence",
                {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"},
                call_id="notes",
            ),
        ],
    )
    uncited = SimpleNamespace(
        status="completed",
        output=[
            {"type": "reasoning", "encrypted_content": "SECRET_NOTE_REPLAY_STATE_8_AFTER_TOOL"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Direct source teaching: Jacob teaches patience.", "annotations": []}],
            },
        ],
    )
    repaired = source_response(
        "Direct source teaching: Jacob teaches patience.",
        [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
    )
    responses = SequenceResponses(first, uncited, repaired, terminal_response("A later turn is ordinary."))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    answer = service.reply(thread_id, "Read my approved notes and relate them to Jacob.", include_approved_notes=True)

    assert answer.citations[0].file_id == "file_jacob"
    assert "waited for confirmation" in responses.calls[1]["input"][-1]["output"]
    repair_input = responses.calls[2]["input"]
    assert not any(item.get("type") in {"reasoning", "function_call", "function_call_output"} for item in repair_input)
    persisted = json.dumps({
        "thread": storage.thread_items(thread_id),
        "replay": storage.replay_items(thread_id),
        "metadata": storage.qualitative_metadata(thread_id),
    })
    assert "SECRET_NOTE_REPLAY_STATE_8" not in persisted
    assert "waited for confirmation" not in persisted
    assert all(item.get("type") not in {"reasoning", "function_call", "function_call_output"} for item in storage.replay_items(thread_id))

    service.reply(thread_id, "What should I check next?")

    future_input = responses.calls[3]["input"]
    assert not any("SECRET_NOTE_REPLAY_STATE_8" in json.dumps(item) for item in future_input)
    assert all(item.get("type") not in {"reasoning", "function_call", "function_call_output"} for item in future_input)
    assert any(item.get("role") == "user" and item["content"][0]["text"] == "Read my approved notes and relate them to Jacob." for item in future_input)
    assert any(item.get("type") == "message" and item.get("role") == "assistant" for item in future_input)


def test_mixed_numeric_and_qualitative_turn_keeps_empirical_evidence_but_not_qualitative_replay_state(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    first = SimpleNamespace(
        status="completed",
        output=[
            {"type": "reasoning", "encrypted_content": "SECRET_NOTE_REPLAY_STATE_8_MIXED"},
            analysis_tool_call("summarize_results", {"filters": []}, call_id="summary"),
            analysis_tool_call(
                "read_text_evidence",
                {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"},
                call_id="notes",
            ),
        ],
    )
    continued = SimpleNamespace(
        status="completed",
        output=[
            {"type": "reasoning", "encrypted_content": "SECRET_NOTE_REPLAY_STATE_8_MIXED_AFTER_TOOL"},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "The numeric result and notes both matter."}]},
        ],
    )
    responses = SequenceResponses(first, continued, terminal_response("The next turn has no note access."))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "Use the numbers and my approved notes.", include_approved_notes=True)

    assert len(storage.analysis_evidence(thread_id)) == 1
    assert storage.qualitative_metadata(thread_id)[0]["returned_rows"] == 2
    assert all(item.get("type") not in {"reasoning", "function_call", "function_call_output"} for item in storage.replay_items(thread_id))
    assert "SECRET_NOTE_REPLAY_STATE_8_MIXED" not in json.dumps(storage.replay_items(thread_id))

    service.reply(thread_id, "Continue without the notes.")

    assert "waited for confirmation" not in json.dumps(responses.calls[2]["input"])
    assert "SECRET_NOTE_REPLAY_STATE_8_MIXED" not in json.dumps(responses.calls[2]["input"])


def test_qualitative_timestamp_repair_uses_only_visible_draft_state(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    citation = [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}]
    first = SimpleNamespace(
        status="completed",
        output=[
            {"type": "reasoning", "encrypted_content": "SECRET_NOTE_REPLAY_TIMESTAMP_8"},
            analysis_tool_call(
                "read_text_evidence",
                {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"},
                call_id="notes",
            ),
        ],
    )
    draft = source_response("Direct source teaching: Jacob says this at 12:10–12:36.", citation)
    draft.output.insert(0, {"type": "reasoning", "encrypted_content": "SECRET_NOTE_REPLAY_TIMESTAMP_8_AFTER_TOOL"})
    draft.output[1]["results"][0]["text"] = "[609.0 --> 616.0] An unrelated passage."
    repaired = source_response("Direct source teaching: Jacob says this at 12:10–12:36.", citation)
    responses = SequenceResponses(first, draft, repaired)

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        thread_id,
        "Read my approved notes. Where exactly does Jacob say this? Give me the timestamp.",
        include_approved_notes=True,
    )

    assert answer.text == "Direct source teaching: Jacob says this at 12:10–12:36."
    repair = responses.calls[2]
    assert repair["tool_choice"] == {"type": "file_search"}
    assert not any(item.get("type") in {"reasoning", "function_call", "function_call_output"} for item in repair["input"])
    assert "SECRET_NOTE_REPLAY_TIMESTAMP_8" not in json.dumps(repair)


def test_later_qualitative_turn_requires_fresh_consent_and_never_resurrects_prior_exchange(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    call = lambda call_id: analysis_tool_call(
        "read_text_evidence",
        {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"},
        call_id=call_id,
    )
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[{"type": "reasoning", "encrypted_content": "SECRET_NOTE_CONSENT_A"}, call("notes_a")]),
        terminal_response("Turn A visible answer."),
        SimpleNamespace(status="completed", output=[call("notes_b")]),
        terminal_response("Turn B needs consent."),
        SimpleNamespace(status="completed", output=[call("notes_c")]),
        terminal_response("Turn C visible answer."),
    )
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(thread_id, "Read approved notes for turn A.", include_approved_notes=True)
    service.reply(thread_id, "Read notes again for turn B.", include_approved_notes=False)
    service.reply(thread_id, "Read newly approved notes for turn C.", include_approved_notes=True)

    assert "waited for confirmation" in responses.calls[1]["input"][-1]["output"]
    assert "waited for confirmation" not in json.dumps(responses.calls[2]["input"])
    rejection = next(item for item in responses.calls[3]["input"] if item.get("type") == "function_call_output")
    assert json.loads(rejection["output"])["reason"] == "qualitative_consent_required"
    assert "waited for confirmation" in responses.calls[5]["input"][-1]["output"]
    historic = json.dumps(storage.replay_items(thread_id))
    assert "SECRET_NOTE_CONSENT_A" not in historic
    assert "waited for confirmation" not in historic


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


def test_personalization_changes_only_bounded_profile_input_not_raw_source_configuration(tmp_path):
    question = "What should I backtest first?"
    requests = []
    for market in ("ES", "NQ"):
        storage = Storage(tmp_path / f"{market}.sqlite3")
        storage.initialize()
        storage.set_vector_store("vs_jacob")
        ProfileService(storage).create_item(
            category="markets/instruments",
            subject="Primary market",
            value=market,
            kind="fact",
            provenance="USER_STATED",
            state="confirmed",
            origin_kind="profile-editor",
        )
        responses = FakeResponses(SimpleNamespace(status="completed", output=[]))
        ChatService(storage, SimpleNamespace(responses=responses)).reply(storage.create_thread("Question"), question)
        requests.append(responses.calls[0])

    profile_a, profile_b = requests
    assert "Primary market: ES" in profile_a["instructions"]
    assert "Primary market: NQ" in profile_b["instructions"]
    assert profile_a["input"] == profile_b["input"]
    assert profile_a["tools"] == profile_b["tools"]
    assert profile_a["include"] == profile_b["include"]
    assert profile_a["reasoning"] == profile_b["reasoning"]
    assert profile_a["context_management"] == profile_b["context_management"]


def test_explicit_strategy_design_uses_broader_questionnaire_context_without_replaying_it(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).save_questionnaire_answers({
        "q1": "Build consistent income.",
        "q2": "ES and NQ.",
        "q3": "London open.",
        "q4": "idk",
        "q7": "Some judgement.",
        "q13": "One percent risk maximum.",
        "q15": "No overnight positions.",
        "q19": "A simple repeatable system.",
    })
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(storage.create_thread("Strategy"), "I don't know what I should backtest — help me develop a strategy.")

    request = responses.calls[0]
    assert "Trader Strategy Profile — user context, not source evidence:" in request["instructions"]
    assert "[ANSWERED] trading objective: Build consistent income." in request["instructions"]
    assert "[EXPLICITLY UNKNOWN] preferred trading style" in request["instructions"]
    assert "[ANSWERED] risk and funding constraints: One percent risk maximum." in request["instructions"]
    assert all("Trader Strategy Profile" not in json.dumps(item) for item in request["input"])


def test_exact_human_profile_overview_prompt_uses_full_profile_state_without_file_search(tmp_path):
    question = (
        "Based only on my Trader Profile, what do you actually know about me, what is explicitly unresolved, "
        "what have I left unanswered, and what are you merely inferring? Do not research Jacob for this."
    )
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).save_questionnaire_answers({"q1": "At least 2R.", "q8": "idk"})
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(storage.create_thread("Profile"), question)

    request = responses.calls[0]
    assert _profile_context_mode(question) == "full_profile"
    assert "Trader Profile — user context, not source evidence:" in request["instructions"]
    assert "[ANSWERED] trading objective: At least 2R." in request["instructions"]
    assert "[EXPLICITLY UNKNOWN] trading strengths" in request["instructions"]
    assert "[UNANSWERED] trusted and uncertain concepts" in request["instructions"]
    assert all(tool["type"] != "file_search" for tool in request["tools"])


def test_exact_human_strategy_prompt_uses_full_strategy_snapshot_and_preserves_answered_minimums(tmp_path):
    question = (
        "I want to start developing a trading strategy from scratch around me. Use my full Trader Profile. "
        "Do not assume answers to anything I've marked unknown. Tell me what kind of strategy we're currently "
        "aiming toward, what constraints are actually known, what important things are still unresolved, and—most "
        "importantly—what you think our first research/backtesting step should be."
    )
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).save_questionnaire_answers({
        "q1": "Ideally I want at least 70% win rate and at least 2R.",
        "q6": "I want at least one opportunity per day.",
        "q13": "idk",
        "q14": "High win rate, minimum 2R.",
        "q16": "idk",
        "q20": "idk",
    })
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(storage.create_thread("Strategy"), question)

    request = responses.calls[0]
    assert _profile_context_mode(question) == "strategy"
    assert "Trader Strategy Profile — user context, not source evidence:" in request["instructions"]
    assert "at least 70% win rate and at least 2R" in request["instructions"]
    assert "High win rate, minimum 2R." in request["instructions"]
    assert "[ANSWERED] preferred trade frequency: I want at least one opportunity per day." in request["instructions"]
    assert "[EXPLICITLY UNKNOWN] risk and funding constraints" in request["instructions"]
    assert "[EXPLICITLY UNKNOWN] backtesting commitment" in request["instructions"]
    assert "[EXPLICITLY UNKNOWN] optimisation principles" in request["instructions"]
    assert "[UNANSWERED] trusted and uncertain concepts" in request["instructions"]
    assert all(tool["type"] != "file_search" for tool in request["tools"])


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What's in my Trader Profile?", "full_profile"),
        ("What do you know about me as a trader?", "full_profile"),
        ("Summarize my Trader Profile.", "full_profile"),
        ("What have I left unanswered?", "full_profile"),
        ("Which profile questions are unresolved?", "full_profile"),
        ("What did I put for my trading preferences?", "full_profile"),
        ("Use my full Trader Profile.", "full_profile"),
        ("Let's design my trading system.", "strategy"),
        ("Use my full Trader Profile and help me figure out what system I should build.", "strategy"),
        ("What is Jacob's strategy?", "none"),
        ("Explain strategy expectancy.", "relevant"),
    ],
)
def test_profile_context_modes_cover_natural_profile_and_strategy_variants(question, expected):
    assert _profile_context_mode(question) == expected


@pytest.mark.parametrize(
    ("question", "answers", "state", "expected"),
    [
        (
            "Based on my Trader Profile, what am I naturally good at?",
            {"q8": "idk"},
            "EXPLICITLY UNKNOWN",
            "must not become a current profile fact",
        ),
        (
            "Which concepts do I currently trust most?",
            {},
            "UNANSWERED",
            "has not answered this field",
        ),
        (
            "What should you optimise around for me?",
            {"q20": "idk", "q14": "70% win rate and at least 2R."},
            "EXPLICITLY UNKNOWN",
            "optimisation principles",
        ),
    ],
)
def test_direct_profile_state_questions_are_explicit_bounded_and_do_not_offer_file_search(
    tmp_path, question, answers, state, expected
):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).save_questionnaire_answers(answers)
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(storage.create_thread("Profile"), question)

    request = responses.calls[0]
    assert "Trader Profile field state — user context, not source evidence:" in request["instructions"]
    assert state in request["instructions"]
    assert expected in request["instructions"]
    if state == "EXPLICITLY UNKNOWN":
        assert "Start with: Your Trader Profile says this is currently unresolved" in request["instructions"]
    else:
        assert "Start with: You have not answered this in your Trader Profile" in request["instructions"]
    assert all(tool["type"] != "file_search" for tool in request["tools"])
    assert all("Trader Profile field state" not in json.dumps(item) for item in request["input"])


def test_direct_profile_state_with_explicit_jacob_request_keeps_raw_source_tool_and_state_separate(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).save_questionnaire_answers({"q20": "idk"})
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        storage.create_thread("Profile"), "What should you optimise around for me according to Jacob?"
    )

    request = responses.calls[0]
    assert "EXPLICITLY UNKNOWN" in request["instructions"]
    assert request["tools"][0]["type"] == "file_search"


def test_source_only_question_does_not_receive_personal_field_state(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).save_questionnaire_answers({"q12": "A London reversal setup."})
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        storage.create_thread("Source"), "What does Jacob say about the ideal trade setup?"
    )

    request = responses.calls[0]
    assert "Trader Profile field state" not in request["instructions"]
    assert request["tools"][0]["type"] == "file_search"


def test_ordinary_methodology_question_keeps_file_search_even_when_it_overlaps_a_questionnaire_topic(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).save_questionnaire_answers({"q12": "A London reversal setup."})
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        storage.create_thread("Methodology", behavior=ThreadSourceBehavior.LEGACY_JACOB), "How does a reversal setup work?"
    )

    request = responses.calls[0]
    assert "Trader Profile field state" not in request["instructions"]
    assert request["tools"][0]["type"] == "file_search"


@pytest.mark.parametrize("question", ["How does a reversal setup work for me?", "What trade setup is good for me?"])
def test_personal_methodology_question_does_not_disable_source_research_without_a_direct_field_match(tmp_path, question):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).save_questionnaire_answers({"q12": "A London reversal setup."})
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        storage.create_thread("Methodology", behavior=ThreadSourceBehavior.LEGACY_JACOB), question
    )

    request = responses.calls[0]
    assert "Trader Profile field state" not in request["instructions"]
    assert request["tools"][0]["type"] == "file_search"


def test_profile_policy_requires_unknown_blank_and_inference_separation():
    assert "EXPLICITLY UNKNOWN" in PROFILE_TOOL_INSTRUCTIONS
    assert "UNANSWERED" in PROFILE_TOOL_INSTRUCTIONS
    assert "AI hypothesis" in PROFILE_TOOL_INSTRUCTIONS
    assert "Jacob teaching must not resolve an unknown user" in PROFILE_TOOL_INSTRUCTIONS


def test_explicit_inference_request_keeps_unknown_profile_state_and_does_not_write_it(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    profile = ProfileService(storage)
    profile.save_questionnaire_answers({"q8": "idk"})
    before = storage.current_confirmed_profile_items()
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        storage.create_thread("Profile"), "What do YOU think I am naturally good at?"
    )

    request = responses.calls[0]
    assert "EXPLICITLY UNKNOWN" in request["instructions"]
    assert "AI hypothesis" in request["instructions"]
    assert storage.current_confirmed_profile_items() == before


def test_known_profile_goals_can_be_presented_as_conflicting_without_resolving_unknown_optimisation(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).save_questionnaire_answers({
        "q14": "70%+ win rate, at least 2R, daily opportunities, and green most days.",
        "q20": "idk",
    })
    responses = FakeResponses(SimpleNamespace(status="completed", output=[]))

    ChatService(storage, SimpleNamespace(responses=responses)).reply(
        storage.create_thread("Profile"), "What should you optimise around for me?"
    )

    instructions = responses.calls[0]["instructions"]
    assert "EXPLICITLY UNKNOWN" in instructions
    assert "70%+ win rate" in instructions
    assert "not a resolution of this unknown field" in instructions
    assert "potentially conflicting targets" in instructions
    assert all(tool["type"] != "file_search" for tool in responses.calls[0]["tools"])


def test_current_profile_survives_cross_thread_then_old_chat_cannot_restore_deleted_or_superseded_values(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    profile = ProfileService(storage)
    old_thread = storage.create_thread("Original profile statement")
    original = profile.create_item(
        category="markets/instruments",
        subject="Primary market",
        value="ES",
        kind="fact",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="chat",
        origin_thread_id=old_thread,
        origin_turn_number=1,
    )
    storage.record_display_turn(
        old_thread,
        user_text="Remember that I only trade ES.",
        answer_markdown="Saved.",
        citations=[],
        evidence=[],
        diagnostics=None,
        response_id=None,
        status="completed",
        incomplete_reason=None,
        profile_update={"kind": "saved"},
    )
    replacement = profile.supersede_item(
        original.id, value="NQ", provenance="USER_DECISION", origin_kind="profile-editor"
    )
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[]),
        SimpleNamespace(status="completed", output=[]),
    )
    service = ChatService(storage, SimpleNamespace(responses=responses))

    service.reply(storage.create_thread("Cross-thread question"), "What market should I backtest first?")
    assert "Primary market: NQ" in responses.calls[0]["instructions"]
    assert "Primary market: ES" not in responses.calls[0]["instructions"]

    assert profile.delete_item(replacement.id) is True
    service.reply(old_thread, "What market should I backtest first?")

    assert storage.display_turns(old_thread)[0]["user_text"] == "Remember that I only trade ES."
    assert "Trader Profile — user context" not in responses.calls[1]["instructions"]
    assert storage.current_confirmed_profile_items() == []


def test_profile_held_source_attribution_cannot_bypass_raw_source_lookup_or_citations(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    ProfileService(storage).create_item(
        category="style/methodology",
        subject="Methodology",
        value="Jacob teaches that Asset Synchronization guarantees direction.",
        kind="preference",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    responses = FakeResponses(
        source_response(
            "Direct source teaching: Jacob discusses Asset Synchronization.",
            [{"type": "file_citation", "file_id": "file_jacob", "filename": "lesson.txt"}],
        )
    )

    answer = ChatService(storage, SimpleNamespace(responses=responses)).reply(
        storage.create_thread("Source lookup"), "What does Jacob say about Asset Synchronization?"
    )

    request = responses.calls[0]
    assert "Trader Profile — user context" not in request["instructions"]
    assert "guarantees direction" not in request["instructions"]
    assert request["tools"][0]["type"] == "file_search"
    assert request["tools"][0]["vector_store_ids"] == ["vs_jacob"]
    assert [citation.file_id for citation in answer.citations] == ["file_jacob"]


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


def test_confirmed_profile_mutation_exposes_one_safe_persisted_acknowledgement(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    terminal = SimpleNamespace(
        status="completed",
        output=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Saved."}]}],
    )
    answer = ChatService(
        storage, SimpleNamespace(responses=SequenceResponses(profile_tool_call(arguments=profile_write_arguments()), terminal))
    ).reply(thread_id, "Remember that I only trade ES.")

    assert answer.profile_update == {"kind": "saved"}
    turn = storage.display_turns(thread_id)[0]
    assert turn["profile_update"] == {"kind": "saved"}
    safe_metadata = json.dumps(turn["profile_update"])
    assert "ES" not in safe_metadata
    assert "call_profile" not in safe_metadata
    assert "item_id" not in safe_metadata


def test_tentative_profile_proposal_is_acknowledged_as_inactive(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    terminal = SimpleNamespace(status="completed", output=[])
    arguments = profile_write_arguments(operation="propose")
    answer = ChatService(
        storage, SimpleNamespace(responses=SequenceResponses(profile_tool_call(arguments=arguments), terminal))
    ).reply(thread_id, "Remember that I only trade ES.")

    assert answer.profile_update == {"kind": "proposed"}
    assert storage.current_confirmed_profile_items() == []


def test_ordinary_turn_and_replayed_profile_call_do_not_create_new_acknowledgements(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    first_thread = storage.create_thread("First")
    second_thread = storage.create_thread("Second")
    terminal = SimpleNamespace(status="completed", output=[])
    responses = SequenceResponses(
        SimpleNamespace(status="completed", output=[]),
        profile_tool_call(arguments=profile_write_arguments()),
        terminal,
        profile_tool_call(arguments=profile_write_arguments()),
        terminal,
    )
    service = ChatService(storage, SimpleNamespace(responses=responses))

    ordinary = service.reply(first_thread, "What does Jacob teach?")
    saved = service.reply(first_thread, "Remember that I only trade ES.")
    replayed = service.reply(second_thread, "Remember that I only trade ES.")

    assert ordinary.profile_update is None
    assert saved.profile_update == {"kind": "saved"}
    assert replayed.profile_update is None
    assert len(storage.current_confirmed_profile_items()) == 1


def test_retry_after_a_failed_terminal_response_cannot_repeat_a_profile_mutation(tmp_path):
    class FailingContinuationResponses:
        def __init__(self, *responses):
            self.responses = list(responses)
            self.calls = []
            self.failed = False

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 2 and not self.failed:
                self.failed = True
                raise RuntimeError("terminal failure")
            return self.responses.pop(0)

    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    terminal = SimpleNamespace(status="completed", output=[])
    responses = FailingContinuationResponses(
        profile_tool_call(call_id="first", arguments=profile_write_arguments(operation="propose")),
        profile_tool_call(call_id="retry", arguments=profile_write_arguments(operation="propose")),
        terminal,
    )
    service = ChatService(storage, SimpleNamespace(responses=responses))

    with pytest.raises(RuntimeError, match="terminal failure"):
        service.reply(thread_id, "Remember that I only trade ES.")
    answer = service.reply(thread_id, "Remember that I only trade ES.")

    assert answer.profile_update is None
    assert len([item for item in storage.profile_items() if item.state == "tentative"]) == 1


def test_citation_repair_keeps_one_profile_acknowledgement_without_replaying_the_write(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    thread_id = storage.create_thread("Question")
    uncited = source_response("Direct source teaching: Jacob teaches this.", [])
    repaired = source_response("Direct source teaching: Jacob teaches this.", [])
    answer = ChatService(
        storage,
        SimpleNamespace(responses=SequenceResponses(profile_tool_call(arguments=profile_write_arguments()), uncited, repaired)),
    ).reply(thread_id, "Remember that I only trade ES.")

    assert answer.profile_update == {"kind": "saved"}
    assert len(storage.current_confirmed_profile_items()) == 1
    assert storage.display_turns(thread_id)[0]["profile_update"] == {"kind": "saved"}


def test_historical_turns_never_restore_deleted_or_superseded_profile_state(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Original")
    profile = ProfileService(storage)
    deleted = profile.create_item(
        category="markets/instruments", subject="Primary market", value="ES", kind="fact",
        provenance="USER_STATED", state="confirmed", origin_kind="chat", origin_thread_id=thread_id,
        origin_turn_number=1,
    )
    storage.record_display_turn(
        thread_id, user_text="Remember that I only trade ES.", answer_markdown="Saved.", citations=[], evidence=[],
        diagnostics=None, response_id=None, status="completed", incomplete_reason=None, profile_update={"kind": "saved"},
    )
    profile.delete_item(deleted.id)
    replacement = profile.create_item(
        category="schedule/horizon", subject="Holding period", value="Intraday", kind="preference",
        provenance="USER_STATED", state="confirmed", origin_kind="profile-editor",
    )
    profile.supersede_item(replacement.id, value="Two days", provenance="USER_DECISION", origin_kind="profile-editor")

    historical = storage.display_turns(thread_id)

    assert historical[0]["profile_update"] == {"kind": "saved"}
    assert all(item.value != "ES" for item in storage.profile_items())
    assert all(item.value != "Intraday" for item in storage.current_confirmed_profile_items())
    assert [item.value for item in storage.current_confirmed_profile_items()] == ["Two days"]


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
                "analysis_calls": {"requested": 0, "executed": 0, "rejected": 0},
                "analysis_operations": [],
                "deterministic_result_chars": 0,
                "qualitative_calls": 0,
                "qualitative_review": None,
                "analysis_batch_status": "not_requested",
                "prior_empirical_evidence_reused": False,
                "auto_mapping_policy_upgraded": False,
                "source_scope": None,
                "mentor_search_calls": {},
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


def test_streaming_qualitative_request_without_consent_pauses_before_persisting_turn(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    response = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call("read_text_evidence", {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"}, call_id="notes")],
    )
    responses = FakeResponses([SimpleNamespace(type="response.completed", response=response)])

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(thread_id, "Read my notes."))

    assert [event.type for event in events] == ["consent_required"]
    assert storage.display_turns(thread_id) == []
    assert len(responses.calls) == 1


def test_streaming_exact_auto_mapped_notes_pause_for_consent_without_manual_mapping(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    source = tmp_path / "phase5_mock_backtest.csv"
    source.write_text(
        "Result_R,Outcome,Trade_Notes\n1,Win,waited for confirmation\n-1,Loss,entered early\n",
        encoding="utf-8",
    )
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "auto-notes").dataset
    inspection = inspect_local_dataset(storage, dataset.id)
    mapping = storage.confirm_mapping_version(
        create_inspected_mapping_draft(storage, inspection, safe_auto_mapping(inspection).entries).id
    )
    thread_id = storage.create_thread("Analysis")
    storage.set_thread_dataset_scope(thread_id, dataset.id)
    notes = next(entry for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Trade notes")
    response = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call(
            "read_text_evidence",
            {"text_field_ids": [notes.field_id], "context_field_ids": [], "filters": [], "order_by": "source"},
            call_id="notes",
        )],
    )
    responses = SequenceResponses([SimpleNamespace(type="response.completed", response=response)], terminal_response())

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(
        thread_id, "Read my trade notes and tell me what recurring mistakes or behavioral patterns you notice."
    ))

    assert [event.type for event in events] == ["consent_required"]
    assert storage.display_turns(thread_id) == []
    assert len(responses.calls) == 1


def test_safe_qualitative_trade_context_pauses_for_consent_without_disclosure(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    source = tmp_path / "trades.csv"
    source.write_text(
        "Timestamp,Result,Outcome,Mistake tag,Rule adherence,Trade notes\n"
        "2026-01-01T09:30:00,1,Win,None,High,synthetic note one\n"
        "2026-01-02T09:30:00,-1,Loss,Early,Low,synthetic note two\n",
        encoding="utf-8",
    )
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "context-notes").dataset
    mapping = storage.confirm_mapping_version(create_inspected_mapping_draft(
        storage, inspect_local_dataset(storage, dataset.id),
        [
            MappingEntry(0, semantic_role="trade_timestamp"),
            MappingEntry(1, semantic_role="trade_return", unit="R"),
            MappingEntry(2, semantic_role="trade_outcome"),
            MappingEntry(3, analysis_label="Mistake tag", model_disclosure=True, source="deterministic_auto"),
            MappingEntry(4, analysis_label="Rule adherence", model_disclosure=True, source="deterministic_auto"),
            MappingEntry(5, analysis_label="Trade notes", mentor_access="allow_row_values_when_analysing_notes"),
        ],
    ).id)
    fields = {entry.analysis_label or entry.semantic_role: entry.field_id for entry in storage.mapping_entries(mapping.id)}
    assert all(
        entry.mentor_access == "aggregates_only"
        for entry in storage.mapping_entries(mapping.id)
        if entry.field_id in {fields["trade_timestamp"], fields["Mistake tag"], fields["Rule adherence"]}
    )
    thread_id = storage.create_thread("Analysis")
    storage.set_thread_dataset_scope(thread_id, dataset.id)
    response = SimpleNamespace(status="completed", output=[analysis_tool_call(
        "read_text_evidence",
        {
            "text_field_ids": [fields["Trade notes"]],
            "context_field_ids": [fields["trade_timestamp"], fields["Mistake tag"], fields["Rule adherence"]],
            "filters": [{"field_id": fields["Trade notes"], "operator": "not_blank", "value": None}],
            "order_by": "timestamp",
        },
        call_id="notes",
    )])
    responses = FakeResponses([SimpleNamespace(type="response.completed", response=response)])

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(
        thread_id, "Are there recurring themes in my trade notes?"
    ))

    assert [event.type for event in events] == ["consent_required"]
    assert events[0].qualitative_field_count == 1
    assert events[0].qualitative_context_field_count == 3
    assert storage.display_turns(thread_id) == []


def test_streaming_invalid_qualitative_request_does_not_offer_consent(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=False)
    response = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call("read_text_evidence", {"text_field_ids": [fields["Journal"]], "context_field_ids": [], "filters": [], "order_by": "source"}, call_id="notes")],
    )
    responses = SequenceResponses(
        [SimpleNamespace(type="response.completed", response=response)],
        terminal_response("That written field is not approved for row-level analysis."),
    )

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(thread_id, "Read my notes."))

    assert [event.type for event in events] == ["complete"]
    output = next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")
    assert json.loads(output["output"])["reason"] == "qualitative_text_not_eligible"


def test_streaming_incompatible_qualitative_filter_does_not_offer_consent(tmp_path):
    storage, thread_id, _dataset, _mapping, fields = _scoped_analysis_dataset(tmp_path, allow_notes=True)
    response = SimpleNamespace(
        status="completed",
        output=[analysis_tool_call(
            "read_text_evidence",
            {
                "text_field_ids": [fields["Journal"]],
                "context_field_ids": [],
                "filters": [{"field_id": fields["Journal"], "operator": "gt", "value": "early"}],
                "order_by": "source",
            },
            call_id="notes",
        )],
    )
    responses = SequenceResponses(
        [SimpleNamespace(type="response.completed", response=response)],
        terminal_response("That qualitative filter is not supported."),
    )

    events = list(ChatService(storage, SimpleNamespace(responses=responses)).stream_reply(thread_id, "Read my notes."))

    assert [event.type for event in events] == ["complete"]
    output = next(item for item in responses.calls[1]["input"] if item.get("type") == "function_call_output")
    assert json.loads(output["output"])["reason"] == "invalid_analysis_arguments"


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
    assert events[0].error_classification == "responses_continuation_error"
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
    thread_id = storage.create_thread("Question", behavior=ThreadSourceBehavior.LEGACY_JACOB)
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
