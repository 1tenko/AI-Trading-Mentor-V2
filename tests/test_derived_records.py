from dataclasses import replace
from hashlib import sha256
import json

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
    is_legacy_record,
)
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.synthesis import SynthesisCandidate
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
    conflict_common = common | {
        "dependencies": (
            RecordDependency("source_revision", "rev_synthetic"),
            RecordDependency("derived_record", "rec_option_a"),
            RecordDependency("derived_record", "rec_option_b"),
        )
    }

    records = (
        claim(),
        Relationship.create(**common, left="signal", relation="supports", right="observation"),
        ProcedureSequenceHierarchy.create(**common, kind="sequence", terms=("first", "then")),
        Evolution.create(
            **common,
            subject="definition",
            previous="earlier",
            current="later",
            earlier_source_set=("rev_synthetic",),
            later_source_set=("rev_synthetic",),
            classification="no_supported_classification",
            negative_evidence_state="unresolved",
            earlier_coverage_id="coverage_synthetic_earlier",
            later_coverage_id="coverage_synthetic_later",
            earlier_observed_years=(2025,),
            later_observed_years=(2026,),
        ),
        ConflictUnresolved.create(
            **conflict_common,
            kind="unresolved",
            subject="question",
            alternatives=("option a", "option b"),
            competing_record_ids=("rec_option_a", "rec_option_b"),
            reconciliation_state="unresolved",
            relevant_scopes=("synthetic scope",),
            unresolved_questions=("Which synthetic condition applies?",),
        ),
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
        assert record.dependencies[0] == RecordDependency("source_revision", "rev_synthetic")
        assert record.qualification == "Only under the stated synthetic condition."


def test_strategy_implications_default_to_cross_source_synthesis_unless_raw_taught():
    inferred = claim(derived_kind="strategy_implication")
    raw_taught = claim(derived_kind="strategy_implication", evidence_state="raw_taught")

    assert inferred.evidence_state == "cross_source_synthesis"
    assert raw_taught.evidence_state == "raw_taught"


def test_evolution_requires_explicit_coverage_and_rejects_unsupported_change_claims():
    common = {
        **_envelope(),
        "anchors": ("anc_synthetic", "anc_competing"),
        "dependencies": (
            RecordDependency("source_revision", "rev_2025_a"),
            RecordDependency("source_revision", "rev_2025_b"),
            RecordDependency("source_revision", "rev_2026_a"),
        ),
    }

    refined = Evolution.create(
        **common,
        subject="synthetic concept",
        previous="earlier qualified teaching",
        current="later qualified teaching",
        earlier_source_set=("rev_2025_a", "rev_2025_b"),
        later_source_set=("rev_2026_a",),
        classification="refined",
        negative_evidence_state="positive_teaching",
        competing_anchors=("anc_competing",),
        earlier_coverage_id="coverage_2025",
        later_coverage_id="coverage_2026",
        earlier_observed_years=(2025,),
        later_observed_years=(2026,),
    )

    assert refined.earlier_source_set == ("rev_2025_a", "rev_2025_b")
    assert refined.later_source_set == ("rev_2026_a",)
    assert refined.classification == "refined"
    assert refined.negative_evidence_state == "positive_teaching"
    assert refined.competing_anchors == ("anc_competing",)
    assert refined.evidence_state == "cross_source_synthesis"

    with pytest.raises(ValueError, match="source asserted absence"):
        Evolution.create(
            **common,
            subject="synthetic concept",
            previous="not observed earlier",
            current="later teaching",
            earlier_source_set=("rev_2025_a",),
            later_source_set=("rev_2026_a",),
            classification="introduced",
            negative_evidence_state="not_found_in_observed_evidence",
            earlier_coverage_id="coverage_2025",
            later_coverage_id="coverage_2026",
            earlier_observed_years=(2025,),
            later_observed_years=(2026,),
        )
    with pytest.raises(ValueError, match="evolution source set"):
        Evolution.create(
            **common,
            subject="synthetic concept",
            previous="earlier teaching",
            current="later teaching",
            earlier_source_set=(),
            later_source_set=("rev_2026_a",),
            classification="no_supported_classification",
            negative_evidence_state="unresolved",
            earlier_coverage_id="coverage_2025",
            later_coverage_id="coverage_2026",
            earlier_observed_years=(2025,),
            later_observed_years=(2026,),
        )
    with pytest.raises(ValueError, match="source revision dependencies"):
        Evolution.create(
            **common,
            subject="synthetic concept",
            previous="earlier teaching",
            current="later teaching",
            earlier_source_set=("rev_untracked",),
            later_source_set=("rev_2026_a",),
            classification="no_supported_classification",
            negative_evidence_state="unresolved",
            earlier_coverage_id="coverage_2025",
            later_coverage_id="coverage_2026",
            earlier_observed_years=(2025,),
            later_observed_years=(2026,),
        )


def test_conflict_records_keep_competing_inputs_visible_until_conditionally_reconciled():
    common = {
        **_envelope(),
        "dependencies": (
            RecordDependency("source_revision", "rev_synthetic"),
            RecordDependency("derived_record", "rec_option_a"),
            RecordDependency("derived_record", "rec_option_b"),
        ),
    }

    compatible = ConflictUnresolved.create(
        **common,
        kind="conflict",
        subject="synthetic condition",
        alternatives=("option a", "option b"),
        competing_record_ids=("rec_option_a", "rec_option_b"),
        reconciliation_state="compatible_under_conditions",
        facets=(Facet("condition", "Different synthetic contexts."),),
        relevant_scopes=("synthetic scope",),
        conditions=("Different synthetic contexts.",),
    )
    unresolved = ConflictUnresolved.create(
        **common,
        kind="unresolved",
        subject="synthetic condition",
        alternatives=("option a", "option b"),
        competing_record_ids=("rec_option_a", "rec_option_b"),
        reconciliation_state="unresolved",
        relevant_scopes=("synthetic scope",),
        unresolved_questions=("Which synthetic condition applies?",),
    )

    assert compatible.competing_record_ids == ("rec_option_a", "rec_option_b")
    assert compatible.reconciliation_state == "compatible_under_conditions"
    assert unresolved.reconciliation_state == "unresolved"
    with pytest.raises(ValueError, match="condition"):
        ConflictUnresolved.create(
            **common,
            kind="conflict",
            subject="synthetic condition",
            alternatives=("option a", "option b"),
            competing_record_ids=("rec_option_a", "rec_option_b"),
            reconciliation_state="compatible_under_conditions",
            relevant_scopes=("synthetic scope",),
        )


def test_evolution_and_conflict_reject_raw_evidence_private_text_and_unsupported_classifications():
    evolution_common = {
        **_envelope(),
        "anchors": ("anc_2025", "anc_2026", "anc_deprecation"),
        "dependencies": (
            RecordDependency("source_revision", "rev_2025"),
            RecordDependency("source_revision", "rev_2026"),
        ),
        "subject": "synthetic concept",
        "previous": "earlier teaching",
        "current": "later teaching",
        "earlier_source_set": ("rev_2025",),
        "later_source_set": ("rev_2026",),
        "earlier_coverage_id": "coverage_2025",
        "later_coverage_id": "coverage_2026",
        "earlier_observed_years": (2025,),
        "later_observed_years": (2026,),
    }
    conflict_common = {
        **_envelope(),
        "dependencies": (
            RecordDependency("source_revision", "rev_synthetic"),
            RecordDependency("derived_record", "rec_a"),
            RecordDependency("derived_record", "rec_b"),
        ),
        "kind": "conflict",
        "subject": "synthetic question",
        "alternatives": ("option a", "option b"),
        "competing_record_ids": ("rec_a", "rec_b"),
        "reconciliation_state": "genuinely_contradictory",
        "relevant_scopes": ("synthetic scope",),
    }

    for private_text in (
        "My private analysis: hidden details.",
        "Scratch pad: hidden details.",
            "Transcript excerpt: exact speaker words.",
    ):
        with pytest.raises(ValueError, match="private|raw"):
            Evolution.create(
                **(evolution_common | {"qualification": private_text}),
                classification="no_supported_classification",
                negative_evidence_state="unresolved",
            )
        with pytest.raises(ValueError, match="private|raw"):
            ConflictUnresolved.create(**conflict_common, conditions=(private_text,))

    with pytest.raises(ValueError, match="source synthesis"):
        Evolution.create(
            **evolution_common,
            evidence_state="raw_taught",
            classification="no_supported_classification",
            negative_evidence_state="unresolved",
        )
    with pytest.raises(ValueError, match="source synthesis"):
        ConflictUnresolved.create(**conflict_common, evidence_state="raw_taught")
    with pytest.raises(ValueError, match="positive or coverage evidence"):
        Evolution.create(
            **evolution_common,
            classification="refined",
            negative_evidence_state="unresolved",
        )
    with pytest.raises(ValueError, match="direct deprecation evidence"):
        Evolution.create(
            **evolution_common,
            classification="deprecated_or_deemphasized",
            negative_evidence_state="source_asserted_absence",
        )

    deprecated = Evolution.create(
        **evolution_common,
        classification="deprecated_or_deemphasized",
        negative_evidence_state="positive_teaching",
        deprecation_evidence_anchors=("anc_deprecation",),
    )
    assert deprecated.deprecation_evidence_anchors == ("anc_deprecation",)


@pytest.mark.parametrize(
    ("field", "wording"),
    [
        ("qualification", "Jacob never taught this before 2026."),
        ("current", "This was new in 2026 and absent before."),
        ("previous", "The earlier teaching was removed in 2026."),
    ],
)
def test_evolution_rejects_negative_claim_wording_without_structural_evidence(field, wording):
    common = {
        **_envelope(),
        "anchors": ("anc_2025", "anc_2026", "anc_deprecation"),
        "dependencies": (
            RecordDependency("source_revision", "rev_2025"),
            RecordDependency("source_revision", "rev_2026"),
        ),
        "subject": "synthetic concept",
        "previous": "earlier teaching",
        "current": "later teaching",
        "earlier_source_set": ("rev_2025",),
        "later_source_set": ("rev_2026",),
        "classification": "no_supported_classification",
        "negative_evidence_state": "not_found_in_observed_evidence",
        "earlier_coverage_id": "coverage_2025",
        "later_coverage_id": "coverage_2026",
        "earlier_observed_years": (2025,),
        "later_observed_years": (2026,),
    }

    with pytest.raises(ValueError, match="negative claim wording"):
        Evolution.create(**(common | {field: wording}))

    direct_deprecation = Evolution.create(
        **(
            common
            | {
                "qualification": "The earlier teaching was deprecated in later material.",
                "classification": "deprecated_or_deemphasized",
                "negative_evidence_state": "positive_teaching",
                "deprecation_evidence_anchors": ("anc_deprecation",),
            }
        ),
    )
    assert direct_deprecation.classification == "deprecated_or_deemphasized"


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
    conflict_common = common | {
        "dependencies": (
            RecordDependency("source_revision", snapshot.selected_revision_ids[0]),
            RecordDependency("derived_record", "rec_option_a"),
            RecordDependency("derived_record", "rec_option_b"),
        )
    }
    records = [
        claim(**common),
        Relationship.create(**common, left="signal", relation="supports", right="observation"),
        ProcedureSequenceHierarchy.create(**common, kind="procedure", terms=("observe", "act")),
        Evolution.create(
            **common,
            subject="definition",
            previous="earlier",
            current="later",
            earlier_source_set=(snapshot.selected_revision_ids[0],),
            later_source_set=(snapshot.selected_revision_ids[0],),
            classification="no_supported_classification",
            negative_evidence_state="unresolved",
            earlier_coverage_id="coverage_synthetic_earlier",
            later_coverage_id="coverage_synthetic_later",
            earlier_observed_years=(2025,),
            later_observed_years=(2026,),
        ),
        ConflictUnresolved.create(
            **conflict_common,
            kind="conflict",
            subject="question",
            alternatives=("option a", "option b"),
            competing_record_ids=("rec_option_a", "rec_option_b"),
            reconciliation_state="genuinely_contradictory",
            relevant_scopes=("synthetic scope",),
        ),
    ]

    for record in records:
        storage.store_derived_record(record)

    assert storage.derived_records(snapshot.snapshot_id) == sorted(records, key=lambda record: record.record_id)


def test_storage_round_trips_explicit_conflict_context_fields(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)
    conflict = ConflictUnresolved.create(
        snapshot_id=snapshot.snapshot_id,
        anchors=("anc_synthetic",),
        dependencies=(
            RecordDependency("source_revision", snapshot.selected_revision_ids[0]),
            RecordDependency("derived_record", "rec_a"),
            RecordDependency("derived_record", "rec_b"),
        ),
        validation_state="validated",
        lifecycle_state="active",
        qualification="Synthetic unresolved conflict.",
        kind="unresolved",
        subject="synthetic question",
        alternatives=("option a", "option b"),
        competing_record_ids=("rec_a", "rec_b"),
        reconciliation_state="unresolved",
        relevant_scopes=("synthetic scope",),
        conditions=("Synthetic condition.",),
        unresolved_questions=("Which option applies?",),
    )

    storage.store_derived_record(conflict)

    assert storage.derived_records(snapshot.snapshot_id) == [conflict]


def test_storage_migrates_a_genuine_old_evolution_row_without_changing_its_identity(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)
    legacy_values = {
        "record_id": "",
        "snapshot_id": snapshot.snapshot_id,
        "family": "evolution",
        "derived_kind": "change",
        "evidence_state": "raw_taught",
        "validation_state": "validated",
        "lifecycle_state": "active",
        "anchors": ["anc_synthetic"],
        "dependencies": [{"kind": "source_revision", "identifier": snapshot.selected_revision_ids[0]}],
        "qualification": "Synthetic legacy support.",
        "facets": [],
        "subject": "definition",
        "previous": "earlier",
        "current": "later",
    }
    legacy_id = f"rec_{sha256(json.dumps(legacy_values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"

    with storage._connect() as connection:
        connection.execute("DROP TRIGGER derived_records_require_staging")
        connection.execute("DROP TRIGGER derived_records_require_children")
        connection.execute("DROP TRIGGER derived_records_lock_finalized_rows")
        connection.execute("DROP TABLE derived_evolutions")
        connection.execute(
            """
            CREATE TABLE derived_evolutions (
                record_id TEXT PRIMARY KEY REFERENCES derived_records(record_id),
                subject TEXT NOT NULL,
                previous_value TEXT NOT NULL,
                current_value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO derived_records(
                record_id, snapshot_id, family, derived_kind, evidence_state, validation_state,
                lifecycle_state, qualification, finalized
            ) VALUES (?, ?, 'evolution', 'change', 'raw_taught', 'validated', 'active', 'Synthetic legacy support.', 0)
            """,
            (legacy_id, snapshot.snapshot_id),
        )
        connection.execute("INSERT INTO derived_record_anchors VALUES (?, 0, 'anc_synthetic')", (legacy_id,))
        connection.execute(
            "INSERT INTO derived_record_dependencies VALUES (?, 0, 'source_revision', ?)",
            (legacy_id, snapshot.selected_revision_ids[0]),
        )
        connection.execute("INSERT INTO derived_evolutions VALUES (?, 'definition', 'earlier', 'later')", (legacy_id,))

    storage.initialize()
    [loaded] = storage.derived_records(snapshot.snapshot_id)

    assert loaded.record_id == legacy_id
    assert is_legacy_record(loaded)
    assert SynthesisCandidate.from_records(snapshot_id=snapshot.snapshot_id, records=(loaded,)).record_ids == ()


def test_storage_reads_first_task9_conflict_rows_without_rewriting_their_identity(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot = snapshot_for(storage)
    legacy_values = {
        "record_id": "",
        "snapshot_id": snapshot.snapshot_id,
        "family": "conflict_unresolved",
        "derived_kind": "unresolved",
        "evidence_state": "cross_source_synthesis",
        "validation_state": "validated",
        "lifecycle_state": "active",
        "anchors": ["anc_synthetic"],
        "dependencies": [
            {"kind": "source_revision", "identifier": snapshot.selected_revision_ids[0]},
            {"kind": "derived_record", "identifier": "rec_a"},
            {"kind": "derived_record", "identifier": "rec_b"},
        ],
        "qualification": "Synthetic legacy conflict.",
        "facets": [],
        "kind": "unresolved",
        "subject": "question",
        "alternatives": ["option a", "option b"],
        "competing_record_ids": ["rec_a", "rec_b"],
        "reconciliation_state": "unresolved",
    }
    legacy_id = f"rec_{sha256(json.dumps(legacy_values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"

    with storage._connect() as connection:
        connection.execute(
            """
            INSERT INTO derived_records(
                record_id, snapshot_id, family, derived_kind, evidence_state, validation_state,
                lifecycle_state, qualification
            ) VALUES (?, ?, 'conflict_unresolved', 'unresolved', 'cross_source_synthesis', 'validated', 'active',
                      'Synthetic legacy conflict.')
            """,
            (legacy_id, snapshot.snapshot_id),
        )
        connection.execute("INSERT INTO derived_record_anchors VALUES (?, 0, 'anc_synthetic')", (legacy_id,))
        connection.executemany(
            "INSERT INTO derived_record_dependencies VALUES (?, ?, ?, ?)",
            [
                (legacy_id, 0, "source_revision", snapshot.selected_revision_ids[0]),
                (legacy_id, 1, "derived_record", "rec_a"),
                (legacy_id, 2, "derived_record", "rec_b"),
            ],
        )
        connection.execute(
            """
            INSERT INTO derived_conflict_unresolved(
                record_id, issue_kind, subject, competing_record_ids_json, reconciliation_state
            ) VALUES (?, 'unresolved', 'question', '[\"rec_a\", \"rec_b\"]', 'unresolved')
            """,
            (legacy_id,),
        )
        connection.executemany(
            "INSERT INTO derived_record_terms VALUES (?, 'alternative', ?, ?)",
            [(legacy_id, 0, "option a"), (legacy_id, 1, "option b")],
        )

    storage.initialize()
    [loaded] = storage.derived_records(snapshot.snapshot_id)

    assert loaded.record_id == legacy_id
    assert is_legacy_record(loaded)


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
