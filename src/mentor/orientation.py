"""Bounded, derived-only orientation for the currently published snapshot."""

from dataclasses import asdict, dataclass
import json
from typing import Any, Iterable, Sequence

from mentor.derived_records import (
    Claim,
    ConflictUnresolved,
    DerivedRecord,
    Evolution,
    ProcedureSequenceHierarchy,
    Relationship,
    input_record_ids,
    reject_private_or_raw_text,
    source_revision_ids,
    validate_record,
)
from mentor.vector_stores import VectorStoreSearchResult


@dataclass(frozen=True)
class OrientationConceptOccurrence:
    """Display-safe concept usage; opaque concept/record identities stay server-owned."""

    role: str
    position: int | None
    label: str


@dataclass(frozen=True)
class OrientationConceptSummary:
    """Bounded semantic orientation without raw spans or private identifiers."""

    canonical_label: str
    aliases: tuple[str, ...]
    scope: str | None
    supporting_record_count: int
    supporting_anchor_count: int
    occurrences: tuple[OrientationConceptOccurrence, ...]


@dataclass(frozen=True)
class OrientationBudget:
    max_records: int
    max_tokens: int

    def __post_init__(self) -> None:
        if self.max_records < 1 or self.max_tokens < 1:
            raise ValueError("orientation budgets must be positive")


@dataclass(frozen=True)
class OrientationSourceArea:
    collection_id: str | None
    year: int | None
    scope: str | None


@dataclass(frozen=True)
class OrientationRecord:
    record_id: str
    concept_id: str | None
    family: str
    derived_kind: str
    evidence_state: str
    qualification: str
    statement: str
    anchor_ids: tuple[str, ...]
    source_area: OrientationSourceArea
    input_record_ids: tuple[str, ...] = ()
    source_revision_ids: tuple[str, ...] = ()
    semantic_subtype: str = "unspecified"
    concept_ids: tuple[str, ...] = ()
    concepts: tuple[OrientationConceptSummary, ...] = ()


@dataclass(frozen=True)
class OrientationResult:
    snapshot_id: str | None
    snapshot_schema_version: str | None
    records: tuple[OrientationRecord, ...]
    used_tokens: int
    budget: OrientationBudget
    truncated: bool
    duplicate_result_count: int
    discarded_result_count: int

    @property
    def record_count(self) -> int:
        return len(self.records)


class OrientationService:
    """Search derived artifacts, then admit only current validated local records."""

    def __init__(self, storage: Any, vector_stores: Any, *, budget: OrientationBudget):
        self._storage = storage
        self._vector_stores = vector_stores
        self._budget = budget

    def consult(
        self,
        question: str,
        *,
        snapshot: Any | None = None,
        collection_id: str | None = None,
        year: int | None = None,
        scope: str | None = None,
    ) -> OrientationResult:
        _require_question(question)
        attributes = {"snapshot_id": "", "status": "published"}
        snapshot = self._storage.current_snapshot() if snapshot is None else snapshot
        if snapshot is None or snapshot.status != "published" or not isinstance(snapshot.derived_store_id, str):
            return OrientationResult(None, None, (), 0, self._budget, False, 0, 0)

        collection_id = self._snapshot_collection_scope(snapshot, collection_id)
        _add_scope(attributes, collection_id=collection_id, year=year, scope=scope)
        attributes["snapshot_id"] = snapshot.snapshot_id
        local_records, invalid_local_ids = self._current_local_records(snapshot.snapshot_id)
        local_concept_ids = self._storage.orientation_concept_ids(snapshot.snapshot_id)
        links_reader = getattr(self._storage, "orientation_concept_links", None)
        local_concept_links = (
            links_reader(snapshot.snapshot_id)
            if callable(links_reader)
            else {record_id: (concept_id,) for record_id, concept_id in local_concept_ids.items()}
        )
        concepts_reader = getattr(self._storage, "orientation_concepts", None)
        occurrences_reader = getattr(self._storage, "orientation_concept_occurrences", None)
        local_concepts = tuple(concepts_reader(snapshot.snapshot_id)) if callable(concepts_reader) else ()
        local_occurrences = (
            tuple(occurrences_reader(snapshot.snapshot_id)) if callable(occurrences_reader) else ()
        )
        remote_results = self._vector_stores.search(
            snapshot.derived_store_id,
            question,
            attributes=attributes,
            max_num_results=min(50, self._budget.max_records * 4),
        )
        return self._bound_results(
            snapshot, local_records, invalid_local_ids, local_concept_ids,
            local_concept_links, local_concepts, local_occurrences, remote_results,
        )

    def _snapshot_collection_scope(self, snapshot: Any, collection_id: str | None) -> str | None:
        reader = getattr(self._storage, "snapshot_collection_ids", None)
        allowed = reader(snapshot.snapshot_id) if callable(reader) else None
        if allowed is None:
            return collection_id
        if collection_id is not None:
            if not isinstance(collection_id, str) or not collection_id.strip() or collection_id not in allowed:
                raise ValueError("orientation collection scope is not owned by the resolved snapshot")
            return collection_id
        return allowed[0] if len(allowed) == 1 else None

    def _current_local_records(self, snapshot_id: str) -> tuple[dict[str, DerivedRecord], set[str]]:
        records: dict[str, DerivedRecord] = {}
        invalid_ids: set[str] = set()
        for record in self._storage.derived_records(snapshot_id):
            record_id = getattr(record, "record_id", None)
            try:
                if not isinstance(record, DerivedRecord):
                    raise ValueError("not a derived record")
                validate_record(record)
                if (
                    record.snapshot_id != snapshot_id
                    or record.validation_state != "validated"
                    or record.lifecycle_state != "active"
                ):
                    raise ValueError("record is not active in the current snapshot")
            except (TypeError, ValueError):
                if isinstance(record_id, str):
                    invalid_ids.add(record_id)
                continue
            records[record.record_id] = record
        return records, invalid_ids

    def _bound_results(
        self,
        snapshot: Any,
        local_records: dict[str, DerivedRecord],
        invalid_local_ids: set[str],
        local_concept_ids: dict[str, str],
        local_concept_links: dict[str, tuple[str, ...]],
        local_concepts: tuple[Any, ...],
        local_occurrences: tuple[Any, ...],
        remote_results: list[VectorStoreSearchResult],
    ) -> OrientationResult:
        records: list[OrientationRecord] = []
        seen_record_ids: set[str] = set()
        used_tokens = duplicate_count = discarded_count = 0
        truncated = False
        for remote in remote_results:
            record, concept_id, concept_ids = self._valid_remote_record(
                remote, snapshot.snapshot_id, local_records, invalid_local_ids,
                local_concept_ids, local_concept_links,
            )
            if record is None or concept_id is None or concept_ids is None:
                discarded_count += 1
                continue
            if record.record_id in seen_record_ids:
                duplicate_count += 1
                continue
            seen_record_ids.add(record.record_id)
            orientation_record = _orientation_record(
                record,
                concept_id,
                self._storage.orientation_source_area(snapshot.snapshot_id, record),
                concept_ids=concept_ids,
                concepts=_concept_summaries(
                    local_concepts,
                    local_occurrences,
                    concept_ids,
                    record_id=record.record_id,
                ),
            )
            record_tokens = _conservative_token_upper_bound(orientation_record)
            if len(records) >= self._budget.max_records or used_tokens + record_tokens > self._budget.max_tokens:
                truncated = True
                continue
            records.append(orientation_record)
            used_tokens += record_tokens
        return OrientationResult(
            snapshot.snapshot_id,
            _optional_identifier(getattr(snapshot, "schema_version", None)),
            tuple(records),
            used_tokens,
            self._budget,
            truncated,
            duplicate_count,
            discarded_count,
        )

    @staticmethod
    def _valid_remote_record(
        remote: VectorStoreSearchResult,
        snapshot_id: str,
        local_records: dict[str, DerivedRecord],
        invalid_local_ids: set[str],
        local_concept_ids: dict[str, str],
        local_concept_links: dict[str, tuple[str, ...]],
    ) -> tuple[DerivedRecord | None, str | None, tuple[str, ...] | None]:
        if not isinstance(remote, VectorStoreSearchResult):
            return None, None, None
        if remote.attributes.get("snapshot_id") != snapshot_id or remote.attributes.get("status") != "published":
            return None, None, None
        record_id = remote.record_id
        if record_id is None or record_id in invalid_local_ids:
            return None, None, None
        record = local_records.get(record_id)
        concept_id = local_concept_ids.get(record_id)
        concept_ids = local_concept_links.get(record_id, ())
        if (
            record is None or not _is_canonical_concept_id(concept_id)
            or not concept_ids or concept_id not in concept_ids
            or any(not _is_canonical_concept_id(value) for value in concept_ids)
        ):
            return None, None, None
        return record, concept_id, concept_ids


def render_orientation_artifact(
    record: DerivedRecord,
    concept_id: str,
    source_area: tuple[str | None, int | None, str | None],
    *,
    max_bytes: int,
    concepts: tuple[OrientationConceptSummary, ...] = (),
) -> str:
    """Render one compact derived record; raw transcript text is never an input."""
    validate_record(record)
    if record.validation_state != "validated" or record.lifecycle_state != "active":
        raise ValueError("orientation artifacts require validated active records")
    if not _is_canonical_concept_id(concept_id):
        raise ValueError("orientation artifacts require a canonical concept ID")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise ValueError("orientation artifact budget must be positive")
    orientation = _orientation_record(record, concept_id, source_area, concepts=concepts)
    # The vector artifact is semantic navigation context, not an identity or
    # citation payload. Opaque IDs and raw-source anchors remain local-only.
    payload = {
        "family": orientation.family,
        "derived_kind": orientation.derived_kind,
        "semantic_subtype": orientation.semantic_subtype,
        "evidence_state": orientation.evidence_state,
        "qualification": orientation.qualification,
        "statement": orientation.statement,
        "source_area": asdict(orientation.source_area),
        "concepts": [asdict(concept) for concept in orientation.concepts],
    }
    content = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(content.encode("utf-8")) > max_bytes:
        raise ValueError("orientation artifact exceeds its byte budget")
    return content


def _add_scope(
    attributes: dict[str, str | int],
    *,
    collection_id: str | None,
    year: int | None,
    scope: str | None,
) -> None:
    for key, value in (("collection_id", collection_id), ("scope", scope)):
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be non-empty text")
            attributes[key] = value
    if year is not None:
        if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
            raise ValueError("year must be a calendar year")
        attributes["year"] = year


def _require_question(question: str) -> None:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("orientation question must be non-empty text")


def _orientation_record(
    record: DerivedRecord,
    concept_id: str,
    source_area: tuple[str | None, int | None, str | None],
    *,
    concept_ids: tuple[str, ...] | None = None,
    concepts: tuple[OrientationConceptSummary, ...] = (),
) -> OrientationRecord:
    concept_ids = concept_ids or (concept_id,)
    return OrientationRecord(
        record_id=record.record_id,
        concept_id=concept_id,
        family=record.family,
        derived_kind=record.derived_kind,
        semantic_subtype=record.semantic_subtype,
        evidence_state=record.evidence_state,
        qualification=record.qualification,
        statement=_statement(record),
        anchor_ids=record.anchors,
        input_record_ids=input_record_ids(record),
        source_revision_ids=source_revision_ids(record),
        source_area=OrientationSourceArea(*_safe_source_area(source_area)),
        concept_ids=concept_ids,
        concepts=concepts,
    )


def concept_summaries(
    concepts: Sequence[Any],
    occurrences: Sequence[Any],
    concept_ids: Iterable[str],
    *,
    record_id: str | None = None,
) -> tuple[OrientationConceptSummary, ...]:
    """Create safe, identity-free summaries from validated local concept rows."""
    return _concept_summaries(concepts, occurrences, tuple(concept_ids), record_id=record_id)


def _concept_summaries(
    concepts: Sequence[Any],
    occurrences: Sequence[Any],
    concept_ids: tuple[str, ...],
    *,
    record_id: str | None,
) -> tuple[OrientationConceptSummary, ...]:
    wanted = set(concept_ids)
    occurrences_by_concept: dict[str, list[Any]] = {}
    for occurrence in occurrences:
        occurrence_concept_id = getattr(occurrence, "concept_id", None)
        if occurrence_concept_id not in wanted:
            continue
        if record_id is not None and getattr(occurrence, "record_id", None) != record_id:
            continue
        occurrences_by_concept.setdefault(occurrence_concept_id, []).append(occurrence)
    result: list[OrientationConceptSummary] = []
    for concept in concepts:
        if getattr(concept, "concept_id", None) not in wanted:
            continue
        canonical_label = _safe_concept_text(getattr(concept, "canonical_label", None), 240)
        if canonical_label is None:
            continue
        aliases = tuple(
            alias
            for value in getattr(concept, "aliases", ())
            if (alias := _safe_concept_text(value, 240)) is not None
        )
        scope = _safe_concept_text(getattr(concept, "scope", None), 160)
        concept_occurrences = []
        for occurrence in occurrences_by_concept.get(concept.concept_id, ()):
            role = _safe_concept_text(getattr(occurrence, "role", None), 120)
            label = _safe_concept_text(getattr(occurrence, "label_key", None), 240)
            position = getattr(occurrence, "position", None)
            if role is None or label is None or (
                position is not None
                and (isinstance(position, bool) or not isinstance(position, int) or position < 0)
            ):
                continue
            concept_occurrences.append(OrientationConceptOccurrence(role, position, label))
        result.append(
            OrientationConceptSummary(
                canonical_label,
                aliases,
                scope,
                len(tuple(getattr(concept, "supporting_record_ids", ()))),
                len(tuple(getattr(concept, "supporting_anchor_ids", ()))),
                tuple(concept_occurrences),
            )
        )
    return tuple(result)


def _safe_concept_text(value: object, maximum: int) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return None
    try:
        reject_private_or_raw_text(value, "orientation concept")
    except ValueError:
        return None
    return value


def _statement(record: DerivedRecord) -> str:
    if isinstance(record, Claim):
        return f"{record.subject} {record.predicate} {record.object}"
    if isinstance(record, Relationship):
        return f"{record.left} {record.relation} {record.right}"
    if isinstance(record, ProcedureSequenceHierarchy):
        return f"{record.kind}: {' -> '.join(record.terms)}"
    if isinstance(record, Evolution):
        return f"{record.subject}: {record.previous} -> {record.current}"
    if isinstance(record, ConflictUnresolved):
        return f"{record.subject}: {' / '.join(record.alternatives)}"
    raise ValueError("unknown derived record family")


def _safe_metadata_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        return None
    try:
        reject_private_or_raw_text(value, "orientation metadata")
    except ValueError:
        return None
    return value


def _safe_year(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 9999 else None


def _optional_identifier(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _is_canonical_concept_id(value: object) -> bool:
    return isinstance(value, str) and len(value) == 68 and value.startswith("con_") and all(
        character in "0123456789abcdef" for character in value[4:]
    )


def _safe_source_area(
    source_area: object,
) -> tuple[str | None, int | None, str | None]:
    if not isinstance(source_area, tuple) or len(source_area) != 3:
        return None, None, None
    return _safe_metadata_text(source_area[0]), _safe_year(source_area[1]), _safe_metadata_text(source_area[2])


def _conservative_token_upper_bound(record: OrientationRecord) -> int:
    """UTF-8 bytes conservatively bound tokens without a local tokenizer dependency."""
    fields = (
        record.record_id,
        record.concept_id or "",
        record.family,
        record.derived_kind,
        record.semantic_subtype,
        record.evidence_state,
        record.qualification,
        record.statement,
        *record.anchor_ids,
        *record.input_record_ids,
        *record.source_revision_ids,
        *record.concept_ids,
        record.source_area.collection_id or "",
        str(record.source_area.year or ""),
        record.source_area.scope or "",
        *(
            value
            for concept in record.concepts
            for value in (
                concept.canonical_label,
                *concept.aliases,
                concept.scope or "",
                str(concept.supporting_record_count),
                str(concept.supporting_anchor_count),
                *(
                    f"{occurrence.role}:{occurrence.position}:{occurrence.label}"
                    for occurrence in concept.occurrences
                ),
            )
        ),
    )
    return len("\n".join(fields).encode("utf-8"))
