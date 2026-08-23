from dataclasses import replace

import pytest

from mentor.derived_records import (
    Claim,
    ConflictUnresolved,
    Facet,
    ProcedureSequenceHierarchy,
    RecordDependency,
    Relationship,
)
from mentor.synthesis import ConceptHint, ProcedureBranch, SynthesisCandidate


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
    with pytest.raises(ValueError, match="scope"):
        candidate.synthesize_relationship(support.record_id)
    synthesis = candidate.synthesize_relationship(support.record_id, left_scope="range")
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


def test_procedure_synthesis_requires_explicit_scope_for_scoped_terms():
    record = procedure(dependencies=(RecordDependency("source_revision", "rev_synthetic"),), terms=("Signal", "Enter"))
    candidate = SynthesisCandidate.from_records(
        snapshot_id=SNAPSHOT_ID,
        records=(record,),
        hints=(ConceptHint(record.record_id, "Signal", scope="entry"),),
    )

    with pytest.raises(ValueError, match="scope"):
        candidate.synthesize_procedure(record.record_id)
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

    with pytest.raises(ValueError, match="scope"):
        candidate.synthesize_relationship(scoped_relationship.record_id)
    with pytest.raises(ValueError, match="scope"):
        candidate.synthesize_relationship(scoped_relationship.record_id, left_scope="exit")
    with pytest.raises(ValueError, match="scope"):
        candidate.synthesize_procedure(scoped_procedure.record_id)
    with pytest.raises(ValueError, match="scope"):
        candidate.synthesize_procedure(scoped_procedure.record_id, step_scopes=("exit", None))

    relationship_synthesis = candidate.synthesize_relationship(scoped_relationship.record_id, left_scope="entry")
    procedure_synthesis = candidate.synthesize_procedure(scoped_procedure.record_id, step_scopes=("entry", None))
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
