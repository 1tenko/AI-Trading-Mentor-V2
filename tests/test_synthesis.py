from dataclasses import replace

import pytest

from mentor.derived_records import Facet, ProcedureSequenceHierarchy, Relationship, Claim, RecordDependency
from mentor.synthesis import Concept, ProcedureBranch, SynthesisCandidate


SNAPSHOT_ID = "snap_synthetic"


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


def procedure(*, dependencies: tuple[RecordDependency, ...]) -> ProcedureSequenceHierarchy:
    return ProcedureSequenceHierarchy.create(
        snapshot_id=SNAPSHOT_ID,
        anchors=("anc_procedure",),
        dependencies=dependencies,
        validation_state="validated",
        lifecycle_state="candidate",
        qualification="Synthetic support.",
        facets=(Facet("condition", "Only after confirmation."),),
        kind="procedure",
        terms=("Observe", "Confirm", "Enter"),
    )


def concept(label: str, *records, aliases: tuple[str, ...] = (), scope: str | None = None) -> Concept:
    return Concept.create(
        snapshot_id=SNAPSHOT_ID,
        canonical_label=label,
        aliases=aliases,
        scope=scope,
        supporting_record_ids=tuple(record.record_id for record in records),
        supporting_anchor_ids=tuple(anchor for record in records for anchor in record.anchors),
    )


def test_candidate_excludes_invalid_records_and_requires_valid_concept_support():
    validated = claim(subject="validated")
    pending = claim(subject="pending", validation_state="pending")
    concept = Concept.create(
        snapshot_id=SNAPSHOT_ID,
        canonical_label="Validated",
        aliases=(),
        scope=None,
        supporting_record_ids=(validated.record_id,),
        supporting_anchor_ids=("anc_validated",),
    )

    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(validated, pending),
        concepts=(concept,),
    )

    assert candidate.record_ids == (validated.record_id,)
    assert candidate.concept_id_for("validated") == concept.concept_id
    with pytest.raises(ValueError, match="valid supporting record"):
        SynthesisCandidate.from_records(
            snapshot_id=SNAPSHOT_ID,
            records=(validated, pending),
            concepts=(
                Concept.create(
                    snapshot_id=SNAPSHOT_ID,
                    canonical_label="Pending",
                    aliases=(),
                    scope=None,
                    supporting_record_ids=(pending.record_id,),
                    supporting_anchor_ids=("anc_pending",),
                ),
            ),
        )


def test_candidate_requires_each_concept_anchor_to_belong_to_its_valid_supporting_record():
    validated = claim(subject="validated")

    with pytest.raises(ValueError, match="valid supporting anchor"):
        SynthesisCandidate.from_records(
            snapshot_id=SNAPSHOT_ID,
            records=(validated,),
            concepts=(
                Concept.create(
                    snapshot_id=SNAPSHOT_ID,
                    canonical_label="Validated",
                    aliases=(),
                    scope=None,
                    supporting_record_ids=(validated.record_id,),
                    supporting_anchor_ids=("anc_other",),
                ),
            ),
        )


def test_repeated_references_converge_on_one_candidate_scoped_concept_id():
    first = relationship(left="Range", right="Entry")
    second = relationship(left="Range", right="Risk")
    range_concept = concept("Range", first, second)
    entry_concept = concept("Entry", first)
    risk_concept = concept("Risk", second)
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(first, second),
        concepts=(range_concept, entry_concept, risk_concept),
    )

    first_synthesis = candidate.synthesize_relationship(first.record_id)
    second_synthesis = candidate.synthesize_relationship(second.record_id)

    assert first_synthesis.left_concept_id == second_synthesis.left_concept_id == range_concept.concept_id
    assert first_synthesis.right_concept_id == entry_concept.concept_id
    assert first_synthesis.relation == "depends_on"
    assert first_synthesis.input_record_ids == (first.record_id,)
    assert first_synthesis.anchor_ids == first.anchors
    assert first_synthesis.justification == "depends_on is supported by validated input records."


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
        concepts=(concept("Range", record), concept("Entry", record)),
    )

    synthesis = candidate.synthesize_relationship(record.record_id)

    assert len(synthesis.input_record_ids) == 6
    assert len(synthesis.justification) <= 280


def test_aliases_resolve_only_to_their_own_concept_and_similar_names_remain_distinct():
    record = claim(subject="support")
    support = concept("Support", record, aliases=("floor",))
    resistance = concept("Supportive", record)
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        concepts=(support, resistance),
    )

    assert candidate.concept_id_for("FLOOR") == support.concept_id
    assert candidate.concept_id_for("support") == support.concept_id
    assert candidate.concept_id_for("supportive") == resistance.concept_id
    assert support.concept_id != resistance.concept_id


def test_concept_ids_are_stable_within_one_candidate_and_scoped_to_another_candidate():
    record = claim(subject="support")
    first = concept("Support", record, aliases=("floor",), scope="market structure")
    second = concept("Support", record, aliases=("floor",), scope="market structure")

    assert first.concept_id == second.concept_id
    assert first.concept_id != Concept.create(
        snapshot_id="snap_other",
        canonical_label="Support",
        aliases=("floor",),
        scope="market structure",
        supporting_record_ids=(record.record_id,),
        supporting_anchor_ids=record.anchors,
    ).concept_id


def test_explicit_scopes_keep_identical_labels_distinct_without_an_unscoped_alias_collision():
    record = claim(subject="signal")
    opening = concept("Signal", record, scope="opening")
    closing = concept("Signal", record, scope="closing")
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        concepts=(opening, closing),
    )

    assert candidate.concept_id_for("Signal", scope="opening") == opening.concept_id
    assert candidate.concept_id_for("Signal", scope="closing") == closing.concept_id
    with pytest.raises(ValueError, match="ambiguous"):
        candidate.concept_id_for("Signal")


def test_procedure_synthesis_retains_transitive_inputs_and_structured_branches():
    context = claim(subject="Context", anchors=("anc_context",))
    record = procedure(
        dependencies=(
            RecordDependency("source_revision", "rev_synthetic"),
            RecordDependency("derived_record", context.record_id),
        )
    )
    concepts = (
        concept("Context", context),
        concept("Observe", record),
        concept("Confirm", record),
        concept("Enter", record),
    )
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(context, record),
        concepts=concepts,
    )

    procedure_synthesis = candidate.synthesize_procedure(
        record.record_id,
        prerequisite_concept_ids=(concepts[0].concept_id,),
        branches=(
            ProcedureBranch(
                condition="If confirmation fails.",
                step_concept_ids=(concepts[1].concept_id,),
            ),
        ),
    )

    assert [step.concept_id for step in procedure_synthesis.steps] == [concept.concept_id for concept in concepts[1:]]
    assert procedure_synthesis.prerequisite_concept_ids == (concepts[0].concept_id,)
    assert procedure_synthesis.conditions == ("Only after confirmation.",)
    assert procedure_synthesis.branches[0].step_concept_ids == (concepts[1].concept_id,)
    assert procedure_synthesis.input_record_ids == (record.record_id, context.record_id)
    assert procedure_synthesis.anchor_ids == ("anc_procedure", "anc_context")
    assert procedure_synthesis.evidence_state == "raw_taught"
    assert procedure_synthesis.justification == "procedure is supported by validated input records."
    assert not hasattr(procedure_synthesis, "confidence")
    assert not hasattr(procedure_synthesis, "reasoning")


def test_synthesis_rejects_raw_text_dumps_and_publish_rejects_unknown_concept_ids():
    record = relationship(left="Range", right="Entry")
    range_concept = concept("Range", record)
    entry_concept = concept("Entry", record)
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        concepts=(range_concept, entry_concept),
    )
    relationship_synthesis = candidate.synthesize_relationship(record.record_id)
    procedure_record = procedure(dependencies=(RecordDependency("source_revision", "rev_synthetic"),))
    procedure_candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(procedure_record,),
        concepts=(
            concept("Observe", procedure_record),
            concept("Confirm", procedure_record),
            concept("Enter", procedure_record),
        ),
    )

    with pytest.raises(ValueError, match="raw text dump"):
        procedure_candidate.synthesize_procedure(
            procedure_record.record_id,
            branches=(ProcedureBranch(condition="source text " * 100, step_concept_ids=(range_concept.concept_id,)),),
        )
    with pytest.raises(ValueError, match="valid concept IDs"):
        candidate.publish(relationships=(replace(relationship_synthesis, left_concept_id="con_unknown"),), procedures=())
