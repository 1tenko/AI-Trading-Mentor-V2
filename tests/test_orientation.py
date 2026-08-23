from dataclasses import replace
from types import SimpleNamespace

from mentor.derived_records import Claim, RecordDependency
from mentor.vector_stores import VectorStoreSearchResult


SNAPSHOT_ID = "snap_current"


class FakeStorage:
    def __init__(self, snapshot, records):
        self.snapshot = snapshot
        self.records = list(records)

    def current_snapshot(self):
        return self.snapshot

    def derived_records(self, snapshot_id):
        assert snapshot_id == self.snapshot.snapshot_id
        return list(self.records)


class FakeVectorStores:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def search(self, store_id, query, *, attributes, max_num_results):
        self.calls.append((store_id, query, attributes, max_num_results))
        return list(self.results)


def claim(subject, *, snapshot_id=SNAPSHOT_ID):
    return Claim.create(
        snapshot_id=snapshot_id,
        anchors=(f"anc_{subject}",),
        dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
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


def test_orientation_searches_only_current_derived_store_and_omits_raw_search_text():
    from mentor.orientation import OrientationBudget, OrientationService

    record = claim("timing")
    vector_stores = FakeVectorStores([
        result(record, concept_id="con_timing", text="RAW TRANSCRIPT PASSAGE MUST NOT ESCAPE"),
    ])
    service = OrientationService(
        FakeStorage(published_snapshot(), [record]),
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
    assert orientation.records[0].statement == "timing guides context"
    assert orientation.records[0].anchor_ids == ("anc_timing",)
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
        FakeStorage(published_snapshot(), [record]),
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
    service = OrientationService(
        FakeStorage(published_snapshot(), [first, duplicate_concept, second]),
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
        FakeStorage(published_snapshot(), [record]),
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
        FakeStorage(published_snapshot(), [valid, invalid, wrong_snapshot]),
        FakeVectorStores([result(valid), result(invalid), result(wrong_snapshot)]),
        budget=OrientationBudget(max_records=3, max_tokens=1_000),
    )

    orientation = service.consult("context")

    assert [record.record_id for record in orientation.records] == [valid.record_id]
    assert orientation.discarded_result_count == 2
