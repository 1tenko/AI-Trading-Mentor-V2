import json

import pytest

from mentor.project_models import ThreadSourceBehavior
from mentor.project_service import ProjectService
from mentor.project_tools import PROJECT_TOOLS, ProjectToolDispatcher
from mentor.storage import Storage


def _project_thread(tmp_path, name="GxT"):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project(name)
    thread_id = storage.create_thread(
        "Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id
    )
    return storage, project, thread_id


def _call(name, arguments, call_id="project-call-1"):
    return {"name": name, "call_id": call_id, "arguments": json.dumps(arguments)}


def test_project_tools_expose_only_constrained_state_and_mastery_updates():
    assert [tool["name"] for tool in PROJECT_TOOLS] == [
        "update_project_state", "update_project_mastery", "record_project_research"
    ]
    assert all(tool["type"] == "function" and tool["strict"] is True for tool in PROJECT_TOOLS)


def test_state_event_and_snapshot_commit_atomically_and_are_idempotent(tmp_path):
    storage, project, thread_id = _project_thread(tmp_path)
    dispatcher = ProjectToolDispatcher(storage)
    call = _call("update_project_state", {
        "kind": "NEXT_ACTION", "operation": "SET", "value": "Define the entry condition."
    })

    first = dispatcher.dispatch(thread_id, call, origin_turn_number=1)
    second = dispatcher.dispatch(thread_id, call, origin_turn_number=1)

    assert first["status"] == "applied"
    assert second["status"] == "already_applied"
    assert ProjectService(storage).roadmap(project.id)["next_action"] == "Define the entry condition."
    with storage._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_state_events").fetchone() == (1,)


def test_blockers_and_mastery_use_controlled_operations_and_vocabulary(tmp_path):
    storage, project, thread_id = _project_thread(tmp_path)
    dispatcher = ProjectToolDispatcher(storage)

    dispatcher.dispatch(thread_id, _call("update_project_state", {
        "kind": "BLOCKER", "operation": "ADD", "value": "Entry is not operational."
    }, "blocker-1"), origin_turn_number=1)
    dispatcher.dispatch(thread_id, _call("update_project_mastery", {
        "concept": "Session selection", "status": "OPERATIONALIZING",
        "reason": "Theo can explain it but has not frozen the rule.", "evidence_reference": None,
    }, "mastery-1"), origin_turn_number=2)

    roadmap = ProjectService(storage).roadmap(project.id)
    assert roadmap["blockers"] == ["Entry is not operational."]
    assert roadmap["mastery"] == [{
        "concept": "Session selection", "status": "OPERATIONALIZING",
        "reason": "Theo can explain it but has not frozen the rule.", "evidence_reference": None,
    }]
    with pytest.raises(ValueError, match="mastery status"):
        dispatcher.dispatch(thread_id, _call("update_project_mastery", {
            "concept": "Session selection", "status": "MASTERED",
            "reason": "No calibrated status.", "evidence_reference": None,
        }, "mastery-2"), origin_turn_number=3)


def test_project_tool_cannot_cross_project_or_write_an_adopted_rule(tmp_path):
    storage, project, thread_id = _project_thread(tmp_path)
    other = storage.create_project("Other")
    other_thread = storage.create_thread(
        "Other", behavior=ThreadSourceBehavior.PROJECT, project_id=other.id
    )
    dispatcher = ProjectToolDispatcher(storage)

    with pytest.raises(ValueError, match="unsupported project tool"):
        dispatcher.dispatch(thread_id, _call("adopt_playbook_rule", {"rule": "Always trade X"}), origin_turn_number=1)
    dispatcher.dispatch(other_thread, _call("update_project_state", {
        "kind": "OBJECTIVE", "operation": "SET", "value": "Other objective"
    }), origin_turn_number=1)

    assert ProjectService(storage).roadmap(project.id)["objective"] is None
    assert ProjectService(storage).roadmap(other.id)["objective"] == "Other objective"


def test_duplicate_event_key_with_different_payload_fails_closed(tmp_path):
    storage, _project, thread_id = _project_thread(tmp_path)
    dispatcher = ProjectToolDispatcher(storage)
    dispatcher.dispatch(thread_id, _call("update_project_state", {
        "kind": "OBJECTIVE", "operation": "SET", "value": "Learn GxT"
    }), origin_turn_number=1)

    with pytest.raises(ValueError, match="idempotency key"):
        dispatcher.dispatch(thread_id, _call("update_project_state", {
            "kind": "OBJECTIVE", "operation": "SET", "value": "Trade live"
        }), origin_turn_number=1)


def test_general_summary_is_bounded_to_safe_coaching_fields(tmp_path):
    storage, project, thread_id = _project_thread(tmp_path)
    dispatcher = ProjectToolDispatcher(storage)
    dispatcher.dispatch(thread_id, _call("update_project_state", {
        "kind": "OBJECTIVE", "operation": "SET", "value": "Operationalize GxT"
    }), origin_turn_number=1)
    dispatcher.dispatch(thread_id, _call("update_project_state", {
        "kind": "NEXT_ACTION", "operation": "SET", "value": "Define one setup"
    }, "next-1"), origin_turn_number=2)

    summary = ProjectService(storage).general_summaries()[0]

    assert summary["name"] == "GxT"
    assert summary["summary"]["objective"] == "Operationalize GxT"
    assert summary["summary"]["next_action"] == "Define one setup"
    assert set(summary["summary"]) == {
        "objective", "experiment", "progress", "next_action", "unresolved_question"
    }


def test_archived_project_rejects_new_state_and_saved_next_action_survives_restart(tmp_path):
    database = tmp_path / "mentor.sqlite3"
    storage, project, thread_id = _project_thread(tmp_path)
    dispatcher = ProjectToolDispatcher(storage)
    dispatcher.dispatch(thread_id, _call("update_project_state", {
        "kind": "NEXT_ACTION", "operation": "SET", "value": "Collect ten examples"
    }), origin_turn_number=1)

    assert ProjectService(Storage(database)).roadmap(project.id)["next_action"] == "Collect ten examples"
    ProjectService(storage).update_project(project.id, status="ARCHIVED")
    with pytest.raises(ValueError, match="archived"):
        dispatcher.dispatch(thread_id, _call("update_project_state", {
            "kind": "NEXT_ACTION", "operation": "SET", "value": "Trade it live"
        }, "next-2"), origin_turn_number=2)


def test_deleting_origin_thread_preserves_project_owned_state_without_dangling_origin(tmp_path):
    storage, project, thread_id = _project_thread(tmp_path)
    ProjectToolDispatcher(storage).dispatch(thread_id, _call("update_project_state", {
        "kind": "NEXT_ACTION", "operation": "SET", "value": "Collect ten examples"
    }), origin_turn_number=1)

    assert storage.delete_thread(thread_id) is True
    assert storage.project_roadmap(project.id)["next_action"] == "Collect ten examples"
    with storage._connect() as connection:
        assert connection.execute(
            "SELECT origin_thread_id, origin_turn_number FROM project_state_events"
        ).fetchone() == (None, None)


def test_project_model_context_is_bounded_without_truncating_the_read_only_roadmap(tmp_path):
    storage, project, thread_id = _project_thread(tmp_path)
    dispatcher = ProjectToolDispatcher(storage)
    for index in range(7):
        dispatcher.dispatch(thread_id, _call("update_project_state", {
            "kind": "BLOCKER", "operation": "ADD", "value": f"Blocker {index} " + "x" * 400
        }, f"blocker-{index}"), origin_turn_number=index + 1)

    context = ProjectService(storage).project_context(project.id)

    assert len(context["blockers"]) == 4
    assert context["blockers_truncated"] is True
    assert all(len(item) == 250 for item in context["blockers"])
    assert len(ProjectService(storage).roadmap(project.id)["blockers"]) == 7
