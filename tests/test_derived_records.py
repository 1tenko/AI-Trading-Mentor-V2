from dataclasses import replace
from hashlib import sha256

import sqlite3

import pytest

from mentor.compilation import CompilationRun, CorpusSnapshot
from mentor.derived_records import (
    Claim,
    ConflictUnresolved,
    Evolution,
    Facet,
    ProcedureSequenceHierarchy,
    RecordDependency,
    Relationship,
    create_record,
)
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import Storage


def claim(**changes):
    values = {
        "snapshot_id": "snap_synthetic",
        "anchors": ("anc_synthetic",),
        "dependencies": (RecordDependency("source_revision", "rev_synthetic"),),
        "validation_state": "validated",
        "lifecycle_state": "active",
        "qualification": "Only under the stated synthetic condition.",
        "subject": "signal",
        "predicate": "states",
        "object": "a constrained observation",
    }
    values.update(changes)
    return Claim.create(**values)


def test_typed_families_have_a_complete_shared_envelope():
    common = {
        "snapshot_id": "snap_synthetic",
        "anchors": ("anc_synthetic",),
        "dependencies": (RecordDependency("source_revision", "rev_synthetic"),),
        "validation_state": "validated",
        "lifecycle_state": "active",
        "qualification": "Only under the stated synthetic condition.",
    }

    records = (
        claim(),
        Relationship.create(**common, left="signal", relation="supports", right="observation"),
        ProcedureSequenceHierarchy.create(**common, kind="sequence", terms=("first", "then")),
        Evolution.create(**common, subject="definition", previous="earlier", current="later"),
        ConflictUnresolved.create(**common, kind="unresolved", subject="question", alternatives=("option a", "option b")),
    )

    assert {record.family for record in records} == {
        "claim",
        "relationship",
        "procedure_sequence_hierarchy",
        "evolution",
        "conflict_unresolved",
    }
    for record in records:
        assert record.record_id.startswith("rec_")
        assert record.snapshot_id == "snap_synthetic"
        assert record.anchors == ("anc_synthetic",)
        assert record.dependencies == (RecordDependency("source_revision", "rev_synthetic"),)
        assert record.qualification == "Only under the stated synthetic condition."


def test_strategy_implications_default_to_cross_source_synthesis_unless_raw_taught():
    inferred = claim(derived_kind="strategy_implication")
    raw_taught = claim(derived_kind="strategy_implication", evidence_state="raw_taught")

    assert inferred.evidence_state == "cross_source_synthesis"
    assert raw_taught.evidence_state == "raw_taught"


@pytest.mark.parametrize(
    "factory, message",
    [
        (lambda: create_record("unknown", **_envelope()), "unknown derived record family"),
        (lambda: claim(anchors=()), "at least one anchor"),
        (lambda: claim(dependencies=()), "at least one dependency"),
        (lambda: claim(validation_state="guessed"), "invalid validation_state"),
        (lambda: claim(facets={"scope": "unbounded"}), "bounded typed facets"),
        (lambda: claim(facets=(Facet("confidence", 0.9),)), "confidence"),
        (lambda: claim(facets=(Facet("reasoning", "private chain"),)), "private reasoning"),
    ],
)
def test_record_construction_rejects_unbounded_or_unsafe_data(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()


def _envelope():
    return {
        "snapshot_id": "snap_synthetic",
        "anchors": ("anc_synthetic",),
        "dependencies": (RecordDependency("source_revision", "rev_synthetic"),),
        "validation_state": "validated",
        "lifecycle_state": "active",
        "qualification": "Only under the stated synthetic condition.",
    }


def test_storage_persists_typed_records_without_a_json_payload(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)
    record = claim(
        snapshot_id=snapshot.snapshot_id,
        dependencies=(RecordDependency("source_revision", snapshot.selected_revision_ids[0]),),
        facets=(Facet("scope", "synthetic"),),
    )

    storage.store_derived_record(record)
    storage.store_derived_record(record)

    assert storage.derived_records(snapshot.snapshot_id) == [record]
    with storage._connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(derived_records)")}
    assert "payload_json" not in columns


def test_storage_round_trips_each_typed_family(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)
    common = {
        "snapshot_id": snapshot.snapshot_id,
        "anchors": ("anc_synthetic",),
        "dependencies": (RecordDependency("source_revision", snapshot.selected_revision_ids[0]),),
        "validation_state": "validated",
        "lifecycle_state": "active",
        "qualification": "Only under the stated synthetic condition.",
    }
    records = [
        claim(**common),
        Relationship.create(**common, left="signal", relation="supports", right="observation"),
        ProcedureSequenceHierarchy.create(**common, kind="procedure", terms=("observe", "act")),
        Evolution.create(**common, subject="definition", previous="earlier", current="later"),
        ConflictUnresolved.create(**common, kind="conflict", subject="question", alternatives=("option a", "option b")),
    ]

    for record in records:
        storage.store_derived_record(record)

    assert storage.derived_records(snapshot.snapshot_id) == sorted(records, key=lambda record: record.record_id)


def test_storage_revalidates_tampered_record_data_at_its_boundary(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)
    record = claim(
        snapshot_id=snapshot.snapshot_id,
        dependencies=(RecordDependency("source_revision", snapshot.selected_revision_ids[0]),),
    )

    with pytest.raises(ValueError, match="private reasoning"):
        storage.store_derived_record(replace(record, facets=(Facet("reasoning", "private chain"),)))


def test_sqlite_rejects_direct_finalization_without_required_children(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)

    with storage._connect() as connection:
        connection.execute(
            """
            INSERT INTO derived_records(
                record_id, snapshot_id, family, derived_kind, evidence_state, validation_state,
                lifecycle_state, qualification
            ) VALUES ('rec_incomplete', ?, 'claim', 'statement', 'raw_taught', 'validated', 'active', 'Synthetic.')
            """,
            (snapshot.snapshot_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="anchors"):
            connection.execute("UPDATE derived_records SET finalized = 1 WHERE record_id = 'rec_incomplete'")
        connection.execute("INSERT INTO derived_record_anchors VALUES ('rec_incomplete', 0, 'anc_synthetic')")
        with pytest.raises(sqlite3.IntegrityError, match="dependencies"):
            connection.execute("UPDATE derived_records SET finalized = 1 WHERE record_id = 'rec_incomplete'")

    assert storage.derived_records(snapshot.snapshot_id) == []


def test_sqlite_rejects_direct_finalization_without_a_matching_family_row(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)

    with storage._connect() as connection:
        connection.execute(
            """
            INSERT INTO derived_records(
                record_id, snapshot_id, family, derived_kind, evidence_state, validation_state,
                lifecycle_state, qualification
            ) VALUES ('rec_no_family', ?, 'claim', 'statement', 'raw_taught', 'validated', 'active', 'Synthetic.')
            """,
            (snapshot.snapshot_id,),
        )
        connection.execute("INSERT INTO derived_record_anchors VALUES ('rec_no_family', 0, 'anc_synthetic')")
        connection.execute(
            "INSERT INTO derived_record_dependencies VALUES ('rec_no_family', 0, 'source_revision', ?) ",
            (snapshot.selected_revision_ids[0],),
        )
        with pytest.raises(sqlite3.IntegrityError, match="family row"):
            connection.execute("UPDATE derived_records SET finalized = 1 WHERE record_id = 'rec_no_family'")

    assert storage.derived_records(snapshot.snapshot_id) == []


@pytest.mark.parametrize(
    "factory, message",
    [
        (lambda: claim(facets=(Facet("scope", 1),)), "bounded typed facets"),
        (lambda: claim(facets=(Facet("scope", "x" * 161),)), "facet value"),
        (lambda: claim(subject="x" * 241), "typed record value"),
        (
            lambda: ProcedureSequenceHierarchy.create(
                **_envelope(), kind="sequence", terms=tuple("term" for _ in range(9))
            ),
            "too many terms",
        ),
        (lambda: claim(facets=tuple(Facet("scope", "small") for _ in range(6))), "too many facets"),
    ],
)
def test_record_construction_rejects_unbounded_semantic_content(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()


@pytest.mark.parametrize(
    "subject, predicate, object",
    [
        ("", "states", "observation"),
        ("signal", "states", "x" * 241),
    ],
)
def test_sqlite_rejects_direct_finalization_with_invalid_claim_content(tmp_path, subject, predicate, object):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)

    with storage._connect() as connection:
        _stage_direct_claim(connection, snapshot, "rec_direct_claim", subject, predicate, object)
        with pytest.raises(sqlite3.IntegrityError, match="claim content"):
            connection.execute("UPDATE derived_records SET finalized = 1 WHERE record_id = 'rec_direct_claim'")


@pytest.mark.parametrize(
    "facets",
    [
        (("scope", "x" * 161),),
        (("scope", "first"), ("scope", "second")),
    ],
)
def test_sqlite_rejects_direct_finalization_with_invalid_facets(tmp_path, facets):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)

    with storage._connect() as connection:
        _stage_direct_claim(connection, snapshot, "rec_direct_facets", "signal", "states", "observation")
        connection.executemany(
            "INSERT INTO derived_record_facets VALUES ('rec_direct_facets', ?, ?, ?)",
            [(position, name, value) for position, (name, value) in enumerate(facets)],
        )
        with pytest.raises(sqlite3.IntegrityError, match="facets"):
            connection.execute("UPDATE derived_records SET finalized = 1 WHERE record_id = 'rec_direct_facets'")


def test_sqlite_rejects_direct_finalization_with_an_invalid_relationship(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)

    with storage._connect() as connection:
        connection.execute(
            """
            INSERT INTO derived_records(
                record_id, snapshot_id, family, derived_kind, evidence_state, validation_state,
                lifecycle_state, qualification
            ) VALUES ('rec_direct_relationship', ?, 'relationship', 'relation', 'raw_taught', 'validated', 'active', 'Synthetic.')
            """,
            (snapshot.snapshot_id,),
        )
        connection.execute("INSERT INTO derived_record_anchors VALUES ('rec_direct_relationship', 0, 'anc_synthetic')")
        connection.execute(
            "INSERT INTO derived_record_dependencies VALUES ('rec_direct_relationship', 0, 'source_revision', ?)",
            (snapshot.selected_revision_ids[0],),
        )
        connection.execute(
            "INSERT INTO derived_relationships VALUES ('rec_direct_relationship', 'left', 'invented', 'right')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="relationship content"):
            connection.execute("UPDATE derived_records SET finalized = 1 WHERE record_id = 'rec_direct_relationship'")


def test_sqlite_rejects_noncanonical_direct_record_at_finalization(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)
    valid = claim(
        snapshot_id=snapshot.snapshot_id,
        dependencies=(RecordDependency("source_revision", snapshot.selected_revision_ids[0]),),
    )
    storage.store_derived_record(valid)

    with storage._connect() as connection:
        _stage_direct_claim(connection, snapshot, f"rec_{'0' * 64}", "signal", "states", "observation")
        with pytest.raises(sqlite3.IntegrityError, match="valid typed record"):
            connection.execute(f"UPDATE derived_records SET finalized = 1 WHERE record_id = 'rec_{'0' * 64}'")

    assert storage.derived_records(snapshot.snapshot_id) == [valid]


def test_sqlite_rejects_invalid_utf8_direct_records_at_finalization(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)
    valid = claim(
        snapshot_id=snapshot.snapshot_id,
        dependencies=(RecordDependency("source_revision", snapshot.selected_revision_ids[0]),),
    )
    storage.store_derived_record(valid)

    with storage._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="valid typed record"):
            _stage_invalid_utf8_record_id(connection, snapshot)
        bad_content = claim(
            snapshot_id=snapshot.snapshot_id,
            dependencies=(RecordDependency("source_revision", snapshot.selected_revision_ids[0]),),
            subject="content seed",
        )
        _stage_direct_claim(
            connection,
            snapshot,
            bad_content.record_id,
            bad_content.subject,
            bad_content.predicate,
            bad_content.object,
            bad_content.qualification,
        )
        connection.execute(
            "UPDATE derived_claims SET subject = CAST(X'80' AS TEXT) WHERE record_id = ?",
            (bad_content.record_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="valid typed record"):
            connection.execute(
                "UPDATE derived_records SET finalized = 1 WHERE record_id = ?", (bad_content.record_id,)
            )
        bad_facet = claim(
            snapshot_id=snapshot.snapshot_id,
            dependencies=(RecordDependency("source_revision", snapshot.selected_revision_ids[0]),),
            subject="facet seed",
            facets=(Facet("scope", "synthetic"),),
        )
        _stage_direct_claim(
            connection,
            snapshot,
            bad_facet.record_id,
            bad_facet.subject,
            bad_facet.predicate,
            bad_facet.object,
            bad_facet.qualification,
        )
        connection.execute(
            "INSERT INTO derived_record_facets VALUES (?, 0, 'scope', CAST(X'80' AS TEXT))",
            (bad_facet.record_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="valid typed record"):
            connection.execute("UPDATE derived_records SET finalized = 1 WHERE record_id = ?", (bad_facet.record_id,))

    assert storage.derived_records(snapshot.snapshot_id) == [valid]


def test_sqlite_rejects_non_text_content_and_facets_at_finalization(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)

    with storage._connect() as connection:
        bad_content = claim(
            snapshot_id=snapshot.snapshot_id,
            dependencies=(RecordDependency("source_revision", snapshot.selected_revision_ids[0]),),
            subject="blob content seed",
        )
        _stage_direct_claim(
            connection,
            snapshot,
            bad_content.record_id,
            bad_content.subject,
            bad_content.predicate,
            bad_content.object,
            bad_content.qualification,
        )
        connection.execute(
            "UPDATE derived_claims SET subject = X'31' WHERE record_id = ?", (bad_content.record_id,)
        )
        with pytest.raises(sqlite3.IntegrityError, match="valid typed record"):
            connection.execute(
                "UPDATE derived_records SET finalized = 1 WHERE record_id = ?", (bad_content.record_id,)
            )

        bad_facet = claim(
            snapshot_id=snapshot.snapshot_id,
            dependencies=(RecordDependency("source_revision", snapshot.selected_revision_ids[0]),),
            subject="blob facet seed",
            facets=(Facet("scope", "synthetic"),),
        )
        _stage_direct_claim(
            connection,
            snapshot,
            bad_facet.record_id,
            bad_facet.subject,
            bad_facet.predicate,
            bad_facet.object,
            bad_facet.qualification,
        )
        connection.execute(
            "INSERT INTO derived_record_facets VALUES (?, 0, 'scope', X'31')", (bad_facet.record_id,)
        )
        with pytest.raises(sqlite3.IntegrityError, match="valid typed record"):
            connection.execute("UPDATE derived_records SET finalized = 1 WHERE record_id = ?", (bad_facet.record_id,))


def _stage_invalid_utf8_record_id(connection, snapshot):
    connection.execute(
        """
        INSERT INTO derived_records(
            record_id, snapshot_id, family, derived_kind, evidence_state, validation_state,
            lifecycle_state, qualification
        ) VALUES (CAST(X'80' AS TEXT), ?, 'claim', 'statement', 'raw_taught', 'validated', 'active', 'Synthetic.')
        """,
        (snapshot.snapshot_id,),
    )
    connection.execute("INSERT INTO derived_record_anchors VALUES (CAST(X'80' AS TEXT), 0, 'anc_synthetic')")
    connection.execute(
        "INSERT INTO derived_record_dependencies VALUES (CAST(X'80' AS TEXT), 0, 'source_revision', ?)",
        (snapshot.selected_revision_ids[0],),
    )
    connection.execute("INSERT INTO derived_claims VALUES (CAST(X'80' AS TEXT), 'signal', 'states', 'observation')")
    connection.execute("UPDATE derived_records SET finalized = 1 WHERE record_id = CAST(X'80' AS TEXT)")


def _stage_direct_claim(connection, snapshot, record_id, subject, predicate, object, qualification="Synthetic."):
    connection.execute(
        """
        INSERT INTO derived_records(
            record_id, snapshot_id, family, derived_kind, evidence_state, validation_state,
            lifecycle_state, qualification
        ) VALUES (?, ?, 'claim', 'statement', 'raw_taught', 'validated', 'active', ?)
        """,
        (record_id, snapshot.snapshot_id, qualification),
    )
    connection.execute("INSERT INTO derived_record_anchors VALUES (?, 0, 'anc_synthetic')", (record_id,))
    connection.execute(
        "INSERT INTO derived_record_dependencies VALUES (?, 0, 'source_revision', ?)",
        (record_id, snapshot.selected_revision_ids[0]),
    )
    connection.execute("INSERT INTO derived_claims VALUES (?, ?, ?, ?)", (record_id, subject, predicate, object))


def snapshot_for(storage: Storage) -> CorpusSnapshot:
    collection = Collection("collection_synthetic", "Synthetic", "test", True, "test")
    source = Source.create(
        collection_id=collection.collection_id,
        identity_key="synthetic:derived-record",
        source_type="transcript",
        author="Synthetic",
        course="Synthetic",
        lesson_title="Synthetic",
        year=2026,
        original_filename="synthetic.txt",
        local_provenance="C:/synthetic/synthetic.txt",
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256=sha256(b"synthetic").hexdigest(),
        byte_size=9,
        local_locator="C:/synthetic/synthetic.txt",
        observed_at=1.0,
        lifecycle_state="active",
    )
    storage.store_collection(collection)
    storage.store_source(source)
    storage.store_source_revision(revision)
    run = CompilationRun("run_derived", "test", "test", "test", 1.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=[revision],
        raw_store_id="raw_synthetic",
        derived_store_id="derived_synthetic",
        created_at=1.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    return snapshot
