"""Compact, typed semantic records derived from anchored source material."""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json


FAMILIES = frozenset(
    {"claim", "relationship", "procedure_sequence_hierarchy", "evolution", "conflict_unresolved"}
)
EVIDENCE_STATES = frozenset({"raw_taught", "cross_source_synthesis"})
VALIDATION_STATES = frozenset({"pending", "validated", "rejected"})
LIFECYCLE_STATES = frozenset({"candidate", "active", "superseded", "retired"})
FACET_NAMES = frozenset({"scope", "condition", "exception", "outcome", "timeframe"})
MAX_FACETS = 5
MAX_FACET_VALUE_LENGTH = 160
MAX_TERMS = 8
MAX_TYPED_CONTENT_LENGTH = 240


@dataclass(frozen=True)
class RecordDependency:
    kind: str
    identifier: str


@dataclass(frozen=True)
class Facet:
    name: str
    value: str


@dataclass(frozen=True)
class CompilerProvenance:
    model_version: str
    prompt_version: str
    schema_version: str


@dataclass(frozen=True)
class DerivedRecord:
    record_id: str
    snapshot_id: str
    family: str
    derived_kind: str
    evidence_state: str
    validation_state: str
    lifecycle_state: str
    anchors: tuple[str, ...]
    dependencies: tuple[RecordDependency, ...]
    qualification: str
    compiler_provenance: CompilerProvenance | None = None
    facets: tuple[Facet, ...] = ()


@dataclass(frozen=True)
class Claim(DerivedRecord):
    subject: str = ""
    predicate: str = ""
    object: str = ""

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        anchors: tuple[str, ...],
        dependencies: tuple[RecordDependency, ...],
        validation_state: str,
        lifecycle_state: str,
        qualification: str,
        subject: str,
        predicate: str,
        object: str,
        derived_kind: str = "statement",
        evidence_state: str | None = None,
        compiler_provenance: CompilerProvenance | None = None,
        facets: tuple[Facet, ...] = (),
    ) -> "Claim":
        if derived_kind not in {"statement", "definition", "recommendation", "strategy_implication"}:
            raise ValueError("invalid claim derived_kind")
        return _new(
            cls,
            snapshot_id=snapshot_id,
            family="claim",
            derived_kind=derived_kind,
            evidence_state=evidence_state or _default_evidence_state(derived_kind),
            validation_state=validation_state,
            lifecycle_state=lifecycle_state,
            anchors=anchors,
            dependencies=dependencies,
            qualification=qualification,
            compiler_provenance=compiler_provenance,
            facets=facets,
            subject=subject,
            predicate=predicate,
            object=object,
        )


@dataclass(frozen=True)
class Relationship(DerivedRecord):
    left: str = ""
    relation: str = ""
    right: str = ""

    @classmethod
    def create(cls, *, left: str, relation: str, right: str, **common: object) -> "Relationship":
        if relation not in {"supports", "contrasts", "depends_on", "causes"}:
            raise ValueError("invalid relationship relation")
        return _new(cls, family="relationship", left=left, relation=relation, right=right, **_common(common, "relation"))


@dataclass(frozen=True)
class ProcedureSequenceHierarchy(DerivedRecord):
    kind: str = ""
    terms: tuple[str, ...] = ()

    @classmethod
    def create(cls, *, kind: str, terms: tuple[str, ...], **common: object) -> "ProcedureSequenceHierarchy":
        if kind not in {"procedure", "sequence", "hierarchy"} or len(terms) < 2:
            raise ValueError("invalid procedure, sequence, or hierarchy")
        if len(terms) > MAX_TERMS:
            raise ValueError("too many terms")
        return _new(
            cls,
            family="procedure_sequence_hierarchy",
            kind=kind,
            terms=terms,
            **_common(common, kind),
        )


@dataclass(frozen=True)
class Evolution(DerivedRecord):
    subject: str = ""
    previous: str = ""
    current: str = ""

    @classmethod
    def create(cls, *, subject: str, previous: str, current: str, **common: object) -> "Evolution":
        return _new(
            cls,
            family="evolution",
            subject=subject,
            previous=previous,
            current=current,
            **_common(common, "change"),
        )


@dataclass(frozen=True)
class ConflictUnresolved(DerivedRecord):
    kind: str = ""
    subject: str = ""
    alternatives: tuple[str, ...] = ()

    @classmethod
    def create(
        cls, *, kind: str, subject: str, alternatives: tuple[str, ...], **common: object
    ) -> "ConflictUnresolved":
        if kind not in {"conflict", "unresolved"} or len(alternatives) < 2:
            raise ValueError("invalid conflict or unresolved record")
        if len(alternatives) > MAX_TERMS:
            raise ValueError("too many terms")
        return _new(
            cls,
            family="conflict_unresolved",
            kind=kind,
            subject=subject,
            alternatives=alternatives,
            **_common(common, kind),
        )


def create_record(family: str, **_: object) -> DerivedRecord:
    if family not in FAMILIES:
        raise ValueError("unknown derived record family")
    raise ValueError("use a typed derived record family")


def validate_record(record: DerivedRecord) -> None:
    if record.family not in FAMILIES:
        raise ValueError("unknown derived record family")
    if record.evidence_state not in EVIDENCE_STATES:
        raise ValueError("invalid evidence_state")
    if record.validation_state not in VALIDATION_STATES:
        raise ValueError("invalid validation_state")
    if record.lifecycle_state not in LIFECYCLE_STATES:
        raise ValueError("invalid lifecycle_state")
    _require_text(record.snapshot_id, "snapshot_id")
    _require_text(record.derived_kind, "derived_kind")
    _require_text(record.qualification, "qualification")
    if len(record.qualification) > 280:
        raise ValueError("qualification must be concise")
    if not isinstance(record.anchors, tuple) or not record.anchors or any(not isinstance(anchor, str) or not anchor for anchor in record.anchors):
        raise ValueError("records require at least one anchor")
    if not isinstance(record.dependencies, tuple) or not record.dependencies:
        raise ValueError("records require at least one dependency")
    for dependency in record.dependencies:
        if not isinstance(dependency, RecordDependency) or dependency.kind not in {"source_revision", "derived_record"}:
            raise ValueError("invalid record dependency")
        _require_text(dependency.identifier, "dependency identifier")
    _validate_compiler_provenance(record.compiler_provenance)
    _validate_facets(record.facets)
    _validate_family(record)
    if record.record_id != _record_id(record):
        raise ValueError("record identity is not canonical")


def _new(cls: type[DerivedRecord], **values: object) -> DerivedRecord:
    record = cls(record_id="", **values)
    record = replace(record, record_id=_record_id(record))
    validate_record(record)
    return record


def _common(values: dict[str, object], default_kind: str) -> dict[str, object]:
    values = dict(values)
    derived_kind = values.pop("derived_kind", default_kind)
    evidence_state = values.pop("evidence_state", None) or _default_evidence_state(str(derived_kind))
    return {"derived_kind": derived_kind, "evidence_state": evidence_state, **values}


def _default_evidence_state(derived_kind: str) -> str:
    return "cross_source_synthesis" if derived_kind == "strategy_implication" else "raw_taught"


def _validate_compiler_provenance(provenance: object) -> None:
    if provenance is None:
        return
    if not isinstance(provenance, CompilerProvenance):
        raise ValueError("invalid compiler provenance")
    _require_text(provenance.model_version, "compiler model version")
    _require_text(provenance.prompt_version, "compiler prompt version")
    _require_text(provenance.schema_version, "compiler schema version")


def _validate_facets(facets: object) -> None:
    if not isinstance(facets, tuple):
        raise ValueError("facets must be bounded typed facets")
    if len(facets) > MAX_FACETS:
        raise ValueError("too many facets")
    names = set()
    for facet in facets:
        if not isinstance(facet, Facet):
            raise ValueError("facets must be bounded typed facets")
        if facet.name == "confidence":
            raise ValueError("numeric confidence is not allowed")
        if facet.name in {"reasoning", "rationale", "chain_of_thought", "private_reasoning"}:
            raise ValueError("private reasoning is not allowed")
        if facet.name not in FACET_NAMES or not isinstance(facet.value, str):
            raise ValueError("facets must be bounded typed facets")
        if facet.name in names:
            raise ValueError("duplicate facet name")
        names.add(facet.name)
        _require_text(facet.value, "facet value", maximum=MAX_FACET_VALUE_LENGTH)


def _validate_family(record: DerivedRecord) -> None:
    strings: tuple[str, ...]
    if isinstance(record, Claim):
        if record.family != "claim" or record.derived_kind not in {"statement", "definition", "recommendation", "strategy_implication"}:
            raise ValueError("invalid claim record")
        strings = (record.subject, record.predicate, record.object)
    elif isinstance(record, Relationship):
        if record.family != "relationship" or record.derived_kind != "relation" or record.relation not in {"supports", "contrasts", "depends_on", "causes"}:
            raise ValueError("invalid relationship record")
        strings = (record.left, record.relation, record.right)
    elif isinstance(record, ProcedureSequenceHierarchy):
        if record.family != "procedure_sequence_hierarchy" or record.derived_kind != record.kind or record.kind not in {"procedure", "sequence", "hierarchy"} or len(record.terms) < 2:
            raise ValueError("invalid procedure, sequence, or hierarchy")
        strings = record.terms
    elif isinstance(record, Evolution):
        if record.family != "evolution" or record.derived_kind != "change":
            raise ValueError("invalid evolution record")
        strings = (record.subject, record.previous, record.current)
    elif isinstance(record, ConflictUnresolved):
        if record.family != "conflict_unresolved" or record.derived_kind != record.kind or record.kind not in {"conflict", "unresolved"} or len(record.alternatives) < 2:
            raise ValueError("invalid conflict or unresolved record")
        strings = (record.subject, *record.alternatives)
    else:
        raise ValueError("unknown derived record family")
    for value in strings:
        _require_text(value, "typed record value", maximum=MAX_TYPED_CONTENT_LENGTH)


def _require_text(value: object, label: str, *, maximum: int | None = None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{label} exceeds its maximum length")


def _record_id(record: DerivedRecord) -> str:
    values = asdict(record)
    values["record_id"] = ""
    if values["compiler_provenance"] is None:
        del values["compiler_provenance"]
    return f"rec_{sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"
