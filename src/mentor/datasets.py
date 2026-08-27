"""Immutable metadata models for local backtest datasets."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    id: str
    original_name: str
    content_sha256: str
    original_extension: str
    byte_size: int
    source_row_count: int
    status: str
    import_spec_id: int


@dataclass(frozen=True)
class DatasetImportSpec:
    header_row: int
    csv_encoding: str | None = None
    csv_delimiter: str | None = None
    csv_quoting: str | None = None
    parser_version: str | None = None
    row_order_policy: str | None = None
    time_parse_policy: str | None = None
    selected_sheet: str | None = None


@dataclass(frozen=True)
class DatasetColumn:
    ordinal: int
    original_header: str
    inferred_type: str
    null_count: int
    invalid_count: int


@dataclass(frozen=True)
class DatasetMappingVersion:
    id: int
    dataset_id: str
    version: int
    status: str


@dataclass(frozen=True)
class MappingEntry:
    column_ordinal: int
    semantic_role: str | None = None
    unit: str | None = None
    analysis_label: str | None = None
    source: str = "manual"


@dataclass(frozen=True)
class AnalysisEvidence:
    id: int
    thread_id: int
    origin_turn_number: int
    dataset_id: str
    dataset_sha256: str
    import_spec_id: int
    mapping_version_id: int
    operation: str
    schema_version: str


@dataclass(frozen=True)
class ThreadDatasetScope:
    thread_id: int
    dataset_id: str | None
    selected_at: str
