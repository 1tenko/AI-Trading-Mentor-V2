"""Bounded, derived-only orientation for the currently published snapshot."""

from dataclasses import dataclass
from typing import Any

from mentor.derived_records import (
    Claim,
    ConflictUnresolved,
    DerivedRecord,
    Evolution,
    ProcedureSequenceHierarchy,
    Relationship,
    reject_private_or_raw_text,
    validate_record,
)
from mentor.vector_stores import VectorStoreSearchResult


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
        collection_id: str | None = None,
        year: int | None = None,
        scope: str | None = None,
    ) -> OrientationResult:
        _require_question(question)
        attributes = {"snapshot_id": "", "status": "published"}
        _add_scope(attributes, collection_id=collection_id, year=year, scope=scope)
        snapshot = self._storage.current_snapshot()
        if snapshot is None or snapshot.status != "published" or not isinstance(snapshot.derived_store_id, str):
            return OrientationResult(None, None, (), 0, self._budget, False, 0, 0)

        attributes["snapshot_id"] = snapshot.snapshot_id
        local_records, invalid_local_ids = self._current_local_records(snapshot.snapshot_id)
        local_concept_ids = self._storage.orientation_concept_ids(snapshot.snapshot_id)
        remote_results = self._vector_stores.search(
            snapshot.derived_store_id,
            question,
            attributes=attributes,
            max_num_results=min(50, self._budget.max_records * 4),
        )
        return self._bound_results(snapshot, local_records, invalid_local_ids, local_concept_ids, remote_results)

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
        remote_results: list[VectorStoreSearchResult],
    ) -> OrientationResult:
        records: list[OrientationRecord] = []
        seen_record_ids: set[str] = set()
        seen_concept_ids: set[str] = set()
        used_tokens = duplicate_count = discarded_count = 0
        truncated = False
        for remote in remote_results:
            record, concept_id = self._valid_remote_record(
                remote, snapshot.snapshot_id, local_records, invalid_local_ids, local_concept_ids
            )
            if record is None or concept_id is None:
                discarded_count += 1
                continue
            if record.record_id in seen_record_ids or concept_id in seen_concept_ids:
                duplicate_count += 1
                continue
            seen_record_ids.add(record.record_id)
            seen_concept_ids.add(concept_id)
            orientation_record = _orientation_record(
                record,
                concept_id,
                self._storage.orientation_source_area(snapshot.snapshot_id, record),
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
    ) -> tuple[DerivedRecord | None, str | None]:
        if not isinstance(remote, VectorStoreSearchResult):
            return None, None
        if remote.attributes.get("snapshot_id") != snapshot_id or remote.attributes.get("status") != "published":
            return None, None
        record_id = remote.record_id
        if record_id is None or record_id in invalid_local_ids:
            return None, None
        record = local_records.get(record_id)
        concept_id = local_concept_ids.get(record_id)
        if record is None or not _is_canonical_concept_id(concept_id):
            return None, None
        return record, concept_id


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
) -> OrientationRecord:
    return OrientationRecord(
        record_id=record.record_id,
        concept_id=concept_id,
        family=record.family,
        derived_kind=record.derived_kind,
        evidence_state=record.evidence_state,
        qualification=record.qualification,
        statement=_statement(record),
        anchor_ids=record.anchors,
        source_area=OrientationSourceArea(*_safe_source_area(source_area)),
    )


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
        record.evidence_state,
        record.qualification,
        record.statement,
        *record.anchor_ids,
        record.source_area.collection_id or "",
        str(record.source_area.year or ""),
        record.source_area.scope or "",
    )
    return len("\n".join(fields).encode("utf-8"))
