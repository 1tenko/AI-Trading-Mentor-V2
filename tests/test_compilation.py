from dataclasses import replace
from hashlib import sha256
import sqlite3

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


def current_pointer(storage: Storage) -> dict[str, str]:
    with storage._connect() as connection:
        rows = connection.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('current_snapshot_id', 'active_raw_store_id', 'active_derived_store_id')"
        ).fetchall()
    return {key: value for key, value in rows}


def test_candidate_requires_validation_before_publication(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = candidate(storage, "run_invalid", [revision_for(storage, "source_a")])

    with pytest.raises(ValueError, match="building.*published"):
        storage.transition_snapshot(snapshot.snapshot_id, "published")

    assert storage.snapshot(snapshot.snapshot_id).status == "building"
    assert storage.current_snapshot() is None


def test_candidate_cannot_fail_before_validation(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = candidate(storage, "run_early_failure", [revision_for(storage, "source_a")])

    with pytest.raises(ValueError, match="building.*failed"):
        storage.transition_snapshot(snapshot.snapshot_id, "failed", failure_reason="synthetic failure")

    assert storage.snapshot(snapshot.snapshot_id).status == "building"


def test_candidate_persistence_rejects_tampered_snapshot_identity(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    first = revision_for(storage, "source_a")
    second = revision_for(storage, "source_b")
    run = CompilationRun("run_tampered", "test-model", "prompt-v1", "schema-v1", 1_700_000_000.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=[first, second],
        raw_store_id="raw_tampered",
        derived_store_id="derived_tampered",
        created_at=1_700_000_001.0,
    )

    for tampered in (
        replace(snapshot, selected_revision_ids=tuple(reversed(snapshot.selected_revision_ids))),
        replace(snapshot, selected_revision_ids=(snapshot.selected_revision_ids[0],) * 2),
        replace(snapshot, selected_revision_fingerprint="0" * 64),
        replace(snapshot, snapshot_id="snap_forged"),
    ):
        with pytest.raises(ValueError, match="snapshot identity"):
            storage.create_compilation_candidate(run, tampered)

    assert storage.compilation_run(run.run_id) is None


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


def test_publication_archives_the_previous_pair_and_resolves_only_the_new_pair(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    previous = candidate(storage, "run_previous", [revision_for(storage, "source_previous")])
    storage.transition_snapshot(previous.snapshot_id, "validating")
    storage.transition_snapshot(previous.snapshot_id, "published")
    replacement = candidate(storage, "run_replacement", [revision_for(storage, "source_replacement")])
    storage.transition_snapshot(replacement.snapshot_id, "validating")

    storage.transition_snapshot(replacement.snapshot_id, "published")

    assert storage.snapshot(previous.snapshot_id).status == "archived"
    assert storage.current_snapshot() == storage.snapshot(replacement.snapshot_id)
    assert current_pointer(storage) == {
        "current_snapshot_id": replacement.snapshot_id,
        "active_raw_store_id": replacement.raw_store_id,
        "active_derived_store_id": replacement.derived_store_id,
    }


def test_failed_pointer_swap_keeps_the_previous_pair_readable(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    previous = candidate(storage, "run_boundary_previous", [revision_for(storage, "source_previous")])
    storage.transition_snapshot(previous.snapshot_id, "validating")
    storage.transition_snapshot(previous.snapshot_id, "published")
    replacement = candidate(storage, "run_boundary_replacement", [revision_for(storage, "source_replacement")])
    storage.transition_snapshot(replacement.snapshot_id, "validating")
    before = current_pointer(storage)
    with storage._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_raw_pointer_update
            BEFORE UPDATE ON settings WHEN NEW.key = 'active_raw_store_id'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic pointer failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic pointer failure"):
        storage.transition_snapshot(replacement.snapshot_id, "published")

    assert current_pointer(storage) == before
    assert storage.current_snapshot() == storage.snapshot(previous.snapshot_id)
    assert storage.snapshot(replacement.snapshot_id).status == "validating"


def test_current_snapshot_rejects_mixed_snapshot_store_pointers(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = candidate(storage, "run_mixed_pointers", [revision_for(storage, "source_mixed")])
    storage.transition_snapshot(snapshot.snapshot_id, "validating")
    storage.transition_snapshot(snapshot.snapshot_id, "published")
    with storage._connect() as connection:
        connection.execute(
            "UPDATE settings SET value = 'raw_other' WHERE key = 'active_raw_store_id'"
        )

    assert storage.current_snapshot() is None
