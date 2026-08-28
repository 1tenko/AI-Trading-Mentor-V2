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
    "wins", "losses", "breakevens", "win_rate", "loss_rate", "max_consecutive_wins", "max_consecutive_losses",
    "total_return", "mean_return", "median_return", "mean_winning_return", "mean_losing_return", "best_return",
    "worst_return", "realized_reward_risk", "cumulative_return", "max_drawdown", "recovery_observations",
    "minimum", "maximum", "sample_standard_deviation", "first_quartile", "third_quartile", "interquartile_range",
    "iqr_outlier_count", "percentile_05", "percentile_10", "percentile_25", "percentile_50", "percentile_75",
    "percentile_90", "percentile_95", "valid_rows", "excluded_rows",
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
    fields: tuple[AnalysisField, ...]
    filters: tuple[AnalysisFilter, ...] = field(repr=False)
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
    if order_by == "timestamp":
        timestamp_id = role_entries["trade_timestamp"].field_id
        _validate_timestamp_order(valid, timestamp_id)
        valid.sort(key=lambda row: (row["values"][timestamp_id], row["source_row_ordinal"]))
        order = AnalysisOrder("timestamp", timestamp_id)
    else:
        order = AnalysisOrder("source")

    data = pd.DataFrame(
        [{"source_row_ordinal": row["source_row_ordinal"], **{field.column_name: row["values"][field.field_id] for field in fields}} for row in valid],
        columns=["source_row_ordinal", *(field.column_name for field in fields)],
    )
    if not data.empty:
        data["source_row_ordinal"] = data["source_row_ordinal"].astype("int64")
    no_data_reason = "no_source_rows" if not rows else "no_matching_rows" if not filtered else "no_valid_rows" if not valid else None
    return AnalysisFrame(
        data=data,
        fields=fields,
        filters=validated_filters,
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
    *,
    dataset_id: str,
    dataset_sha256: str,
    mapping_version_id: int,
) -> dict[str, object]:
    """Calculate the bounded core summary from an already validated frame."""
    if not isinstance(frame, AnalysisFrame):
        raise ValueError("summary requires a validated analysis frame")
    if not isinstance(dataset_id, str) or not dataset_id or not isinstance(dataset_sha256, str) or len(dataset_sha256) != 64:
        raise ValueError("summary provenance is invalid")
    if type(mapping_version_id) is not int or mapping_version_id < 1:
        raise ValueError("summary provenance is invalid")

    has_return = any(field.semantic_role == "trade_return" for field in frame.fields)
    has_outcome = any(field.semantic_role == "trade_outcome" for field in frame.fields)
    returns = frame.data["trade_return"].dropna().astype(float).tolist() if has_return else []
    outcomes = frame.data["trade_outcome"].dropna().tolist() if has_outcome else []
    metrics = {name: None for name in _SUMMARY_METRICS}
    metrics["valid_rows"] = frame.valid_rows
    metrics["excluded_rows"] = frame.excluded_rows

    if has_outcome:
        metrics.update(_outcome_metrics(outcomes))
    if has_return and returns:
        metrics.update(_return_metrics(returns, frame, has_outcome))
    if frame.return_unit == "R" and has_return and returns:
        metrics.update(_r_metrics(returns, frame if has_outcome else None))

    limitations = _summary_limitations(frame, metrics)
    return {
        "provenance": "USER_EMPIRICAL_EVIDENCE",
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256,
        "mapping_version_id": mapping_version_id,
        "operation": "summarize_results",
        "schema_version": "1.0",
        "filters": [_filter_descriptor(item) for item in frame.filters],
        "metric_definitions": {
            "outcome_rate_denominator": "wins + losses + breakevens",
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
        "exclusions": [
            {"role": item.role, "reason": item.reason, "count": item.count} for item in frame.exclusions
        ],
        "metrics": metrics,
        "limitations": limitations,
    }


def _outcome_metrics(outcomes: Sequence[object]) -> dict[str, float | int | None]:
    wins = sum(outcome == "win" for outcome in outcomes)
    losses = sum(outcome == "loss" for outcome in outcomes)
    breakevens = sum(outcome == "breakeven" for outcome in outcomes)
    denominator = wins + losses + breakevens
    return {
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate": wins / denominator if denominator else None,
        "loss_rate": losses / denominator if denominator else None,
        "max_consecutive_wins": _longest_streak(outcomes, "win"),
        "max_consecutive_losses": _longest_streak(outcomes, "loss"),
    }


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
        if not winners.empty and not losers.empty:
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
    return AnalysisField(entry.field_id or "", entry.semantic_role or entry.field_id or "", entry.value_type or "unknown", entry.semantic_role)


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
