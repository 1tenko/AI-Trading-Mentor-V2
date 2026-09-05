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

    def create_promotion_request(
        self,
        project_id: int,
        provisional_rule_id: int,
        proposed_rule: str,
        *,
        proposed_thread_id: int,
        proposed_turn_number: int,
        shown_turn_number: int,
    ) -> dict[str, object]:
        self._require_active_project(project_id)
        proposed_rule = _text(proposed_rule, 2_000, "proposed rule")
        if any(type(value) is not int or value < 1 for value in (
            provisional_rule_id, proposed_thread_id, proposed_turn_number, shown_turn_number,
        )):
            raise ValueError("promotion identifiers are invalid")
        return self.storage.create_project_promotion_request(
            project_id=project_id, provisional_rule_id=provisional_rule_id,
            proposed_rule=proposed_rule, proposed_thread_id=proposed_thread_id,
            proposed_turn_number=proposed_turn_number, shown_turn_number=shown_turn_number,
        )

    def approve_promotion(
        self,
        project_id: int,
        promotion_id: int,
        *,
        expected_status: str,
        idempotency_key: str,
        decision_thread_id: int,
        decision_turn_number: int,
    ) -> dict[str, object]:
        if expected_status != "PENDING":
            raise ValueError("expected promotion status must be PENDING")
        _text(idempotency_key, 128, "idempotency key")
        if any(type(value) is not int or value < 1 for value in (promotion_id, decision_thread_id, decision_turn_number)):
            raise ValueError("promotion approval identifiers are invalid")
        promotion = self.storage.project_promotion_request(project_id, promotion_id)
        if promotion is None:
            raise LookupError("promotion request not found")
        if promotion["status"] != "APPROVED":
            self._require_active_project(project_id)
        return self.storage.approve_project_promotion(
            project_id=project_id, promotion_id=promotion_id,
            decision_thread_id=decision_thread_id, decision_turn_number=decision_turn_number,
        )

    def reject_promotion(
        self,
        project_id: int,
        promotion_id: int,
        *,
        expected_status: str,
        decision_thread_id: int,
        decision_turn_number: int,
    ) -> dict[str, object]:
        self._require_active_project(project_id)
        if expected_status != "PENDING":
            raise ValueError("expected promotion status must be PENDING")
        if any(type(value) is not int or value < 1 for value in (
            promotion_id, decision_thread_id, decision_turn_number,
        )):
            raise ValueError("promotion rejection identifiers are invalid")
        return self.storage.reject_project_promotion(
            project_id, promotion_id,
            decision_thread_id=decision_thread_id, decision_turn_number=decision_turn_number,
        )

    def playbook(self, project_id: int) -> dict[str, object]:
        return self.storage.project_playbook(project_id)

    def pending_promotions(self, project_id: int) -> list[dict[str, object]]:
        return self.storage.pending_project_promotions(project_id)

    def _require_active_project(self, project_id: int) -> None:
        project = self.storage.project(project_id)
        if project is None:
            raise LookupError("project not found")
        if project.status is ProjectStatus.ARCHIVED:
            raise ValueError("archived projects cannot change playbook state")


def _text(value: object, limit: int, label: str) -> str:
    if not isinstance(value, str) or not (value := " ".join(value.split())) or len(value) > limit:
        raise ValueError(f"{label} is invalid")
    return value
