"""Candidate-scoped concepts assembled from validated typed records."""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Sequence

from mentor.derived_records import (
    Claim,
    ConflictUnresolved,
    DerivedRecord,
    Evolution,
    ProcedureSequenceHierarchy,
    Relationship,
    is_legacy_record,
    reject_private_or_raw_text,
    validate_record,
)


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
class ConceptHint:
    record_id: str
    label: str
    aliases: tuple[str, ...] = ()
    scope: str | None = None
    role: str | None = None
    position: int | None = None


@dataclass(frozen=True)
class ConceptOccurrence:
    record_id: str
    role: str
    position: int | None
    label_key: str
    scope: str | None
    concept_id: str


@dataclass(frozen=True)
class RelationshipSynthesis:
    synthesis_id: str
    snapshot_id: str
    source_record_id: str
    input_record_ids: tuple[str, ...]
    anchor_ids: tuple[str, ...]
    evidence_state: str
    left_concept_id: str
    relation: str
    right_concept_id: str
    justification: str
    left_scope: str | None
    right_scope: str | None


@dataclass(frozen=True)
class ProcedureStep:
    position: int
    concept_id: str


@dataclass(frozen=True)
class ProcedureBranch:
    condition: str
    step_concept_ids: tuple[str, ...]
    positions: tuple[int, ...] = ()
    condition_index: int | None = None


@dataclass(frozen=True)
class ProcedureSynthesis:
    synthesis_id: str
    snapshot_id: str
    source_record_id: str
    input_record_ids: tuple[str, ...]
    anchor_ids: tuple[str, ...]
    evidence_state: str
    steps: tuple[ProcedureStep, ...]
    prerequisite_concept_ids: tuple[str, ...]
    branches: tuple[ProcedureBranch, ...]
    conditions: tuple[str, ...]
    justification: str
    step_scopes: tuple[str | None, ...]


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
    concept_occurrences: tuple[ConceptOccurrence, ...] = ()

    @classmethod
    def from_records(
        cls,
        *,
        snapshot_id: str,
        records: Sequence[DerivedRecord],
        hints: Sequence[ConceptHint] = (),
    ) -> "SynthesisCandidate":
        _require_bounded_text(snapshot_id, "snapshot_id", MAX_LABEL_LENGTH)
        valid_records = []
        for record in records:
            validate_record(record)
            if record.snapshot_id != snapshot_id:
                raise ValueError("record snapshot_id does not match candidate")
            if record.validation_state == "validated" and not is_legacy_record(record):
                valid_records.append(record)
        record_map = {record.record_id: record for record in valid_records}
        if len(record_map) != len(valid_records):
            raise ValueError("duplicate validated record")
        for record in valid_records:
            if isinstance(record, ConflictUnresolved):
                if not set(record.competing_record_ids) <= set(record_map):
                    raise ValueError("conflict requires valid competing record inputs")
                required_anchors = {
                    anchor for record_id in record.competing_record_ids for anchor in record_map[record_id].anchors
                }
                if not required_anchors <= set(record.anchors):
                    raise ValueError("conflict anchors must include competing record anchors")
        ordered_records = tuple(sorted(record_map.values(), key=lambda record: record.record_id))
        concepts, occurrences = _cluster_concepts(snapshot_id, ordered_records, hints)
        ordered_concepts = tuple(sorted(concepts, key=lambda concept: concept.concept_id))
        _validate_concepts(snapshot_id, ordered_concepts, record_map)
        _validate_concept_occurrences(occurrences, ordered_records, ordered_concepts)
        return cls(snapshot_id, ordered_records, ordered_concepts, occurrences)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(record.record_id for record in self.records)

    def concept_id_for(self, label: str, *, scope: str | None = None) -> str:
        requested_scope = _scope_key(scope)
        matches = [
            concept
            for concept in self.concepts
            if _label_key(label) in {_label_key(concept.canonical_label), *(_label_key(alias) for alias in concept.aliases)}
        ]
        scoped_matches = [
            concept
            for concept in matches
            if (scope is None and concept.scope is None)
            or (scope is not None and _scope_key(concept.scope) == requested_scope)
        ]
        if len(scoped_matches) == 1:
            return scoped_matches[0].concept_id
        if scope is None and matches:
            raise ValueError("scope is required for this concept")
        if len(scoped_matches) != 1:
            raise ValueError("unknown or ambiguous concept label")

    def synthesize_relationship(
        self, record_id: str, *, left_scope: str | None = None, right_scope: str | None = None
    ) -> RelationshipSynthesis:
        record = self._record(record_id)
        if not isinstance(record, Relationship):
            raise ValueError("relationship synthesis requires a relationship record")
        inputs = self._supporting_records(record)
        left_concept_id = self._concept_id_for_occurrence(
            record.record_id, "left", None, record.left, scope=left_scope
        )
        right_concept_id = self._concept_id_for_occurrence(
            record.record_id, "right", None, record.right, scope=right_scope
        )
        justification = _justification(record.relation)
        synthesis = RelationshipSynthesis(
            synthesis_id=_synthesis_id(
                "relationship",
                self.snapshot_id,
                tuple(item.record_id for item in inputs),
                _anchors(inputs),
                (left_concept_id, record.relation, right_concept_id, left_scope, right_scope),
            ),
            snapshot_id=self.snapshot_id,
            source_record_id=record.record_id,
            input_record_ids=tuple(item.record_id for item in inputs),
            anchor_ids=_anchors(inputs),
            evidence_state=_evidence_state(inputs),
            left_concept_id=left_concept_id,
            relation=record.relation,
            right_concept_id=right_concept_id,
            justification=justification,
            left_scope=left_scope,
            right_scope=right_scope,
        )
        _validate_justification(synthesis.justification)
        return synthesis

    def synthesize_procedure(
        self,
        record_id: str,
        *,
        prerequisite_concept_ids: tuple[str, ...] = (),
        branches: tuple[ProcedureBranch, ...] = (),
        step_scopes: tuple[str | None, ...] = (),
    ) -> ProcedureSynthesis:
        record = self._record(record_id)
        if not isinstance(record, ProcedureSequenceHierarchy) or record.kind != "procedure":
            raise ValueError("procedure synthesis requires a procedure record")
        _require_concept_ids(prerequisite_concept_ids, self.concepts, allow_empty=True)
        if not isinstance(step_scopes, tuple) or (step_scopes and len(step_scopes) != len(record.terms)):
            raise ValueError("procedure step scopes must match ordered steps")
        step_scopes = step_scopes or (None,) * len(record.terms)
        conditions = self._procedure_conditions(record)
        branches = _normalized_branches(branches, self.concepts, conditions)
        inputs = self._supporting_records(record)
        steps = tuple(
            ProcedureStep(
                position,
                self._concept_id_for_occurrence(record.record_id, "term", position, term, scope=step_scopes[position]),
            )
            for position, term in enumerate(record.terms)
        )
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
                    tuple(
                        (branch.condition, branch.step_concept_ids, branch.positions, branch.condition_index)
                        for branch in branches
                    ),
                    conditions,
                    step_scopes,
                ),
            ),
            snapshot_id=self.snapshot_id,
            source_record_id=record.record_id,
            input_record_ids=tuple(item.record_id for item in inputs),
            anchor_ids=_anchors(inputs),
            evidence_state=_evidence_state(inputs),
            steps=steps,
            prerequisite_concept_ids=prerequisite_concept_ids,
            branches=branches,
            conditions=conditions,
            justification=justification,
            step_scopes=step_scopes,
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
            if not isinstance(relationship, RelationshipSynthesis):
                raise ValueError("published relationships must be typed")
            if any(
                not isinstance(concept_id, str) or concept_id not in known_concept_ids
                for concept_id in (relationship.left_concept_id, relationship.right_concept_id)
            ):
                raise ValueError("published records require valid concept IDs")
            _validate_justification(relationship.justification)
            expected = self.synthesize_relationship(
                relationship.source_record_id,
                left_scope=relationship.left_scope,
                right_scope=relationship.right_scope,
            )
            if relationship != expected:
                raise ValueError("published relationship is not canonical")
        for procedure in procedures:
            if not isinstance(procedure, ProcedureSynthesis):
                raise ValueError("published procedures must be typed")
            _validate_justification(procedure.justification)
            _require_concept_ids(procedure.prerequisite_concept_ids, self.concepts, allow_empty=True)
            _require_ordered_concept_ids(tuple(step.concept_id for step in procedure.steps), self.concepts)
            source_record = self._record(procedure.source_record_id)
            if not isinstance(source_record, ProcedureSequenceHierarchy) or source_record.kind != "procedure":
                raise ValueError("procedure synthesis requires a procedure record")
            _normalized_branches(procedure.branches, self.concepts, self._procedure_conditions(source_record))
            expected = self.synthesize_procedure(
                procedure.source_record_id,
                prerequisite_concept_ids=procedure.prerequisite_concept_ids,
                branches=procedure.branches,
                step_scopes=procedure.step_scopes,
            )
            if procedure != expected:
                raise ValueError("published procedure is not canonical")
        if len({item.synthesis_id for item in relationships}) != len(relationships) or len(
            {item.synthesis_id for item in procedures}
        ) != len(procedures):
            raise ValueError("published synthesis IDs must be unique")
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

    def _concept_id_for_occurrence(
        self, record_id: str, role: str, position: int | None, label: str, *, scope: str | None
    ) -> str:
        label_key = _label_key(label)
        role_key = _role_key(role)
        _require_position(position)
        matches = [
            occurrence
            for occurrence in self.concept_occurrences
            if occurrence.record_id == record_id
            and occurrence.role == role_key
            and occurrence.position == position
            and occurrence.label_key == label_key
        ]
        if len(matches) != 1:
            raise ValueError("record occurrence does not resolve to one concept")
        occurrence = matches[0]
        if _scope_key(scope) != _scope_key(occurrence.scope):
            if occurrence.scope is not None and scope is None:
                raise ValueError("scope is required for this record occurrence")
            raise ValueError("scope does not match this record occurrence")
        return occurrence.concept_id

    def _procedure_conditions(self, record: ProcedureSequenceHierarchy) -> tuple[str, ...]:
        conditions = tuple(facet.value for facet in record.facets if facet.name == "condition")
        for condition in conditions:
            _require_condition(condition)
        return conditions

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


def _validate_concept_occurrences(
    occurrences: tuple[ConceptOccurrence, ...], records: Sequence[DerivedRecord], concepts: Sequence[Concept]
) -> None:
    expected = {
        (record.record_id, role, position)
        for record in records
        for role, position, _ in _record_occurrence_terms(record)
    }
    occurrence_keys = {(occurrence.record_id, occurrence.role, occurrence.position) for occurrence in occurrences}
    if occurrence_keys != expected or len(occurrence_keys) != len(occurrences):
        raise ValueError("concept occurrences must cover validated record terms exactly once")
    records_by_id = {record.record_id: record for record in records}
    concepts_by_id = {concept.concept_id: concept for concept in concepts}
    for occurrence in occurrences:
        record = records_by_id[occurrence.record_id]
        _role_key(occurrence.role)
        _require_position(occurrence.position)
        concept = concepts_by_id.get(occurrence.concept_id)
        if concept is None or record.record_id not in concept.supporting_record_ids:
            raise ValueError("concept occurrence requires a valid supporting concept")
        if _scope_key(occurrence.scope) != _scope_key(concept.scope):
            raise ValueError("concept occurrence scope does not match concept")
        labels = {_label_key(concept.canonical_label), *(_label_key(alias) for alias in concept.aliases)}
        if occurrence.label_key not in labels:
            raise ValueError("concept occurrence label does not match concept")
        if (occurrence.role, occurrence.position, occurrence.label_key) not in {
            (role, position, _label_key(label)) for role, position, label in _record_occurrence_terms(record)
        }:
            raise ValueError("concept occurrence does not match a validated record term")


def _cluster_concepts(
    snapshot_id: str, records: Sequence[DerivedRecord], hints: Sequence[ConceptHint]
) -> tuple[tuple[Concept, ...], tuple[ConceptOccurrence, ...]]:
    occurrences: dict[
        tuple[str, str, int | None], tuple[DerivedRecord, str, int | None, str, str | None, tuple[str, ...]]
    ] = {}
    for record in records:
        for role, position, label in _record_occurrence_terms(record):
            occurrences[(record.record_id, role, position)] = (record, role, position, label, None, ())
    hinted = set()
    for hint in hints:
        if not isinstance(hint, ConceptHint):
            raise ValueError("concept hints must be typed")
        role = _role_key(hint.role) if hint.role is not None else None
        _require_position(hint.position)
        candidates = [
            key
            for key, (_, occurrence_role, position, label, _, _) in occurrences.items()
            if key[0] == hint.record_id
            and _label_key(label) == _label_key(hint.label)
            and (role is None or occurrence_role == role)
            and (hint.position is None or position == hint.position)
        ]
        if not candidates:
            raise ValueError("concept hints require a valid supporting record reference")
        if len(candidates) != 1:
            raise ValueError("concept hint is ambiguous; role or position is required")
        key = candidates[0]
        if key in hinted:
            raise ValueError("duplicate concept hint")
        hinted.add(key)
        record, occurrence_role, position, label, _, _ = occurrences[key]
        _require_labels(hint.aliases, _label_key(hint.label))
        if hint.scope is not None:
            _scope_key(hint.scope)
        occurrences[key] = (record, occurrence_role, position, label, hint.scope, hint.aliases)

    nodes: dict[tuple[str | None, str], list[tuple[DerivedRecord, str]]] = {}
    edges: dict[tuple[str | None, str], set[tuple[str | None, str]]] = {}
    scope_values: dict[str | None, str | None] = {None: None}
    for record, _, _, label, scope, aliases in occurrences.values():
        scope_key = _scope_key(scope)
        scope_values.setdefault(scope_key, scope)
        node = (scope_key, _label_key(label))
        nodes.setdefault(node, []).append((record, label))
        edges.setdefault(node, set())
        for alias in aliases:
            alias_node = (scope_key, _label_key(alias))
            edges.setdefault(alias_node, set()).add(node)
            edges[node].add(alias_node)

    concepts = []
    concept_occurrences = []
    visited = set()
    for start in sorted(nodes, key=lambda node: ((node[0] or ""), node[1])):
        if start in visited:
            continue
        component = set()
        pending = [start]
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            pending.extend(edges[node])
        visited.update(component)
        labels = [item for node in component for item in nodes.get(node, ())]
        canonical_label = min((label for _, label in labels), key=lambda label: (_label_key(label), label))
        canonical_key = _label_key(canonical_label)
        alias_keys = {node[1] for node in component} - {canonical_key}
        aliases = tuple(sorted(alias_keys))
        supporting_records = tuple(dict.fromkeys(record.record_id for record, _ in labels))
        supporting_anchors = tuple(dict.fromkeys(anchor for record, _ in labels for anchor in record.anchors))
        concept = Concept.create(
            snapshot_id=snapshot_id,
            canonical_label=canonical_label,
            aliases=aliases,
            scope=scope_values[start[0]],
            supporting_record_ids=supporting_records,
            supporting_anchor_ids=supporting_anchors,
        )
        concepts.append(concept)
        for (record_id, role, position), (_, _, _, label, scope, _) in occurrences.items():
            label_key = _label_key(label)
            if (_scope_key(scope), label_key) in component:
                concept_occurrences.append(ConceptOccurrence(record_id, role, position, label_key, scope, concept.concept_id))
    return (
        tuple(concepts),
        tuple(
            sorted(
                concept_occurrences,
                key=lambda occurrence: (occurrence.record_id, occurrence.role, occurrence.position or -1),
            )
        ),
    )


def _record_occurrence_terms(record: DerivedRecord) -> tuple[tuple[str, int | None, str], ...]:
    if isinstance(record, Claim):
        return ("subject", None, record.subject), ("object", None, record.object)
    if isinstance(record, Relationship):
        return ("left", None, record.left), ("right", None, record.right)
    if isinstance(record, ProcedureSequenceHierarchy):
        return tuple(("term", position, term) for position, term in enumerate(record.terms))
    if isinstance(record, Evolution):
        return (
            ("subject", None, record.subject),
            ("previous", None, record.previous),
            ("current", None, record.current),
        )
    if isinstance(record, ConflictUnresolved):
        return (
            ("subject", None, record.subject),
            *(("alternative", position, term) for position, term in enumerate(record.alternatives)),
        )
    raise ValueError("unknown validated record")


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


def _require_ordered_concept_ids(concept_ids: tuple[str, ...], concepts: Sequence[Concept]) -> None:
    if not isinstance(concept_ids, tuple) or not concept_ids:
        raise ValueError("ordered concept IDs must be a non-empty tuple")
    known_concept_ids = {concept.concept_id for concept in concepts}
    if any(not isinstance(concept_id, str) or concept_id not in known_concept_ids for concept_id in concept_ids):
        raise ValueError("published records require valid concept IDs")


def _normalized_branches(
    branches: tuple[ProcedureBranch, ...], concepts: Sequence[Concept], allowed_conditions: tuple[str, ...]
) -> tuple[ProcedureBranch, ...]:
    if not isinstance(branches, tuple):
        raise ValueError("procedure branches must be a tuple")
    normalized = []
    for branch in branches:
        if not isinstance(branch, ProcedureBranch):
            raise ValueError("procedure branches must be structured")
        _require_condition(branch.condition)
        condition_index = branch.condition_index
        if condition_index is None:
            matches = [
                index for index, condition in enumerate(allowed_conditions) if condition == branch.condition
            ]
            if len(matches) != 1:
                raise ValueError("procedure branch condition requires structured provenance")
            condition_index = matches[0]
        if (
            isinstance(condition_index, bool)
            or not isinstance(condition_index, int)
            or condition_index < 0
            or condition_index >= len(allowed_conditions)
            or branch.condition != allowed_conditions[condition_index]
        ):
            raise ValueError("procedure branch condition requires structured provenance")
        _require_ordered_concept_ids(branch.step_concept_ids, concepts)
        positions = branch.positions or tuple(range(len(branch.step_concept_ids)))
        if not isinstance(positions, tuple) or positions != tuple(range(len(branch.step_concept_ids))):
            raise ValueError("procedure branch positions must be ordered")
        normalized.append(ProcedureBranch(branch.condition, branch.step_concept_ids, positions, condition_index))
    return tuple(normalized)


def _require_condition(condition: object) -> None:
    if not isinstance(condition, str) or not condition.strip():
        raise ValueError("procedure condition must be non-empty text")
    if len(condition) > MAX_CONDITION_LENGTH:
        raise ValueError("raw text dump is not allowed")
    _reject_private_text(condition, "procedure condition")


def _validate_justification(justification: object) -> None:
    if not isinstance(justification, str) or not justification.strip() or len(justification) > MAX_JUSTIFICATION_LENGTH:
        raise ValueError("synthesis justification must be concise")
    _reject_private_text(justification, "synthesis justification")


def _reject_private_text(value: str, label: str) -> None:
    reject_private_or_raw_text(value, label)


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


def _role_key(role: str) -> str:
    _require_bounded_text(role, "concept occurrence role", MAX_LABEL_LENGTH)
    return " ".join(role.split()).casefold()


def _require_position(position: object) -> None:
    if position is not None and (isinstance(position, bool) or not isinstance(position, int) or position < 0):
        raise ValueError("concept occurrence position must be a non-negative integer")


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
