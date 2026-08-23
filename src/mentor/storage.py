"""Small SQLite store for private Trading Mentor state."""

import json
import sqlite3
import time
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path

from mentor.compilation import CompilationMetric, CompilationRun, CorpusSnapshot
from mentor.derived_records import (
    Claim,
    CompilerProvenance,
    ConflictUnresolved,
    DerivedRecord,
    Evolution,
    Facet,
    ProcedureSequenceHierarchy,
    RecordDependency,
    Relationship,
    validate_record,
)
from mentor.knowledge import Collection, Source as LibrarySource, SourceRevision


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
class Thread:
    id: int
    title: str


_SNAPSHOT_QUERY = """
SELECT snapshot_id, run_id, selected_revision_ids_json, selected_revision_fingerprint,
       raw_store_id, derived_store_id, model_version, prompt_version, schema_version,
       status, created_at, validated_at, published_at, failed_at, failure_reason
FROM corpus_snapshots
"""


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
                CREATE TABLE IF NOT EXISTS derived_record_dependencies (
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    position INTEGER NOT NULL,
                    dependency_kind TEXT NOT NULL CHECK(dependency_kind IN ('source_revision', 'derived_record')),
                    dependency_id TEXT NOT NULL,
                    PRIMARY KEY(record_id, position)
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
                    structure_kind TEXT NOT NULL CHECK(structure_kind IN ('procedure', 'sequence', 'hierarchy'))
                );
                CREATE TABLE IF NOT EXISTS derived_evolutions (
                    record_id TEXT PRIMARY KEY REFERENCES derived_records(record_id),
                    subject TEXT NOT NULL,
                    previous_value TEXT NOT NULL,
                    current_value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS derived_conflict_unresolved (
                    record_id TEXT PRIMARY KEY REFERENCES derived_records(record_id),
                    issue_kind TEXT NOT NULL CHECK(issue_kind IN ('conflict', 'unresolved')),
                    subject TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS derived_record_terms (
                    record_id TEXT NOT NULL REFERENCES derived_records(record_id),
                    term_role TEXT NOT NULL CHECK(term_role IN ('procedure_term', 'alternative')),
                    position INTEGER NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY(record_id, term_role, position)
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sources)")}
            if "modified_at" not in columns:
                connection.execute("ALTER TABLE sources ADD COLUMN modified_at REAL")
            derived_columns = {row[1] for row in connection.execute("PRAGMA table_info(derived_records)")}
            if "finalized" not in derived_columns:
                connection.execute(
                    "ALTER TABLE derived_records ADD COLUMN finalized INTEGER NOT NULL DEFAULT 0 CHECK(finalized IN (0, 1))"
                )
            for column in ("compiler_model_version", "compiler_prompt_version", "compiler_schema_version"):
                if column not in derived_columns:
                    connection.execute(f"ALTER TABLE derived_records ADD COLUMN {column} TEXT")
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
                snapshot.run_id, snapshot.selected_revision_ids
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
            row = connection.execute(_SNAPSHOT_QUERY + " WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
            if row is None:
                raise ValueError("unknown snapshot")
            snapshot = _snapshot_from_row(row)
            allowed = {
                "building": {"validating"},
                "validating": {"published", "failed"},
                "failed": set(),
                "published": set(),
            }
            if status not in allowed[snapshot.status]:
                raise ValueError(f"cannot transition snapshot from {snapshot.status} to {status}")
            if status == "failed" and not failure_reason:
                raise ValueError("failed snapshots require a failure_reason")
            if status == "published" and (not snapshot.raw_store_id or not snapshot.derived_store_id):
                raise ValueError("published snapshots require raw and derived store IDs")

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
            row = connection.execute(_SNAPSHOT_QUERY + " WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        return None if row is None else _snapshot_from_row(row)

    def current_snapshot(self) -> CorpusSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                _SNAPSHOT_QUERY
                + " JOIN settings ON settings.key = 'current_snapshot_id'"
                " AND settings.value = corpus_snapshots.snapshot_id"
                " WHERE corpus_snapshots.status = 'published'"
            ).fetchone()
        return None if row is None else _snapshot_from_row(row)

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
                    record_count, call_count, input_tokens, output_tokens, latency_ms, cost_usd,
                    remote_calls, failure_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                       latency_ms, cost_usd, remote_calls, failure_count, model_version,
                       prompt_version, schema_version
                FROM compilation_metrics WHERE run_id = ? ORDER BY metric_id
                """,
                (run_id,),
            ).fetchall()
        return [CompilationMetric(*row) for row in rows]

    def store_derived_record(self, record: DerivedRecord) -> None:
        validate_record(record)
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM corpus_snapshots WHERE snapshot_id = ?", (record.snapshot_id,)).fetchone() is None:
                raise ValueError("derived record references an unknown snapshot")
            if connection.execute(
                "SELECT 1 FROM derived_records WHERE record_id = ? AND finalized = 1", (record.record_id,)
            ).fetchone() is not None:
                return
            connection.execute(
                """
                INSERT INTO derived_records(
                    record_id, snapshot_id, family, derived_kind, evidence_state, validation_state, lifecycle_state, qualification,
                    compiler_model_version, compiler_prompt_version, compiler_schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO NOTHING
                """,
                (
                    record.record_id,
                    record.snapshot_id,
                    record.family,
                    record.derived_kind,
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

    def derived_records(self, snapshot_id: str) -> list[DerivedRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT record_id, snapshot_id, family, derived_kind, evidence_state, validation_state, lifecycle_state, qualification,
                       compiler_model_version, compiler_prompt_version, compiler_schema_version
                FROM derived_records WHERE snapshot_id = ? AND finalized = 1 ORDER BY record_id
                """,
                (snapshot_id,),
            ).fetchall()
            return [self._derived_record_from_row(connection, row) for row in rows]

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
                INSERT INTO derived_procedure_sequence_hierarchy(record_id, structure_kind)
                VALUES (?, ?) ON CONFLICT DO NOTHING
                """,
                (record.record_id, record.kind),
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
                INSERT INTO derived_evolutions(record_id, subject, previous_value, current_value)
                VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
                """,
                (record.record_id, record.subject, record.previous, record.current),
            )
        elif isinstance(record, ConflictUnresolved):
            connection.execute(
                """
                INSERT INTO derived_conflict_unresolved(record_id, issue_kind, subject)
                VALUES (?, ?, ?) ON CONFLICT DO NOTHING
                """,
                (record.record_id, record.kind, record.subject),
            )
            connection.executemany(
                """
                INSERT INTO derived_record_terms(record_id, term_role, position, value)
                VALUES (?, 'alternative', ?, ?) ON CONFLICT DO NOTHING
                """,
                [(record.record_id, position, alternative) for position, alternative in enumerate(record.alternatives)],
            )

    def _derived_record_from_row(self, connection: sqlite3.Connection, row: tuple) -> DerivedRecord:
        record_id, snapshot_id, family, derived_kind, evidence_state, validation_state, lifecycle_state, qualification = (
            _decode_sqlite_text(value) for value in row[:8]
        )
        provenance_values = row[8:]
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
                derived_kind=derived_kind,
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
            kind = connection.execute(
                "SELECT structure_kind FROM derived_procedure_sequence_hierarchy WHERE record_id = ?", (record_id,)
            ).fetchone()
            kind = _decode_sqlite_text(kind[0])
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
            record = ProcedureSequenceHierarchy.create(**common, kind=kind, terms=terms)
        elif family == "evolution":
            values = connection.execute(
                "SELECT subject, previous_value, current_value FROM derived_evolutions WHERE record_id = ?", (record_id,)
            ).fetchone()
            values = tuple(_decode_sqlite_text(value) for value in values)
            record = Evolution.create(**common, subject=values[0], previous=values[1], current=values[2])
        elif family == "conflict_unresolved":
            kind, subject = connection.execute(
                "SELECT issue_kind, subject FROM derived_conflict_unresolved WHERE record_id = ?", (record_id,)
            ).fetchone()
            kind, subject = _decode_sqlite_text(kind), _decode_sqlite_text(subject)
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
            record = ConflictUnresolved.create(**common, kind=kind, subject=subject, alternatives=alternatives)
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
                SELECT record_id, snapshot_id, family, derived_kind, evidence_state, validation_state, lifecycle_state, qualification,
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
        connection.execute("DROP TRIGGER IF EXISTS derived_records_require_children")
        connection.executescript(
            """
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
                      AND NEW.derived_kind IN ('statement', 'definition', 'recommendation', 'strategy_implication')
                      AND length(trim(subject)) > 0 AND length(subject) <= 240
                      AND length(trim(predicate)) > 0 AND length(predicate) <= 240
                      AND length(trim(object)) > 0 AND length(object) <= 240
                ) THEN RAISE(ABORT, 'derived records require valid claim content') END;
                SELECT CASE WHEN NEW.family = 'relationship' AND NOT EXISTS(
                    SELECT 1 FROM derived_relationships
                    WHERE record_id = NEW.record_id
                      AND NEW.derived_kind = 'relation'
                      AND relation IN ('supports', 'contrasts', 'depends_on', 'causes')
                      AND length(trim(left_term)) > 0 AND length(left_term) <= 240
                      AND length(trim(right_term)) > 0 AND length(right_term) <= 240
                ) THEN RAISE(ABORT, 'derived records require valid relationship content') END;
                SELECT CASE WHEN NEW.family = 'procedure_sequence_hierarchy' AND NOT EXISTS(
                    SELECT 1 FROM derived_procedure_sequence_hierarchy
                    WHERE record_id = NEW.record_id AND structure_kind = NEW.derived_kind
                ) OR NEW.family = 'procedure_sequence_hierarchy' AND EXISTS(
                    SELECT 1 FROM derived_record_terms
                    WHERE record_id = NEW.record_id AND term_role = 'procedure_term'
                      AND (length(trim(value)) = 0 OR length(value) > 240)
                ) THEN RAISE(ABORT, 'derived records require valid procedure content') END;
                SELECT CASE WHEN NEW.family = 'evolution' AND NOT EXISTS(
                    SELECT 1 FROM derived_evolutions
                    WHERE record_id = NEW.record_id AND NEW.derived_kind = 'change'
                      AND length(trim(subject)) > 0 AND length(subject) <= 240
                      AND length(trim(previous_value)) > 0 AND length(previous_value) <= 240
                      AND length(trim(current_value)) > 0 AND length(current_value) <= 240
                ) THEN RAISE(ABORT, 'derived records require valid evolution content') END;
                SELECT CASE WHEN NEW.family = 'conflict_unresolved' AND NOT EXISTS(
                    SELECT 1 FROM derived_conflict_unresolved
                    WHERE record_id = NEW.record_id AND issue_kind = NEW.derived_kind
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
