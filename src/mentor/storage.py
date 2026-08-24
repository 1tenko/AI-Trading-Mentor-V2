"""Small SQLite store for private Trading Mentor state."""

import json
import ntpath
import re
import sqlite3
import time
from dataclasses import replace
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from mentor.anchors import LOCATOR_VERSION, SourceAnchor
from mentor.config import PRODUCTION_RUNTIME_SCOPE, RUNTIME_SCOPES
from mentor.compilation import CandidateGateResult, CompilationMetric, CompilationRun, CorpusSnapshot, SourceProcessingResult
from mentor.dependencies import DependencyEdge, DependencyGraph, DependencyNode
from mentor.derived_records import (
    Claim,
    CompilerProvenance,
    ConflictUnresolved,
    DerivedRecord,
    Evolution,
    Facet,
    ProcedureRecordBranch,
    ProcedureSequenceHierarchy,
    RecordDependency,
    Relationship,
    validate_record,
)
from mentor.validation import SemanticValidator, ValidationResult
from mentor.knowledge import Collection, Source as LibrarySource, SourceRevision
from mentor.synthesis import Concept, ConceptHint, ConceptOccurrence


@dataclass(frozen=True)
class Source:
    relative_path: str
    filename: str
    year: int
    local_path: str
    modified_at: float
    file_id: str


@dataclass(frozen=True)
class LegacySource:
    relative_path: str
    filename: str
    year: int
    local_path: str
    modified_at: float
    file_id: str
    vector_store_file_id: str


@dataclass(frozen=True)
class SourceChange:
    source_id: str
    lifecycle_state: str
    revision_id: str | None
    local_locator: str
    observed_at: float


@dataclass(frozen=True)
class CandidateRemoteOperation:
    operation_id: int
    snapshot_id: str
    stage: str
    operation: str
    status: str
    store_id: str | None
    file_id: str | None
    attachment_id: str | None
    batch_id: str | None


@dataclass(frozen=True)
class Thread:
    id: int
    title: str


_SNAPSHOT_QUERY = """
SELECT corpus_snapshots.snapshot_id, run_id, selected_revision_ids_json, selected_revision_fingerprint,
       raw_store_id, derived_store_id, model_version, prompt_version, schema_version,
       CASE WHEN archived_snapshots.snapshot_id IS NULL THEN corpus_snapshots.status ELSE 'archived' END,
       created_at, validated_at, published_at, failed_at, failure_reason
FROM corpus_snapshots
LEFT JOIN archived_snapshots ON archived_snapshots.snapshot_id = corpus_snapshots.snapshot_id
"""

_CANDIDATE_GATE_STRUCTURE_VERSION = "record-structure-v1"


class Storage:
    def __init__(self, database_path: Path, runtime_scope: str = PRODUCTION_RUNTIME_SCOPE):
        if runtime_scope not in RUNTIME_SCOPES:
            raise ValueError("invalid runtime scope")
        self.database_path = database_path
        self.runtime_scope = runtime_scope

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
                    response_id TEXT,
                    status TEXT NOT NULL,
                    incomplete_reason TEXT,
                    raw_start_position INTEGER,
                    raw_end_position INTEGER,
                    PRIMARY KEY(thread_id, turn_number)
                );
                CREATE TABLE IF NOT EXISTS collections (
                    collection_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    scope TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS library_sources (
                    source_id TEXT PRIMARY KEY,
                    collection_id TEXT NOT NULL REFERENCES collections(collection_id),
                    identity_key TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    author TEXT NOT NULL,
                    course TEXT NOT NULL,
                    lesson_title TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    original_filename TEXT NOT NULL,
                    local_provenance TEXT NOT NULL,
                    UNIQUE(collection_id, identity_key)
                );
                CREATE TABLE IF NOT EXISTS source_revisions (
                    revision_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES library_sources(source_id),
                    content_sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                    local_locator TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    remote_file_id TEXT,
                    remote_vector_store_file_id TEXT,
                    UNIQUE(source_id, content_sha256)
                );
                CREATE TABLE IF NOT EXISTS source_changes (
                    source_id TEXT PRIMARY KEY REFERENCES library_sources(source_id),
                    lifecycle_state TEXT NOT NULL,
                    revision_id TEXT REFERENCES source_revisions(revision_id),
                    local_locator TEXT NOT NULL,
                    observed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS compilation_runs (
                    run_id TEXT PRIMARY KEY,
                    model_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('building', 'validating', 'failed', 'published')),
                    completed_at REAL,
                    failure_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS corpus_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE REFERENCES compilation_runs(run_id),
                    selected_revision_ids_json TEXT NOT NULL,
                    selected_revision_fingerprint TEXT NOT NULL,
                    raw_store_id TEXT,
                    derived_store_id TEXT,
                    model_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('building', 'validating', 'failed', 'published')),
                    created_at REAL NOT NULL,
                    validated_at REAL,
                    published_at REAL,
                    failed_at REAL,
                    failure_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS archived_snapshots (
                    snapshot_id TEXT PRIMARY KEY REFERENCES corpus_snapshots(snapshot_id),
                    archived_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_artifact_scopes (
                    snapshot_id TEXT PRIMARY KEY REFERENCES corpus_snapshots(snapshot_id),
                    artifact_scope TEXT NOT NULL CHECK(artifact_scope IN ('pilot', 'production'))
                );
                CREATE TABLE IF NOT EXISTS candidate_remote_operations (
                    operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL REFERENCES corpus_snapshots(snapshot_id),
                    stage TEXT NOT NULL CHECK(stage IN ('remote_setup', 'raw_store', 'derived_store')),
                    operation TEXT NOT NULL CHECK(operation IN (
                        'create_store', 'create_batch', 'batch_status',
                        'upload_file', 'attach_file', 'attachment_status'
                    )),
                    status TEXT NOT NULL CHECK(status IN ('attempted', 'succeeded', 'failed')),
                    store_id TEXT,
                    file_id TEXT,
                    attachment_id TEXT,
                    batch_id TEXT
                );
                CREATE TABLE IF NOT EXISTS snapshot_source_coverage (
                    snapshot_id TEXT NOT NULL REFERENCES corpus_snapshots(snapshot_id),
                    revision_id TEXT NOT NULL REFERENCES source_revisions(revision_id),
                    status TEXT NOT NULL CHECK(status IN ('processed', 'failed')),
                    record_count INTEGER NOT NULL CHECK(record_count >= 0),
                    PRIMARY KEY(snapshot_id, revision_id)
                );
                CREATE TABLE IF NOT EXISTS candidate_gates (
                    snapshot_id TEXT PRIMARY KEY REFERENCES corpus_snapshots(snapshot_id),
                    status TEXT NOT NULL CHECK(status IN ('passed', 'failed')),
                    checked_at REAL NOT NULL,
                    failure_reason TEXT,
                    structural_version TEXT NOT NULL,
                    record_count INTEGER NOT NULL CHECK(record_count >= 0),
                    record_fingerprint TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS compilation_metrics (
                    metric_id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES compilation_runs(run_id),
                    stage TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    source_count INTEGER NOT NULL CHECK(source_count >= 0),
                    record_count INTEGER NOT NULL CHECK(record_count >= 0),
                    call_count INTEGER NOT NULL CHECK(call_count >= 0),
                    input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
                    output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0 CHECK(reasoning_tokens >= 0),
                    latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
                    cost_usd REAL NOT NULL CHECK(cost_usd >= 0),
                    remote_calls INTEGER NOT NULL CHECK(remote_calls >= 0),
                    failure_count INTEGER NOT NULL CHECK(failure_count >= 0)
                );
                CREATE TABLE IF NOT EXISTS derived_records (
                    record_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES corpus_snapshots(snapshot_id),
                    family TEXT NOT NULL CHECK(family IN (
                        'claim', 'relationship', 'procedure_sequence_hierarchy', 'evolution', 'conflict_unresolved'
                    )),
                    derived_kind TEXT NOT NULL,
                    semantic_subtype TEXT NOT NULL DEFAULT '',
                    evidence_state TEXT NOT NULL CHECK(evidence_state IN ('raw_taught', 'cross_source_synthesis')),
                    validation_state TEXT NOT NULL CHECK(validation_state IN ('pending', 'validated', 'rejected')),
                    lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN ('candidate', 'active', 'superseded', 'retired')),
                    qualification TEXT NOT NULL,
                    compiler_model_version TEXT,
                    compiler_prompt_version TEXT,
                    compiler_schema_version TEXT,
                    finalized INTEGER NOT NULL DEFAULT 0 CHECK(finalized IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS derived_record_anchors (
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    position INTEGER NOT NULL,
                    anchor_id TEXT NOT NULL,
                    PRIMARY KEY(record_id, position)
                );
                CREATE TABLE IF NOT EXISTS source_anchors (
                    anchor_id TEXT PRIMARY KEY,
                    collection_id TEXT NOT NULL REFERENCES collections(collection_id),
                    source_id TEXT NOT NULL REFERENCES library_sources(source_id),
                    revision_id TEXT NOT NULL REFERENCES source_revisions(revision_id),
                    revision_sha256 TEXT NOT NULL,
                    start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
                    end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
                    timestamp_start_ms INTEGER,
                    timestamp_end_ms INTEGER,
                    span_fingerprint TEXT NOT NULL,
                    locator_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS derived_record_dependencies (
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    position INTEGER NOT NULL,
                    dependency_kind TEXT NOT NULL CHECK(dependency_kind IN ('source_revision', 'derived_record')),
                    dependency_id TEXT NOT NULL,
                    PRIMARY KEY(record_id, position)
                );
                CREATE TABLE IF NOT EXISTS derived_record_staleness (
                    snapshot_id TEXT NOT NULL REFERENCES corpus_snapshots(snapshot_id),
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    revision_id TEXT NOT NULL REFERENCES source_revisions(revision_id),
                    PRIMARY KEY(snapshot_id, record_id, revision_id)
                );
                CREATE TABLE IF NOT EXISTS derived_record_reuse (
                    snapshot_id TEXT NOT NULL REFERENCES corpus_snapshots(snapshot_id),
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    previous_snapshot_id TEXT NOT NULL REFERENCES corpus_snapshots(snapshot_id),
                    previous_record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    PRIMARY KEY(snapshot_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS derived_record_concepts (
                    snapshot_id TEXT NOT NULL REFERENCES corpus_snapshots(snapshot_id),
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    concept_id TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS derived_concepts (
                    snapshot_id TEXT NOT NULL REFERENCES corpus_snapshots(snapshot_id),
                    concept_id TEXT NOT NULL,
                    canonical_label TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, concept_id)
                );
                CREATE TABLE IF NOT EXISTS derived_concept_aliases (
                    snapshot_id TEXT NOT NULL,
                    concept_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    alias TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, concept_id, position),
                    FOREIGN KEY(snapshot_id, concept_id) REFERENCES derived_concepts(snapshot_id, concept_id)
                );
                CREATE TABLE IF NOT EXISTS derived_concept_supporting_records (
                    snapshot_id TEXT NOT NULL,
                    concept_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    PRIMARY KEY(snapshot_id, concept_id, position),
                    FOREIGN KEY(snapshot_id, concept_id) REFERENCES derived_concepts(snapshot_id, concept_id)
                );
                CREATE TABLE IF NOT EXISTS derived_concept_supporting_anchors (
                    snapshot_id TEXT NOT NULL,
                    concept_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    anchor_id TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, concept_id, position),
                    FOREIGN KEY(snapshot_id, concept_id) REFERENCES derived_concepts(snapshot_id, concept_id)
                );
                CREATE TABLE IF NOT EXISTS derived_record_concept_links (
                    snapshot_id TEXT NOT NULL,
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    concept_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    is_primary INTEGER NOT NULL CHECK(is_primary IN (0, 1)),
                    PRIMARY KEY(snapshot_id, record_id, concept_id),
                    UNIQUE(snapshot_id, record_id, position),
                    FOREIGN KEY(snapshot_id, concept_id) REFERENCES derived_concepts(snapshot_id, concept_id)
                );
                CREATE TABLE IF NOT EXISTS derived_concept_occurrences (
                    snapshot_id TEXT NOT NULL,
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    position INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    term_position INTEGER,
                    label_key TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    concept_id TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, record_id, position),
                    FOREIGN KEY(snapshot_id, concept_id) REFERENCES derived_concepts(snapshot_id, concept_id)
                );
                CREATE TABLE IF NOT EXISTS derived_record_facets (
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    position INTEGER NOT NULL,
                    facet_name TEXT NOT NULL CHECK(facet_name IN ('scope', 'condition', 'exception', 'outcome', 'timeframe')),
                    facet_value TEXT NOT NULL,
                    PRIMARY KEY(record_id, position)
                );
                CREATE TABLE IF NOT EXISTS derived_claims (
                    record_id TEXT PRIMARY KEY REFERENCES derived_records(record_id),
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS derived_relationships (
                    record_id TEXT PRIMARY KEY REFERENCES derived_records(record_id),
                    left_term TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    right_term TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS derived_procedure_sequence_hierarchy (
                    record_id TEXT PRIMARY KEY REFERENCES derived_records(record_id),
                    structure_kind TEXT NOT NULL CHECK(structure_kind IN ('procedure', 'sequence', 'hierarchy')),
                    prerequisites_json TEXT NOT NULL DEFAULT '[]',
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    branches_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS derived_evolutions (
                    record_id TEXT PRIMARY KEY REFERENCES derived_records(record_id),
                    subject TEXT NOT NULL,
                    previous_value TEXT NOT NULL,
                    current_value TEXT NOT NULL,
                    earlier_source_set_json TEXT NOT NULL DEFAULT '[]',
                    later_source_set_json TEXT NOT NULL DEFAULT '[]',
                    classification TEXT NOT NULL DEFAULT 'no_supported_classification',
                    negative_evidence_state TEXT NOT NULL DEFAULT 'unresolved',
                    competing_anchor_ids_json TEXT NOT NULL DEFAULT '[]',
                    earlier_coverage_id TEXT NOT NULL DEFAULT '',
                    later_coverage_id TEXT NOT NULL DEFAULT '',
                    earlier_observed_years_json TEXT NOT NULL DEFAULT '[]',
                    later_observed_years_json TEXT NOT NULL DEFAULT '[]',
                    deprecation_evidence_anchor_ids_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS derived_conflict_unresolved (
                    record_id TEXT PRIMARY KEY REFERENCES derived_records(record_id),
                    issue_kind TEXT NOT NULL CHECK(issue_kind IN ('conflict', 'unresolved')),
                    subject TEXT NOT NULL,
                    competing_record_ids_json TEXT NOT NULL DEFAULT '[]',
                    reconciliation_state TEXT NOT NULL DEFAULT 'unresolved',
                    relevant_scopes_json TEXT NOT NULL DEFAULT '[]',
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    unresolved_questions_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS derived_record_terms (
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    term_role TEXT NOT NULL CHECK(term_role IN ('procedure_term', 'alternative')),
                    position INTEGER NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY(record_id, term_role, position)
                );
                CREATE TABLE IF NOT EXISTS candidate_validation_audits (
                    candidate_record_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES corpus_snapshots(snapshot_id),
                    outcome TEXT NOT NULL CHECK(outcome IN (
                        'affirmatively_supported', 'partially_supported', 'unsupported', 'ambiguous', 'needs_broader_context'
                    )),
                    audit TEXT NOT NULL CHECK(length(audit) BETWEEN 1 AND 280),
                    validated_record_id TEXT REFERENCES derived_records(record_id),
                    CHECK(
                        (outcome = 'affirmatively_supported' AND validated_record_id IS NOT NULL)
                        OR (outcome <> 'affirmatively_supported' AND validated_record_id IS NULL)
                    )
                );
                """
            )
            stored_scope = connection.execute(
                "SELECT value FROM settings WHERE key = 'runtime_scope'"
            ).fetchone()
            if stored_scope is None:
                connection.execute(
                    "INSERT INTO settings(key, value) VALUES ('runtime_scope', ?)",
                    (self.runtime_scope,),
                )
            elif stored_scope[0] != self.runtime_scope:
                raise ValueError("database runtime scope does not match requested runtime scope")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sources)")}
            if "modified_at" not in columns:
                connection.execute("ALTER TABLE sources ADD COLUMN modified_at REAL")
            derived_columns = {row[1] for row in connection.execute("PRAGMA table_info(derived_records)")}
            if "semantic_subtype" not in derived_columns:
                connection.execute(
                    "ALTER TABLE derived_records ADD COLUMN semantic_subtype TEXT NOT NULL DEFAULT ''"
                )
                connection.execute("UPDATE derived_records SET semantic_subtype = derived_kind")
                connection.execute(
                    """
                    UPDATE derived_records SET derived_kind = CASE
                        WHEN family = 'conflict_unresolved' THEN 'unresolved_or_conflicting'
                        WHEN evidence_state = 'raw_taught' THEN 'source_extracted_claim'
                        ELSE 'cross_source_synthesis'
                    END
                    """
                )
            if "finalized" not in derived_columns:
                connection.execute(
                    "ALTER TABLE derived_records ADD COLUMN finalized INTEGER NOT NULL DEFAULT 0 CHECK(finalized IN (0, 1))"
                )
            metric_columns = {row[1] for row in connection.execute("PRAGMA table_info(compilation_metrics)")}
            if "reasoning_tokens" not in metric_columns:
                connection.execute(
                    "ALTER TABLE compilation_metrics ADD COLUMN reasoning_tokens INTEGER NOT NULL DEFAULT 0 CHECK(reasoning_tokens >= 0)"
                )
            for column in ("compiler_model_version", "compiler_prompt_version", "compiler_schema_version"):
                if column not in derived_columns:
                    connection.execute(f"ALTER TABLE derived_records ADD COLUMN {column} TEXT")
            evolution_columns = {row[1] for row in connection.execute("PRAGMA table_info(derived_evolutions)")}
            for column, definition in (
                ("earlier_source_set_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("later_source_set_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("classification", "TEXT NOT NULL DEFAULT 'no_supported_classification'"),
                ("negative_evidence_state", "TEXT NOT NULL DEFAULT 'unresolved'"),
                ("competing_anchor_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("earlier_coverage_id", "TEXT NOT NULL DEFAULT ''"),
                ("later_coverage_id", "TEXT NOT NULL DEFAULT ''"),
                ("earlier_observed_years_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("later_observed_years_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("deprecation_evidence_anchor_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if column not in evolution_columns:
                    connection.execute(f"ALTER TABLE derived_evolutions ADD COLUMN {column} {definition}")
            conflict_columns = {row[1] for row in connection.execute("PRAGMA table_info(derived_conflict_unresolved)")}
            for column, definition in (
                ("competing_record_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("reconciliation_state", "TEXT NOT NULL DEFAULT 'unresolved'"),
                ("relevant_scopes_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("conditions_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("unresolved_questions_json", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if column not in conflict_columns:
                    connection.execute(f"ALTER TABLE derived_conflict_unresolved ADD COLUMN {column} {definition}")
            procedure_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(derived_procedure_sequence_hierarchy)"
                )
            }
            for column in ("prerequisites_json", "conditions_json", "branches_json"):
                if column not in procedure_columns:
                    connection.execute(
                        f"ALTER TABLE derived_procedure_sequence_hierarchy "
                        f"ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'"
                    )
            gate_columns = {row[1] for row in connection.execute("PRAGMA table_info(candidate_gates)")}
            for column, definition in (
                ("structural_version", "TEXT"),
                ("record_count", "INTEGER"),
                ("record_fingerprint", "TEXT"),
            ):
                if column not in gate_columns:
                    connection.execute(f"ALTER TABLE candidate_gates ADD COLUMN {column} {definition}")
            self._create_derived_record_triggers(connection)
            connection.execute("UPDATE derived_records SET finalized = 1 WHERE finalized = 0")
            self._backfill_display_turns(connection)

    def create_compilation_candidate(self, run: CompilationRun, snapshot: CorpusSnapshot) -> None:
        if run.status != "building" or snapshot.status != "building":
            raise ValueError("new compilation candidates must be building")
        if snapshot.run_id != run.run_id:
            raise ValueError("snapshot run_id does not match compilation run")
        if (
            snapshot.model_version,
            snapshot.prompt_version,
            snapshot.schema_version,
        ) != (run.model_version, run.prompt_version, run.schema_version):
            raise ValueError("snapshot versions do not match compilation run")
        try:
            expected_ids, expected_fingerprint, expected_snapshot_id = CorpusSnapshot.identity_for(
                snapshot.run_id,
                snapshot.selected_revision_ids,
                compiler_versions=(
                    snapshot.model_version,
                    snapshot.prompt_version,
                    snapshot.schema_version,
                ),
            )
        except ValueError as error:
            raise ValueError("snapshot identity is not canonical") from error
        if (
            snapshot.selected_revision_ids,
            snapshot.selected_revision_fingerprint,
            snapshot.snapshot_id,
        ) != (expected_ids, expected_fingerprint, expected_snapshot_id):
            raise ValueError("snapshot identity is not canonical")
        with self._connect() as connection:
            known_revisions = {
                row[0]
                for row in connection.execute(
                    "SELECT revision_id FROM source_revisions WHERE revision_id IN "
                    f"({','.join('?' for _ in snapshot.selected_revision_ids)})",
                    snapshot.selected_revision_ids,
                )
            }
            if known_revisions != set(snapshot.selected_revision_ids):
                raise ValueError("snapshot contains an unknown source revision")
            connection.execute(
                """
                INSERT INTO compilation_runs(
                    run_id, model_version, prompt_version, schema_version, started_at, status,
                    completed_at, failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.model_version,
                    run.prompt_version,
                    run.schema_version,
                    run.started_at,
                    run.status,
                    run.completed_at,
                    run.failure_reason,
                ),
            )
            connection.execute(
                """
                INSERT INTO corpus_snapshots(
                    snapshot_id, run_id, selected_revision_ids_json, selected_revision_fingerprint,
                    raw_store_id, derived_store_id, model_version, prompt_version, schema_version,
                    status, created_at, validated_at, published_at, failed_at, failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.run_id,
                    json.dumps(snapshot.selected_revision_ids),
                    snapshot.selected_revision_fingerprint,
                    snapshot.raw_store_id,
                    snapshot.derived_store_id,
                    snapshot.model_version,
                    snapshot.prompt_version,
                    snapshot.schema_version,
                    snapshot.status,
                    snapshot.created_at,
                    snapshot.validated_at,
                    snapshot.published_at,
                    snapshot.failed_at,
                    snapshot.failure_reason,
                ),
            )

    def record_candidate_store(self, snapshot_id: str, kind: str, store_id: str) -> CorpusSnapshot:
        column = {"raw": "raw_store_id", "derived": "derived_store_id"}.get(kind)
        if column is None or not isinstance(store_id, str) or not store_id.strip():
            raise ValueError("candidate store requires a valid kind and ID")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT status, {column} FROM corpus_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown snapshot")
            if row[0] != "building":
                raise ValueError("candidate stores require a building candidate")
            if row[1] not in (None, store_id):
                raise ValueError("candidate store identity is immutable")
            connection.execute(
                f"UPDATE corpus_snapshots SET {column} = ? WHERE snapshot_id = ? AND {column} IS NULL",
                (store_id, snapshot_id),
            )
        return self.snapshot(snapshot_id)

    def record_candidate_artifact_scope(self, snapshot_id: str, artifact_scope: str) -> None:
        if artifact_scope not in {"pilot", "production"}:
            raise ValueError("invalid candidate artifact scope")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM corpus_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown snapshot")
            if row[0] != "building":
                raise ValueError("candidate artifact scope requires a building candidate")
            existing = connection.execute(
                "SELECT artifact_scope FROM candidate_artifact_scopes WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if existing is not None and existing[0] != artifact_scope:
                raise ValueError("candidate artifact scope is immutable")
            connection.execute(
                "INSERT INTO candidate_artifact_scopes(snapshot_id, artifact_scope) VALUES (?, ?) "
                "ON CONFLICT(snapshot_id) DO NOTHING",
                (snapshot_id, artifact_scope),
            )

    def candidate_artifact_scope(self, snapshot_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT artifact_scope FROM candidate_artifact_scopes WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def begin_candidate_remote_operation(
        self,
        snapshot_id: str,
        stage: str,
        operation: str,
        *,
        store_id: str | None = None,
        file_id: str | None = None,
        attachment_id: str | None = None,
        batch_id: str | None = None,
    ) -> int:
        if stage not in {"remote_setup", "raw_store", "derived_store"}:
            raise ValueError("invalid candidate remote stage")
        if operation not in {
            "create_store", "create_batch", "batch_status",
            "upload_file", "attach_file", "attachment_status",
        }:
            raise ValueError("invalid candidate remote operation")
        identifiers = (store_id, file_id, attachment_id, batch_id)
        if any(value is not None and (not isinstance(value, str) or not value) for value in identifiers):
            raise ValueError("candidate remote identifiers must be non-empty strings")
        with self._connect() as connection:
            snapshot = connection.execute(
                "SELECT status FROM corpus_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            if snapshot is None or snapshot[0] != "building":
                raise ValueError("candidate remote operations require a building candidate")
            cursor = connection.execute(
                """
                INSERT INTO candidate_remote_operations(
                    snapshot_id, stage, operation, status,
                    store_id, file_id, attachment_id, batch_id
                ) VALUES (?, ?, ?, 'attempted', ?, ?, ?, ?)
                """,
                (snapshot_id, stage, operation, store_id, file_id, attachment_id, batch_id),
            )
            return int(cursor.lastrowid)

    def finish_candidate_remote_operation(
        self,
        operation_id: int,
        status: str,
        *,
        store_id: str | None = None,
        file_id: str | None = None,
        attachment_id: str | None = None,
        batch_id: str | None = None,
    ) -> CandidateRemoteOperation:
        if status not in {"succeeded", "failed"}:
            raise ValueError("candidate remote operation requires a terminal status")
        values = (store_id, file_id, attachment_id, batch_id)
        if any(value is not None and (not isinstance(value, str) or not value) for value in values):
            raise ValueError("candidate remote identifiers must be non-empty strings")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT store_id, file_id, attachment_id, batch_id, status
                FROM candidate_remote_operations WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown candidate remote operation")
            if row[4] != "attempted":
                raise ValueError("candidate remote operation is already complete")
            merged = tuple(new if new is not None else old for old, new in zip(row[:4], values))
            if any(old is not None and new is not None and old != new for old, new in zip(row[:4], values)):
                raise ValueError("candidate remote operation identity is immutable")
            connection.execute(
                """
                UPDATE candidate_remote_operations
                SET status = ?, store_id = ?, file_id = ?, attachment_id = ?, batch_id = ?
                WHERE operation_id = ?
                """,
                (status, *merged, operation_id),
            )
            updated = connection.execute(
                """
                SELECT operation_id, snapshot_id, stage, operation, status,
                       store_id, file_id, attachment_id, batch_id
                FROM candidate_remote_operations WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        return CandidateRemoteOperation(*updated)

    def candidate_remote_operations(self, snapshot_id: str) -> tuple[CandidateRemoteOperation, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT operation_id, snapshot_id, stage, operation, status,
                       store_id, file_id, attachment_id, batch_id
                FROM candidate_remote_operations
                WHERE snapshot_id = ? ORDER BY operation_id
                """,
                (snapshot_id,),
            ).fetchall()
        return tuple(CandidateRemoteOperation(*row) for row in rows)

    def transition_snapshot(
        self,
        snapshot_id: str,
        status: str,
        *,
        failure_reason: str | None = None,
        transitioned_at: float | None = None,
    ) -> CorpusSnapshot:
        transitioned_at = time.time() if transitioned_at is None else transitioned_at
        with self._connect() as connection:
            row = connection.execute(
                _SNAPSHOT_QUERY + " WHERE corpus_snapshots.snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown snapshot")
            snapshot = _snapshot_from_row(row)
            allowed = {
                "building": {"validating"},
                "validating": {"published", "failed"},
                "failed": set(),
                "published": set(),
                "archived": set(),
            }
            if status not in allowed[snapshot.status]:
                raise ValueError(f"cannot transition snapshot from {snapshot.status} to {status}")
            if status == "failed" and not failure_reason:
                raise ValueError("failed snapshots require a failure_reason")
            if status == "validating":
                self._dependency_graph(connection, snapshot_id).assert_acyclic()
            if status in {"validating", "published"} and connection.execute(
                "SELECT 1 FROM derived_record_staleness WHERE snapshot_id = ? LIMIT 1", (snapshot_id,)
            ).fetchone():
                raise ValueError("stale derived records cannot pass candidate validation")
            if status == "published" and (not snapshot.raw_store_id or not snapshot.derived_store_id):
                raise ValueError("published snapshots require raw and derived store IDs")
            if status == "published":
                artifact_scope = connection.execute(
                    "SELECT artifact_scope FROM candidate_artifact_scopes WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()
                artifact_scope = None if artifact_scope is None else artifact_scope[0]
                if artifact_scope != self.runtime_scope and not (
                    artifact_scope is None and self.runtime_scope == PRODUCTION_RUNTIME_SCOPE
                ):
                    raise ValueError("candidate artifact scope does not match runtime scope")
                self._require_passing_candidate_gate(connection, snapshot_id)
            if status == "published" and connection.execute(
                "SELECT 1 FROM derived_records WHERE snapshot_id = ? AND validation_state <> 'validated' LIMIT 1",
                (snapshot_id,),
            ).fetchone():
                raise ValueError("published snapshots require validated derived records")

            connection.execute(
                """
                UPDATE corpus_snapshots
                SET status = ?, validated_at = ?, published_at = ?, failed_at = ?, failure_reason = ?
                WHERE snapshot_id = ?
                """,
                (
                    status,
                    transitioned_at if status == "validating" else snapshot.validated_at,
                    transitioned_at if status == "published" else snapshot.published_at,
                    transitioned_at if status == "failed" else snapshot.failed_at,
                    failure_reason,
                    snapshot_id,
                ),
            )
            connection.execute(
                """
                UPDATE compilation_runs
                SET status = ?, completed_at = ?, failure_reason = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    transitioned_at if status in {"published", "failed"} else None,
                    failure_reason,
                    snapshot.run_id,
                ),
            )
            if status == "published":
                previous = connection.execute(
                    "SELECT value FROM settings WHERE key = 'current_snapshot_id'"
                ).fetchone()
                if previous is not None and previous[0] != snapshot_id:
                    connection.execute(
                        "INSERT INTO archived_snapshots(snapshot_id, archived_at) VALUES (?, ?) "
                        "ON CONFLICT(snapshot_id) DO NOTHING",
                        (previous[0], transitioned_at),
                    )
                selected_revision_ids = tuple(snapshot.selected_revision_ids)
                selected_rows = connection.execute(
                    f"""SELECT revision_id, source_id, lifecycle_state
                        FROM source_revisions WHERE revision_id IN ({','.join('?' for _ in selected_revision_ids)})""",
                    selected_revision_ids,
                ).fetchall()
                for revision_id, source_id, lifecycle_state in selected_rows:
                    if lifecycle_state == "replacement_pending":
                        connection.execute(
                            """UPDATE source_revisions SET lifecycle_state = 'superseded'
                               WHERE source_id = ? AND lifecycle_state = 'active' AND revision_id <> ?""",
                            (source_id, revision_id),
                        )
                        connection.execute(
                            """UPDATE source_revisions SET lifecycle_state = 'active'
                               WHERE revision_id = ? AND lifecycle_state = 'replacement_pending'""",
                            (revision_id,),
                        )
                        connection.execute("DELETE FROM source_changes WHERE source_id = ?", (source_id,))
                for key, value in (
                    ("current_snapshot_id", snapshot_id),
                    ("active_raw_store_id", snapshot.raw_store_id),
                    ("active_derived_store_id", snapshot.derived_store_id),
                ):
                    connection.execute(
                        "INSERT INTO settings(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, value),
                    )
        return self.snapshot(snapshot_id)

    def snapshot(self, snapshot_id: str) -> CorpusSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                _SNAPSHOT_QUERY + " WHERE corpus_snapshots.snapshot_id = ?", (snapshot_id,)
            ).fetchone()
        return None if row is None else _snapshot_from_row(row)

    def current_snapshot(self) -> CorpusSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                _SNAPSHOT_QUERY
                + " LEFT JOIN candidate_artifact_scopes AS current_scope"
                " ON current_scope.snapshot_id = corpus_snapshots.snapshot_id"
                + " JOIN settings AS current_pointer ON current_pointer.key = 'current_snapshot_id'"
                " AND current_pointer.value = corpus_snapshots.snapshot_id"
                " JOIN settings AS raw_pointer ON raw_pointer.key = 'active_raw_store_id'"
                " AND raw_pointer.value = corpus_snapshots.raw_store_id"
                " JOIN settings AS derived_pointer ON derived_pointer.key = 'active_derived_store_id'"
                " AND derived_pointer.value = corpus_snapshots.derived_store_id"
                " WHERE corpus_snapshots.status = 'published' AND archived_snapshots.snapshot_id IS NULL"
                " AND (current_scope.artifact_scope = ?"
                " OR (current_scope.artifact_scope IS NULL AND ? = 'production'))",
                (self.runtime_scope, self.runtime_scope),
            ).fetchone()
        return None if row is None else _snapshot_from_row(row)

    def snapshots(self) -> list[CorpusSnapshot]:
        with self._connect() as connection:
            rows = connection.execute(
                _SNAPSHOT_QUERY + " ORDER BY corpus_snapshots.created_at DESC, corpus_snapshots.snapshot_id"
            ).fetchall()
        return [_snapshot_from_row(row) for row in rows]

    def collections(self) -> list[Collection]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT collection_id, display_name, domain, enabled, scope FROM collections ORDER BY collection_id"
            ).fetchall()
        return [Collection(row[0], row[1], row[2], bool(row[3]), row[4]) for row in rows]

    def compilation_run(self, run_id: str) -> CompilationRun | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT run_id, model_version, prompt_version, schema_version, started_at, status,
                       completed_at, failure_reason
                FROM compilation_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return None if row is None else CompilationRun(*row)

    def record_compilation_metric(self, run_id: str, metric: CompilationMetric) -> CompilationMetric:
        values = (
            metric.source_count,
            metric.record_count,
            metric.call_count,
            metric.input_tokens,
            metric.output_tokens,
            metric.reasoning_tokens,
            metric.latency_ms,
            metric.cost_usd,
            metric.remote_calls,
            metric.failure_count,
        )
        if any(value < 0 for value in values):
            raise ValueError("compilation metrics cannot be negative")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT model_version, prompt_version, schema_version FROM compilation_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown compilation run")
            versions = tuple(row)
            supplied_versions = (metric.model_version, metric.prompt_version, metric.schema_version)
            if any(value is not None for value in supplied_versions) and supplied_versions != versions:
                raise ValueError("metric versions do not match compilation run")
            metric = replace(
                metric,
                model_version=versions[0],
                prompt_version=versions[1],
                schema_version=versions[2],
            )
            connection.execute(
                """
                INSERT INTO compilation_metrics(
                    run_id, stage, model_version, prompt_version, schema_version, source_count,
                    record_count, call_count, input_tokens, output_tokens, reasoning_tokens, latency_ms, cost_usd,
                    remote_calls, failure_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    metric.stage,
                    metric.model_version,
                    metric.prompt_version,
                    metric.schema_version,
                    metric.source_count,
                    metric.record_count,
                    metric.call_count,
                    metric.input_tokens,
                    metric.output_tokens,
                    metric.reasoning_tokens,
                    metric.latency_ms,
                    metric.cost_usd,
                    metric.remote_calls,
                    metric.failure_count,
                ),
            )
        return metric

    def compilation_metrics(self, run_id: str) -> list[CompilationMetric]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT stage, source_count, record_count, call_count, input_tokens, output_tokens,
                       latency_ms, cost_usd, remote_calls, failure_count, reasoning_tokens, model_version,
                       prompt_version, schema_version
                FROM compilation_metrics WHERE run_id = ? ORDER BY metric_id
                """,
                (run_id,),
            ).fetchall()
        return [CompilationMetric(*row) for row in rows]

    def record_candidate_gate(
        self,
        snapshot_id: str,
        results: tuple[SourceProcessingResult, ...],
        *,
        checked_at: float | None = None,
    ) -> CandidateGateResult:
        checked_at = time.time() if checked_at is None else checked_at
        with self._connect() as connection:
            status, selected_revision_ids = self._snapshot_state(connection, snapshot_id)
            if status != "building":
                raise ValueError("candidate gates require a building candidate")
            structure_version, record_count, record_fingerprint, structural_failure = self._candidate_gate_structure(
                connection, snapshot_id
            )
            failure_reason = self._candidate_gate_failure(connection, selected_revision_ids, results) or structural_failure
            connection.execute("DELETE FROM snapshot_source_coverage WHERE snapshot_id = ?", (snapshot_id,))
            connection.executemany(
                """
                INSERT INTO snapshot_source_coverage(snapshot_id, revision_id, status, record_count)
                VALUES (?, ?, ?, ?)
                """,
                [(snapshot_id, result.revision_id, result.status, result.record_count) for result in results],
            )
            status = "failed" if failure_reason else "passed"
            connection.execute(
                """
                INSERT INTO candidate_gates(
                    snapshot_id, status, checked_at, failure_reason,
                    structural_version, record_count, record_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    status = excluded.status,
                    checked_at = excluded.checked_at,
                    failure_reason = excluded.failure_reason,
                    structural_version = excluded.structural_version,
                    record_count = excluded.record_count,
                    record_fingerprint = excluded.record_fingerprint
                """,
                (
                    snapshot_id,
                    status,
                    checked_at,
                    failure_reason,
                    structure_version,
                    record_count,
                    record_fingerprint,
                ),
            )
        return CandidateGateResult(snapshot_id, status, checked_at, failure_reason)

    def candidate_gate(self, snapshot_id: str) -> CandidateGateResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_id, status, checked_at, failure_reason FROM candidate_gates WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        return None if row is None else CandidateGateResult(*row)

    def snapshot_source_coverage(self, snapshot_id: str) -> list[SourceProcessingResult]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT revision_id, status, record_count FROM snapshot_source_coverage
                WHERE snapshot_id = ? ORDER BY revision_id
                """,
                (snapshot_id,),
            ).fetchall()
        return [SourceProcessingResult(*row) for row in rows]

    def _candidate_gate_failure(
        self,
        connection: sqlite3.Connection,
        selected_revision_ids: set[str],
        results: tuple[SourceProcessingResult, ...],
    ) -> str | None:
        if not all(
            isinstance(result, SourceProcessingResult)
            and isinstance(result.revision_id, str)
            and result.revision_id
            and result.status in {"processed", "failed"}
            and isinstance(result.record_count, int)
            and not isinstance(result.record_count, bool)
            and result.record_count >= 0
            for result in results
        ):
            raise ValueError("invalid source processing result")
        revision_ids = [result.revision_id for result in results]
        if len(set(revision_ids)) != len(revision_ids):
            return "coverage contains duplicate revision results"
        if set(revision_ids) != selected_revision_ids:
            return "coverage does not include every selected revision"
        if any(result.status != "processed" for result in results):
            return "coverage includes a failed revision"
        placeholders = ",".join("?" for _ in selected_revision_ids)
        duplicate = connection.execute(
            "SELECT source_id FROM source_revisions WHERE revision_id IN "
            f"({placeholders}) GROUP BY source_id HAVING COUNT(*) > 1",
            tuple(sorted(selected_revision_ids)),
        ).fetchone()
        if duplicate is not None:
            return "candidate selects duplicate revisions for one source"
        return None

    def _candidate_gate_structure(
        self, connection: sqlite3.Connection, snapshot_id: str
    ) -> tuple[str, int, str, str | None]:
        rows = connection.execute(
            """
            SELECT record_id, finalized, validation_state
            FROM derived_records WHERE snapshot_id = ? ORDER BY record_id
            """,
            (snapshot_id,),
        ).fetchall()
        record_ids = tuple(row[0] for row in rows)
        fingerprint = sha256("\n".join(record_ids).encode()).hexdigest()
        if not rows:
            return _CANDIDATE_GATE_STRUCTURE_VERSION, 0, fingerprint, "candidate contains no validated derived records"
        if any(finalized != 1 or validation_state != "validated" for _, finalized, validation_state in rows):
            return _CANDIDATE_GATE_STRUCTURE_VERSION, len(rows), fingerprint, "candidate records must be finalized and validated"
        if connection.execute(
            "SELECT 1 FROM derived_record_staleness WHERE snapshot_id = ? LIMIT 1", (snapshot_id,)
        ).fetchone():
            return _CANDIDATE_GATE_STRUCTURE_VERSION, len(rows), fingerprint, "candidate records must not be stale"
        try:
            self._dependency_graph(connection, snapshot_id).assert_acyclic()
        except ValueError:
            return _CANDIDATE_GATE_STRUCTURE_VERSION, len(rows), fingerprint, "candidate records must be snapshot-owned"
        return _CANDIDATE_GATE_STRUCTURE_VERSION, len(rows), fingerprint, None

    def _require_passing_candidate_gate(self, connection: sqlite3.Connection, snapshot_id: str) -> None:
        row = connection.execute(
            """
            SELECT status, structural_version, record_count, record_fingerprint
            FROM candidate_gates WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None or row[0] != "passed":
            raise ValueError("published snapshots require a passing candidate gate")
        version, count, fingerprint, failure = self._candidate_gate_structure(connection, snapshot_id)
        if failure or (version, count, fingerprint) != row[1:]:
            raise ValueError("candidate gate no longer matches candidate content")

    def store_derived_record(self, record: DerivedRecord) -> None:
        validate_record(record)
        if isinstance(record, Claim) and record.evidence_state == "raw_taught" and record.compiler_provenance is not None:
            raise ValueError("source-extracted records require a semantic validation result before storage")
        self._store_derived_record(record)

    def _store_derived_record(self, record: DerivedRecord, connection: sqlite3.Connection | None = None) -> None:
        validate_record(record)
        if connection is None:
            with self._connect() as connection:
                self._store_derived_record(record, connection)
            return
        snapshot = connection.execute(
            """
            SELECT corpus_snapshots.status, archived_snapshots.snapshot_id
            FROM corpus_snapshots LEFT JOIN archived_snapshots USING(snapshot_id)
            WHERE corpus_snapshots.snapshot_id = ?
            """,
            (record.snapshot_id,),
        ).fetchone()
        if snapshot is None:
            raise ValueError("derived record references an unknown snapshot")
        if snapshot[0] != "building" or snapshot[1] is not None:
            raise ValueError("derived records require a building candidate")
        if connection.execute(
            "SELECT 1 FROM derived_records WHERE record_id = ? AND finalized = 1", (record.record_id,)
        ).fetchone() is not None:
            return
        self._assert_derived_dependencies_belong_to_snapshot(connection, record)
        if self._depends_on_stale_record(connection, record, reject_cross_snapshot=True):
            raise ValueError("derived records cannot depend on stale records")
        connection.execute(
            """
            INSERT INTO derived_records(
                record_id, snapshot_id, family, derived_kind, semantic_subtype, evidence_state, validation_state, lifecycle_state, qualification,
                compiler_model_version, compiler_prompt_version, compiler_schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO NOTHING
            """,
            (
                record.record_id,
                record.snapshot_id,
                record.family,
                record.derived_kind,
                record.semantic_subtype,
                record.evidence_state,
                record.validation_state,
                record.lifecycle_state,
                record.qualification,
                record.compiler_provenance.model_version if record.compiler_provenance else None,
                record.compiler_provenance.prompt_version if record.compiler_provenance else None,
                record.compiler_provenance.schema_version if record.compiler_provenance else None,
            ),
        )
        connection.executemany(
            "INSERT INTO derived_record_anchors(record_id, position, anchor_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            [(record.record_id, position, anchor) for position, anchor in enumerate(record.anchors)],
        )
        connection.executemany(
            """
            INSERT INTO derived_record_dependencies(record_id, position, dependency_kind, dependency_id)
            VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
            """,
            [
                (record.record_id, position, dependency.kind, dependency.identifier)
                for position, dependency in enumerate(record.dependencies)
            ],
        )
        connection.executemany(
            """
            INSERT INTO derived_record_facets(record_id, position, facet_name, facet_value)
            VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
            """,
            [(record.record_id, position, facet.name, facet.value) for position, facet in enumerate(record.facets)],
        )
        self._store_record_family(connection, record)
        connection.execute(
            "UPDATE derived_records SET finalized = 1 WHERE record_id = ? AND finalized = 0", (record.record_id,)
        )

    def _assert_derived_dependencies_belong_to_snapshot(
        self, connection: sqlite3.Connection, record: DerivedRecord
    ) -> None:
        pending = [dependency.identifier for dependency in record.dependencies if dependency.kind == "derived_record"]
        visited: set[str] = set()
        while pending:
            record_id = pending.pop()
            if record_id in visited:
                continue
            visited.add(record_id)
            owner = connection.execute(
                "SELECT snapshot_id FROM derived_records WHERE record_id = ? AND finalized = 1", (record_id,)
            ).fetchone()
            if owner is None:
                continue
            if owner[0] != record.snapshot_id:
                raise ValueError("derived record dependency belongs to a different snapshot")
            pending.extend(
                dependency_id
                for dependency_kind, dependency_id in connection.execute(
                    """
                    SELECT dependency_kind, dependency_id FROM derived_record_dependencies
                    WHERE record_id = ? ORDER BY position
                    """,
                    (record_id,),
                )
                if dependency_kind == "derived_record"
            )

    def _depends_on_stale_record(
        self, connection: sqlite3.Connection, record: DerivedRecord, *, reject_cross_snapshot: bool
    ) -> bool:
        if connection.execute(
            "SELECT 1 FROM derived_record_staleness WHERE snapshot_id = ? AND record_id = ? LIMIT 1",
            (record.snapshot_id, record.record_id),
        ).fetchone():
            return True
        pending = [dependency.identifier for dependency in record.dependencies if dependency.kind == "derived_record"]
        visited: set[str] = set()
        while pending:
            record_id = pending.pop()
            if record_id in visited:
                continue
            visited.add(record_id)
            owner = connection.execute(
                "SELECT snapshot_id FROM derived_records WHERE record_id = ? AND finalized = 1", (record_id,)
            ).fetchone()
            if owner is None:
                continue
            owner_snapshot_id = owner[0]
            if owner_snapshot_id != record.snapshot_id:
                if reject_cross_snapshot:
                    raise ValueError("derived record dependency belongs to a different snapshot")
                return True
            if connection.execute(
                """
                SELECT 1 FROM derived_record_staleness
                WHERE snapshot_id = ? AND record_id = ? LIMIT 1
                """,
                (owner_snapshot_id, record_id),
            ).fetchone():
                return True
            pending.extend(
                dependency_id
                for dependency_kind, dependency_id in connection.execute(
                    """
                    SELECT dependency_kind, dependency_id FROM derived_record_dependencies
                    WHERE record_id = ? ORDER BY position
                    """,
                    (record_id,),
                )
                if dependency_kind == "derived_record"
            )
        return False

    def validate_and_store_source_extracted(
        self,
        *,
        client: object,
        candidate: DerivedRecord,
        revision: SourceRevision,
        transcript: str,
        anchors: Mapping[str, SourceAnchor],
        model: str = "synthetic-validator",
        live_mode: bool = False,
        pricing=None,
        concept_hints: tuple[ConceptHint, ...] = (),
    ) -> ValidationResult:
        result = SemanticValidator(client, model=model, live_mode=live_mode, pricing=pricing).validate(
            candidate=candidate,
            revision=revision,
            transcript=transcript,
            anchors=anchors,
            concept_hints=concept_hints,
        )
        if result.outcome == "affirmatively_supported":
            if not isinstance(result.source_extracted, (Claim, Relationship, ProcedureSequenceHierarchy)):
                raise ValueError("affirmative semantic validation requires a validated source-extracted record")
            if (
                result.source_extracted.snapshot_id != result.snapshot_id
                or result.source_extracted.record_id == result.candidate_record_id
                or result.source_extracted.validation_state != "validated"
                or result.source_extracted.compiler_provenance is None
            ):
                raise ValueError("validation result snapshot does not match its source-extracted record")
        elif result.source_extracted is not None:
            raise ValueError("nonaffirmative semantic validation cannot retain a source-extracted record")
        if not result.candidate_record_id or not result.snapshot_id or not result.audit.strip() or len(result.audit) > 280:
            raise ValueError("semantic validation audit is invalid")
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM corpus_snapshots WHERE snapshot_id = ?", (result.snapshot_id,)).fetchone() is None:
                raise ValueError("validation result references an unknown snapshot")
            if result.source_extracted:
                self._store_derived_record(result.source_extracted, connection)
            connection.execute(
                """
                INSERT INTO candidate_validation_audits(
                    candidate_record_id, snapshot_id, outcome, audit, validated_record_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    result.candidate_record_id,
                    result.snapshot_id,
                    result.outcome,
                    result.audit,
                    result.source_extracted.record_id if result.source_extracted else None,
                ),
            )
        return result

    def validation_audits(self, snapshot_id: str) -> list[tuple[str, str, str, str | None]]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT candidate_record_id, outcome, audit, validated_record_id
                FROM candidate_validation_audits WHERE snapshot_id = ? ORDER BY candidate_record_id
                """,
                (snapshot_id,),
            ).fetchall()

    def validation_audit_counts(self, snapshot_id: str) -> dict[str, int]:
        """Return audit-visible record outcomes; rejected candidates are excluded, not unresolved."""
        outcomes = {
            "affirmatively_supported": 0,
            "partially_supported": 0,
            "ambiguous": 0,
            "unsupported": 0,
            "needs_broader_context": 0,
        }
        with self._connect() as connection:
            outcomes.update(dict(connection.execute(
                """
                SELECT outcome, COUNT(*) FROM candidate_validation_audits
                WHERE snapshot_id = ? GROUP BY outcome
                """,
                (snapshot_id,),
            ).fetchall()))
            extracted = connection.execute(
                """
                SELECT compilation_metrics.record_count
                FROM corpus_snapshots JOIN compilation_metrics
                    ON compilation_metrics.run_id = corpus_snapshots.run_id
                WHERE corpus_snapshots.snapshot_id = ? AND compilation_metrics.stage = 'extraction'
                """,
                (snapshot_id,),
            ).fetchone()
        excluded = sum(count for outcome, count in outcomes.items() if outcome != "affirmatively_supported")
        return {
            "extracted": sum(outcomes.values()) if extracted is None else extracted[0],
            "affirmative": outcomes["affirmatively_supported"],
            "partial": outcomes["partially_supported"],
            "ambiguous": outcomes["ambiguous"],
            "unsupported": outcomes["unsupported"],
            "needs_broader_context": outcomes["needs_broader_context"],
            "excluded": excluded,
            "unresolved": 0,
        }

    def dependency_graph(self, snapshot_id: str) -> DependencyGraph:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM corpus_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone() is None:
                raise ValueError("unknown snapshot")
            return self._dependency_graph(connection, snapshot_id)

    def _dependency_graph(self, connection: sqlite3.Connection, snapshot_id: str) -> DependencyGraph:
        _status, selected_revision_ids = self._snapshot_state(connection, snapshot_id)
        candidate_record_ids = {
            row[0]
            for row in connection.execute(
                "SELECT record_id FROM derived_records WHERE snapshot_id = ? AND finalized = 1", (snapshot_id,)
            )
        }
        rows = connection.execute(
            """
            SELECT dependency_kind, dependency_id, record_id
            FROM derived_record_dependencies
            WHERE record_id IN (
                SELECT record_id FROM derived_records WHERE snapshot_id = ? AND finalized = 1
            )
            ORDER BY dependency_kind, dependency_id, record_id
            """,
            (snapshot_id,),
        ).fetchall()
        for kind, identifier, _record_id in rows:
            if kind == "source_revision" and identifier not in selected_revision_ids:
                raise ValueError("derived record dependency is outside the candidate raw snapshot")
            if kind == "derived_record" and identifier not in candidate_record_ids:
                raise ValueError("derived record dependency is outside the candidate snapshot")
        return DependencyGraph(
            DependencyEdge(DependencyNode(kind, identifier), DependencyNode("derived_record", record_id))
            for kind, identifier, record_id in rows
        )

    def _snapshot_state(self, connection: sqlite3.Connection, snapshot_id: str) -> tuple[str, set[str]]:
        row = connection.execute(
            "SELECT status, selected_revision_ids_json FROM corpus_snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown snapshot")
        return row[0], set(json.loads(row[1]))

    @staticmethod
    def _require_selected_revisions(selected_revision_ids: set[str], revision_ids: tuple[str, ...]) -> None:
        if not set(revision_ids) <= selected_revision_ids:
            raise ValueError("revision IDs must be selected by the target snapshot")

    def mark_stale_for_revisions(self, snapshot_id: str, revision_ids: tuple[str, ...]) -> tuple[str, ...]:
        if not revision_ids or any(not isinstance(revision_id, str) or not revision_id for revision_id in revision_ids):
            raise ValueError("revision IDs must be non-empty")
        with self._connect() as connection:
            status, selected_revision_ids = self._snapshot_state(connection, snapshot_id)
            if status != "building":
                raise ValueError("only building candidates can be invalidated")
            self._require_selected_revisions(selected_revision_ids, revision_ids)
            graph = self._dependency_graph(connection, snapshot_id)
            stale_by_revision = {revision_id: graph.stale_record_ids((revision_id,)) for revision_id in revision_ids}
            stale_ids = tuple(sorted({record_id for record_ids in stale_by_revision.values() for record_id in record_ids}))
            connection.executemany(
                """
                INSERT INTO derived_record_staleness(snapshot_id, record_id, revision_id)
                VALUES (?, ?, ?) ON CONFLICT DO NOTHING
                """,
                [
                    (snapshot_id, record_id, revision_id)
                    for revision_id, record_ids in stale_by_revision.items()
                    for record_id in record_ids
                ],
            )
        return stale_ids

    def stale_record_ids(self, snapshot_id: str) -> tuple[str, ...]:
        with self._connect() as connection:
            return tuple(
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT record_id FROM derived_record_staleness WHERE snapshot_id = ? ORDER BY record_id",
                    (snapshot_id,),
                )
            )

    def rebuild_record_ids(self, snapshot_id: str, revision_ids: tuple[str, ...]) -> tuple[str, ...]:
        if not revision_ids or any(not isinstance(revision_id, str) or not revision_id for revision_id in revision_ids):
            raise ValueError("revision IDs must be non-empty")
        with self._connect() as connection:
            _status, selected_revision_ids = self._snapshot_state(connection, snapshot_id)
            self._require_selected_revisions(selected_revision_ids, revision_ids)
            return self._dependency_graph(connection, snapshot_id).rebuild_record_ids(revision_ids)

    def derived_records(self, snapshot_id: str, *, include_stale: bool = False) -> list[DerivedRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT record_id, snapshot_id, family, derived_kind, semantic_subtype, evidence_state, validation_state, lifecycle_state, qualification,
                       compiler_model_version, compiler_prompt_version, compiler_schema_version
                FROM derived_records WHERE snapshot_id = ? AND finalized = 1 ORDER BY record_id
                """,
                (snapshot_id,),
            ).fetchall()
            records = [self._derived_record_from_row(connection, row) for row in rows]
            if include_stale:
                return records
            return [
                record
                for record in records
                if not self._depends_on_stale_record(connection, record, reject_cross_snapshot=False)
            ]

    def clone_reusable_records(
        self,
        previous_snapshot_id: str,
        candidate_snapshot_id: str,
        *,
        selected_revision_ids: tuple[str, ...],
        stale_revision_ids: tuple[str, ...] = (),
    ) -> tuple[dict[str, str], tuple[DerivedRecord, ...]]:
        """Clone only the predecessor dependency closure unaffected by changed revisions."""
        selected = set(selected_revision_ids)
        stale = set(stale_revision_ids)
        previous = self.snapshot(previous_snapshot_id)
        candidate = self.snapshot(candidate_snapshot_id)
        if previous is None or previous.status not in {"published", "archived"}:
            raise ValueError("selective reuse requires a published predecessor")
        if candidate is None or candidate.status != "building":
            raise ValueError("selective reuse requires a building candidate")
        old_records = self.derived_records(previous_snapshot_id)
        mapping: dict[str, str] = {}
        cloned: list[DerivedRecord] = []
        pending = list(old_records)
        while pending:
            progressed = False
            for record in tuple(pending):
                source_dependencies = {
                    item.identifier for item in record.dependencies if item.kind == "source_revision"
                }
                derived_dependencies = {
                    item.identifier for item in record.dependencies if item.kind == "derived_record"
                }
                if source_dependencies & stale or not source_dependencies <= selected:
                    pending.remove(record)
                    progressed = True
                    continue
                if not derived_dependencies <= set(mapping):
                    continue
                clone = _clone_derived_record(
                    record,
                    snapshot_id=candidate_snapshot_id,
                    record_id_mapping=mapping,
                )
                with self._connect() as connection:
                    self._store_derived_record(clone, connection)
                    connection.execute(
                        """INSERT INTO derived_record_reuse(
                               snapshot_id, record_id, previous_snapshot_id, previous_record_id
                           ) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                        (candidate_snapshot_id, clone.record_id, previous_snapshot_id, record.record_id),
                    )
                mapping[record.record_id] = clone.record_id
                cloned.append(clone)
                pending.remove(record)
                progressed = True
            if not progressed:
                break
        return mapping, tuple(cloned)

    def derived_record_reuse(self, snapshot_id: str) -> dict[str, tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT record_id, previous_snapshot_id, previous_record_id
                   FROM derived_record_reuse WHERE snapshot_id = ? ORDER BY record_id""",
                (snapshot_id,),
            ).fetchall()
        return {
            str(record_id): (str(previous_snapshot_id), str(previous_record_id))
            for record_id, previous_snapshot_id, previous_record_id in rows
        }

    def store_orientation_concept_ids(
        self,
        snapshot_id: str,
        record_concept_ids: Mapping[str, str | tuple[str, ...]],
        *,
        concepts: tuple[Concept, ...],
        primary_concept_ids: Mapping[str, str] | None = None,
        concept_occurrences: tuple[ConceptOccurrence, ...] = (),
    ) -> None:
        """Seal candidate concepts and every explicit record occurrence association."""
        raw_entries = dict(record_concept_ids)
        entries = {
            record_id: (value,) if isinstance(value, str) else tuple(value)
            for record_id, value in raw_entries.items()
        }
        primary = dict(primary_concept_ids or {
            record_id: concept_ids[0] for record_id, concept_ids in entries.items() if concept_ids
        })
        if not entries or any(
            not isinstance(record_id, str)
            or not record_id
            or not concept_ids
            or len(set(concept_ids)) != len(concept_ids)
            or any(re.fullmatch(r"con_[0-9a-f]{64}", concept_id) is None for concept_id in concept_ids)
            for record_id, concept_ids in entries.items()
        ):
            raise ValueError("orientation concept links must use canonical record and concept IDs")
        if set(primary) != set(entries) or any(primary[record_id] not in entries[record_id] for record_id in entries):
            raise ValueError("orientation concept links require one explicit primary association")
        known_concepts: dict[str, Concept] = {}
        for concept in concepts:
            if not isinstance(concept, Concept) or concept.snapshot_id != snapshot_id:
                raise ValueError("orientation concept links require current candidate concepts")
            canonical = Concept.create(
                snapshot_id=concept.snapshot_id,
                canonical_label=concept.canonical_label,
                aliases=concept.aliases,
                scope=concept.scope,
                supporting_record_ids=concept.supporting_record_ids,
                supporting_anchor_ids=concept.supporting_anchor_ids,
            )
            if canonical != concept or concept.concept_id in known_concepts:
                raise ValueError("orientation concept links require canonical unique concepts")
            known_concepts[concept.concept_id] = concept
        if any(
            concept_id not in known_concepts
            or record_id not in known_concepts[concept_id].supporting_record_ids
            for record_id, concept_ids in entries.items()
            for concept_id in concept_ids
        ):
            raise ValueError("orientation concept links must be supported by their candidate concepts")
        if concept_occurrences and (
            any(
                not isinstance(occurrence, ConceptOccurrence)
                or occurrence.record_id not in entries
                or occurrence.concept_id not in entries[occurrence.record_id]
                for occurrence in concept_occurrences
            )
            or {occurrence.record_id for occurrence in concept_occurrences} != set(entries)
        ):
            raise ValueError("orientation concept occurrences must be candidate-owned")
        with self._connect() as connection:
            status, _selected_revision_ids = self._snapshot_state(connection, snapshot_id)
            if status != "building":
                raise ValueError("orientation concept links require a building candidate")
            for record_id, concept_ids in entries.items():
                row = connection.execute(
                    """
                    SELECT validation_state, lifecycle_state, finalized
                    FROM derived_records WHERE snapshot_id = ? AND record_id = ?
                    """,
                    (snapshot_id, record_id),
                ).fetchone()
                if row != ("validated", "active", 1):
                    raise ValueError("orientation concept links require validated active candidate records")
                existing = tuple(
                    row[0] for row in connection.execute(
                        """SELECT concept_id FROM derived_record_concept_links
                           WHERE snapshot_id = ? AND record_id = ? ORDER BY position""",
                        (snapshot_id, record_id),
                    )
                )
                if existing and existing != concept_ids:
                    raise ValueError("orientation concept links are immutable")
            for concept in known_concepts.values():
                connection.execute(
                    """INSERT INTO derived_concepts(snapshot_id, concept_id, canonical_label, scope)
                       VALUES (?, ?, ?, ?) ON CONFLICT(snapshot_id, concept_id) DO NOTHING""",
                    (snapshot_id, concept.concept_id, concept.canonical_label, concept.scope or ""),
                )
                connection.executemany(
                    """INSERT INTO derived_concept_aliases(snapshot_id, concept_id, position, alias)
                       VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                    [(snapshot_id, concept.concept_id, position, alias)
                     for position, alias in enumerate(concept.aliases)],
                )
                connection.executemany(
                    """INSERT INTO derived_concept_supporting_records(snapshot_id, concept_id, position, record_id)
                       VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                    [(snapshot_id, concept.concept_id, position, record_id)
                     for position, record_id in enumerate(concept.supporting_record_ids)],
                )
                connection.executemany(
                    """INSERT INTO derived_concept_supporting_anchors(snapshot_id, concept_id, position, anchor_id)
                       VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                    [(snapshot_id, concept.concept_id, position, anchor_id)
                     for position, anchor_id in enumerate(concept.supporting_anchor_ids)],
                )
            connection.executemany(
                """
                INSERT INTO derived_record_concepts(snapshot_id, record_id, concept_id)
                VALUES (?, ?, ?) ON CONFLICT(snapshot_id, record_id) DO NOTHING
                """,
                [(snapshot_id, record_id, primary[record_id]) for record_id in entries],
            )
            connection.executemany(
                """INSERT INTO derived_record_concept_links(
                       snapshot_id, record_id, concept_id, position, is_primary
                   ) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                [
                    (snapshot_id, record_id, concept_id, position, int(concept_id == primary[record_id]))
                    for record_id, concept_ids in entries.items()
                    for position, concept_id in enumerate(concept_ids)
                ],
            )
            connection.executemany(
                """INSERT INTO derived_concept_occurrences(
                       snapshot_id, record_id, position, role, term_position, label_key, scope, concept_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                [
                    (
                        snapshot_id, occurrence.record_id, position, occurrence.role,
                        occurrence.position, occurrence.label_key, occurrence.scope or "", occurrence.concept_id,
                    )
                    for position, occurrence in enumerate(concept_occurrences)
                ],
            )

    def orientation_concept_ids(self, snapshot_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT record_id, concept_id FROM derived_record_concepts
                WHERE snapshot_id = ? ORDER BY record_id
                """,
                (snapshot_id,),
            ).fetchall()
        return {str(record_id): str(concept_id) for record_id, concept_id in rows}

    def orientation_concept_links(self, snapshot_id: str) -> dict[str, tuple[str, ...]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT record_id, concept_id FROM derived_record_concept_links
                   WHERE snapshot_id = ? ORDER BY record_id, position""",
                (snapshot_id,),
            ).fetchall()
        result: dict[str, list[str]] = {}
        for record_id, concept_id in rows:
            result.setdefault(str(record_id), []).append(str(concept_id))
        return {record_id: tuple(concept_ids) for record_id, concept_ids in result.items()}

    def orientation_concepts(self, snapshot_id: str) -> tuple[Concept, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT concept_id, canonical_label, scope FROM derived_concepts
                   WHERE snapshot_id = ? ORDER BY concept_id""",
                (snapshot_id,),
            ).fetchall()
            concepts = []
            for concept_id, label, scope in rows:
                aliases = tuple(row[0] for row in connection.execute(
                    """SELECT alias FROM derived_concept_aliases
                       WHERE snapshot_id = ? AND concept_id = ? ORDER BY position""",
                    (snapshot_id, concept_id),
                ))
                records = tuple(row[0] for row in connection.execute(
                    """SELECT record_id FROM derived_concept_supporting_records
                       WHERE snapshot_id = ? AND concept_id = ? ORDER BY position""",
                    (snapshot_id, concept_id),
                ))
                anchors = tuple(row[0] for row in connection.execute(
                    """SELECT anchor_id FROM derived_concept_supporting_anchors
                       WHERE snapshot_id = ? AND concept_id = ? ORDER BY position""",
                    (snapshot_id, concept_id),
                ))
                concepts.append(Concept.create(
                    snapshot_id=snapshot_id, canonical_label=str(label), aliases=aliases,
                    scope=str(scope) or None, supporting_record_ids=records,
                    supporting_anchor_ids=anchors,
                ))
        return tuple(concepts)

    def orientation_concept_occurrences(self, snapshot_id: str) -> tuple[ConceptOccurrence, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT record_id, role, term_position, label_key, scope, concept_id
                   FROM derived_concept_occurrences WHERE snapshot_id = ? ORDER BY record_id, position""",
                (snapshot_id,),
            ).fetchall()
        return tuple(
            ConceptOccurrence(
                str(record_id), str(role), term_position, str(label_key), str(scope) or None, str(concept_id)
            )
            for record_id, role, term_position, label_key, scope, concept_id in rows
        )

    def orientation_source_area(
        self, snapshot_id: str, record: DerivedRecord
    ) -> tuple[str | None, int | None, str | None]:
        """Derive display-safe source context from local revision dependencies only."""
        revision_ids = tuple(
            dependency.identifier for dependency in record.dependencies if dependency.kind == "source_revision"
        )
        scope = next((facet.value for facet in record.facets if facet.name == "scope"), None)
        if not revision_ids:
            return None, None, scope
        placeholders = ",".join("?" for _ in revision_ids)
        with self._connect() as connection:
            owner = connection.execute(
                """
                SELECT 1 FROM derived_records
                WHERE snapshot_id = ? AND record_id = ? AND finalized = 1
                """,
                (snapshot_id, record.record_id),
            ).fetchone()
            if owner is None:
                return None, None, scope
            rows = connection.execute(
                f"""
                SELECT library_sources.collection_id, library_sources.year
                FROM source_revisions JOIN library_sources USING(source_id)
                WHERE source_revisions.revision_id IN ({placeholders})
                """,
                revision_ids,
            ).fetchall()
        if len(rows) != len(set(revision_ids)):
            return None, None, scope
        collections = {row[0] for row in rows}
        years = {row[1] for row in rows}
        return (
            next(iter(collections)) if len(collections) == 1 else None,
            next(iter(years)) if len(years) == 1 else None,
            scope,
        )

    def _store_record_family(self, connection: sqlite3.Connection, record: DerivedRecord) -> None:
        if isinstance(record, Claim):
            connection.execute(
                "INSERT INTO derived_claims(record_id, subject, predicate, object) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (record.record_id, record.subject, record.predicate, record.object),
            )
        elif isinstance(record, Relationship):
            connection.execute(
                """
                INSERT INTO derived_relationships(record_id, left_term, relation, right_term)
                VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
                """,
                (record.record_id, record.left, record.relation, record.right),
            )
        elif isinstance(record, ProcedureSequenceHierarchy):
            connection.execute(
                """
                INSERT INTO derived_procedure_sequence_hierarchy(
                    record_id, structure_kind, prerequisites_json, conditions_json, branches_json
                ) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
                """,
                (
                    record.record_id,
                    record.kind,
                    json.dumps(record.prerequisites),
                    json.dumps(record.conditions),
                    json.dumps([
                        {"condition": branch.condition, "steps": list(branch.steps)}
                        for branch in record.branches
                    ]),
                ),
            )
            connection.executemany(
                """
                INSERT INTO derived_record_terms(record_id, term_role, position, value)
                VALUES (?, 'procedure_term', ?, ?) ON CONFLICT DO NOTHING
                """,
                [(record.record_id, position, term) for position, term in enumerate(record.terms)],
            )
        elif isinstance(record, Evolution):
            connection.execute(
                """
                INSERT INTO derived_evolutions(
                    record_id, subject, previous_value, current_value, earlier_source_set_json,
                    later_source_set_json, classification, negative_evidence_state, competing_anchor_ids_json,
                    earlier_coverage_id, later_coverage_id, earlier_observed_years_json,
                    later_observed_years_json, deprecation_evidence_anchor_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
                """,
                (
                    record.record_id,
                    record.subject,
                    record.previous,
                    record.current,
                    json.dumps(record.earlier_source_set),
                    json.dumps(record.later_source_set),
                    record.classification,
                    record.negative_evidence_state,
                    json.dumps(record.competing_anchors),
                    record.earlier_coverage_id,
                    record.later_coverage_id,
                    json.dumps(record.earlier_observed_years),
                    json.dumps(record.later_observed_years),
                    json.dumps(record.deprecation_evidence_anchors),
                ),
            )
        elif isinstance(record, ConflictUnresolved):
            connection.execute(
                """
                INSERT INTO derived_conflict_unresolved(
                    record_id, issue_kind, subject, competing_record_ids_json, reconciliation_state
                    , relevant_scopes_json, conditions_json, unresolved_questions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
                """,
                (
                    record.record_id,
                    record.kind,
                    record.subject,
                    json.dumps(record.competing_record_ids),
                    record.reconciliation_state,
                    json.dumps(record.relevant_scopes),
                    json.dumps(record.conditions),
                    json.dumps(record.unresolved_questions),
                ),
            )
            connection.executemany(
                """
                INSERT INTO derived_record_terms(record_id, term_role, position, value)
                VALUES (?, 'alternative', ?, ?) ON CONFLICT DO NOTHING
                """,
                [(record.record_id, position, alternative) for position, alternative in enumerate(record.alternatives)],
            )

    def _derived_record_from_row(self, connection: sqlite3.Connection, row: tuple) -> DerivedRecord:
        record_id, snapshot_id, family, derived_kind, semantic_subtype, evidence_state, validation_state, lifecycle_state, qualification = (
            _decode_sqlite_text(value) for value in row[:9]
        )
        semantic_subtype = semantic_subtype or derived_kind
        if derived_kind not in {
            "source_extracted_claim", "cross_source_synthesis", "unresolved_or_conflicting"
        }:
            derived_kind = (
                "unresolved_or_conflicting"
                if family == "conflict_unresolved"
                else "source_extracted_claim" if evidence_state == "raw_taught" else "cross_source_synthesis"
            )
        provenance_values = row[9:]
        if any(value is None for value in provenance_values):
            if any(value is not None for value in provenance_values):
                raise ValueError("stored compiler provenance is incomplete")
            compiler_provenance = None
        else:
            compiler_provenance = CompilerProvenance(*(_decode_sqlite_text(value) for value in provenance_values))
        common = {
            "snapshot_id": snapshot_id,
            "anchors": tuple(
                _decode_sqlite_text(item[0])
                for item in connection.execute(
                    "SELECT anchor_id FROM derived_record_anchors WHERE record_id = ? ORDER BY position", (record_id,)
                )
            ),
            "dependencies": tuple(
                RecordDependency(*(_decode_sqlite_text(value) for value in item))
                for item in connection.execute(
                    """
                    SELECT dependency_kind, dependency_id FROM derived_record_dependencies
                    WHERE record_id = ? ORDER BY position
                    """,
                    (record_id,),
                )
            ),
            "validation_state": validation_state,
            "lifecycle_state": lifecycle_state,
            "qualification": qualification,
            "derived_kind": derived_kind,
            "semantic_subtype": semantic_subtype,
            "evidence_state": evidence_state,
            "compiler_provenance": compiler_provenance,
            "facets": tuple(
                Facet(*(_decode_sqlite_text(value) for value in item))
                for item in connection.execute(
                    "SELECT facet_name, facet_value FROM derived_record_facets WHERE record_id = ? ORDER BY position", (record_id,)
                )
            ),
        }
        if family == "claim":
            values = connection.execute(
                "SELECT subject, predicate, object FROM derived_claims WHERE record_id = ?", (record_id,)
            ).fetchone()
            values = tuple(_decode_sqlite_text(value) for value in values)
            record = Claim.create(
                **common,
                subject=values[0],
                predicate=values[1],
                object=values[2],
            )
        elif family == "relationship":
            values = connection.execute(
                "SELECT left_term, relation, right_term FROM derived_relationships WHERE record_id = ?", (record_id,)
            ).fetchone()
            values = tuple(_decode_sqlite_text(value) for value in values)
            record = Relationship.create(**common, left=values[0], relation=values[1], right=values[2])
        elif family == "procedure_sequence_hierarchy":
            kind, prerequisites_json, conditions_json, branches_json = connection.execute(
                """SELECT structure_kind, prerequisites_json, conditions_json, branches_json
                   FROM derived_procedure_sequence_hierarchy WHERE record_id = ?""",
                (record_id,),
            ).fetchone()
            kind = _decode_sqlite_text(kind)
            prerequisites_json = _decode_sqlite_text(prerequisites_json)
            conditions_json = _decode_sqlite_text(conditions_json)
            branches_json = _decode_sqlite_text(branches_json)
            terms = tuple(
                _decode_sqlite_text(item[0])
                for item in connection.execute(
                    """
                    SELECT value FROM derived_record_terms
                    WHERE record_id = ? AND term_role = 'procedure_term' ORDER BY position
                    """,
                    (record_id,),
                )
            )
            prerequisites = tuple(json.loads(prerequisites_json))
            conditions = tuple(json.loads(conditions_json))
            branches = tuple(
                ProcedureRecordBranch(item["condition"], tuple(item["steps"]))
                for item in json.loads(branches_json)
            )
            record = ProcedureSequenceHierarchy.create(
                **common,
                kind=kind,
                terms=terms,
                prerequisites=prerequisites,
                conditions=conditions,
                branches=branches,
            )
            if record.record_id != record_id and not prerequisites and not conditions and not branches:
                record = ProcedureSequenceHierarchy(
                    record_id=record_id,
                    family="procedure_sequence_hierarchy",
                    **common,
                    kind=kind,
                    terms=terms,
                )
        elif family == "evolution":
            values = connection.execute(
                """
                SELECT subject, previous_value, current_value, earlier_source_set_json, later_source_set_json,
                       classification, negative_evidence_state, competing_anchor_ids_json, earlier_coverage_id,
                       later_coverage_id, earlier_observed_years_json, later_observed_years_json,
                       deprecation_evidence_anchor_ids_json
                FROM derived_evolutions WHERE record_id = ?
                """,
                (record_id,),
            ).fetchone()
            values = tuple(_decode_sqlite_text(value) for value in values)
            if not any(json.loads(values[index]) for index in (10, 11, 12)) and not values[8] and not values[9]:
                record = Evolution(
                    record_id=record_id,
                    family="evolution",
                    **common,
                    subject=values[0],
                    previous=values[1],
                    current=values[2],
                    earlier_source_set=tuple(json.loads(values[3])),
                    later_source_set=tuple(json.loads(values[4])),
                    classification=values[5],
                    negative_evidence_state=values[6],
                    competing_anchors=tuple(json.loads(values[7])),
                )
            else:
                record = Evolution.create(
                    **common,
                    subject=values[0],
                    previous=values[1],
                    current=values[2],
                    earlier_source_set=tuple(json.loads(values[3])),
                    later_source_set=tuple(json.loads(values[4])),
                    classification=values[5],
                    negative_evidence_state=values[6],
                    competing_anchors=tuple(json.loads(values[7])),
                    earlier_coverage_id=values[8],
                    later_coverage_id=values[9],
                    earlier_observed_years=tuple(json.loads(values[10])),
                    later_observed_years=tuple(json.loads(values[11])),
                    deprecation_evidence_anchors=tuple(json.loads(values[12])),
                )
        elif family == "conflict_unresolved":
            kind, subject, competing_record_ids_json, reconciliation_state, relevant_scopes_json, conditions_json, unresolved_questions_json = connection.execute(
                """
                SELECT issue_kind, subject, competing_record_ids_json, reconciliation_state, relevant_scopes_json,
                       conditions_json, unresolved_questions_json
                FROM derived_conflict_unresolved WHERE record_id = ?
                """,
                (record_id,),
            ).fetchone()
            kind, subject, competing_record_ids_json, reconciliation_state, relevant_scopes_json, conditions_json, unresolved_questions_json = (
                _decode_sqlite_text(kind),
                _decode_sqlite_text(subject),
                _decode_sqlite_text(competing_record_ids_json),
                _decode_sqlite_text(reconciliation_state),
                _decode_sqlite_text(relevant_scopes_json),
                _decode_sqlite_text(conditions_json),
                _decode_sqlite_text(unresolved_questions_json),
            )
            alternatives = tuple(
                _decode_sqlite_text(item[0])
                for item in connection.execute(
                    """
                    SELECT value FROM derived_record_terms
                    WHERE record_id = ? AND term_role = 'alternative' ORDER BY position
                    """,
                    (record_id,),
                )
            )
            if not any(json.loads(value) for value in (relevant_scopes_json, conditions_json, unresolved_questions_json)):
                record = ConflictUnresolved(
                    record_id=record_id,
                    family="conflict_unresolved",
                    **common,
                    kind=kind,
                    subject=subject,
                    alternatives=alternatives,
                    competing_record_ids=tuple(json.loads(competing_record_ids_json)),
                    reconciliation_state=reconciliation_state,
                )
            else:
                record = ConflictUnresolved.create(
                    **common,
                    kind=kind,
                    subject=subject,
                    alternatives=alternatives,
                    competing_record_ids=tuple(json.loads(competing_record_ids_json)),
                    reconciliation_state=reconciliation_state,
                    relevant_scopes=tuple(json.loads(relevant_scopes_json)),
                    conditions=tuple(json.loads(conditions_json)),
                    unresolved_questions=tuple(json.loads(unresolved_questions_json)),
                )
        else:
            raise ValueError("unknown derived record family")
        if record.record_id != record_id:
            raise ValueError("stored derived record identity is not canonical")
        return record

    def _derived_record_is_valid(self, connection: sqlite3.Connection, raw_record_id: object) -> int:
        try:
            if not isinstance(raw_record_id, bytes):
                return 0
            record_id = raw_record_id.decode("utf-8")
            row = connection.execute(
                """
                SELECT record_id, snapshot_id, family, derived_kind, semantic_subtype, evidence_state, validation_state, lifecycle_state, qualification,
                       compiler_model_version, compiler_prompt_version, compiler_schema_version
                FROM derived_records WHERE record_id = ?
                """,
                (record_id,),
            ).fetchone()
            self._derived_record_from_row(connection, row)
        except (IndexError, sqlite3.Error, TypeError, UnicodeError, ValueError):
            return 0
        return 1

    def _create_derived_record_triggers(self, connection: sqlite3.Connection) -> None:
        connection.execute("DROP TRIGGER IF EXISTS derived_records_require_building_snapshot")
        connection.execute("DROP TRIGGER IF EXISTS derived_records_updates_require_building_snapshot")
        connection.execute("DROP TRIGGER IF EXISTS derived_records_deletes_require_building_snapshot")
        connection.execute("DROP TRIGGER IF EXISTS derived_records_require_children")
        connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS derived_records_require_building_snapshot
            BEFORE INSERT ON derived_records
            WHEN NOT EXISTS(
                SELECT 1 FROM corpus_snapshots
                LEFT JOIN archived_snapshots USING(snapshot_id)
                WHERE corpus_snapshots.snapshot_id = NEW.snapshot_id
                  AND corpus_snapshots.status = 'building'
                  AND archived_snapshots.snapshot_id IS NULL
            )
            BEGIN
                SELECT RAISE(ABORT, 'derived records require a building candidate');
            END;
            CREATE TRIGGER IF NOT EXISTS derived_records_updates_require_building_snapshot
            BEFORE UPDATE ON derived_records
            WHEN NOT EXISTS(
                    SELECT 1 FROM corpus_snapshots
                    LEFT JOIN archived_snapshots USING(snapshot_id)
                    WHERE corpus_snapshots.snapshot_id = NEW.snapshot_id
                      AND corpus_snapshots.status = 'building'
                      AND archived_snapshots.snapshot_id IS NULL
                ) OR NOT EXISTS(
                    SELECT 1 FROM corpus_snapshots
                    LEFT JOIN archived_snapshots USING(snapshot_id)
                    WHERE corpus_snapshots.snapshot_id = OLD.snapshot_id
                      AND corpus_snapshots.status = 'building'
                      AND archived_snapshots.snapshot_id IS NULL
                )
            BEGIN
                SELECT RAISE(ABORT, 'derived records require a building candidate');
            END;
            CREATE TRIGGER IF NOT EXISTS derived_records_deletes_require_building_snapshot
            BEFORE DELETE ON derived_records
            WHEN NOT EXISTS(
                SELECT 1 FROM corpus_snapshots
                LEFT JOIN archived_snapshots USING(snapshot_id)
                WHERE corpus_snapshots.snapshot_id = OLD.snapshot_id
                  AND corpus_snapshots.status = 'building'
                  AND archived_snapshots.snapshot_id IS NULL
            )
            BEGIN
                SELECT RAISE(ABORT, 'derived records require a building candidate');
            END;
            CREATE TRIGGER IF NOT EXISTS derived_records_require_staging
            BEFORE INSERT ON derived_records WHEN NEW.finalized = 1
            BEGIN
                SELECT RAISE(ABORT, 'derived records must be finalized after their children');
            END;
            CREATE TRIGGER IF NOT EXISTS derived_records_require_children
            BEFORE UPDATE OF finalized ON derived_records WHEN OLD.finalized = 0 AND NEW.finalized = 1
            BEGIN
                SELECT CASE WHEN NOT EXISTS(
                    SELECT 1 FROM derived_record_anchors WHERE record_id = NEW.record_id
                ) THEN RAISE(ABORT, 'derived records require anchors') END;
                SELECT CASE WHEN NOT EXISTS(
                    SELECT 1 FROM derived_record_dependencies WHERE record_id = NEW.record_id
                ) THEN RAISE(ABORT, 'derived records require dependencies') END;
                SELECT CASE WHEN NOT (
                    (NEW.family = 'claim'
                        AND (SELECT COUNT(*) FROM derived_claims WHERE record_id = NEW.record_id) = 1
                        AND (SELECT COUNT(*) FROM derived_relationships WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_procedure_sequence_hierarchy WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_evolutions WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_conflict_unresolved WHERE record_id = NEW.record_id) = 0)
                    OR (NEW.family = 'relationship'
                        AND (SELECT COUNT(*) FROM derived_claims WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_relationships WHERE record_id = NEW.record_id) = 1
                        AND (SELECT COUNT(*) FROM derived_procedure_sequence_hierarchy WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_evolutions WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_conflict_unresolved WHERE record_id = NEW.record_id) = 0)
                    OR (NEW.family = 'procedure_sequence_hierarchy'
                        AND (SELECT COUNT(*) FROM derived_claims WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_relationships WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_procedure_sequence_hierarchy WHERE record_id = NEW.record_id) = 1
                        AND (SELECT COUNT(*) FROM derived_evolutions WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_conflict_unresolved WHERE record_id = NEW.record_id) = 0)
                    OR (NEW.family = 'evolution'
                        AND (SELECT COUNT(*) FROM derived_claims WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_relationships WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_procedure_sequence_hierarchy WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_evolutions WHERE record_id = NEW.record_id) = 1
                        AND (SELECT COUNT(*) FROM derived_conflict_unresolved WHERE record_id = NEW.record_id) = 0)
                    OR (NEW.family = 'conflict_unresolved'
                        AND (SELECT COUNT(*) FROM derived_claims WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_relationships WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_procedure_sequence_hierarchy WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_evolutions WHERE record_id = NEW.record_id) = 0
                        AND (SELECT COUNT(*) FROM derived_conflict_unresolved WHERE record_id = NEW.record_id) = 1)
                ) THEN RAISE(ABORT, 'derived records require one matching family row') END;
                SELECT CASE WHEN NEW.family = 'procedure_sequence_hierarchy' AND (
                    SELECT COUNT(*) FROM derived_record_terms WHERE record_id = NEW.record_id AND term_role = 'procedure_term'
                ) NOT BETWEEN 2 AND 8 THEN RAISE(ABORT, 'derived records require bounded procedure terms') END;
                SELECT CASE WHEN NEW.family = 'conflict_unresolved' AND (
                    SELECT COUNT(*) FROM derived_record_terms WHERE record_id = NEW.record_id AND term_role = 'alternative'
                ) NOT BETWEEN 2 AND 8 THEN RAISE(ABORT, 'derived records require bounded alternatives') END;
                SELECT CASE WHEN length(trim(NEW.qualification)) = 0 OR length(NEW.qualification) > 280
                    THEN RAISE(ABORT, 'derived records require concise qualification') END;
                SELECT CASE WHEN EXISTS(
                    SELECT 1 FROM derived_record_anchors
                    WHERE record_id = NEW.record_id AND length(trim(anchor_id)) = 0
                ) OR EXISTS(
                    SELECT 1 FROM derived_record_dependencies
                    WHERE record_id = NEW.record_id AND length(trim(dependency_id)) = 0
                ) THEN RAISE(ABORT, 'derived records require non-empty references') END;
                SELECT CASE WHEN (SELECT COUNT(*) FROM derived_record_facets WHERE record_id = NEW.record_id) > 5
                    OR EXISTS(
                        SELECT 1 FROM derived_record_facets
                        WHERE record_id = NEW.record_id AND (length(trim(facet_value)) = 0 OR length(facet_value) > 160)
                    ) OR (SELECT COUNT(DISTINCT facet_name) FROM derived_record_facets WHERE record_id = NEW.record_id)
                        != (SELECT COUNT(*) FROM derived_record_facets WHERE record_id = NEW.record_id)
                    THEN RAISE(ABORT, 'derived records require bounded unique facets') END;
                SELECT CASE WHEN NEW.family = 'claim' AND NOT EXISTS(
                    SELECT 1 FROM derived_claims
                    WHERE record_id = NEW.record_id
                      AND COALESCE(NULLIF(NEW.semantic_subtype, ''), NEW.derived_kind)
                          IN ('statement', 'definition', 'recommendation', 'strategy_implication')
                      AND length(trim(subject)) > 0 AND length(subject) <= 240
                      AND length(trim(predicate)) > 0 AND length(predicate) <= 240
                      AND length(trim(object)) > 0 AND length(object) <= 240
                ) THEN RAISE(ABORT, 'derived records require valid claim content') END;
                SELECT CASE WHEN NEW.family = 'relationship' AND NOT EXISTS(
                    SELECT 1 FROM derived_relationships
                    WHERE record_id = NEW.record_id
                      AND COALESCE(NULLIF(NEW.semantic_subtype, ''), NEW.derived_kind) = 'relation'
                      AND relation IN (
                          'supports', 'contrasts', 'depends_on', 'causes', 'applies_when',
                          'exception_to', 'refines', 'anticipates', 'uses_internal_structure'
                      )
                      AND length(trim(left_term)) > 0 AND length(left_term) <= 240
                      AND length(trim(right_term)) > 0 AND length(right_term) <= 240
                ) THEN RAISE(ABORT, 'derived records require valid relationship content') END;
                SELECT CASE WHEN NEW.family = 'procedure_sequence_hierarchy' AND NOT EXISTS(
                    SELECT 1 FROM derived_procedure_sequence_hierarchy
                    WHERE record_id = NEW.record_id
                      AND structure_kind = COALESCE(NULLIF(NEW.semantic_subtype, ''), NEW.derived_kind)
                ) OR NEW.family = 'procedure_sequence_hierarchy' AND EXISTS(
                    SELECT 1 FROM derived_record_terms
                    WHERE record_id = NEW.record_id AND term_role = 'procedure_term'
                      AND (length(trim(value)) = 0 OR length(value) > 240)
                ) THEN RAISE(ABORT, 'derived records require valid procedure content') END;
                SELECT CASE WHEN NEW.family = 'evolution' AND NOT EXISTS(
                    SELECT 1 FROM derived_evolutions
                    WHERE record_id = NEW.record_id
                      AND COALESCE(NULLIF(NEW.semantic_subtype, ''), NEW.derived_kind) = 'change'
                      AND length(trim(subject)) > 0 AND length(subject) <= 240
                      AND length(trim(previous_value)) > 0 AND length(previous_value) <= 240
                      AND length(trim(current_value)) > 0 AND length(current_value) <= 240
                ) THEN RAISE(ABORT, 'derived records require valid evolution content') END;
                SELECT CASE WHEN NEW.family = 'conflict_unresolved' AND NOT EXISTS(
                    SELECT 1 FROM derived_conflict_unresolved
                    WHERE record_id = NEW.record_id
                      AND issue_kind = COALESCE(NULLIF(NEW.semantic_subtype, ''), NEW.derived_kind)
                      AND length(trim(subject)) > 0 AND length(subject) <= 240
                ) OR NEW.family = 'conflict_unresolved' AND EXISTS(
                    SELECT 1 FROM derived_record_terms
                    WHERE record_id = NEW.record_id AND term_role = 'alternative'
                      AND (length(trim(value)) = 0 OR length(value) > 240)
                ) THEN RAISE(ABORT, 'derived records require valid conflict content') END;
                SELECT CASE WHEN derived_record_is_valid(CAST(NEW.record_id AS BLOB)) != 1
                    THEN RAISE(ABORT, 'derived records require a valid typed record') END;
            END;
            CREATE TRIGGER IF NOT EXISTS derived_records_lock_finalized_rows
            BEFORE UPDATE ON derived_records WHEN OLD.finalized = 1
            BEGIN
                SELECT RAISE(ABORT, 'finalized derived records are immutable');
            END;
            """
        )
        for table in (
            "derived_record_anchors",
            "derived_record_dependencies",
            "derived_record_facets",
            "derived_claims",
            "derived_relationships",
            "derived_procedure_sequence_hierarchy",
            "derived_evolutions",
            "derived_conflict_unresolved",
            "derived_record_terms",
        ):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                connection.execute(
                    f"DROP TRIGGER IF EXISTS {table}_require_building_{operation.lower()}"
                )
                record_check = {
                    "INSERT": "record_id = NEW.record_id",
                    "UPDATE": "(record_id = OLD.record_id OR record_id = NEW.record_id)",
                    "DELETE": "record_id = OLD.record_id",
                }[operation]
                connection.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_lock_finalized_{operation.lower()}
                    BEFORE {operation} ON {table}
                    WHEN EXISTS(SELECT 1 FROM derived_records WHERE {record_check} AND finalized = 1)
                    BEGIN
                        SELECT RAISE(ABORT, 'finalized derived records are immutable');
                    END;
                    """
                )
                connection.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_require_building_{operation.lower()}
                    BEFORE {operation} ON {table}
                    WHEN EXISTS(
                        SELECT 1 FROM derived_records
                        JOIN corpus_snapshots USING(snapshot_id)
                        LEFT JOIN archived_snapshots USING(snapshot_id)
                        WHERE {record_check}
                          AND (corpus_snapshots.status <> 'building' OR archived_snapshots.snapshot_id IS NOT NULL)
                    )
                    BEGIN
                        SELECT RAISE(ABORT, 'derived records require a building candidate');
                    END;
                    """
                )
        for table in (
            "derived_record_concepts",
            "derived_concepts",
            "derived_concept_aliases",
            "derived_concept_supporting_records",
            "derived_concept_supporting_anchors",
            "derived_record_concept_links",
            "derived_concept_occurrences",
            "derived_record_reuse",
        ):
            for operation, snapshot_expressions in (
                ("INSERT", ("NEW.snapshot_id",)),
                ("UPDATE", ("NEW.snapshot_id", "OLD.snapshot_id")),
                ("DELETE", ("OLD.snapshot_id",)),
            ):
                trigger = f"{table}_require_building_{operation.lower()}"
                connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                blocked = " OR ".join(
                    f"""NOT EXISTS(
                        SELECT 1 FROM corpus_snapshots
                        LEFT JOIN archived_snapshots USING(snapshot_id)
                        WHERE corpus_snapshots.snapshot_id = {snapshot_expression}
                          AND corpus_snapshots.status = 'building'
                          AND archived_snapshots.snapshot_id IS NULL
                    )"""
                    for snapshot_expression in snapshot_expressions
                )
                connection.execute(
                    f"""
                    CREATE TRIGGER {trigger}
                    BEFORE {operation} ON {table}
                    WHEN {blocked}
                    BEGIN
                        SELECT RAISE(ABORT, 'candidate knowledge requires a building snapshot');
                    END;
                    """
                )

    def store_collection(self, collection: Collection) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO collections(collection_id, display_name, domain, enabled, scope) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(collection_id) DO NOTHING",
                (
                    collection.collection_id,
                    collection.display_name,
                    collection.domain,
                    collection.enabled,
                    collection.scope,
                ),
            )

    def collection(self, collection_id: str) -> Collection | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT collection_id, display_name, domain, enabled, scope "
                "FROM collections WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        return None if row is None else Collection(*row[:3], bool(row[3]), row[4])

    def store_source(self, source: LibrarySource) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO library_sources(
                    source_id, collection_id, identity_key, source_type, author, course,
                    lesson_title, year, original_filename, local_provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO NOTHING
                """,
                (
                    source.source_id,
                    source.collection_id,
                    source.identity_key,
                    source.source_type,
                    source.author,
                    source.course,
                    source.lesson_title,
                    source.year,
                    source.original_filename,
                    source.local_provenance,
                ),
            )

    def library_source(self, source_id: str) -> LibrarySource | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_id, collection_id, identity_key, source_type, author, course,
                       lesson_title, year, original_filename, local_provenance
                FROM library_sources WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
        return None if row is None else LibrarySource(*row)

    def library_source_for_identity(
        self, collection_id: str, identity_key: str
    ) -> LibrarySource | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_id, collection_id, identity_key, source_type, author, course,
                       lesson_title, year, original_filename, local_provenance
                FROM library_sources WHERE collection_id = ? AND identity_key = ?
                """,
                (collection_id, identity_key),
            ).fetchone()
        return None if row is None else LibrarySource(*row)

    def store_source_revision(self, revision: SourceRevision) -> None:
        with self._connect() as connection:
            source = connection.execute(
                "SELECT collection_id FROM library_sources WHERE source_id = ?",
                (revision.source_id,),
            ).fetchone()
            if source is None or source[0] != revision.collection_id:
                raise ValueError("revision collection_id does not match stored source")
            connection.execute(
                """
                INSERT INTO source_revisions(
                    revision_id, source_id, content_sha256, byte_size, local_locator,
                    observed_at, lifecycle_state, remote_file_id, remote_vector_store_file_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(revision_id) DO NOTHING
                """,
                (
                    revision.revision_id,
                    revision.source_id,
                    revision.content_sha256,
                    revision.byte_size,
                    revision.local_locator,
                    revision.observed_at,
                    revision.lifecycle_state,
                    revision.remote_file_id,
                    revision.remote_vector_store_file_id,
                ),
            )

    def mark_source_revision_remote_ready(
        self, revision_id: str, *, remote_file_id: str, remote_vector_store_file_id: str | None = None
    ) -> SourceRevision:
        if (
            not isinstance(remote_file_id, str) or not remote_file_id.strip()
            or remote_vector_store_file_id is not None
            and (not isinstance(remote_vector_store_file_id, str) or not remote_vector_store_file_id.strip())
        ):
            raise ValueError("remote eligibility requires non-empty remote IDs")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT lifecycle_state, remote_file_id, remote_vector_store_file_id
                   FROM source_revisions WHERE revision_id = ?""",
                (revision_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown source revision")
            if row[0] != "replacement_pending":
                raise ValueError("only pending replacements can become remote eligible")
            if row[1] not in (None, remote_file_id) or row[2] not in (None, remote_vector_store_file_id):
                raise ValueError("source revision remote identity is immutable")
            connection.execute(
                """UPDATE source_revisions
                   SET remote_file_id = COALESCE(remote_file_id, ?),
                       remote_vector_store_file_id = COALESCE(remote_vector_store_file_id, ?)
                   WHERE revision_id = ?""",
                (remote_file_id, remote_vector_store_file_id, revision_id),
            )
        revision = self.source_revision(revision_id)
        if revision is None:
            raise ValueError("source revision disappeared")
        return revision

    def source_revision(self, revision_id: str) -> SourceRevision | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_revisions.revision_id, library_sources.collection_id,
                       source_revisions.source_id, source_revisions.content_sha256,
                       source_revisions.byte_size, source_revisions.local_locator,
                       source_revisions.observed_at, source_revisions.lifecycle_state,
                       source_revisions.remote_file_id, source_revisions.remote_vector_store_file_id
                FROM source_revisions JOIN library_sources USING(source_id)
                WHERE revision_id = ?
                """,
                (revision_id,),
            ).fetchone()
        return None if row is None else SourceRevision(*row)

    def store_source_anchors(self, anchors: tuple[SourceAnchor, ...]) -> None:
        """Persist immutable anchor metadata only; transcript text is never stored here."""
        if not anchors:
            return
        with self._connect() as connection:
            for anchor in anchors:
                if not isinstance(anchor, SourceAnchor):
                    raise ValueError("source anchors must be SourceAnchor values")
                revision = connection.execute(
                    """
                    SELECT library_sources.collection_id, source_revisions.source_id,
                           library_sources.identity_key, source_revisions.content_sha256
                    FROM source_revisions JOIN library_sources USING(source_id)
                    WHERE source_revisions.revision_id = ?
                    """,
                    (anchor.revision_id,),
                ).fetchone()
                if revision is None:
                    raise ValueError("source anchor references an unknown source revision")
                collection_id, source_id, identity_key, content_sha256 = revision
                if (anchor.collection_id, anchor.source_id, anchor.revision_sha256) != (
                    collection_id,
                    source_id,
                    content_sha256,
                ):
                    raise ValueError("source anchor does not match a stored source revision")
                _validate_anchor_identity(anchor, identity_key)
                values = (
                    anchor.anchor_id,
                    anchor.collection_id,
                    anchor.source_id,
                    anchor.revision_id,
                    anchor.revision_sha256,
                    anchor.start_offset,
                    anchor.end_offset,
                    anchor.timestamp_start_ms,
                    anchor.timestamp_end_ms,
                    anchor.span_fingerprint,
                    anchor.locator_version,
                )
                existing = connection.execute(
                    """
                    SELECT anchor_id, collection_id, source_id, revision_id, revision_sha256,
                           start_offset, end_offset, timestamp_start_ms, timestamp_end_ms,
                           span_fingerprint, locator_version
                    FROM source_anchors WHERE anchor_id = ?
                    """,
                    (anchor.anchor_id,),
                ).fetchone()
                if existing is not None and existing != values:
                    raise ValueError("stored source anchor is immutable")
                connection.execute(
                    """
                    INSERT INTO source_anchors(
                        anchor_id, collection_id, source_id, revision_id, revision_sha256,
                        start_offset, end_offset, timestamp_start_ms, timestamp_end_ms,
                        span_fingerprint, locator_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(anchor_id) DO NOTHING
                    """,
                    values,
                )

    def source_anchor_metadata(self, anchor_ids: tuple[str, ...]) -> list[dict]:
        """Return anchor identity and display metadata without raw text or source locations."""
        if not anchor_ids:
            return []
        placeholders = ",".join("?" for _ in anchor_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT source_anchors.anchor_id, source_anchors.collection_id, source_anchors.source_id,
                       source_anchors.revision_id, source_anchors.revision_sha256,
                       library_sources.identity_key, library_sources.original_filename, library_sources.lesson_title,
                       library_sources.author, library_sources.course, library_sources.year,
                       source_anchors.timestamp_start_ms, source_anchors.timestamp_end_ms,
                       source_anchors.start_offset, source_anchors.end_offset,
                       source_anchors.span_fingerprint, source_anchors.locator_version
                FROM source_anchors
                JOIN source_revisions ON source_revisions.revision_id = source_anchors.revision_id
                    AND source_revisions.source_id = source_anchors.source_id
                    AND source_revisions.content_sha256 = source_anchors.revision_sha256
                JOIN library_sources ON library_sources.source_id = source_revisions.source_id
                    AND library_sources.collection_id = source_anchors.collection_id
                WHERE source_anchors.anchor_id IN ({placeholders})
                """,
                anchor_ids,
            ).fetchall()
        by_id = {
            metadata["anchor_id"]: metadata
            for row in rows
            if (metadata := _safe_anchor_metadata(row)) is not None
        }
        return [by_id[anchor_id] for anchor_id in anchor_ids if anchor_id in by_id]

    def source_changes(self) -> list[SourceChange]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id, lifecycle_state, revision_id, local_locator, observed_at
                FROM source_changes ORDER BY source_id
                """
            ).fetchall()
        return [SourceChange(*row) for row in rows]

    def orientation_audits(self, thread_id: int) -> list[dict] | None:
        if not self.has_thread(thread_id):
            return None
        turns = self.display_turns(thread_id)
        result: list[dict] = []
        for turn in turns:
            diagnostic = turn.get("diagnostics") or {}
            context = diagnostic.get("knowledge_context")
            if not isinstance(context, dict):
                continue
            safe_context = _safe_orientation_context(context)
            if safe_context is None:
                continue
            result.append(
                {
                    "turn_number": turn["turn_number"],
                    "knowledge_context": safe_context,
                }
            )
        return result

    def source_revisions(self, source_id: str) -> list[SourceRevision]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_revisions.revision_id, library_sources.collection_id,
                       source_revisions.source_id, source_revisions.content_sha256,
                       source_revisions.byte_size, source_revisions.local_locator,
                       source_revisions.observed_at, source_revisions.lifecycle_state,
                       source_revisions.remote_file_id, source_revisions.remote_vector_store_file_id
                FROM source_revisions JOIN library_sources USING(source_id)
                WHERE source_revisions.source_id = ? ORDER BY source_revisions.rowid
                """,
                (source_id,),
            ).fetchall()
        return [SourceRevision(*row) for row in rows]

    def store_source_change(self, change: SourceChange) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_changes(
                    source_id, lifecycle_state, revision_id, local_locator, observed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    lifecycle_state = excluded.lifecycle_state,
                    revision_id = excluded.revision_id,
                    local_locator = excluded.local_locator,
                    observed_at = excluded.observed_at
                WHERE source_changes.lifecycle_state IS NOT excluded.lifecycle_state
                   OR source_changes.revision_id IS NOT excluded.revision_id
                   OR source_changes.local_locator IS NOT excluded.local_locator
                """,
                (
                    change.source_id,
                    change.lifecycle_state,
                    change.revision_id,
                    change.local_locator,
                    change.observed_at,
                ),
            )

    def source_change(self, source_id: str) -> SourceChange | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_id, lifecycle_state, revision_id, local_locator, observed_at
                FROM source_changes WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
        return None if row is None else SourceChange(*row)

    def clear_source_change(self, source_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM source_changes WHERE source_id = ?", (source_id,))

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

    def legacy_sources(self) -> list[LegacySource]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT relative_path, filename, year, local_path, modified_at, file_id,
                       vector_store_file_id
                FROM sources ORDER BY relative_path
                """
            ).fetchall()
        return [LegacySource(*row) for row in rows]

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
                    citations_json, evidence_json, diagnostic_json, response_id,
                    status, incomplete_reason, raw_start_position, raw_end_position
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    int(row[0]) + 1,
                    user_text,
                    answer_markdown,
                    json.dumps(citations),
                    json.dumps(evidence),
                    None if diagnostics is None else json.dumps(diagnostics),
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
                       evidence_json, diagnostic_json, response_id, status,
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
                "response_id": row[6],
                "status": row[7],
                "incomplete_reason": row[8],
            }
            for row in rows
        ]

    def delete_thread(self, thread_id: int) -> bool:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if exists is None:
                return False
            connection.execute("DELETE FROM display_turns WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM response_diagnostics WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM thread_replay_items WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM thread_items WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        return True

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
        connection.create_function(
            "derived_record_is_valid",
            1,
            lambda record_id: self._derived_record_is_valid(connection, record_id),
        )
        return connection


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


def _snapshot_from_row(row: tuple) -> CorpusSnapshot:
    return CorpusSnapshot(
        row[0],
        row[1],
        tuple(json.loads(row[2])),
        *row[3:],
    )


def _decode_sqlite_text(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError("expected SQLite text")


def _safe_orientation_context(context: dict) -> dict | None:
    record_ids = context.get("record_ids")
    budget = context.get("budget")
    snapshot_id = context.get("snapshot_id")
    schema_version = context.get("snapshot_schema_version")
    if (
        context.get("status") not in {"used", "unavailable"}
        or not isinstance(context.get("used"), bool)
        or not isinstance(record_ids, list)
        or any(not isinstance(record_id, str) or re.fullmatch(r"rec_[0-9a-f]{64}", record_id) is None for record_id in record_ids)
        or not isinstance(budget, dict)
        or not _nonnegative_int(budget.get("max_records"))
        or not _nonnegative_int(budget.get("max_tokens"))
        or not _nonnegative_int(context.get("used_tokens"))
        or not isinstance(context.get("truncated"), bool)
        or (snapshot_id is not None and (not isinstance(snapshot_id, str) or re.fullmatch(r"snap_[0-9a-f]{64}", snapshot_id) is None))
        or (schema_version is not None and (not isinstance(schema_version, str) or not 0 < len(schema_version) <= 120))
    ):
        return None
    return {
        "status": context["status"],
        "used": context["used"],
        "snapshot_id": snapshot_id,
        "snapshot_schema_version": schema_version,
        "record_ids": record_ids,
        "record_count": len(record_ids),
        "budget": {"max_records": budget["max_records"], "max_tokens": budget["max_tokens"]},
        "used_tokens": context["used_tokens"],
        "truncated": context["truncated"],
    }


def _validate_anchor_identity(anchor: SourceAnchor, identity_key: object) -> None:
    if not isinstance(identity_key, str) or not identity_key or not _is_sha256(anchor.revision_sha256):
        raise ValueError("source anchor requires canonical source and revision identity")
    expected_source_id = "src_" + sha256(f"{anchor.collection_id}\0{identity_key}".encode()).hexdigest()
    expected_revision_id = f"rev_{expected_source_id}_{anchor.revision_sha256}"
    expected_anchor_id = "anc_" + sha256(
        f"{anchor.collection_id}\0{expected_revision_id}\0{anchor.start_offset}\0"
        f"{anchor.end_offset}\0{anchor.locator_version}".encode()
    ).hexdigest()
    if (
        not _nonempty_text(anchor.collection_id)
        or anchor.source_id != expected_source_id
        or anchor.revision_id != expected_revision_id
        or anchor.anchor_id != expected_anchor_id
        or anchor.locator_version != LOCATOR_VERSION
        or not _is_sha256(anchor.span_fingerprint)
        or not _valid_offsets(anchor.start_offset, anchor.end_offset)
        or not _valid_timestamps(anchor.timestamp_start_ms, anchor.timestamp_end_ms)
    ):
        raise ValueError("source anchor identity or locator metadata is invalid")


def _clone_derived_record(
    record: DerivedRecord, *, snapshot_id: str, record_id_mapping: Mapping[str, str]
) -> DerivedRecord:
    dependencies = tuple(
        RecordDependency(
            dependency.kind,
            record_id_mapping[dependency.identifier]
            if dependency.kind == "derived_record"
            else dependency.identifier,
        )
        for dependency in record.dependencies
    )
    common = {
        "snapshot_id": snapshot_id,
        "anchors": record.anchors,
        "dependencies": dependencies,
        "validation_state": record.validation_state,
        "lifecycle_state": record.lifecycle_state,
        "qualification": record.qualification,
        "derived_kind": record.derived_kind,
        "evidence_state": record.evidence_state,
        "compiler_provenance": record.compiler_provenance,
        "facets": record.facets,
    }
    if isinstance(record, Claim):
        return Claim.create(
            **common, semantic_subtype=record.semantic_subtype,
            subject=record.subject, predicate=record.predicate, object=record.object,
        )
    if isinstance(record, Relationship):
        return Relationship.create(**common, left=record.left, relation=record.relation, right=record.right)
    if isinstance(record, ProcedureSequenceHierarchy):
        return ProcedureSequenceHierarchy.create(
            **common,
            kind=record.kind,
            terms=record.terms,
            prerequisites=record.prerequisites,
            conditions=record.conditions,
            branches=record.branches,
        )
    if isinstance(record, Evolution):
        return Evolution.create(
            **common, subject=record.subject, previous=record.previous, current=record.current,
            earlier_source_set=record.earlier_source_set, later_source_set=record.later_source_set,
            classification=record.classification, negative_evidence_state=record.negative_evidence_state,
            competing_anchors=record.competing_anchors, earlier_coverage_id=record.earlier_coverage_id,
            later_coverage_id=record.later_coverage_id, earlier_observed_years=record.earlier_observed_years,
            later_observed_years=record.later_observed_years,
            deprecation_evidence_anchors=record.deprecation_evidence_anchors,
        )
    if isinstance(record, ConflictUnresolved):
        return ConflictUnresolved.create(
            **common, kind=record.kind, subject=record.subject, alternatives=record.alternatives,
            competing_record_ids=tuple(record_id_mapping[item] for item in record.competing_record_ids),
            reconciliation_state=record.reconciliation_state, relevant_scopes=record.relevant_scopes,
            conditions=record.conditions, unresolved_questions=record.unresolved_questions,
        )
    raise ValueError("unknown derived record family")


def _safe_anchor_metadata(row: tuple) -> dict | None:
    (
        anchor_id,
        collection_id,
        source_id,
        revision_id,
        revision_sha256,
        identity_key,
        original_filename,
        lesson_title,
        author,
        course,
        year,
        timestamp_start_ms,
        timestamp_end_ms,
        start_offset,
        end_offset,
        span_fingerprint,
        locator_version,
    ) = row
    if not all(_nonempty_text(value) for value in (
        anchor_id, collection_id, source_id, revision_id, identity_key, lesson_title, author, course
    )):
        return None
    if (
        not _is_sha256(revision_sha256)
        or not _is_sha256(span_fingerprint)
        or source_id != "src_" + sha256(f"{collection_id}\0{identity_key}".encode()).hexdigest()
        or revision_id != f"rev_{source_id}_{revision_sha256}"
        or anchor_id != "anc_" + sha256(
            f"{collection_id}\0{revision_id}\0{start_offset}\0{end_offset}\0{locator_version}".encode()
        ).hexdigest()
        or locator_version != LOCATOR_VERSION
        or not _valid_offsets(start_offset, end_offset)
        or not _valid_timestamps(timestamp_start_ms, timestamp_end_ms)
        or not isinstance(year, int)
        or isinstance(year, bool)
    ):
        return None
    filename = _safe_filename(original_filename)
    if filename is None:
        return None
    return {
        "anchor_id": anchor_id,
        "collection_id": collection_id,
        "source_id": source_id,
        "revision_id": revision_id,
        "revision_sha256": revision_sha256,
        "filename": filename,
        "lesson_title": lesson_title,
        "author": author,
        "course": course,
        "year": year,
        "timestamp_start_ms": timestamp_start_ms,
        "timestamp_end_ms": timestamp_end_ms,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "span_fingerprint": span_fingerprint,
        "locator_version": locator_version,
    }


def _safe_filename(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    filename = ntpath.basename(value.replace("/", "\\"))
    return filename if filename and filename not in {".", ".."} else None


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_offsets(start: object, end: object) -> bool:
    return _nonnegative_int(start) and _nonnegative_int(end) and start < end


def _valid_timestamps(start: object, end: object) -> bool:
    return (start is None and end is None) or (
        _nonnegative_int(start) and _nonnegative_int(end) and start <= end
    )


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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
