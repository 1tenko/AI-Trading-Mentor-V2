"""Local, validated input boundary for deterministic backtest analysis."""

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from numbers import Integral, Real
from typing import TYPE_CHECKING, Literal, Sequence

import pandas as pd

from mentor.datasets import DatasetImportError, _inspection_rows_from_bytes
from mentor.storage import ANALYSIS_EXCLUSION_LIMIT, ANALYSIS_FILTER_LIMIT, validate_completed_evidence_envelope

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
_FILTER_LIMIT = ANALYSIS_FILTER_LIMIT
_TEXT_FIELD_LIMIT = 3
_TEXT_ROW_LIMIT = 100
_TEXT_CELL_LIMIT = 1_200
_TEXT_CHARACTER_LIMIT = 24_000
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


class AnalysisLimitError(ValueError):
    """A bounded analysis cannot faithfully represent the requested result."""

    def __init__(self, code: str, metadata: dict[str, object]):
        self.code = code
        self.metadata = metadata
        super().__init__(code)


class AnalysisNumericError(ValueError):
    """A required deterministic metric cannot be represented safely."""

    code = "numeric_overflow"

    def __init__(self):
        super().__init__(self.code)


@dataclass
class TextEvidenceUseGuard:
    """Caller-owned per-turn guard; this boundary consumes it once."""

    used: bool = False

    def consume(self) -> None:
        if self.used:
            raise ValueError("qualitative evidence is limited to one call per turn")
        self.used = True


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
    kind: Literal["filter_invalid", "required_role_diagnostic"]
    reason: Literal["blank", "invalid"]
    count: int
    role: str | None = None
    filter_position: int | None = None
    field_id: str | None = None
    operator: str | None = None
    canonical_id: str | None = None


@dataclass(frozen=True)
class FilterDescriptor:
    position: int
    field_id: str
    operator: str
    value_spec: dict[str, object]
    canonical_id: str


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
    filter_descriptors: tuple[FilterDescriptor, ...]
    outcome_sequence: tuple[str | None, ...] = field(repr=False)
    required_roles: tuple[str, ...]
    source_rows: int
    filtered_rows: int
    valid_rows: int
    excluded_rows: int
    disposition_counts: dict[str, int]
    exclusions: tuple[ExclusionReason, ...]
    return_unit: str | None
    order: AnalysisOrder
    no_data_reason: Literal["no_source_rows", "no_matching_rows", "no_valid_rows"] | None


@dataclass(frozen=True)
class GroupEvidenceGroup:
    key: tuple[object, ...]
    frame: AnalysisFrame = field(repr=False, compare=False)

    def payload(self) -> dict[str, object]:
        summary = summarize_results(self.frame)
        metrics = summary["metrics"]
        assert isinstance(metrics, dict)
        counts = _partition_counts((self.frame,))
        return {
            "key": [_json_value(value) for value in self.key],
            **counts,
            "metrics": {name: metrics[name] for name in _GROUP_METRICS},
            "limitations": sorted({*summary["limitations"], *(["no_valid_rows"] if self.frame.filtered_rows and not self.frame.valid_rows else [])}),
        }


@dataclass(frozen=True)
class GroupEvidencePartition:
    """The sole grouped population accounting boundary before serialization."""

    frame: AnalysisFrame = field(repr=False, compare=False)
    returned_groups: tuple[GroupEvidenceGroup, ...]
    omitted_groups: tuple[AnalysisFrame, ...] = field(repr=False, compare=False)
    ungrouped: AnalysisFrame = field(repr=False, compare=False)
    ungrouped_reasons: tuple[dict[str, object], ...]

    def __post_init__(self) -> None:
        keys = tuple(group.key for group in self.returned_groups)
        populations = tuple(group.frame for group in self.returned_groups) + self.omitted_groups + (self.ungrouped,)
        ordinals = [ordinal for population in populations for ordinal in population.filtered_data["source_row_ordinal"].tolist()]
        expected = self.frame.filtered_data["source_row_ordinal"].tolist()
        if (
            len(keys) != len(set(keys))
            or any(not group.frame.filtered_rows for group in self.returned_groups)
            or any(not population.filtered_rows for population in self.omitted_groups)
            or len(ordinals) != len(set(ordinals))
            or set(ordinals) != set(expected)
            or _partition_counts(populations) != _partition_counts((self.frame,))
        ):
            raise ValueError("group evidence partition is invalid")

    def payload(self) -> dict[str, object]:
        return {
            "returned_groups": [group.payload() for group in self.returned_groups],
            "omitted": {"group_count": len(self.omitted_groups), **_partition_counts(self.omitted_groups)},
            "ungrouped": {**_partition_counts((self.ungrouped,)), "reasons": list(self.ungrouped_reasons)},
        }


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
    if any(not isinstance(role, str) for role in required) or len(set(required)) != len(required):
        raise ValueError("required_roles must be a unique sequence")
    if order_by not in _ORDER_MODES:
        raise ValueError("analysis order is unsupported")
    if len(filters) > _FILTER_LIMIT:
        raise ValueError(f"filter limit is {_FILTER_LIMIT}")

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
    filter_descriptors = _canonical_filter_descriptors(validated_filters, by_id, mapping.id)
    filters_by_id = {
        _canonical_filter_descriptors((filter_,), by_id, mapping.id)[0].canonical_id: filter_
        for filter_ in validated_filters
    }
    validated_filters = tuple(filters_by_id[item.canonical_id] for item in filter_descriptors)
    if len(filter_descriptors) + 2 * len(effective_required) > ANALYSIS_EXCLUSION_LIMIT:
        raise ValueError("analysis exclusion detail limit would be exceeded")
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
    for row in rows:
        filter_state, invalid_positions = _evaluate_filters(row, validated_filters, by_id)
        row["filter_state"] = filter_state
        row["filter_invalid_positions"] = invalid_positions
        row["row_disposition"] = _row_disposition(row, effective_required, role_entries)
    filtered = [row for row in rows if row["filter_state"] == "match"]
    valid = [
        row
        for row in filtered
        if row["row_disposition"] == "valid_for_analysis"
    ]
    exclusions = _exclusions(rows, role_entries, effective_required, filter_descriptors)
    outcome_rows = [
        row
        for row in filtered
        if row["filter_state"] == "match"
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
    data = _frame_data(valid, fields, include_states=True)
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
        filter_descriptors=filter_descriptors,
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
        excluded_rows=len(rows) - len(valid),
        disposition_counts=_disposition_counts(rows),
        exclusions=exclusions,
        return_unit=role_entries.get("trade_return").unit if "trade_return" in role_entries else None,
        order=order,
        no_data_reason=no_data_reason,
    )


def read_text_evidence(
    storage: "Storage",
    dataset_id: str,
    mapping_version_id: int,
    *,
    text_field_ids: Sequence[str],
    context_field_ids: Sequence[str] = (),
    filters: Sequence[AnalysisFilter] = (),
    order_by: Literal["source", "timestamp"] = "source",
    include_approved_notes: bool = False,
    use_guard: TextEvidenceUseGuard,
) -> dict[str, object]:
    """Return only explicitly approved, bounded local qualitative values."""
    if include_approved_notes is not True:
        raise ValueError("explicit approved-notes consent is required")
    if not isinstance(use_guard, TextEvidenceUseGuard):
        raise ValueError("qualitative evidence requires a caller-owned use guard")
    use_guard.consume()
    if (
        not _field_ids_are_bounded(text_field_ids, _TEXT_FIELD_LIMIT)
        or not _field_ids_are_bounded(context_field_ids, _TEXT_FIELD_LIMIT, required=False)
        or set(text_field_ids) & set(context_field_ids)
    ):
        raise ValueError("text and context fields must be unique bounded field IDs")

    dataset = storage.dataset(dataset_id)
    mapping = storage.mapping_version(mapping_version_id)
    if dataset is None or mapping is None or mapping.status != "confirmed" or mapping.dataset_id != dataset_id:
        raise ValueError("text evidence requires a confirmed mapping for the dataset")
    entries = {entry.field_id: entry for entry in storage.mapping_entries(mapping_version_id) if entry.field_id}
    selected_ids = (*text_field_ids, *context_field_ids)
    if any(field_id not in entries for field_id in selected_ids):
        raise ValueError("text evidence field is unsupported")
    if any(entries[field_id].mentor_access != "allow_row_values_when_analysing_notes" for field_id in selected_ids):
        raise ValueError("text evidence field is not approved")
    if any(entries[field_id].value_type != "categorical" for field_id in text_field_ids):
        raise ValueError("text evidence fields must be mapped text")
    timestamp_id = next((entry.field_id for entry in entries.values() if entry.semantic_role == "trade_timestamp"), None)
    if order_by not in _ORDER_MODES:
        raise ValueError("text evidence order is unsupported")
    if order_by == "timestamp" and (
        timestamp_id is None or entries[timestamp_id].mentor_access != "allow_row_values_when_analysing_notes"
    ):
        raise ValueError("timestamp order requires an approved timestamp field")

    frame = build_analysis_frame(
        storage, dataset_id, mapping_version_id, required_roles=(), filters=filters, order_by="source"
    )
    rows = frame.filtered_data.to_dict("records")
    if order_by == "timestamp":
        if _mixed_timezone([
            _row_value(row, entries[timestamp_id])
            for row in rows
            if row[_state_column(_field(entries[timestamp_id]))] == "valid"
        ]):
            raise ValueError("timestamp order requires compatible timezone data")
        rows.sort(
            key=lambda row: (
                0 if row[_state_column(_field(entries[timestamp_id]))] == "valid" else 1,
                _row_value(row, entries[timestamp_id]) if row[_state_column(_field(entries[timestamp_id]))] == "valid" else datetime.max,
                row["source_row_ordinal"],
            )
        )
    candidates = [
        row
        for row in rows
        if any(row[_state_column(_field(entries[field_id]))] == "valid" for field_id in text_field_ids)
    ]
    character_count = 0
    cell_truncated = False
    row_truncated = False
    items: list[dict[str, object]] = []
    for row in candidates[:_TEXT_ROW_LIMIT]:
        text, _, text_truncated, character_count = _bounded_text_values(
            row, text_field_ids, entries, character_count
        )
        cell_truncated |= text_truncated
        if not text:
            row_truncated = True
            if character_count >= _TEXT_CHARACTER_LIMIT:
                break
            continue
        context, unavailable, context_truncated, character_count = _bounded_text_values(
            row, context_field_ids, entries, character_count
        )
        cell_truncated |= context_truncated
        row_truncated |= text_truncated or context_truncated
        item: dict[str, object] = {"text": text}
        if context:
            item["context"] = context
        if unavailable:
            item["unavailable_context_field_ids"] = unavailable
        items.append(item)
    returned_rows = len(items)
    omitted_rows = len(candidates) - returned_rows
    row_truncated |= omitted_rows > 0
    complete = omitted_rows == 0 and not cell_truncated and not row_truncated
    return {
        "provenance": "USER_SUPPLIED_QUALITATIVE_DATA",
        "dataset_id": dataset.id,
        "dataset_sha256": dataset.content_sha256,
        "mapping_version_id": mapping.id,
        "operation": "read_text_evidence",
        "text_fields": [_safe_text_field(entries[field_id]) for field_id in text_field_ids],
        "context_fields": [_safe_text_field(entries[field_id]) for field_id in context_field_ids],
        "filters": [_filter_payload(item) for item in frame.filter_descriptors],
        "ordering": {"mode": order_by, "timestamp_field_id": timestamp_id if order_by == "timestamp" else None},
        "bounds": {"text_field_limit": _TEXT_FIELD_LIMIT, "context_field_limit": _TEXT_FIELD_LIMIT, "row_limit": _TEXT_ROW_LIMIT, "cell_character_limit": _TEXT_CELL_LIMIT, "character_limit": _TEXT_CHARACTER_LIMIT},
        "matching_rows": len(rows),
        "usable_text_rows": len(candidates),
        "returned_rows": returned_rows,
        "omitted_rows": omitted_rows,
        "characters_returned": character_count,
        "cell_truncated": cell_truncated,
        "row_truncated": row_truncated,
        "complete": complete,
        "items": items,
    }


def _field_ids_are_bounded(field_ids: Sequence[str], limit: int, *, required: bool = True) -> bool:
    return (
        isinstance(field_ids, Sequence)
        and not isinstance(field_ids, (str, bytes))
        and (1 if required else 0) <= len(field_ids) <= limit
        and all(isinstance(field_id, str) and field_id for field_id in field_ids)
        and len(set(field_ids)) == len(field_ids)
    )


def _safe_text_field(entry: "MappingEntry") -> dict[str, str]:
    return {"field_id": entry.field_id or "", "label": entry.analysis_label or entry.semantic_role or entry.field_id or ""}


def _row_value(row: dict[str, object], entry: "MappingEntry") -> object:
    return row[_field(entry).column_name]


def _bounded_text_values(
    row: dict[str, object],
    field_ids: Sequence[str],
    entries: dict[str, "MappingEntry"],
    character_count: int,
) -> tuple[list[dict[str, str]], list[str], bool, int]:
    values: list[dict[str, str]] = []
    unavailable: list[str] = []
    truncated = False
    for field_id in field_ids:
        entry = entries[field_id]
        if row[_state_column(_field(entry))] != "valid":
            unavailable.append(field_id)
            continue
        value = "".join(character for character in " ".join(str(_row_value(row, entry)).split()) if character.isprintable())
        if not value:
            unavailable.append(field_id)
            continue
        if len(value) > _TEXT_CELL_LIMIT:
            value = value[:_TEXT_CELL_LIMIT]
            truncated = True
        remaining = _TEXT_CHARACTER_LIMIT - character_count
        if remaining <= 0:
            truncated = True
            break
        if len(value) > remaining:
            value = value[:remaining]
            truncated = True
        character_count += len(value)
        values.append({**_safe_text_field(entry), "value": value})
    return values, unavailable, truncated, character_count


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
    return _finalize_evidence({
        **_result_metadata(frame, "summarize_results"),
        "metrics": metrics,
        "limitations": limitations,
    })


def group_results(frame: AnalysisFrame, group_fields: Sequence[str]) -> dict[str, object]:
    """Return bounded, privacy-approved descriptive metrics for one or two groups."""
    fields = _grouping_fields(frame, group_fields)
    group_columns = [field.column_name for field in fields]
    # The filtered population with valid group labels is authoritative. A
    # member with no valid metric rows remains a returned group.
    keys = _group_keys(frame.filtered_data, group_columns)
    returned_keys = keys[:_GROUP_LIMIT]
    omitted_keys = keys[_GROUP_LIMIT:]
    partition = GroupEvidencePartition(
        frame=frame,
        returned_groups=tuple(GroupEvidenceGroup(key, _group_frame(frame, fields, key)) for key in returned_keys),
        omitted_groups=tuple(_group_frame(frame, fields, key) for key in omitted_keys),
        ungrouped=_ungrouped_frame(frame, fields),
        ungrouped_reasons=tuple(_ungrouped_reasons(frame.filtered_data, fields)),
    )
    limitations = _group_limitations(frame, len(keys), len(returned_keys))
    if partition.ungrouped.filtered_rows:
        limitations.append("ungrouped_group_values_excluded")
    metadata = _result_metadata(frame, "group_results")
    metadata["counts"] = {"source_rows": frame.source_rows, **_partition_counts((frame,))}
    return _finalize_evidence({
        **metadata,
        "grouping": {
            "field_ids": [field.field_id for field in fields],
            "limit": _GROUP_LIMIT,
        },
        "group_evidence": partition.payload(),
        "limitations": limitations,
    })


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
    return _finalize_evidence({
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
    })


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
    source_timestamps = _timestamp_valid_rows(
        frame.source_data.loc[frame.source_data["__filter_state"] != "no_match"], timestamp_field
    )
    buckets: list[tuple[str, pd.DataFrame, pd.DataFrame]] = []
    if mode == "month":
        for period in sorted({(value.year, value.month) for value in source_timestamps["trade_timestamp"]}):
            bucket = _month_rows(source_timestamps, period)
            buckets.append(
                (
                    f"{period[0]:04d}-{period[1]:02d}",
                    bucket,
                    _rows_with_ordinals(frame.filtered_data, bucket),
                )
            )
    elif mode == "halves":
        split = (len(source_timestamps) + 1) // 2
        if split:
            bucket = source_timestamps.iloc[:split]
            buckets.append(("earlier_half", bucket, _rows_with_ordinals(frame.filtered_data, bucket)))
        if split < len(source_timestamps):
            bucket = source_timestamps.iloc[split:]
            buckets.append(("later_half", bucket, _rows_with_ordinals(frame.filtered_data, bucket)))
    elif len(source_timestamps):
        assert window_size is not None
        if window_size > len(source_timestamps):
            raise ValueError("rolling window size exceeds valid timestamp rows")
        for index in range(len(source_timestamps) - window_size + 1):
            bucket = source_timestamps.iloc[index:index + window_size]
            buckets.append((f"rolling_{index + 1}", bucket, _rows_with_ordinals(frame.filtered_data, bucket)))

    if len(buckets) > _TEMPORAL_BUCKET_LIMIT:
        raise AnalysisLimitError(
            "temporal_bucket_limit_exceeded",
            {"mode": mode, "total_buckets": len(buckets), "max_buckets": _TEMPORAL_BUCKET_LIMIT},
        )
    metadata = _result_metadata(frame, "analyze_over_time")
    return _finalize_evidence({
        **metadata,
        "temporal": {
            "mode": mode,
            "timestamp_field_id": frame.order.timestamp_field_id,
            "rolling_window_size": window_size if mode == "rolling" else None,
        },
        "buckets": [_temporal_bucket(frame, label, source, filtered) for label, source, filtered in buckets],
        "omissions": {
            "total_buckets": len(buckets),
            "returned_buckets": len(buckets),
            "omitted_buckets": 0,
        },
        "limitations": _temporal_limitations(frame, mode, len(source_timestamps)),
    })


def analyze_mfe_mae(frame: AnalysisFrame) -> dict[str, object]:
    """Return only unit-confirmed MFE/MAE aggregates from a validated frame."""
    if not isinstance(frame, AnalysisFrame):
        raise ValueError("MFE/MAE analysis requires a validated analysis frame")
    metadata = _result_metadata(frame, "analyze_mfe_mae")
    mfe = _mfe_mae_payload(frame, "mfe")
    mae = _mfe_mae_payload(frame, "mae")
    return _finalize_evidence({
        **metadata,
        "mfe": mfe,
        "mae": mae,
        "limitations": [f"{role}_unavailable" for role, payload in (("mfe", mfe), ("mae", mae)) if not payload["available"]],
    })


def _result_metadata(frame: AnalysisFrame, operation: str) -> dict[str, object]:
    return {
        "provenance": "USER_EMPIRICAL_EVIDENCE",
        "dataset_id": frame.dataset_id,
        "dataset_sha256": frame.dataset_sha256,
        "mapping_version_id": frame.mapping_version_id,
        "operation": operation,
        "schema_version": "1.0",
        "filters": [_filter_payload(item) for item in frame.filter_descriptors],
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
        "disposition_counts": frame.disposition_counts,
        "exclusion_contract": {
            "row_dispositions_exclusive": True,
            "diagnostic_exclusions_exclusive": False,
            "diagnostic_exclusions_may_overlap": True,
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
    exclusions = _frame_exclusions(source_data, frame)
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
        filter_descriptors=frame.filter_descriptors,
        outcome_sequence=outcome_sequence,
        required_roles=frame.required_roles,
        source_rows=len(source_data),
        filtered_rows=len(filtered_data),
        valid_rows=len(data),
        excluded_rows=len(source_data) - len(data),
        disposition_counts=_dataframe_disposition_counts(source_data),
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
    matched = data.loc[data["__filter_state"] == "match"] if not data.empty else data
    for role in frame.required_roles:
        states = matched[_state_column(by_role[role])] if not matched.empty else pd.Series(dtype="object")
        for reason in ("blank", "invalid"):
            count = int((states == reason).sum())
            if count:
                reasons.append(ExclusionReason("required_role_diagnostic", reason, count, role=role))
    invalid_positions = data.get("__filter_invalid_positions", pd.Series(dtype="object"))
    for descriptor in {item.canonical_id: item for item in frame.filter_descriptors}.values():
        positions = {item.position for item in frame.filter_descriptors if item.canonical_id == descriptor.canonical_id}
        count = sum(len(positions.intersection(row_positions)) for row_positions in invalid_positions if isinstance(row_positions, tuple))
        if count:
            reasons.append(
                ExclusionReason(
                    "filter_invalid", "invalid", count,
                    filter_position=descriptor.position,
                    field_id=descriptor.field_id,
                    operator=descriptor.operator,
                    canonical_id=descriptor.canonical_id,
                )
            )
    return tuple(reasons)


def _group_payload(frame: AnalysisFrame, values: Sequence[object]) -> dict[str, object]:
    summary = summarize_results(frame)
    metrics = summary["metrics"]
    assert isinstance(metrics, dict)
    counts = {**summary["counts"], "required_role_excluded_rows": frame.filtered_rows - frame.valid_rows}
    return {
        "values": [_json_value(value) for value in values],
        "counts": counts,
        "exclusions": summary["exclusions"],
        "metrics": {name: metrics[name] for name in _GROUP_METRICS},
        "limitations": sorted({*summary["limitations"], *(["no_valid_rows"] if frame.filtered_rows and not frame.valid_rows else [])}),
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


def _rows_with_ordinals(data: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    return data.loc[data["source_row_ordinal"].isin(rows["source_row_ordinal"])]


def _temporal_bucket(frame: AnalysisFrame, period: str, source_data: pd.DataFrame, filtered_data: pd.DataFrame) -> dict[str, object]:
    ordinals = set(filtered_data["source_row_ordinal"].tolist())
    data = frame.data.loc[frame.data["source_row_ordinal"].isin(ordinals)]
    bucket = _subset_frame(frame, source_data, filtered_data, data)
    payload = _group_payload(bucket, ())
    counts = dict(payload["counts"])
    del counts["required_role_excluded_rows"]
    timestamps = source_data["trade_timestamp"]
    return {
        "period": period,
        "start_date": _date_text(timestamps.min()),
        "end_date": _date_text(timestamps.max()),
        "counts": counts,
        "exclusions": payload["exclusions"],
        "metrics": payload["metrics"],
        "limitations": payload["limitations"],
    }


def _temporal_limitations(frame: AnalysisFrame, mode: str, timestamp_rows: int) -> list[str]:
    limitations = _summary_limitations(frame, {"valid_rows": timestamp_rows})
    if not timestamp_rows:
        limitations.append("no_valid_timestamp_rows")
    if mode == "rolling" and timestamp_rows == 0:
        limitations.append("no_rolling_windows")
    return sorted(set(limitations))


def _date_text(value: object) -> str:
    assert isinstance(value, (datetime, pd.Timestamp))
    return value.date().isoformat()


def _mfe_mae_payload(frame: AnalysisFrame, role: Literal["mfe", "mae"]) -> dict[str, object]:
    field = next((item for item in frame.fields if item.semantic_role == role), None)
    if field is None or field.unit is None:
        return {"available": False, "reason": "missing_confirmed_mapping"}
    state_column = _state_column(field)
    data = frame.data
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
            "excluded_rows": frame.source_rows - len(valid),
        },
        "exclusions": exclusions,
        "metrics": metrics,
    }


def _partition_counts(frames: Sequence[AnalysisFrame]) -> dict[str, int]:
    valid_rows = sum(frame.valid_rows for frame in frames)
    filtered_rows = sum(frame.filtered_rows for frame in frames)
    return {
        "filtered_rows": filtered_rows,
        "valid_rows": valid_rows,
        "excluded_rows": filtered_rows - valid_rows,
    }


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
    if _group_subset(frame.data, (field,), (value,)).empty:
        raise ValueError("comparison value has no eligible rows after validated filters")


def _metric_delta(a: object, b: object) -> float | None:
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return float(a - b)
    return None


def _json_value(value: object) -> object:
    return value.item() if hasattr(value, "item") else value


def _exclusion_payload(exclusions: Sequence[ExclusionReason]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for item in exclusions:
        if item.kind == "required_role_diagnostic":
            payload.append({"kind": item.kind, "role": item.role, "reason": item.reason, "count": item.count})
        else:
            payload.append({
                "kind": item.kind,
                "canonical_id": item.canonical_id,
                "reason": item.reason,
                "count": item.count,
            })
    return payload


def _finite_sum(values: Sequence[float]) -> float:
    try:
        return math.fsum(values)
    except OverflowError as error:
        raise AnalysisNumericError() from error


def _finalize_evidence(result: dict[str, object]) -> dict[str, object]:
    """Reject an empirical envelope containing an unrepresentable real metric."""
    _assert_finite_evidence(result)
    validate_completed_evidence_envelope(result)
    return result


def _assert_finite_evidence(value: object) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _assert_finite_evidence(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite_evidence(item)
    elif isinstance(value, Real) and not isinstance(value, (Integral, bool)):
        try:
            finite = math.isfinite(float(value))
        except OverflowError as error:
            raise AnalysisNumericError() from error
        if not finite:
            raise AnalysisNumericError()


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
    total = _finite_sum(values)
    result: dict[str, float | int | None] = {
        "total_return": total,
        "mean_return": total / len(values),
        "median_return": _quantile(series, 0.50),
        "best_return": float(series.max()),
        "worst_return": float(series.min()),
        "minimum": float(series.min()),
        "maximum": float(series.max()),
        "sample_standard_deviation": _sample_standard_deviation(values),
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
        result["mean_winning_return"] = _finite_sum(winners.tolist()) / len(winners) if not winners.empty else None
        result["mean_losing_return"] = _finite_sum(losers.tolist()) / len(losers) if not losers.empty else None
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
        "mean": _finite_sum(values.tolist()) / len(values),
        "median": _quantile(values, 0.50),
        "minimum": float(values.min()),
        "maximum": float(values.max()),
        "sample_standard_deviation": _sample_standard_deviation(values.tolist()),
        "percentile_05": _quantile(values, 0.05),
        "percentile_25": _quantile(values, 0.25),
        "percentile_75": _quantile(values, 0.75),
        "percentile_95": _quantile(values, 0.95),
    }


def _r_metrics(values: Sequence[float], frame: AnalysisFrame | None) -> dict[str, float | int | None]:
    equity_values = [0.0]
    for value in values:
        equity_values.append(_finite_sum((equity_values[-1], value)))
    equity = pd.Series(equity_values, dtype="float64")
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
            winner_mean = _finite_sum(winners.tolist()) / len(winners)
            loser_mean = _finite_sum(losers.tolist()) / len(losers)
            if loser_mean != 0:
                realized_reward_risk = float(winner_mean / abs(loser_mean))
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


def _sample_standard_deviation(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = _finite_sum(values) / len(values)
    scale = max(abs(value) for value in values)
    if scale == 0:
        return 0.0
    scaled_squares = [((value - mean) / scale) ** 2 for value in values]
    return scale * math.sqrt(_finite_sum(scaled_squares) / (len(values) - 1))


def _quantile(series: pd.Series, quantile: float) -> float:
    values = sorted(float(value) for value in series.tolist())
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return _finite_sum((values[lower] * (1 - fraction), values[upper] * fraction))


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


def _canonical_filter_descriptors(
    filters: Sequence[AnalysisFilter], fields: dict[str, AnalysisField], mapping_version_id: int
) -> tuple[FilterDescriptor, ...]:
    """Exact duplicate predicates are deterministically deduplicated before evaluation."""
    descriptors: dict[str, tuple[str, str, dict[str, object]]] = {}
    for filter_ in filters:
        field = fields[filter_.field_id]
        values = () if filter_.operator in {"is_blank", "not_blank"} else (
            filter_.value if filter_.operator in {"in", "not_in", "between"} else (filter_.value,)
        )
        canonical_values = [_filter_json_value(value) for value in values]
        if filter_.operator in {"in", "not_in"}:
            canonical_values.sort(key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))
        value_spec = {
            "mapping_version_id": mapping_version_id,
            "value_type": field.value_type,
            "unit": field.unit,
            "values": canonical_values,
        }
        encoded = json.dumps(
            {"field_id": field.field_id, "operator": filter_.operator, "value_spec": value_spec},
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        descriptors[hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]] = (field.field_id, filter_.operator, value_spec)
    return tuple(
        FilterDescriptor(position, field_id, operator, value_spec, canonical_id)
        for position, (canonical_id, (field_id, operator, value_spec)) in enumerate(sorted(descriptors.items()))
    )


def _filter_json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return value


def _filter_payload(filter_: FilterDescriptor) -> dict[str, object]:
    return {
        "field_id": filter_.field_id,
        "operator": filter_.operator,
        "value_spec": filter_.value_spec,
        "canonical_id": filter_.canonical_id,
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
        columns.extend((*(_state_column(field) for field in fields), "__filter_state", "__filter_invalid_positions", "__row_disposition"))
    records = []
    for row in rows:
        values = row["values"]
        states = row["states"]
        assert isinstance(values, dict) and isinstance(states, dict)
        record = {"source_row_ordinal": row["source_row_ordinal"], **{field.column_name: values[field.field_id] for field in fields}}
        if include_states:
            record.update({_state_column(field): states[field.field_id] for field in fields})
            record["__filter_state"] = row.get("filter_state", "match")
            record["__filter_invalid_positions"] = row.get("filter_invalid_positions", ())
            record["__row_disposition"] = row.get("row_disposition", "valid_for_analysis")
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


def _evaluate_filters(
    row: dict[str, object], filters: Sequence[AnalysisFilter], fields: dict[str, AnalysisField]
) -> tuple[Literal["match", "no_match", "invalid"], tuple[int, ...]]:
    values = row["values"]
    states = row["states"]
    assert isinstance(values, dict) and isinstance(states, dict)
    invalid_positions: list[int] = []
    all_matched = True
    for position, filter_ in enumerate(filters):
        state = states[filter_.field_id]
        if filter_.operator == "is_blank":
            if state == "invalid":
                invalid_positions.append(position)
                continue
            matched = state == "blank"
        elif filter_.operator == "not_blank":
            if state == "invalid":
                invalid_positions.append(position)
                continue
            matched = state == "valid"
        elif state == "blank":
            matched = False
        elif state == "invalid":
            invalid_positions.append(position)
            continue
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
            all_matched = False
    if invalid_positions:
        return "invalid", tuple(invalid_positions)
    return ("match" if all_matched else "no_match"), ()


def _row_disposition(
    row: dict[str, object], required: Sequence[str], roles: dict[str | None, "MappingEntry"]
) -> Literal["valid_for_analysis", "filtered_out", "filter_invalid", "required_role_blank", "required_role_invalid"]:
    filter_state = row["filter_state"]
    if filter_state == "invalid":
        return "filter_invalid"
    if filter_state == "no_match":
        return "filtered_out"
    states = row["states"]
    assert isinstance(states, dict)
    required_states = [states[roles[role].field_id or ""] for role in required]
    if "invalid" in required_states:
        return "required_role_invalid"
    if "blank" in required_states:
        return "required_role_blank"
    return "valid_for_analysis"


def _disposition_counts(rows: Sequence[dict[str, object]]) -> dict[str, int]:
    names = ("valid_for_analysis", "filtered_out", "filter_invalid", "required_role_blank", "required_role_invalid")
    return {name: sum(row.get("row_disposition") == name for row in rows) for name in names}


def _dataframe_disposition_counts(data: pd.DataFrame) -> dict[str, int]:
    names = ("valid_for_analysis", "filtered_out", "filter_invalid", "required_role_blank", "required_role_invalid")
    states = data.get("__row_disposition", pd.Series(dtype="object"))
    return {name: int((states == name).sum()) for name in names}


def _exclusions(
    rows: Sequence[dict[str, object]],
    roles: dict[str | None, "MappingEntry"],
    required: Sequence[str],
    descriptors: Sequence[FilterDescriptor],
) -> tuple[ExclusionReason, ...]:
    reasons: list[ExclusionReason] = []
    for role in required:
        states = [
            row["states"][roles[role].field_id or ""]
            for row in rows
            if row["filter_state"] == "match"
        ]
        for reason in ("blank", "invalid"):
            count = states.count(reason)
            if count:
                reasons.append(ExclusionReason("required_role_diagnostic", reason, count, role=role))
    for descriptor in {item.canonical_id: item for item in descriptors}.values():
        filter_positions = {item.position for item in descriptors if item.canonical_id == descriptor.canonical_id}
        count = sum(
            len(filter_positions.intersection(positions))
            for positions in (row.get("filter_invalid_positions", ()) for row in rows)
            if isinstance(positions, tuple)
        )
        if count:
            reasons.append(
                ExclusionReason(
                    "filter_invalid", "invalid", count,
                    filter_position=descriptor.position,
                    field_id=descriptor.field_id,
                    operator=descriptor.operator,
                    canonical_id=descriptor.canonical_id,
                )
            )
    return tuple(reasons)


def _validate_timestamp_order(rows: Sequence[dict[str, object]], timestamp_field_id: str | None) -> None:
    values = [row["values"][timestamp_field_id or ""] for row in rows]  # type: ignore[index]
    aware = {value.tzinfo is not None and value.utcoffset() is not None for value in values}  # type: ignore[union-attr]
    if len(aware) > 1:
        raise ValueError("timestamp order requires compatible timezone data")
