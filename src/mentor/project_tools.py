"""Strict local tools for the small project coaching whiteboard."""

import json

from mentor.project_ledger import ProjectLedgerService
from mentor.project_models import ThreadSourceBehavior
from mentor.project_service import ProjectService
from mentor.storage import Storage


MASTERY_STATUSES = frozenset({
    "NOT_STARTED", "LEARNING", "OPERATIONALIZING", "TESTING", "PROVISIONAL", "VALIDATED"
})
PROJECT_TOOL_NAMES = frozenset({"update_project_state", "update_project_mastery", "record_project_research"})
PROJECT_TOOLS = [
    {
        "type": "function", "name": "update_project_state", "strict": True,
        "description": "Update one project coaching field after it is established in the conversation.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "enum": ["OBJECTIVE", "EXPERIMENT", "BLOCKER", "NEXT_ACTION"]},
                "operation": {"type": "string", "enum": ["SET", "CLEAR", "ADD", "REMOVE"]},
                "value": {"type": ["string", "null"]},
            },
            "required": ["kind", "operation", "value"],
        },
    },
    {
        "type": "function", "name": "update_project_mastery", "strict": True,
        "description": "Record a controlled learning status with a concise reason.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "concept": {"type": "string"},
                "status": {"type": "string", "enum": sorted(MASTERY_STATUSES)},
                "reason": {"type": "string"},
                "evidence_reference": {"type": ["string", "null"]},
            },
            "required": ["concept", "status", "reason", "evidence_reference"],
        },
    },
    {
        "type": "function", "name": "record_project_research", "strict": True,
        "description": "Record one concise project research item with explicit provenance.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "enum": [
                    "OBSERVATION", "HYPOTHESIS", "OPERATIONAL_DEFINITION", "EXPERIMENT",
                    "EMPIRICAL_FINDING", "PROJECT_FINDING", "LIMITATION", "PROVISIONAL_RULE", "USER_DECISION",
                ]},
                "status": {"type": "string", "enum": [
                    "DRAFT", "ACTIVE", "COMPLETED", "SUPPORTED", "REJECTED", "INCONCLUSIVE", "VALIDATED", "SUPERSEDED",
                ]},
                "summary": {"type": "string"},
                "provenance": {"type": "string"},
                "analysis_evidence_id": {"type": ["integer", "null"]},
                "supersedes_record_id": {"type": ["integer", "null"]},
            },
            "required": [
                "kind", "status", "summary", "provenance", "analysis_evidence_id", "supersedes_record_id",
            ],
        },
    },
]


class ProjectToolDispatcher:
    def __init__(self, storage: Storage):
        self.storage = storage

    def dispatch(self, thread_id: int, call: dict, *, origin_turn_number: int) -> dict[str, object]:
        thread = self.storage.thread_context(thread_id)
        if thread is None or thread.thread_source_behavior is not ThreadSourceBehavior.PROJECT or thread.project_id is None:
            raise ValueError("project tool requires a project conversation")
        call_id, name = call.get("call_id"), call.get("name")
        if not isinstance(call_id, str) or not call_id or name not in PROJECT_TOOL_NAMES:
            raise ValueError("unsupported project tool")
        try:
            arguments = json.loads(call.get("arguments", ""))
        except (TypeError, json.JSONDecodeError):
            raise ValueError("project tool arguments are invalid") from None
        if not isinstance(arguments, dict):
            raise ValueError("project tool arguments are invalid")
        if name == "record_project_research":
            if set(arguments) != {
                "kind", "status", "summary", "provenance", "analysis_evidence_id", "supersedes_record_id"
            }:
                raise ValueError("project research arguments are invalid")
            record = ProjectLedgerService(self.storage).record_research(
                thread.project_id,
                origin_thread_id=thread_id,
                origin_turn_number=origin_turn_number,
                **arguments,
            )
            return {
                "status": "recorded", "record_id": record["id"],
                "kind": record["kind"], "provenance": record["provenance"],
            }
        if name == "update_project_state":
            payload = _state_payload(arguments)
            kind = str(payload.pop("kind"))
        else:
            payload = _mastery_payload(arguments)
            kind = "MASTERY"
        return ProjectService(self.storage).apply_state_event(
            thread.project_id,
            event_key=call_id,
            kind=kind,
            payload=payload,
            origin_thread_id=thread_id,
            origin_turn_number=origin_turn_number,
        )


def _state_payload(arguments: dict) -> dict[str, object]:
    if set(arguments) != {"kind", "operation", "value"}:
        raise ValueError("project state arguments are invalid")
    kind, operation, value = arguments["kind"], arguments["operation"], arguments["value"]
    if kind not in {"OBJECTIVE", "EXPERIMENT", "BLOCKER", "NEXT_ACTION"}:
        raise ValueError("project state kind is invalid")
    allowed = {"BLOCKER": {"ADD", "REMOVE", "CLEAR"}}.get(kind, {"SET", "CLEAR"})
    if operation not in allowed:
        raise ValueError("project state operation is invalid")
    if operation == "CLEAR":
        if value is not None:
            raise ValueError("clear operations require a null value")
    elif not isinstance(value, str) or not (value := " ".join(value.split())) or len(value) > 2_000:
        raise ValueError("project state value is invalid")
    return {"kind": kind, "operation": operation, "value": value}


def _mastery_payload(arguments: dict) -> dict[str, object]:
    if set(arguments) != {"concept", "status", "reason", "evidence_reference"}:
        raise ValueError("project mastery arguments are invalid")
    concept = _text(arguments["concept"], 200, "project mastery concept")
    reason = _text(arguments["reason"], 1_000, "project mastery reason")
    status = arguments["status"]
    if status not in MASTERY_STATUSES:
        raise ValueError("project mastery status is invalid")
    reference = arguments["evidence_reference"]
    if reference is not None:
        reference = _text(reference, 500, "project mastery evidence reference")
    return {"concept": concept, "status": status, "reason": reason, "evidence_reference": reference}


def _text(value: object, limit: int, label: str) -> str:
    if not isinstance(value, str) or not (value := " ".join(value.split())) or len(value) > limit:
        raise ValueError(f"{label} is invalid")
    return value
