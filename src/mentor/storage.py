"""Small SQLite store for private Trading Mentor state."""

import json
import math
import sqlite3
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from mentor.datasets import (
    AnalysisEvidence,
    Dataset,
    DatasetColumn,
    DatasetImportSpec,
    DatasetMappingVersion,
    MappingEntry,
    ThreadDatasetScope,
)


_ANALYSIS_LIMITATION_CODES = frozenset(
    {
        "ambiguous_date",
        "derived_outcome",
        "incompatible_timezone",
        "insufficient_valid_rows",
        "invalid_values_excluded",
        "missing_required_role",
        "no_matching_rows",
        "omitted_groups",
        "small_sample",
        "unavailable_metric",
        "unsupported_operation",
    }
)

_ANALYSIS_METRIC_NAMES = frozenset(
    {
        "best_return",
        "breakevens",
        "cumulative_return",
        "excluded_rows",
        "first_quartile",
        "interquartile_range",
        "iqr_outlier_count",
        "loss_rate",
        "losses",
        "max_consecutive_losses",
        "max_consecutive_wins",
        "max_drawdown",
        "maximum",
        "mean_losing_return",
        "mean_mae",
        "mean_mfe",
        "mean_return",
        "mean_winning_return",
        "median_mae",
        "median_mfe",
        "median_return",
        "minimum",
        "percentile_05",
        "percentile_10",
        "percentile_25",
        "percentile_50",
        "percentile_75",
        "percentile_90",
        "percentile_95",
        "realized_reward_risk",
        "recovery_observations",
        "sample_standard_deviation",
        "third_quartile",
        "total_return",
        "valid_rows",
        "wilson_95_lower",
        "wilson_95_upper",
        "win_rate",
        "wins",
        "worst_return",
    }
)


@dataclass(frozen=True)
class Source:
    relative_path: str
    filename: str
    year: int
    local_path: str
    modified_at: float
    file_id: str


@dataclass(frozen=True)
class Thread:
    id: int
    title: str


@dataclass(frozen=True)
class TraderProfileItem:
    id: int
    category: str
    subject_key: str
    subject: str
    value: str
    kind: str
    provenance: str
    state: str
    origin_kind: str
    origin_thread_id: int | None
    origin_turn_number: int | None
    origin_available: bool
    supersedes_item_id: int | None


class Storage:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    relative_path TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    year INTEGER NOT NULL CHECK(year IN (2025, 2026)),
                    local_path TEXT NOT NULL,
                    modified_at REAL NOT NULL,
                    file_id TEXT NOT NULL,
                    vector_store_file_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS threads (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thread_items (
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    position INTEGER NOT NULL,
                    item_json TEXT NOT NULL,
                    PRIMARY KEY(thread_id, position)
                );
                CREATE TABLE IF NOT EXISTS thread_replay_items (
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    position INTEGER NOT NULL,
                    item_json TEXT NOT NULL,
                    PRIMARY KEY(thread_id, position)
                );
                CREATE TABLE IF NOT EXISTS response_diagnostics (
                    response_id TEXT PRIMARY KEY,
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    diagnostic_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS display_turns (
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    turn_number INTEGER NOT NULL,
                    user_text TEXT NOT NULL,
                    answer_markdown TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    diagnostic_json TEXT,
                    profile_update_json TEXT,
                    response_id TEXT,
                    status TEXT NOT NULL,
                    incomplete_reason TEXT,
                    raw_start_position INTEGER,
                    raw_end_position INTEGER,
                    PRIMARY KEY(thread_id, turn_number)
                );
                CREATE TABLE IF NOT EXISTS trader_profile_items (
                    id INTEGER PRIMARY KEY,
                    category TEXT NOT NULL CHECK(category IN (
                        'goals/research', 'markets/instruments', 'schedule/horizon',
                        'style/methodology', 'execution/risk/constraints', 'experience/learning',
                        'preferences/discretion', 'strengths/difficulties/principles'
                    )),
                    subject_key TEXT NOT NULL CHECK(
                        length(subject_key) BETWEEN 1 AND 120
                        AND subject_key = lower(trim(subject_key))
                        AND instr(subject_key, '  ') = 0
                    ),
                    subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 120),
                    value TEXT NOT NULL CHECK(length(value) BETWEEN 1 AND 500),
                    kind TEXT NOT NULL CHECK(kind IN (
                        'fact', 'preference', 'constraint', 'goal', 'principle', 'learning-state'
                    )),
                    provenance TEXT NOT NULL CHECK(provenance IN (
                        'USER_STATED', 'USER_CONFIRMED', 'AI_INFERRED', 'USER_DECISION'
                    )),
                    state TEXT NOT NULL CHECK(state IN (
                        'confirmed', 'tentative', 'superseded', 'conflicting', 'archived'
                    )),
                    origin_kind TEXT NOT NULL CHECK(origin_kind IN ('chat', 'profile-editor', 'confirmation')),
                    origin_thread_id INTEGER,
                    origin_turn_number INTEGER,
                    origin_available INTEGER NOT NULL CHECK(origin_available IN (0, 1)),
                    supersedes_item_id INTEGER REFERENCES trader_profile_items(id) ON DELETE SET NULL,
                    tool_call_id TEXT,
                    CHECK(
                        (origin_thread_id IS NULL AND origin_turn_number IS NULL AND origin_available = 0)
                        OR (origin_thread_id IS NOT NULL AND origin_turn_number > 0)
                    )
                );
                CREATE UNIQUE INDEX IF NOT EXISTS unique_current_profile_subject
                    ON trader_profile_items(category, subject_key) WHERE state = 'confirmed';
                CREATE INDEX IF NOT EXISTS profile_origin_thread
                    ON trader_profile_items(origin_thread_id);
                CREATE TABLE IF NOT EXISTS profile_tool_operations (
                    tool_call_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL CHECK(operation IN ('archive', 'delete')),
                    target_item_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('archived', 'deleted')),
                    origin_thread_id INTEGER NOT NULL,
                    origin_turn_number INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY,
                    original_name TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
                    original_extension TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                    source_row_count INTEGER NOT NULL CHECK(source_row_count >= 0),
                    status TEXT NOT NULL,
                    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS dataset_import_specs (
                    id INTEGER PRIMARY KEY,
                    dataset_id TEXT NOT NULL UNIQUE REFERENCES datasets(id),
                    selected_sheet TEXT,
                    header_row INTEGER NOT NULL CHECK(header_row >= 0),
                    csv_encoding TEXT,
                    csv_delimiter TEXT,
                    csv_quoting TEXT,
                    parser_version TEXT,
                    row_order_policy TEXT,
                    time_parse_policy TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS dataset_columns (
                    dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                    original_header TEXT NOT NULL,
                    inferred_type TEXT NOT NULL,
                    null_count INTEGER NOT NULL CHECK(null_count >= 0),
                    invalid_count INTEGER NOT NULL CHECK(invalid_count >= 0),
                    PRIMARY KEY(dataset_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS dataset_mapping_versions (
                    id INTEGER PRIMARY KEY,
                    dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    version INTEGER NOT NULL CHECK(version >= 1),
                    status TEXT NOT NULL CHECK(status IN ('draft', 'confirmed')),
                    parent_mapping_version_id INTEGER REFERENCES dataset_mapping_versions(id),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    confirmed_at TEXT,
                    UNIQUE(dataset_id, version)
                );
                CREATE TABLE IF NOT EXISTS dataset_mapping_entries (
                    mapping_version_id INTEGER NOT NULL REFERENCES dataset_mapping_versions(id),
                    column_ordinal INTEGER NOT NULL CHECK(column_ordinal >= 0),
                    semantic_role TEXT,
                    unit TEXT,
                    analysis_label TEXT,
                    source TEXT NOT NULL,
                    PRIMARY KEY(mapping_version_id, column_ordinal)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS unique_mapping_role
                    ON dataset_mapping_entries(mapping_version_id, semantic_role)
                    WHERE semantic_role IS NOT NULL;
                CREATE TABLE IF NOT EXISTS thread_dataset_scopes (
                    thread_id INTEGER PRIMARY KEY REFERENCES threads(id),
                    dataset_id TEXT REFERENCES datasets(id),
                    selected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS analysis_evidence (
                    id INTEGER PRIMARY KEY,
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    origin_turn_number INTEGER NOT NULL CHECK(origin_turn_number > 0),
                    display_turn_number INTEGER,
                    dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    dataset_sha256 TEXT NOT NULL CHECK(length(dataset_sha256) = 64),
                    import_spec_id INTEGER NOT NULL REFERENCES dataset_import_specs(id),
                    mapping_version_id INTEGER NOT NULL REFERENCES dataset_mapping_versions(id),
                    operation TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS analysis_tool_outputs (
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    tool_call_id TEXT NOT NULL,
                    evidence_id INTEGER NOT NULL REFERENCES analysis_evidence(id),
                    arguments_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    PRIMARY KEY(thread_id, tool_call_id)
                );
                CREATE TRIGGER IF NOT EXISTS datasets_are_immutable
                BEFORE UPDATE ON datasets
                BEGIN SELECT RAISE(ABORT, 'dataset metadata is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS dataset_import_specs_are_immutable
                BEFORE UPDATE ON dataset_import_specs
                BEGIN SELECT RAISE(ABORT, 'dataset import specs are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS dataset_columns_are_immutable
                BEFORE UPDATE ON dataset_columns
                BEGIN SELECT RAISE(ABORT, 'dataset columns are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS mapping_versions_are_immutable_except_confirmation
                BEFORE UPDATE ON dataset_mapping_versions
                WHEN NOT (
                    OLD.status = 'draft' AND NEW.status = 'confirmed'
                    AND OLD.dataset_id = NEW.dataset_id AND OLD.version = NEW.version
                    AND OLD.created_at = NEW.created_at AND OLD.confirmed_at IS NULL
                    AND NEW.confirmed_at IS NOT NULL
                )
                BEGIN SELECT RAISE(ABORT, 'mapping versions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS confirmed_mapping_entries_are_immutable
                BEFORE INSERT ON dataset_mapping_entries
                WHEN (SELECT status FROM dataset_mapping_versions WHERE id = NEW.mapping_version_id) = 'confirmed'
                BEGIN SELECT RAISE(ABORT, 'confirmed mapping entries are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS confirmed_mapping_entries_cannot_change
                BEFORE UPDATE ON dataset_mapping_entries
                WHEN (SELECT status FROM dataset_mapping_versions WHERE id = OLD.mapping_version_id) = 'confirmed'
                  OR (SELECT status FROM dataset_mapping_versions WHERE id = NEW.mapping_version_id) = 'confirmed'
                BEGIN SELECT RAISE(ABORT, 'confirmed mapping entries are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS confirmed_mapping_entries_cannot_delete
                BEFORE DELETE ON dataset_mapping_entries
                WHEN (SELECT status FROM dataset_mapping_versions WHERE id = OLD.mapping_version_id) = 'confirmed'
                BEGIN SELECT RAISE(ABORT, 'confirmed mapping entries are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS analysis_evidence_requires_confirmed_mapping
                BEFORE INSERT ON analysis_evidence
                WHEN CASE
                    WHEN json_valid(NEW.result_json) = 0 THEN 1
                    WHEN NOT EXISTS (
                        SELECT 1 FROM dataset_mapping_versions
                        JOIN datasets ON datasets.id = dataset_mapping_versions.dataset_id
                        JOIN dataset_import_specs ON dataset_import_specs.dataset_id = datasets.id
                        WHERE dataset_mapping_versions.id = NEW.mapping_version_id
                          AND dataset_mapping_versions.dataset_id = NEW.dataset_id
                          AND dataset_mapping_versions.status = 'confirmed'
                          AND datasets.content_sha256 = NEW.dataset_sha256
                          AND dataset_import_specs.id = NEW.import_spec_id
                    ) THEN 1
                    WHEN length(NEW.result_json) > 8000 THEN 1
                    WHEN json_type(NEW.result_json, '$') != 'object'
                      OR json_type(NEW.result_json, '$.provenance') != 'text'
                      OR json_extract(NEW.result_json, '$.provenance') != 'USER_EMPIRICAL_EVIDENCE'
                      OR json_extract(NEW.result_json, '$.dataset_id') != NEW.dataset_id
                      OR json_extract(NEW.result_json, '$.dataset_sha256') != NEW.dataset_sha256
                      OR json_extract(NEW.result_json, '$.mapping_version_id') != NEW.mapping_version_id
                      OR json_extract(NEW.result_json, '$.operation') != NEW.operation
                      OR json_extract(NEW.result_json, '$.schema_version') != NEW.schema_version
                      OR json_type(NEW.result_json, '$.counts') != 'object'
                      OR json_type(NEW.result_json, '$.metrics') != 'object'
                      OR json_type(NEW.result_json, '$.limitations') != 'array' THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.result_json)) != 9
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.result_json)
                          WHERE key NOT IN (
                              'provenance', 'dataset_id', 'dataset_sha256', 'mapping_version_id',
                              'operation', 'schema_version', 'counts', 'metrics', 'limitations'
                          )
                      ) THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.result_json, '$.counts')) != 4
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.result_json, '$.counts')
                          WHERE key NOT IN ('source_rows', 'filtered_rows', 'valid_rows', 'excluded_rows')
                             OR type != 'integer' OR value < 0
                      ) THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.result_json, '$.metrics')) > 50
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.result_json, '$.metrics')
                          WHERE type NOT IN ('integer', 'real', 'null') OR length(key) > 64
                      ) THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.result_json, '$.limitations')) > 20
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.result_json, '$.limitations')
                          WHERE type != 'text' OR value NOT IN (
                              'ambiguous_date', 'derived_outcome', 'incompatible_timezone',
                              'insufficient_valid_rows', 'invalid_values_excluded', 'missing_required_role',
                              'no_matching_rows', 'omitted_groups', 'small_sample', 'unavailable_metric',
                              'unsupported_operation'
                          )
                      ) THEN 1
                    ELSE 0
                END
                BEGIN SELECT RAISE(ABORT, 'analysis evidence provenance or result envelope is invalid'); END;
                CREATE TRIGGER IF NOT EXISTS analysis_evidence_is_immutable
                BEFORE UPDATE ON analysis_evidence
                BEGIN SELECT RAISE(ABORT, 'analysis evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS mapping_entries_require_dataset_column
                BEFORE INSERT ON dataset_mapping_entries
                WHEN NOT EXISTS (
                    SELECT 1 FROM dataset_mapping_versions
                    JOIN dataset_columns ON dataset_columns.dataset_id = dataset_mapping_versions.dataset_id
                    WHERE dataset_mapping_versions.id = NEW.mapping_version_id
                      AND dataset_columns.ordinal = NEW.column_ordinal
                )
                BEGIN SELECT RAISE(ABORT, 'mapping entry must reference an existing dataset column'); END;
                CREATE TRIGGER IF NOT EXISTS mapping_entry_updates_require_dataset_column
                BEFORE UPDATE OF mapping_version_id, column_ordinal ON dataset_mapping_entries
                WHEN NOT EXISTS (
                    SELECT 1 FROM dataset_mapping_versions
                    JOIN dataset_columns ON dataset_columns.dataset_id = dataset_mapping_versions.dataset_id
                    WHERE dataset_mapping_versions.id = NEW.mapping_version_id
                      AND dataset_columns.ordinal = NEW.column_ordinal
                )
                BEGIN SELECT RAISE(ABORT, 'mapping entry must reference an existing dataset column'); END;
                CREATE TRIGGER IF NOT EXISTS dataset_columns_cannot_delete_referenced
                BEFORE DELETE ON dataset_columns
                WHEN EXISTS (
                    SELECT 1 FROM dataset_mapping_entries
                    JOIN dataset_mapping_versions
                      ON dataset_mapping_versions.id = dataset_mapping_entries.mapping_version_id
                    WHERE dataset_mapping_versions.dataset_id = OLD.dataset_id
                      AND dataset_mapping_entries.column_ordinal = OLD.ordinal
                )
                BEGIN SELECT RAISE(ABORT, 'dataset column is referenced by a mapping'); END;
                CREATE TRIGGER IF NOT EXISTS analysis_tool_outputs_require_result_envelope
                BEFORE INSERT ON analysis_tool_outputs
                WHEN CASE
                    WHEN json_valid(NEW.output_json) = 0 THEN 1
                    WHEN NOT EXISTS (
                        SELECT 1 FROM analysis_evidence
                        WHERE id = NEW.evidence_id AND thread_id = NEW.thread_id
                          AND json_extract(NEW.output_json, '$.provenance') = 'USER_EMPIRICAL_EVIDENCE'
                          AND json_extract(NEW.output_json, '$.dataset_id') = dataset_id
                          AND json_extract(NEW.output_json, '$.dataset_sha256') = dataset_sha256
                          AND json_extract(NEW.output_json, '$.mapping_version_id') = mapping_version_id
                          AND json_extract(NEW.output_json, '$.operation') = operation
                          AND json_extract(NEW.output_json, '$.schema_version') = schema_version
                    ) THEN 1
                    WHEN length(NEW.output_json) > 8000
                      OR json_type(NEW.output_json, '$') != 'object'
                      OR json_type(NEW.output_json, '$.counts') != 'object'
                      OR json_type(NEW.output_json, '$.metrics') != 'object'
                      OR json_type(NEW.output_json, '$.limitations') != 'array' THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.output_json)) != 9
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.output_json)
                          WHERE key NOT IN (
                              'provenance', 'dataset_id', 'dataset_sha256', 'mapping_version_id',
                              'operation', 'schema_version', 'counts', 'metrics', 'limitations'
                          )
                      ) THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.output_json, '$.counts')) != 4
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.output_json, '$.counts')
                          WHERE key NOT IN ('source_rows', 'filtered_rows', 'valid_rows', 'excluded_rows')
                             OR type != 'integer' OR value < 0
                      ) THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.output_json, '$.metrics')) > 50
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.output_json, '$.metrics')
                          WHERE type NOT IN ('integer', 'real', 'null') OR length(key) > 64
                      ) THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.output_json, '$.limitations')) > 20
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.output_json, '$.limitations')
                          WHERE type != 'text' OR value NOT IN (
                              'ambiguous_date', 'derived_outcome', 'incompatible_timezone',
                              'insufficient_valid_rows', 'invalid_values_excluded', 'missing_required_role',
                              'no_matching_rows', 'omitted_groups', 'small_sample', 'unavailable_metric',
                              'unsupported_operation'
                          )
                      ) THEN 1
                    ELSE 0
                END
                BEGIN SELECT RAISE(ABORT, 'analysis result envelope is invalid'); END;
                CREATE TRIGGER IF NOT EXISTS analysis_tool_outputs_are_immutable
                BEFORE UPDATE ON analysis_tool_outputs
                BEGIN SELECT RAISE(ABORT, 'analysis tool output is immutable'); END;
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sources)")}
            if "modified_at" not in columns:
                connection.execute("ALTER TABLE sources ADD COLUMN modified_at REAL")
            profile_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(trader_profile_items)")
            }
            if "tool_call_id" not in profile_columns:
                connection.execute("ALTER TABLE trader_profile_items ADD COLUMN tool_call_id TEXT")
            display_turn_columns = {row[1] for row in connection.execute("PRAGMA table_info(display_turns)")}
            if "profile_update_json" not in display_turn_columns:
                connection.execute("ALTER TABLE display_turns ADD COLUMN profile_update_json TEXT")
            mapping_version_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(dataset_mapping_versions)")
            }
            if "parent_mapping_version_id" not in mapping_version_columns:
                connection.execute(
                    "ALTER TABLE dataset_mapping_versions "
                    "ADD COLUMN parent_mapping_version_id INTEGER REFERENCES dataset_mapping_versions(id)"
                )
            tool_output_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(analysis_tool_outputs)")
            }
            if "arguments_json" not in tool_output_columns:
                connection.execute("ALTER TABLE analysis_tool_outputs ADD COLUMN arguments_json TEXT")
                connection.execute(
                    """
                    UPDATE analysis_tool_outputs
                    SET arguments_json = (
                        SELECT json_object(
                            'dataset_id', dataset_id,
                            'mapping_version_id', mapping_version_id,
                            'operation', operation
                        )
                        FROM analysis_evidence
                        WHERE analysis_evidence.id = analysis_tool_outputs.evidence_id
                    )
                    WHERE arguments_json IS NULL
                    """
                )
            connection.executescript(
                """
                DROP TRIGGER IF EXISTS mapping_versions_are_immutable_except_confirmation;
                CREATE TRIGGER mapping_versions_are_immutable_except_confirmation
                BEFORE UPDATE ON dataset_mapping_versions
                WHEN NOT (
                    OLD.status = 'draft' AND NEW.status = 'confirmed'
                    AND OLD.dataset_id = NEW.dataset_id AND OLD.version = NEW.version
                    AND OLD.parent_mapping_version_id IS NEW.parent_mapping_version_id
                    AND OLD.parent_mapping_version_id IS NOT NULL
                    AND OLD.created_at = NEW.created_at AND OLD.confirmed_at IS NULL
                    AND NEW.confirmed_at IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM dataset_mapping_versions AS parent
                        WHERE parent.id = OLD.parent_mapping_version_id
                          AND parent.dataset_id = OLD.dataset_id
                          AND parent.status = 'draft'
                    )
                )
                BEGIN SELECT RAISE(ABORT, 'mapping versions are immutable'); END;
                DROP TRIGGER IF EXISTS confirmed_mapping_entries_cannot_change;
                CREATE TRIGGER confirmed_mapping_entries_cannot_change
                BEFORE UPDATE ON dataset_mapping_entries
                WHEN (SELECT status FROM dataset_mapping_versions WHERE id = OLD.mapping_version_id) = 'confirmed'
                  OR (SELECT status FROM dataset_mapping_versions WHERE id = NEW.mapping_version_id) = 'confirmed'
                BEGIN SELECT RAISE(ABORT, 'confirmed mapping entries are immutable'); END;
                DROP TRIGGER IF EXISTS analysis_tool_outputs_require_result_envelope;
                CREATE TRIGGER analysis_tool_outputs_require_result_envelope
                BEFORE INSERT ON analysis_tool_outputs
                WHEN CASE
                    WHEN json_valid(NEW.output_json) = 0 THEN 1
                    WHEN NOT EXISTS (
                        SELECT 1 FROM analysis_evidence
                        WHERE id = NEW.evidence_id AND thread_id = NEW.thread_id
                          AND json_extract(NEW.output_json, '$.provenance') = 'USER_EMPIRICAL_EVIDENCE'
                          AND json_extract(NEW.output_json, '$.dataset_id') = dataset_id
                          AND json_extract(NEW.output_json, '$.dataset_sha256') = dataset_sha256
                          AND json_extract(NEW.output_json, '$.mapping_version_id') = mapping_version_id
                          AND json_extract(NEW.output_json, '$.operation') = operation
                          AND json_extract(NEW.output_json, '$.schema_version') = schema_version
                    ) THEN 1
                    WHEN length(NEW.output_json) > 8000
                      OR json_type(NEW.output_json, '$') != 'object'
                      OR json_type(NEW.output_json, '$.counts') != 'object'
                      OR json_type(NEW.output_json, '$.metrics') != 'object'
                      OR json_type(NEW.output_json, '$.limitations') != 'array' THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.output_json)) != 9
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.output_json)
                          WHERE key NOT IN (
                              'provenance', 'dataset_id', 'dataset_sha256', 'mapping_version_id',
                              'operation', 'schema_version', 'counts', 'metrics', 'limitations'
                          )
                      ) THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.output_json, '$.counts')) != 4
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.output_json, '$.counts')
                          WHERE key NOT IN ('source_rows', 'filtered_rows', 'valid_rows', 'excluded_rows')
                             OR type != 'integer' OR value < 0
                      ) THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.output_json, '$.metrics')) > 50
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.output_json, '$.metrics')
                          WHERE type NOT IN ('integer', 'real', 'null') OR length(key) > 64
                      ) THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.output_json, '$.limitations')) > 20
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.output_json, '$.limitations')
                          WHERE type != 'text' OR value NOT IN (
                              'ambiguous_date', 'derived_outcome', 'incompatible_timezone',
                              'insufficient_valid_rows', 'invalid_values_excluded', 'missing_required_role',
                              'no_matching_rows', 'omitted_groups', 'small_sample', 'unavailable_metric',
                              'unsupported_operation'
                          )
                      ) THEN 1
                    ELSE 0
                END
                BEGIN SELECT RAISE(ABORT, 'analysis result envelope is invalid'); END;
                DROP TRIGGER IF EXISTS analysis_tool_arguments_are_metadata_only;
                CREATE TRIGGER analysis_tool_arguments_are_metadata_only
                BEFORE INSERT ON analysis_tool_outputs
                WHEN CASE
                    WHEN json_valid(NEW.arguments_json) = 0 THEN 1
                    WHEN length(NEW.arguments_json) > 512
                      OR json_type(NEW.arguments_json, '$') != 'object' THEN 1
                    WHEN (SELECT COUNT(*) FROM json_each(NEW.arguments_json)) != 3
                      OR EXISTS (
                          SELECT 1 FROM json_each(NEW.arguments_json)
                          WHERE key NOT IN ('dataset_id', 'mapping_version_id', 'operation')
                      ) THEN 1
                    WHEN NOT EXISTS (
                        SELECT 1 FROM analysis_evidence
                        WHERE id = NEW.evidence_id AND thread_id = NEW.thread_id
                          AND json_extract(NEW.arguments_json, '$.dataset_id') = dataset_id
                          AND json_extract(NEW.arguments_json, '$.mapping_version_id') = mapping_version_id
                          AND json_extract(NEW.arguments_json, '$.operation') = operation
                    ) THEN 1
                    ELSE 0
                END
                BEGIN SELECT RAISE(ABORT, 'analysis tool arguments are invalid'); END;
                DROP TRIGGER IF EXISTS analysis_evidence_metrics_are_bounded;
                CREATE TRIGGER analysis_evidence_metrics_are_bounded
                BEFORE INSERT ON analysis_evidence
                WHEN json_valid(NEW.result_json) = 0
                  OR EXISTS (
                      SELECT 1 FROM json_each(NEW.result_json, '$.metrics')
                      WHERE key NOT IN (
                          'best_return', 'breakevens', 'cumulative_return', 'excluded_rows',
                          'first_quartile', 'interquartile_range', 'iqr_outlier_count', 'loss_rate',
                          'losses', 'max_consecutive_losses', 'max_consecutive_wins', 'max_drawdown',
                          'maximum', 'mean_losing_return', 'mean_mae', 'mean_mfe', 'mean_return',
                          'mean_winning_return', 'median_mae', 'median_mfe', 'median_return', 'minimum',
                          'percentile_05', 'percentile_10', 'percentile_25', 'percentile_50',
                          'percentile_75', 'percentile_90', 'percentile_95', 'realized_reward_risk',
                          'recovery_observations', 'sample_standard_deviation', 'third_quartile',
                          'total_return', 'valid_rows', 'wilson_95_lower', 'wilson_95_upper',
                          'win_rate', 'wins', 'worst_return'
                      ) OR type NOT IN ('integer', 'real', 'null')
                  )
                BEGIN SELECT RAISE(ABORT, 'analysis result envelope metrics are invalid'); END;
                DROP TRIGGER IF EXISTS analysis_tool_output_metrics_are_bounded;
                CREATE TRIGGER analysis_tool_output_metrics_are_bounded
                BEFORE INSERT ON analysis_tool_outputs
                WHEN json_valid(NEW.output_json) = 0
                  OR EXISTS (
                      SELECT 1 FROM json_each(NEW.output_json, '$.metrics')
                      WHERE key NOT IN (
                          'best_return', 'breakevens', 'cumulative_return', 'excluded_rows',
                          'first_quartile', 'interquartile_range', 'iqr_outlier_count', 'loss_rate',
                          'losses', 'max_consecutive_losses', 'max_consecutive_wins', 'max_drawdown',
                          'maximum', 'mean_losing_return', 'mean_mae', 'mean_mfe', 'mean_return',
                          'mean_winning_return', 'median_mae', 'median_mfe', 'median_return', 'minimum',
                          'percentile_05', 'percentile_10', 'percentile_25', 'percentile_50',
                          'percentile_75', 'percentile_90', 'percentile_95', 'realized_reward_risk',
                          'recovery_observations', 'sample_standard_deviation', 'third_quartile',
                          'total_return', 'valid_rows', 'wilson_95_lower', 'wilson_95_upper',
                          'win_rate', 'wins', 'worst_return'
                      ) OR type NOT IN ('integer', 'real', 'null')
                  )
                BEGIN SELECT RAISE(ABORT, 'analysis result envelope metrics are invalid'); END;
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS unique_profile_tool_call "
                "ON trader_profile_items(tool_call_id) WHERE tool_call_id IS NOT NULL"
            )
            self._backfill_display_turns(connection)

    def set_vector_store(self, vector_store_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES ('vector_store_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (vector_store_id,),
            )

    def vector_store_id(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'vector_store_id'"
            ).fetchone()
        return None if row is None else str(row[0])

    def register_source(
        self,
        *,
        relative_path: str,
        filename: str,
        year: int,
        local_path: str,
        modified_at: float,
        file_id: str,
        vector_store_file_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sources(
                    relative_path, filename, year, local_path, modified_at, file_id, vector_store_file_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO NOTHING
                """,
                (
                    relative_path,
                    filename,
                    year,
                    local_path,
                    modified_at,
                    file_id,
                    vector_store_file_id,
                ),
            )

    def has_source(self, relative_path: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sources WHERE relative_path = ?", (relative_path,)
            ).fetchone()
        return row is not None

    def source_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM sources").fetchone()
        return int(row[0])

    def source_counts_by_year(self) -> dict[int, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT year, COUNT(*) FROM sources GROUP BY year"
            ).fetchall()
        counts = dict(rows)
        return {year: counts.get(year, 0) for year in (2025, 2026)}

    def source_for_file(self, file_id: str) -> Source | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT relative_path, filename, year, local_path, modified_at, file_id "
                "FROM sources WHERE file_id = ?",
                (file_id,),
            ).fetchone()
        return None if row is None else Source(*row)

    def update_source_modified_at(self, relative_path: str, modified_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sources SET modified_at = ? WHERE relative_path = ?",
                (modified_at, relative_path),
            )

    def create_thread(self, title: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute("INSERT INTO threads(title) VALUES (?)", (title,))
        return int(cursor.lastrowid)

    def threads(self) -> list[Thread]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT threads.id, threads.title, ("
                "SELECT item_json FROM thread_items "
                "WHERE thread_id = threads.id ORDER BY position LIMIT 1"
                ") FROM threads ORDER BY threads.id DESC"
            ).fetchall()
        return [
            Thread(row[0], label)
            for row in rows
            if (label := _thread_label(row[1], row[2])) != "New conversation"
        ]

    def has_thread(self, thread_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
        return row is not None

    def thread(self, thread_id: int) -> Thread | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, title FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
        return None if row is None else Thread(*row)

    def append_thread_items(self, thread_id: int, items: list[dict]) -> tuple[int, int] | None:
        if not items:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) FROM thread_items WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            start = int(row[0]) + 1
            connection.executemany(
                "INSERT INTO thread_items(thread_id, position, item_json) VALUES (?, ?, ?)",
                [
                    (thread_id, start + index, json.dumps(item))
                    for index, item in enumerate(items)
                ],
            )
            title = _user_text(items[0])
            if title:
                connection.execute(
                    "UPDATE threads SET title = ? WHERE id = ? AND title = 'New conversation'",
                    (_compact_title(title), thread_id),
                )
        return start, start + len(items) - 1

    def thread_items(self, thread_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT item_json FROM thread_items WHERE thread_id = ? ORDER BY position",
                (thread_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def replay_items(self, thread_id: int) -> list[dict]:
        """Return the model-only replay state, falling back to complete raw history."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT item_json FROM thread_replay_items WHERE thread_id = ? ORDER BY position",
                (thread_id,),
            ).fetchall()
        return self.thread_items(thread_id) if not rows else [json.loads(row[0]) for row in rows]

    def replace_replay_items(self, thread_id: int, items: list[dict]) -> None:
        """Atomically replace only the server-owned model replay state."""
        with self._connect() as connection:
            connection.execute("DELETE FROM thread_replay_items WHERE thread_id = ?", (thread_id,))
            connection.executemany(
                "INSERT INTO thread_replay_items(thread_id, position, item_json) VALUES (?, ?, ?)",
                [(thread_id, position, json.dumps(item)) for position, item in enumerate(items)],
            )

    def append_replay_items(self, thread_id: int, items: list[dict]) -> None:
        if not items:
            return
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM thread_replay_items WHERE thread_id = ? LIMIT 1", (thread_id,)
            ).fetchone()
            if exists is None:
                return
            row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) FROM thread_replay_items WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            start = int(row[0]) + 1
            connection.executemany(
                "INSERT INTO thread_replay_items(thread_id, position, item_json) VALUES (?, ?, ?)",
                [(thread_id, start + index, json.dumps(item)) for index, item in enumerate(items)],
            )

    def record_response_diagnostics(
        self, thread_id: int, response_id: str, diagnostic: dict
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO response_diagnostics(response_id, thread_id, diagnostic_json) "
                "VALUES (?, ?, ?)",
                (response_id, thread_id, json.dumps(diagnostic)),
            )

    def response_diagnostics(self, thread_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT diagnostic_json FROM response_diagnostics WHERE thread_id = ? ORDER BY rowid",
                (thread_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def record_display_turn(
        self,
        thread_id: int,
        *,
        user_text: str,
        answer_markdown: str,
        citations: list[dict],
        evidence: list[dict],
        diagnostics: dict | None,
        response_id: str | None,
        status: str,
        incomplete_reason: str | None,
        profile_update: dict[str, str] | None = None,
        raw_start_position: int | None = None,
        raw_end_position: int | None = None,
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(turn_number), 0) FROM display_turns WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO display_turns(
                    thread_id, turn_number, user_text, answer_markdown,
                    citations_json, evidence_json, diagnostic_json, profile_update_json, response_id,
                    status, incomplete_reason, raw_start_position, raw_end_position
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    int(row[0]) + 1,
                    user_text,
                    answer_markdown,
                    json.dumps(citations),
                    json.dumps(evidence),
                    None if diagnostics is None else json.dumps(diagnostics),
                    None if profile_update is None else json.dumps(profile_update),
                    response_id,
                    status,
                    incomplete_reason,
                    raw_start_position,
                    raw_end_position,
                ),
            )

    def display_turns(self, thread_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT turn_number, user_text, answer_markdown, citations_json,
                       evidence_json, diagnostic_json, profile_update_json, response_id, status,
                       incomplete_reason
                FROM display_turns WHERE thread_id = ? ORDER BY turn_number
                """,
                (thread_id,),
            ).fetchall()
        return [
            {
                "turn_number": row[0],
                "user_text": row[1],
                "answer_markdown": row[2],
                "citations": json.loads(row[3]),
                "evidence": json.loads(row[4]),
                "diagnostics": None if row[5] is None else json.loads(row[5]),
                "response_id": row[7],
                "status": row[8],
                "incomplete_reason": row[9],
                **({"profile_update": json.loads(row[6])} if row[6] is not None else {}),
            }
            for row in rows
        ]

    def create_profile_item(
        self,
        *,
        category: str,
        subject: str,
        value: str,
        kind: str,
        provenance: str,
        state: str,
        origin_kind: str,
        origin_thread_id: int | None = None,
        origin_turn_number: int | None = None,
        origin_available: bool | None = None,
        supersedes_item_id: int | None = None,
        tool_call_id: str | None = None,
    ) -> TraderProfileItem:
        with self._connect() as connection:
            if tool_call_id is not None:
                row = connection.execute(
                    "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                    "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                    "supersedes_item_id FROM trader_profile_items WHERE tool_call_id = ?",
                    (tool_call_id,),
                ).fetchone()
                if row is not None:
                    return _profile_item_from_row(row)
            return self._insert_profile_item(
                connection,
                category=category,
                subject=subject,
                value=value,
                kind=kind,
                provenance=provenance,
                state=state,
                origin_kind=origin_kind,
                origin_thread_id=origin_thread_id,
                origin_turn_number=origin_turn_number,
                origin_available=origin_available,
                supersedes_item_id=supersedes_item_id,
                tool_call_id=tool_call_id,
            )

    def profile_item(self, item_id: int) -> TraderProfileItem | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items WHERE id = ?",
                (item_id,),
            ).fetchone()
        return None if row is None else _profile_item_from_row(row)

    def profile_item_for_tool_call(self, tool_call_id: str) -> TraderProfileItem | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
        return None if row is None else _profile_item_from_row(row)

    def current_confirmed_profile_items(self) -> list[TraderProfileItem]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items WHERE state = 'confirmed' "
                "ORDER BY category, subject_key, id"
            ).fetchall()
        return [_profile_item_from_row(row) for row in rows]

    def profile_items(self) -> list[TraderProfileItem]:
        """Return local profile records for the browser-safe profile projection."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items ORDER BY id"
            ).fetchall()
        return [_profile_item_from_row(row) for row in rows]

    def save_questionnaire_answers(self, changes) -> dict[str, TraderProfileItem]:
        """Apply validated fixed questionnaire fields as one local transaction."""
        saved: dict[str, TraderProfileItem] = {}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for field, value in changes:
                subject_key = _profile_subject_key(field.subject)
                row = connection.execute(
                    "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                    "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                    "supersedes_item_id FROM trader_profile_items "
                    "WHERE category = ? AND subject_key = ? AND state = 'confirmed'",
                    (field.category, subject_key),
                ).fetchone()
                current = None if row is None else _profile_item_from_row(row)
                if not value:
                    if current is not None:
                        connection.execute("UPDATE trader_profile_items SET state = 'archived' WHERE id = ?", (current.id,))
                    continue
                if current is not None and current.value == value:
                    saved[field.key] = current
                    continue
                if current is not None:
                    connection.execute("UPDATE trader_profile_items SET state = 'superseded' WHERE id = ?", (current.id,))
                saved[field.key] = self._insert_profile_item(
                    connection,
                    category=field.category,
                    subject=field.subject,
                    value=value,
                    kind=field.kind,
                    provenance="USER_STATED",
                    state="confirmed",
                    origin_kind="profile-editor",
                    origin_thread_id=None,
                    origin_turn_number=None,
                    origin_available=None,
                    supersedes_item_id=None if current is None else current.id,
                    tool_call_id=None,
                )
        return saved

    def supersede_profile_item(
        self,
        item_id: int,
        *,
        value: str,
        provenance: str,
        origin_kind: str,
        subject: str | None = None,
        kind: str | None = None,
        origin_thread_id: int | None = None,
        origin_turn_number: int | None = None,
        origin_available: bool | None = None,
    ) -> TraderProfileItem:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            predecessor = _profile_item_from_row(row)
            connection.execute(
                "UPDATE trader_profile_items SET state = 'superseded' WHERE id = ?", (item_id,)
            )
            return self._insert_profile_item(
                connection,
                category=predecessor.category,
                subject=predecessor.subject if subject is None else subject,
                value=value,
                kind=predecessor.kind if kind is None else kind,
                provenance=provenance,
                state="confirmed",
                origin_kind=origin_kind,
                origin_thread_id=origin_thread_id,
                origin_turn_number=origin_turn_number,
                origin_available=origin_available,
                supersedes_item_id=item_id,
                tool_call_id=None,
            )

    def archive_profile_item(self, item_id: int) -> bool:
        return self._set_profile_state(item_id, "archived")

    def conflict_profile_items(self, item_ids: list[int]) -> int:
        if len(item_ids) < 2:
            raise ValueError("a conflict requires at least two distinct profile items")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ", ".join("?" for _ in item_ids)
            rows = connection.execute(
                "SELECT id, category, subject_key, state FROM trader_profile_items "
                f"WHERE id IN ({placeholders})",
                item_ids,
            ).fetchall()
            if len(rows) != len(item_ids):
                raise ValueError("all conflicting profile items must exist and be distinct")
            category, subject_key = rows[0][1:3]
            if any(
                row[3] not in ("confirmed", "tentative")
                or row[1] != category
                or row[2] != subject_key
                for row in rows
            ):
                raise ValueError(
                    "conflicting profile items must be current or tentative with the same category and subject"
                )
            cursor = connection.execute(
                f"UPDATE trader_profile_items SET state = 'conflicting' WHERE id IN ({placeholders})",
                item_ids,
            )
        return cursor.rowcount

    def delete_profile_item(self, item_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM trader_profile_items WHERE id = ?", (item_id,))
        return cursor.rowcount == 1

    def apply_profile_forget_operation(
        self,
        *,
        tool_call_id: str,
        operation: str,
        target_item_id: int,
        origin_thread_id: int,
        origin_turn_number: int,
    ) -> str:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT operation, target_item_id, status FROM profile_tool_operations WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != operation or existing[1] != target_item_id:
                    raise ValueError("tool call id cannot target a different profile operation")
                return str(existing[2])
            row = connection.execute(
                "SELECT state FROM trader_profile_items WHERE id = ?", (target_item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(target_item_id)
            if operation == "archive":
                if row[0] != "confirmed":
                    raise ValueError("only a confirmed profile item can be archived by chat")
                connection.execute(
                    "UPDATE trader_profile_items SET state = 'archived' WHERE id = ?", (target_item_id,)
                )
                status = "archived"
            elif operation == "delete":
                connection.execute("DELETE FROM trader_profile_items WHERE id = ?", (target_item_id,))
                status = "deleted"
            else:
                raise ValueError("unsupported profile operation")
            connection.execute(
                "INSERT INTO profile_tool_operations(\n"
                "tool_call_id, operation, target_item_id, status, origin_thread_id, origin_turn_number\n"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (tool_call_id, operation, target_item_id, status, origin_thread_id, origin_turn_number),
            )
            return status

    def profile_operation_status(self, tool_call_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM profile_tool_operations WHERE tool_call_id = ?", (tool_call_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    def profile_mutation_exists_for_origin(self, thread_id: int, turn_number: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM trader_profile_items WHERE origin_thread_id = ? AND origin_turn_number = ? "
                "UNION SELECT 1 FROM profile_tool_operations WHERE origin_thread_id = ? AND origin_turn_number = ? "
                "LIMIT 1",
                (thread_id, turn_number, thread_id, turn_number),
            ).fetchone()
        return row is not None

    def create_dataset(
        self,
        *,
        dataset_id: str,
        original_name: str,
        content_sha256: str,
        original_extension: str,
        byte_size: int,
        source_row_count: int,
        status: str,
        import_spec: DatasetImportSpec | Mapping[str, Any],
        columns: list[DatasetColumn | Mapping[str, Any]],
    ) -> Dataset:
        """Store metadata only; original rows remain in the ignored local file."""
        spec = _dataset_values(import_spec)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO datasets(id, original_name, content_sha256, original_extension, byte_size, source_row_count, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (dataset_id, original_name, content_sha256, original_extension, byte_size, source_row_count, status),
            )
            cursor = connection.execute(
                """
                INSERT INTO dataset_import_specs(
                    dataset_id, selected_sheet, header_row, csv_encoding, csv_delimiter,
                    csv_quoting, parser_version, row_order_policy, time_parse_policy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    spec.get("selected_sheet"),
                    spec["header_row"],
                    spec.get("csv_encoding"),
                    spec.get("csv_delimiter"),
                    spec.get("csv_quoting"),
                    spec.get("parser_version"),
                    spec.get("row_order_policy"),
                    spec.get("time_parse_policy"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO dataset_columns(dataset_id, ordinal, original_header, inferred_type, null_count, invalid_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        dataset_id,
                        values["ordinal"],
                        values["original_header"],
                        values["inferred_type"],
                        values["null_count"],
                        values["invalid_count"],
                    )
                    for column in columns
                    if (values := _dataset_values(column))
                ],
            )
            return Dataset(
                dataset_id,
                original_name,
                content_sha256,
                original_extension,
                byte_size,
                source_row_count,
                status,
                int(cursor.lastrowid),
            )

    def dataset(self, dataset_id: str) -> Dataset | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT datasets.id, original_name, content_sha256, original_extension,
                       byte_size, source_row_count, status, dataset_import_specs.id
                FROM datasets JOIN dataset_import_specs ON dataset_import_specs.dataset_id = datasets.id
                WHERE datasets.id = ?
                """,
                (dataset_id,),
            ).fetchone()
        return None if row is None else Dataset(*row)

    def create_mapping_draft(
        self, dataset_id: str, entries: list[MappingEntry | Mapping[str, Any]]
    ) -> DatasetMappingVersion:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM dataset_mapping_versions WHERE dataset_id = ?",
                (dataset_id,),
            ).fetchone()
            cursor = connection.execute(
                "INSERT INTO dataset_mapping_versions(dataset_id, version, status, parent_mapping_version_id) "
                "VALUES (?, ?, 'draft', NULL)",
                (dataset_id, int(row[0])),
            )
            self._insert_mapping_entries(connection, int(cursor.lastrowid), entries)
            return DatasetMappingVersion(int(cursor.lastrowid), dataset_id, int(row[0]), "draft", None)

    def confirm_mapping_version(self, draft_mapping_version_id: int) -> DatasetMappingVersion:
        """Copy a draft into a separate immutable confirmed snapshot."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = connection.execute(
                "SELECT dataset_id, status FROM dataset_mapping_versions WHERE id = ?", (draft_mapping_version_id,)
            ).fetchone()
            if draft is None or draft[1] != "draft":
                raise ValueError("only a draft mapping version can be confirmed")
            next_version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM dataset_mapping_versions WHERE dataset_id = ?",
                (draft[0],),
            ).fetchone()
            cursor = connection.execute(
                "INSERT INTO dataset_mapping_versions(dataset_id, version, status, parent_mapping_version_id) "
                "VALUES (?, ?, 'draft', ?)",
                (draft[0], int(next_version[0]), draft_mapping_version_id),
            )
            confirmed_id = int(cursor.lastrowid)
            self._insert_mapping_entries(
                connection,
                confirmed_id,
                [
                    MappingEntry(*row)
                    for row in connection.execute(
                        "SELECT column_ordinal, semantic_role, unit, analysis_label, source "
                        "FROM dataset_mapping_entries WHERE mapping_version_id = ? ORDER BY column_ordinal",
                        (draft_mapping_version_id,),
                    )
                ],
            )
            connection.execute(
                "UPDATE dataset_mapping_versions SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (confirmed_id,),
            )
            return DatasetMappingVersion(
                confirmed_id, str(draft[0]), int(next_version[0]), "confirmed", draft_mapping_version_id
            )

    def mapping_version(self, mapping_version_id: int) -> DatasetMappingVersion | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, dataset_id, version, status, parent_mapping_version_id "
                "FROM dataset_mapping_versions WHERE id = ?",
                (mapping_version_id,),
            ).fetchone()
        return None if row is None else DatasetMappingVersion(*row)

    def mapping_entries(self, mapping_version_id: int) -> list[MappingEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT column_ordinal, semantic_role, unit, analysis_label, source "
                "FROM dataset_mapping_entries WHERE mapping_version_id = ? ORDER BY column_ordinal",
                (mapping_version_id,),
            ).fetchall()
        return [MappingEntry(*row) for row in rows]

    def set_thread_dataset_scope(self, thread_id: int, dataset_id: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO thread_dataset_scopes(thread_id, dataset_id) VALUES (?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET dataset_id = excluded.dataset_id, selected_at = CURRENT_TIMESTAMP",
                (thread_id, dataset_id),
            )

    def thread_dataset_scope(self, thread_id: int) -> ThreadDatasetScope | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT thread_id, dataset_id, selected_at FROM thread_dataset_scopes WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return None if row is None else ThreadDatasetScope(*row)

    def record_analysis_evidence(
        self,
        *,
        thread_id: int,
        origin_turn_number: int,
        dataset_id: str,
        mapping_version_id: int,
        operation: str,
        schema_version: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
        display_turn_number: int | None = None,
    ) -> AnalysisEvidence:
        with self._connect() as connection:
            dataset = connection.execute(
                """
                SELECT datasets.content_sha256, dataset_import_specs.id
                FROM datasets
                JOIN dataset_import_specs ON dataset_import_specs.dataset_id = datasets.id
                JOIN dataset_mapping_versions ON dataset_mapping_versions.id = ?
                WHERE datasets.id = ? AND dataset_mapping_versions.dataset_id = datasets.id
                      AND dataset_mapping_versions.status = 'confirmed'
                """,
                (mapping_version_id, dataset_id),
            ).fetchone()
            if dataset is None:
                raise ValueError("analysis requires a confirmed mapping version for its dataset")
            result_json = _analysis_result_envelope_json(
                result,
                dataset_id=dataset_id,
                dataset_sha256=str(dataset[0]),
                mapping_version_id=mapping_version_id,
                operation=operation,
                schema_version=schema_version,
            )
            cursor = connection.execute(
                """
                INSERT INTO analysis_evidence(
                    thread_id, origin_turn_number, display_turn_number, dataset_id, dataset_sha256,
                    import_spec_id, mapping_version_id, operation, schema_version, arguments_json, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    origin_turn_number,
                    display_turn_number,
                    dataset_id,
                    dataset[0],
                    dataset[1],
                    mapping_version_id,
                    operation,
                    schema_version,
                    json.dumps(arguments, separators=(",", ":")),
                    result_json,
                ),
            )
            return AnalysisEvidence(
                int(cursor.lastrowid),
                thread_id,
                origin_turn_number,
                dataset_id,
                str(dataset[0]),
                int(dataset[1]),
                mapping_version_id,
                operation,
                schema_version,
            )

    def analysis_evidence(self, thread_id: int) -> list[AnalysisEvidence]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, thread_id, origin_turn_number, dataset_id, dataset_sha256, import_spec_id,
                       mapping_version_id, operation, schema_version
                FROM analysis_evidence WHERE thread_id = ? ORDER BY id
                """,
                (thread_id,),
            ).fetchall()
        return [AnalysisEvidence(*row) for row in rows]

    def record_analysis_tool_output(
        self,
        thread_id: int,
        tool_call_id: str,
        evidence_id: int,
        output: Mapping[str, Any],
        *,
        arguments: Mapping[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            evidence = connection.execute(
                """
                SELECT dataset_id, dataset_sha256, mapping_version_id, operation, schema_version
                FROM analysis_evidence WHERE id = ? AND thread_id = ?
                """,
                (evidence_id, thread_id),
            ).fetchone()
            if evidence is None:
                raise ValueError("analysis tool output must belong to its thread evidence")
            output_json = _analysis_result_envelope_json(
                output,
                dataset_id=str(evidence[0]),
                dataset_sha256=str(evidence[1]),
                mapping_version_id=int(evidence[2]),
                operation=str(evidence[3]),
                schema_version=str(evidence[4]),
            )
            arguments_json = _analysis_tool_arguments_json(
                arguments,
                dataset_id=str(evidence[0]),
                mapping_version_id=int(evidence[2]),
                operation=str(evidence[3]),
            )
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, tool_call_id, evidence_id, arguments_json, output_json),
            )

    def analysis_tool_outputs(self, thread_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT tool_call_id, evidence_id, arguments_json, output_json
                FROM analysis_tool_outputs WHERE thread_id = ? ORDER BY tool_call_id
                """,
                (thread_id,),
            ).fetchall()
        return [
            {
                "tool_call_id": row[0],
                "evidence_id": row[1],
                "arguments": json.loads(row[2]),
                "output": json.loads(row[3]),
            }
            for row in rows
        ]

    def _insert_mapping_entries(
        self,
        connection: sqlite3.Connection,
        mapping_version_id: int,
        entries: list[MappingEntry | Mapping[str, Any]],
    ) -> None:
        mapping = connection.execute(
            "SELECT dataset_id FROM dataset_mapping_versions WHERE id = ?", (mapping_version_id,)
        ).fetchone()
        if mapping is None:
            raise ValueError("mapping version does not exist")
        available_columns = {
            row[0]
            for row in connection.execute(
                "SELECT ordinal FROM dataset_columns WHERE dataset_id = ?", (mapping[0],)
            )
        }
        values_by_entry = [_dataset_values(entry) for entry in entries]
        if any(values["column_ordinal"] not in available_columns for values in values_by_entry):
            raise ValueError("mapping entries must reference an existing dataset column")
        connection.executemany(
            """
            INSERT INTO dataset_mapping_entries(
                mapping_version_id, column_ordinal, semantic_role, unit, analysis_label, source
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    mapping_version_id,
                    values["column_ordinal"],
                    values.get("semantic_role"),
                    values.get("unit"),
                    values.get("analysis_label"),
                    values.get("source", "manual"),
                )
                for values in values_by_entry
            ],
        )

    def delete_thread(self, thread_id: int) -> bool:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if exists is None:
                return False
            connection.execute(
                "UPDATE trader_profile_items SET origin_available = 0 WHERE origin_thread_id = ?",
                (thread_id,),
            )
            connection.execute(
                "DELETE FROM profile_tool_operations WHERE origin_thread_id = ?", (thread_id,)
            )
            connection.execute("DELETE FROM analysis_tool_outputs WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM analysis_evidence WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM thread_dataset_scopes WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM display_turns WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM response_diagnostics WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM thread_replay_items WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM thread_items WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        return True

    def _set_profile_state(self, item_id: int, state: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE trader_profile_items SET state = ? WHERE id = ?", (state, item_id)
            )
        return cursor.rowcount == 1

    def _insert_profile_item(
        self,
        connection: sqlite3.Connection,
        *,
        category: str,
        subject: str,
        value: str,
        kind: str,
        provenance: str,
        state: str,
        origin_kind: str,
        origin_thread_id: int | None,
        origin_turn_number: int | None,
        origin_available: bool | None,
        supersedes_item_id: int | None,
        tool_call_id: str | None,
    ) -> TraderProfileItem:
        subject = " ".join(subject.split())
        value = value.strip()
        cursor = connection.execute(
            """
            INSERT INTO trader_profile_items(
                category, subject_key, subject, value, kind, provenance, state, origin_kind,
                origin_thread_id, origin_turn_number, origin_available, supersedes_item_id, tool_call_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                category,
                _profile_subject_key(subject),
                subject,
                value,
                kind,
                provenance,
                state,
                origin_kind,
                origin_thread_id,
                origin_turn_number,
                int(origin_thread_id is not None if origin_available is None else origin_available),
                supersedes_item_id,
                tool_call_id,
            ),
        )
        row = connection.execute(
            "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
            "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
            "supersedes_item_id FROM trader_profile_items WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return _profile_item_from_row(row)

    def _backfill_display_turns(self, connection: sqlite3.Connection) -> None:
        thread_ids = connection.execute("SELECT id FROM threads").fetchall()
        for (thread_id,) in thread_ids:
            items = [
                (row[0], json.loads(row[1]))
                for row in connection.execute(
                    "SELECT position, item_json FROM thread_items WHERE thread_id = ? ORDER BY position",
                    (thread_id,),
                )
            ]
            first_user = next((item for _, item in items if _user_text(item)), None)
            if first_user is not None:
                connection.execute(
                    "UPDATE threads SET title = ? WHERE id = ? AND title = 'New conversation'",
                    (_compact_title(_user_text(first_user) or ""), thread_id),
                )
            existing = connection.execute(
                "SELECT 1 FROM display_turns WHERE thread_id = ? LIMIT 1", (thread_id,)
            ).fetchone()
            if existing is not None:
                continue
            diagnostics = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT diagnostic_json FROM response_diagnostics WHERE thread_id = ? ORDER BY rowid",
                    (thread_id,),
                )
            ]
            diagnostic_index = 0
            starts = [index for index, (_, item) in enumerate(items) if _user_text(item) is not None]
            for turn_number, start_index in enumerate(starts, start=1):
                end_index = starts[turn_number] if turn_number < len(starts) else len(items)
                raw_items = [item for _, item in items[start_index:end_index]]
                user_text = _user_text(raw_items[0]) or ""
                answer_markdown, citations, evidence = _display_content(raw_items[1:])
                diagnostic = diagnostics[diagnostic_index] if answer_markdown and diagnostic_index < len(diagnostics) else None
                if diagnostic is not None:
                    diagnostic_index += 1
                status = str((diagnostic or {}).get("status") or ("completed" if answer_markdown else "incomplete"))
                response_id = (diagnostic or {}).get("response_id")
                connection.execute(
                    """
                    INSERT INTO display_turns(
                        thread_id, turn_number, user_text, answer_markdown,
                        citations_json, evidence_json, diagnostic_json, response_id,
                        status, incomplete_reason, raw_start_position, raw_end_position
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        turn_number,
                        user_text.strip(),
                        answer_markdown,
                        json.dumps(citations),
                        json.dumps(evidence),
                        None if diagnostic is None else json.dumps(diagnostic),
                        response_id,
                        status,
                        (diagnostic or {}).get("incomplete_reason"),
                        items[start_index][0],
                        items[end_index - 1][0],
                    ),
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _dataset_values(value: DatasetImportSpec | DatasetColumn | MappingEntry | Mapping[str, Any]) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else asdict(value)


def _analysis_result_envelope_json(
    value: Mapping[str, Any],
    *,
    dataset_id: str,
    dataset_sha256: str,
    mapping_version_id: int,
    operation: str,
    schema_version: str,
) -> str:
    if not isinstance(value, Mapping) or set(value) != {
        "provenance",
        "dataset_id",
        "dataset_sha256",
        "mapping_version_id",
        "operation",
        "schema_version",
        "counts",
        "metrics",
        "limitations",
    }:
        raise ValueError("analysis result envelope is invalid")
    if (
        value["provenance"] != "USER_EMPIRICAL_EVIDENCE"
        or value["dataset_id"] != dataset_id
        or value["dataset_sha256"] != dataset_sha256
        or value["mapping_version_id"] != mapping_version_id
        or value["operation"] != operation
        or value["schema_version"] != schema_version
    ):
        raise ValueError("analysis result envelope does not match its evidence provenance")
    counts = value["counts"]
    if (
        not isinstance(counts, Mapping)
        or set(counts) != {"source_rows", "filtered_rows", "valid_rows", "excluded_rows"}
        or any(type(count) is not int or count < 0 for count in counts.values())
    ):
        raise ValueError("analysis result envelope is invalid")
    metrics = value["metrics"]
    if (
        not isinstance(metrics, Mapping)
        or len(metrics) > 50
        or any(
            not isinstance(name, str)
            or name not in _ANALYSIS_METRIC_NAMES
            or type(metric) not in (int, float, type(None))
            or (isinstance(metric, float) and not math.isfinite(metric))
            for name, metric in metrics.items()
        )
    ):
        raise ValueError("analysis result envelope is invalid")
    limitations = value["limitations"]
    if (
        not isinstance(limitations, list)
        or len(limitations) > 20
        or any(item not in _ANALYSIS_LIMITATION_CODES for item in limitations)
    ):
        raise ValueError("analysis result envelope is invalid")
    serialized = json.dumps(value, separators=(",", ":"), allow_nan=False)
    if len(serialized) > 8000:
        raise ValueError("analysis result envelope is invalid")
    return serialized


def _analysis_tool_arguments_json(
    value: Mapping[str, Any] | None,
    *,
    dataset_id: str,
    mapping_version_id: int,
    operation: str,
) -> str:
    expected = {
        "dataset_id": dataset_id,
        "mapping_version_id": mapping_version_id,
        "operation": operation,
    }
    if value is None:
        value = expected
    if not isinstance(value, Mapping) or dict(value) != expected:
        raise ValueError("analysis tool arguments are invalid")
    serialized = json.dumps(expected, separators=(",", ":"))
    if len(serialized) > 512:
        raise ValueError("analysis tool arguments are invalid")
    return serialized


def _thread_label(title: str, first_item_json: str | None) -> str:
    if title != "New conversation" or not first_item_json:
        return title
    try:
        item = json.loads(first_item_json)
        text = item["content"][0]["text"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return title
    compact = " ".join(str(text).split())
    return f"{compact[:55]}…" if len(compact) > 56 else compact or title


def _user_text(item: dict) -> str | None:
    if item.get("role") != "user":
        return None
    for content in item.get("content") or []:
        if content.get("type") == "input_text" and isinstance(content.get("text"), str):
            return content["text"]
    return None


def _compact_title(text: str) -> str:
    compact = " ".join(text.split())
    return f"{compact[:55]}…" if len(compact) > 56 else compact


def _profile_subject_key(subject: str) -> str:
    return " ".join(subject.split()).casefold()


def _profile_item_from_row(row: tuple) -> TraderProfileItem:
    return TraderProfileItem(*row[:11], bool(row[11]), row[12])


def _display_content(items: list[dict]) -> tuple[str, list[dict], list[dict]]:
    text_parts: list[str] = []
    citations: list[dict] = []
    evidence: list[dict] = []
    for item in items:
        if item.get("type") == "file_search_call":
            for result in item.get("results") or []:
                attributes = result.get("attributes") or {}
                evidence.append(
                    {
                        "file_id": result["file_id"],
                        "filename": result.get("filename", "Unknown source"),
                        "excerpt": result.get("text", ""),
                        "year": attributes.get("year"),
                        "metadata": {str(key): str(value) for key, value in attributes.items()},
                    }
                )
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        for content in item.get("content") or []:
            if content.get("type") != "output_text":
                continue
            text_parts.append(content.get("text", ""))
            for annotation in content.get("annotations") or []:
                if annotation.get("type") == "file_citation":
                    citation = {
                        "file_id": annotation["file_id"],
                        "filename": annotation.get("filename", "Unknown source"),
                    }
                    if citation not in citations:
                        citations.append(citation)
    return "".join(text_parts), citations, evidence
