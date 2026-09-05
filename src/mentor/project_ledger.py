"""Typed, project-local research records with safe empirical evidence links."""

from mentor.project_models import ProjectStatus
from mentor.storage import Storage


_PROVENANCE_BY_KIND = {
    "OBSERVATION": {"AI_INTERPRETATION", "USER_EMPIRICAL_EVIDENCE", "DIRECT_SOURCE_TEACHING", "SOURCE_SYNTHESIS", "USER_STATED"},
    "HYPOTHESIS": {"AI_RESEARCH_HYPOTHESIS"},
    "OPERATIONAL_DEFINITION": {"AI_RECOMMENDATION", "USER_DECISION"},
    "EXPERIMENT": {"AI_RECOMMENDATION", "USER_DECISION"},
    "EMPIRICAL_FINDING": {"USER_EMPIRICAL_EVIDENCE"},
    "PROJECT_FINDING": {"AI_INTERPRETATION", "SOURCE_SYNTHESIS"},
    "LIMITATION": {"AI_INTERPRETATION", "USER_EMPIRICAL_EVIDENCE", "SOURCE_SYNTHESIS"},
    "PROVISIONAL_RULE": {"AI_RECOMMENDATION"},
    "USER_DECISION": {"USER_DECISION"},
}
_STATUSES_BY_KIND = {
    "OBSERVATION": {"DRAFT", "ACTIVE", "SUPERSEDED"},
    "HYPOTHESIS": {"DRAFT", "ACTIVE", "SUPPORTED", "REJECTED", "INCONCLUSIVE", "SUPERSEDED"},
    "OPERATIONAL_DEFINITION": {"DRAFT", "ACTIVE", "VALIDATED", "SUPERSEDED"},
    "EXPERIMENT": {"DRAFT", "ACTIVE", "COMPLETED", "SUPERSEDED"},
    "EMPIRICAL_FINDING": {"SUPPORTED", "REJECTED", "INCONCLUSIVE", "SUPERSEDED"},
    "PROJECT_FINDING": {"DRAFT", "ACTIVE", "VALIDATED", "SUPERSEDED"},
    "LIMITATION": {"ACTIVE", "SUPERSEDED"},
    "PROVISIONAL_RULE": {"DRAFT", "ACTIVE", "VALIDATED", "SUPERSEDED"},
    "USER_DECISION": {"ACTIVE", "SUPERSEDED"},
}


class ProjectLedgerService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def record_research(
        self,
        project_id: int,
        *,
        kind: str,
        status: str,
        summary: str,
        provenance: str,
        origin_thread_id: int,
        origin_turn_number: int,
        supersedes_record_id: int | None = None,
        analysis_evidence_id: int | None = None,
    ) -> dict[str, object]:
        project = self.storage.project(project_id)
        if project is None:
            raise LookupError("project not found")
        if project.status is ProjectStatus.ARCHIVED:
            raise ValueError("archived projects cannot change research")
        if kind not in _PROVENANCE_BY_KIND:
            raise ValueError("research kind is invalid")
        if provenance not in _PROVENANCE_BY_KIND[kind]:
            raise ValueError("research provenance is invalid for its kind")
        if status not in _STATUSES_BY_KIND[kind]:
            raise ValueError("research status is invalid")
        summary = _text(summary, 2_000, "research summary")
        for value, label in (
            (analysis_evidence_id, "analysis evidence"), (supersedes_record_id, "superseded record")
        ):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{label} identifier is invalid")
        if kind == "EMPIRICAL_FINDING" and analysis_evidence_id is None:
            raise ValueError("empirical finding requires deterministic evidence")
        if kind != "EMPIRICAL_FINDING" and analysis_evidence_id is not None:
            raise ValueError("this research kind cannot link empirical evidence")
        record_id = self.storage.create_project_research_record(
            project_id=project_id, kind=kind, status=status, summary=summary,
            provenance=provenance, origin_thread_id=origin_thread_id,
            origin_turn_number=origin_turn_number, supersedes_record_id=supersedes_record_id,
            analysis_evidence_id=analysis_evidence_id,
        )
        return self.storage.project_research_record(project_id, record_id)

    def link_analysis_evidence(self, *args, **kwargs):
        raise ValueError("analysis evidence must be linked atomically when recording the finding")

    def ledger(self, project_id: int) -> list[dict[str, object]]:
        return self.storage.project_research_records(project_id)


def _text(value: object, limit: int, label: str) -> str:
    if not isinstance(value, str) or not (value := " ".join(value.split())) or len(value) > limit:
        raise ValueError(f"{label} is invalid")
    return value
