"""Explicit, budgeted Gate 1 runner for the isolated six-source pilot."""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import math
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

from mentor.candidate_compiler import (
    ArtifactScope,
    BuildRequest,
    CandidateCompiler,
    CandidateSource,
    CandidateSourcePreparer,
    RemoteArtifactRetention,
)
from mentor.chat_service import (
    ChatService,
    EvaluationConfig,
    FILE_SEARCH_CALL_COST_USD,
    _effective_research_depth,
    _should_orient,
)
from mentor.compilation import CompilationRun, TokenPricing, usage_from_response
from mentor.compiler import SourceExtractor
from mentor.compiler_prompts import (
    EXTRACTION_INITIAL_MAX_OUTPUT_TOKENS,
    EXTRACTION_MAX_ATTEMPTS,
    EXTRACTION_RETRY_MAX_OUTPUT_TOKENS,
    MAX_CANDIDATES_PER_SOURCE,
)
from mentor.config import load_config
from mentor.evaluation import EvaluationCase, PilotManifest, PilotManifestEntry, PilotRuntime
from mentor.orientation import OrientationBudget
from mentor.storage import Storage
from mentor.synthesis import SynthesisReconciler
from mentor.vector_stores import (
    FileExpiration,
    UploadedFile,
    VectorStore,
    VectorStoreAdapter,
    VectorStoreExpiration,
)
from mentor.structured_response import private_response_diagnostic


GATE1_MODEL = "gpt-5.6-sol"
GATE1_PRICING_CHECKED_ON = date(2026, 8, 24)
GATE1_PRICING_SOURCE = "https://platform.openai.com/pricing"
APPROVED_GATE1_MANIFEST_SHA256 = "3798d537cd486f782449d9833b5fc06dd28fa93aed4564cdd742e66225d15d38"
# Standard short-context rates are deliberately higher than the current model-page promotion.
CONSERVATIVE_SOL_PRICING = TokenPricing(5.0, 30.0, 30.0)
HARD_SPEND_CEILING_USD = 30.0
_MONEY_QUANTUM = Decimal("0.000000001")
_PRICING_MAX_AGE_DAYS = 7
_MAX_OUTPUT_TOKENS_BY_STAGE = {
    "extraction": EXTRACTION_RETRY_MAX_OUTPUT_TOKENS,
    "validation": 1_000,
    "synthesis": 10_000,
    "mentor_evaluation": 4_000,
}
_MAX_SYNTHESIS_CALLS = 6
_FILE_SEARCH_INPUT_RESERVE_TOKENS = 50_000
_FILE_SEARCH_CALL_RESERVE = 8
_BYTES_PER_GIB = 2**30
FILE_SEARCH_STORAGE_USD_PER_GIB_DAY = 0.10
PILOT_REMOTE_RETENTION = RemoteArtifactRetention(
    VectorStoreExpiration("last_active_at", 1), FileExpiration("created_at", 86_400)
)

GATE1_EVALUATION_CASES = (
    EvaluationCase(
        "broad-integration",
        "broad_integration",
        "Explain how the major parts of Jacob's system fit together.",
    ),
    EvaluationCase(
        "procedure",
        "procedure",
        "Walk me through the reversal model sequence and explain why each stage matters.",
    ),
    EvaluationCase(
        "conditions",
        "conditions",
        "What makes a setup or scenario lower probability according to Jacob?",
    ),
    EvaluationCase(
        "cross-year",
        "cross_year_synthesis",
        "Compare Jacob's 2025 and 2026 Asset Synchronization teaching. What actually changed versus what was retained?",
    ),
    EvaluationCase(
        "relationships",
        "relationship_heavy",
        "How do Asset Synchronization, reversal sequencing, and lower-probability conditions interact inside Jacob's system?",
    ),
    EvaluationCase(
        "false-attribution",
        "source_discipline",
        "Jacob teaches that every lower-probability scenario must always be ignored. Explain why that is his rule.",
    ),
)


@dataclass(frozen=True)
class StageEstimate:
    stage: str
    call_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class Gate1CostPlan:
    source_count: int
    source_bytes: int
    estimated_candidate_count: int
    stages: tuple[StageEstimate, ...]
    estimated_upper_bound_usd: float

    def cost_for(self, *stages: str) -> float:
        selected = set(stages)
        return sum(stage.cost_usd for stage in self.stages if stage.stage in selected)


@dataclass(frozen=True)
class Gate1RunReport:
    executed: bool
    published: bool
    run_id: str
    source_count: int
    revision_ids: tuple[str, ...]
    estimated_upper_bound_usd: float
    spent_usd: float
    remaining_usd: float
    pilot_directory: Path | None = None
    output_path: Path | None = None


@dataclass(frozen=True)
class _ProductionPointers:
    current_snapshot_id: str | None
    raw_store_id: str | None
    derived_store_id: str | None


@dataclass(frozen=True)
class CumulativeSpendState:
    actual_incurred_usd: Decimal
    reserved_pending_usd: Decimal
    bounded_storage_exposure_usd: Decimal
    unknown_liability_usd: Decimal
    entry_count: int

    @property
    def spent_usd(self) -> Decimal:
        """Compatibility name: strictly settled/actual cost only."""
        return self.actual_incurred_usd

    @property
    def effective_budget_exposure_usd(self) -> Decimal:
        return (
            self.actual_incurred_usd
            + self.reserved_pending_usd
            + self.bounded_storage_exposure_usd
            + self.unknown_liability_usd
        )

    def remaining_usd(self, ceiling_usd: float | Decimal) -> Decimal:
        return _money(ceiling_usd) - self.effective_budget_exposure_usd


class CumulativeSpendLedger:
    """Private, fail-closed cumulative Gate 1 accounting.

    Historical run reports are immutable evidence.  This compact journal imports
    each report once and durably reserves every future paid operation before it
    is sent, so a later run never starts below auditable incurred spend.
    """

    _SCHEMA_VERSION = "gate1-cumulative-spend-v1"
    _COUNTED_STATUSES = {"settled", "usage_unknown", "storage_exposure"}
    _LEGACY_STORAGE_STATUS = "projected_maximum"

    def __init__(self, path: Path, pilot_root: Path):
        self._path = Path(path)
        self._pilot_root = Path(pilot_root)
        self._entries = self._load()

    def reconcile(self) -> CumulativeSpendState:
        entries_by_id = {entry["entry_id"]: entry for entry in self._entries}
        if len(entries_by_id) != len(self._entries):
            raise ValueError("duplicate cumulative spend entry")
        entries_by_run: dict[str, list[dict[str, Any]]] = {}
        for entry in self._entries:
            run_id = entry.get("run_id")
            if isinstance(run_id, str):
                entries_by_run.setdefault(run_id, []).append(entry)
        changed = False
        for result_path in sorted(self._pilot_root.glob("gate1-*/outputs/gate1-result.json")):
            payload = _read_private_json(result_path)
            run_id = payload.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise ValueError(f"historical Gate 1 result lacks run_id: {result_path}")
            audited_cost = _historical_run_cost(payload, result_path)
            existing = entries_by_run.get(run_id, [])
            existing_cost = _state_from_entries(existing)
            components = (
                ("actual", "settled", "incurred_usd", audited_cost.actual_incurred_usd),
                ("storage", "storage_exposure", "exposure_usd", audited_cost.bounded_storage_exposure_usd),
                ("unknown", "usage_unknown", "incurred_usd", audited_cost.unknown_liability_usd),
            )
            for suffix, status, field, expected in components:
                observed = {
                    "actual": existing_cost.actual_incurred_usd,
                    "storage": existing_cost.bounded_storage_exposure_usd,
                    "unknown": existing_cost.unknown_liability_usd,
                }[suffix]
                if observed > expected:
                    raise ValueError(f"cumulative spend disagrees with historical result for {run_id}")
                if observed == expected:
                    continue
                entry_id = f"historical:{run_id}:{suffix}"
                if entry_id in entries_by_id:
                    raise ValueError("duplicate cumulative spend entry")
                entry = {
                    "entry_id": entry_id,
                    "kind": "historical_run",
                    "run_id": run_id,
                    "stage": "remote_storage" if suffix == "storage" else "historical",
                    "status": status,
                    field: _money_text(expected - observed),
                }
                self._entries.append(entry)
                entries_by_id[entry_id] = entry
                entries_by_run.setdefault(run_id, []).append(entry)
                changed = True
        for probe_path in sorted(self._pilot_root.glob("response-envelope-probe-*/probe-summary.json")):
            payload = _read_private_json(probe_path)
            cost = payload.get("probe_cost_usd")
            if payload.get("status") != "completed":
                raise ValueError(f"historical Gate 1 probe is not completed: {probe_path}")
            try:
                amount = _money(cost)
            except ValueError as error:
                raise ValueError(f"historical Gate 1 probe has no auditable cost: {probe_path}") from error
            entry_id = f"probe:{probe_path.parent.name}"
            existing = entries_by_id.get(entry_id)
            if existing is not None:
                if _entry_cost(existing) != amount:
                    raise ValueError(f"cumulative spend disagrees with historical probe: {probe_path}")
                continue
            entry = {
                "entry_id": entry_id,
                "kind": "probe",
                "status": "settled",
                "incurred_usd": _money_text(amount),
            }
            self._entries.append(entry)
            entries_by_id[entry_id] = entry
            changed = True
        state = self._state()
        if state.effective_budget_exposure_usd > _money(HARD_SPEND_CEILING_USD):
            raise ValueError("auditable Gate 1 spend exceeds the hard ceiling")
        if changed or not self._path.exists():
            self._write()
        return state

    def reserve(self, *, run_id: str, operation_id: int, stage: str, cost_usd: float) -> None:
        self._append(
            {
                "entry_id": f"operation:{run_id}:{operation_id}",
                "kind": "operation",
                "run_id": run_id,
                "stage": stage,
                "status": "reserved",
                "reserved_usd": _money_text(cost_usd),
            }
        )

    def settle(
        self, *, run_id: str, operation_id: int, status: str, incurred_usd: float
    ) -> None:
        if status not in self._COUNTED_STATUSES:
            raise ValueError("invalid cumulative spend settlement status")
        entry_id = f"operation:{run_id}:{operation_id}"
        for index, entry in enumerate(self._entries):
            if entry["entry_id"] == entry_id:
                if entry.get("status") != "reserved":
                    raise ValueError("cumulative spend operation is not reserved")
                replacement = dict(entry)
                replacement["status"] = status
                replacement["incurred_usd"] = _money_text(incurred_usd)
                self._entries[index] = replacement
                self._write()
                return
        raise ValueError("cumulative spend reservation is missing")

    def record_projected_maximum(self, *, run_id: str, operation_id: int, stage: str, cost_usd: float) -> None:
        self._append(
            {
                "entry_id": f"operation:{run_id}:fixed:{operation_id}",
                "kind": "operation",
                "run_id": run_id,
                "stage": stage,
                "status": "storage_exposure",
                "exposure_usd": _money_text(cost_usd),
            }
        )

    def _append(self, entry: dict[str, Any]) -> None:
        if any(existing["entry_id"] == entry["entry_id"] for existing in self._entries):
            raise ValueError("duplicate cumulative spend entry")
        self._entries.append(entry)
        self._write()

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        payload = _read_private_json(self._path)
        if payload.get("schema_version") != self._SCHEMA_VERSION:
            raise ValueError("unrecognized cumulative spend ledger schema")
        entries = payload.get("entries")
        if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("invalid cumulative spend ledger entries")
        return [dict(entry) for entry in entries]

    def _state(self) -> CumulativeSpendState:
        return _state_from_entries(self._entries)

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"schema_version": self._SCHEMA_VERSION, "entries": self._entries},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(self._path)


def _read_private_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unreadable private Gate 1 accounting: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"invalid private Gate 1 accounting: {path}")
    return payload


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("invalid Gate 1 money value") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid Gate 1 money value")
    return amount


def _money_text(value: Any) -> str:
    return format(_money(value), "f")


def _historical_run_cost(payload: dict[str, Any], path: Path) -> CumulativeSpendState:
    spend = payload.get("spend")
    accounting = spend if isinstance(spend, dict) else payload
    events = accounting.get("events")
    if not isinstance(events, list):
        raise ValueError(f"historical Gate 1 result has no auditable cost: {path}")
    actual = Decimal()
    storage = Decimal()
    unknown = Decimal()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError(f"historical Gate 1 event is invalid: {path}")
        status = event.get("status")
        if status == "reserved":
            continue
        if status == CumulativeSpendLedger._LEGACY_STORAGE_STATUS:
            if event.get("stage") != "remote_storage":
                raise ValueError(f"ambiguous legacy storage accounting event: {path}")
            status = "storage_exposure"
        if status not in CumulativeSpendLedger._COUNTED_STATUSES:
            raise ValueError(f"historical Gate 1 event has unknown accounting status: {path}")
        cost = event.get("cost_usd")
        try:
            amount = _money(cost)
        except ValueError as error:
            raise ValueError(f"historical Gate 1 event has invalid cost: {path}") from error
        if status == "usage_unknown":
            unknown += amount
        elif status == "storage_exposure":
            storage += amount
        else:
            actual += amount
    state = CumulativeSpendState(actual, Decimal(), storage, unknown, 0)
    reported_total = accounting.get("spent_usd")
    reported_prior = accounting.get("prior_spend_usd")
    if reported_total is not None:
        prior = Decimal() if reported_prior is None else _money(reported_prior)
        if _money(reported_total) - prior != state.effective_budget_exposure_usd:
            raise ValueError(f"historical Gate 1 result total disagrees with operation events: {path}")
    elif reported_prior is not None:
        raise ValueError(f"historical Gate 1 result has incomplete cumulative total: {path}")
    return state


def _entry_cost(entry: dict[str, Any]) -> Decimal:
    status = entry.get("status")
    field = (
        "reserved_usd" if status == "reserved" else
        "exposure_usd" if status == "storage_exposure" else
        "incurred_usd"
    )
    value = entry.get(field)
    if (
        status not in {"reserved", *CumulativeSpendLedger._COUNTED_STATUSES}
        or isinstance(value, bool)
    ):
        raise ValueError("invalid cumulative spend entry")
    return _money(value)


def _state_from_entries(entries: list[dict[str, Any]]) -> CumulativeSpendState:
    actual = Decimal()
    reserved = Decimal()
    storage = Decimal()
    unknown = Decimal()
    for entry in entries:
        status = entry.get("status")
        amount = _entry_cost(entry)
        if status == "reserved":
            reserved += amount
        elif status == "storage_exposure":
            storage += amount
        elif status == "usage_unknown":
            unknown += amount
        else:
            actual += amount
    return CumulativeSpendState(actual, reserved, storage, unknown, len(entries))


def _state_amounts(state: CumulativeSpendState) -> tuple[Decimal, Decimal, Decimal]:
    return (
        state.actual_incurred_usd,
        state.reserved_pending_usd,
        state.bounded_storage_exposure_usd,
        state.unknown_liability_usd,
    )


class SpendLedger:
    """Sequential hard ceiling checked before every paid Responses call."""

    def __init__(
        self,
        limit_usd: float,
        pricing: TokenPricing,
        *,
        prior_spend_usd: float = 0.0,
        accounting: CumulativeSpendLedger | None = None,
        run_id: str | None = None,
    ):
        if (
            isinstance(limit_usd, bool)
            or not isinstance(limit_usd, int | float)
            or not math.isfinite(limit_usd)
            or not 0 < limit_usd <= HARD_SPEND_CEILING_USD
        ):
            raise ValueError("Gate 1 spend limit must be positive and no more than $30")
        if (
            isinstance(prior_spend_usd, bool)
            or not isinstance(prior_spend_usd, int | float | Decimal)
            or _money(prior_spend_usd) > _money(limit_usd)
        ):
            raise ValueError("prior Gate 1 spend must be finite and within the cumulative ceiling")
        pricing.require_complete("Gate 1")
        self.limit_usd = _money(limit_usd)
        self.pricing = pricing
        self.prior_spend_usd = _money(prior_spend_usd)
        self.spent_usd = self.prior_spend_usd
        if (accounting is None) != (run_id is None):
            raise ValueError("durable accounting requires both ledger and run ID")
        self._accounting = accounting
        self._run_id = run_id
        self._reservations: dict[int, tuple[str, Decimal]] = {}
        self._next_ticket = 1
        self.events: list[dict[str, Any]] = []

    def ensure(self, stage: str, projected_cost_usd: float | Decimal) -> None:
        projected_cost_usd = _money(projected_cost_usd)
        reserved = sum(value for _stage, value in self._reservations.values())
        if self.spent_usd + reserved + projected_cost_usd > self.limit_usd:
            raise RuntimeError(
                f"Gate 1 spend ceiling blocks {stage}: spent ${self.spent_usd:.4f}, "
                f"projected additional ${projected_cost_usd:.4f}, limit ${self.limit_usd:.2f}"
            )

    def reserve(self, stage: str, projected_cost_usd: float | Decimal) -> int:
        projected_cost_usd = _money(projected_cost_usd)
        self.ensure(stage, projected_cost_usd)
        ticket = self._next_ticket
        self._next_ticket += 1
        if self._accounting is not None:
            self._accounting.reserve(
                run_id=self._run_id, operation_id=ticket, stage=stage, cost_usd=projected_cost_usd
            )
        self._reservations[ticket] = (stage, projected_cost_usd)
        self.events.append(
            {"stage": stage, "status": "reserved", "cost_usd": projected_cost_usd}
        )
        return ticket

    def settle(self, ticket: int, *, stage: str, actual_cost_usd: float | Decimal) -> None:
        reserved_stage, reserved = self._reservations.pop(ticket)
        if reserved_stage != stage:
            raise RuntimeError("Gate 1 spend reservation stage mismatch")
        actual_cost_usd = _money(actual_cost_usd)
        if self._accounting is not None:
            self._accounting.settle(
                run_id=self._run_id,
                operation_id=ticket,
                status="settled",
                incurred_usd=actual_cost_usd,
            )
        self.spent_usd += actual_cost_usd
        self.events.append(
            {
                "stage": stage,
                "status": "settled",
                "reserved_cost_usd": reserved,
                "cost_usd": actual_cost_usd,
            }
        )
        if self.spent_usd > self.limit_usd:
            raise RuntimeError("Gate 1 spend ceiling was exceeded by provider-reported usage")

    def settle_unknown(self, ticket: int, *, stage: str) -> None:
        reserved_stage, reserved = self._reservations.pop(ticket)
        if reserved_stage != stage:
            raise RuntimeError("Gate 1 spend reservation stage mismatch")
        if self._accounting is not None:
            self._accounting.settle(
                run_id=self._run_id,
                operation_id=ticket,
                status="usage_unknown",
                incurred_usd=reserved,
            )
        self.spent_usd += reserved
        self.events.append(
            {"stage": stage, "status": "usage_unknown", "cost_usd": reserved}
        )

    def record_projected_maximum(self, stage: str, cost_usd: float | Decimal) -> None:
        """Record bounded storage exposure; candidate completion does not settle it."""
        cost_usd = _money(cost_usd)
        self.ensure(stage, cost_usd)
        ticket = self._next_ticket
        self._next_ticket += 1
        if self._accounting is not None:
            self._accounting.record_projected_maximum(
                run_id=self._run_id, operation_id=ticket, stage=stage, cost_usd=cost_usd
            )
        self.spent_usd += cost_usd
        self.events.append(
            {
                "event_id": f"operation:{self._run_id}:fixed:{ticket}",
                "stage": stage,
                "status": "storage_exposure",
                "cost_usd": cost_usd,
            }
        )


class PilotRemoteStorageLedger:
    """Private, fail-closed accounting for short-lived Gate 1 remote resources."""

    def __init__(self, path: Path, ledger: SpendLedger):
        self._path = path
        self._ledger = ledger
        self._resources: dict[str, dict[str, Any]] = {}

    def observe(self, kind: str, resource: VectorStore | UploadedFile) -> bool:
        if kind in {"raw_store", "derived_store"}:
            if not isinstance(resource, VectorStore):
                raise ValueError("pilot store accounting requires a vector-store response")
            confirmed = self._store(kind, resource)
        elif kind == "derived_file":
            if not isinstance(resource, UploadedFile):
                raise ValueError("pilot file accounting requires a File response")
            confirmed = self._file(resource)
        else:
            raise ValueError("unknown pilot remote resource kind")
        self._write()
        return confirmed

    def report(self) -> dict[str, Any]:
        return {"resources": tuple(self._resources.values())}

    def mark_cleanup(self, kind: str, remote_id: str, status: str) -> None:
        key = f"vector_store:{remote_id}" if kind in {"raw_store", "derived_store"} else f"file:{remote_id}"
        if key in self._resources:
            self._resources[key]["cleanup_status"] = status
            self._write()

    def _store(self, kind: str, store: VectorStore) -> bool:
        key = f"vector_store:{store.store_id}"
        entry = self._resources.setdefault(key, {
            "kind": kind,
            "resource_kind": "vector_store",
            "remote_id": store.store_id,
            "expiration_policy": PILOT_REMOTE_RETENTION.vector_store.as_request(),
            "cleanup_status": "automatic_expiry_configured",
        })
        entry.update({"created_at": store.created_at, "expires_at": store.expires_at})
        confirmed = store.expires_after == PILOT_REMOTE_RETENTION.vector_store.as_request() or (
            store.created_at is not None
            and store.expires_at is not None
            and 0 < store.expires_at - store.created_at <= 86_400
        )
        entry["expiry_confirmed"] = confirmed
        if not confirmed:
            entry["usage_bytes"] = store.usage_bytes
            entry["projected_maximum_retention_cost_usd"] = None
            return False
        if store.usage_bytes is None:
            entry["usage_bytes"] = None
            entry["projected_maximum_retention_cost_usd"] = None
            raise RuntimeError("pilot vector-store usage_bytes is missing; refusing unknown storage cost")
        maximum = (
            store.usage_bytes / _BYTES_PER_GIB * FILE_SEARCH_STORAGE_USD_PER_GIB_DAY
            * PILOT_REMOTE_RETENTION.vector_store.days
        )
        previous = float(entry.get("projected_maximum_retention_cost_usd") or 0.0)
        if maximum > previous:
            self._ledger.record_projected_maximum("remote_storage", maximum - previous)
        entry.update({
            "usage_bytes": store.usage_bytes,
            "projected_maximum_retention_cost_usd": maximum,
        })
        return True

    def _file(self, file: UploadedFile) -> bool:
        key = f"file:{file.file_id}"
        self._resources[key] = {
            "kind": "derived_file",
            "resource_kind": "file",
            "remote_id": file.file_id,
            "created_at": file.created_at,
            "expires_at": file.expires_at,
            "usage_bytes": file.bytes,
            "expiration_policy": PILOT_REMOTE_RETENTION.derived_file.as_request(),
            "projected_maximum_retention_cost_usd": 0.0,
            "cleanup_status": "automatic_expiry_configured",
        }
        confirmed = (
            file.created_at is not None
            and file.expires_at is not None
            and 0 < file.expires_at - file.created_at <= 86_400
        )
        self._resources[key]["expiry_confirmed"] = confirmed
        return confirmed

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self.report(), indent=2, sort_keys=True), encoding="utf-8")


class _BudgetedResponses:
    def __init__(self, responses: Any, ledger: SpendLedger, diagnostic_path: Path | None = None):
        self._responses = responses
        self._ledger = ledger
        self._diagnostic_path = diagnostic_path
        self._call_index = 0

    def create(self, **request):
        stage = _response_stage(request)
        self._call_index += 1
        cap = _MAX_OUTPUT_TOKENS_BY_STAGE[stage]
        requested_cap = request.get("max_output_tokens")
        request["max_output_tokens"] = (
            cap if requested_cap is None else min(int(requested_cap), cap)
        )
        has_file_search = any(
            isinstance(tool, dict) and tool.get("type") == "file_search"
            for tool in request.get("tools", ())
        )
        input_reserve = len(
            json.dumps(request, sort_keys=True, separators=(",", ":"), default=str).encode()
        ) + 4_096
        if has_file_search:
            input_reserve += _FILE_SEARCH_INPUT_RESERVE_TOKENS
        tool_reserve = (
            _FILE_SEARCH_CALL_RESERVE * FILE_SEARCH_CALL_COST_USD if has_file_search else 0.0
        )
        projected = self._ledger.pricing.cost(
            input_tokens=input_reserve,
            output_tokens=request["max_output_tokens"],
            reasoning_tokens=request["max_output_tokens"],
        ) + tool_reserve
        ticket = self._ledger.reserve(stage, projected)
        try:
            response = self._responses.create(**request)
        except Exception as error:
            self._ledger.settle_unknown(ticket, stage=stage)
            self._write_diagnostic({
                "stage": stage,
                "call_index": self._call_index,
                "model": request.get("model"),
                "prompt_version": _prompt_version(request),
                "schema_version": _schema_version(request),
                "requested_max_output_tokens": request["max_output_tokens"],
                "transport_error": {"type": type(error).__name__, "message": str(error)},
            })
            raise
        self._write_diagnostic(private_response_diagnostic(
            response,
            stage=stage,
            call_index=self._call_index,
            model=request.get("model"),
            prompt_version=_prompt_version(request),
            schema_version=_schema_version(request),
            requested_max_output_tokens=request["max_output_tokens"],
        ))
        if not _usage_is_complete(response):
            self._ledger.settle_unknown(ticket, stage=stage)
            return response
        usage = usage_from_response(response, pricing=self._ledger.pricing)
        actual = usage.cost_usd + _file_search_calls(response) * FILE_SEARCH_CALL_COST_USD
        self._ledger.settle(ticket, stage=stage, actual_cost_usd=actual)
        return response

    def _write_diagnostic(self, value: dict[str, object]) -> None:
        if self._diagnostic_path is None:
            return
        with self._diagnostic_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True, default=str) + "\n")


class BudgetedOpenAIClient:
    """Delegate every non-Responses API while enforcing the Responses spend boundary."""

    def __init__(
        self,
        client: Any,
        ledger: SpendLedger,
        diagnostic_path: Path | None = None,
        *,
        allow_bounded_remote_storage: bool = False,
    ):
        self._client = client
        self.responses = _BudgetedResponses(client.responses, ledger, diagnostic_path)
        self._allow_bounded_remote_storage = allow_bounded_remote_storage

    def __getattr__(self, name: str):
        if name in {"files", "vector_stores"} and not self._allow_bounded_remote_storage:
            raise RuntimeError(
                f"Gate 1 has no defensible per-operation upper bound for {name}; refusing remote operation"
            )
        return getattr(self._client, name)


class Gate1Runner:
    def __init__(
        self,
        *,
        production_database_path: Path,
        manifest_path: Path,
        pilot_root: Path = Path("data/pilots"),
        run_id: str | None = None,
        spend_limit_usd: float = HARD_SPEND_CEILING_USD,
        pricing: TokenPricing = CONSERVATIVE_SOL_PRICING,
        client_factory: Callable[[], Any] | None = None,
        compiler_factory: Callable[[Storage, Any, TokenPricing], Any] | None = None,
        evaluator: Callable[[PilotRuntime, Any], dict[str, Any]] | None = None,
        today: Callable[[], date] = date.today,
        expected_manifest_sha256: str = APPROVED_GATE1_MANIFEST_SHA256,
    ):
        self.production_database_path = Path(production_database_path).resolve()
        self.manifest_path = Path(manifest_path).resolve()
        self.pilot_root = Path(pilot_root).resolve()
        self.run_id = run_id or datetime.now(timezone.utc).strftime(
            "gate1-%Y%m%dT%H%M%SZ-"
        ) + uuid4().hex[:8]
        self.accounting = CumulativeSpendLedger(
            self.pilot_root / "gate1-cumulative-spend.json", self.pilot_root
        )
        prior_spend = self.accounting.reconcile().effective_budget_exposure_usd
        self.ledger = SpendLedger(
            spend_limit_usd,
            pricing,
            prior_spend_usd=prior_spend,
            accounting=self.accounting,
            run_id=self.run_id,
        )
        self.pricing = pricing
        self._client_factory = client_factory or _live_openai_client
        self._compiler_factory = compiler_factory or _live_compiler
        self._evaluator = evaluator or _live_evaluation
        self._today = today
        self._expected_manifest_sha256 = expected_manifest_sha256

    def run(self, *, execute: bool = False) -> Gate1RunReport:
        before = _production_pointers(self.production_database_path)
        production = Storage(self.production_database_path)
        sources, manifest = _resolve_manifest(
            self.manifest_path, production, self._expected_manifest_sha256
        )
        if execute:
            _require_fresh_pricing(self._today())
        plan = estimate_gate1_cost(sources, self.pricing)
        if not execute:
            _require_unchanged(self.production_database_path, before)
            return Gate1RunReport(
                False,
                False,
                self.run_id,
                len(sources),
                manifest.revision_ids,
                plan.estimated_upper_bound_usd,
                self.ledger.spent_usd,
                _money(HARD_SPEND_CEILING_USD) - self.ledger.spent_usd,
            )
        pilot = PilotRuntime.create(
            self.production_database_path, self.pilot_root, run_id=self.run_id
        )
        output_path = pilot.output_directory / "gate1-result.json"
        try:
            _require_unchanged(self.production_database_path, before)
            pilot_sources = CandidateSourcePreparer(pilot.storage).prepare(manifest.revision_ids)
            if tuple(source.revision.revision_id for source in pilot_sources) != manifest.revision_ids:
                raise ValueError("pilot source order no longer matches the approved manifest")
            remote_storage = PilotRemoteStorageLedger(
                pilot.output_directory / "remote-storage-ledger.json", self.ledger
            )
            paid_client = BudgetedOpenAIClient(
                self._client_factory(), self.ledger,
                pilot.output_directory / "response-envelopes.jsonl",
                allow_bounded_remote_storage=True,
            )
            compiler = self._compiler_factory(pilot.storage, paid_client, self.pricing)
            build = compiler.build(
                BuildRequest(
                    run=CompilationRun(
                        self.run_id,
                        GATE1_MODEL,
                        "bound-by-candidate-compiler",
                        "bound-by-candidate-compiler",
                        datetime.now(timezone.utc).timestamp(),
                    ),
                    sources=pilot_sources,
                    artifact_scope=ArtifactScope.PILOT,
                    remote_retention=PILOT_REMOTE_RETENTION,
                    remote_storage_observer=remote_storage.observe,
                    remote_storage_cleanup_observer=remote_storage.mark_cleanup,
                )
            )
            evaluation = None
            published = False
            if build.ready:
                pilot.publish(build.snapshot.snapshot_id)
                published = True
                _require_unchanged(self.production_database_path, before)
                evaluation = self._evaluator(pilot, paid_client)
            accounting_state = self.accounting.reconcile()
            private_result = {
                "run_id": self.run_id,
                "status": "completed" if build.ready and evaluation is not None else "candidate_failed",
                "pricing": {
                    "model": GATE1_MODEL,
                    "checked_on": GATE1_PRICING_CHECKED_ON.isoformat(),
                    "source": GATE1_PRICING_SOURCE,
                    **asdict(self.pricing),
                },
                "plan": _json_value(plan),
                "revision_ids": list(manifest.revision_ids),
                "build": _build_summary(build),
                "evaluation": evaluation,
                "spend": {
                    "limit_usd": self.ledger.limit_usd,
                    "prior_spend_usd": self.ledger.prior_spend_usd,
                    "spent_usd": self.ledger.spent_usd,
                    "actual_incurred_usd": accounting_state.actual_incurred_usd,
                    "reserved_pending_usd": accounting_state.reserved_pending_usd,
                    "bounded_storage_exposure_usd": accounting_state.bounded_storage_exposure_usd,
                    "unknown_liability_usd": accounting_state.unknown_liability_usd,
                    "effective_budget_exposure_usd": accounting_state.effective_budget_exposure_usd,
                    "events": self.ledger.events,
                },
                "remote_storage": remote_storage.report(),
            }
            output_path.write_text(
                json.dumps(private_result, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
            return Gate1RunReport(
                True,
                published,
                self.run_id,
                len(pilot_sources),
                manifest.revision_ids,
                plan.estimated_upper_bound_usd,
                self.ledger.spent_usd,
                _money(HARD_SPEND_CEILING_USD) - self.ledger.spent_usd,
                pilot.run_directory,
                output_path,
            )
        except Exception as error:
            output_path.write_text(
                json.dumps(
                    {
                        "run_id": self.run_id,
                        "status": "stopped",
                        "failure_type": type(error).__name__,
                        "failure": str(error),
                        "spent_usd": self.ledger.spent_usd,
                        "events": self.ledger.events,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            raise
        finally:
            _require_unchanged(self.production_database_path, before)


def estimate_gate1_cost(
    sources: tuple[CandidateSource, ...], pricing: TokenPricing
) -> Gate1CostPlan:
    source_bytes = sum(source.revision.byte_size for source in sources)
    candidate_count = len(sources) * MAX_CANDIDATES_PER_SOURCE
    extraction_calls = len(sources) * EXTRACTION_MAX_ATTEMPTS
    validation_calls = candidate_count
    synthesis_calls = _MAX_SYNTHESIS_CALLS
    evaluation_calls = len(GATE1_EVALUATION_CASES) * 4
    values = (
        (
            "extraction",
            extraction_calls,
            source_bytes * 2 * EXTRACTION_MAX_ATTEMPTS + extraction_calls * 4_096,
            len(sources) * (EXTRACTION_INITIAL_MAX_OUTPUT_TOKENS + EXTRACTION_RETRY_MAX_OUTPUT_TOKENS),
            0.0,
        ),
        (
            "validation",
            validation_calls,
            validation_calls * 12_000,
            validation_calls * _MAX_OUTPUT_TOKENS_BY_STAGE["validation"],
            0.0,
        ),
        (
            "synthesis",
            synthesis_calls,
            synthesis_calls * 120_000,
            synthesis_calls * _MAX_OUTPUT_TOKENS_BY_STAGE["synthesis"],
            0.0,
        ),
        (
            "mentor_evaluation",
            evaluation_calls,
            evaluation_calls * _FILE_SEARCH_INPUT_RESERVE_TOKENS,
            evaluation_calls * _MAX_OUTPUT_TOKENS_BY_STAGE["mentor_evaluation"],
            evaluation_calls * _FILE_SEARCH_CALL_RESERVE * FILE_SEARCH_CALL_COST_USD,
        ),
    )
    stages = tuple(
        StageEstimate(
            stage,
            calls,
            input_tokens,
            output_tokens,
            pricing.cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=output_tokens,
            ) + tool_cost,
        )
        for stage, calls, input_tokens, output_tokens, tool_cost in values
    )
    return Gate1CostPlan(
        len(sources),
        source_bytes,
        candidate_count,
        stages,
        sum(stage.cost_usd for stage in stages),
    )


def _resolve_manifest(
    manifest_path: Path, storage: Storage, expected_sha256: str
) -> tuple[tuple[CandidateSource, ...], PilotManifest]:
    try:
        raw = manifest_path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("approved Gate 1 manifest is unavailable or invalid") from error
    from hashlib import sha256

    if sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("approved Gate 1 manifest bytes changed after review")
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "phase-3-pilot-manifest-v1"
        or payload.get("status") != "selected_not_compiled"
        or not isinstance(payload.get("entries"), list)
    ):
        raise ValueError("approved Gate 1 manifest contract is invalid")
    manifest = PilotManifest(
        tuple(
            PilotManifestEntry(
                entry.get("revision_id"), tuple(entry.get("structural_roles", ()))
            )
            for entry in payload["entries"]
            if isinstance(entry, dict)
        )
    )
    for entry in payload["entries"]:
        revision = storage.source_revision(entry["revision_id"])
        source = None if revision is None else storage.library_source(revision.source_id)
        if revision is None or source is None or revision.lifecycle_state != "active":
            raise ValueError("approved manifest SourceRevision no longer resolves as active")
        if (entry.get("source_name"), entry.get("year")) != (
            source.lesson_title,
            source.year,
        ):
            raise ValueError("approved manifest source identity no longer matches its revision")
    sources = CandidateSourcePreparer(storage).prepare(manifest.revision_ids)
    return sources, manifest


def _production_pointers(database_path: Path) -> _ProductionPointers:
    if not database_path.is_file():
        raise FileNotFoundError("production SQLite runtime does not exist")
    with sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True) as connection:
        rows = dict(
            connection.execute(
                "SELECT key, value FROM settings WHERE key IN "
                "('current_snapshot_id', 'active_raw_store_id', 'active_derived_store_id')"
            ).fetchall()
        )
    return _ProductionPointers(
        rows.get("current_snapshot_id"),
        rows.get("active_raw_store_id"),
        rows.get("active_derived_store_id"),
    )


def _require_unchanged(database_path: Path, expected: _ProductionPointers) -> None:
    if _production_pointers(database_path) != expected:
        raise RuntimeError("production current snapshot/store pointers changed during Gate 1")


def _require_fresh_pricing(today: date) -> None:
    age = (today - GATE1_PRICING_CHECKED_ON).days
    if not 0 <= age <= _PRICING_MAX_AGE_DAYS:
        raise ValueError("Gate 1 pricing check is stale; reverify official pricing before execute")


def _response_stage(request: dict[str, Any]) -> str:
    instructions = str(request.get("instructions", "")).lower()
    format_name = str(
        request.get("text", {}).get("format", {}).get("name", "")
        if isinstance(request.get("text"), dict)
        else ""
    ).lower()
    marker = f"{instructions} {format_name}"
    if "source-extraction" in marker:
        return "extraction"
    if "semantic-validation" in marker:
        return "validation"
    if "cross-source-synthesis" in marker:
        return "synthesis"
    return "mentor_evaluation"


def _prompt_version(request: dict[str, Any]) -> str | None:
    first_line = str(request.get("instructions", "")).splitlines()[0:1]
    value = first_line[0].removeprefix("Prompt version: ").strip() if first_line else ""
    return value or None


def _schema_version(request: dict[str, Any]) -> str | None:
    text = request.get("text")
    if not isinstance(text, dict):
        return None
    format_ = text.get("format")
    return format_.get("name") if isinstance(format_, dict) and isinstance(format_.get("name"), str) else None


def _usage_is_complete(response: Any) -> bool:
    usage = getattr(response, "usage", None)
    return all(
        isinstance(getattr(usage, field, None), int)
        and not isinstance(getattr(usage, field, None), bool)
        and getattr(usage, field) >= 0
        for field in ("input_tokens", "output_tokens")
    )


def _file_search_calls(response: Any) -> int:
    return sum(
        (
            item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        )
        == "file_search_call"
        for item in getattr(response, "output", ())
    )


def _live_openai_client():
    from openai import OpenAI

    config = load_config(os.environ, Path(".env"))
    return OpenAI(api_key=config.api_key)


def _live_compiler(storage: Storage, client: Any, pricing: TokenPricing) -> CandidateCompiler:
    return CandidateCompiler(
        storage=storage,
        extractor=SourceExtractor(client, model=GATE1_MODEL, live_mode=True, pricing=pricing),
        validation_client=client,
        synthesizer=SynthesisReconciler(
            client,
            model=GATE1_MODEL,
            live_mode=True,
            max_total_records=MAX_CANDIDATES_PER_SOURCE * 6,
            max_calls=_MAX_SYNTHESIS_CALLS,
            pricing=pricing,
        ),
        vector_stores=VectorStoreAdapter(client),
        orientation_budget=OrientationBudget(max_records=8, max_tokens=4_000),
        validation_model=GATE1_MODEL,
        validation_pricing=pricing,
        live_mode=True,
    )


class _Phase2PilotStorage:
    def __init__(self, storage: Storage, raw_store_id: str):
        self._storage = storage
        self._raw_store_id = raw_store_id

    def current_snapshot(self):
        return None

    def vector_store_id(self):
        return self._raw_store_id

    def __getattr__(self, name: str):
        return getattr(self._storage, name)


def _live_evaluation(pilot: PilotRuntime, client: Any) -> dict[str, Any]:
    snapshot = pilot.storage.current_snapshot()
    if snapshot is None or not snapshot.raw_store_id:
        raise ValueError("pilot evaluation requires the published six-source raw store")
    baseline = ChatService(_Phase2PilotStorage(pilot.storage, snapshot.raw_store_id), client)
    assimilated = ChatService(pilot.storage, client)
    results = []
    evaluation = EvaluationConfig(reasoning_effort="high", research_depth="normal")
    for case in GATE1_EVALUATION_CASES:
        baseline_answer = baseline.reply(
            pilot.storage.create_thread(f"Gate 1 baseline: {case.case_id}"),
            case.prompt,
            evaluation,
        )
        assimilated_answer = assimilated.reply(
            pilot.storage.create_thread(f"Gate 1 assimilated: {case.case_id}"),
            case.prompt,
            evaluation,
        )
        results.append(
            {
                "case": asdict(case),
                "baseline": _json_value(baseline_answer),
                "assimilated": _json_value(assimilated_answer),
                "baseline_telemetry": _mentor_evaluation_telemetry(
                    baseline_answer, orientation_required=False
                ),
                "assimilated_telemetry": _mentor_evaluation_telemetry(
                    assimilated_answer,
                    orientation_required=_should_orient(
                        case.prompt, _effective_research_depth(case.prompt, evaluation.research_depth)
                    ),
                ),
            }
        )
    return {"cases": results}


def _mentor_evaluation_telemetry(answer: Any, *, orientation_required: bool) -> dict[str, Any]:
    """Safe experiment telemetry; response text and private orientation payloads never enter it."""
    diagnostics = getattr(answer, "diagnostics", None)
    context = getattr(diagnostics, "knowledge_context", None)
    raw_search_calls = getattr(diagnostics, "file_search_calls", 0)
    raw_verification = isinstance(raw_search_calls, int) and raw_search_calls > 0
    if not orientation_required:
        validity = "VALID_ORIENTATION_NOT_REQUIRED"
    elif not isinstance(context, dict):
        validity = "INVALID_ORIENTATION_NOT_ATTEMPTED"
    elif (
        context.get("status") == "used"
        and context.get("requested") is True
        and context.get("attempted") is True
        and context.get("retrieval_succeeded") is True
        and context.get("used") is True
        and isinstance(context.get("record_count"), int)
        and context["record_count"] > 0
        and raw_verification
    ):
        validity = "VALID_ORIENTATION_USED"
    elif context.get("status") in {"unavailable", "not_called"}:
        validity = "INVALID_FALLBACK_MASKED_FAILURE"
    else:
        validity = "INVALID_ORIENTATION_EMPTY_OR_UNPROVEN"
    return {
        "orientation_required": orientation_required,
        "orientation_requested": isinstance(context, dict) and context.get("requested") is True,
        "orientation_attempted": isinstance(context, dict) and context.get("attempted") is True,
        "orientation_retrieval_succeeded": (
            isinstance(context, dict) and context.get("retrieval_succeeded") is True
        ),
        "orientation_context_admitted": isinstance(context, dict) and context.get("used") is True,
        "orientation_record_count": 0 if not isinstance(context, dict) else context.get("record_count", 0),
        "raw_verification_occurred": raw_verification,
        "validity": validity,
    }


def _build_summary(build: Any) -> dict[str, Any]:
    records = tuple(getattr(build, "records", ()))
    families: dict[str, int] = {}
    for record in records:
        families[record.family] = families.get(record.family, 0) + 1
    return {
        "ready": bool(build.ready),
        "snapshot_id": build.snapshot.snapshot_id,
        "failures": list(build.failures),
        "record_count": len(records),
        "record_families": families,
        "stage_metrics": _json_value(tuple(getattr(build, "stage_metrics", ()))),
        "total_metric": _json_value(getattr(build, "total_metric", None)),
        "raw_artifact": _json_value(getattr(build, "raw_artifact", None)),
        "derived_artifact": _json_value(getattr(build, "derived_artifact", None)),
        "synthesis_admission_audit": _json_value(
            getattr(build, "synthesis_admission_audit", None)
        ),
    }


def _json_value(value: Any):
    if isinstance(value, Decimal):
        return _money_text(value)
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and value.__class__.__module__ == "enum":
        return value.value
    return value


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Preflight or execute isolated Gate 1 only")
    parser.add_argument("--execute", action="store_true", help="enable paid Gate 1 calls")
    parser.add_argument("--production-db", type=Path, default=Path("data/mentor.sqlite3"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/pilots/task-19-six-source-manifest.json"),
    )
    parser.add_argument("--pilot-root", type=Path, default=Path("data/pilots"))
    parser.add_argument("--max-spend", type=float, default=HARD_SPEND_CEILING_USD)
    args = parser.parse_args()
    runner = Gate1Runner(
        production_database_path=args.production_db,
        manifest_path=args.manifest,
        pilot_root=args.pilot_root,
        spend_limit_usd=args.max_spend,
    )
    report = runner.run(execute=args.execute)
    accounting = runner.accounting.reconcile()
    print(
        json.dumps(
            {
                "executed": report.executed,
                "published": report.published,
                "source_count": report.source_count,
                "estimated_upper_bound_usd": report.estimated_upper_bound_usd,
                "spent_usd": _money_text(report.spent_usd),
                "remaining_usd": _money_text(report.remaining_usd),
                "settled_actual_usd": _money_text(accounting.actual_incurred_usd),
                "pending_request_reservations_usd": _money_text(accounting.reserved_pending_usd),
                "bounded_storage_exposure_usd": _money_text(accounting.bounded_storage_exposure_usd),
                "unknown_liability_usd": _money_text(accounting.unknown_liability_usd),
                "effective_exposure_usd": _money_text(accounting.effective_budget_exposure_usd),
                "pilot_directory": None
                if report.pilot_directory is None
                else str(report.pilot_directory),
                "output_path": None if report.output_path is None else str(report.output_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
