from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

from mentor.compilation import CompilationRun, CorpusSnapshot
from mentor.derived_records import Claim, RecordDependency
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import Storage
from mentor.synthesis import SynthesisCandidate
from mentor.vector_stores import VectorStoreSearchResult


SNAPSHOT_ID = "snap_current"


class FakeStorage:
    def __init__(self, snapshot, records, concept_ids=None, source_areas=None):
        self.snapshot = snapshot
        self.records = list(records)
        self.concept_ids = dict(concept_ids or {})
        self.source_areas = dict(source_areas or {})

    def current_snapshot(self):
        return self.snapshot

    def derived_records(self, snapshot_id):
        assert snapshot_id == self.snapshot.snapshot_id
        return list(self.records)

    def orientation_concept_ids(self, snapshot_id):
        assert snapshot_id == self.snapshot.snapshot_id
        return dict(self.concept_ids)

    def orientation_source_area(self, snapshot_id, record):
        assert snapshot_id == self.snapshot.snapshot_id
        return self.source_areas.get(record.record_id, (None, None, None))


class FakeVectorStores:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def search(self, store_id, query, *, attributes, max_num_results):
        self.calls.append((store_id, query, attributes, max_num_results))
        return list(self.results)


def claim(subject, *, snapshot_id=SNAPSHOT_ID, dependencies=(RecordDependency("source_revision", "rev_synthetic"),)):
    return Claim.create(
        snapshot_id=snapshot_id,
        anchors=(f"anc_{subject}",),
        dependencies=dependencies,
        validation_state="validated",
        lifecycle_state="active",
        qualification="Bounded derived orientation.",
        subject=subject,
        predicate="guides",
        object="context",
    )


def published_snapshot():
    return SimpleNamespace(
        snapshot_id=SNAPSHOT_ID,
        status="published",
        derived_store_id="vs_derived_current",
    )


def result(record, *, concept_id=None, snapshot_id=SNAPSHOT_ID, status="published", text=""):
    attributes = {
        "snapshot_id": snapshot_id,
        "status": status,
        "record_id": record.record_id,
        "collection_id": "collection_jacob",
        "year": 2026,
        "scope": "market context",
    }
    if concept_id:
        attributes["concept_id"] = concept_id
    return VectorStoreSearchResult(
        file_id=f"file_{record.record_id}",
        filename="derived-orientation.json",
        score=0.9,
        attributes=attributes,
        text=text,
    )


def context(records):
    return {record.record_id: f"con_{index:064x}" for index, record in enumerate(records, start=1)}


def test_orientation_searches_only_current_derived_store_and_omits_raw_search_text():
    from mentor.orientation import OrientationBudget, OrientationService

    record = claim("timing")
    vector_stores = FakeVectorStores([
        result(record, concept_id="con_timing", text="RAW TRANSCRIPT PASSAGE MUST NOT ESCAPE"),
    ])
    service = OrientationService(
        FakeStorage(published_snapshot(), [record], context([record]), {
            record.record_id: ("collection_jacob", 2026, "local market scope"),
        }),
        vector_stores,
        budget=OrientationBudget(max_records=2, max_tokens=1_000),
    )

    orientation = service.consult("How does timing fit together?", collection_id="collection_jacob", year=2026)

    assert vector_stores.calls == [
        (
            "vs_derived_current",
            "How does timing fit together?",
            {
                "snapshot_id": SNAPSHOT_ID,
                "status": "published",
                "collection_id": "collection_jacob",
                "year": 2026,
            },
            8,
        )
    ]
    assert orientation.snapshot_id == SNAPSHOT_ID
    assert orientation.record_count == 1
    assert orientation.records[0].record_id == record.record_id
    assert orientation.records[0].concept_id == context([record])[record.record_id]
    assert orientation.records[0].statement == "timing guides context"
    assert orientation.records[0].anchor_ids == ("anc_timing",)
    assert orientation.records[0].source_area.collection_id == "collection_jacob"
    assert orientation.records[0].source_area.scope == "local market scope"
    assert "RAW TRANSCRIPT" not in repr(orientation)
    assert "citations" not in orientation.__dataclass_fields__


def test_orientation_discards_wrong_snapshot_or_nonpublished_remote_results():
    from mentor.orientation import OrientationBudget, OrientationService

    record = claim("timing")
    vector_stores = FakeVectorStores([
        result(record, snapshot_id="snap_stale"),
        result(record, status="candidate"),
    ])
    service = OrientationService(
        FakeStorage(published_snapshot(), [record], context([record])),
        vector_stores,
        budget=OrientationBudget(max_records=2, max_tokens=1_000),
    )

    orientation = service.consult("timing")

    assert orientation.records == ()
    assert orientation.discarded_result_count == 2


def test_orientation_deduplicates_concepts_before_applying_the_record_budget():
    from mentor.orientation import OrientationBudget, OrientationService

    first = claim("first")
    duplicate_concept = claim("duplicate")
    second = claim("second")
    concept_ids = {
        first.record_id: "con_" + "b" * 64,
        duplicate_concept.record_id: "con_" + "b" * 64,
        second.record_id: "con_" + "c" * 64,
    }
    service = OrientationService(
        FakeStorage(published_snapshot(), [first, duplicate_concept, second], concept_ids),
        FakeVectorStores([
            result(first, concept_id="con_shared"),
            result(duplicate_concept, concept_id="con_shared"),
            result(second, concept_id="con_second"),
        ]),
        budget=OrientationBudget(max_records=2, max_tokens=1_000),
    )

    orientation = service.consult("compare")

    assert [record.record_id for record in orientation.records] == [first.record_id, second.record_id]
    assert orientation.duplicate_result_count == 1
    assert orientation.truncated is False


def test_orientation_enforces_a_hard_token_budget_and_reports_truncation():
    from mentor.orientation import OrientationBudget, OrientationService

    record = claim("timing")
    service = OrientationService(
        FakeStorage(published_snapshot(), [record], context([record])),
        FakeVectorStores([result(record, concept_id="con_timing")]),
        budget=OrientationBudget(max_records=3, max_tokens=1),
    )

    orientation = service.consult("timing")

    assert orientation.records == ()
    assert orientation.used_tokens == 0
    assert orientation.truncated is True


def test_orientation_rejects_invalid_or_wrong_snapshot_local_records():
    from mentor.orientation import OrientationBudget, OrientationService

    valid = claim("valid")
    invalid = replace(claim("invalid"), record_id="rec_tampered")
    wrong_snapshot = claim("old", snapshot_id="snap_old")
    service = OrientationService(
        FakeStorage(published_snapshot(), [valid, invalid, wrong_snapshot], context([valid, invalid, wrong_snapshot])),
        FakeVectorStores([result(valid), result(invalid), result(wrong_snapshot)]),
        budget=OrientationBudget(max_records=3, max_tokens=1_000),
    )

    orientation = service.consult("context")

    assert [record.record_id for record in orientation.records] == [valid.record_id]
    assert orientation.discarded_result_count == 2


def test_orientation_uses_local_concepts_and_source_area_when_remote_metadata_is_forged_or_missing():
    from mentor.orientation import OrientationBudget, OrientationService

    first = claim("first")
    second = claim("second")
    local_concept_id = "con_" + "d" * 64
    concept_ids = {first.record_id: local_concept_id, second.record_id: local_concept_id}
    service = OrientationService(
        FakeStorage(
            published_snapshot(),
            [first, second],
            concept_ids,
            {first.record_id: ("collection_local", 2025, "local scope")},
        ),
        FakeVectorStores([
            result(first, concept_id="con_forged"),
            result(first, concept_id="con_another_forged"),
            result(second),
        ]),
        budget=OrientationBudget(max_records=3, max_tokens=1_000),
    )

    orientation = service.consult("compare")

    assert [record.record_id for record in orientation.records] == [first.record_id]
    assert orientation.records[0].concept_id == local_concept_id
    assert orientation.records[0].source_area.collection_id == "collection_local"
    assert orientation.records[0].source_area.year == 2025
    assert orientation.records[0].source_area.scope == "local scope"
    assert orientation.duplicate_result_count == 2


def test_storage_persists_candidate_record_concept_links(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    collection = Collection("collection_synthetic", "Synthetic", "trading", True, "test")
    source = Source.create(
        collection_id=collection.collection_id,
        identity_key="orientation-link",
        source_type="transcript",
        author="Synthetic",
        course="Synthetic",
        lesson_title="Synthetic",
        year=2026,
        original_filename="synthetic.txt",
        local_provenance="C:/synthetic.txt",
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256=sha256(b"orientation").hexdigest(),
        byte_size=11,
        local_locator="C:/synthetic.txt",
        observed_at=1.0,
        lifecycle_state="active",
    )
    storage.store_collection(collection)
    storage.store_source(source)
    storage.store_source_revision(revision)
    run = CompilationRun("run_orientation", "test", "test", "test", 1.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=[revision],
        raw_store_id="raw_synthetic",
        derived_store_id="derived_synthetic",
        created_at=1.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    record = claim(
        "stored",
        snapshot_id=snapshot.snapshot_id,
        dependencies=(RecordDependency("source_revision", revision.revision_id),),
    )
    storage.store_derived_record(record)

    synthesis = SynthesisCandidate.from_records(snapshot_id=snapshot.snapshot_id, records=[record])
    concept_id = synthesis.concept_id_for("stored")

    storage.store_orientation_concept_ids(
        snapshot.snapshot_id,
        {record.record_id: concept_id},
        concepts=synthesis.concepts,
    )

    assert storage.orientation_concept_ids(snapshot.snapshot_id) == {record.record_id: concept_id}
    assert storage.orientation_source_area(snapshot.snapshot_id, record) == ("collection_synthetic", 2026, None)
