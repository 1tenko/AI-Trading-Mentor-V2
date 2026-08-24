from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from openai.lib._pydantic import _ensure_strict_json_schema

from mentor.derived_records import (
    Claim,
    ConflictUnresolved,
    Facet,
    ProcedureSequenceHierarchy,
    RecordDependency,
    Relationship,
)
from mentor.synthesis import (
    ConceptHint,
    ProcedureBranch,
    ReconciliationSource,
    SynthesisCandidate,
    SynthesisReconciler,
    SYNTHESIS_RESPONSE_SCHEMA,
    _replace_legacy_synthesis_hints,
    concept_hint_from_record_selector,
    validate_concept_hint_integrity,
)


SNAPSHOT_ID = "snap_synthetic"


def synthesis_occurrence(text: str, *, aliases: tuple[str, ...] = (), scope: str | None = None) -> dict[str, object]:
    return {"text": text, "aliases": list(aliases), "scope": scope}


def claim(
    *,
    subject: str,
    validation_state: str = "validated",
    anchors: tuple[str, ...] | None = None,
    dependencies: tuple[RecordDependency, ...] | None = None,
) -> Claim:
    return Claim.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=anchors or (f"anc_{subject}",),
        dependencies=dependencies or (RecordDependency("source_revision", "rev_synthetic"),),
        validation_state=validation_state,
        lifecycle_state="candidate",
        qualification="Synthetic support.",
        subject=subject,
        predicate="has",
        object="meaning",
    )


def relationship(
    *,
    left: str,
    right: str,
    anchors: tuple[str, ...] | None = None,
    dependencies: tuple[RecordDependency, ...] | None = None,
) -> Relationship:
    return Relationship.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=anchors or (f"anc_{left}_{right}",),
        dependencies=dependencies or (RecordDependency("source_revision", "rev_synthetic"),),
        validation_state="validated",
        lifecycle_state="candidate",
        qualification="Synthetic support.",
        left=left,
        relation="depends_on",
        right=right,
    )


def procedure(
    *,
    dependencies: tuple[RecordDependency, ...],
    terms: tuple[str, ...] = ("Observe", "Confirm", "Enter"),
    conditions: tuple[str, ...] = ("Only after confirmation.",),
) -> ProcedureSequenceHierarchy:
    return ProcedureSequenceHierarchy.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=("anc_procedure",),
        dependencies=dependencies,
        validation_state="validated",
        lifecycle_state="candidate",
        qualification="Synthetic support.",
        facets=tuple(Facet("condition", condition) for condition in conditions),
        kind="procedure",
        terms=terms,
    )


class RecordingResponses:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        payload = self.responder(json.loads(request["input"]))
        return SimpleNamespace(output_text=json.dumps(payload), usage=None)


def reconciliation_source(revision_id: str, *, year: int) -> ReconciliationSource:
    return ReconciliationSource(
        revision_id=revision_id,
        collection_id="collection_synthetic",
        source_id=f"source_{revision_id}",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title=f"Synthetic lesson {year}",
        year=year,
        original_filename=f"{revision_id}.txt",
    )


def test_synthesis_response_schema_is_a_closed_typed_union_under_the_openai_strict_contract():
    schema = deepcopy(SYNTHESIS_RESPONSE_SCHEMA)
    assert _ensure_strict_json_schema(schema, path=(), root=schema) == SYNTHESIS_RESPONSE_SCHEMA

    response_properties = SYNTHESIS_RESPONSE_SCHEMA["properties"]
    assert set(SYNTHESIS_RESPONSE_SCHEMA["required"]) == set(response_properties)
    assert SYNTHESIS_RESPONSE_SCHEMA["additionalProperties"] is False
    record_schemas = response_properties["records"]["items"]["anyOf"]
    assert {item["properties"]["family"]["enum"][0] for item in record_schemas} == {
        "relationship",
        "procedure_sequence_hierarchy",
        "evolution",
        "conflict_unresolved",
    }
    for item in record_schemas:
        assert item["additionalProperties"] is False
        assert set(item["required"]) == set(item["properties"])
        assert {"family", "qualification", "anchors", "input_record_ids", "input_conclusion_ids", "source_revision_ids"} <= set(item["required"])


def test_synthesis_response_schema_closes_family_and_concept_hint_shapes():
    record_schemas = SYNTHESIS_RESPONSE_SCHEMA["properties"]["records"]["items"]["anyOf"]
    relationship_schema = next(
        item for item in record_schemas
        if item["properties"]["family"]["enum"] == ["relationship"]
    )
    assert "kind" not in relationship_schema["properties"]
    assert relationship_schema["additionalProperties"] is False

    assert "concept_hints" not in SYNTHESIS_RESPONSE_SCHEMA["properties"]
    occurrence_schema = relationship_schema["properties"]["left"]
    assert occurrence_schema["additionalProperties"] is False
    assert set(occurrence_schema["required"]) == {"text", "aliases", "scope"}


def test_synthesis_inline_occurrences_attach_aliases_to_typed_record_terms():
    first = claim(
        subject="First", anchors=("anc_earlier",),
        dependencies=(RecordDependency("source_revision", "rev_earlier"),),
    )
    second = claim(
        subject="Second", anchors=("anc_later",),
        dependencies=(RecordDependency("source_revision", "rev_later"),),
    )
    occurrence = lambda text, aliases=(): {"text": text, "aliases": list(aliases), "scope": None}
    payload = {
        "records": [{
            "family": "procedure_sequence_hierarchy",
            "qualification": "Synthetic bounded reconciliation.",
            "anchors": ["anc_earlier", "anc_later"],
            "input_record_ids": [first.record_id, second.record_id],
            "input_conclusion_ids": [],
            "source_revision_ids": ["rev_earlier", "rev_later"],
            "kind": "procedure",
            "terms": [occurrence("Observe", ("Inspect",)), occurrence("Act")],
            "prerequisites": [occurrence("Context")],
            "conditions": ["Only after confirmation."],
            "branches": [{"condition": "If confirmation fails", "steps": [occurrence("Observe again")]}],
        }]
    }
    responses = RecordingResponses(lambda _request: payload)

    result = SynthesisReconciler(SimpleNamespace(responses=responses)).synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=(first, second),
        revisions=(SimpleNamespace(revision_id="rev_earlier"), SimpleNamespace(revision_id="rev_later")),
        source_metadata=(
            reconciliation_source("rev_earlier", year=2025),
            reconciliation_source("rev_later", year=2026),
        ),
        anchor_spans={"anc_earlier": "earlier span", "anc_later": "later span"},
    )

    record = result.records[0]
    assert result.hints == (
        ConceptHint(record.record_id, "Observe", ("Inspect",), None, "term", 0),
        ConceptHint(record.record_id, "Act", (), None, "term", 1),
        ConceptHint(record.record_id, "Context", (), None, "prerequisite", 0),
        ConceptHint(record.record_id, "Observe again", (), None, "branch_step", 0),
    )


def test_candidate_hint_integrity_rejects_dangling_invalid_and_duplicate_selectors():
    record = Relationship.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=("anc_integrity",),
        dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
        validation_state="validated",
        lifecycle_state="active",
        qualification="Synthetic support.",
        left="Left term",
        relation="supports",
        right="Right term",
    )
    valid = concept_hint_from_record_selector(
        record, aliases=("Left alias",), scope=None, role="left", position=None
    )
    validate_concept_hint_integrity((record,), (valid,), require_active=True)

    with pytest.raises(ValueError, match="candidate-owned"):
        validate_concept_hint_integrity(
            (record,), (replace(valid, record_id="rec_missing"),), require_active=True
        )
    with pytest.raises(ValueError, match="does not identify"):
        validate_concept_hint_integrity(
            (record,), (replace(valid, role="subject"),), require_active=True
        )
    with pytest.raises(ValueError, match="does not identify"):
        validate_concept_hint_integrity(
            (record,), (replace(valid, position=0),), require_active=True
        )
    with pytest.raises(ValueError, match="duplicate"):
        validate_concept_hint_integrity((record,), (valid, valid), require_active=True)


def test_legacy_hint_replacement_rejects_conflicting_duplicate_selectors():
    inline = ConceptHint("rec_synthetic", "Term", (), None, "term", 0)
    first = ConceptHint("rec_synthetic", "Term", ("First alias",), None, "term", 0)
    second = ConceptHint("rec_synthetic", "Term", ("Second alias",), None, "term", 0)

    with pytest.raises(ValueError, match="duplicate legacy synthesis selector"):
        _replace_legacy_synthesis_hints((inline,), (first, second))


@pytest.mark.parametrize(
    "family_payload",
    [
        lambda first, second: {
            "family": "relationship", "left": synthesis_occurrence("First"), "relation": "supports", "right": synthesis_occurrence("Second"),
        },
        lambda first, second: {
            "family": "procedure_sequence_hierarchy", "kind": "sequence",
            "terms": [synthesis_occurrence("First"), synthesis_occurrence("Second")], "prerequisites": [], "conditions": [], "branches": [],
        },
        lambda first, second: {
            "family": "evolution", "subject": synthesis_occurrence("Synthetic concept"), "previous": synthesis_occurrence("Earlier form"),
            "current": synthesis_occurrence("Later form"), "earlier_source_set": ["rev_earlier"],
            "later_source_set": ["rev_later"], "classification": "refined",
            "negative_evidence_state": "positive_teaching", "competing_anchors": [],
            "deprecation_evidence_anchors": [],
        },
        lambda first, second: {
            "family": "conflict_unresolved", "kind": "unresolved", "subject": synthesis_occurrence("Synthetic question"),
            "alternatives": [synthesis_occurrence("First statement"), synthesis_occurrence("Second statement")],
            "competing_record_ids": [first.record_id, second.record_id],
            "reconciliation_state": "unresolved", "relevant_scopes": ["Synthetic scope"],
            "conditions": [], "unresolved_questions": ["Which scope applies?"],
        },
    ],
    ids=["relationship", "procedure", "evolution", "conflict"],
)
def test_each_strict_synthesis_family_shape_reaches_the_typed_parser(family_payload):
    first = claim(
        subject="First", anchors=("anc_earlier",),
        dependencies=(RecordDependency("source_revision", "rev_earlier"),),
    )
    second = claim(
        subject="Second", anchors=("anc_later",),
        dependencies=(RecordDependency("source_revision", "rev_later"),),
    )

    payload = {
        **family_payload(first, second),
        "qualification": "Synthetic bounded reconciliation.",
        "anchors": ["anc_earlier", "anc_later"],
        "input_record_ids": [first.record_id, second.record_id],
        "input_conclusion_ids": [],
        "source_revision_ids": ["rev_earlier", "rev_later"],
    }
    responses = RecordingResponses(lambda _request: {"records": [payload], "concept_hints": []})

    result = SynthesisReconciler(SimpleNamespace(responses=responses)).synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=(first, second),
        revisions=(SimpleNamespace(revision_id="rev_earlier"), SimpleNamespace(revision_id="rev_later")),
        source_metadata=(
            reconciliation_source("rev_earlier", year=2025),
            reconciliation_source("rev_later", year=2026),
        ),
        anchor_spans={"anc_earlier": "earlier span", "anc_later": "later span"},
    )

    assert len(result.records) == 1
    request_format = responses.calls[0]["text"]["format"]
    assert request_format["strict"] is True
    assert request_format["schema"] == SYNTHESIS_RESPONSE_SCHEMA


def test_evolution_scope_and_coverage_are_derived_from_canonical_source_metadata():
    earlier = claim(
        subject="Earlier form",
        anchors=("anc_earlier",),
        dependencies=(RecordDependency("source_revision", "rev_earlier"),),
    )
    later = claim(
        subject="Later form",
        anchors=("anc_later",),
        dependencies=(RecordDependency("source_revision", "rev_later"),),
    )

    def response(request):
        assert {item["year"] for item in request["sources"]} == {2025, 2026}
        return {"records": [{
            "family": "evolution",
            "qualification": "Synthetic comparison.",
            "anchors": ["anc_earlier", "anc_later"],
            "input_record_ids": [earlier.record_id, later.record_id],
            "source_revision_ids": ["rev_earlier", "rev_later"],
            "subject": "Synthetic form",
            "previous": "Earlier",
            "current": "Later",
            "earlier_source_set": ["rev_earlier"],
            "later_source_set": ["rev_later"],
            "classification": "refined",
            "negative_evidence_state": "positive_teaching",
            "earlier_coverage_id": "model-invented-earlier",
            "later_coverage_id": "model-invented-later",
            "earlier_observed_years": [1900],
            "later_observed_years": [1901],
        }]}

    responses = RecordingResponses(response)
    result = SynthesisReconciler(SimpleNamespace(responses=responses)).synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=(earlier, later),
        revisions=(SimpleNamespace(revision_id="rev_earlier"), SimpleNamespace(revision_id="rev_later")),
        source_metadata=(
            reconciliation_source("rev_earlier", year=2025),
            reconciliation_source("rev_later", year=2026),
        ),
        anchor_spans={"anc_earlier": "earlier span", "anc_later": "later span"},
    )

    evolution = result.records[0]
    assert evolution.earlier_observed_years == (2025,)
    assert evolution.later_observed_years == (2026,)
    assert evolution.earlier_coverage_id.startswith("cov_")
    assert evolution.later_coverage_id.startswith("cov_")
    assert evolution.earlier_coverage_id != "model-invented-earlier"


def test_concrete_reconciliation_batches_every_record_and_filters_anchor_spans():
    records = tuple(claim(subject=f"Concept {index}") for index in range(5))
    responses = RecordingResponses(lambda _request: {"records": [], "concept_hints": []})
    reconciler = SynthesisReconciler(
        SimpleNamespace(responses=responses),
        max_records_per_call=2,
    )

    result = reconciler.synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=records,
        revisions=(SimpleNamespace(revision_id="rev_synthetic"),),
        source_metadata=(reconciliation_source("rev_synthetic", year=2025),),
        anchor_spans={record.anchors[0]: f"span {index}" for index, record in enumerate(records)},
    )

    requests = [json.loads(call["input"]) for call in responses.calls]
    assert result.records == ()
    assert len(requests) == 5
    assert max(len(request["records"]) for request in requests) == 2
    assert {
        record["record_id"] for request in requests for record in request["records"]
    } == {record.record_id for record in records}
    assert all(
        set(request["supporting_spans"])
        == {anchor for record in request["records"] for anchor in record["anchors"]}
        for request in requests
    )
    reduction_requests = [
        request for request in requests
        if request["reconciliation_batch"]["kind"].startswith("hierarchical_reduction")
    ]
    assert reduction_requests
    assert "prior_cluster_summaries" in reduction_requests[-1]
    assert sum(
        summary["covered_record_count"]
        for summary in reduction_requests[-1]["prior_cluster_summaries"]
    ) == len(records)
    assert max(
        len(summary["conclusions"])
        for request in reduction_requests
        for summary in request["prior_cluster_summaries"]
    ) <= 16


def test_reconciliation_uses_unchanged_lower_synthesis_as_context_not_primary_target():
    target = claim(
        subject="Affected claim",
        anchors=("anc_affected",),
        dependencies=(RecordDependency("source_revision", "rev_affected"),),
    )
    context = relationship(
        left="Unchanged cluster",
        right="Stable conclusion",
        anchors=("anc_context",),
        dependencies=(RecordDependency("source_revision", "rev_context"),),
    )

    def response(request):
        if request["reconciliation_batch"]["kind"] == "primary":
            return {"records": [], "concept_hints": []}
        return {"records": [{
            "family": "relationship",
            "qualification": "Synthetic rebuilt higher relationship.",
            "anchors": ["anc_affected", "anc_context"],
            "input_record_ids": [target.record_id, context.record_id],
            "source_revision_ids": ["rev_affected", "rev_context"],
            "left": "Affected claim",
            "relation": "depends_on",
            "right": "Unchanged cluster",
        }]}

    responses = RecordingResponses(response)
    result = SynthesisReconciler(
        SimpleNamespace(responses=responses), max_records_per_call=2
    ).synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=(target,),
        context_records=(context,),
        revisions=(
            SimpleNamespace(revision_id="rev_affected"),
            SimpleNamespace(revision_id="rev_context"),
        ),
        source_metadata=(
            reconciliation_source("rev_affected", year=2026),
            reconciliation_source("rev_context", year=2025),
        ),
        anchor_spans={"anc_affected": "affected span", "anc_context": "context span"},
    )

    requests = [json.loads(call["input"]) for call in responses.calls]
    primary = next(
        request for request in requests if request["reconciliation_batch"]["kind"] == "primary"
    )
    assert [record["record_id"] for record in primary["records"]] == [target.record_id]
    assert result.coverage.covered_record_ids == (target.record_id,)
    assert result.coverage.input_record_count == 1
    assert len(result.records) == 1
    assert {dependency.identifier for dependency in result.records[0].dependencies} >= {
        target.record_id, context.record_id, "rev_affected", "rev_context"
    }


def test_reconciliation_filters_context_only_paraphrase_and_preserves_selected_summary_lineage():
    target = claim(
        subject="Affected claim",
        anchors=("anc_affected",),
        dependencies=(RecordDependency("source_revision", "rev_affected"),),
    )
    context = relationship(
        left="Unchanged B cluster",
        right="Stable conclusion",
        anchors=("anc_context",),
        dependencies=(RecordDependency("source_revision", "rev_context"),),
    )
    unrelated_context = relationship(
        left="Gamma island",
        right="Separate boundary",
        anchors=("anc_unrelated",),
        dependencies=(RecordDependency("source_revision", "rev_unrelated"),),
    )

    def response(request):
        if request["reconciliation_batch"]["kind"] == "primary":
            return {"records": [], "concept_hints": []}
        summaries = request["prior_cluster_summaries"]
        assert all(summary["summary_id"] for summary in summaries)
        target_summary = next(
            summary for summary in summaries if summary["lineage_role"] == "target"
        )
        target_conclusion = target_summary["conclusions"][0]
        common = {
            "family": "relationship",
            "relation": "depends_on",
        }
        return {"records": [
            common | {
                "qualification": "Paraphrased unchanged B-only conclusion.",
                "anchors": ["anc_context"],
                "input_record_ids": [context.record_id],
                "input_conclusion_ids": [],
                "source_revision_ids": ["rev_context"],
                "left": "Reworded B framework",
                "right": "B-only context",
            },
            common | {
                "qualification": "Affected A reconciled with unchanged B.",
                "anchors": ["anc_context", "anc_affected"],
                "input_record_ids": [context.record_id],
                "input_conclusion_ids": [target_conclusion["conclusion_id"]],
                "source_revision_ids": ["rev_context", "rev_affected"],
                "left": "Affected A framework",
                "right": "Unchanged B cluster",
            },
        ], "concept_hints": []}

    result = SynthesisReconciler(
        SimpleNamespace(responses=RecordingResponses(response)), max_records_per_call=3
    ).synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=(target,),
        context_records=(context, unrelated_context),
        revisions=(
            SimpleNamespace(revision_id="rev_affected"),
            SimpleNamespace(revision_id="rev_context"),
            SimpleNamespace(revision_id="rev_unrelated"),
        ),
        source_metadata=(
            reconciliation_source("rev_affected", year=2026),
            reconciliation_source("rev_context", year=2025),
            reconciliation_source("rev_unrelated", year=2025),
        ),
        anchor_spans={
            "anc_affected": "affected span",
            "anc_context": "context span",
            "anc_unrelated": "unrelated span",
        },
    )

    assert len(result.records) == 1
    dependencies = {dependency.identifier for dependency in result.records[0].dependencies}
    assert dependencies == {
        target.record_id,
        context.record_id,
        "rev_affected",
        "rev_context",
    }
    assert unrelated_context.record_id not in dependencies
    assert "rev_unrelated" not in dependencies


def test_reconciliation_does_not_expose_an_omitted_child_conclusion_without_its_lineage():
    target = claim(
        subject="Affected target",
        anchors=("anc_target",),
        dependencies=(RecordDependency("source_revision", "rev_target"),),
    )
    contexts = tuple(
        claim(
            subject=f"Context {index}",
            anchors=(f"anc_context_{index}",),
            dependencies=(RecordDependency("source_revision", f"rev_context_{index}"),),
        )
        for index in range(3)
    )
    records_by_id = {record.record_id: record for record in contexts}
    omitted_conclusion = None
    selected_context = None

    def response(request):
        nonlocal omitted_conclusion, selected_context
        if request["reconciliation_batch"]["kind"] == "primary":
            return {"records": [], "concept_hints": []}
        summaries = request["prior_cluster_summaries"]
        target_summary = next(
            summary for summary in summaries if summary["lineage_role"] == "target"
        )
        if request["reconciliation_batch"]["kind"] == "hierarchical_reduction_1":
            context_summaries = [
                summary for summary in summaries if summary["lineage_role"] == "context"
            ]
            selected_conclusion = context_summaries[0]["conclusions"][0]
            omitted_conclusion = context_summaries[1]["conclusions"][0]
            selected_context = records_by_id[selected_conclusion["conclusion_id"]]
            selected_index = contexts.index(selected_context)
            target_conclusion = target_summary["conclusions"][0]
            return {"records": [{
                "family": "relationship",
                "qualification": "Affected target reconciled with selected context.",
                "anchors": [selected_context.anchors[0], "anc_target"],
                "input_record_ids": [],
                "input_conclusion_ids": [
                    selected_conclusion["conclusion_id"],
                    target_conclusion["conclusion_id"],
                ],
                "source_revision_ids": [f"rev_context_{selected_index}", "rev_target"],
                "left": "Affected target",
                "relation": "depends_on",
                "right": selected_context.subject,
            }], "concept_hints": []}

        assert omitted_conclusion is not None
        if any(
            conclusion["conclusion_id"] == omitted_conclusion["conclusion_id"]
            for conclusion in target_summary["conclusions"]
        ):
            raise AssertionError("omitted conclusion leaked into the merged summary")
        return {"records": [], "concept_hints": []}

    responses = RecordingResponses(response)
    result = SynthesisReconciler(
        SimpleNamespace(responses=responses), max_records_per_call=3
    ).synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=(target,),
        context_records=contexts,
        revisions=(
            SimpleNamespace(revision_id="rev_target"),
            *(SimpleNamespace(revision_id=f"rev_context_{index}") for index in range(3)),
        ),
        source_metadata=(
            reconciliation_source("rev_target", year=2026),
            *(reconciliation_source(f"rev_context_{index}", year=2025) for index in range(3)),
        ),
        anchor_spans={
            "anc_target": "target span",
            **{f"anc_context_{index}": f"context span {index}" for index in range(3)},
        },
    )

    reduction_requests = [
        json.loads(call["input"])
        for call in responses.calls
        if json.loads(call["input"])["reconciliation_batch"]["kind"].startswith(
            "hierarchical_reduction"
        )
    ]
    assert len(reduction_requests) == 2
    final_target_summary = next(
        summary
        for summary in reduction_requests[-1]["prior_cluster_summaries"]
        if summary["lineage_role"] == "target"
    )
    assert omitted_conclusion["conclusion_id"] not in {
        conclusion["conclusion_id"]
        for conclusion in final_target_summary["conclusions"]
    }
    assert selected_context is not None
    assert [
        conclusion["conclusion_id"]
        for conclusion in final_target_summary["conclusions"]
    ] == [result.records[0].record_id]
    assert len(result.records) == 1
    assert result.records[0].right == selected_context.subject
    selected_index = contexts.index(selected_context)
    assert {dependency.identifier for dependency in result.records[0].dependencies} == {
        target.record_id,
        selected_context.record_id,
        "rev_target",
        f"rev_context_{selected_index}",
    }


def test_reconciliation_rejects_a_batch_size_that_cannot_compare_records():
    with pytest.raises(ValueError, match="batch size"):
        SynthesisReconciler(
            SimpleNamespace(responses=RecordingResponses(lambda _request: {})),
            max_records_per_call=1,
        )


def test_reconciliation_uses_alias_affinity_instead_of_alphabetical_adjacency():
    alpha = claim(subject="Alpha setup")
    middle = tuple(claim(subject=f"Middle {index}") for index in range(4))
    zulu = claim(subject="Zulu setup")
    records = (alpha, *middle, zulu)
    responses = RecordingResponses(lambda _request: {"records": [], "concept_hints": []})
    reconciler = SynthesisReconciler(
        SimpleNamespace(responses=responses),
        max_records_per_call=2,
    )

    result = reconciler.synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=records,
        revisions=(SimpleNamespace(revision_id="rev_synthetic"),),
        source_metadata=(reconciliation_source("rev_synthetic", year=2025),),
        anchor_spans={record.anchors[0]: "bounded span" for record in records},
        hints=(
            ConceptHint(alpha.record_id, "Alpha setup", aliases=("Shared setup",)),
            ConceptHint(zulu.record_id, "Zulu setup", aliases=("Shared setup",)),
        ),
    )

    requests = [json.loads(call["input"]) for call in responses.calls]
    related_ids = {alpha.record_id, zulu.record_id}
    assert any(
        related_ids <= {record["record_id"] for record in request["records"]}
        for request in requests
    )
    assert result.coverage.covered_record_ids == tuple(sorted(record.record_id for record in records))
    assert result.coverage.complete is True
    assert result.coverage.bridge_call_count > 0


def test_reconciliation_can_emit_more_than_sixty_four_candidate_records_with_bounded_calls():
    records = tuple(claim(subject=f"Distinct topic {index:03d}") for index in range(70))

    def response(request):
        if request["reconciliation_batch"]["kind"] != "primary":
            return {"records": [], "concept_hints": []}
        outputs = []
        for record in request["records"]:
            outputs.append({
                "family": "relationship",
                "qualification": "Synthetic per-record relationship.",
                "anchors": record["anchors"],
                "input_record_ids": [record["record_id"]],
                "source_revision_ids": ["rev_synthetic"],
                "left": record["subject"],
                "relation": "supports",
                "right": "bounded meaning",
            })
        return {"records": outputs, "concept_hints": []}

    responses = RecordingResponses(response)
    reconciler = SynthesisReconciler(
        SimpleNamespace(responses=responses),
        max_records_per_call=8,
    )

    result = reconciler.synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=records,
        revisions=(SimpleNamespace(revision_id="rev_synthetic"),),
        source_metadata=(reconciliation_source("rev_synthetic", year=2025),),
        anchor_spans={record.anchors[0]: "bounded span" for record in records},
    )

    requests = [json.loads(call["input"]) for call in responses.calls]
    assert len(result.records) == 70
    assert all(len(request["records"]) <= 8 for request in requests)
    assert result.coverage.input_record_count == 70
    assert result.coverage.complete is True
    assert result.call_count == result.coverage.primary_call_count + result.coverage.bridge_call_count


def test_concrete_reconciliation_emits_structured_procedure_and_alias_hint():
    records = (claim(subject="Observe"), claim(subject="Act"))

    def response(request):
        record_ids = [record["record_id"] for record in request["records"]]
        anchors = [anchor for record in request["records"] for anchor in record["anchors"]]
        return {
            "records": [
                {
                    "family": "procedure_sequence_hierarchy",
                    "qualification": "Synthetic cross-source procedure.",
                    "anchors": anchors,
                    "input_record_ids": record_ids,
                    "source_revision_ids": ["rev_synthetic"],
                    "kind": "procedure",
                    "terms": ["Observe", "Act"],
                    "prerequisites": ["Context"],
                    "conditions": ["Only after confirmation"],
                    "branches": [
                        {"condition": "If confirmation fails", "steps": ["Observe"]}
                    ],
                }
            ],
            "concept_hints": [
                {
                    "record_index": 0,
                    "label": "Observe",
                    "aliases": ["Inspect"],
                    "scope": "synthetic",
                    "role": "term",
                    "position": 0,
                }
            ],
        }

    responses = RecordingResponses(response)
    reconciler = SynthesisReconciler(SimpleNamespace(responses=responses), max_records_per_call=8)

    result = reconciler.synthesize(
        snapshot_id=SNAPSHOT_ID,
        records=records,
        revisions=(SimpleNamespace(revision_id="rev_synthetic"),),
        source_metadata=(reconciliation_source("rev_synthetic", year=2025),),
        anchor_spans={record.anchors[0]: "bounded span" for record in records},
    )

    assert isinstance(result.records[0], ProcedureSequenceHierarchy)
    assert result.records[0].terms == ("Observe", "Act")
    assert result.records[0].prerequisites == ("Context",)
    assert result.records[0].conditions == ("Only after confirmation",)
    assert result.records[0].branches[0].condition == "If confirmation fails"
    assert result.records[0].branches[0].steps == ("Observe",)
    assert result.hints == (
        ConceptHint(
            result.records[0].record_id,
            "Observe",
            aliases=("Inspect",),
            scope="synthetic",
            role="term",
            position=0,
        ),
        ConceptHint(result.records[0].record_id, "Act", (), None, "term", 1),
        ConceptHint(result.records[0].record_id, "Context", (), None, "prerequisite", 0),
        ConceptHint(result.records[0].record_id, "Observe", (), None, "branch_step", 0),
    )


def test_candidate_excludes_invalid_records_and_requires_valid_concept_support():
    validated = claim(subject="validated")
    pending = claim(subject="pending", validation_state="pending")

    candidate = SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(validated, pending))
    clustered = next(concept for concept in candidate.concepts if concept.canonical_label == "validated")

    assert candidate.record_ids == (validated.record_id,)
    assert candidate.concept_id_for("validated") == clustered.concept_id
    assert clustered.supporting_record_ids == (validated.record_id,)
    assert clustered.supporting_anchor_ids == validated.anchors
    with pytest.raises(ValueError, match="valid supporting record"):
        SynthesisCandidate.from_records(
            snapshot_id=SNAPSHOT_ID,
            records=(validated, pending),
            hints=(ConceptHint(pending.record_id, "pending"),),
        )


def test_conflict_inputs_must_be_validated_candidate_records_and_remain_visible():
    first = claim(subject="first")
    second = claim(subject="second")
    conflict = ConflictUnresolved.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=("anc_first", "anc_second"),
        dependencies=(
            RecordDependency("source_revision", "rev_synthetic"),
            RecordDependency("derived_record", first.record_id),
            RecordDependency("derived_record", second.record_id),
        ),
        validation_state="validated",
        lifecycle_state="candidate",
        qualification="The synthetic contexts conflict without a supported reconciliation.",
        kind="unresolved",
        subject="synthetic question",
        alternatives=("first", "second"),
        competing_record_ids=(first.record_id, second.record_id),
        reconciliation_state="unresolved",
        relevant_scopes=("synthetic scope",),
        unresolved_questions=("Which synthetic condition applies?",),
    )

    candidate = SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(first, second, conflict))

    assert conflict.record_id in candidate.record_ids
    assert conflict.competing_record_ids == (first.record_id, second.record_id)
    missing_anchor_conflict = ConflictUnresolved.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=("anc_first",),
        dependencies=(
            RecordDependency("source_revision", "rev_synthetic"),
            RecordDependency("derived_record", first.record_id),
            RecordDependency("derived_record", second.record_id),
        ),
        validation_state="validated",
        lifecycle_state="candidate",
        qualification="The synthetic contexts conflict without a supported reconciliation.",
        kind="unresolved",
        subject="synthetic question",
        alternatives=("first", "second"),
        competing_record_ids=(first.record_id, second.record_id),
        reconciliation_state="unresolved",
        relevant_scopes=("synthetic scope",),
        unresolved_questions=("Which synthetic condition applies?",),
    )
    with pytest.raises(ValueError, match="competing record anchors"):
        SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(first, second, missing_anchor_conflict))

    unknown_conflict = ConflictUnresolved.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=("anc_first", "anc_second"),
        dependencies=(
            RecordDependency("source_revision", "rev_synthetic"),
            RecordDependency("derived_record", first.record_id),
            RecordDependency("derived_record", "rec_unknown"),
        ),
        validation_state="validated",
        lifecycle_state="candidate",
        qualification="The synthetic contexts need more evidence.",
        kind="unresolved",
        subject="synthetic question",
        alternatives=("first", "unknown"),
        competing_record_ids=(first.record_id, "rec_unknown"),
        reconciliation_state="unresolved",
        relevant_scopes=("synthetic scope",),
        unresolved_questions=("Which synthetic condition applies?",),
    )

    with pytest.raises(ValueError, match="competing record"):
        SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(first, second, unknown_conflict))


def test_candidate_requires_each_concept_anchor_to_belong_to_its_valid_supporting_record():
    validated = claim(subject="validated")
    candidate = SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(validated,))
    clustered = candidate.concept_id_for("validated")

    assert next(concept for concept in candidate.concepts if concept.concept_id == clustered).supporting_anchor_ids == validated.anchors


def test_repeated_references_converge_on_one_candidate_scoped_concept_id():
    first = relationship(left="Range", right="Entry")
    second = relationship(left="Range", right="Risk")
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(first, second),
    )

    first_synthesis = candidate.synthesize_relationship(first.record_id)
    second_synthesis = candidate.synthesize_relationship(second.record_id)

    assert first_synthesis.left_concept_id == second_synthesis.left_concept_id == candidate.concept_id_for("Range")
    assert first_synthesis.right_concept_id == candidate.concept_id_for("Entry")
    assert first_synthesis.relation == "depends_on"
    assert first_synthesis.input_record_ids == (first.record_id,)
    assert first_synthesis.anchor_ids == first.anchors
    assert first_synthesis.justification == "depends_on is supported by validated input records."


def test_candidate_clusters_validated_record_terms_without_manual_concepts():
    first = relationship(left="Range", right="Entry")
    second = relationship(left="range", right="Risk")

    candidate = SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(first, second))

    first_synthesis = candidate.synthesize_relationship(first.record_id)
    second_synthesis = candidate.synthesize_relationship(second.record_id)
    assert first_synthesis.left_concept_id == second_synthesis.left_concept_id
    assert candidate.concept_id_for("Range") == first_synthesis.left_concept_id


def test_explicit_alias_hints_cluster_only_the_supported_scoped_references():
    support = relationship(left="Support", right="Entry")
    floor = relationship(left="Floor", right="Risk")
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(support, floor),
        hints=(
            ConceptHint(support.record_id, "Support", aliases=("Floor",), scope="range"),
            ConceptHint(floor.record_id, "Floor", aliases=("Support",), scope="range"),
        ),
    )

    assert candidate.concept_id_for("Floor", scope="range") == candidate.concept_id_for("Support", scope="range")
    with pytest.raises(ValueError, match="scope"):
        candidate.concept_id_for("Floor")
    automatic = candidate.synthesize_relationship(support.record_id)
    synthesis = candidate.synthesize_relationship(support.record_id, left_scope="range")
    assert automatic.left_concept_id == candidate.concept_id_for("Support", scope="range")
    assert synthesis.left_concept_id == candidate.concept_id_for("Support", scope="range")
    assert synthesis.left_scope == "range"


def test_same_labels_with_explicit_distinct_scopes_do_not_merge():
    opening = relationship(left="Signal", right="Entry")
    closing = relationship(left="Signal", right="Risk")
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(opening, closing),
        hints=(
            ConceptHint(opening.record_id, "Signal", scope="opening"),
            ConceptHint(closing.record_id, "Signal", scope="closing"),
        ),
    )

    assert candidate.concept_id_for("Signal", scope="opening") != candidate.concept_id_for("Signal", scope="closing")
    with pytest.raises(ValueError, match="scope"):
        candidate.concept_id_for("Signal")


def test_synthesis_keeps_a_concise_justification_when_transitive_inputs_are_long():
    context = claim(subject="context0")
    contexts = [context]
    for index in range(1, 5):
        context = claim(
            subject=f"context{index}",
            dependencies=(
                RecordDependency("source_revision", "rev_synthetic"),
                RecordDependency("derived_record", context.record_id),
            ),
        )
        contexts.append(context)
    record = relationship(
        left="Range",
        right="Entry",
        dependencies=(
            RecordDependency("source_revision", "rev_synthetic"),
            RecordDependency("derived_record", context.record_id),
        ),
    )
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(*contexts, record),
    )

    synthesis = candidate.synthesize_relationship(record.record_id)

    assert len(synthesis.input_record_ids) == 6
    assert len(synthesis.justification) <= 280


def test_aliases_resolve_only_to_their_own_concept_and_similar_names_remain_distinct():
    record = relationship(left="Support", right="Supportive")
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        hints=(ConceptHint(record.record_id, "Support", aliases=("floor",)),),
    )

    assert candidate.concept_id_for("FLOOR") == candidate.concept_id_for("support")
    assert candidate.concept_id_for("support") != candidate.concept_id_for("supportive")


def test_concept_ids_are_stable_within_one_candidate_and_scoped_to_another_candidate():
    record = relationship(left="Support", right="Entry")
    first = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        hints=(ConceptHint(record.record_id, "Support", scope="market structure"),),
    )
    second = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        hints=(ConceptHint(record.record_id, "Support", scope="market structure"),),
    )
    other = Relationship.create(
        snapshot_id="snap_other",
        anchors=("anc_other",),
        dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
        validation_state="validated",
        lifecycle_state="candidate",
        qualification="Synthetic support.",
        left="Support",
        relation="depends_on",
        right="Entry",
    )

    assert first.concept_id_for("Support", scope="market structure") == second.concept_id_for(
        "Support", scope="market structure"
    )
    assert first.concept_id_for("Support", scope="market structure") != SynthesisCandidate.from_records(
        snapshot_id="snap_other",
        records=(other,),
        hints=(ConceptHint(other.record_id, "Support", scope="market structure"),),
    ).concept_id_for("Support", scope="market structure")


def test_explicit_scopes_keep_identical_labels_distinct_without_an_unscoped_alias_collision():
    opening = relationship(left="Signal", right="Entry")
    closing = relationship(left="Signal", right="Risk")
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(opening, closing),
        hints=(
            ConceptHint(opening.record_id, "Signal", scope="opening"),
            ConceptHint(closing.record_id, "Signal", scope="closing"),
        ),
    )

    assert candidate.concept_id_for("Signal", scope="opening") != candidate.concept_id_for("Signal", scope="closing")
    with pytest.raises(ValueError, match="scope"):
        candidate.concept_id_for("Signal")


def test_procedure_synthesis_retains_transitive_inputs_and_structured_branches():
    context = claim(subject="Context", anchors=("anc_context",))
    record = procedure(
        dependencies=(
            RecordDependency("source_revision", "rev_synthetic"),
            RecordDependency("derived_record", context.record_id),
        ),
        conditions=("If confirmation fails.",),
    )
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(context, record),
    )

    procedure_synthesis = candidate.synthesize_procedure(
        record.record_id,
        prerequisite_concept_ids=(candidate.concept_id_for("Context"),),
        branches=(
            ProcedureBranch(
                condition="If confirmation fails.",
                step_concept_ids=(candidate.concept_id_for("Observe"),),
                condition_index=0,
            ),
        ),
    )

    assert [step.concept_id for step in procedure_synthesis.steps] == [
        candidate.concept_id_for("Observe"),
        candidate.concept_id_for("Confirm"),
        candidate.concept_id_for("Enter"),
    ]
    assert procedure_synthesis.prerequisite_concept_ids == (candidate.concept_id_for("Context"),)
    assert procedure_synthesis.conditions == ("If confirmation fails.",)
    assert procedure_synthesis.branches[0].step_concept_ids == (candidate.concept_id_for("Observe"),)
    assert procedure_synthesis.input_record_ids == (record.record_id, context.record_id)
    assert procedure_synthesis.anchor_ids == ("anc_procedure", "anc_context")
    assert procedure_synthesis.evidence_state == "raw_taught"
    assert procedure_synthesis.justification == "procedure is supported by validated input records."
    assert not hasattr(procedure_synthesis, "confidence")
    assert not hasattr(procedure_synthesis, "reasoning")


def test_synthesis_rejects_raw_text_dumps_and_publish_rejects_unknown_concept_ids():
    record = relationship(left="Range", right="Entry")
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
    )
    relationship_synthesis = candidate.synthesize_relationship(record.record_id)
    procedure_record = procedure(dependencies=(RecordDependency("source_revision", "rev_synthetic"),))
    procedure_candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(procedure_record,),
    )

    with pytest.raises(ValueError, match="raw text dump"):
        procedure_candidate.synthesize_procedure(
            procedure_record.record_id,
            branches=(
                ProcedureBranch(condition="source text " * 100, step_concept_ids=(candidate.concept_id_for("Range"),)),
            ),
        )
    with pytest.raises(ValueError, match="valid concept IDs"):
        candidate.publish(relationships=(replace(relationship_synthesis, left_concept_id="con_unknown"),), procedures=())


def test_publication_rejects_replacements_with_unrelated_valid_records_and_private_justification():
    record = relationship(left="Range", right="Entry")
    unrelated = relationship(left="Risk", right="Target")
    candidate = SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(record, unrelated))
    synthesis = candidate.synthesize_relationship(record.record_id)
    unrelated_synthesis = candidate.synthesize_relationship(unrelated.record_id)

    with pytest.raises(ValueError, match="canonical"):
        candidate.publish(
            relationships=(
                replace(
                    synthesis,
                    input_record_ids=unrelated_synthesis.input_record_ids,
                    anchor_ids=unrelated_synthesis.anchor_ids,
                    left_concept_id=unrelated_synthesis.left_concept_id,
                    right_concept_id=unrelated_synthesis.right_concept_id,
                ),
            ),
            procedures=(),
        )
    with pytest.raises(ValueError, match="private"):
        candidate.publish(
            relationships=(replace(synthesis, justification="Private reasoning: hidden analysis."),), procedures=()
        )
    for replacement in (replace(synthesis, relation="supports"), replace(synthesis, evidence_state="cross_source_synthesis")):
        with pytest.raises(ValueError, match="canonical"):
            candidate.publish(relationships=(replacement,), procedures=())


def test_ordered_procedure_and_branch_steps_allow_repeated_concept_ids_with_positions():
    record = procedure(
        dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
        terms=("Observe", "Observe", "Enter"),
        conditions=("Repeat observation.",),
    )
    candidate = SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(record,))
    observe_id = candidate.concept_id_for("Observe")

    synthesis = candidate.synthesize_procedure(
        record.record_id,
        branches=(
            ProcedureBranch(
                condition="Repeat observation.",
                step_concept_ids=(observe_id, observe_id),
                condition_index=0,
            ),
        ),
    )

    assert [step.concept_id for step in synthesis.steps[:2]] == [observe_id, observe_id]
    assert synthesis.branches[0].positions == (0, 1)
    assert candidate.publish(relationships=(), procedures=(synthesis,)).procedures == (synthesis,)
    with pytest.raises(ValueError, match="positions"):
        candidate.synthesize_procedure(
            record.record_id,
            branches=(
                ProcedureBranch(
                    condition="Repeat observation.",
                    step_concept_ids=(observe_id, observe_id),
                    positions=(1, 0),
                    condition_index=0,
                ),
            ),
        )


def test_procedure_synthesis_uses_the_recorded_scope_for_scoped_terms():
    record = procedure(dependencies=(RecordDependency("source_revision", "rev_synthetic"),), terms=("Signal", "Enter"))
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        hints=(ConceptHint(record.record_id, "Signal", scope="entry"),),
    )

    automatic = candidate.synthesize_procedure(record.record_id)
    assert automatic.steps[0].concept_id == candidate.concept_id_for("Signal", scope="entry")
    assert automatic.step_scopes == ("entry", None)
    synthesis = candidate.synthesize_procedure(record.record_id, step_scopes=("entry", None))
    assert synthesis.steps[0].concept_id == candidate.concept_id_for("Signal", scope="entry")
    assert synthesis.step_scopes == ("entry", None)


def test_synthesis_preserves_scoped_record_occurrences_over_global_label_lookup():
    scoped_relationship = relationship(left="Signal", right="Entry")
    unscoped_relationship = relationship(left="Signal", right="Risk")
    differently_scoped_relationship = relationship(left="Signal", right="Target")
    scoped_procedure = procedure(
        dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
        terms=("Signal", "Enter"),
    )
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(
            scoped_relationship,
            unscoped_relationship,
            differently_scoped_relationship,
            scoped_procedure,
        ),
        hints=(
            ConceptHint(scoped_relationship.record_id, "Signal", scope="entry"),
            ConceptHint(differently_scoped_relationship.record_id, "Signal", scope="exit"),
            ConceptHint(scoped_procedure.record_id, "Signal", scope="entry"),
        ),
    )

    automatic_relationship = candidate.synthesize_relationship(scoped_relationship.record_id)
    with pytest.raises(ValueError, match="scope"):
        candidate.synthesize_relationship(scoped_relationship.record_id, left_scope="exit")
    automatic_procedure = candidate.synthesize_procedure(scoped_procedure.record_id)
    with pytest.raises(ValueError, match="scope"):
        candidate.synthesize_procedure(scoped_procedure.record_id, step_scopes=("exit", None))

    relationship_synthesis = candidate.synthesize_relationship(scoped_relationship.record_id, left_scope="entry")
    procedure_synthesis = candidate.synthesize_procedure(scoped_procedure.record_id, step_scopes=("entry", None))
    assert automatic_relationship.left_concept_id == candidate.concept_id_for("Signal", scope="entry")
    assert automatic_procedure.steps[0].concept_id == candidate.concept_id_for("Signal", scope="entry")
    assert relationship_synthesis.left_concept_id == candidate.concept_id_for("Signal", scope="entry")
    assert procedure_synthesis.steps[0].concept_id == candidate.concept_id_for("Signal", scope="entry")


def test_procedure_rejects_private_text_in_conditions_and_branches():
    record = procedure(
        dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
        terms=("Observe", "Enter"),
    )
    candidate = SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(record,))
    observe_id = candidate.concept_id_for("Observe")

    with pytest.raises(ValueError, match="private"):
        candidate.synthesize_procedure(
            record.record_id,
            branches=(ProcedureBranch("Private reasoning: hidden analysis.", (observe_id,)),),
        )

    synthesis = candidate.synthesize_procedure(record.record_id)
    with pytest.raises(ValueError, match="private"):
        candidate.publish(
            relationships=(),
            procedures=(
                replace(
                    synthesis,
                    branches=(ProcedureBranch("Private reasoning: hidden analysis.", (observe_id,)),),
                ),
            ),
        )

    with pytest.raises(ValueError, match="private"):
        procedure(
            dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
            terms=("Observe", "Enter"),
            conditions=("Private reasoning: hidden analysis.",),
        )


def test_hints_keep_same_label_relationship_roles_in_distinct_scopes():
    record = relationship(left="Signal", right="Signal")
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        hints=(
            ConceptHint(record.record_id, "Signal", scope="entry", role="left"),
            ConceptHint(record.record_id, "Signal", scope="exit", role="right"),
        ),
    )

    synthesis = candidate.synthesize_relationship(record.record_id, left_scope="entry", right_scope="exit")
    assert synthesis.left_concept_id == candidate.concept_id_for("Signal", scope="entry")
    assert synthesis.right_concept_id == candidate.concept_id_for("Signal", scope="exit")
    assert synthesis.left_concept_id != synthesis.right_concept_id


def test_hints_keep_repeated_procedure_positions_in_distinct_scopes():
    record = procedure(
        dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
        terms=("Observe", "Observe", "Enter"),
    )
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        hints=(
            ConceptHint(record.record_id, "Observe", scope="first", role="term", position=0),
            ConceptHint(record.record_id, "Observe", scope="second", role="term", position=1),
        ),
    )

    synthesis = candidate.synthesize_procedure(record.record_id, step_scopes=("first", "second", None))
    assert synthesis.steps[0].concept_id == candidate.concept_id_for("Observe", scope="first")
    assert synthesis.steps[1].concept_id == candidate.concept_id_for("Observe", scope="second")
    assert synthesis.steps[0].concept_id != synthesis.steps[1].concept_id


def test_hints_reject_ambiguous_repeated_record_labels_without_a_role_or_position():
    record = procedure(
        dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
        terms=("Observe", "Observe", "Enter"),
    )

    with pytest.raises(ValueError, match="ambiguous"):
        SynthesisCandidate.from_records(
            snapshot_id=SNAPSHOT_ID,
            records=(record,),
            hints=(ConceptHint(record.record_id, "Observe", scope="first"),),
        )


def test_procedure_conditions_require_an_allowed_structured_source_and_reject_normalized_private_text():
    record = procedure(
        dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
        terms=("Observe", "Enter"),
        conditions=("Proceed after confirmation.",),
    )
    candidate = SynthesisCandidate.from_records(snapshot_id=SNAPSHOT_ID, records=(record,))
    observe_id = candidate.concept_id_for("Observe")

    with pytest.raises(ValueError, match="provenance"):
        candidate.synthesize_procedure(
            record.record_id,
            branches=(
                ProcedureBranch(
                    condition="A safe but unsourced condition.",
                    step_concept_ids=(observe_id,),
                    condition_index=0,
                ),
            ),
        )

    sourced = candidate.synthesize_procedure(
        record.record_id,
        branches=(ProcedureBranch(condition="Proceed after confirmation.", step_concept_ids=(observe_id,)),),
    )
    assert sourced.branches[0].condition_index == 0

    for private_condition in (
        "My private analysis: hidden details.",
        "Scratch pad: hidden details.",
        "Transcript excerpt: exact speaker words.",
    ):
        with pytest.raises(ValueError, match="private|raw"):
            procedure(
                dependencies=(RecordDependency("source_revision", "rev_synthetic"),),
                terms=("Observe", "Enter"),
                conditions=(private_condition,),
            )
