"""Local, validated input boundary for deterministic backtest analysis."""

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Literal, Sequence

import pandas as pd

from mentor.datasets import DatasetImportError, _inspection_rows_from_bytes

if TYPE_CHECKING:
    from mentor.datasets import MappingEntry
    from mentor.storage import Storage


_OUTCOMES = {"win": "win", "w": "win", "loss": "loss", "l": "loss", "breakeven": "breakeven", "break even": "breakeven", "be": "breakeven"}
_FILTER_OPERATORS = frozenset({"eq", "neq", "in", "not_in", "is_blank", "not_blank", "gt", "gte", "lt", "lte", "between"})
_ORDER_MODES = frozenset({"source", "timestamp"})
_SUMMARY_METRICS = (
    "wins", "losses", "breakevens", "win_rate", "loss_rate", "wilson_95_lower", "wilson_95_upper", "max_consecutive_wins", "max_consecutive_losses",
    "total_return", "mean_return", "median_return", "mean_winning_return", "mean_losing_return", "best_return",
    "worst_return", "realized_reward_risk", "cumulative_return", "max_drawdown", "recovery_observations",
    "minimum", "maximum", "sample_standard_deviation", "first_quartile", "third_quartile", "interquartile_range",
    "iqr_outlier_count", "percentile_05", "percentile_10", "percentile_25", "percentile_50", "percentile_75",
    "percentile_90", "percentile_95", "valid_rows", "excluded_rows",
)
_GROUP_LIMIT = 50
_TEMPORAL_BUCKET_LIMIT = 50
_GROUP_METRICS = (
    "wins", "losses", "breakevens", "win_rate", "loss_rate", "wilson_95_lower", "wilson_95_upper",
    "total_return", "mean_return", "median_return", "mean_winning_return", "mean_losing_return",
    "best_return", "worst_return", "sample_standard_deviation", "first_quartile", "third_quartile",
    "interquartile_range", "iqr_outlier_count", "percentile_05", "percentile_10", "percentile_25",
    "percentile_50", "percentile_75", "percentile_90", "percentile_95",
)


@dataclass(frozen=True)
class AnalysisFilter:
    field_id: str
    operator: str
    value: object | None = None


@dataclass(frozen=True)
class AnalysisField:
    field_id: str
    column_name: str
    value_type: str
    semantic_role: str | None
    unit: str | None
    aggregate_labels_allowed: bool


@dataclass(frozen=True)
class ExclusionReason:
    role: str
    reason: Literal["blank", "invalid"]
    count: int


@dataclass(frozen=True)
class AnalysisOrder:
    mode: Literal["source", "timestamp"]
    timestamp_field_id: str | None = None


@dataclass(frozen=True)
class AnalysisFrame:
    """Mapped local values only; repr deliberately never includes cell values."""

    data: pd.DataFrame = field(repr=False, compare=False)
    source_data: pd.DataFrame = field(repr=False, compare=False)
    filtered_data: pd.DataFrame = field(repr=False, compare=False)
    dataset_id: str
    dataset_sha256: str
    mapping_version_id: int
    fields: tuple[AnalysisField, ...]
    filters: tuple[AnalysisFilter, ...] = field(repr=False)
    outcome_sequence: tuple[str | None, ...] = field(repr=False)
    required_roles: tuple[str, ...]
    source_rows: int
    filtered_rows: int
    valid_rows: int
    excluded_rows: int
    exclusions: tuple[ExclusionReason, ...]
    return_unit: str | None
    order: AnalysisOrder
    no_data_reason: Literal["no_source_rows", "no_matching_rows", "no_valid_rows"] | None


def build_analysis_frame(
    storage: "Storage",
    dataset_id: str,
    mapping_version_id: int,
    *,
    required_roles: Sequence[str],
    filters: Sequence[AnalysisFilter] = (),
    order_by: Literal["source", "timestamp"] = "source",
) -> AnalysisFrame:
    """Build the sole typed local input for later analysis calculations."""
    if not isinstance(required_roles, Sequence) or isinstance(required_roles, (str, bytes)):
        raise ValueError("required_roles must be an explicit sequence")
    required = tuple(required_roles)
    if not required or any(not isinstance(role, str) for role in required) or len(set(required)) != len(required):
        raise ValueError("required_roles must be a non-empty unique sequence")
    if order_by not in _ORDER_MODES:
        raise ValueError("analysis order is unsupported")

    dataset = storage.dataset(dataset_id)
    mapping = storage.mapping_version(mapping_version_id)
    if dataset is None or mapping is None or mapping.status != "confirmed" or mapping.dataset_id != dataset_id:
        raise ValueError("analysis requires a confirmed mapping for the dataset")
    spec = storage.dataset_import_spec(dataset_id)
    columns = storage.dataset_columns(dataset_id)
    if spec is None or not columns:
        raise ValueError("dataset metadata is unavailable")
    entries = tuple(entry for entry in storage.mapping_entries(mapping_version_id) if entry.field_id and (entry.semantic_role or entry.analysis_label))
    role_entries = {entry.semantic_role: entry for entry in entries if entry.semantic_role}
    for role in required:
        if role not in role_entries:
            raise ValueError(f"missing required role: {role}")
    if order_by == "timestamp" and "trade_timestamp" not in role_entries:
        raise ValueError("timestamp order requires confirmed trade_timestamp role")
    effective_required = required + (("trade_timestamp",) if order_by == "timestamp" and "trade_timestamp" not in required else ())

    fields = tuple(_field(entry) for entry in entries)
    by_id = {field.field_id: field for field in fields}
    validated_filters = tuple(_validate_filter(filter_, by_id) for filter_ in filters)
    original_path = storage.database_path.parent / "datasets" / dataset.id / f"original{dataset.original_extension}"
    try:
        contents = original_path.read_bytes()
    except OSError as error:
        raise DatasetImportError("local dataset original is unavailable") from error
    if hashlib.sha256(contents).hexdigest() != dataset.content_sha256:
        raise DatasetImportError("local dataset original no longer matches its immutable hash")
    headers, source_rows = _inspection_rows_from_bytes(contents, dataset.original_extension, spec)
    if headers != [column.original_header for column in columns] or len(source_rows) != dataset.source_row_count:
        raise ValueError("local dataset schema is unavailable")

    rows = [_typed_row(index, row, entries) for index, row in enumerate(source_rows)]
    _validate_datetime_filter_timezones(rows, validated_filters, by_id)
    filtered = [row for row in rows if _matches_filters(row, validated_filters, by_id)]
    valid = [row for row in filtered if all(row["states"][role_entries[role].field_id] == "valid" for role in effective_required)]
    exclusions = _exclusions(filtered, role_entries, effective_required)
    outcome_rows = [
        row
        for row in filtered
        if "trade_outcome" in effective_required
        and all(
            row["states"][role_entries[role].field_id] == "valid"
            for role in effective_required
            if role not in ("trade_outcome", "trade_return")
        )
    ]
    if order_by == "timestamp":
        timestamp_id = role_entries["trade_timestamp"].field_id
        _validate_timestamp_order(
            [row for row in rows if row["states"][timestamp_id or ""] == "valid"], timestamp_id
        )
        valid.sort(key=lambda row: (row["values"][timestamp_id], row["source_row_ordinal"]))
        outcome_rows.sort(key=lambda row: (row["values"][timestamp_id], row["source_row_ordinal"]))
        order = AnalysisOrder("timestamp", timestamp_id)
    else:
        order = AnalysisOrder("source")

    source_data = _frame_data(rows, fields, include_states=True)
    filtered_data = _frame_data(filtered, fields, include_states=True)
    data = _frame_data(valid, fields)
    no_data_reason = "no_source_rows" if not rows else "no_matching_rows" if not filtered else "no_valid_rows" if not valid else None
    return AnalysisFrame(
        data=data,
        source_data=source_data,
        filtered_data=filtered_data,
        dataset_id=dataset.id,
        dataset_sha256=dataset.content_sha256,
        mapping_version_id=mapping.id,
        fields=fields,
        filters=validated_filters,
        outcome_sequence=tuple(
            row["values"][role_entries["trade_outcome"].field_id]
            if row["states"][role_entries["trade_outcome"].field_id] == "valid"
            and (
                "trade_return" not in effective_required
                or row["states"][role_entries["trade_return"].field_id] == "valid"
            )
            else None
            for row in outcome_rows
        ),
        required_roles=effective_required,
        source_rows=len(rows),
        filtered_rows=len(filtered),
        valid_rows=len(valid),
        excluded_rows=len(filtered) - len(valid),
        exclusions=exclusions,
        return_unit=role_entries.get("trade_return").unit if "trade_return" in role_entries else None,
        order=order,
        no_data_reason=no_data_reason,
    )


def summarize_results(
    frame: AnalysisFrame,
) -> dict[str, object]:
    """Calculate the bounded core summary from an already validated frame."""
    if not isinstance(frame, AnalysisFrame):
        raise ValueError("summary requires a validated analysis frame")
    has_return = "trade_return" in frame.required_roles
    has_outcome = "trade_outcome" in frame.required_roles
    returns = frame.data["trade_return"].dropna().astype(float).tolist() if has_return else []
    metrics = {name: None for name in _SUMMARY_METRICS}
    metrics["valid_rows"] = frame.valid_rows
    metrics["excluded_rows"] = frame.excluded_rows

    if has_outcome:
        metrics.update(_outcome_metrics(frame.data["trade_outcome"].tolist(), frame.outcome_sequence))
    if has_return and returns:
        metrics.update(_return_metrics(returns, frame, has_outcome))
    if frame.return_unit == "R" and has_return and returns:
        metrics.update(_r_metrics(returns, frame if has_outcome else None))

    limitations = _summary_limitations(frame, metrics)
    return {
        **_result_metadata(frame, "summarize_results"),
        "metrics": metrics,
        "limitations": limitations,
    }


def group_results(frame: AnalysisFrame, group_fields: Sequence[str]) -> dict[str, object]:
    """Return bounded, privacy-approved descriptive metrics for one or two groups."""
    fields = _grouping_fields(frame, group_fields)
    group_columns = [field.column_name for field in fields]
    keys = _group_keys(frame.filtered_data, group_columns)
    source_keys = _group_keys(frame.source_data, group_columns)
    returned_keys = keys[:_GROUP_LIMIT]
    omitted_keys = keys[_GROUP_LIMIT:]
    groups = [_group_payload(_group_frame(frame, fields, key), key) for key in returned_keys]
    omitted = [_group_frame(frame, fields, key) for key in source_keys if key not in returned_keys]
    ungrouped = _ungrouped_frame(frame, fields)
    limitations = _group_limitations(frame, len(keys), len(returned_keys))
    if ungrouped.filtered_rows:
        limitations.append("ungrouped_group_values_excluded")
    return {
        **_result_metadata(frame, "group_results"),
        "grouping": {
            "field_ids": [field.field_id for field in fields],
            "limit": _GROUP_LIMIT,
            "total_groups": len(keys),
            "returned_groups": len(returned_keys),
            "omitted_groups": len(omitted_keys),
            "omitted_group_rows": sum(item.filtered_rows for item in omitted),
        },
        "omissions": {"counts": _combined_counts(omitted)},
        "ungrouped": {
            "counts": _frame_counts(ungrouped),
            "reasons": _ungrouped_reasons(frame.filtered_data, fields),
        },
        "groups": groups,
        "limitations": limitations,
    }


def compare_groups(frame: AnalysisFrame, field_id: str, value_a: object, value_b: object) -> dict[str, object]:
    """Compare two typed, distinct values in an approved group field without inference."""
    field = _grouping_fields(frame, (field_id,))[0]
    _validate_comparison_value(frame, field, value_a)
    _validate_comparison_value(frame, field, value_b)
    if value_a == value_b:
        raise ValueError("comparison values must be distinct")
    a = _group_frame(frame, (field,), (value_a,))
    b = _group_frame(frame, (field,), (value_b,))
    a_payload = _group_payload(a, (value_a,))
    b_payload = _group_payload(b, (value_b,))
    a_payload["value"] = a_payload.pop("values")[0]  # type: ignore[index]
    b_payload["value"] = b_payload.pop("values")[0]  # type: ignore[index]
    deltas = {
        name: _metric_delta(a_payload["metrics"][name], b_payload["metrics"][name])  # type: ignore[index]
        for name in _GROUP_METRICS
    }
    limitations = sorted({"descriptive_comparison_only", *a_payload["limitations"], *b_payload["limitations"]})  # type: ignore[arg-type]
    metadata = _result_metadata(frame, "compare_groups")
    return {
        **metadata,
        "metric_definitions": {
            **metadata["metric_definitions"],  # type: ignore[arg-type]
            "comparison_delta": "A - B for numeric metrics available on both sides",
        },
        "comparison": {
            "field_id": field.field_id,
            "a": a_payload,
            "b": b_payload,
            "deltas": deltas,
        },
        "limitations": limitations,
    }


def analyze_over_time(
    frame: AnalysisFrame, *, mode: Literal["month", "halves", "rolling"], window_size: int | None = None
) -> dict[str, object]:
    """Return aggregate chronological slices from a timestamp-validated frame."""
    if not isinstance(frame, AnalysisFrame) or "trade_timestamp" not in frame.required_roles or frame.order.mode != "timestamp":
        raise ValueError("temporal analysis requires a timestamp-ordered validated frame")
    if mode not in {"month", "halves", "rolling"}:
        raise ValueError("temporal analysis mode is unsupported")
    if mode == "rolling" and (not isinstance(window_size, int) or isinstance(window_size, bool) or window_size < 1):
        raise ValueError("rolling window size must be a positive integer")
    if mode != "rolling" and window_size is not None:
        raise ValueError("only rolling analysis accepts a window size")

    timestamp_field = _temporal_timestamp_field(frame)
    filtered_timestamps = _timestamp_valid_rows(frame.filtered_data, timestamp_field)
    source_timestamps = _timestamp_valid_rows(frame.source_data, timestamp_field)
    buckets: list[tuple[str, pd.DataFrame, pd.DataFrame]] = []
    if mode == "month":
        for period in sorted({(value.year, value.month) for value in filtered_timestamps["trade_timestamp"]}):
            buckets.append(
                (
                    f"{period[0]:04d}-{period[1]:02d}",
                    _month_rows(source_timestamps, period),
                    _month_rows(filtered_timestamps, period),
                )
            )
    elif mode == "halves":
        split = (len(filtered_timestamps) + 1) // 2
        if split:
            bucket = filtered_timestamps.iloc[:split]
            buckets.append(("earlier_half", _timestamp_range(source_timestamps, bucket), bucket))
        if split < len(filtered_timestamps):
            bucket = filtered_timestamps.iloc[split:]
            buckets.append(("later_half", _timestamp_range(source_timestamps, bucket), bucket))
    elif len(filtered_timestamps):
        assert window_size is not None
        if window_size > len(filtered_timestamps):
            raise ValueError("rolling window size exceeds valid timestamp rows")
        for index in range(len(filtered_timestamps) - window_size + 1):
            bucket = filtered_timestamps.iloc[index:index + window_size]
            buckets.append((f"rolling_{index + 1}", _timestamp_range(source_timestamps, bucket), bucket))

    metadata = _result_metadata(frame, "analyze_over_time")
    returned_buckets = buckets[:_TEMPORAL_BUCKET_LIMIT] if mode == "rolling" else buckets
    omitted_buckets = len(buckets) - len(returned_buckets)
    return {
        **metadata,
        "temporal": {
            "mode": mode,
            "timestamp_field_id": frame.order.timestamp_field_id,
            "rolling_window_size": window_size if mode == "rolling" else None,
        },
        "buckets": [_temporal_bucket(frame, label, source, filtered) for label, source, filtered in returned_buckets],
        "omissions": {
            "total_buckets": len(buckets),
            "returned_buckets": len(returned_buckets),
            "omitted_buckets": omitted_buckets,
        },
        "limitations": _temporal_limitations(frame, mode, len(filtered_timestamps), omitted_buckets),
    }


def analyze_mfe_mae(frame: AnalysisFrame) -> dict[str, object]:
    """Return only unit-confirmed MFE/MAE aggregates from a validated frame."""
    if not isinstance(frame, AnalysisFrame):
        raise ValueError("MFE/MAE analysis requires a validated analysis frame")
    metadata = _result_metadata(frame, "analyze_mfe_mae")
    mfe = _mfe_mae_payload(frame, "mfe")
    mae = _mfe_mae_payload(frame, "mae")
    return {
        **metadata,
        "mfe": mfe,
        "mae": mae,
        "limitations": [f"{role}_unavailable" for role, payload in (("mfe", mfe), ("mae", mae)) if not payload["available"]],
    }


def _result_metadata(frame: AnalysisFrame, operation: str) -> dict[str, object]:
    return {
        "provenance": "USER_EMPIRICAL_EVIDENCE",
        "dataset_id": frame.dataset_id,
        "dataset_sha256": frame.dataset_sha256,
        "mapping_version_id": frame.mapping_version_id,
        "operation": operation,
        "schema_version": "1.0",
        "filters": [_filter_descriptor(item) for item in frame.filters],
        "metric_definitions": {
            "outcome_rate_denominator": "wins + losses + breakevens",
            "win_rate_interval": "Wilson 95% interval",
            "quantile_method": "linear",
            "return_unit": frame.return_unit,
            "row_order": frame.order.mode,
        },
        "counts": {
            "source_rows": frame.source_rows,
            "filtered_rows": frame.filtered_rows,
            "valid_rows": frame.valid_rows,
            "excluded_rows": frame.excluded_rows,
        },
        "exclusions": _exclusion_payload(frame.exclusions),
    }


def _grouping_fields(frame: AnalysisFrame, group_fields: Sequence[str]) -> tuple[AnalysisField, ...]:
    if not isinstance(frame, AnalysisFrame):
        raise ValueError("groups require a validated analysis frame")
    if not isinstance(group_fields, Sequence) or isinstance(group_fields, (str, bytes)) or not 1 <= len(group_fields) <= 2:
        raise ValueError("groups require one or two field IDs")
    if any(not isinstance(field_id, str) for field_id in group_fields) or len(set(group_fields)) != len(group_fields):
        raise ValueError("group field IDs must be unique strings")
    fields_by_id = {field.field_id: field for field in frame.fields}
    fields = tuple(fields_by_id.get(field_id) for field_id in group_fields)
    if any(field is None or field.value_type not in {"categorical", "boolean"} or not field.aggregate_labels_allowed for field in fields):
        raise ValueError("group field is unsupported or not approved for aggregate labels")
    return fields  # type: ignore[return-value]


def _group_keys(data: pd.DataFrame, columns: Sequence[str]) -> list[tuple[object, ...]]:
    if data.empty:
        return []
    rows = data.dropna(subset=list(columns))
    keys = {tuple(row) for row in rows.loc[:, list(columns)].itertuples(index=False, name=None)}
    return sorted(keys, key=lambda key: tuple(f"{type(value).__name__}:{value}" for value in key))


def _group_subset(data: pd.DataFrame, fields: Sequence[AnalysisField], values: Sequence[object]) -> pd.DataFrame:
    if data.empty:
        return data.copy()
    mask = pd.Series(True, index=data.index)
    for field, value in zip(fields, values, strict=True):
        mask &= data[field.column_name] == value
    return data.loc[mask].copy()


def _group_frame(frame: AnalysisFrame, fields: Sequence[AnalysisField], values: Sequence[object]) -> AnalysisFrame:
    return _subset_frame(
        frame,
        _group_subset(frame.source_data, fields, values),
        _group_subset(frame.filtered_data, fields, values),
        _group_subset(frame.data, fields, values),
    )


def _ungrouped_frame(frame: AnalysisFrame, fields: Sequence[AnalysisField]) -> AnalysisFrame:
    return _subset_frame(
        frame,
        _ungrouped_subset(frame.source_data, fields),
        _ungrouped_subset(frame.filtered_data, fields),
        _ungrouped_subset(frame.data, fields),
    )


def _subset_frame(
    frame: AnalysisFrame, source_data: pd.DataFrame, filtered_data: pd.DataFrame, data: pd.DataFrame
) -> AnalysisFrame:
    if frame.order.mode == "timestamp" and not data.empty:
        data = data.sort_values(["trade_timestamp", "source_row_ordinal"], kind="stable")
    exclusions = _frame_exclusions(filtered_data, frame)
    outcome_sequence = _group_outcome_sequence(filtered_data, frame)
    no_data_reason = "no_source_rows" if source_data.empty else "no_matching_rows" if filtered_data.empty else "no_valid_rows" if data.empty else None
    return AnalysisFrame(
        data=data,
        source_data=source_data,
        filtered_data=filtered_data,
        dataset_id=frame.dataset_id,
        dataset_sha256=frame.dataset_sha256,
        mapping_version_id=frame.mapping_version_id,
        fields=frame.fields,
        filters=frame.filters,
        outcome_sequence=outcome_sequence,
        required_roles=frame.required_roles,
        source_rows=len(source_data),
        filtered_rows=len(filtered_data),
        valid_rows=len(data),
        excluded_rows=len(filtered_data) - len(data),
        exclusions=exclusions,
        return_unit=frame.return_unit,
        order=frame.order,
        no_data_reason=no_data_reason,
    )


def _ungrouped_subset(data: pd.DataFrame, fields: Sequence[AnalysisField]) -> pd.DataFrame:
    if data.empty:
        return data.copy()
    state_columns = [_state_column(field) for field in fields]
    if all(column in data for column in state_columns):
        mask = pd.Series(False, index=data.index)
        for column in state_columns:
            mask |= data[column] != "valid"
    else:
        mask = data[[field.column_name for field in fields]].isna().any(axis=1)
    return data.loc[mask].copy()


def _group_outcome_sequence(data: pd.DataFrame, frame: AnalysisFrame) -> tuple[str | None, ...]:
    if "trade_outcome" not in frame.required_roles:
        return ()
    by_role = {field.semantic_role: field for field in frame.fields if field.semantic_role}
    ordered = data
    if frame.order.mode == "timestamp" and not ordered.empty:
        ordered = ordered.sort_values(["trade_timestamp", "source_row_ordinal"], kind="stable")
    sequence: list[str | None] = []
    for _, row in ordered.iterrows():
        if not all(row[_state_column(by_role[role])] == "valid" for role in frame.required_roles if role not in {"trade_outcome", "trade_return"}):
            continue
        outcome = row[by_role["trade_outcome"].column_name]
        outcome_valid = row[_state_column(by_role["trade_outcome"])] == "valid"
        return_valid = "trade_return" not in frame.required_roles or row[_state_column(by_role["trade_return"])] == "valid"
        sequence.append(str(outcome) if outcome_valid and return_valid else None)
    return tuple(sequence)


def _frame_exclusions(data: pd.DataFrame, frame: AnalysisFrame) -> tuple[ExclusionReason, ...]:
    by_role = {field.semantic_role: field for field in frame.fields if field.semantic_role}
    reasons: list[ExclusionReason] = []
    for role in frame.required_roles:
        states = data[_state_column(by_role[role])] if not data.empty else pd.Series(dtype="object")
        for reason in ("blank", "invalid"):
            count = int((states == reason).sum())
            if count:
                reasons.append(ExclusionReason(role, reason, count))
    return tuple(reasons)


def _group_payload(frame: AnalysisFrame, values: Sequence[object]) -> dict[str, object]:
    summary = summarize_results(frame)
    metrics = summary["metrics"]
    assert isinstance(metrics, dict)
    return {
        "values": [_json_value(value) for value in values],
        "counts": summary["counts"],
        "exclusions": summary["exclusions"],
        "metrics": {name: metrics[name] for name in _GROUP_METRICS},
        "limitations": summary["limitations"],
    }


def _group_limitations(frame: AnalysisFrame, total_groups: int, returned_groups: int) -> list[str]:
    limitations: list[str] = []
    if frame.no_data_reason == "no_matching_rows":
        limitations.append("no_matching_rows")
    elif frame.no_data_reason == "no_valid_rows":
        limitations.append("no_valid_rows")
    if total_groups > returned_groups:
        limitations.append("groups_omitted")
    return limitations


def _temporal_timestamp_field(frame: AnalysisFrame) -> AnalysisField:
    field = next((item for item in frame.fields if item.semantic_role == "trade_timestamp"), None)
    if field is None:
        raise ValueError("temporal analysis requires a confirmed timestamp field")
    return field


def _timestamp_valid_rows(data: pd.DataFrame, field: AnalysisField) -> pd.DataFrame:
    return data.loc[data[_state_column(field)] == "valid"].sort_values([field.column_name, "source_row_ordinal"], kind="stable")


def _month_rows(data: pd.DataFrame, period: tuple[int, int]) -> pd.DataFrame:
    return data.loc[data["trade_timestamp"].map(lambda value: (value.year, value.month) == period)]


def _timestamp_range(data: pd.DataFrame, bucket: pd.DataFrame) -> pd.DataFrame:
    timestamps = bucket["trade_timestamp"]
    return data.loc[(data["trade_timestamp"] >= timestamps.min()) & (data["trade_timestamp"] <= timestamps.max())]


def _temporal_bucket(frame: AnalysisFrame, period: str, source_data: pd.DataFrame, filtered_data: pd.DataFrame) -> dict[str, object]:
    ordinals = set(filtered_data["source_row_ordinal"].tolist())
    data = frame.data.loc[frame.data["source_row_ordinal"].isin(ordinals)]
    bucket = _subset_frame(frame, source_data, filtered_data, data)
    payload = _group_payload(bucket, ())
    timestamps = filtered_data["trade_timestamp"]
    return {
        "period": period,
        "start_date": _date_text(timestamps.min()),
        "end_date": _date_text(timestamps.max()),
        "counts": payload["counts"],
        "exclusions": payload["exclusions"],
        "metrics": payload["metrics"],
        "limitations": payload["limitations"],
    }


def _temporal_limitations(frame: AnalysisFrame, mode: str, timestamp_rows: int, omitted_buckets: int) -> list[str]:
    limitations = _summary_limitations(frame, {"valid_rows": timestamp_rows})
    if not timestamp_rows:
        limitations.append("no_valid_timestamp_rows")
    if mode == "rolling" and timestamp_rows == 0:
        limitations.append("no_rolling_windows")
    if omitted_buckets:
        limitations.append("temporal_buckets_omitted")
    return sorted(set(limitations))


def _date_text(value: object) -> str:
    assert isinstance(value, (datetime, pd.Timestamp))
    return value.date().isoformat()


def _mfe_mae_payload(frame: AnalysisFrame, role: Literal["mfe", "mae"]) -> dict[str, object]:
    field = next((item for item in frame.fields if item.semantic_role == role), None)
    if field is None or field.unit is None:
        return {"available": False, "reason": "missing_confirmed_mapping"}
    state_column = _state_column(field)
    data = frame.filtered_data
    valid = data.loc[data[state_column] == "valid", field.column_name].astype(float)
    exclusions = [
        {"reason": reason, "count": int((data[state_column] == reason).sum())}
        for reason in ("blank", "invalid")
        if int((data[state_column] == reason).sum())
    ]
    metrics = _distribution_metrics(valid)
    return {
        "available": True,
        "field_id": field.field_id,
        "unit": field.unit,
        "counts": {
            "source_rows": frame.source_rows,
            "filtered_rows": frame.filtered_rows,
            "valid_rows": len(valid),
            "excluded_rows": frame.filtered_rows - len(valid),
        },
        "exclusions": exclusions,
        "metrics": metrics,
    }


def _frame_counts(frame: AnalysisFrame) -> dict[str, int]:
    return {
        "source_rows": frame.source_rows,
        "filtered_rows": frame.filtered_rows,
        "valid_rows": frame.valid_rows,
        "excluded_rows": frame.excluded_rows,
    }


def _combined_counts(frames: Sequence[AnalysisFrame]) -> dict[str, int]:
    return {name: sum(_frame_counts(frame)[name] for frame in frames) for name in ("source_rows", "filtered_rows", "valid_rows", "excluded_rows")}


def _ungrouped_reasons(data: pd.DataFrame, fields: Sequence[AnalysisField]) -> list[dict[str, object]]:
    reasons: list[dict[str, object]] = []
    for field in fields:
        state_column = _state_column(field)
        for reason in ("blank", "invalid"):
            count = int((data[state_column] == reason).sum()) if state_column in data else 0
            if count:
                reasons.append({"field_id": field.field_id, "reason": reason, "count": count})
    return reasons


def _validate_comparison_value(frame: AnalysisFrame, field: AnalysisField, value: object) -> None:
    if not _filter_value_matches(field.value_type, value):
        raise ValueError("comparison values are incompatible with the group field")
    if isinstance(value, str) and len(value) > 80:
        raise ValueError("comparison value exceeds the approved mapped field label limit")
    source_values = _group_keys(frame.source_data, (field.column_name,))
    approved_values = {key[0] for key in source_values}
    if len(approved_values) > 20 or value not in approved_values:
        raise ValueError("comparison value is absent from the approved mapped field domain")
    if _group_subset(frame.filtered_data, (field,), (value,)).empty:
        raise ValueError("comparison value has no matching rows after validated filters")


def _metric_delta(a: object, b: object) -> float | None:
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return float(a - b)
    return None


def _json_value(value: object) -> object:
    return value.item() if hasattr(value, "item") else value


def _exclusion_payload(exclusions: Sequence[ExclusionReason]) -> list[dict[str, object]]:
    return [{"role": item.role, "reason": item.reason, "count": item.count} for item in exclusions]


def _outcome_metrics(
    outcomes: Sequence[object], ordered_outcomes: Sequence[object] | None = None
) -> dict[str, float | int | None]:
    streak_outcomes = outcomes if ordered_outcomes is None else ordered_outcomes
    wins = sum(outcome == "win" for outcome in outcomes)
    losses = sum(outcome == "loss" for outcome in outcomes)
    breakevens = sum(outcome == "breakeven" for outcome in outcomes)
    denominator = wins + losses + breakevens
    wilson_lower, wilson_upper = _wilson_95(wins, denominator)
    return {
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate": wins / denominator if denominator else None,
        "loss_rate": losses / denominator if denominator else None,
        "wilson_95_lower": wilson_lower,
        "wilson_95_upper": wilson_upper,
        "max_consecutive_wins": _longest_streak(streak_outcomes, "win"),
        "max_consecutive_losses": _longest_streak(streak_outcomes, "loss"),
    }


def _wilson_95(successes: int, total: int) -> tuple[float | None, float | None]:
    if not total:
        return None, None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return centre - margin, centre + margin


def _return_metrics(values: Sequence[float], frame: AnalysisFrame, has_outcome: bool) -> dict[str, float | int | None]:
    series = pd.Series(values, dtype="float64")
    result: dict[str, float | int | None] = {
        "total_return": float(series.sum()),
        "mean_return": float(series.mean()),
        "median_return": float(series.median()),
        "best_return": float(series.max()),
        "worst_return": float(series.min()),
        "minimum": float(series.min()),
        "maximum": float(series.max()),
        "sample_standard_deviation": float(series.std(ddof=1)) if len(series) >= 2 else None,
        "first_quartile": _quantile(series, 0.25),
        "third_quartile": _quantile(series, 0.75),
        "percentile_05": _quantile(series, 0.05),
        "percentile_10": _quantile(series, 0.10),
        "percentile_25": _quantile(series, 0.25),
        "percentile_50": _quantile(series, 0.50),
        "percentile_75": _quantile(series, 0.75),
        "percentile_90": _quantile(series, 0.90),
        "percentile_95": _quantile(series, 0.95),
    }
    result["interquartile_range"] = result["third_quartile"] - result["first_quartile"]  # type: ignore[operator]
    result["iqr_outlier_count"] = _iqr_outlier_count(series) if len(series) >= 4 else None
    if has_outcome:
        paired = frame.data[["trade_return", "trade_outcome"]].dropna()
        winners = paired.loc[paired["trade_outcome"] == "win", "trade_return"]
        losers = paired.loc[paired["trade_outcome"] == "loss", "trade_return"]
        result["mean_winning_return"] = float(winners.mean()) if not winners.empty else None
        result["mean_losing_return"] = float(losers.mean()) if not losers.empty else None
    return result


def _distribution_metrics(values: pd.Series) -> dict[str, float | None]:
    if values.empty:
        return {
            "mean": None,
            "median": None,
            "minimum": None,
            "maximum": None,
            "sample_standard_deviation": None,
            "percentile_05": None,
            "percentile_25": None,
            "percentile_75": None,
            "percentile_95": None,
        }
    return {
        "mean": float(values.mean()),
        "median": float(values.median()),
        "minimum": float(values.min()),
        "maximum": float(values.max()),
        "sample_standard_deviation": float(values.std(ddof=1)) if len(values) >= 2 else None,
        "percentile_05": _quantile(values, 0.05),
        "percentile_25": _quantile(values, 0.25),
        "percentile_75": _quantile(values, 0.75),
        "percentile_95": _quantile(values, 0.95),
    }


def _r_metrics(values: Sequence[float], frame: AnalysisFrame | None) -> dict[str, float | int | None]:
    equity = pd.concat([pd.Series([0.0]), pd.Series(values, dtype="float64").cumsum()], ignore_index=True)
    peaks = equity.cummax()
    drawdowns = equity - peaks
    trough_index = int(drawdowns.idxmin())
    max_drawdown = float(-drawdowns.iloc[trough_index])
    peak = float(peaks.iloc[trough_index])
    recovery = (
        next((index - trough_index for index in range(trough_index + 1, len(equity)) if equity.iloc[index] >= peak), None)
        if max_drawdown > 0
        else None
    )
    realized_reward_risk = None
    if frame is not None:
        paired = frame.data[["trade_return", "trade_outcome"]].dropna()
        winners = paired.loc[paired["trade_outcome"] == "win", "trade_return"]
        losers = paired.loc[paired["trade_outcome"] == "loss", "trade_return"]
        if not winners.empty and not losers.empty and losers.mean() != 0:
            realized_reward_risk = float(winners.mean() / abs(losers.mean()))
    return {
        "realized_reward_risk": realized_reward_risk,
        "cumulative_return": float(equity.iloc[-1]),
        "max_drawdown": max_drawdown,
        "recovery_observations": recovery,
    }


def _longest_streak(outcomes: Sequence[object], target: str) -> int:
    longest = current = 0
    for outcome in outcomes:
        current = current + 1 if outcome == target else 0
        longest = max(longest, current)
    return longest


def _quantile(series: pd.Series, quantile: float) -> float:
    return float(series.quantile(quantile, interpolation="linear"))


def _iqr_outlier_count(series: pd.Series) -> int:
    first, third = _quantile(series, 0.25), _quantile(series, 0.75)
    iqr = third - first
    return int(((series < first - 1.5 * iqr) | (series > third + 1.5 * iqr)).sum())


def _summary_limitations(frame: AnalysisFrame, metrics: dict[str, object]) -> list[str]:
    limitations: list[str] = []
    if frame.valid_rows < 30:
        limitations.append("small_sample")
    if any(item.reason == "invalid" for item in frame.exclusions):
        limitations.append("invalid_values_excluded")
    if frame.no_data_reason == "no_matching_rows":
        limitations.append("no_matching_rows")
    if any(value is None for value in metrics.values()):
        limitations.append("unavailable_metric")
    return limitations


def _filter_descriptor(filter_: AnalysisFilter) -> dict[str, str]:
    value = filter_.value
    encoded = json.dumps(value, default=lambda item: item.isoformat() if isinstance(item, datetime) else None, sort_keys=True, separators=(",", ":"))
    return {
        "field_id": filter_.field_id,
        "operator": filter_.operator,
        "value_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _field(entry: "MappingEntry") -> AnalysisField:
    return AnalysisField(
        entry.field_id or "",
        entry.semantic_role or entry.field_id or "",
        entry.value_type or "unknown",
        entry.semantic_role,
        entry.unit,
        entry.aggregate_labels_allowed,
    )


def _frame_data(rows: Sequence[dict[str, object]], fields: Sequence[AnalysisField], *, include_states: bool = False) -> pd.DataFrame:
    columns = ["source_row_ordinal", *(field.column_name for field in fields)]
    if include_states:
        columns.extend(_state_column(field) for field in fields)
    records = []
    for row in rows:
        values = row["values"]
        states = row["states"]
        assert isinstance(values, dict) and isinstance(states, dict)
        record = {"source_row_ordinal": row["source_row_ordinal"], **{field.column_name: values[field.field_id] for field in fields}}
        if include_states:
            record.update({_state_column(field): states[field.field_id] for field in fields})
        records.append(record)
    data = pd.DataFrame(records, columns=columns)
    if not data.empty:
        data["source_row_ordinal"] = data["source_row_ordinal"].astype("int64")
    return data


def _state_column(field: AnalysisField) -> str:
    return f"__state__{field.field_id}"


def _typed_row(ordinal: int, source: list[object], entries: Sequence["MappingEntry"]) -> dict[str, object]:
    values: dict[str, object | None] = {}
    states: dict[str, str] = {}
    for entry in entries:
        value, state = _typed_value(entry, source[entry.column_ordinal])
        values[entry.field_id or ""] = value
        states[entry.field_id or ""] = state
    return {"source_row_ordinal": ordinal, "values": values, "states": states}


def _typed_value(entry: "MappingEntry", raw: object) -> tuple[object | None, Literal["valid", "blank", "invalid"]]:
    if raw is None or isinstance(raw, str) and not raw.strip():
        return None, "blank"
    role = entry.semantic_role
    if role in {"trade_return", "mfe", "mae"} or entry.value_type == "number":
        return _number(raw)
    if role == "trade_timestamp" or entry.value_type == "datetime":
        return _datetime(raw)
    if role == "trade_outcome":
        value = _OUTCOMES.get(str(raw).strip().casefold())
        return (value, "valid") if value is not None else (None, "invalid")
    if entry.value_type == "boolean":
        if isinstance(raw, bool):
            return raw, "valid"
        value = {"true": True, "yes": True, "y": True, "false": False, "no": False, "n": False}.get(str(raw).strip().casefold())
        return (value, "valid") if value is not None else (None, "invalid")
    if entry.value_type == "categorical":
        return str(raw).strip(), "valid"
    return None, "invalid"


def _number(raw: object) -> tuple[float | None, Literal["valid", "invalid"]]:
    if isinstance(raw, bool):
        return None, "invalid"
    try:
        text = raw.strip() if isinstance(raw, str) else raw
        value = float(text[:-1]) / 100 if isinstance(text, str) and text.endswith("%") else float(text)
    except (TypeError, ValueError):
        return None, "invalid"
    return (value, "valid") if math.isfinite(value) else (None, "invalid")


def _datetime(raw: object) -> tuple[datetime | None, Literal["valid", "invalid"]]:
    if isinstance(raw, datetime):
        return raw, "valid"
    if isinstance(raw, date):
        return datetime.combine(raw, time.min), "valid"
    if not isinstance(raw, str) or "/" in raw:
        return None, "invalid"
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")), "valid"
    except ValueError:
        return None, "invalid"


def _validate_filter(filter_: AnalysisFilter, fields: dict[str, AnalysisField]) -> AnalysisFilter:
    if not isinstance(filter_, AnalysisFilter) or filter_.field_id not in fields or filter_.operator not in _FILTER_OPERATORS:
        raise ValueError("filter field or operator is unsupported")
    field = fields[filter_.field_id]
    allowed = {"eq", "neq", "in", "not_in", "is_blank", "not_blank"}
    if field.value_type in {"number", "datetime"}:
        allowed |= {"gt", "gte", "lt", "lte", "between"}
    if filter_.operator not in allowed:
        raise ValueError("filter operator is incompatible with the field type")
    if filter_.operator in {"is_blank", "not_blank"}:
        if filter_.value is not None:
            raise ValueError("filter blank operators do not accept a value")
        return filter_
    values = filter_.value if filter_.operator in {"in", "not_in", "between"} else (filter_.value,)
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError("filter values are incompatible with the field type")
    if filter_.operator == "between" and len(values) != 2 or filter_.operator != "between" and not values:
        raise ValueError("filter values are invalid")
    if any(not _filter_value_matches(field.value_type, value) for value in values):
        raise ValueError("filter values are incompatible with the field type")
    if filter_.operator == "between" and field.value_type == "datetime" and _mixed_timezone(values):
        raise ValueError("filter timestamp timezones are incompatible")
    if filter_.operator == "between" and values[0] > values[1]:
        raise ValueError("filter range is invalid")
    return filter_


def _filter_value_matches(value_type: str, value: object) -> bool:
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    if value_type == "datetime":
        return isinstance(value, datetime)
    if value_type == "boolean":
        return isinstance(value, bool)
    return value_type == "categorical" and isinstance(value, str)


def _validate_datetime_filter_timezones(
    rows: Sequence[dict[str, object]], filters: Sequence[AnalysisFilter], fields: dict[str, AnalysisField]
) -> None:
    for filter_ in filters:
        if fields[filter_.field_id].value_type != "datetime" or filter_.operator in {"is_blank", "not_blank"}:
            continue
        filter_values = filter_.value if filter_.operator in {"in", "not_in", "between"} else (filter_.value,)
        values = [row["values"][filter_.field_id] for row in rows if row["states"][filter_.field_id] == "valid"]  # type: ignore[index]
        if _mixed_timezone(values) or _mixed_timezone(filter_values):
            raise ValueError("filter timestamp timezones are incompatible")
        if values and filter_values and _timezone_aware(values[0]) != _timezone_aware(filter_values[0]):
            raise ValueError("filter timestamp timezone does not match dataset timestamps")


def _mixed_timezone(values: Sequence[object]) -> bool:
    return len({_timezone_aware(value) for value in values if isinstance(value, datetime)}) > 1


def _timezone_aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _matches_filters(row: dict[str, object], filters: Sequence[AnalysisFilter], fields: dict[str, AnalysisField]) -> bool:
    values = row["values"]
    states = row["states"]
    assert isinstance(values, dict) and isinstance(states, dict)
    for filter_ in filters:
        state = states[filter_.field_id]
        if filter_.operator == "is_blank":
            matched = state == "blank"
        elif filter_.operator == "not_blank":
            matched = state == "valid"
        elif state != "valid":
            matched = False
        else:
            value = values[filter_.field_id]
            if filter_.operator == "eq": matched = value == filter_.value
            elif filter_.operator == "neq": matched = value != filter_.value
            elif filter_.operator == "in": matched = value in filter_.value  # type: ignore[operator]
            elif filter_.operator == "not_in": matched = value not in filter_.value  # type: ignore[operator]
            elif filter_.operator == "gt": matched = value > filter_.value  # type: ignore[operator]
            elif filter_.operator == "gte": matched = value >= filter_.value  # type: ignore[operator]
            elif filter_.operator == "lt": matched = value < filter_.value  # type: ignore[operator]
            elif filter_.operator == "lte": matched = value <= filter_.value  # type: ignore[operator]
            else: matched = filter_.value[0] <= value <= filter_.value[1]  # type: ignore[index,operator]
        if not matched:
            return False
    return True


def _exclusions(rows: Sequence[dict[str, object]], roles: dict[str | None, "MappingEntry"], required: Sequence[str]) -> tuple[ExclusionReason, ...]:
    counts: dict[tuple[str, Literal["blank", "invalid"]], int] = {}
    for row in rows:
        states = row["states"]
        assert isinstance(states, dict)
        for role in required:
            state = states[roles[role].field_id or ""]
            if state in {"blank", "invalid"}:
                key = (role, state)
                counts[key] = counts.get(key, 0) + 1
    return tuple(ExclusionReason(role, reason, count) for (role, reason), count in sorted(counts.items()))


def _validate_timestamp_order(rows: Sequence[dict[str, object]], timestamp_field_id: str | None) -> None:
    values = [row["values"][timestamp_field_id or ""] for row in rows]  # type: ignore[index]
    aware = {value.tzinfo is not None and value.utcoffset() is not None for value in values}  # type: ignore[union-attr]
    if len(aware) > 1:
        raise ValueError("timestamp order requires compatible timezone data")
