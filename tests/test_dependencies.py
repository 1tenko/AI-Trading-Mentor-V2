from hashlib import sha256

import pytest

from mentor.compilation import CompilationRun, CorpusSnapshot, SourceProcessingResult
from mentor.dependencies import DependencyEdge, DependencyGraph, DependencyNode
from mentor.derived_records import Claim, RecordDependency
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import Storage


def node(kind: str, identifier: str) -> DependencyNode:
    return DependencyNode(kind, identifier)


def edge(dependency: DependencyNode, dependent: DependencyNode) -> DependencyEdge:
    return DependencyEdge(dependency, dependent)


def test_dependency_graph_rejects_self_and_multi_record_cycles():
    record_a = node("derived_record", "rec_a")
    record_b = node("derived_record", "rec_b")

    with pytest.raises(ValueError, match="cycle"):
        DependencyGraph((edge(record_a, record_a),)).assert_acyclic()
    with pytest.raises(ValueError, match="cycle"):
        DependencyGraph((edge(record_a, record_b), edge(record_b, record_a))).assert_acyclic()


def test_revision_change_returns_only_the_reverse_reachable_rebuild_closure():
    revision_a = node("source_revision", "rev_a")
    revision_b = node("source_revision", "rev_b")
    extracted = node("derived_record", "rec_extracted")
    synthesis = node("derived_record", "rec_synthesis")
    higher_synthesis = node("derived_record", "rec_higher")
    unaffected = node("derived_record", "rec_unaffected")
    graph = DependencyGraph(
        (
            edge(revision_a, extracted),
            edge(extracted, synthesis),
            edge(synthesis, higher_synthesis),
            edge(revision_b, unaffected),
        )
    )

    assert graph.stale_record_ids(("rev_a",)) == ("rec_extracted", "rec_higher", "rec_synthesis")
    assert graph.rebuild_record_ids(("rev_a",)) == ("rec_extracted", "rec_higher", "rec_synthesis")


def test_candidate_validation_uses_the_dependency_cycle_gate(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = source_revision(storage, "source_cycle")
    snapshot = candidate(storage, (revision,))
    record_a = node("derived_record", "rec_a")
    record_b = node("derived_record", "rec_b")
    cyclic_graph = DependencyGraph((edge(record_a, record_b), edge(record_b, record_a)))
    monkeypatch.setattr(storage, "_dependency_graph", lambda _connection, _snapshot_id: cyclic_graph)

    with pytest.raises(ValueError, match="cycle"):
        storage.transition_snapshot(snapshot.snapshot_id, "validating")


def source_revision(storage: Storage, identity_key: str) -> SourceRevision:
    collection = Collection("collection_dependencies", "Dependencies", "trading", True, "test")
    source = Source.create(
        collection_id=collection.collection_id,
        identity_key=identity_key,
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title=identity_key,
        year=2026,
        original_filename=f"{identity_key}.txt",
        local_provenance=f"C:/synthetic/{identity_key}.txt",
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256=sha256(identity_key.encode()).hexdigest(),
        byte_size=1,
        local_locator=f"C:/synthetic/{identity_key}.txt",
        observed_at=1_700_000_000.0,
        lifecycle_state="active",
    )
    storage.store_collection(collection)
    storage.store_source(source)
    storage.store_source_revision(revision)
    return revision


def candidate(
    storage: Storage, revisions: tuple[SourceRevision, ...], *, run_id: str = "run_dependencies"
) -> CorpusSnapshot:
    run = CompilationRun(run_id, "test-model", "prompt-v1", "schema-v1", 1_700_000_000.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=revisions,
        raw_store_id="raw_dependencies",
        derived_store_id="derived_dependencies",
        created_at=1_700_000_001.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    storage.record_candidate_gate(
        snapshot.snapshot_id,
        tuple(SourceProcessingResult(revision.revision_id, "processed", 0) for revision in revisions),
        checked_at=1_700_000_002.0,
    )
    return snapshot


def claim(snapshot_id: str, subject: str, dependencies: tuple[RecordDependency, ...]) -> Claim:
    return Claim.create(
        snapshot_id=snapshot_id,
        anchors=(f"anc_{subject}",),
        dependencies=dependencies,
        validation_state="validated",
        lifecycle_state="candidate",
        qualification="Synthetic dependency support.",
        subject=subject,
        predicate="has",
        object="meaning",
    )


def test_storage_marks_transitive_records_stale_and_rejects_them_from_normal_retrieval(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    first_revision = source_revision(storage, "source_a")
    second_revision = source_revision(storage, "source_b")
    snapshot = candidate(storage, (first_revision, second_revision))
    extracted = claim(
        snapshot.snapshot_id,
        "extracted",
        (RecordDependency("source_revision", first_revision.revision_id),),
    )
    synthesis = claim(
        snapshot.snapshot_id,
        "synthesis",
        (
            RecordDependency("source_revision", first_revision.revision_id),
            RecordDependency("derived_record", extracted.record_id),
        ),
    )
    unaffected = claim(
        snapshot.snapshot_id,
        "unaffected",
        (RecordDependency("source_revision", second_revision.revision_id),),
    )
    for record in (extracted, synthesis, unaffected):
        storage.store_derived_record(record)

    assert storage.mark_stale_for_revisions(snapshot.snapshot_id, (first_revision.revision_id,)) == (
        extracted.record_id,
        synthesis.record_id,
    )
    assert storage.stale_record_ids(snapshot.snapshot_id) == (extracted.record_id, synthesis.record_id)
    assert storage.rebuild_record_ids(snapshot.snapshot_id, (first_revision.revision_id,)) == (
        extracted.record_id,
        synthesis.record_id,
    )
    with pytest.raises(ValueError, match="stale"):
        storage.transition_snapshot(snapshot.snapshot_id, "validating")
    assert [record.record_id for record in storage.derived_records(snapshot.snapshot_id)] == [unaffected.record_id]
    assert [record.record_id for record in storage.derived_records(snapshot.snapshot_id, include_stale=True)] == sorted(
        (extracted.record_id, synthesis.record_id, unaffected.record_id)
    )


def test_candidate_validation_rejects_dependencies_outside_its_raw_snapshot(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    selected_revision = source_revision(storage, "source_selected")
    outside_revision = source_revision(storage, "source_outside")
    snapshot = candidate(storage, (selected_revision,))
    storage.store_derived_record(
        claim(
            snapshot.snapshot_id,
            "outside",
            (RecordDependency("source_revision", outside_revision.revision_id),),
        )
    )

    with pytest.raises(ValueError, match="outside the candidate raw snapshot"):
        storage.transition_snapshot(snapshot.snapshot_id, "validating")


def test_published_snapshot_rejects_invalidation_without_changing_current_retrieval(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = source_revision(storage, "source_published")
    snapshot = candidate(storage, (revision,))
    record = claim(
        snapshot.snapshot_id,
        "published",
        (RecordDependency("source_revision", revision.revision_id),),
    )
    storage.store_derived_record(record)
    storage.record_candidate_gate(
        snapshot.snapshot_id,
        (SourceProcessingResult(revision.revision_id, "processed", 1),),
        checked_at=1_700_000_003.0,
    )
    storage.transition_snapshot(snapshot.snapshot_id, "validating")
    storage.transition_snapshot(snapshot.snapshot_id, "published")

    with pytest.raises(ValueError, match="building"):
        storage.mark_stale_for_revisions(snapshot.snapshot_id, (revision.revision_id,))

    assert storage.current_snapshot().snapshot_id == snapshot.snapshot_id
    assert storage.stale_record_ids(snapshot.snapshot_id) == ()
    assert storage.derived_records(snapshot.snapshot_id) == [record]


def test_stale_and_rebuild_require_a_revision_in_the_target_snapshot(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = source_revision(storage, "source_selected_only")
    snapshot = candidate(storage, (revision,))

    with pytest.raises(ValueError, match="selected by the target snapshot"):
        storage.mark_stale_for_revisions(snapshot.snapshot_id, ("rev_typo",))
    with pytest.raises(ValueError, match="selected by the target snapshot"):
        storage.rebuild_record_ids(snapshot.snapshot_id, ("rev_typo",))


def test_storage_rejects_new_direct_and_transitive_dependencies_on_stale_records(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = source_revision(storage, "source_stale")
    snapshot = candidate(storage, (revision,))
    stale = claim(
        snapshot.snapshot_id,
        "stale",
        (RecordDependency("source_revision", revision.revision_id),),
    )
    intermediary = claim(
        snapshot.snapshot_id,
        "intermediary",
        (
            RecordDependency("source_revision", revision.revision_id),
            RecordDependency("derived_record", stale.record_id),
        ),
    )
    storage.store_derived_record(stale)
    storage.store_derived_record(intermediary)
    with storage._connect() as connection:
        connection.execute(
            "INSERT INTO derived_record_staleness(snapshot_id, record_id, revision_id) VALUES (?, ?, ?)",
            (snapshot.snapshot_id, stale.record_id, revision.revision_id),
        )

    direct = claim(
        snapshot.snapshot_id,
        "direct",
        (RecordDependency("derived_record", stale.record_id),),
    )
    transitive = claim(
        snapshot.snapshot_id,
        "transitive",
        (RecordDependency("derived_record", intermediary.record_id),),
    )
    with pytest.raises(ValueError, match="stale"):
        storage.store_derived_record(direct)
    with pytest.raises(ValueError, match="stale"):
        storage.store_derived_record(transitive)


def test_storage_rejects_cross_snapshot_dependencies_and_hides_legacy_cross_snapshot_records(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    first_revision = source_revision(storage, "source_first_snapshot")
    second_revision = source_revision(storage, "source_second_snapshot")
    first_snapshot = candidate(storage, (first_revision,), run_id="run_first")
    second_snapshot = candidate(storage, (second_revision,), run_id="run_second")
    first_record = claim(
        first_snapshot.snapshot_id,
        "first",
        (RecordDependency("source_revision", first_revision.revision_id),),
    )
    cross_snapshot = claim(
        second_snapshot.snapshot_id,
        "cross",
        (
            RecordDependency("source_revision", second_revision.revision_id),
            RecordDependency("derived_record", first_record.record_id),
        ),
    )
    storage.store_derived_record(first_record)

    with pytest.raises(ValueError, match="different snapshot"):
        storage.store_derived_record(cross_snapshot)

    with monkeypatch.context() as legacy_store:
        legacy_store.setattr(storage, "_assert_derived_dependencies_belong_to_snapshot", lambda _connection, _record: None)
        legacy_store.setattr(storage, "_depends_on_stale_record", lambda _connection, _record, **_kwargs: False)
        storage.store_derived_record(cross_snapshot)
    storage.mark_stale_for_revisions(first_snapshot.snapshot_id, (first_revision.revision_id,))

    assert storage.derived_records(second_snapshot.snapshot_id) == []
    assert storage.derived_records(second_snapshot.snapshot_id, include_stale=True) == [cross_snapshot]


def test_normal_retrieval_hides_legacy_transitive_dependents_of_stale_records(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = source_revision(storage, "source_legacy")
    snapshot = candidate(storage, (revision,))
    base = claim(
        snapshot.snapshot_id,
        "base",
        (RecordDependency("source_revision", revision.revision_id),),
    )
    dependent = claim(
        snapshot.snapshot_id,
        "dependent",
        (
            RecordDependency("source_revision", revision.revision_id),
            RecordDependency("derived_record", base.record_id),
        ),
    )
    storage.store_derived_record(base)
    storage.store_derived_record(dependent)
    with storage._connect() as connection:
        connection.execute(
            "INSERT INTO derived_record_staleness(snapshot_id, record_id, revision_id) VALUES (?, ?, ?)",
            (snapshot.snapshot_id, base.record_id, revision.revision_id),
        )

    assert storage.derived_records(snapshot.snapshot_id) == []
    assert storage.derived_records(snapshot.snapshot_id, include_stale=True) == sorted(
        (base, dependent), key=lambda record: record.record_id
    )


@pytest.mark.parametrize("target_status", ("validating", "failed", "published"))
def test_only_building_candidates_can_be_marked_stale(tmp_path, target_status):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    revision = source_revision(storage, f"source_{target_status}")
    snapshot = candidate(storage, (revision,), run_id=f"run_{target_status}")
    storage.transition_snapshot(snapshot.snapshot_id, "validating")
    if target_status == "failed":
        storage.transition_snapshot(snapshot.snapshot_id, "failed", failure_reason="synthetic failure")
    elif target_status == "published":
        storage.transition_snapshot(snapshot.snapshot_id, "published")

    with pytest.raises(ValueError, match="building"):
        storage.mark_stale_for_revisions(snapshot.snapshot_id, (revision.revision_id,))
