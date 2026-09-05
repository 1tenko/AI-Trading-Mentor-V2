import json

import pytest

from mentor.analysis import build_analysis_frame, summarize_results
from mentor.datasets import MappingEntry, create_inspected_mapping_draft, import_local_dataset, inspect_local_dataset
from mentor.project_ledger import ProjectLedgerService
from mentor.project_models import ThreadSourceBehavior
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
