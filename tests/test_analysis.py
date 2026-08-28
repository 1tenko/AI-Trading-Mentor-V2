from pathlib import Path

import pytest

from mentor.analysis import AnalysisFilter, build_analysis_frame
from mentor.datasets import MappingEntry, create_inspected_mapping_draft, import_local_dataset, inspect_local_dataset
from mentor.storage import Storage


def _confirmed_dataset(tmp_path: Path, contents: str, entries: list[MappingEntry]):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text(contents, encoding="utf-8")
    dataset = import_local_dataset(source, storage, dataset_id_factory=lambda: "dataset-analysis").dataset
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
