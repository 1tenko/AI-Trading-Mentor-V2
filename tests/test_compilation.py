from dataclasses import replace
from hashlib import sha256
import sqlite3

import pytest

from mentor.compilation import (
    CallUsage,
    CandidateGateResult,
    CompilationMetric,
    CompilationRun,
    CorpusSnapshot,
    SourceProcessingResult,
    TokenPricing,
    usage_from_response,
)
from mentor.derived_records import Claim, RecordDependency
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


def candidate(
    storage: Storage, run_id: str, revisions: list[SourceRevision], *, record_gate: bool = True
) -> CorpusSnapshot:
    run = CompilationRun(run_id, "test-model", "prompt-v1", "schema-v1", 1_700_000_000.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=revisions,
        raw_store_id=f"raw_{run_id}",
        derived_store_id=f"derived_{run_id}",
        created_at=1_700_000_001.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    if record_gate:
        storage.record_candidate_gate(
            snapshot.snapshot_id,
            tuple(SourceProcessingResult(revision.revision_id, "processed", 0) for revision in revisions),
            checked_at=1_700_000_002.0,
        )
    return snapshot


def current_pointer(storage: Storage) -> dict[str, str]:
    with storage._connect() as connection:
        rows = connection.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('current_snapshot_id', 'active_raw_store_id', 'active_derived_store_id')"
        ).fetchall()
    return {key: value for key, value in rows}


def test_usage_captures_reasoning_tokens_and_reproducible_caller_pricing():
    response = type(
        "Response",
        (),
        {
            "usage": type(
                "Usage",
                (),
                {
                    "input_tokens": 1_000,
                    "output_tokens": 500,
                    "output_tokens_details": type("Details", (), {"reasoning_tokens": 200})(),
                },
            )()
        },
    )()
    pricing = TokenPricing(input_per_million=2.0, output_per_million=4.0, reasoning_per_million=6.0)

    usage = usage_from_response(response, pricing=pricing)

    assert usage.input_tokens == 1_000
    assert usage.output_tokens == 500
    assert usage.reasoning_tokens == 200
    assert usage.cost_usd == pytest.approx(0.0044)


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


def test_publication_requires_a_persisted_passing_candidate_gate(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = revision_for(storage, "source_gate")
    missing = candidate(storage, "run_gate_missing", [revision], record_gate=False)
    storage.transition_snapshot(missing.snapshot_id, "validating")

    with pytest.raises(ValueError, match="passing candidate gate"):
        storage.transition_snapshot(missing.snapshot_id, "published")

    incomplete = candidate(storage, "run_gate_incomplete", [revision], record_gate=False)
    failed = storage.record_candidate_gate(incomplete.snapshot_id, (), checked_at=1_700_000_002.0)

    assert failed == CandidateGateResult(
        incomplete.snapshot_id,
        "failed",
        1_700_000_002.0,
        "coverage does not include every selected revision",
    )

    failed_source = candidate(storage, "run_gate_failed_source", [revision], record_gate=False)
    assert storage.record_candidate_gate(
        failed_source.snapshot_id,
        (SourceProcessingResult(revision.revision_id, "failed", 0),),
        checked_at=1_700_000_003.0,
    ) == CandidateGateResult(
        failed_source.snapshot_id,
        "failed",
        1_700_000_003.0,
        "coverage includes a failed revision",
    )
    storage.transition_snapshot(failed_source.snapshot_id, "validating")
    with pytest.raises(ValueError, match="passing candidate gate"):
        storage.transition_snapshot(failed_source.snapshot_id, "published")


def test_candidate_gate_accepts_processed_zero_records_but_rejects_duplicate_active_revisions(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    first = revision_for(storage, "source_zero")
    zero = candidate(storage, "run_gate_zero", [first], record_gate=False)

    assert storage.record_candidate_gate(
        zero.snapshot_id,
        (SourceProcessingResult(first.revision_id, "processed", 0),),
        checked_at=1_700_000_002.0,
    ) == CandidateGateResult(zero.snapshot_id, "passed", 1_700_000_002.0, None)
    with storage._connect() as connection:
        gate_shape = connection.execute(
            "SELECT structural_version, record_count, record_fingerprint FROM candidate_gates WHERE snapshot_id = ?",
            (zero.snapshot_id,),
        ).fetchone()
    assert gate_shape == (
        "record-structure-v1",
        0,
        sha256(b"").hexdigest(),
    )

    source = storage.library_source(first.source_id)
    duplicate = SourceRevision.create(
        source=source,
        content_sha256=sha256(b"duplicate active revision").hexdigest(),
        byte_size=1,
        local_locator="C:/synthetic/duplicate.txt",
        observed_at=1_700_000_003.0,
        lifecycle_state="active",
    )
    storage.store_source_revision(duplicate)
    duplicate_snapshot = candidate(
        storage, "run_gate_duplicate", [first, duplicate], record_gate=False
    )

    assert storage.record_candidate_gate(
        duplicate_snapshot.snapshot_id,
        (
            SourceProcessingResult(first.revision_id, "processed", 0),
            SourceProcessingResult(duplicate.revision_id, "processed", 0),
        ),
        checked_at=1_700_000_004.0,
    ) == CandidateGateResult(
        duplicate_snapshot.snapshot_id,
        "failed",
        1_700_000_004.0,
        "candidate selects duplicate revisions for one source",
    )
    storage.transition_snapshot(duplicate_snapshot.snapshot_id, "validating")
    with pytest.raises(ValueError, match="passing candidate gate"):
        storage.transition_snapshot(duplicate_snapshot.snapshot_id, "published")

    superseded = SourceRevision.create(
        source=source,
        content_sha256=sha256(b"superseded revision").hexdigest(),
        byte_size=1,
        local_locator="C:/synthetic/superseded.txt",
        observed_at=1_700_000_005.0,
        lifecycle_state="superseded",
    )
    storage.store_source_revision(superseded)
    mixed_lifecycle = candidate(
        storage, "run_gate_mixed_lifecycle", [first, superseded], record_gate=False
    )
    assert storage.record_candidate_gate(
        mixed_lifecycle.snapshot_id,
        (
            SourceProcessingResult(first.revision_id, "processed", 0),
            SourceProcessingResult(superseded.revision_id, "processed", 0),
        ),
        checked_at=1_700_000_006.0,
    ) == CandidateGateResult(
        mixed_lifecycle.snapshot_id,
        "failed",
        1_700_000_006.0,
        "candidate selects duplicate revisions for one source",
    )


@pytest.mark.parametrize("state", ("validating", "failed", "published", "archived"))
def test_derived_record_writes_are_sealed_outside_building_candidates(tmp_path, state):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = revision_for(storage, f"source_sealed_{state}")
    snapshot = candidate(storage, f"run_sealed_{state}", [revision])
    storage.transition_snapshot(snapshot.snapshot_id, "validating")
    if state == "failed":
        storage.transition_snapshot(snapshot.snapshot_id, "failed", failure_reason="synthetic failure")
    elif state == "published":
        storage.transition_snapshot(snapshot.snapshot_id, "published")
    elif state == "archived":
        storage.transition_snapshot(snapshot.snapshot_id, "published")
        replacement = candidate(
            storage, f"run_sealed_{state}_replacement", [revision_for(storage, f"replacement_{state}")]
        )
        storage.transition_snapshot(replacement.snapshot_id, "validating")
        storage.transition_snapshot(replacement.snapshot_id, "published")

    record = Claim.create(
        snapshot_id=snapshot.snapshot_id,
        anchors=("anc_sealed",),
        dependencies=(RecordDependency("source_revision", revision.revision_id),),
        validation_state="pending" if state == "published" else "validated",
        lifecycle_state="candidate",
        qualification="Synthetic post-transition write.",
        subject="sealed",
        predicate="has",
        object="boundary",
    )
    with pytest.raises(ValueError, match="building candidate"):
        storage.store_derived_record(record)
    with storage._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="building candidate"):
            connection.execute(
                """
                INSERT INTO derived_records(
                    record_id, snapshot_id, family, derived_kind, evidence_state,
                    validation_state, lifecycle_state, qualification
                ) VALUES (?, ?, 'claim', 'statement', 'raw_taught', 'pending', 'candidate', 'Synthetic bypass.')
                """,
                (f"rec_bypass_{state}", snapshot.snapshot_id),
            )

    assert storage.derived_records(snapshot.snapshot_id, include_stale=True) == []
    if state == "published":
        assert storage.current_snapshot() == storage.snapshot(snapshot.snapshot_id)


def test_archived_snapshots_reject_normal_transitions(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    first = candidate(storage, "run_archived_first", [revision_for(storage, "source_archived_first")])
    storage.transition_snapshot(first.snapshot_id, "validating")
    storage.transition_snapshot(first.snapshot_id, "published")
    second = candidate(storage, "run_archived_second", [revision_for(storage, "source_archived_second")])
    storage.transition_snapshot(second.snapshot_id, "validating")
    storage.transition_snapshot(second.snapshot_id, "published")

    with pytest.raises(ValueError, match="archived.*published"):
        storage.transition_snapshot(first.snapshot_id, "published")


def test_sqlite_seal_blocks_completion_of_a_building_stage_after_publication(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = revision_for(storage, "source_staged")
    snapshot = candidate(storage, "run_staged", [revision])
    with storage._connect() as connection:
        connection.execute(
            """
            INSERT INTO derived_records(
                record_id, snapshot_id, family, derived_kind, evidence_state,
                validation_state, lifecycle_state, qualification
            ) VALUES ('rec_staged', ?, 'claim', 'statement', 'raw_taught', 'validated', 'candidate', 'Synthetic stage.')
            """,
            (snapshot.snapshot_id,),
        )
    with storage._connect() as connection:
        connection.execute(
            "UPDATE corpus_snapshots SET status = 'published' WHERE snapshot_id = ?",
            (snapshot.snapshot_id,),
        )

    with storage._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="building candidate"):
            connection.execute(
                "INSERT INTO derived_record_anchors(record_id, position, anchor_id) VALUES ('rec_staged', 0, 'anc_staged')"
            )


def test_sqlite_seal_blocks_moving_a_published_stage_to_a_building_snapshot(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    published_revision = revision_for(storage, "source_move_published")
    published = candidate(storage, "run_move_published", [published_revision])
    with storage._connect() as connection:
        connection.execute(
            """
            INSERT INTO derived_records(
                record_id, snapshot_id, family, derived_kind, evidence_state,
                validation_state, lifecycle_state, qualification
            ) VALUES ('rec_movable', ?, 'claim', 'statement', 'raw_taught', 'validated', 'candidate', 'Synthetic stage.')
            """,
            (published.snapshot_id,),
        )
    with storage._connect() as connection:
        connection.execute(
            "UPDATE corpus_snapshots SET status = 'published' WHERE snapshot_id = ?",
            (published.snapshot_id,),
        )
    building = candidate(storage, "run_move_building", [revision_for(storage, "source_move_building")])

    with storage._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="building candidate"):
            connection.execute(
                "UPDATE derived_records SET snapshot_id = ? WHERE record_id = 'rec_movable'",
                (building.snapshot_id,),
            )


def test_candidate_gate_rejects_unfinished_staged_records(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = revision_for(storage, "source_unfinished")
    snapshot = candidate(storage, "run_unfinished", [revision], record_gate=False)
    with storage._connect() as connection:
        connection.execute(
            """
            INSERT INTO derived_records(
                record_id, snapshot_id, family, derived_kind, evidence_state,
                validation_state, lifecycle_state, qualification
            ) VALUES ('rec_unfinished', ?, 'claim', 'statement', 'raw_taught', 'validated', 'candidate', 'Synthetic stage.')
            """,
            (snapshot.snapshot_id,),
        )

    assert storage.record_candidate_gate(
        snapshot.snapshot_id,
        (SourceProcessingResult(revision.revision_id, "processed", 0),),
        checked_at=1_700_000_007.0,
    ) == CandidateGateResult(
        snapshot.snapshot_id,
        "failed",
        1_700_000_007.0,
        "candidate records must be finalized and validated",
    )


def test_publication_rejects_content_added_after_a_passing_gate(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = revision_for(storage, "source_gate_drift")
    snapshot = candidate(storage, "run_gate_drift", [revision])
    storage.store_derived_record(
        Claim.create(
            snapshot_id=snapshot.snapshot_id,
            anchors=("anc_gate_drift",),
            dependencies=(RecordDependency("source_revision", revision.revision_id),),
            validation_state="validated",
            lifecycle_state="candidate",
            qualification="Synthetic post-gate record.",
            subject="gate",
            predicate="binds",
            object="records",
        )
    )
    storage.transition_snapshot(snapshot.snapshot_id, "validating")

    with pytest.raises(ValueError, match="candidate gate no longer matches candidate content"):
        storage.transition_snapshot(snapshot.snapshot_id, "published")
