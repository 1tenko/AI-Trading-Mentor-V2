"""Candidate-scoped concepts assembled from validated typed records."""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Sequence

from mentor.derived_records import DerivedRecord, ProcedureSequenceHierarchy, Relationship, validate_record


MAX_LABEL_LENGTH = 120
MAX_ALIASES = 8
MAX_SCOPE_LENGTH = 160
MAX_CONDITION_LENGTH = 160
MAX_JUSTIFICATION_LENGTH = 280


@dataclass(frozen=True)
class Concept:
    concept_id: str
    snapshot_id: str
    canonical_label: str
    aliases: tuple[str, ...]
    scope: str | None
    supporting_record_ids: tuple[str, ...]
    supporting_anchor_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        canonical_label: str,
        aliases: tuple[str, ...],
        scope: str | None,
        supporting_record_ids: tuple[str, ...],
        supporting_anchor_ids: tuple[str, ...],
    ) -> "Concept":
        label_key = _label_key(canonical_label)
        _require_bounded_text(snapshot_id, "snapshot_id", MAX_LABEL_LENGTH)
        _require_labels(aliases, label_key)
        if scope is not None:
            _require_bounded_text(scope, "scope", MAX_SCOPE_LENGTH)
        _require_identifiers(supporting_record_ids, "supporting record")
        _require_identifiers(supporting_anchor_ids, "supporting anchor")
        return cls(
            concept_id=_concept_id(snapshot_id, label_key, scope),
            snapshot_id=snapshot_id,
            canonical_label=canonical_label,
            aliases=aliases,
            scope=scope,
            supporting_record_ids=supporting_record_ids,
            supporting_anchor_ids=supporting_anchor_ids,
        )


@dataclass(frozen=True)
class RelationshipSynthesis:
    synthesis_id: str
    snapshot_id: str
    input_record_ids: tuple[str, ...]
    anchor_ids: tuple[str, ...]
    evidence_state: str
    left_concept_id: str
    relation: str
    right_concept_id: str
    justification: str


@dataclass(frozen=True)
class ProcedureStep:
    position: int
    concept_id: str


@dataclass(frozen=True)
class ProcedureBranch:
    condition: str
    step_concept_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProcedureSynthesis:
    synthesis_id: str
    snapshot_id: str
    input_record_ids: tuple[str, ...]
    anchor_ids: tuple[str, ...]
    evidence_state: str
    steps: tuple[ProcedureStep, ...]
    prerequisite_concept_ids: tuple[str, ...]
    branches: tuple[ProcedureBranch, ...]
    conditions: tuple[str, ...]
    justification: str


@dataclass(frozen=True)
class PublishedSynthesis:
    snapshot_id: str
    concepts: tuple[Concept, ...]
    relationships: tuple[RelationshipSynthesis, ...]
    procedures: tuple[ProcedureSynthesis, ...]


@dataclass(frozen=True)
class SynthesisCandidate:
    snapshot_id: str
    records: tuple[DerivedRecord, ...]
    concepts: tuple[Concept, ...]

    @classmethod
    def from_records(
        cls,
        *,
        snapshot_id: str,
        records: Sequence[DerivedRecord],
        concepts: Sequence[Concept],
    ) -> "SynthesisCandidate":
        _require_bounded_text(snapshot_id, "snapshot_id", MAX_LABEL_LENGTH)
        valid_records = []
        for record in records:
            validate_record(record)
            if record.snapshot_id != snapshot_id:
                raise ValueError("record snapshot_id does not match candidate")
            if record.validation_state == "validated":
                valid_records.append(record)
        record_map = {record.record_id: record for record in valid_records}
        if len(record_map) != len(valid_records):
            raise ValueError("duplicate validated record")
        ordered_records = tuple(sorted(record_map.values(), key=lambda record: record.record_id))
        ordered_concepts = tuple(sorted(concepts, key=lambda concept: concept.concept_id))
        _validate_concepts(snapshot_id, ordered_concepts, record_map)
        return cls(snapshot_id, ordered_records, ordered_concepts)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(record.record_id for record in self.records)

    def concept_id_for(self, label: str, *, scope: str | None = None) -> str:
        requested_scope = _scope_key(scope)
        matches = [
            concept.concept_id
            for concept in self.concepts
            if _label_key(label) in {_label_key(concept.canonical_label), *(_label_key(alias) for alias in concept.aliases)}
            and (scope is None or _scope_key(concept.scope) == requested_scope)
        ]
        if len(matches) != 1:
            raise ValueError("unknown or ambiguous concept label")
        return matches[0]

    def synthesize_relationship(self, record_id: str) -> RelationshipSynthesis:
        record = self._record(record_id)
        if not isinstance(record, Relationship):
            raise ValueError("relationship synthesis requires a relationship record")
        inputs = self._supporting_records(record)
        justification = _justification(record.relation)
        synthesis = RelationshipSynthesis(
            synthesis_id=_synthesis_id(
                "relationship",
                self.snapshot_id,
                tuple(item.record_id for item in inputs),
                _anchors(inputs),
                (self.concept_id_for(record.left), record.relation, self.concept_id_for(record.right)),
            ),
            snapshot_id=self.snapshot_id,
            input_record_ids=tuple(item.record_id for item in inputs),
            anchor_ids=_anchors(inputs),
            evidence_state=_evidence_state(inputs),
            left_concept_id=self.concept_id_for(record.left),
            relation=record.relation,
            right_concept_id=self.concept_id_for(record.right),
            justification=justification,
        )
        _validate_justification(synthesis.justification)
        return synthesis

    def synthesize_procedure(
        self,
        record_id: str,
        *,
        prerequisite_concept_ids: tuple[str, ...] = (),
        branches: tuple[ProcedureBranch, ...] = (),
    ) -> ProcedureSynthesis:
        record = self._record(record_id)
        if not isinstance(record, ProcedureSequenceHierarchy) or record.kind != "procedure":
            raise ValueError("procedure synthesis requires a procedure record")
        _require_concept_ids(prerequisite_concept_ids, self.concepts, allow_empty=True)
        _validate_branches(branches, self.concepts)
        inputs = self._supporting_records(record)
        steps = tuple(ProcedureStep(position, self.concept_id_for(term)) for position, term in enumerate(record.terms))
        conditions = tuple(facet.value for facet in record.facets if facet.name == "condition")
        for condition in conditions:
            _require_condition(condition)
        justification = _justification("procedure")
        synthesis = ProcedureSynthesis(
            synthesis_id=_synthesis_id(
                "procedure",
                self.snapshot_id,
                tuple(item.record_id for item in inputs),
                _anchors(inputs),
                (
                    tuple(step.concept_id for step in steps),
                    prerequisite_concept_ids,
                    tuple((branch.condition, branch.step_concept_ids) for branch in branches),
                    conditions,
                ),
            ),
            snapshot_id=self.snapshot_id,
            input_record_ids=tuple(item.record_id for item in inputs),
            anchor_ids=_anchors(inputs),
            evidence_state=_evidence_state(inputs),
            steps=steps,
            prerequisite_concept_ids=prerequisite_concept_ids,
            branches=branches,
            conditions=conditions,
            justification=justification,
        )
        _validate_justification(synthesis.justification)
        return synthesis

    def publish(
        self,
        *,
        relationships: Sequence[RelationshipSynthesis],
        procedures: Sequence[ProcedureSynthesis],
    ) -> PublishedSynthesis:
        known_concept_ids = {concept.concept_id for concept in self.concepts}
        for relationship in relationships:
            _validate_published_common(relationship, self)
            if {relationship.left_concept_id, relationship.right_concept_id} - known_concept_ids:
                raise ValueError("published records require valid concept IDs")
        for procedure in procedures:
            _validate_published_common(procedure, self)
            _require_concept_ids(procedure.prerequisite_concept_ids, self.concepts, allow_empty=True)
            _require_concept_ids(tuple(step.concept_id for step in procedure.steps), self.concepts)
            _validate_branches(procedure.branches, self.concepts)
            for condition in procedure.conditions:
                _require_condition(condition)
        return PublishedSynthesis(
            self.snapshot_id,
            self.concepts,
            tuple(sorted(relationships, key=lambda relationship: relationship.synthesis_id)),
            tuple(sorted(procedures, key=lambda procedure: procedure.synthesis_id)),
        )

    def _record(self, record_id: str) -> DerivedRecord:
        for record in self.records:
            if record.record_id == record_id:
                return record
        raise ValueError("unknown validated record")

    def _supporting_records(self, record: DerivedRecord) -> tuple[DerivedRecord, ...]:
        record_map = {item.record_id: item for item in self.records}
        result: list[DerivedRecord] = []
        active: set[str] = set()

        def visit(current: DerivedRecord) -> None:
            if current.record_id in active:
                raise ValueError("derived record dependencies cannot cycle")
            if current in result:
                return
            active.add(current.record_id)
            result.append(current)
            for dependency in current.dependencies:
                if dependency.kind == "derived_record":
                    dependent = record_map.get(dependency.identifier)
                    if dependent is None:
                        raise ValueError("synthesis requires valid dependent records")
                    visit(dependent)
            active.remove(current.record_id)

        visit(record)
        return tuple(result)


def _validate_concepts(
    snapshot_id: str, concepts: tuple[Concept, ...], records: dict[str, DerivedRecord]
) -> None:
    concept_ids = set()
    labels = set()
    for concept in concepts:
        if concept.snapshot_id != snapshot_id:
            raise ValueError("concept snapshot_id does not match candidate")
        label_key = _label_key(concept.canonical_label)
        _require_labels(concept.aliases, label_key)
        if concept.scope is not None:
            _require_bounded_text(concept.scope, "scope", MAX_SCOPE_LENGTH)
        if concept.concept_id != _concept_id(snapshot_id, label_key, concept.scope):
            raise ValueError("concept identity is not canonical")
        if concept.concept_id in concept_ids:
            raise ValueError("duplicate concept")
        concept_ids.add(concept.concept_id)
        _require_identifiers(concept.supporting_record_ids, "supporting record")
        _require_identifiers(concept.supporting_anchor_ids, "supporting anchor")
        supporting_records = []
        for record_id in concept.supporting_record_ids:
            record = records.get(record_id)
            if record is None:
                raise ValueError("concept requires a valid supporting record")
            supporting_records.append(record)
        valid_anchors = {anchor_id for record in supporting_records for anchor_id in record.anchors}
        if not set(concept.supporting_anchor_ids) <= valid_anchors:
            raise ValueError("concept requires a valid supporting anchor")
        for label in (concept.canonical_label, *concept.aliases):
            key = (_label_key(label), _scope_key(concept.scope))
            if key in labels:
                raise ValueError("concept labels must resolve uniquely")
            labels.add(key)


def _anchors(records: Sequence[DerivedRecord]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(anchor for record in records for anchor in record.anchors))


def _evidence_state(records: Sequence[DerivedRecord]) -> str:
    return "cross_source_synthesis" if any(record.evidence_state == "cross_source_synthesis" for record in records) else "raw_taught"


def _justification(kind: str) -> str:
    return f"{kind} is supported by validated input records."


def _synthesis_id(
    kind: str,
    snapshot_id: str,
    input_record_ids: tuple[str, ...],
    anchor_ids: tuple[str, ...],
    structure: object,
) -> str:
    value = json.dumps(
        (kind, snapshot_id, input_record_ids, anchor_ids, structure), sort_keys=True, separators=(",", ":")
    )
    return f"syn_{sha256(value.encode()).hexdigest()}"


def _require_concept_ids(
    concept_ids: tuple[str, ...], concepts: Sequence[Concept], *, allow_empty: bool = False
) -> None:
    if not isinstance(concept_ids, tuple) or (not allow_empty and not concept_ids) or len(set(concept_ids)) != len(concept_ids):
        raise ValueError("concept IDs must be a unique tuple")
    known_concept_ids = {concept.concept_id for concept in concepts}
    if any(not isinstance(concept_id, str) or concept_id not in known_concept_ids for concept_id in concept_ids):
        raise ValueError("published records require valid concept IDs")


def _validate_branches(branches: tuple[ProcedureBranch, ...], concepts: Sequence[Concept]) -> None:
    if not isinstance(branches, tuple):
        raise ValueError("procedure branches must be a tuple")
    for branch in branches:
        if not isinstance(branch, ProcedureBranch):
            raise ValueError("procedure branches must be structured")
        _require_condition(branch.condition)
        _require_concept_ids(branch.step_concept_ids, concepts)


def _require_condition(condition: object) -> None:
    if not isinstance(condition, str) or not condition.strip():
        raise ValueError("procedure condition must be non-empty text")
    if len(condition) > MAX_CONDITION_LENGTH:
        raise ValueError("raw text dump is not allowed")


def _validate_published_common(
    synthesis: RelationshipSynthesis | ProcedureSynthesis,
    candidate: SynthesisCandidate,
) -> None:
    if synthesis.snapshot_id != candidate.snapshot_id:
        raise ValueError("published records must belong to the candidate")
    if not synthesis.input_record_ids or set(synthesis.input_record_ids) - set(candidate.record_ids):
        raise ValueError("published records require valid input record IDs")
    if not synthesis.anchor_ids or set(synthesis.anchor_ids) - {anchor for record in candidate.records for anchor in record.anchors}:
        raise ValueError("published records require valid anchor IDs")
    if synthesis.evidence_state not in {"raw_taught", "cross_source_synthesis"}:
        raise ValueError("published records require valid evidence state")
    _validate_justification(synthesis.justification)


def _validate_justification(justification: object) -> None:
    if not isinstance(justification, str) or not justification.strip() or len(justification) > MAX_JUSTIFICATION_LENGTH:
        raise ValueError("synthesis justification must be concise")


def _concept_id(snapshot_id: str, label_key: str, scope: str | None) -> str:
    scope_key = _scope_key(scope) or ""
    return f"con_{sha256(f'{snapshot_id}\0{label_key}\0{scope_key}'.encode()).hexdigest()}"


def _label_key(value: str) -> str:
    _require_bounded_text(value, "concept label", MAX_LABEL_LENGTH)
    return " ".join(value.split()).casefold()


def _scope_key(scope: str | None) -> str | None:
    if scope is None:
        return None
    _require_bounded_text(scope, "scope", MAX_SCOPE_LENGTH)
    return " ".join(scope.split()).casefold()


def _require_labels(aliases: tuple[str, ...], canonical_key: str) -> None:
    if not isinstance(aliases, tuple) or len(aliases) > MAX_ALIASES:
        raise ValueError("aliases must be a bounded tuple")
    alias_keys = tuple(_label_key(alias) for alias in aliases)
    if canonical_key in alias_keys or len(set(alias_keys)) != len(alias_keys):
        raise ValueError("aliases must resolve uniquely")


def _require_identifiers(values: tuple[str, ...], label: str) -> None:
    if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
        raise ValueError(f"{label} IDs must be non-empty and unique")
    for value in values:
        _require_bounded_text(value, f"{label} ID", MAX_LABEL_LENGTH)


def _require_bounded_text(value: object, label: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds its maximum length")
