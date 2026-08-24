from hashlib import sha256
from pathlib import Path

import pytest

from mentor.compilation import CompilationRun, CorpusSnapshot, SourceProcessingResult
from mentor.evaluation import (
    EvaluationCase,
    EvaluationMetrics,
    PilotRuntime,
    compare_evaluations,
    run_evaluation,
)
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import Storage


def _revision(storage: Storage) -> SourceRevision:
    collection = Collection("collection_synthetic", "Synthetic", "trading", True, "test")
    source = Source.create(
        collection_id=collection.collection_id,
        identity_key="pilot-source",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Synthetic lesson",
        year=2026,
        original_filename="synthetic.txt",
        local_provenance="C:/synthetic/synthetic.txt",
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256=sha256(b"synthetic pilot source").hexdigest(),
        byte_size=22,
        local_locator="C:/synthetic/synthetic.txt",
        observed_at=1.0,
        lifecycle_state="active",
        remote_file_id="file_synthetic",
    )
    storage.store_collection(collection)
    storage.store_source(source)
    storage.store_source_revision(revision)
    return revision


def _candidate(
    storage: Storage,
    revision: SourceRevision,
    run_id: str,
    *,
    artifact_scope: str | None = None,
) -> CorpusSnapshot:
    run = CompilationRun(run_id, "synthetic-model", "prompt-v1", "schema-v1", 1.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=(revision,),
        raw_store_id=f"vs_{artifact_scope or 'production'}_raw_{run_id}",
        derived_store_id=f"vs_{artifact_scope or 'production'}_derived_{run_id}",
        created_at=1.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    if artifact_scope is not None:
        storage.record_candidate_artifact_scope(snapshot.snapshot_id, artifact_scope)
    storage.record_candidate_gate(
        snapshot.snapshot_id,
        (SourceProcessingResult(revision.revision_id, "processed", 0),),
        checked_at=2.0,
    )
    storage.transition_snapshot(snapshot.snapshot_id, "validating", transitioned_at=2.0)
    return snapshot


def _production_runtime(database_path: Path) -> tuple[Storage, SourceRevision, CorpusSnapshot]:
    storage = Storage(database_path)
    storage.initialize()
    revision = _revision(storage)
    snapshot = _candidate(storage, revision, "run_production")
    snapshot = storage.transition_snapshot(snapshot.snapshot_id, "published", transitioned_at=3.0)
    return storage, revision, snapshot


def _metrics(**changes) -> EvaluationMetrics:
    values = {
        "quality_state": "passed",
        "citation_count": 1,
        "connection_state": "passed",
        "evolution_state": "not_scored",
        "correction_state": "not_scored",
        "orientation_calls": 1,
        "orientation_record_count": 2,
        "raw_search_calls": 1,
        "retrieved_passage_count": 3,
        "input_tokens": 100,
        "output_tokens": 20,
        "latency_ms": 25,
        "estimated_cost_usd": 0.01,
    }
    return EvaluationMetrics(**(values | changes))


def test_evaluation_harness_aggregates_required_metrics_and_isolates_case_failures():
    cases = (
        EvaluationCase("case_source", "source_authority", "Synthetic source question"),
        EvaluationCase("case_failure", "conflict", "Synthetic failing question"),
    )

    def runner(case):
        if case.case_id == "case_failure":
            raise RuntimeError("private response text must not be retained")
        return _metrics()

    report = run_evaluation("assimilated", cases, runner)

    assert report.summary.case_count == 2
    assert report.summary.completed_count == 1
    assert report.summary.failed_count == 1
    assert report.summary.citation_count == 1
    assert report.summary.orientation_calls == 1
    assert report.summary.raw_search_calls == 1
    assert report.summary.retrieved_passage_count == 3
    assert report.summary.input_tokens == 100
    assert report.summary.output_tokens == 20
    assert report.summary.latency_ms == 25
    assert report.summary.estimated_cost_usd == pytest.approx(0.01)
    assert report.outcomes[1].failure_type == "RuntimeError"
    assert "private response text" not in repr(report)


def test_baseline_comparison_requires_matching_cases_and_preserves_quality_dimensions():
    cases = (EvaluationCase("case_compare", "baseline_comparison", "Synthetic comparison"),)
    baseline = run_evaluation(
        "baseline",
        cases,
        lambda _case: _metrics(
            quality_state="failed",
            citation_count=0,
            connection_state="failed",
            orientation_calls=0,
            orientation_record_count=0,
            raw_search_calls=3,
        ),
    )
    assimilated = run_evaluation("assimilated", cases, lambda _case: _metrics())

    comparison = compare_evaluations(baseline, assimilated)

    assert comparison.baseline.quality_passed_count == 0
    assert comparison.assimilated.quality_passed_count == 1
    assert comparison.baseline.connection_passed_count == 0
    assert comparison.assimilated.connection_passed_count == 1
    assert comparison.baseline.orientation_calls == 0
    assert comparison.assimilated.orientation_calls == 1
    assert comparison.baseline.raw_search_calls == 3
    assert comparison.assimilated.raw_search_calls == 1

    wrong = run_evaluation(
        "assimilated",
        (EvaluationCase("other", "baseline_comparison", "Other"),),
        lambda _case: _metrics(),
    )
    with pytest.raises(ValueError, match="same cases"):
        compare_evaluations(baseline, wrong)


def test_pilot_runtime_copies_sqlite_and_publishes_only_inside_the_copy(tmp_path):
    production_path = tmp_path / "production" / "mentor.sqlite3"
    production, revision, production_snapshot = _production_runtime(production_path)
    before_pointer = production.current_snapshot()
    before_files = set(production_path.parent.rglob("*"))

    pilot = PilotRuntime.create(
        production_path,
        tmp_path / "data" / "pilots",
        run_id="pilot-synthetic-001",
    )

    assert pilot.database_path != production_path
    assert pilot.storage.runtime_scope == "pilot"
    assert pilot.storage.current_snapshot() is None
    with pytest.raises(ValueError, match="database runtime scope"):
        Storage(pilot.database_path).initialize()
    pilot_snapshot = _candidate(
        pilot.storage,
        revision,
        "run_pilot",
        artifact_scope="pilot",
    )
    published = pilot.publish(pilot_snapshot.snapshot_id, published_at=4.0)
    pilot.output_directory.joinpath("result.json").write_text("{}", encoding="utf-8")
    pilot.trace_directory.joinpath("trace.json").write_text("{}", encoding="utf-8")

    assert published.raw_store_id.startswith("vs_pilot_")
    assert pilot.storage.current_snapshot() == published
    assert production.current_snapshot() == before_pointer == production_snapshot
    assert set(production_path.parent.rglob("*")) == before_files


def test_production_rejects_pilot_scoped_publication_and_resolution(tmp_path):
    production, revision, production_snapshot = _production_runtime(
        tmp_path / "production" / "mentor.sqlite3"
    )
    pilot_snapshot = _candidate(
        production,
        revision,
        "run_injected_pilot",
        artifact_scope="pilot",
    )

    with pytest.raises(ValueError, match="runtime scope"):
        production.transition_snapshot(pilot_snapshot.snapshot_id, "published", transitioned_at=4.0)

    assert production.current_snapshot() == production_snapshot
    with production._connect() as connection:
        connection.execute(
            "UPDATE corpus_snapshots SET status = 'published' WHERE snapshot_id = ?",
            (pilot_snapshot.snapshot_id,),
        )
        for key, value in (
            ("current_snapshot_id", pilot_snapshot.snapshot_id),
            ("active_raw_store_id", pilot_snapshot.raw_store_id),
            ("active_derived_store_id", pilot_snapshot.derived_store_id),
        ):
            connection.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))

    assert production.current_snapshot() is None


def test_pilot_creation_failure_leaves_production_untouched_and_never_reuses_a_run(tmp_path):
    production_path = tmp_path / "production" / "mentor.sqlite3"
    production, _revision_value, production_snapshot = _production_runtime(production_path)
    pilot_root = tmp_path / "data" / "pilots"
    PilotRuntime.create(production_path, pilot_root, run_id="pilot-duplicate")

    with pytest.raises(FileExistsError):
        PilotRuntime.create(production_path, pilot_root, run_id="pilot-duplicate")

    assert production.current_snapshot() == production_snapshot
    assert len(tuple(pilot_root.iterdir())) == 1


def test_pilot_copy_rejects_a_pilot_database_without_leaving_a_partial_run(tmp_path):
    production_path = tmp_path / "production" / "mentor.sqlite3"
    production, _revision_value, production_snapshot = _production_runtime(production_path)
    pilot_root = tmp_path / "data" / "pilots"
    first = PilotRuntime.create(production_path, pilot_root, run_id="pilot-source")

    with pytest.raises(ValueError, match="production runtime"):
        PilotRuntime.create(first.database_path, pilot_root, run_id="invalid-nested-pilot")

    assert not pilot_root.joinpath("invalid-nested-pilot").exists()
    assert production.current_snapshot() == production_snapshot


def test_pilot_copy_accepts_an_unmarked_legacy_production_runtime_without_mutating_it(tmp_path):
    production_path = tmp_path / "production" / "mentor.sqlite3"
    production, _revision_value, _snapshot = _production_runtime(production_path)
    with production._connect() as connection:
        connection.execute("DELETE FROM settings WHERE key = 'runtime_scope'")

    pilot = PilotRuntime.create(
        production_path,
        tmp_path / "data" / "pilots",
        run_id="pilot-legacy-production",
    )

    with production._connect() as connection:
        assert connection.execute(
            "SELECT value FROM settings WHERE key = 'runtime_scope'"
        ).fetchone() is None
    assert pilot.storage.runtime_scope == "pilot"


def test_pilot_server_rejects_a_chat_service_bound_to_production_storage(tmp_path):
    production_path = tmp_path / "production" / "mentor.sqlite3"
    production, _revision_value, _snapshot = _production_runtime(production_path)
    pilot = PilotRuntime.create(
        production_path,
        tmp_path / "data" / "pilots",
        run_id="pilot-server",
    )

    class Service:
        def __init__(self, storage):
            self.storage = storage

    with pytest.raises(ValueError, match="same runtime"):
        pilot.create_server(Service(production), port=0)

    server = pilot.create_server(Service(pilot.storage), port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_pilot_runtime_directory_is_git_ignored():
    ignore_rules = Path(__file__).parents[1].joinpath(".gitignore").read_text(encoding="utf-8")

    assert "/data/pilots/" in ignore_rules.splitlines()
