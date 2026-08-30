import json
import math
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

import mentor.analysis as analysis_module
from mentor.analysis import (
    AnalysisLimitError,
    AnalysisNumericError,
    AnalysisFilter,
    analyze_mfe_mae,
    analyze_over_time,
    build_analysis_frame,
    compare_groups,
    group_results,
    read_text_evidence,
    summarize_results,
    TextEvidenceUseGuard,
)
from mentor.datasets import MappingEntry, create_inspected_mapping_draft, import_local_dataset, inspect_local_dataset
from mentor.storage import ANALYSIS_EXCLUSION_LIMIT, Storage, validate_completed_evidence_envelope


def _confirmed_dataset(tmp_path: Path, contents: str, entries: list[MappingEntry], *, dataset_id: str = "dataset-analysis"):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text(contents, encoding="utf-8")
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: dataset_id).dataset
    draft = create_inspected_mapping_draft(storage, inspect_local_dataset(storage, dataset.id), entries)
    confirmed = storage.confirm_mapping_version(draft.id)
    return storage, dataset, confirmed


def test_text_evidence_requires_explicit_mapping_permission_and_consent(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Trade Date,Journal,Session\n"
        "bad,2026-01-03,synthetic third,London\n"
        "1,2026-01-01,synthetic first,London\n"
        "2,2026-01-02,synthetic second,New York\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="trade_timestamp", mentor_access="allow_row_values_when_analysing_notes"),
            MappingEntry(2, analysis_label="Journal", mentor_access="allow_row_values_when_analysing_notes"),
            MappingEntry(3, analysis_label="Session", mentor_access="allow_row_values_when_analysing_notes"),
        ],
    )
    entries = storage.mapping_entries(mapping.id)
    ids = {entry.analysis_label or entry.semantic_role: entry.field_id for entry in entries}

    with pytest.raises(ValueError, match="consent"):
        read_text_evidence(
            storage,
            dataset.id,
            mapping.id,
            text_field_ids=(ids["Journal"] or "",),
            context_field_ids=(ids["Session"] or "",),
            filters=(AnalysisFilter(ids["Session"] or "", "eq", "London"),),
            order_by="timestamp",
            include_approved_notes=False,
            use_guard=TextEvidenceUseGuard(),
        )

    evidence = read_text_evidence(
        storage,
        dataset.id,
        mapping.id,
        text_field_ids=(ids["Journal"] or "",),
        context_field_ids=(ids["Session"] or "",),
        filters=(AnalysisFilter(ids["Session"] or "", "eq", "London"),),
        order_by="timestamp",
        include_approved_notes=True,
        use_guard=TextEvidenceUseGuard(),
    )

    assert evidence["provenance"] == "USER_SUPPLIED_QUALITATIVE_DATA"
    assert evidence["matching_rows"] == 2
    assert evidence["usable_text_rows"] == 2
    assert evidence["returned_rows"] == 2
    assert [item["text"][0]["value"] for item in evidence["items"]] == ["synthetic first", "synthetic third"]
    assert "Result" not in json.dumps(evidence)


def test_text_evidence_rejects_unapproved_fields_wrong_scope_and_raw_inputs(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Journal,Session\nsynthetic note,London\n",
        [
            MappingEntry(0, analysis_label="Journal", mentor_access="allow_row_values_when_analysing_notes"),
            MappingEntry(1, analysis_label="Session"),
        ],
    )
    journal_id, session_id = (entry.field_id or "" for entry in storage.mapping_entries(mapping.id))

    with pytest.raises(ValueError, match="not approved"):
        read_text_evidence(
            storage, dataset.id, mapping.id, text_field_ids=(journal_id,), context_field_ids=(session_id,),
            include_approved_notes=True, use_guard=TextEvidenceUseGuard()
        )
    with pytest.raises(ValueError, match="field is not approved"):
        read_text_evidence(
            storage, dataset.id, mapping.id, text_field_ids=(session_id,), include_approved_notes=True, use_guard=TextEvidenceUseGuard()
        )
    with pytest.raises(ValueError, match="confirmed mapping"):
        read_text_evidence(
            storage, "wrong-dataset", mapping.id, text_field_ids=(journal_id,), include_approved_notes=True, use_guard=TextEvidenceUseGuard()
        )
    with pytest.raises(ValueError, match="confirmed mapping"):
        read_text_evidence(
            storage, dataset.id, mapping.id + 100, text_field_ids=(journal_id,), include_approved_notes=True, use_guard=TextEvidenceUseGuard()
        )
    for raw_input in ({"raw_header": "Journal"}, {"path": "C:/private.csv"}, {"dataframe": object()}, {"sql": "SELECT *"}, {"python": "open()"}):
        with pytest.raises(TypeError):
            read_text_evidence(  # type: ignore[call-arg]
                storage, dataset.id, mapping.id, text_field_ids=(journal_id,), include_approved_notes=True,
                use_guard=TextEvidenceUseGuard(), **raw_input
            )


@pytest.mark.parametrize(("count", "expected_complete"), [(50, True), (100, True), (200, False)])
def test_text_evidence_short_note_row_bound_and_deterministic_completeness(tmp_path, count, expected_complete):
    rows = "\n".join(f"2026-01-{(index % 28) + 1:02d},synthetic note {index:03d}" for index in range(count))
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        f"Trade Date,Journal\n{rows}\n",
        [
            MappingEntry(0, semantic_role="trade_timestamp", mentor_access="allow_row_values_when_analysing_notes"),
            MappingEntry(1, analysis_label="Journal", mentor_access="allow_row_values_when_analysing_notes"),
        ],
    )
    journal_id = storage.mapping_entries(mapping.id)[1].field_id or ""

    evidence = read_text_evidence(
        storage, dataset.id, mapping.id, text_field_ids=(journal_id,), order_by="timestamp",
        include_approved_notes=True, use_guard=TextEvidenceUseGuard(),
    )

    assert evidence["matching_rows"] == count
    assert evidence["usable_text_rows"] == count
    assert evidence["returned_rows"] == min(count, 100)
    assert evidence["omitted_rows"] == max(count - 100, 0)
    assert evidence["complete"] is expected_complete
    assert evidence["row_truncated"] is (count > 100)
    assert [item["text"][0]["value"] for item in evidence["items"]][:2] == ["synthetic note 000", "synthetic note 028"]
    assert "source_row_ordinal" not in json.dumps(evidence)


def test_text_evidence_bounds_cells_total_characters_and_unavailable_context_without_losing_text(tmp_path):
    rows = "\n".join(f"{'x' * 1_300},{'London' if index == 0 else ''}" for index in range(21))
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        f"Journal,Session\n{rows}\n",
        [
            MappingEntry(0, analysis_label="Journal", mentor_access="allow_row_values_when_analysing_notes"),
            MappingEntry(1, analysis_label="Session", mentor_access="allow_row_values_when_analysing_notes"),
        ],
    )
    journal_id, session_id = (entry.field_id or "" for entry in storage.mapping_entries(mapping.id))

    evidence = read_text_evidence(
        storage, dataset.id, mapping.id, text_field_ids=(journal_id,), context_field_ids=(session_id,),
        include_approved_notes=True, use_guard=TextEvidenceUseGuard(),
    )

    assert evidence["usable_text_rows"] == 21
    assert evidence["returned_rows"] < 21
    assert evidence["omitted_rows"] == 21 - evidence["returned_rows"]
    assert evidence["characters_returned"] < 24_000
    assert len(json.dumps(evidence, separators=(",", ":"))) <= 24_000
    assert evidence["cell_truncated"] is True
    assert evidence["row_truncated"] is True
    assert evidence["complete"] is False
    assert evidence["items"][0]["context"][0]["value"] == "London"
    assert evidence["items"][1]["unavailable_context_field_ids"] == [session_id]


def test_text_evidence_revocation_requires_a_new_confirmed_mapping_and_consumes_each_guard_once(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Journal\nsynthetic note\n", encoding="utf-8")
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "text-revocation").dataset
    inspection = inspect_local_dataset(storage, dataset.id)
    allowed = storage.confirm_mapping_version(create_inspected_mapping_draft(
        storage, inspection, [MappingEntry(0, analysis_label="Journal", mentor_access="allow_row_values_when_analysing_notes")]
    ).id)
    revoked = storage.confirm_mapping_version(create_inspected_mapping_draft(
        storage, inspection, [MappingEntry(0, analysis_label="Journal")]
    ).id)
    journal_id = storage.mapping_entries(allowed.id)[0].field_id or ""
    guard = TextEvidenceUseGuard()

    assert read_text_evidence(
        storage, dataset.id, allowed.id, text_field_ids=(journal_id,), include_approved_notes=True, use_guard=guard
    )["returned_rows"] == 1
    with pytest.raises(ValueError, match="one call per turn"):
        read_text_evidence(
            storage, dataset.id, allowed.id, text_field_ids=(journal_id,), include_approved_notes=True, use_guard=guard
        )
    with pytest.raises(ValueError, match="not approved"):
        read_text_evidence(
            storage, dataset.id, revoked.id, text_field_ids=(journal_id,), include_approved_notes=True, use_guard=TextEvidenceUseGuard()
        )


def test_text_evidence_rejects_mixed_timestamp_timezones_before_ordering(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Trade Date,Journal\n2026-01-01,synthetic naive\n2026-01-02T00:00:00Z,synthetic aware\n",
        [
            MappingEntry(0, semantic_role="trade_timestamp", mentor_access="allow_row_values_when_analysing_notes"),
            MappingEntry(1, analysis_label="Journal", mentor_access="allow_row_values_when_analysing_notes"),
        ],
    )
    journal_id = storage.mapping_entries(mapping.id)[1].field_id or ""

    with pytest.raises(ValueError, match="compatible timezone"):
        read_text_evidence(
            storage, dataset.id, mapping.id, text_field_ids=(journal_id,), order_by="timestamp",
            include_approved_notes=True, use_guard=TextEvidenceUseGuard(),
        )


def test_text_evidence_caps_the_entire_envelope_when_a_canonical_filter_value_is_long(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Journal,Session\nsynthetic note,London\n",
        [
            MappingEntry(0, analysis_label="Journal", mentor_access="allow_row_values_when_analysing_notes"),
            MappingEntry(1, analysis_label="Session", mentor_access="allow_row_values_when_analysing_notes"),
        ],
    )
    journal_id, session_id = (entry.field_id or "" for entry in storage.mapping_entries(mapping.id))
    long_value = "x" * 30_000

    evidence = read_text_evidence(
        storage, dataset.id, mapping.id, text_field_ids=(journal_id,),
        filters=(AnalysisFilter(session_id, "eq", long_value),),
        include_approved_notes=True, use_guard=TextEvidenceUseGuard(),
    )

    encoded = json.dumps(evidence, separators=(",", ":"))
    assert evidence["matching_rows"] == 0
    assert len(encoded) <= 24_000
    assert long_value not in encoded


def test_analysis_frame_types_valid_rows_and_exclusions_without_exposing_raw_data(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome,Session,Desk Secret\n1.5,Win,London,do-not-disclose\nbad,loss,London,do-not-disclose\n,BE,New York,do-not-disclose\n2,unknown,New York,do-not-disclose\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="percentage"),
            MappingEntry(1, semantic_role="trade_outcome"),
            MappingEntry(2, analysis_label="Session"),
        ],
    )

    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_outcome"))

    assert frame.return_unit == "percentage"
    assert (frame.source_rows, frame.filtered_rows, frame.valid_rows, frame.excluded_rows) == (4, 4, 1, 3)
    assert {(item.role, item.reason, item.count) for item in frame.exclusions} == {
        ("trade_return", "invalid", 1),
        ("trade_return", "blank", 1),
        ("trade_outcome", "invalid", 1),
    }
    assert frame.data["source_row_ordinal"].tolist() == [0]
    assert frame.data["trade_return"].tolist() == [1.5]
    assert frame.data["trade_outcome"].tolist() == ["win"]
    assert "Desk Secret" not in frame.data.columns
    assert "do-not-disclose" not in repr(frame)


@pytest.mark.parametrize(
    "filter_",
    [
        AnalysisFilter("field_unknown", "eq", "London"),
        AnalysisFilter("session", "gt", "London"),
        AnalysisFilter("trade_return", "eq", "1"),
        AnalysisFilter("session", "contains", "London"),
    ],
)
def test_analysis_filters_reject_unknown_fields_incompatible_values_and_operators(tmp_path, filter_):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session\n1,London\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, analysis_label="Session")],
    )
    entries = storage.mapping_entries(mapping.id)
    by_role = {entry.semantic_role: entry.field_id for entry in entries if entry.semantic_role}
    session_id = next(entry.field_id for entry in entries if entry.analysis_label == "Session")
    resolved = AnalysisFilter(
        {"field_unknown": "field_unknown", "session": session_id, "trade_return": by_role["trade_return"]}[filter_.field_id],
        filter_.operator,
        filter_.value,
    )

    with pytest.raises(ValueError, match="filter"):
        build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=(resolved,))


def test_analysis_frame_reports_no_data_and_missing_required_role(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result\n\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R")],
    )

    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    assert (frame.source_rows, frame.filtered_rows, frame.valid_rows, frame.excluded_rows) == (0, 0, 0, 0)
    assert frame.no_data_reason == "no_source_rows"
    with pytest.raises(ValueError, match="missing required role: trade_outcome"):
        build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_outcome",))


def test_analysis_filters_report_no_matching_rows(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session\n1,London\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, analysis_label="Session")],
    )
    session_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Session")

    frame = build_analysis_frame(
        storage,
        dataset.id,
        mapping.id,
        required_roles=("trade_return",),
        filters=(AnalysisFilter(session_id or "", "eq", "New York"),),
    )

    assert (frame.source_rows, frame.filtered_rows, frame.valid_rows, frame.excluded_rows) == (1, 0, 0, 1)
    assert frame.no_data_reason == "no_matching_rows"


def test_analysis_frame_uses_source_order_unless_validated_timestamp_order_is_requested(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Trade Date\n1,2026-01-03\n2,2026-01-02\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_timestamp")],
    )

    source_order = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))
    time_order = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",), order_by="timestamp")

    assert source_order.data["source_row_ordinal"].tolist() == [0, 1]
    assert source_order.order.mode == "source"
    assert time_order.data["source_row_ordinal"].tolist() == [1, 0]
    assert time_order.order.mode == "timestamp"
    assert time_order.order.timestamp_field_id is not None


def test_summarize_results_calculates_r_metrics_in_source_order_and_returns_a_reproducible_envelope(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome,Desk Secret\n2,win,do-not-disclose\n-1,loss,do-not-disclose\n-2,loss,do-not-disclose\n3,win,do-not-disclose\n0,BE,do-not-disclose\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="trade_outcome"),
        ],
    )
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_outcome")
    )

    result = summarize_results(
        frame,
    )

    assert result["provenance"] == "USER_EMPIRICAL_EVIDENCE"
    assert result["dataset_id"] == dataset.id
    assert result["dataset_sha256"] == dataset.content_sha256
    assert result["mapping_version_id"] == mapping.id
    assert result["operation"] == "summarize_results"
    assert result["schema_version"] == "1.0"
    assert result["filters"] == []
    assert result["counts"] == {"source_rows": 5, "filtered_rows": 5, "valid_rows": 5, "excluded_rows": 0}
    assert result["exclusions"] == []
    assert result["metric_definitions"] == {
        "outcome_rate_denominator": "wins + losses + breakevens",
        "win_rate_interval": "Wilson 95% interval",
        "quantile_method": "linear",
        "return_unit": "R",
        "row_order": "source",
    }
    assert result["metrics"] == pytest.approx({
        "wins": 2,
        "losses": 2,
        "breakevens": 1,
        "win_rate": 0.4,
        "loss_rate": 0.4,
        "wilson_95_lower": 0.11762077423264783,
        "wilson_95_upper": 0.769275718723987,
        "max_consecutive_wins": 1,
        "max_consecutive_losses": 2,
        "total_return": 2.0,
        "mean_return": 0.4,
        "median_return": 0.0,
        "mean_winning_return": 2.5,
        "mean_losing_return": -1.5,
        "best_return": 3.0,
        "worst_return": -2.0,
        "realized_reward_risk": 5 / 3,
        "cumulative_return": 2.0,
        "max_drawdown": 3.0,
        "recovery_observations": 1,
        "minimum": -2.0,
        "maximum": 3.0,
        "sample_standard_deviation": math.sqrt(4.3),
        "first_quartile": -1.0,
        "third_quartile": 2.0,
        "interquartile_range": 3.0,
        "iqr_outlier_count": 0,
        "percentile_05": -1.8,
        "percentile_10": -1.6,
        "percentile_25": -1.0,
        "percentile_50": 0.0,
        "percentile_75": 2.0,
        "percentile_90": 2.6,
        "percentile_95": 2.8,
        "valid_rows": 5,
        "excluded_rows": 0,
    })
    assert result["limitations"] == ["small_sample"]
    assert "Desk Secret" not in json.dumps(result)
    assert "do-not-disclose" not in json.dumps(result)


def test_summarize_results_preserves_non_r_returns_and_marks_missing_capabilities_unavailable(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Private Header\n1.5,private-value\n-0.5,private-value\n",
        [MappingEntry(0, semantic_role="trade_return", unit="percentage")],
    )
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    result = summarize_results(
        frame,
    )

    assert result["metric_definitions"]["return_unit"] == "percentage"
    assert result["metrics"]["total_return"] == 1.0
    assert result["metrics"]["mean_return"] == 0.5
    assert result["metrics"]["wins"] is None
    assert result["metrics"]["win_rate"] is None
    assert result["metrics"]["realized_reward_risk"] is None
    assert result["metrics"]["cumulative_return"] is None
    assert result["metrics"]["max_drawdown"] is None
    assert result["metrics"]["recovery_observations"] is None
    assert result["limitations"] == ["small_sample", "unavailable_metric"]
    assert "Private Header" not in json.dumps(result)
    assert "private-value" not in json.dumps(result)


def test_summarize_results_uses_the_frame_validated_filter_fingerprint_without_disclosing_its_value(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result\n1\n2\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R")],
    )
    field_id = storage.mapping_entries(mapping.id)[0].field_id
    filter_ = AnalysisFilter(field_id or "", "gt", 1)
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=(filter_,)
    )

    result = summarize_results(
        frame,
    )

    assert result["counts"]["filtered_rows"] == 1
    assert result["filters"] == [
        {
            "field_id": field_id,
            "operator": "gt",
            "value_spec": {"mapping_version_id": mapping.id, "value_type": "number", "unit": "R", "values": [1.0]},
            "canonical_id": result["filters"][0]["canonical_id"],
        }
    ]
    assert len(result["filters"][0]["canonical_id"]) == 12


def test_summarize_results_binds_its_provenance_to_the_validated_frame(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result\n1\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R")],
    )
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    result = summarize_results(frame)

    assert result["dataset_id"] == dataset.id
    assert result["dataset_sha256"] == dataset.content_sha256
    assert result["mapping_version_id"] == mapping.id
    assert result["metric_definitions"]["return_unit"] == "R"


def test_cross_dataset_evidence_rejects_a_summary_bound_to_another_frame(tmp_path):
    alpha_storage, alpha_dataset, alpha_mapping = _confirmed_dataset(
        tmp_path / "alpha", "Result\n1\n", [MappingEntry(0, semantic_role="trade_return", unit="R")]
    )
    beta_storage, beta_dataset, beta_mapping = _confirmed_dataset(
        tmp_path / "beta",
        "Result\n2\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R")],
        dataset_id="dataset-beta",
    )
    beta_frame = build_analysis_frame(beta_storage, beta_dataset.id, beta_mapping.id, required_roles=("trade_return",))

    with pytest.raises(ValueError, match="provenance"):
        alpha_storage.record_analysis_evidence(
            thread_id=alpha_storage.create_thread("Cross dataset"),
            origin_turn_number=1,
            dataset_id=alpha_dataset.id,
            mapping_version_id=alpha_mapping.id,
            operation="summarize_results",
            schema_version="1.0",
            arguments={"dataset_id": alpha_dataset.id},
            result=summarize_results(beta_frame),
        )


def test_summarize_results_treats_missing_outcomes_as_ordered_streak_breakers(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome\n1,win\n1,\n1,win\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_outcome")],
    )
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_outcome")
    )

    result = summarize_results(frame)

    assert result["metrics"]["wins"] == 2
    assert result["metrics"]["max_consecutive_wins"] == 1
    assert result["exclusions"] == [{"kind": "required_role_diagnostic", "role": "trade_outcome", "reason": "blank", "count": 1}]


def test_summarize_results_treats_invalid_required_returns_as_ordered_streak_breakers(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome\n1,win\nbad,loss\n1,win\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_outcome")],
    )
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_outcome")
    )

    result = summarize_results(frame)

    assert frame.outcome_sequence == ("win", None, "win")
    assert result["counts"] == {"source_rows": 3, "filtered_rows": 3, "valid_rows": 2, "excluded_rows": 1}
    assert result["metrics"]["wins"] == 2
    assert result["metrics"]["losses"] == 0
    assert result["metrics"]["wins"] + result["metrics"]["losses"] + result["metrics"]["breakevens"] == 2
    assert result["metrics"]["win_rate"] == 1.0
    assert result["metrics"]["max_consecutive_wins"] == 1
    assert result["exclusions"] == [{"kind": "required_role_diagnostic", "role": "trade_return", "reason": "invalid", "count": 1}]


def test_summarize_results_uses_a_neutral_breaker_for_invalid_required_return_wins(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome\n-1,loss\nbad,win\n-1,loss\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_outcome")],
    )
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_outcome")
    )

    result = summarize_results(frame)

    assert frame.outcome_sequence == ("loss", None, "loss")
    assert result["counts"] == {"source_rows": 3, "filtered_rows": 3, "valid_rows": 2, "excluded_rows": 1}
    assert result["metrics"]["wins"] == 0
    assert result["metrics"]["losses"] == 2
    assert result["metrics"]["loss_rate"] == 1.0
    assert result["metrics"]["max_consecutive_losses"] == 1


def test_summarize_results_marks_realized_reward_risk_unavailable_when_mean_loss_is_zero(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome\n1,win\n0,loss\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_outcome")],
    )
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_outcome")
    )

    result = summarize_results(frame)

    assert result["metrics"]["realized_reward_risk"] is None
    assert "unavailable_metric" in result["limitations"]


def test_summarize_results_requires_trade_return_for_return_metrics(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome\nbad,win\n2,loss\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_outcome")],
    )
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_outcome",))

    result = summarize_results(frame)

    assert result["counts"] == {"source_rows": 2, "filtered_rows": 2, "valid_rows": 2, "excluded_rows": 0}
    assert result["metrics"]["wins"] == 1
    assert result["metrics"]["total_return"] is None
    assert result["metrics"]["cumulative_return"] is None
    assert "unavailable_metric" in result["limitations"]


def test_analysis_frame_parses_the_verified_bytes_if_the_local_original_is_swapped(tmp_path, monkeypatch):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result\n1\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R")],
    )
    original_path = storage.database_path.parent / "datasets" / dataset.id / "original.csv"
    original_parser = analysis_module._inspection_rows_from_bytes

    def parse_verified_bytes(contents, extension, spec):
        original_path.write_text("Result\n999\n", encoding="utf-8")
        return original_parser(contents, extension, spec)

    monkeypatch.setattr(analysis_module, "_inspection_rows_from_bytes", parse_verified_bytes)

    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    assert frame.data["trade_return"].tolist() == [1.0]


@pytest.mark.parametrize(
    "operator,value",
    [
        ("eq", datetime(2026, 1, 2)),
        ("in", [datetime(2026, 1, 2)]),
        ("between", [datetime(2026, 1, 1), datetime(2026, 1, 3)]),
    ],
)
def test_timestamp_filters_reject_naive_values_for_aware_dataset_times(tmp_path, operator, value):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Trade Date\n1,2026-01-02T12:00:00+00:00\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_timestamp")],
    )
    timestamp_id = next(
        entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.semantic_role == "trade_timestamp"
    )

    with pytest.raises(ValueError, match="timezone"):
        build_analysis_frame(
            storage,
            dataset.id,
            mapping.id,
            required_roles=("trade_return",),
            filters=(AnalysisFilter(timestamp_id or "", operator, value),),
        )


def test_group_results_reports_session_metrics_with_validated_filter_handoff(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome,Session,Desk Secret\n1,win,London,do-not-disclose\n-1,loss,London,do-not-disclose\n2,win,New York,do-not-disclose\nbad,loss,New York,do-not-disclose\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="trade_outcome"),
            MappingEntry(2, analysis_label="Session", model_disclosure=True),
        ],
    )
    entries = storage.mapping_entries(mapping.id)
    session_id = next(entry.field_id for entry in entries if entry.analysis_label == "Session")
    frame = build_analysis_frame(
        storage,
        dataset.id,
        mapping.id,
        required_roles=("trade_return", "trade_outcome"),
        filters=(AnalysisFilter(session_id or "", "in", ["London", "New York"]),),
    )

    result = group_results(frame, (session_id or "",))

    assert result["operation"] == "group_results"
    assert result["grouping"] == {"field_ids": [session_id], "limit": 50}
    london, new_york = result["group_evidence"]["returned_groups"]
    assert london["key"] == ["London"]
    assert {name: london[name] for name in ("filtered_rows", "valid_rows", "excluded_rows")} == {"filtered_rows": 2, "valid_rows": 2, "excluded_rows": 0}
    assert london["metrics"]["mean_return"] == 0.0
    assert new_york["key"] == ["New York"]
    assert {name: new_york[name] for name in ("filtered_rows", "valid_rows", "excluded_rows")} == {"filtered_rows": 2, "valid_rows": 1, "excluded_rows": 1}
    assert result["filters"] == [{
        "field_id": session_id,
        "operator": "in",
        "value_spec": {"mapping_version_id": mapping.id, "value_type": "categorical", "unit": None, "values": ["London", "New York"]},
        "canonical_id": result["filters"][0]["canonical_id"],
    }]
    assert "Desk Secret" not in json.dumps(result)
    assert "do-not-disclose" not in json.dumps(result)


def test_group_results_supports_two_columns_and_caps_deterministic_groups(tmp_path):
    rows = "\n".join(f"1,S{index // 5},Setup{index % 5}" for index in range(55))
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        f"Result,Session,Setup\n{rows}\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="Session", model_disclosure=True),
            MappingEntry(2, analysis_label="Setup", model_disclosure=True),
        ],
    )
    entries = storage.mapping_entries(mapping.id)
    session_id = next(entry.field_id for entry in entries if entry.analysis_label == "Session")
    setup_id = next(entry.field_id for entry in entries if entry.analysis_label == "Setup")
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    result = group_results(frame, (session_id or "", setup_id or ""))

    partition = result["group_evidence"]
    assert len(partition["returned_groups"]) == 50
    assert partition["omitted"] == {"group_count": 5, "filtered_rows": 5, "valid_rows": 5, "excluded_rows": 0}
    assert all(group["key"][0].startswith("S") and group["key"][1].startswith("Setup") for group in partition["returned_groups"])
    with pytest.raises(ValueError, match="one or two"):
        group_results(frame, (session_id or "", setup_id or "", session_id or ""))


@pytest.mark.parametrize(
"entries,group_field,required_roles",
[
    ([MappingEntry(0, semantic_role="trade_return", unit="R")], "trade_return", ("trade_return",)),
    ([MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, analysis_label="Session")], "Session", ("trade_return",)),
],
)
def test_group_results_rejects_non_groupable_or_undisclosed_fields(tmp_path, entries, group_field, required_roles):
    storage, dataset, mapping = _confirmed_dataset(tmp_path, "Result,Session\n1,London\n", entries)
    mapped = storage.mapping_entries(mapping.id)
    field_id = next(entry.field_id for entry in mapped if entry.semantic_role == group_field or entry.analysis_label == group_field)
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=required_roles)

    with pytest.raises(ValueError, match="group"):
        group_results(frame, (field_id or "",))


def test_group_results_handles_empty_filtered_data_without_group_values(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session\n1,London\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, analysis_label="Session", model_disclosure=True)],
    )
    session_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Session")
    frame = build_analysis_frame(
        storage,
        dataset.id,
        mapping.id,
        required_roles=("trade_return",),
        filters=(AnalysisFilter(session_id or "", "eq", "New York"),),
    )

    result = group_results(frame, (session_id or "",))

    assert result["group_evidence"]["returned_groups"] == []
    assert result["group_evidence"]["omitted"] == {"group_count": 0, "filtered_rows": 0, "valid_rows": 0, "excluded_rows": 0}
    assert result["limitations"] == ["no_matching_rows"]


def test_group_results_reports_blank_boolean_values_as_ungrouped_accounting(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Condition\n1,true\n1,false\n1,\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, analysis_label="Condition", model_disclosure=True)],
    )
    condition_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Condition")
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    result = group_results(frame, (condition_id or "",))

    partition = result["group_evidence"]
    assert [group["key"] for group in partition["returned_groups"]] == [[False], [True]]
    assert partition["ungrouped"] == {
        "filtered_rows": 1, "valid_rows": 1, "excluded_rows": 0,
        "reasons": [{"field_id": condition_id, "reason": "blank", "count": 1}],
    }
    assert partition["omitted"] == {"group_count": 0, "filtered_rows": 0, "valid_rows": 0, "excluded_rows": 0}
    assert sum(group["filtered_rows"] for group in partition["returned_groups"]) + partition["ungrouped"]["filtered_rows"] == result["counts"]["filtered_rows"]
    assert "ungrouped_group_values_excluded" in result["limitations"]


def test_compare_groups_returns_both_sides_and_zero_delta_for_equal_metrics(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session,Desk Secret\n1,London,do-not-disclose\n-1,London,do-not-disclose\n1,New York,do-not-disclose\n-1,New York,do-not-disclose\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, analysis_label="Session", model_disclosure=True)],
    )
    session_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Session")
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    result = compare_groups(frame, session_id or "", "London", "New York")

    assert result["operation"] == "compare_groups"
    assert result["comparison"]["field_id"] == session_id
    assert result["comparison"]["a"]["value"] == "London"
    assert result["comparison"]["b"]["value"] == "New York"
    assert result["comparison"]["a"]["counts"]["valid_rows"] == 2
    assert result["comparison"]["deltas"]["mean_return"] == 0.0
    assert result["metric_definitions"]["comparison_delta"] == "A - B for numeric metrics available on both sides"
    assert "small_sample" in result["limitations"]
    assert "causal" not in json.dumps(result).casefold()
    assert "Desk Secret" not in json.dumps(result)
    assert "do-not-disclose" not in json.dumps(result)
    with pytest.raises(ValueError, match="absent from the approved mapped field domain"):
        compare_groups(frame, session_id or "", "London", "Atlantis")


def test_compare_groups_rejects_invalid_types_equal_values_and_unknown_fields(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Condition\n1,true\n1,false\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, analysis_label="Condition", model_disclosure=True)],
    )
    condition_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Condition")
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    grouped = group_results(frame, (condition_id or "",))

    assert [group["key"] for group in grouped["group_evidence"]["returned_groups"]] == [[False], [True]]
    with pytest.raises(ValueError, match="comparison"):
        compare_groups(frame, condition_id or "", "true", False)
    with pytest.raises(ValueError, match="distinct"):
        compare_groups(frame, condition_id or "", True, True)
    with pytest.raises(ValueError, match="group"):
        compare_groups(frame, "field-unknown", True, False)


def test_temporal_analysis_returns_months_halves_and_fixed_rolling_windows_with_bucket_n(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome,Trade Date,Desk Secret\n2,win,2026-02-02,do-not-disclose\n1,win,2026-01-03,do-not-disclose\n-1,loss,2026-01-20,do-not-disclose\n3,win,2026-03-01,do-not-disclose\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="trade_outcome"),
            MappingEntry(2, semantic_role="trade_timestamp"),
        ],
    )
    frame = build_analysis_frame(
        storage,
        dataset.id,
        mapping.id,
        required_roles=("trade_return", "trade_outcome", "trade_timestamp"),
        order_by="timestamp",
    )

    monthly = analyze_over_time(frame, mode="month")
    halves = analyze_over_time(frame, mode="halves")
    rolling = analyze_over_time(frame, mode="rolling", window_size=2)

    assert [(bucket["period"], bucket["counts"]["valid_rows"]) for bucket in monthly["buckets"]] == [
        ("2026-01", 2), ("2026-02", 1), ("2026-03", 1)
    ]
    assert [(bucket["period"], bucket["counts"]["valid_rows"]) for bucket in halves["buckets"]] == [
        ("earlier_half", 2), ("later_half", 2)
    ]
    assert [(bucket["start_date"], bucket["end_date"], bucket["counts"]["valid_rows"]) for bucket in rolling["buckets"]] == [
        ("2026-01-03", "2026-01-20", 2),
        ("2026-01-20", "2026-02-02", 2),
        ("2026-02-02", "2026-03-01", 2),
    ]
    assert "Desk Secret" not in json.dumps(monthly)
    assert "do-not-disclose" not in json.dumps(monthly)


def test_temporal_analysis_rejects_missing_or_mixed_timezone_timestamps(tmp_path):
    missing_storage, missing_dataset, missing_mapping = _confirmed_dataset(
        tmp_path / "missing",
        "Result\n1\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R")],
    )
    missing_frame = build_analysis_frame(
        missing_storage, missing_dataset.id, missing_mapping.id, required_roles=("trade_return",)
    )
    with pytest.raises(ValueError, match="timestamp"):
        analyze_over_time(missing_frame, mode="month")

    storage, dataset, mapping = _confirmed_dataset(
        tmp_path / "mixed",
        "Result,Trade Date\n1,2026-01-01\n2,2026-01-02T00:00:00+00:00\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_timestamp")],
    )
    with pytest.raises(ValueError, match="timezone"):
        build_analysis_frame(
            storage,
            dataset.id,
            mapping.id,
            required_roles=("trade_return", "trade_timestamp"),
            order_by="timestamp",
        )


def test_temporal_analysis_keeps_canonical_excluded_rows_in_bucket_accounting(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Trade Date\n1,2026-01-03\nbad,2026-01-20\nbad,2026-02-01\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_timestamp")],
    )
    frame = build_analysis_frame(
        storage,
        dataset.id,
        mapping.id,
        required_roles=("trade_return", "trade_timestamp"),
        order_by="timestamp",
    )

    result = analyze_over_time(frame, mode="month")

    january, february = result["buckets"]
    assert january["counts"] == {
        "source_rows": 2,
        "filtered_rows": 2,
        "valid_rows": 1,
        "excluded_rows": 1,
    }
    assert january["exclusions"] == [{"kind": "required_role_diagnostic", "role": "trade_return", "reason": "invalid", "count": 1}]
    assert february["counts"] == {"source_rows": 1, "filtered_rows": 1, "valid_rows": 0, "excluded_rows": 1}
    assert (february["start_date"], february["end_date"]) == ("2026-02-01", "2026-02-01")


def test_timestamp_order_rejects_mixed_timezones_in_excluded_timestamp_valid_rows(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Trade Date\n1,2026-01-01\nbad,2026-01-02T00:00:00+00:00\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_timestamp")],
    )

    with pytest.raises(ValueError, match="timezone"):
        build_analysis_frame(
            storage,
            dataset.id,
            mapping.id,
            required_roles=("trade_return", "trade_timestamp"),
            order_by="timestamp",
        )


def test_temporal_rolling_buckets_over_the_evidence_limit_fail_closed(tmp_path):
    rows = "\n".join(f"1,2026-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(55))
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        f"Result,Trade Date\n{rows}\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_timestamp")],
    )
    frame = build_analysis_frame(
        storage,
        dataset.id,
        mapping.id,
        required_roles=("trade_return", "trade_timestamp"),
        order_by="timestamp",
    )

    with pytest.raises(AnalysisLimitError) as error:
        analyze_over_time(frame, mode="rolling", window_size=2)

    assert error.value.code == "temporal_bucket_limit_exceeded"
    assert error.value.metadata == {"mode": "rolling", "total_buckets": 54, "max_buckets": 50}


def test_mfe_mae_outputs_confirmed_unit_distributions_and_explicit_unavailability(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,MFE,MAE,Desk Secret\n1,4,-2,do-not-disclose\n2,bad,-4,do-not-disclose\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="mfe", unit="points"),
            MappingEntry(2, semantic_role="mae", unit="points"),
        ],
    )
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    result = analyze_mfe_mae(frame)

    assert result["mfe"] == {
        "available": True,
        "field_id": next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.semantic_role == "mfe"),
        "unit": "points",
        "counts": {"source_rows": 2, "filtered_rows": 2, "valid_rows": 1, "excluded_rows": 1},
        "exclusions": [{"reason": "invalid", "count": 1}],
        "metrics": pytest.approx({"mean": 4.0, "median": 4.0, "minimum": 4.0, "maximum": 4.0, "sample_standard_deviation": None, "percentile_05": 4.0, "percentile_25": 4.0, "percentile_75": 4.0, "percentile_95": 4.0}),
    }
    assert result["mae"]["available"] is True
    assert result["mae"]["unit"] == "points"
    assert "Desk Secret" not in json.dumps(result)
    assert "do-not-disclose" not in json.dumps(result)

    absent_storage, absent_dataset, absent_mapping = _confirmed_dataset(
        tmp_path / "absent", "Result\n1\n", [MappingEntry(0, semantic_role="trade_return", unit="R")]
    )
    absent = analyze_mfe_mae(
        build_analysis_frame(absent_storage, absent_dataset.id, absent_mapping.id, required_roles=("trade_return",))
    )
    assert absent["mfe"] == {"available": False, "reason": "missing_confirmed_mapping"}
    assert absent["mae"] == {"available": False, "reason": "missing_confirmed_mapping"}
    assert "unit" not in absent["mfe"] and "unit" not in absent["mae"]


def test_win_rate_wilson_interval_and_r_spread_are_descriptive_without_inference(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome\n1,win\n-1,loss\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_outcome")],
    )
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_outcome"))

    result = summarize_results(frame)

    assert result["metrics"]["wilson_95_lower"] == pytest.approx(0.09453120573423071)
    assert result["metrics"]["wilson_95_upper"] == pytest.approx(0.9054687942657693)
    assert result["metrics"]["sample_standard_deviation"] == pytest.approx(math.sqrt(2))
    assert result["metric_definitions"]["win_rate_interval"] == "Wilson 95% interval"
    assert "small_sample" in result["limitations"]
    rendered = json.dumps(result).casefold()
    assert "p-value" not in rendered and "hypothesis" not in rendered and "causal" not in rendered and "edge detector" not in rendered

    zero_storage, zero_dataset, zero_mapping = _confirmed_dataset(
        tmp_path / "zero",
        "Result,Outcome\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_outcome")],
    )
    zero = summarize_results(
        build_analysis_frame(zero_storage, zero_dataset.id, zero_mapping.id, required_roles=("trade_return", "trade_outcome"))
    )
    assert zero["metrics"]["win_rate"] is None
    assert zero["metrics"]["wilson_95_lower"] is None
    assert zero["metrics"]["wilson_95_upper"] is None
    assert "small_sample" in zero["limitations"]


def test_filter_invalid_values_are_excluded_not_hidden_and_rebuild_reproduces_counts(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Trade Date,Session\n1,2026-01-02,London\nbad,not-a-date,New York\n,,London\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="trade_timestamp"),
            MappingEntry(2, analysis_label="Session", model_disclosure=True),
        ],
    )
    entries = storage.mapping_entries(mapping.id)
    result_id = next(entry.field_id for entry in entries if entry.semantic_role == "trade_return")
    timestamp_id = next(entry.field_id for entry in entries if entry.semantic_role == "trade_timestamp")
    session_id = next(entry.field_id for entry in entries if entry.analysis_label == "Session")

    not_blank = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=(AnalysisFilter(result_id or "", "not_blank"),)
    )
    is_blank = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=(AnalysisFilter(result_id or "", "is_blank"),)
    )
    numeric = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=(AnalysisFilter(result_id or "", "gt", 0),)
    )
    temporal = build_analysis_frame(
        storage,
        dataset.id,
        mapping.id,
        required_roles=("trade_return", "trade_timestamp"),
        filters=(AnalysisFilter(timestamp_id or "", "gte", datetime(2026, 1, 1)),),
        order_by="timestamp",
    )

    assert (not_blank.filtered_rows, not_blank.valid_rows, not_blank.excluded_rows) == (1, 1, 2)
    assert not_blank.disposition_counts["filter_invalid"] == 1
    assert (is_blank.filtered_rows, is_blank.valid_rows, is_blank.excluded_rows) == (1, 0, 3)
    assert is_blank.disposition_counts["required_role_blank"] == 1
    assert (numeric.filtered_rows, numeric.valid_rows, numeric.excluded_rows) == (1, 1, 2)
    assert (temporal.filtered_rows, temporal.valid_rows, temporal.excluded_rows) == (1, 1, 2)
    assert group_results(not_blank, (session_id or "",))["group_evidence"]["returned_groups"][0]["filtered_rows"] == 1

    reopened = Storage(storage.database_path)
    reopened.initialize()
    rebuilt = build_analysis_frame(
        reopened, dataset.id, mapping.id, required_roles=("trade_return",), filters=(AnalysisFilter(result_id or "", "not_blank"),)
    )
    assert summarize_results(rebuilt)["counts"] == summarize_results(not_blank)["counts"]
    assert summarize_results(rebuilt)["exclusions"] == summarize_results(not_blank)["exclusions"]


def test_filter_limit_is_shared_and_exact_duplicates_are_deduplicated(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path, "Result,Session\n1,London\n", [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, analysis_label="Session")]
    )
    session_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Session")
    at_limit = tuple(AnalysisFilter(session_id or "", "eq", "London") for _ in range(20))

    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=at_limit)

    assert len(summarize_results(frame)["filters"]) == 1
    evidence = storage.record_analysis_evidence(
        thread_id=storage.create_thread("Filter limit"),
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=mapping.id,
        operation="summarize_results",
        schema_version="1.0",
        arguments={"dataset_id": dataset.id},
        result=summarize_results(frame),
    )
    assert evidence.dataset_id == dataset.id
    with pytest.raises(ValueError, match="filter limit"):
        build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=at_limit + (AnalysisFilter(session_id or "", "eq", "London"),))


def test_filter_stage_exclusion_for_a_nonrequired_typed_field_is_persistable(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,MFE\n1,1\n2,bad\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="MFE"),
        ],
    )
    score_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "MFE")
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=(AnalysisFilter(score_id or "", "gt", 0),)
    )

    result = summarize_results(frame)

    assert result["counts"] == {"source_rows": 2, "filtered_rows": 1, "valid_rows": 1, "excluded_rows": 1}
    assert result["exclusions"] == [
        {
            "kind": "filter_invalid",
            "canonical_id": result["filters"][0]["canonical_id"],
            "reason": "invalid",
            "count": 1,
        }
    ]
    storage.record_analysis_evidence(
        thread_id=storage.create_thread("Filter exclusion"),
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=mapping.id,
        operation="summarize_results",
        schema_version="1.0",
        arguments={"dataset_id": dataset.id},
        result=result,
    )


@pytest.mark.parametrize("values", [(1e308, 1e308), (1.7e308, -1.7e308)])
def test_nonfinite_derived_summary_metrics_fail_closed(tmp_path, values):
    rows = "\n".join(str(value) for value in values)
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path, f"Result\n{rows}\n", [MappingEntry(0, semantic_role="trade_return", unit="R")]
    )
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    with pytest.raises(AnalysisNumericError, match="numeric_overflow") as error:
        summarize_results(frame)

    assert error.value.code == "numeric_overflow"


def test_nonfinite_comparison_group_and_temporal_metrics_fail_closed(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session,Trade Date\n1e308,A,2026-01-01\n-1e308,B,2026-02-01\n1e308,C,2026-03-01\n1e308,C,2026-03-02\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="Session", model_disclosure=True),
            MappingEntry(2, semantic_role="trade_timestamp"),
        ],
    )
    entries = storage.mapping_entries(mapping.id)
    session_id = next(entry.field_id for entry in entries if entry.analysis_label == "Session")
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_timestamp"), order_by="timestamp"
    )

    with pytest.raises(AnalysisNumericError, match="numeric_overflow"):
        compare_groups(frame, session_id or "", "A", "B")
    with pytest.raises(AnalysisNumericError, match="numeric_overflow"):
        group_results(frame, (session_id or "",))
    with pytest.raises(AnalysisNumericError, match="numeric_overflow"):
        analyze_over_time(frame, mode="month")


def test_nonfinite_cumulative_and_mfe_distribution_metrics_fail_closed(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,MFE\n1,1.7e308\n2,-1.7e308\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="mfe", unit="points")],
    )
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",))

    with pytest.raises(AnalysisNumericError, match="numeric_overflow"):
        analysis_module._r_metrics((1e308, 1e308), None)
    with pytest.raises(AnalysisNumericError, match="numeric_overflow"):
        analyze_mfe_mae(frame)


def test_ordinary_result_envelopes_remain_json_safe(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path, "Result\n1\n-1\n", [MappingEntry(0, semantic_role="trade_return", unit="R")]
    )

    result = summarize_results(build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",)))

    assert json.dumps(result, allow_nan=False)


def _monthly_rows(count: int) -> str:
    return "\n".join(f"1,{2020 + index // 12:04d}-{index % 12 + 1:02d}-01" for index in range(count))


@pytest.mark.parametrize("count", [49, 50])
def test_temporal_monthly_buckets_at_or_below_limit_remain_complete_and_chronological(tmp_path, count):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        f"Result,Trade Date\n{_monthly_rows(count)}\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_timestamp")],
    )
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_timestamp"), order_by="timestamp")

    result = analyze_over_time(frame, mode="month")

    assert len(result["buckets"]) == count
    assert [bucket["period"] for bucket in result["buckets"]] == sorted(bucket["period"] for bucket in result["buckets"])


@pytest.mark.parametrize("count", [51, 60])
def test_temporal_monthly_limit_fails_closed_but_filtered_range_and_halves_remain_bounded(tmp_path, count):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        f"Result,Trade Date\n{_monthly_rows(count)}\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_timestamp")],
    )
    timestamp_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.semantic_role == "trade_timestamp")
    frame = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_timestamp"), order_by="timestamp")

    with pytest.raises(AnalysisLimitError) as error:
        analyze_over_time(frame, mode="month")
    assert error.value.metadata == {"mode": "month", "total_buckets": count, "max_buckets": 50}
    assert len(analyze_over_time(frame, mode="halves")["buckets"]) == 2

    if count == 51:
        return

    narrowed = build_analysis_frame(
        storage,
        dataset.id,
        mapping.id,
        required_roles=("trade_return", "trade_timestamp"),
        filters=(AnalysisFilter(timestamp_id or "", "gte", datetime(2020, 11, 1)),),
        order_by="timestamp",
    )
    assert len(analyze_over_time(narrowed, mode="month")["buckets"]) == 50


def test_canonical_row_disposition_is_filter_order_independent_and_shared_by_mfe_mae(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session,MFE,Trade Date\n1,London,4,2026-01-01\nbad,New York,99,2026-01-02\n2,London,bad,2026-01-03\n3,New York,7,2026-01-04\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="Session", model_disclosure=True),
            MappingEntry(2, semantic_role="mfe", unit="points"),
            MappingEntry(3, semantic_role="trade_timestamp"),
        ],
    )
    entries = storage.mapping_entries(mapping.id)
    result_id = next(entry.field_id for entry in entries if entry.semantic_role == "trade_return")
    session_id = next(entry.field_id for entry in entries if entry.analysis_label == "Session")
    filters = (
        AnalysisFilter(session_id or "", "eq", "London"),
        AnalysisFilter(result_id or "", "gt", 0),
    )

    forward = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=filters)
    reverse = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=tuple(reversed(filters)))

    assert forward.data["source_row_ordinal"].tolist() == reverse.data["source_row_ordinal"].tolist() == [0, 2]
    assert forward.disposition_counts == reverse.disposition_counts == {
        "valid_for_analysis": 2,
        "filtered_out": 1,
        "filter_invalid": 1,
        "required_role_blank": 0,
        "required_role_invalid": 0,
    }
    assert summarize_results(forward)["counts"] == {
        "source_rows": 4,
        "filtered_rows": 2,
        "valid_rows": 2,
        "excluded_rows": 2,
    }
    assert analyze_mfe_mae(forward)["mfe"]["counts"] == {
        "source_rows": 4,
        "filtered_rows": 2,
        "valid_rows": 1,
        "excluded_rows": 3,
    }


def test_filter_permutation_matrix_keeps_dispositions_and_invalid_diagnostics_stable(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,MFE,Trade Date,Session\n1,1,2026-01-01,London\nbad,1,2026-01-02,New York\n1,bad,2026-01-03,New York\nbad,bad,2026-01-04,London\n,bad,2026-01-05,London\n1,1,bad,New York\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="mfe", unit="points"),
            MappingEntry(2, semantic_role="trade_timestamp"),
            MappingEntry(3, analysis_label="Session"),
        ],
    )
    entries = storage.mapping_entries(mapping.id)
    by_role = {entry.semantic_role: entry.field_id for entry in entries if entry.semantic_role}
    session_id = next(entry.field_id for entry in entries if entry.analysis_label == "Session")
    cases = (
        (AnalysisFilter(session_id or "", "eq", "London"), AnalysisFilter(by_role["trade_return"] or "", "gt", 0)),
        (AnalysisFilter(session_id or "", "eq", "London"), AnalysisFilter(by_role["mfe"] or "", "gt", 0)),
        (AnalysisFilter(by_role["trade_return"] or "", "is_blank"), AnalysisFilter(by_role["mfe"] or "", "gt", 0)),
        (AnalysisFilter(by_role["trade_timestamp"] or "", "gte", datetime(2026, 1, 1)), AnalysisFilter(session_id or "", "eq", "London")),
    )

    for filters in cases:
        first = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=filters)
        second = build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=tuple(reversed(filters)))
        first_diagnostics = sorted((item.canonical_id, item.count) for item in first.exclusions if item.kind == "filter_invalid")
        second_diagnostics = sorted((item.canonical_id, item.count) for item in second.exclusions if item.kind == "filter_invalid")

        assert first.data["source_row_ordinal"].tolist() == second.data["source_row_ordinal"].tolist()
        assert first.disposition_counts == second.disposition_counts
        assert first_diagnostics == second_diagnostics


def test_all_deterministic_operations_share_the_frame_eligible_population(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,MFE,Trade Date,Session\n1,4,2026-01-01,A\n2,bad,2026-01-02,B\nbad,7,2026-01-03,A\n3,8,2026-01-04,B\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="mfe", unit="points"),
            MappingEntry(2, semantic_role="trade_timestamp"),
            MappingEntry(3, analysis_label="Session", model_disclosure=True),
        ],
    )
    session_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Session")
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_timestamp"), order_by="timestamp"
    )

    results = (
        summarize_results(frame),
        group_results(frame, (session_id or "",)),
        compare_groups(frame, session_id or "", "A", "B"),
        analyze_over_time(frame, mode="halves"),
        analyze_mfe_mae(frame),
    )

    assert {tuple(sorted(result["counts"].items())) for result in results} == {
        (("excluded_rows", 1), ("filtered_rows", 4), ("source_rows", 4), ("valid_rows", 3))
    }
    assert analyze_mfe_mae(frame)["mfe"]["counts"]["valid_rows"] == 2


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ("1,2026-01-01\n", [(1, 1)]),
        ("1,2026-01-01\n2,2026-01-01\n", [(1, 1), (1, 2)]),
        ("1,2026-01-01\n2,2026-01-01\n3,2026-01-01\n4,2026-01-01\n", [(2, 3), (2, 7)]),
        ("1,2026-01-01\n2,2026-01-01\n3,2026-01-01\n4,2026-01-01\n5,2026-01-01\n", [(3, 6), (2, 9)]),
    ],
)
def test_temporal_halves_use_disjoint_timestamp_and_source_ordinal_membership(tmp_path, rows, expected):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        f"Result,Trade Date\n{rows}",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_timestamp")],
    )
    frame = build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return", "trade_timestamp"), order_by="timestamp"
    )

    buckets = analyze_over_time(frame, mode="halves")["buckets"]

    assert [(bucket["counts"]["valid_rows"], bucket["metrics"]["total_return"]) for bucket in buckets] == expected
    assert sum(bucket["counts"]["valid_rows"] for bucket in buckets) == frame.valid_rows


def test_persisted_filter_invalid_diagnostic_is_bound_to_the_recorded_filter(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session\n1,London\nbad,New York\n",
        [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, analysis_label="Session")],
    )
    entries = storage.mapping_entries(mapping.id)
    result_id = next(entry.field_id for entry in entries if entry.semantic_role == "trade_return")
    session_id = next(entry.field_id for entry in entries if entry.analysis_label == "Session")
    result = summarize_results(
        build_analysis_frame(
            storage,
            dataset.id,
            mapping.id,
            required_roles=("trade_return",),
            filters=(AnalysisFilter(session_id or "", "eq", "London"), AnalysisFilter(result_id or "", "gt", 0)),
        )
    )
    evidence = storage.record_analysis_evidence(
        thread_id=storage.create_thread("Provenance"),
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=mapping.id,
        operation="summarize_results",
        schema_version="1.0",
        arguments={"dataset_id": dataset.id},
        result=result,
    )
    storage.record_analysis_tool_output(evidence.thread_id, "call_1", evidence.id, result)

    restarted = Storage(storage.database_path)
    restarted.initialize()
    persisted = restarted.analysis_tool_outputs(evidence.thread_id)[0]["output"]
    diagnostic = next(item for item in persisted["exclusions"] if item["kind"] == "filter_invalid")

    recorded_filter = next(item for item in persisted["filters"] if item["canonical_id"] == diagnostic["canonical_id"])
    assert recorded_filter["field_id"] == result_id and recorded_filter["operator"] == "gt"
    assert "bad" not in json.dumps(persisted)


def test_same_field_filters_have_replayable_specs_and_nonexclusive_diagnostics(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session\n1,London\nbad,London\n4,New York\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="Session", model_disclosure=True),
        ],
    )
    result_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.semantic_role == "trade_return")
    result = summarize_results(build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",),
        filters=(AnalysisFilter(result_id or "", "gt", 0), AnalysisFilter(result_id or "", "lt", 3)),
    ))

    assert result["exclusion_contract"] == {
        "row_dispositions_exclusive": True,
        "diagnostic_exclusions_exclusive": False,
        "diagnostic_exclusions_may_overlap": True,
    }
    assert {(item["operator"], tuple(item["value_spec"]["values"])) for item in result["filters"]} == {("gt", (0.0,)), ("lt", (3.0,))}
    assert len({item["canonical_id"] for item in result["filters"]}) == 2
    assert result["disposition_counts"]["filter_invalid"] == 1
    assert {item["canonical_id"] for item in result["exclusions"] if item["kind"] == "filter_invalid"} == {
        item["canonical_id"] for item in result["filters"]
    }
    duplicate = summarize_results(build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",),
        filters=(AnalysisFilter(result_id or "", "gt", 0), AnalysisFilter(result_id or "", "gt", 0), AnalysisFilter(result_id or "", "lt", 3)),
    ))
    assert duplicate["filters"] == result["filters"]
    assert duplicate["counts"] == result["counts"]

    evidence = storage.record_analysis_evidence(
        thread_id=storage.create_thread("Same field filters"), origin_turn_number=1,
        dataset_id=dataset.id, mapping_version_id=mapping.id, operation="summarize_results",
        schema_version="1.0", arguments={"dataset_id": dataset.id}, result=result,
    )
    storage.record_analysis_tool_output(evidence.thread_id, "same-field", evidence.id, result)
    restarted = Storage(storage.database_path)
    restarted.initialize()
    replayed = restarted.analysis_tool_outputs(evidence.thread_id)[0]["output"]
    assert replayed["filters"] == result["filters"]
    assert replayed["exclusions"] == result["exclusions"]


def test_group_results_keeps_excluded_only_groups_and_reconciles_group_universe(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session\n1,NYAM\n2,NYAM\nbad,Asia\nbad,Asia\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="Session", model_disclosure=True),
        ],
    )
    session_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Session")
    result = group_results(build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",)
    ), (session_id or "",))

    asia, nyam = result["group_evidence"]["returned_groups"]
    assert ({name: asia[name] for name in ("key", "filtered_rows", "valid_rows", "excluded_rows")}) == {"key": ["Asia"], "filtered_rows": 2, "valid_rows": 0, "excluded_rows": 2}
    assert nyam["key"] == ["NYAM"]
    assert "no_valid_rows" in asia["limitations"]
    assert result["group_evidence"]["omitted"]["filtered_rows"] == 0
    assert result["group_evidence"]["ungrouped"]["filtered_rows"] == 0
    evidence = storage.record_analysis_evidence(
        thread_id=storage.create_thread("Excluded-only group"), origin_turn_number=1,
        dataset_id=dataset.id, mapping_version_id=mapping.id, operation="group_results",
        schema_version="1.0", arguments={"dataset_id": dataset.id}, result=result,
    )
    storage.record_analysis_tool_output(evidence.thread_id, "excluded-group", evidence.id, result)
    restarted = Storage(storage.database_path)
    restarted.initialize()
    assert restarted.analysis_tool_outputs(evidence.thread_id)[0]["output"] == result


def test_group_results_emits_one_reconciled_partition_for_returned_omitted_and_ungrouped_rows(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session\n1,London\nbad,London\n1,Asia\n1,\nbad,\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="Session", model_disclosure=True),
        ],
    )
    session_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Session")
    result = group_results(build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",)
    ), (session_id or "",))

    partition = result["group_evidence"]
    returned = partition["returned_groups"]
    assert [(group["key"], group["filtered_rows"], group["valid_rows"], group["excluded_rows"]) for group in returned] == [
        (["Asia"], 1, 1, 0),
        (["London"], 2, 1, 1),
    ]
    assert partition["omitted"] == {"group_count": 0, "filtered_rows": 0, "valid_rows": 0, "excluded_rows": 0}
    assert partition["ungrouped"] == {
        "filtered_rows": 2,
        "valid_rows": 1,
        "excluded_rows": 1,
        "reasons": [{"field_id": session_id, "reason": "blank", "count": 2}],
    }
    assert all(group["filtered_rows"] == group["valid_rows"] + group["excluded_rows"] for group in returned)
    assert partition["omitted"]["filtered_rows"] == partition["omitted"]["valid_rows"] + partition["omitted"]["excluded_rows"]
    assert partition["ungrouped"]["filtered_rows"] == partition["ungrouped"]["valid_rows"] + partition["ungrouped"]["excluded_rows"]
    assert {
        name: sum(group[name] for group in returned) + partition["omitted"][name] + partition["ungrouped"][name]
        for name in ("filtered_rows", "valid_rows", "excluded_rows")
    } == {name: result["counts"][name] for name in ("filtered_rows", "valid_rows", "excluded_rows")}


def test_group_partition_rejects_corruption_before_persistence_and_on_replay(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session\n1,London\n-1,New York\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="Session", model_disclosure=True),
        ],
    )
    session_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Session")
    result = group_results(build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",)
    ), (session_id or "",))
    corruptions = []
    duplicate_key = deepcopy(result)
    duplicate_key["group_evidence"]["returned_groups"].append(deepcopy(duplicate_key["group_evidence"]["returned_groups"][0]))
    corruptions.append(duplicate_key)
    zero_returned = deepcopy(result)
    zero_returned["group_evidence"]["returned_groups"][0].update(filtered_rows=0, valid_rows=0, excluded_rows=0)
    corruptions.append(zero_returned)
    impossible_omitted = deepcopy(result)
    impossible_omitted["group_evidence"]["omitted"]["group_count"] = 1
    corruptions.append(impossible_omitted)
    impossible_ungrouped = deepcopy(result)
    impossible_ungrouped["group_evidence"]["ungrouped"]["excluded_rows"] = 1
    corruptions.append(impossible_ungrouped)
    overlap = deepcopy(result)
    overlap["group_evidence"]["returned_groups"][0]["filtered_rows"] += 1
    overlap["group_evidence"]["returned_groups"][0]["excluded_rows"] += 1
    corruptions.append(overlap)

    for corrupted in corruptions:
        with pytest.raises(ValueError, match="envelope"):
            validate_completed_evidence_envelope(corrupted)
        with pytest.raises(ValueError, match="envelope"):
            storage.record_analysis_evidence(
                thread_id=storage.create_thread("Invalid group partition"), origin_turn_number=1,
                dataset_id=dataset.id, mapping_version_id=mapping.id, operation="group_results",
                schema_version="1.0", arguments={"dataset_id": dataset.id}, result=corrupted,
            )

    evidence = storage.record_analysis_evidence(
        thread_id=storage.create_thread("Replay group partition"), origin_turn_number=1,
        dataset_id=dataset.id, mapping_version_id=mapping.id, operation="group_results",
        schema_version="1.0", arguments={"dataset_id": dataset.id}, result=result,
    )
    storage.record_analysis_tool_output(evidence.thread_id, "group-partition", evidence.id, result)
    corrupted_replay = deepcopy(result)
    corrupted_replay["group_evidence"]["returned_groups"][0]["key"] = ["New York"]
    with storage._connect() as connection:
        connection.execute("DROP TRIGGER analysis_tool_outputs_are_immutable")
        connection.execute(
            "UPDATE analysis_tool_outputs SET output_json = ? WHERE thread_id = ?",
            (json.dumps(corrupted_replay), evidence.thread_id),
        )
    with pytest.raises(ValueError, match="envelope"):
        storage.analysis_tool_outputs(evidence.thread_id)


def test_group_partition_rejects_impossible_omissions_and_overclaiming_ungrouped_reasons(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Session,Setup\n1,London,A\n1,London,A\n1,New York,B\n1,,\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="Session", model_disclosure=True),
            MappingEntry(2, analysis_label="Setup", model_disclosure=True),
        ],
    )
    entries = storage.mapping_entries(mapping.id)
    result_id = next(entry.field_id for entry in entries if entry.semantic_role == "trade_return")
    session_id = next(entry.field_id for entry in entries if entry.analysis_label == "Session")
    setup_id = next(entry.field_id for entry in entries if entry.analysis_label == "Setup")
    result = group_results(build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",)
    ), (session_id or "", setup_id or ""))

    assert result["group_evidence"]["ungrouped"]["reasons"] == [
        {"field_id": session_id, "reason": "blank", "count": 1},
        {"field_id": setup_id, "reason": "blank", "count": 1},
    ]
    validate_completed_evidence_envelope(result)

    too_many_omitted = deepcopy(result)
    too_many_omitted["group_evidence"]["returned_groups"][0].update(filtered_rows=1, valid_rows=1)
    too_many_omitted["group_evidence"]["omitted"] = {
        "group_count": 2, "filtered_rows": 1, "valid_rows": 1, "excluded_rows": 0,
    }
    unrelated_reason = deepcopy(result)
    unrelated_reason["group_evidence"]["ungrouped"]["reasons"][0]["field_id"] = result_id
    duplicate_reason = deepcopy(result)
    duplicate_reason["group_evidence"]["ungrouped"]["reasons"].append(
        deepcopy(duplicate_reason["group_evidence"]["ungrouped"]["reasons"][0])
    )
    overcounted_reason = deepcopy(result)
    overcounted_reason["group_evidence"]["ungrouped"]["reasons"][0]["count"] = 2

    for corrupted in (too_many_omitted, unrelated_reason, duplicate_reason, overcounted_reason):
        with pytest.raises(ValueError, match="envelope"):
            validate_completed_evidence_envelope(corrupted)


def test_completed_evidence_envelope_fails_closed_on_nonexclusive_contract_or_bad_filter_reference(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path, "Result\n1\nbad\n", [MappingEntry(0, semantic_role="trade_return", unit="R")]
    )
    field_id = storage.mapping_entries(mapping.id)[0].field_id
    result = summarize_results(build_analysis_frame(
        storage, dataset.id, mapping.id, required_roles=("trade_return",), filters=(AnalysisFilter(field_id or "", "gt", 0),)
    ))
    invalid_contract = {**result, "exclusion_contract": {**result["exclusion_contract"], "diagnostic_exclusions_exclusive": True}}
    invalid_reference = {**result, "exclusions": [{**result["exclusions"][0], "canonical_id": "0" * 12}]}
    with pytest.raises(ValueError, match="envelope"):
        validate_completed_evidence_envelope(invalid_contract)
    with pytest.raises(ValueError, match="envelope"):
        validate_completed_evidence_envelope(invalid_reference)


def test_group_limit_keeps_boundary_excluded_only_group_instead_of_omitting_it(tmp_path):
    pairs = [(f"S{index // 17}", f"K{index % 17}") for index in range(51)]
    ordered_pairs = sorted(pairs)
    rows = "\n".join([*(f"1,{session},{setup}" for session, setup in ordered_pairs[:49]), f"bad,{ordered_pairs[49][0]},{ordered_pairs[49][1]}", f"1,{ordered_pairs[50][0]},{ordered_pairs[50][1]}"])
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path, f"Result,Session,Setup\n{rows}\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, analysis_label="Session", model_disclosure=True),
            MappingEntry(2, analysis_label="Setup", model_disclosure=True),
        ],
    )
    session_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Session")
    setup_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.analysis_label == "Setup")
    result = group_results(build_analysis_frame(storage, dataset.id, mapping.id, required_roles=("trade_return",)), (session_id or "", setup_id or ""))

    partition = result["group_evidence"]
    assert len(partition["returned_groups"]) == 50
    assert partition["omitted"]["group_count"] == 1
    assert partition["returned_groups"][-1]["key"] == list(ordered_pairs[49])
    assert partition["returned_groups"][-1]["valid_rows"] == 0
    assert partition["omitted"]["filtered_rows"] == 1


def test_maximum_accepted_filter_diagnostics_fit_the_persisted_exclusion_envelope(tmp_path):
    storage, dataset, mapping = _confirmed_dataset(
        tmp_path,
        "Result,Outcome,Trade Date,Session,Direction,MFE,MAE,Instrument,Setup\n"
        "bad,win,2026-01-01,L,S,1,-1,ES,A\n"
        "1,,,,,,,,\n"
        "1,bad,bad,L,S,bad,bad,ES,A\n",
        [
            MappingEntry(0, semantic_role="trade_return", unit="R"),
            MappingEntry(1, semantic_role="trade_outcome"),
            MappingEntry(2, semantic_role="trade_timestamp"),
            MappingEntry(3, semantic_role="session"),
            MappingEntry(4, semantic_role="direction"),
            MappingEntry(5, semantic_role="mfe", unit="points"),
            MappingEntry(6, semantic_role="mae", unit="points"),
            MappingEntry(7, semantic_role="instrument"),
            MappingEntry(8, semantic_role="setup"),
        ],
    )
    result_id = next(entry.field_id for entry in storage.mapping_entries(mapping.id) if entry.semantic_role == "trade_return")
    frame = build_analysis_frame(
        storage,
        dataset.id,
        mapping.id,
        required_roles=("trade_return", "trade_outcome", "trade_timestamp", "session", "direction", "mfe", "mae", "instrument", "setup"),
        filters=tuple(AnalysisFilter(result_id or "", "in", [1, 100 + index]) for index in range(20)),
    )
    result = summarize_results(frame)

    assert len(result["exclusions"]) <= ANALYSIS_EXCLUSION_LIMIT
    assert len(json.dumps(result, separators=(",", ":"))) <= 8000
    storage.record_analysis_evidence(
        thread_id=storage.create_thread("Capacity"),
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=mapping.id,
        operation="summarize_results",
        schema_version="1.0",
        arguments={"dataset_id": dataset.id},
        result=result,
    )
