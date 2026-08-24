"""Compact, typed semantic records derived from anchored source material."""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import re
import unicodedata


FAMILIES = frozenset(
    {"claim", "relationship", "procedure_sequence_hierarchy", "evolution", "conflict_unresolved"}
)
DERIVED_KINDS = frozenset(
    {"source_extracted_claim", "cross_source_synthesis", "unresolved_or_conflicting"}
)
EVIDENCE_STATES = frozenset({"raw_taught", "cross_source_synthesis"})
VALIDATION_STATES = frozenset({"pending", "validated", "rejected"})
LIFECYCLE_STATES = frozenset({"candidate", "active", "superseded", "retired"})
EVOLUTION_CLASSIFICATIONS = frozenset(
    {
        "introduced",
        "repeated",
        "refined",
        "expanded",
        "reframed",
        "deprecated_or_deemphasized",
        "apparently_contradictory",
        "uncertain_chronology",
        "no_supported_classification",
    }
)
NEGATIVE_EVIDENCE_STATES = frozenset(
    {
        "positive_teaching",
        "not_found_in_observed_evidence",
        "source_asserted_absence",
        "coverage_supported_synthesis",
        "unresolved",
    }
)
RECONCILIATION_STATES = frozenset(
    {"compatible_under_conditions", "unresolved", "genuinely_contradictory"}
)
RELATIONSHIP_TYPES = frozenset(
    {
        "supports",
        "contrasts",
        "depends_on",
        "causes",
        "applies_when",
        "exception_to",
        "refines",
        "anticipates",
        "uses_internal_structure",
    }
)
FACET_NAMES = frozenset({"scope", "condition", "exception", "outcome", "timeframe"})
MAX_FACETS = 5
MAX_FACET_VALUE_LENGTH = 160
MAX_TERMS = 8
MAX_TYPED_CONTENT_LENGTH = 240
EVOLUTION_WORDING_FAMILIES = frozenset(
    {"introduction", "absence", "removal", "deprecation"}
)


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
    semantic_subtype: str
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
        semantic_subtype: str = "statement",
        derived_kind: str | None = None,
        evidence_state: str | None = None,
        compiler_provenance: CompilerProvenance | None = None,
        facets: tuple[Facet, ...] = (),
    ) -> "Claim":
        if semantic_subtype not in {"statement", "definition", "recommendation", "strategy_implication"}:
            raise ValueError("invalid claim semantic_subtype")
        if derived_kind is None and evidence_state is None and semantic_subtype == "strategy_implication":
            derived_kind = "cross_source_synthesis"
        derived_kind = derived_kind or _derived_kind_for_evidence(evidence_state)
        evidence_state = evidence_state or _evidence_for_derived_kind(derived_kind)
        return _new(
            cls,
            snapshot_id=snapshot_id,
            family="claim",
            derived_kind=derived_kind,
            semantic_subtype=semantic_subtype,
            evidence_state=evidence_state,
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
        if relation not in RELATIONSHIP_TYPES:
            raise ValueError("invalid relationship relation")
        return _new(
            cls,
            family="relationship",
            left=left,
            relation=relation,
            right=right,
            **_common(common, "relation"),
        )


@dataclass(frozen=True)
class ProcedureRecordBranch:
    condition: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class ProcedureSequenceHierarchy(DerivedRecord):
    kind: str = ""
    terms: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    branches: tuple[ProcedureRecordBranch, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        terms: tuple[str, ...],
        prerequisites: tuple[str, ...] = (),
        conditions: tuple[str, ...] = (),
        branches: tuple[ProcedureRecordBranch, ...] = (),
        **common: object,
    ) -> "ProcedureSequenceHierarchy":
        if kind not in {"procedure", "sequence", "hierarchy"} or len(terms) < 2:
            raise ValueError("invalid procedure, sequence, or hierarchy")
        if len(terms) > MAX_TERMS:
            raise ValueError("too many terms")
        return _new(
            cls,
            family="procedure_sequence_hierarchy",
            kind=kind,
            terms=terms,
            prerequisites=prerequisites,
            conditions=conditions,
            branches=branches,
            **_common(common, kind),
        )


@dataclass(frozen=True)
class Evolution(DerivedRecord):
    subject: str = ""
    previous: str = ""
    current: str = ""
    earlier_source_set: tuple[str, ...] = ()
    later_source_set: tuple[str, ...] = ()
    classification: str = "no_supported_classification"
    negative_evidence_state: str = "unresolved"
    competing_anchors: tuple[str, ...] = ()
    earlier_coverage_id: str = ""
    later_coverage_id: str = ""
    earlier_observed_years: tuple[int, ...] = ()
    later_observed_years: tuple[int, ...] = ()
    deprecation_evidence_anchors: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        subject: str,
        previous: str,
        current: str,
        earlier_source_set: tuple[str, ...],
        later_source_set: tuple[str, ...],
        classification: str,
        negative_evidence_state: str,
        earlier_coverage_id: str,
        later_coverage_id: str,
        earlier_observed_years: tuple[int, ...],
        later_observed_years: tuple[int, ...],
        competing_anchors: tuple[str, ...] = (),
        deprecation_evidence_anchors: tuple[str, ...] = (),
        **common: object,
    ) -> "Evolution":
        return _new(
            cls,
            family="evolution",
            subject=subject,
            previous=previous,
            current=current,
            earlier_source_set=earlier_source_set,
            later_source_set=later_source_set,
            classification=classification,
            negative_evidence_state=negative_evidence_state,
            competing_anchors=competing_anchors,
            earlier_coverage_id=earlier_coverage_id,
            later_coverage_id=later_coverage_id,
            earlier_observed_years=earlier_observed_years,
            later_observed_years=later_observed_years,
            deprecation_evidence_anchors=deprecation_evidence_anchors,
            **_synthesis_common(common, "change"),
        )


@dataclass(frozen=True)
class ConflictUnresolved(DerivedRecord):
    kind: str = ""
    subject: str = ""
    alternatives: tuple[str, ...] = ()
    competing_record_ids: tuple[str, ...] = ()
    reconciliation_state: str = "unresolved"
    relevant_scopes: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        subject: str,
        alternatives: tuple[str, ...],
        competing_record_ids: tuple[str, ...],
        reconciliation_state: str,
        relevant_scopes: tuple[str, ...],
        conditions: tuple[str, ...] = (),
        unresolved_questions: tuple[str, ...] = (),
        **common: object,
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
            competing_record_ids=competing_record_ids,
            reconciliation_state=reconciliation_state,
            relevant_scopes=relevant_scopes,
            conditions=conditions,
            unresolved_questions=unresolved_questions,
            **_synthesis_common(common, kind, unresolved=True),
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
    if record.derived_kind not in DERIVED_KINDS:
        raise ValueError("invalid derived_kind")
    _require_text(record.semantic_subtype, "semantic_subtype")
    if record.derived_kind == "source_extracted_claim" and record.evidence_state != "raw_taught":
        raise ValueError("source-extracted derived_kind requires raw-taught evidence")
    if record.derived_kind != "source_extracted_claim" and record.evidence_state != "cross_source_synthesis":
        raise ValueError("synthesis derived_kind requires synthesis evidence")
    if record.derived_kind == "unresolved_or_conflicting" and not isinstance(record, ConflictUnresolved):
        raise ValueError("unresolved derived_kind requires conflict/unresolved semantics")
    _require_auditable_text(record.qualification, "qualification")
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
    if record.record_id != _record_id(record) and not is_legacy_record(record):
        raise ValueError("record identity is not canonical")


def _new(cls: type[DerivedRecord], **values: object) -> DerivedRecord:
    record = cls(record_id="", **values)
    record = replace(record, record_id=_record_id(record))
    validate_record(record)
    return record


def _common(values: dict[str, object], semantic_subtype: str) -> dict[str, object]:
    values = dict(values)
    evidence_state = values.pop("evidence_state", None)
    derived_kind = values.pop("derived_kind", None) or _derived_kind_for_evidence(evidence_state)
    return {
        "derived_kind": derived_kind,
        "semantic_subtype": semantic_subtype,
        "evidence_state": evidence_state or _evidence_for_derived_kind(derived_kind),
        **values,
    }


def _synthesis_common(
    values: dict[str, object], semantic_subtype: str, *, unresolved: bool = False
) -> dict[str, object]:
    values = dict(values)
    values.setdefault(
        "derived_kind", "unresolved_or_conflicting" if unresolved else "cross_source_synthesis"
    )
    values.setdefault("evidence_state", "cross_source_synthesis")
    common = _common(values, semantic_subtype)
    if common["evidence_state"] != "cross_source_synthesis":
        raise ValueError("evolution and conflict records must remain source synthesis or unresolved")
    return common


def _derived_kind_for_evidence(evidence_state: str | None) -> str:
    return "cross_source_synthesis" if evidence_state == "cross_source_synthesis" else "source_extracted_claim"


def _evidence_for_derived_kind(derived_kind: str | None) -> str:
    return "raw_taught" if derived_kind in {None, "source_extracted_claim"} else "cross_source_synthesis"


def _validate_compiler_provenance(provenance: object) -> None:
    if provenance is None:
        return
    if not isinstance(provenance, CompilerProvenance):
        raise ValueError("invalid compiler provenance")
    _require_auditable_text(provenance.model_version, "compiler model version")
    _require_auditable_text(provenance.prompt_version, "compiler prompt version")
    _require_auditable_text(provenance.schema_version, "compiler schema version")


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
        _require_auditable_text(facet.value, "facet value", maximum=MAX_FACET_VALUE_LENGTH)


def _validate_family(record: DerivedRecord) -> None:
    strings: tuple[str, ...]
    if isinstance(record, Claim):
        if record.family != "claim" or record.semantic_subtype not in {"statement", "definition", "recommendation", "strategy_implication"}:
            raise ValueError("invalid claim record")
        strings = (record.subject, record.predicate, record.object)
    elif isinstance(record, Relationship):
        if record.family != "relationship" or record.semantic_subtype != "relation" or record.relation not in RELATIONSHIP_TYPES:
            raise ValueError("invalid relationship record")
        strings = (record.left, record.relation, record.right)
    elif isinstance(record, ProcedureSequenceHierarchy):
        if record.family != "procedure_sequence_hierarchy" or record.semantic_subtype != record.kind or record.kind not in {"procedure", "sequence", "hierarchy"} or len(record.terms) < 2:
            raise ValueError("invalid procedure, sequence, or hierarchy")
        _require_prose_tuple(record.prerequisites, "procedure prerequisite", allow_empty=True)
        _require_prose_tuple(record.conditions, "procedure condition", allow_empty=True)
        if not isinstance(record.branches, tuple) or len(record.branches) > MAX_TERMS:
            raise ValueError("procedure branches must be a bounded tuple")
        branch_values: list[str] = []
        for branch in record.branches:
            if not isinstance(branch, ProcedureRecordBranch):
                raise ValueError("procedure branches must be structured")
            _require_auditable_text(
                branch.condition, "procedure branch condition", maximum=MAX_FACET_VALUE_LENGTH
            )
            _require_prose_tuple(branch.steps, "procedure branch step")
            if len(branch.steps) > MAX_TERMS:
                raise ValueError("too many procedure branch steps")
            branch_values.extend((branch.condition, *branch.steps))
        strings = (*record.terms, *record.prerequisites, *record.conditions, *branch_values)
    elif isinstance(record, Evolution):
        if record.family != "evolution" or record.semantic_subtype != "change":
            raise ValueError("invalid evolution record")
        if is_legacy_record(record):
            strings = (record.subject, record.previous, record.current)
            for value in strings:
                _require_auditable_text(value, "typed record value", maximum=MAX_TYPED_CONTENT_LENGTH)
            return
        if record.evidence_state != "cross_source_synthesis":
            raise ValueError("evolution records must remain source synthesis or unresolved")
        _require_identifier_tuple(record.earlier_source_set, "earlier evolution source set")
        _require_identifier_tuple(record.later_source_set, "later evolution source set")
        _require_text(record.earlier_coverage_id, "earlier coverage ID", maximum=MAX_TYPED_CONTENT_LENGTH)
        _require_text(record.later_coverage_id, "later coverage ID", maximum=MAX_TYPED_CONTENT_LENGTH)
        _require_year_tuple(record.earlier_observed_years, "earlier observed years")
        _require_year_tuple(record.later_observed_years, "later observed years")
        source_revision_dependencies = {
            dependency.identifier for dependency in record.dependencies if dependency.kind == "source_revision"
        }
        if not set(record.earlier_source_set + record.later_source_set) <= source_revision_dependencies:
            raise ValueError("evolution source sets must be source revision dependencies")
        if record.classification not in EVOLUTION_CLASSIFICATIONS:
            raise ValueError("invalid evolution classification")
        if record.negative_evidence_state not in NEGATIVE_EVIDENCE_STATES:
            raise ValueError("invalid negative evidence state")
        if (
            record.classification == "introduced"
            and record.negative_evidence_state != "source_asserted_absence"
        ):
            raise ValueError("introduced classifications require source asserted absence")
        if (
            record.classification in {"repeated", "refined", "expanded", "reframed"}
            and record.negative_evidence_state not in {"positive_teaching", "coverage_supported_synthesis"}
        ):
            raise ValueError("supported evolution classifications require positive or coverage evidence")
        if (
            record.negative_evidence_state == "not_found_in_observed_evidence"
            and record.classification not in {"no_supported_classification", "uncertain_chronology"}
        ):
            raise ValueError("not-found evidence cannot support an evolution classification")
        _require_identifier_tuple(record.competing_anchors, "competing anchor", allow_empty=True)
        if not set(record.competing_anchors) <= set(record.anchors):
            raise ValueError("competing anchors must be supporting anchors")
        _require_identifier_tuple(record.deprecation_evidence_anchors, "deprecation evidence anchor", allow_empty=True)
        if not set(record.deprecation_evidence_anchors) <= set(record.anchors):
            raise ValueError("deprecation evidence anchors must be supporting anchors")
        if record.classification == "deprecated_or_deemphasized":
            if record.negative_evidence_state != "positive_teaching" or not record.deprecation_evidence_anchors:
                raise ValueError("deprecated classifications require direct deprecation evidence")
        _validate_evolution_negative_claim_wording(record)
        strings = (record.subject, record.previous, record.current)
    elif isinstance(record, ConflictUnresolved):
        if record.family != "conflict_unresolved" or record.semantic_subtype != record.kind or record.kind not in {"conflict", "unresolved"} or len(record.alternatives) < 2:
            raise ValueError("invalid conflict or unresolved record")
        if is_legacy_record(record):
            strings = (record.subject, *record.alternatives)
            for value in strings:
                _require_auditable_text(value, "typed record value", maximum=MAX_TYPED_CONTENT_LENGTH)
            return
        if record.evidence_state != "cross_source_synthesis":
            raise ValueError("conflict records must remain source synthesis or unresolved")
        _require_identifier_tuple(record.competing_record_ids, "competing record")
        if not set(record.competing_record_ids) <= {
            dependency.identifier for dependency in record.dependencies if dependency.kind == "derived_record"
        }:
            raise ValueError("competing records must be derived dependencies")
        if record.reconciliation_state not in RECONCILIATION_STATES:
            raise ValueError("invalid conflict reconciliation state")
        if record.kind == "unresolved" and record.reconciliation_state != "unresolved":
            raise ValueError("unresolved records must remain unresolved")
        _require_prose_tuple(record.relevant_scopes, "relevant scope")
        _require_prose_tuple(record.conditions, "condition", allow_empty=True)
        _require_prose_tuple(record.unresolved_questions, "unresolved question", allow_empty=True)
        if record.reconciliation_state == "compatible_under_conditions" and not record.conditions:
            raise ValueError("compatible conflicts require an explicit condition")
        if record.kind == "unresolved" and not record.unresolved_questions:
            raise ValueError("unresolved conflicts require an unresolved question")
        strings = (record.subject, *record.alternatives)
    else:
        raise ValueError("unknown derived record family")
    for value in strings:
        _require_auditable_text(value, "typed record value", maximum=MAX_TYPED_CONTENT_LENGTH)


def _require_text(value: object, label: str, *, maximum: int | None = None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{label} exceeds its maximum length")


def _require_auditable_text(value: object, label: str, *, maximum: int | None = None) -> None:
    _require_text(value, label, maximum=maximum)
    reject_private_or_raw_text(value, label)


def reject_private_or_raw_text(value: str, label: str) -> None:
    tokens = _normalized_tokens(value)
    if (
        "private" in tokens
        or "scratchpad" in tokens
        or "transcript" in tokens
        or "excerpt" in tokens
        or "verbatim" in tokens
        or {"scratch", "pad"} <= tokens
        or {"chain", "thought"} <= tokens
        or {"hidden", "analysis"} <= tokens
        or {"internal", "deliberation"} <= tokens
    ):
        raise ValueError(f"{label} cannot contain private or raw source content")
    if "confidence" in tokens and any(token.isdigit() for token in tokens):
        raise ValueError(f"{label} cannot contain numeric confidence")


def _validate_evolution_negative_claim_wording(record: Evolution) -> None:
    if is_forbidden_negative_wording(record):
        raise ValueError("evolution negative claim wording requires supporting classification and evidence")


def is_forbidden_negative_wording(record: Evolution) -> bool:
    wording_families = _evolution_wording_families(
        (
            record.qualification,
            record.subject,
            record.previous,
            record.current,
            *(facet.value for facet in record.facets),
        )
    )
    if not wording_families:
        return False

    authorized_families: frozenset[str] = frozenset()
    if (
        record.classification == "introduced"
        and record.negative_evidence_state == "source_asserted_absence"
    ):
        authorized_families = frozenset({"introduction", "absence"})
    elif (
        record.classification == "deprecated_or_deemphasized"
        and record.negative_evidence_state == "positive_teaching"
        and record.deprecation_evidence_anchors
    ):
        authorized_families = frozenset({"removal", "deprecation"})
    return not wording_families <= authorized_families


def _evolution_wording_families(values: tuple[str, ...]) -> frozenset[str]:
    tokens: list[str] = []
    compact_terms: set[str] = set()
    for value in values:
        normalized = _normalized_text(value)
        value_tokens = re.findall(r"[a-z0-9]+", normalized)
        tokens.extend(value_tokens)
        compact_terms.update(
            re.sub(r"[^a-z0-9]+", "", match.group(0))
            for match in re.finditer(r"[a-z0-9]+(?:\s*(?:[^\w\s]|_)\s*[a-z0-9]+)+", normalized)
        )
        for start in range(len(value_tokens)):
            for end in range(start + 2, min(start + 16, len(value_tokens)) + 1):
                compact_terms.add("".join(value_tokens[start:end]))

    terms = set(tokens) | compact_terms
    families = set()
    if any(
        term == "never"
        or term in {"new", "newly", "newness", "newer", "newest"}
        or term.startswith("introduc")
        for term in terms
    ):
        families.add("introduction")
    if (
        any(term == "never" or term.startswith("absen") for term in terms)
        or "nottaught" in terms
        or any(first == "not" and second.startswith("taught") for first, second in zip(tokens, tokens[1:]))
    ):
        families.add("absence")
    if any(term.startswith("remov") for term in terms):
        families.add("removal")
    if any(term.startswith("deprecat") or term.startswith("deemphas") for term in terms):
        families.add("deprecation")
    return frozenset(families & EVOLUTION_WORDING_FAMILIES)


def _normalized_tokens(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", _normalized_text(value)))


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _require_identifier_tuple(values: object, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(values, tuple) or (not allow_empty and not values) or len(set(values)) != len(values):
        raise ValueError(f"{label} IDs must be non-empty and unique")
    for value in values:
        _require_text(value, f"{label} ID", maximum=MAX_TYPED_CONTENT_LENGTH)


def _require_prose_tuple(values: object, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(values, tuple) or (not allow_empty and not values) or len(set(values)) != len(values):
        raise ValueError(f"{label} values must be non-empty and unique")
    for value in values:
        _require_auditable_text(value, label, maximum=MAX_TYPED_CONTENT_LENGTH)


def _require_year_tuple(values: object, label: str) -> None:
    if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
        raise ValueError(f"{label} must be a non-empty unique tuple")
    if any(isinstance(year, bool) or not isinstance(year, int) or year < 1 or year > 9999 for year in values):
        raise ValueError(f"{label} must contain calendar years")


def is_legacy_record(record: DerivedRecord) -> bool:
    if isinstance(record, ProcedureSequenceHierarchy):
        return (
            not record.prerequisites
            and not record.conditions
            and not record.branches
            and record.record_id == _pre_round2_procedure_record_id(record)
        )
    if isinstance(record, Evolution):
        return (
            not record.earlier_source_set
            and not record.later_source_set
            and not record.competing_anchors
            and not record.earlier_coverage_id
            and not record.later_coverage_id
            and not record.earlier_observed_years
            and not record.later_observed_years
            and not record.deprecation_evidence_anchors
            and record.record_id == _legacy_record_id(record)
        ) or (
            not record.earlier_coverage_id
            and not record.later_coverage_id
            and not record.earlier_observed_years
            and not record.later_observed_years
            and not record.deprecation_evidence_anchors
            and record.record_id == _first_task9_record_id(record)
        )
    if isinstance(record, ConflictUnresolved):
        return (
            not record.competing_record_ids
            and not record.relevant_scopes
            and not record.conditions
            and not record.unresolved_questions
            and record.record_id == _legacy_record_id(record)
        ) or (
            not record.relevant_scopes
            and not record.conditions
            and not record.unresolved_questions
            and record.record_id == _first_task9_record_id(record)
        )
    return False


def _record_id(record: DerivedRecord) -> str:
    values = asdict(record)
    values["record_id"] = ""
    if values["compiler_provenance"] is None:
        del values["compiler_provenance"]
    return f"rec_{sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"


def _legacy_record_id(record: DerivedRecord) -> str:
    values = asdict(record)
    values["record_id"] = ""
    values["derived_kind"] = values.pop("semantic_subtype")
    if isinstance(record, Evolution):
        for name in (
            "earlier_source_set",
            "later_source_set",
            "classification",
            "negative_evidence_state",
            "competing_anchors",
            "earlier_coverage_id",
            "later_coverage_id",
            "earlier_observed_years",
            "later_observed_years",
            "deprecation_evidence_anchors",
        ):
            values.pop(name)
    elif isinstance(record, ConflictUnresolved):
        for name in (
            "competing_record_ids",
            "reconciliation_state",
            "relevant_scopes",
            "conditions",
            "unresolved_questions",
        ):
            values.pop(name)
    if values["compiler_provenance"] is None:
        del values["compiler_provenance"]
    return f"rec_{sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"


def _first_task9_record_id(record: DerivedRecord) -> str:
    values = asdict(record)
    values["record_id"] = ""
    values["derived_kind"] = values.pop("semantic_subtype")
    if isinstance(record, Evolution):
        for name in (
            "earlier_coverage_id",
            "later_coverage_id",
            "earlier_observed_years",
            "later_observed_years",
            "deprecation_evidence_anchors",
        ):
            values.pop(name)
    elif isinstance(record, ConflictUnresolved):
        for name in ("relevant_scopes", "conditions", "unresolved_questions"):
            values.pop(name)
    if values["compiler_provenance"] is None:
        del values["compiler_provenance"]
    return f"rec_{sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"


def _pre_round2_procedure_record_id(record: ProcedureSequenceHierarchy) -> str:
    values = asdict(record)
    values["record_id"] = ""
    for name in ("prerequisites", "conditions", "branches"):
        values.pop(name)
    if values["compiler_provenance"] is None:
        del values["compiler_provenance"]
    return f"rec_{sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"
