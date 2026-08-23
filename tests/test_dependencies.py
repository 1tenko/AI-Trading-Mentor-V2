from hashlib import sha256

import pytest

from mentor.compilation import CompilationRun, CorpusSnapshot
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


def candidate(storage: Storage, revisions: tuple[SourceRevision, ...]) -> CorpusSnapshot:
    run = CompilationRun("run_dependencies", "test-model", "prompt-v1", "schema-v1", 1_700_000_000.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=revisions,
        raw_store_id="raw_dependencies",
        derived_store_id="derived_dependencies",
        created_at=1_700_000_001.0,
    )
    storage.create_compilation_candidate(run, snapshot)
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
