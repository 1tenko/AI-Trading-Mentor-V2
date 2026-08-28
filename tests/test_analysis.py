import json
import math
from datetime import datetime
from pathlib import Path

import pytest

import mentor.analysis as analysis_module
from mentor.analysis import AnalysisFilter, build_analysis_frame, summarize_results
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

    assert result["counts"] == {"source_rows": 3, "filtered_rows": 3, "valid_rows": 2, "excluded_rows": 1}
    assert result["metrics"]["wins"] == 2
    assert result["metrics"]["max_consecutive_wins"] == 1
    assert result["exclusions"] == [{"role": "trade_return", "reason": "invalid", "count": 1}]


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
