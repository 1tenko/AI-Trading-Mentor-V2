import json
import sqlite3

import pytest

from mentor.analysis import build_analysis_frame, summarize_results
from mentor.datasets import MappingEntry, create_inspected_mapping_draft, import_local_dataset, inspect_local_dataset
from mentor.project_ledger import ProjectLedgerService
from mentor.project_models import ProjectStatus, ThreadSourceBehavior
from mentor.storage import Storage


def _project_evidence(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    thread_id = storage.create_thread(
        "Research", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id
    )
    source = tmp_path / "trades.csv"
    source.write_text("Result_R,Outcome\n2,Win\n-1,Loss\n", encoding="utf-8")
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "ledger-data").dataset
    draft = create_inspected_mapping_draft(
        storage, inspect_local_dataset(storage, dataset.id),
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_outcome")],
    )
    mapping = storage.confirm_mapping_version(draft.id)
    result = summarize_results(build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_outcome")
    ))
    evidence = storage.record_analysis_evidence(
        thread_id=thread_id, origin_turn_number=1, display_turn_number=1,
        dataset_id=dataset.id, mapping_version_id=mapping.id,
        operation="summarize_results", schema_version=result["schema_version"],
        arguments={"dataset_id": dataset.id}, result=result,
    )
    return storage, project, thread_id, evidence


def test_empirical_finding_links_only_same_project_validated_analysis_evidence(tmp_path):
    storage, project, thread_id, evidence = _project_evidence(tmp_path)
    record = ProjectLedgerService(storage).record_research(
        project.id, kind="EMPIRICAL_FINDING", status="SUPPORTED",
        summary="The two-trade sample was positive but is too small for adoption.",
        provenance="USER_EMPIRICAL_EVIDENCE", origin_thread_id=thread_id,
        origin_turn_number=2, analysis_evidence_id=evidence.id,
    )

    assert record["kind"] == "EMPIRICAL_FINDING"
    linked = ProjectLedgerService(storage).ledger(project.id)[0]["evidence"][0]
    assert linked["original_evidence_id"] == evidence.id
    assert linked["origin_available"] is True
    assert linked["safe_envelope"]["metrics"]["mean_return"] == 0.5
    assert not ({"rows", "headers", "notes", "qualitative_output"} & linked["safe_envelope"].keys())


def test_cross_project_evidence_and_thread_references_fail_closed(tmp_path):
    storage, project, thread_id, evidence = _project_evidence(tmp_path)
    other = storage.create_project("Other")
    other_thread = storage.create_thread(
        "Other", behavior=ThreadSourceBehavior.PROJECT, project_id=other.id
    )
    ledger = ProjectLedgerService(storage)

    with pytest.raises(ValueError, match="owning project"):
        ledger.record_research(
            other.id, kind="EMPIRICAL_FINDING", status="SUPPORTED", summary="Wrong project",
            provenance="USER_EMPIRICAL_EVIDENCE", origin_thread_id=other_thread,
            origin_turn_number=1, analysis_evidence_id=evidence.id,
        )
    with pytest.raises(ValueError, match="conversation"):
        ledger.record_research(
            other.id, kind="HYPOTHESIS", status="ACTIVE", summary="Wrong origin",
            provenance="AI_RESEARCH_HYPOTHESIS", origin_thread_id=thread_id,
            origin_turn_number=1,
        )


@pytest.mark.parametrize(
    ("kind", "provenance"),
    (("HYPOTHESIS", "USER_EMPIRICAL_EVIDENCE"), ("EMPIRICAL_FINDING", "AI_RECOMMENDATION")),
)
def test_research_kind_provenance_pairs_are_fixed(kind, provenance, tmp_path):
    storage, project, thread_id, evidence = _project_evidence(tmp_path)
    with pytest.raises(ValueError, match="provenance"):
        ProjectLedgerService(storage).record_research(
            project.id, kind=kind, status="ACTIVE", summary="Invalid pair", provenance=provenance,
            origin_thread_id=thread_id, origin_turn_number=2,
            analysis_evidence_id=evidence.id if kind == "EMPIRICAL_FINDING" else None,
        )


def test_research_kind_status_pairs_are_fixed(tmp_path):
    storage, project, thread_id, _evidence = _project_evidence(tmp_path)
    with pytest.raises(ValueError, match="status"):
        ProjectLedgerService(storage).record_research(
            project.id, kind="HYPOTHESIS", status="VALIDATED", summary="Skipped evidence",
            provenance="AI_RESEARCH_HYPOTHESIS", origin_thread_id=thread_id, origin_turn_number=2,
        )


def test_empirical_finding_requires_evidence_and_non_empirical_record_cannot_link_it(tmp_path):
    storage, project, thread_id, evidence = _project_evidence(tmp_path)
    ledger = ProjectLedgerService(storage)
    with pytest.raises(ValueError, match="requires deterministic evidence"):
        ledger.record_research(
            project.id, kind="EMPIRICAL_FINDING", status="SUPPORTED", summary="Missing evidence",
            provenance="USER_EMPIRICAL_EVIDENCE", origin_thread_id=thread_id, origin_turn_number=2,
        )
    with pytest.raises(ValueError, match="cannot link"):
        ledger.record_research(
            project.id, kind="HYPOTHESIS", status="ACTIVE", summary="Test this",
            provenance="AI_RESEARCH_HYPOTHESIS", origin_thread_id=thread_id, origin_turn_number=2,
            analysis_evidence_id=evidence.id,
        )


def test_deleted_origin_removes_thread_evidence_but_retains_safe_project_finding(tmp_path):
    storage, project, thread_id, evidence = _project_evidence(tmp_path)
    ProjectLedgerService(storage).record_research(
        project.id, kind="EMPIRICAL_FINDING", status="SUPPORTED", summary="Small positive sample",
        provenance="USER_EMPIRICAL_EVIDENCE", origin_thread_id=thread_id,
        origin_turn_number=2, analysis_evidence_id=evidence.id,
    )

    assert storage.delete_thread(thread_id) is True
    assert storage.analysis_evidence(thread_id) == []
    retained = ProjectLedgerService(storage).ledger(project.id)[0]
    assert retained["origin_available"] is False
    assert retained["evidence"][0]["origin_available"] is False
    assert retained["evidence"][0]["safe_envelope"]["operation"] == "summarize_results"


def test_stale_or_missing_evidence_is_reported_not_silently_repaired(tmp_path):
    storage, project, thread_id, _evidence = _project_evidence(tmp_path)
    with pytest.raises(ValueError, match="validated AnalysisEvidence"):
        ProjectLedgerService(storage).record_research(
            project.id, kind="EMPIRICAL_FINDING", status="SUPPORTED", summary="Missing",
            provenance="USER_EMPIRICAL_EVIDENCE", origin_thread_id=thread_id,
            origin_turn_number=2, analysis_evidence_id=999,
        )


def test_safe_ledger_serialization_contains_no_private_dataset_strings(tmp_path):
    storage, project, thread_id, evidence = _project_evidence(tmp_path)
    ProjectLedgerService(storage).record_research(
        project.id, kind="EMPIRICAL_FINDING", status="SUPPORTED", summary="Bounded finding",
        provenance="USER_EMPIRICAL_EVIDENCE", origin_thread_id=thread_id,
        origin_turn_number=2, analysis_evidence_id=evidence.id,
    )
    encoded = json.dumps(ProjectLedgerService(storage).ledger(project.id))
    assert "trades.csv" not in encoded
    assert str(tmp_path) not in encoded


def _pending_promotion(tmp_path):
    storage, project, thread_id, evidence = _project_evidence(tmp_path)
    ledger = ProjectLedgerService(storage)
    finding = ledger.record_research(
        project.id, kind="EMPIRICAL_FINDING", status="SUPPORTED", summary="Setup A held in sample.",
        provenance="USER_EMPIRICAL_EVIDENCE", origin_thread_id=thread_id,
        origin_turn_number=1, analysis_evidence_id=evidence.id,
    )
    rule = ledger.record_research(
        project.id, kind="PROVISIONAL_RULE", status="VALIDATED",
        summary="Trade setup A only in the tested session.", provenance="AI_RECOMMENDATION",
        origin_thread_id=thread_id, origin_turn_number=2, supersedes_record_id=finding["id"],
    )
    promotion = ledger.create_promotion_request(
        project.id, rule["id"], "Trade setup A only in the tested session.",
        proposed_thread_id=thread_id, proposed_turn_number=2, shown_turn_number=2,
    )
    return storage, project, thread_id, ledger, promotion


def test_proposal_does_not_adopt_until_explicit_approval_and_duplicate_approval_is_idempotent(tmp_path):
    storage, project, thread_id, ledger, promotion = _pending_promotion(tmp_path)

    assert ledger.playbook(project.id)["version"] == 0
    first = ledger.approve_promotion(
        project.id, promotion["id"], expected_status="PENDING", idempotency_key="approve-1",
        decision_thread_id=thread_id, decision_turn_number=3,
    )
    second = ledger.approve_promotion(
        project.id, promotion["id"], expected_status="PENDING", idempotency_key="approve-1",
        decision_thread_id=thread_id, decision_turn_number=3,
    )

    assert first == second
    assert first["playbook_version"] == 1
    playbook = ledger.playbook(project.id)
    assert playbook["rules"][0]["rule"] == "Trade setup A only in the tested session."
    lineage = playbook["rules"][0]["lineage"]
    assert lineage["promotion_id"] == promotion["id"]
    assert len(lineage["research_record_ids"]) == 2
    assert len(lineage["analysis_evidence_ids"]) == 1
    assert storage.project_research_record(project.id, lineage["user_decision_record_id"])["kind"] == "USER_DECISION"


def test_rejecting_promotion_changes_no_playbook_and_cannot_then_approve(tmp_path):
    storage, project, thread_id, ledger, promotion = _pending_promotion(tmp_path)

    rejected = ledger.reject_promotion(
        project.id, promotion["id"], expected_status="PENDING",
        decision_thread_id=thread_id, decision_turn_number=3,
    )

    assert rejected["status"] == "REJECTED"
    assert rejected["decision_thread_id"] == thread_id
    decision = ledger.ledger(project.id)[0]
    assert decision["kind"] == "USER_DECISION"
    assert decision["provenance"] == "USER_DECISION"
    assert decision["supersedes_record_id"] == promotion["provisional_rule_id"]
    assert ledger.playbook(project.id)["version"] == 0
    with pytest.raises(ValueError, match="not pending"):
        ledger.approve_promotion(
            project.id, promotion["id"], expected_status="PENDING", idempotency_key="late",
            decision_thread_id=promotion["proposed_thread_id"], decision_turn_number=3,
        )


def test_archived_project_cannot_create_or_decide_a_promotion(tmp_path):
    storage, project, thread_id, ledger, promotion = _pending_promotion(tmp_path)
    storage.update_project_status(project.id, ProjectStatus.ARCHIVED)

    with pytest.raises(ValueError, match="archived"):
        ledger.approve_promotion(
            project.id, promotion["id"], expected_status="PENDING", idempotency_key="archived",
            decision_thread_id=thread_id, decision_turn_number=3,
        )
    with pytest.raises(ValueError, match="archived"):
        ledger.reject_promotion(
            project.id, promotion["id"], expected_status="PENDING",
            decision_thread_id=thread_id, decision_turn_number=3,
        )


def test_adopted_playbook_rows_are_immutable(tmp_path):
    storage, project, thread_id, ledger, promotion = _pending_promotion(tmp_path)
    ledger.approve_promotion(
        project.id, promotion["id"], expected_status="PENDING", idempotency_key="immutable",
        decision_thread_id=thread_id, decision_turn_number=3,
    )

    with storage._connect() as connection, pytest.raises(sqlite3.IntegrityError, match="playbook rules are immutable"):
        connection.execute("UPDATE project_playbook_rules SET rule_text = 'rewritten'")
    with storage._connect() as connection, pytest.raises(sqlite3.IntegrityError, match="playbook versions are immutable"):
        connection.execute("DELETE FROM project_playbook_versions")


def test_deleting_proposal_thread_cancels_pending_request_without_changing_playbook(tmp_path):
    storage, project, thread_id, ledger, promotion = _pending_promotion(tmp_path)

    assert storage.delete_thread(thread_id) is True

    assert storage.project_promotion_request(project.id, promotion["id"])["status"] == "CANCELLED"
    assert ledger.playbook(project.id)["version"] == 0
    assert ledger.pending_promotions(project.id) == []


def _restore_old_playbook_schema(storage):
    with storage._connect() as connection:
        connection.execute("DROP TABLE project_playbook_rules")
        connection.execute("DROP TABLE project_playbook_versions")
        connection.execute(
            "CREATE TABLE project_playbook_versions (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL "
            "REFERENCES strategy_projects(id), version INTEGER NOT NULL, approval_thread_id INTEGER NOT NULL "
            "REFERENCES threads(id), approval_turn_number INTEGER NOT NULL, UNIQUE(project_id, version))"
        )
        connection.execute(
            "CREATE TABLE project_playbook_rules (id INTEGER PRIMARY KEY, playbook_version_id INTEGER NOT NULL "
            "REFERENCES project_playbook_versions(id), promotion_request_id INTEGER NOT NULL UNIQUE "
            "REFERENCES project_promotion_requests(id), rule_text TEXT NOT NULL, lineage_json TEXT NOT NULL)"
        )


def test_empty_old_playbook_schema_upgrades_without_rewriting_other_project_data(tmp_path):
    path = tmp_path / "mentor.sqlite3"
    storage = Storage(path)
    storage.initialize()
    project = storage.create_project("GxT")
    _restore_old_playbook_schema(storage)

    Storage(path).initialize()

    assert Storage(path).project(project.id).name == "GxT"
    with storage._connect() as connection:
        approval = next(row for row in connection.execute(
            "PRAGMA table_info(project_playbook_versions)"
        ) if row[1] == "approval_thread_id")
    assert approval[3] == 0


def test_old_playbook_schema_with_data_stops_for_review_instead_of_rewriting(tmp_path):
    path = tmp_path / "mentor.sqlite3"
    storage = Storage(path)
    storage.initialize()
    project = storage.create_project("GxT")
    thread_id = storage.create_thread(
        "Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id
    )
    _restore_old_playbook_schema(storage)
    with storage._connect() as connection:
        connection.execute(
            "INSERT INTO project_playbook_versions(project_id, version, approval_thread_id, approval_turn_number) "
            "VALUES (?, 1, ?, 1)", (project.id, thread_id),
        )

    with pytest.raises(RuntimeError, match="requires review"):
        Storage(path).initialize()


def test_only_validated_provisional_rule_can_create_a_promotion(tmp_path):
    storage, project, thread_id, _evidence = _project_evidence(tmp_path)
    ledger = ProjectLedgerService(storage)
    hypothesis = ledger.record_research(
        project.id, kind="HYPOTHESIS", status="ACTIVE", summary="Maybe X",
        provenance="AI_RESEARCH_HYPOTHESIS", origin_thread_id=thread_id, origin_turn_number=2,
    )

    with pytest.raises(ValueError, match="validated provisional rule"):
        ledger.create_promotion_request(
            project.id, hypothesis["id"], "Adopt X", proposed_thread_id=thread_id,
            proposed_turn_number=2, shown_turn_number=2,
        )


def test_new_approved_rule_creates_a_new_version_without_rewriting_history(tmp_path):
    storage, project, thread_id, ledger, first_promotion = _pending_promotion(tmp_path)
    ledger.approve_promotion(
        project.id, first_promotion["id"], expected_status="PENDING", idempotency_key="first",
        decision_thread_id=thread_id, decision_turn_number=3,
    )
    old_rule = storage.project_research_record(project.id, first_promotion["provisional_rule_id"])
    revised = ledger.record_research(
        project.id, kind="PROVISIONAL_RULE", status="VALIDATED", summary="Trade setup A only before noon.",
        provenance="AI_RECOMMENDATION", origin_thread_id=thread_id, origin_turn_number=4,
        supersedes_record_id=old_rule["id"],
    )
    second_promotion = ledger.create_promotion_request(
        project.id, revised["id"], revised["summary"], proposed_thread_id=thread_id,
        proposed_turn_number=4, shown_turn_number=4,
    )

    ledger.approve_promotion(
        project.id, second_promotion["id"], expected_status="PENDING", idempotency_key="second",
        decision_thread_id=thread_id, decision_turn_number=5,
    )

    playbook = ledger.playbook(project.id)
    assert playbook["version"] == 2
    assert [rule["version"] for rule in playbook["rules"]] == [1, 2]
    assert playbook["rules"][0]["rule"] == "Trade setup A only in the tested session."


def test_deleting_approval_thread_keeps_project_owned_playbook_and_removes_thread(tmp_path):
    storage, project, thread_id, ledger, promotion = _pending_promotion(tmp_path)
    ledger.approve_promotion(
        project.id, promotion["id"], expected_status="PENDING", idempotency_key="delete-origin",
        decision_thread_id=thread_id, decision_turn_number=3,
    )

    assert storage.delete_thread(thread_id) is True
    assert storage.thread_context(thread_id) is None
    playbook = ledger.playbook(project.id)
    assert playbook["version"] == 1
    assert playbook["rules"][0]["approval"] == {
        "origin_available": False, "thread_id": None, "turn_number": 3,
    }
    with storage._connect() as connection:
        assert connection.execute(
            "SELECT approval_thread_id FROM project_playbook_versions"
        ).fetchone() == (None,)
