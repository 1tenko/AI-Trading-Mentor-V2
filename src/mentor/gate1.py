"""Explicit, budgeted Gate 1 runner for the isolated six-source pilot."""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
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
)
from mentor.chat_service import ChatService, EvaluationConfig, FILE_SEARCH_CALL_COST_USD
from mentor.compilation import CompilationRun, TokenPricing, usage_from_response
from mentor.compiler import SourceExtractor
from mentor.compiler_prompts import MAX_CANDIDATES_PER_SOURCE
from mentor.config import load_config
from mentor.evaluation import EvaluationCase, PilotManifest, PilotManifestEntry, PilotRuntime
from mentor.orientation import OrientationBudget
from mentor.storage import Storage
from mentor.synthesis import SynthesisReconciler
from mentor.vector_stores import VectorStoreAdapter
from mentor.structured_response import private_response_diagnostic


GATE1_MODEL = "gpt-5.6-sol"
GATE1_PRICING_CHECKED_ON = date(2026, 8, 24)
GATE1_PRICING_SOURCE = "https://platform.openai.com/pricing"
APPROVED_GATE1_MANIFEST_SHA256 = "3798d537cd486f782449d9833b5fc06dd28fa93aed4564cdd742e66225d15d38"
# Standard short-context rates are deliberately higher than the current model-page promotion.
CONSERVATIVE_SOL_PRICING = TokenPricing(5.0, 30.0, 30.0)
HARD_SPEND_CEILING_USD = 30.0
GATE1_PRIOR_SPEND_USD = 8.502370
_PRICING_MAX_AGE_DAYS = 7
_OUTPUT_CAPS = {
    "extraction": 8_000,
    "validation": 1_000,
    "synthesis": 10_000,
    "mentor_evaluation": 4_000,
}
_MAX_SYNTHESIS_CALLS = 6
_FILE_SEARCH_INPUT_RESERVE_TOKENS = 50_000
_FILE_SEARCH_CALL_RESERVE = 8

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
    pilot_directory: Path | None = None
    output_path: Path | None = None


@dataclass(frozen=True)
class _ProductionPointers:
    current_snapshot_id: str | None
    raw_store_id: str | None
    derived_store_id: str | None


class SpendLedger:
    """Sequential hard ceiling checked before every paid Responses call."""

    def __init__(self, limit_usd: float, pricing: TokenPricing, *, prior_spend_usd: float = 0.0):
        if (
            isinstance(limit_usd, bool)
            or not isinstance(limit_usd, int | float)
            or not math.isfinite(limit_usd)
            or not 0 < limit_usd <= HARD_SPEND_CEILING_USD
        ):
            raise ValueError("Gate 1 spend limit must be positive and no more than $30")
        if (
            isinstance(prior_spend_usd, bool)
            or not isinstance(prior_spend_usd, int | float)
            or not math.isfinite(prior_spend_usd)
            or prior_spend_usd < 0
            or prior_spend_usd > limit_usd
        ):
            raise ValueError("prior Gate 1 spend must be finite and within the cumulative ceiling")
        pricing.require_complete("Gate 1")
        self.limit_usd = float(limit_usd)
        self.pricing = pricing
        self.prior_spend_usd = float(prior_spend_usd)
        self.spent_usd = self.prior_spend_usd
        self._reservations: dict[int, tuple[str, float]] = {}
        self._stage_limits: dict[str, float] = {}
        self._stage_spend: dict[str, float] = {}
        self._next_ticket = 1
        self.events: list[dict[str, Any]] = []

    def ensure(self, stage: str, projected_cost_usd: float) -> None:
        if projected_cost_usd < 0 or not math.isfinite(projected_cost_usd):
            raise ValueError("projected Gate 1 cost must be finite and non-negative")
        reserved = sum(value for _stage, value in self._reservations.values())
        if self.spent_usd + reserved + projected_cost_usd > self.limit_usd:
            raise RuntimeError(
                f"Gate 1 spend ceiling blocks {stage}: spent ${self.spent_usd:.4f}, "
                f"projected additional ${projected_cost_usd:.4f}, limit ${self.limit_usd:.2f}"
            )
        stage_reserved = sum(
            value for reserved_stage, value in self._reservations.values()
            if reserved_stage == stage
        )
        stage_limit = self._stage_limits.get(stage)
        if (
            stage_limit is not None
            and self._stage_spend.get(stage, 0.0) + stage_reserved + projected_cost_usd
            > stage_limit
        ):
            raise RuntimeError(f"Gate 1 {stage} stage budget is exhausted")

    def set_stage_limits(self, limits: dict[str, float]) -> None:
        if self._stage_spend or self._reservations:
            raise RuntimeError("Gate 1 stage budgets must be fixed before paid work")
        if any(cost <= 0 or not math.isfinite(cost) for cost in limits.values()):
            raise ValueError("Gate 1 stage budgets must be finite and positive")
        self._stage_limits = dict(limits)

    def reserve(self, stage: str, projected_cost_usd: float) -> int:
        self.ensure(stage, projected_cost_usd)
        ticket = self._next_ticket
        self._next_ticket += 1
        self._reservations[ticket] = (stage, projected_cost_usd)
        self.events.append(
            {"stage": stage, "status": "reserved", "cost_usd": projected_cost_usd}
        )
        return ticket

    def settle(self, ticket: int, *, stage: str, actual_cost_usd: float) -> None:
        reserved_stage, reserved = self._reservations.pop(ticket)
        if reserved_stage != stage:
            raise RuntimeError("Gate 1 spend reservation stage mismatch")
        if actual_cost_usd < 0 or not math.isfinite(actual_cost_usd):
            raise ValueError("actual Gate 1 cost must be finite and non-negative")
        self.spent_usd += actual_cost_usd
        self._stage_spend[stage] = self._stage_spend.get(stage, 0.0) + actual_cost_usd
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
        self.spent_usd += reserved
        self._stage_spend[stage] = self._stage_spend.get(stage, 0.0) + reserved
        self.events.append(
            {"stage": stage, "status": "usage_unknown", "cost_usd": reserved}
        )


class _BudgetedResponses:
    def __init__(self, responses: Any, ledger: SpendLedger, diagnostic_path: Path | None = None):
        self._responses = responses
        self._ledger = ledger
        self._diagnostic_path = diagnostic_path
        self._call_index = 0

    def create(self, **request):
        stage = _response_stage(request)
        self._call_index += 1
        cap = _OUTPUT_CAPS[stage]
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

    def __init__(self, client: Any, ledger: SpendLedger, diagnostic_path: Path | None = None):
        self._client = client
        self.responses = _BudgetedResponses(client.responses, ledger, diagnostic_path)

    def __getattr__(self, name: str):
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
        self.ledger = SpendLedger(
            spend_limit_usd, pricing, prior_spend_usd=GATE1_PRIOR_SPEND_USD
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
        plan = estimate_gate1_cost(sources, self.pricing)
        if self.ledger.spent_usd + plan.estimated_upper_bound_usd > self.ledger.limit_usd:
            raise ValueError(
                f"estimated Gate 1 cost ${plan.estimated_upper_bound_usd:.4f} plus prior spend "
                f"${self.ledger.prior_spend_usd:.4f} exceeds the ${self.ledger.limit_usd:.2f} cumulative limit"
            )
        self.ledger.set_stage_limits({stage.stage: stage.cost_usd for stage in plan.stages})
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
            )
        _require_fresh_pricing(self._today())
        pilot = PilotRuntime.create(
            self.production_database_path, self.pilot_root, run_id=self.run_id
        )
        output_path = pilot.output_directory / "gate1-result.json"
        try:
            _require_unchanged(self.production_database_path, before)
            pilot_sources = CandidateSourcePreparer(pilot.storage).prepare(manifest.revision_ids)
            if tuple(source.revision.revision_id for source in pilot_sources) != manifest.revision_ids:
                raise ValueError("pilot source order no longer matches the approved manifest")
            paid_client = BudgetedOpenAIClient(
                self._client_factory(), self.ledger,
                pilot.output_directory / "response-envelopes.jsonl",
            )
            compiler = self._compiler_factory(pilot.storage, paid_client, self.pricing)
            self.ledger.ensure(
                "compiler",
                plan.cost_for("extraction", "validation", "synthesis"),
            )
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
                )
            )
            evaluation = None
            published = False
            if build.ready:
                self.ledger.ensure("mentor_evaluation", plan.cost_for("mentor_evaluation"))
                pilot.publish(build.snapshot.snapshot_id)
                published = True
                _require_unchanged(self.production_database_path, before)
                evaluation = self._evaluator(pilot, paid_client)
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
                    "events": self.ledger.events,
                },
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
    candidate_count = sum(
        max(1, min(MAX_CANDIDATES_PER_SOURCE, math.ceil(source.revision.byte_size / 4_000)))
        for source in sources
    )
    extraction_calls = len(sources)
    validation_calls = candidate_count
    synthesis_calls = _MAX_SYNTHESIS_CALLS
    evaluation_calls = len(GATE1_EVALUATION_CASES) * 4
    values = (
        (
            "extraction",
            extraction_calls,
            source_bytes * 2 + extraction_calls * 4_096,
            extraction_calls * _OUTPUT_CAPS["extraction"],
            0.0,
        ),
        (
            "validation",
            validation_calls,
            validation_calls * 12_000,
            validation_calls * _OUTPUT_CAPS["validation"],
            0.0,
        ),
        (
            "synthesis",
            synthesis_calls,
            synthesis_calls * 120_000,
            synthesis_calls * _OUTPUT_CAPS["synthesis"],
            0.0,
        ),
        (
            "mentor_evaluation",
            evaluation_calls,
            evaluation_calls * _FILE_SEARCH_INPUT_RESERVE_TOKENS,
            evaluation_calls * _OUTPUT_CAPS["mentor_evaluation"],
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
            }
        )
    return {"cases": results}


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
    }


def _json_value(value: Any):
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
    report = Gate1Runner(
        production_database_path=args.production_db,
        manifest_path=args.manifest,
        pilot_root=args.pilot_root,
        spend_limit_usd=args.max_spend,
    ).run(execute=args.execute)
    print(
        json.dumps(
            {
                "executed": report.executed,
                "published": report.published,
                "source_count": report.source_count,
                "estimated_upper_bound_usd": report.estimated_upper_bound_usd,
                "spent_usd": report.spent_usd,
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
