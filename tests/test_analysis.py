import json
import math
from datetime import datetime
from pathlib import Path

import pytest

import mentor.analysis as analysis_module
from mentor.analysis import (
    AnalysisFilter,
    analyze_mfe_mae,
    analyze_over_time,
    build_analysis_frame,
    compare_groups,
    group_results,
    summarize_results,
)
from mentor.datasets import MappingEntry, create_inspected_mapping_draft, import_local_dataset, inspect_local_dataset
from mentor.storage import Storage


def _confirmed_dataset(tmp_path: Path, contents: str, entries: list[MappingEntry], *, dataset_id: str = "dataset-analysis"):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text(contents, encoding="utf-8")
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: dataset_id).dataset
    draft = create_inspected_mapping_draft(storage, inspect_local_dataset(storage, dataset.id), entries)
    confirmed = storage.confirm_mapping_version(draft.id)
    return storage, dataset, confirmed


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

    assert (frame.source_rows, frame.filtered_rows, frame.valid_rows, frame.excluded_rows) == (1, 0, 0, 0)
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
        {"field_id": field_id, "operator": "gt", "value_sha256": "6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b"}
    ]


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
    assert result["exclusions"] == [{"role": "trade_outcome", "reason": "blank", "count": 1}]


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
    assert result["exclusions"] == [{"role": "trade_return", "reason": "invalid", "count": 1}]


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
    assert result["grouping"] == {
        "field_ids": [session_id],
        "limit": 50,
        "total_groups": 2,
        "returned_groups": 2,
        "omitted_groups": 0,
        "omitted_group_rows": 0,
    }
    london, new_york = result["groups"]
    assert london["values"] == ["London"]
    assert london["counts"] == {"source_rows": 2, "filtered_rows": 2, "valid_rows": 2, "excluded_rows": 0}
    assert london["metrics"]["mean_return"] == 0.0
    assert new_york["values"] == ["New York"]
    assert new_york["counts"] == {"source_rows": 2, "filtered_rows": 2, "valid_rows": 1, "excluded_rows": 1}
    assert new_york["exclusions"] == [{"role": "trade_return", "reason": "invalid", "count": 1}]
    assert result["filters"] == [
        {"field_id": session_id, "operator": "in", "value_sha256": "9fcb3941d9a9dae06bc93589259d603afc167d4c41a32418ca0680c0f0389483"}
    ]
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

    assert result["grouping"]["total_groups"] == 55
    assert result["grouping"]["returned_groups"] == 50
    assert result["grouping"]["omitted_groups"] == 5
    assert result["grouping"]["omitted_group_rows"] == 5
    assert all(group["values"][0].startswith("S") and group["values"][1].startswith("Setup") for group in result["groups"])
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

    assert result["groups"] == []
    assert result["grouping"]["total_groups"] == 0
    assert result["omissions"]["counts"] == {"source_rows": 1, "filtered_rows": 0, "valid_rows": 0, "excluded_rows": 0}
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

    assert [group["values"] for group in result["groups"]] == [[False], [True]]
    assert result["ungrouped"] == {
        "counts": {"source_rows": 1, "filtered_rows": 1, "valid_rows": 1, "excluded_rows": 0},
        "reasons": [{"field_id": condition_id, "reason": "blank", "count": 1}],
    }
    assert result["omissions"]["counts"] == {"source_rows": 0, "filtered_rows": 0, "valid_rows": 0, "excluded_rows": 0}
    assert sum(group["counts"]["source_rows"] for group in result["groups"]) + result["ungrouped"]["counts"]["source_rows"] == result["counts"]["source_rows"]
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

    assert [group["values"] for group in grouped["groups"]] == [[False], [True]]
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
