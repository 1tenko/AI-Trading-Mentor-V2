import json
from types import SimpleNamespace

from mentor.derived_records import (
    Claim,
    ConflictUnresolved,
    RecordDependency,
    conflict_side_lineages,
)
from mentor.synthesis import ReconciliationSource, SynthesisReconciler


SNAPSHOT_ID = "snap_conclusion_lineage"


def _claim(index: int) -> Claim:
    return Claim.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=(f"anc_{index}",),
        dependencies=(RecordDependency("source_revision", f"rev_{index}"),),
        validation_state="validated",
        lifecycle_state="active",
        qualification="Synthetic support.",
        subject=f"Concept {index}",
        predicate="has",
        object="a bounded meaning",
    )


def _source(index: int) -> ReconciliationSource:
    return ReconciliationSource(
        revision_id=f"rev_{index}",
        collection_id="collection_synthetic",
        source_id=f"source_{index}",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title=f"Synthetic lesson {index}",
        year=2025 + index % 2,
        original_filename=f"synthetic-{index}.txt",
    )


class _Responses:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def create(self, **request):
        payload = json.loads(request["input"])
        self.calls.append(payload)
        return SimpleNamespace(
            output_text=json.dumps(self.responder(payload)),
            usage=None,
        )


def _run(records, responder, *, width=2):
    responses = _Responses(responder)
    result = SynthesisReconciler(
        SimpleNamespace(responses=responses), max_records_per_call=width
    ).synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=records,
        revisions=tuple(SimpleNamespace(revision_id=f"rev_{index}") for index in range(len(records))),
        source_metadata=tuple(_source(index) for index in range(len(records))),
        anchor_spans={f"anc_{index}": f"bounded span {index}" for index in range(len(records))},
    )
    return result, responses.calls


def _one_input_relationship(record_payload, *, suffix=""):
    record_id = record_payload["record_id"]
    anchor_id = record_payload["anchors"][0]
    revision_id = next(
        dependency["identifier"]
        for dependency in record_payload["dependencies"]
        if dependency["kind"] == "source_revision"
    )
    return {
        "family": "relationship",
        "qualification": "One independently supported conclusion.",
        "anchors": [anchor_id],
        "input_record_ids": [record_id],
        "source_revision_ids": [revision_id],
        "left": f"{record_payload['subject']}{suffix}",
        "relation": "supports",
        "right": "Independent conclusion",
    }


def test_summary_exposes_each_disjoint_conclusion_with_its_own_lineage():
    records = tuple(_claim(index) for index in range(4))

    def response(request):
        if request["reconciliation_batch"]["kind"] == "primary":
            return {
                "records": [_one_input_relationship(record) for record in request["records"]],
                "concept_hints": [],
            }
        for summary in request["prior_cluster_summaries"]:
            for conclusion in summary["conclusions"]:
                assert set(conclusion) == {
                    "conclusion_id",
                    "statement",
                    "input_record_ids",
                    "anchor_ids",
                    "source_revision_ids",
                }
                assert len(conclusion["anchor_ids"]) == 1
                assert len(conclusion["source_revision_ids"]) == 1
        return {"records": [], "concept_hints": []}

    _result, calls = _run(records, response)
    assert any(call["reconciliation_batch"]["kind"].startswith("hierarchical_reduction") for call in calls)


def test_multi_round_conclusion_can_depend_on_one_earlier_synthesis_and_one_raw_claim():
    records = tuple(_claim(index) for index in range(6))
    intermediate_id = None
    tail_id = None

    def response(request):
        nonlocal intermediate_id, tail_id
        kind = request["reconciliation_batch"]["kind"]
        if kind == "primary":
            return {"records": [], "concept_hints": []}
        summaries = request["prior_cluster_summaries"]
        if kind == "hierarchical_reduction_1":
            first, second = (summary["conclusions"][0] for summary in summaries)
            payload = {
                "family": "relationship",
                "qualification": "A bounded earlier synthesis.",
                "anchors": first["anchor_ids"] + second["anchor_ids"],
                "input_record_ids": [],
                "input_conclusion_ids": [first["conclusion_id"], second["conclusion_id"]],
                "source_revision_ids": first["source_revision_ids"] + second["source_revision_ids"],
                "left": "Earlier synthesis",
                "relation": "depends_on",
                "right": "Two inputs",
            }
            return {"records": [payload], "concept_hints": []}
        synthesized = next(
            conclusion
            for summary in summaries
            for conclusion in summary["conclusions"]
            if conclusion["statement"].startswith("Earlier synthesis")
        )
        tail = next(
            conclusion
            for summary in summaries
            for conclusion in summary["conclusions"]
            if conclusion["conclusion_id"] != synthesized["conclusion_id"]
        )
        intermediate_id = synthesized["conclusion_id"]
        tail_id = tail["conclusion_id"]
        return {"records": [{
            "family": "relationship",
            "qualification": "Higher synthesis uses exactly two conclusions.",
            "anchors": synthesized["anchor_ids"] + tail["anchor_ids"],
            "input_record_ids": [],
            "input_conclusion_ids": [intermediate_id, tail_id],
            "source_revision_ids": synthesized["source_revision_ids"] + tail["source_revision_ids"],
            "left": "Higher synthesis",
            "relation": "depends_on",
            "right": "Selected conclusions",
        }], "concept_hints": []}

    result, _calls = _run(records, response)
    higher = next(record for record in result.records if record.left == "Higher synthesis")
    assert {
        dependency.identifier
        for dependency in higher.dependencies
        if dependency.kind == "derived_record"
    } == {intermediate_id, tail_id}


def test_selecting_one_summary_conclusion_does_not_leak_its_sibling_anchor():
    records = tuple(_claim(index) for index in range(4))
    selected = None
    omitted = None

    def response(request):
        nonlocal selected, omitted
        if request["reconciliation_batch"]["kind"] == "primary":
            return {
                "records": [_one_input_relationship(record) for record in request["records"]],
                "concept_hints": [],
            }
        conclusions = request["prior_cluster_summaries"][0]["conclusions"]
        selected, omitted = conclusions
        return {"records": [{
            "family": "relationship",
            "qualification": "Uses only the selected conclusion.",
            "anchors": selected["anchor_ids"],
            "input_record_ids": [],
            "input_conclusion_ids": [selected["conclusion_id"]],
            "source_revision_ids": selected["source_revision_ids"],
            "left": "Selected conclusion",
            "relation": "supports",
            "right": "No leaked evidence",
        }], "concept_hints": []}

    result, _calls = _run(records, response)
    final = next(record for record in result.records if record.left == "Selected conclusion")
    assert set(final.anchors) == set(selected["anchor_ids"])
    assert set(final.anchors).isdisjoint(omitted["anchor_ids"])


def test_conflict_alternatives_have_a_one_to_one_input_record_identity():
    first, second = _claim(0), _claim(1)
    conflict = ConflictUnresolved.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=first.anchors + second.anchors,
        dependencies=(
            RecordDependency("source_revision", "rev_0"),
            RecordDependency("source_revision", "rev_1"),
            RecordDependency("derived_record", first.record_id),
            RecordDependency("derived_record", second.record_id),
        ),
        validation_state="validated",
        lifecycle_state="active",
        qualification="The two synthetic sides remain unresolved.",
        kind="unresolved",
        subject="Synthetic conflict",
        alternatives=("First side", "Second side"),
        competing_record_ids=(first.record_id, second.record_id),
        reconciliation_state="unresolved",
        relevant_scopes=("Synthetic scope",),
        unresolved_questions=("Which side applies?",),
    )

    assert [
        (side.alternative, side.input_record_id)
        for side in getattr(conflict, "competing_sides", ())
    ] == [
        ("First side", first.record_id),
        ("Second side", second.record_id),
    ]
    assert [
        (side.alternative, lineage.anchor_ids, lineage.source_revision_ids)
        for side, lineage in conflict_side_lineages(
            conflict, {first.record_id: first, second.record_id: second}
        )
    ] == [
        ("First side", ("anc_0",), ("rev_0",)),
        ("Second side", ("anc_1",), ("rev_1",)),
    ]
