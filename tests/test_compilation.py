from dataclasses import replace
from hashlib import sha256

import pytest

from mentor.compilation import CompilationMetric, CompilationRun, CorpusSnapshot
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import Storage


def revision_for(storage: Storage, identity_key: str) -> SourceRevision:
    collection = Collection("collection_synthetic", "Synthetic", "trading", True, "test")
    source = Source.create(
        collection_id=collection.collection_id,
        identity_key=identity_key,
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
        content_sha256=sha256(identity_key.encode()).hexdigest(),
        byte_size=42,
        local_locator="C:/synthetic/synthetic.txt",
        observed_at=1_700_000_000.0,
        lifecycle_state="active",
    )
    storage.store_collection(collection)
    storage.store_source(source)
    storage.store_source_revision(revision)
    return revision


def candidate(storage: Storage, run_id: str, revisions: list[SourceRevision]) -> CorpusSnapshot:
    run = CompilationRun(run_id, "test-model", "prompt-v1", "schema-v1", 1_700_000_000.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=revisions,
        raw_store_id=f"raw_{run_id}",
        derived_store_id=f"derived_{run_id}",
        created_at=1_700_000_001.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    return snapshot


def test_candidate_requires_validation_before_publication(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = candidate(storage, "run_invalid", [revision_for(storage, "source_a")])

    with pytest.raises(ValueError, match="building.*published"):
        storage.transition_snapshot(snapshot.snapshot_id, "published")

    assert storage.snapshot(snapshot.snapshot_id).status == "building"


def test_failed_candidate_remains_isolated_from_the_published_snapshot(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    published = candidate(storage, "run_published", [revision_for(storage, "source_a")])
    storage.transition_snapshot(published.snapshot_id, "validating")
    storage.transition_snapshot(published.snapshot_id, "published")
    failed = candidate(storage, "run_failed", [revision_for(storage, "source_b")])

    storage.transition_snapshot(failed.snapshot_id, "validating")
    storage.transition_snapshot(failed.snapshot_id, "failed", failure_reason="synthetic validation failure")

    assert storage.current_snapshot() == storage.snapshot(published.snapshot_id)
    assert storage.snapshot(failed.snapshot_id).status == "failed"
    assert storage.compilation_run("run_failed").failure_reason == "synthetic validation failure"


def test_current_snapshot_lookup_and_metric_rows_are_deterministic(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    first = revision_for(storage, "source_a")
    second = revision_for(storage, "source_b")
    snapshot = candidate(storage, "run_metrics", [second, first])

    storage.record_compilation_metric(
        "run_metrics",
        CompilationMetric(
            stage="synthetic",
            source_count=2,
            record_count=3,
            call_count=0,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            cost_usd=0,
            remote_calls=0,
            failure_count=0,
        ),
    )
    storage.transition_snapshot(snapshot.snapshot_id, "validating")
    storage.transition_snapshot(snapshot.snapshot_id, "published")

    current = storage.current_snapshot()
    assert current == storage.current_snapshot()
    assert current.snapshot_id == snapshot.snapshot_id
    assert current.selected_revision_ids == tuple(sorted((first.revision_id, second.revision_id)))
    assert current.selected_revision_fingerprint == CorpusSnapshot.create(
        run=CompilationRun("another_run", "test-model", "prompt-v1", "schema-v1", 1_700_000_000.0),
        selected_revisions=[first, second],
        raw_store_id="raw_other",
        derived_store_id="derived_other",
        created_at=1_700_000_001.0,
    ).selected_revision_fingerprint
    assert storage.compilation_metrics("run_metrics") == [
        replace(
            CompilationMetric(
                stage="synthetic",
                source_count=2,
                record_count=3,
                call_count=0,
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                cost_usd=0,
                remote_calls=0,
                failure_count=0,
            ),
            model_version="test-model",
            prompt_version="prompt-v1",
            schema_version="schema-v1",
        )
    ]
