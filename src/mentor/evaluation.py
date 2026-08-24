"""Deterministic Phase 3 evaluation metrics and isolated pilot runtime."""

from dataclasses import dataclass
from contextlib import closing
import math
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Callable, Iterable
from uuid import uuid4

from mentor.config import PILOT_RUNTIME_SCOPE, PRODUCTION_RUNTIME_SCOPE
from mentor.storage import Storage


_QUALITY_STATES = frozenset({"passed", "failed", "not_scored"})
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_PILOT_STRUCTURAL_ROLES = frozenset(
    {
        "foundation",
        "procedure",
        "cross_year_2025",
        "cross_year_2026",
        "exception_condition",
        "synthesis_evolution",
        "conflict_uncertainty",
    }
)


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    category: str
    prompt: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in vars(self).values()):
            raise ValueError("evaluation case fields must be non-empty text")


@dataclass(frozen=True)
class EvaluationMetrics:
    correctness_state: str
    completeness_state: str
    source_discipline_state: str
    citation_count: int
    connection_state: str
    evolution_state: str
    correction_state: str
    orientation_calls: int
    orientation_record_ids: tuple[str, ...]
    raw_search_calls: int
    retrieved_passage_count: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_usd: float

    def __post_init__(self) -> None:
        for value in (
            self.correctness_state,
            self.completeness_state,
            self.source_discipline_state,
            self.connection_state,
            self.evolution_state,
            self.correction_state,
        ):
            if value not in _QUALITY_STATES:
                raise ValueError("evaluation quality states must be passed, failed, or not_scored")
        counts = (
            self.citation_count,
            self.orientation_calls,
            self.raw_search_calls,
            self.retrieved_passage_count,
            self.input_tokens,
            self.output_tokens,
            self.latency_ms,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("evaluation counts must be non-negative integers")
        if not isinstance(self.orientation_record_ids, tuple) or any(
            not isinstance(value, str) or not value.strip()
            for value in self.orientation_record_ids
        ):
            raise ValueError("orientation record IDs must be non-empty text")
        object.__setattr__(
            self,
            "orientation_record_ids",
            tuple(dict.fromkeys(self.orientation_record_ids)),
        )
        if (
            isinstance(self.estimated_cost_usd, bool)
            or not isinstance(self.estimated_cost_usd, int | float)
            or not math.isfinite(self.estimated_cost_usd)
            or self.estimated_cost_usd < 0
        ):
            raise ValueError("evaluation cost must be a finite non-negative number")

    @property
    def orientation_record_count(self) -> int:
        return len(self.orientation_record_ids)


@dataclass(frozen=True)
class EvaluationOutcome:
    case_id: str
    category: str
    metrics: EvaluationMetrics | None
    failure_type: str | None = None


@dataclass(frozen=True)
class EvaluationSummary:
    case_count: int
    completed_count: int
    failed_count: int
    correctness_passed_count: int
    completeness_passed_count: int
    source_discipline_passed_count: int
    citation_count: int
    connection_passed_count: int
    evolution_passed_count: int
    correction_passed_count: int
    orientation_calls: int
    orientation_record_ids: tuple[str, ...]
    orientation_record_count: int
    raw_search_calls: int
    retrieved_passage_count: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_usd: float


@dataclass(frozen=True)
class EvaluationReport:
    variant: str
    outcomes: tuple[EvaluationOutcome, ...]
    summary: EvaluationSummary


@dataclass(frozen=True)
class EvaluationComparison:
    baseline: EvaluationSummary
    assimilated: EvaluationSummary


@dataclass(frozen=True)
class PilotManifestEntry:
    revision_id: str
    structural_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.revision_id, str) or not self.revision_id.strip():
            raise ValueError("pilot manifest revision IDs must be non-empty text")
        if (
            not isinstance(self.structural_roles, tuple)
            or not self.structural_roles
            or any(role not in _PILOT_STRUCTURAL_ROLES for role in self.structural_roles)
        ):
            raise ValueError("pilot manifest structural roles are invalid")


@dataclass(frozen=True)
class PilotManifest:
    entries: tuple[PilotManifestEntry, ...]

    def __post_init__(self) -> None:
        if len(self.entries) != 6 or any(
            not isinstance(entry, PilotManifestEntry) for entry in self.entries
        ):
            raise ValueError("pilot manifest requires exactly six typed source revisions")
        revision_ids = self.revision_ids
        if len(set(revision_ids)) != len(revision_ids):
            raise ValueError("pilot manifest source revisions must be unique")
        covered_roles = {role for entry in self.entries for role in entry.structural_roles}
        if not _PILOT_STRUCTURAL_ROLES <= covered_roles:
            raise ValueError("pilot manifest is missing required structural roles")

    @property
    def revision_ids(self) -> tuple[str, ...]:
        return tuple(entry.revision_id for entry in self.entries)


def run_evaluation(
    variant: str,
    cases: Iterable[EvaluationCase],
    runner: Callable[[EvaluationCase], EvaluationMetrics],
) -> EvaluationReport:
    if not isinstance(variant, str) or not variant.strip():
        raise ValueError("evaluation variant must be non-empty text")
    cases = tuple(cases)
    if any(not isinstance(case, EvaluationCase) for case in cases):
        raise ValueError("evaluation cases must be typed")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("evaluation case IDs must be unique")
    outcomes = []
    for case in cases:
        try:
            metrics = runner(case)
            if not isinstance(metrics, EvaluationMetrics):
                raise TypeError("runner did not return EvaluationMetrics")
            outcomes.append(EvaluationOutcome(case.case_id, case.category, metrics))
        except Exception as error:
            outcomes.append(
                EvaluationOutcome(case.case_id, case.category, None, type(error).__name__)
            )
    values = tuple(outcomes)
    return EvaluationReport(variant, values, _summarize(values))


def compare_evaluations(
    baseline: EvaluationReport, assimilated: EvaluationReport
) -> EvaluationComparison:
    baseline_cases = tuple((item.case_id, item.category) for item in baseline.outcomes)
    assimilated_cases = tuple((item.case_id, item.category) for item in assimilated.outcomes)
    if baseline_cases != assimilated_cases:
        raise ValueError("baseline and assimilated evaluation must use the same cases")
    return EvaluationComparison(baseline.summary, assimilated.summary)


def _summarize(outcomes: tuple[EvaluationOutcome, ...]) -> EvaluationSummary:
    metrics = tuple(item.metrics for item in outcomes if item.metrics is not None)
    orientation_record_ids = tuple(
        dict.fromkeys(
            record_id
            for item in metrics
            for record_id in item.orientation_record_ids
        )
    )
    return EvaluationSummary(
        case_count=len(outcomes),
        completed_count=len(metrics),
        failed_count=len(outcomes) - len(metrics),
        correctness_passed_count=sum(item.correctness_state == "passed" for item in metrics),
        completeness_passed_count=sum(item.completeness_state == "passed" for item in metrics),
        source_discipline_passed_count=sum(
            item.source_discipline_state == "passed" for item in metrics
        ),
        citation_count=sum(item.citation_count for item in metrics),
        connection_passed_count=sum(item.connection_state == "passed" for item in metrics),
        evolution_passed_count=sum(item.evolution_state == "passed" for item in metrics),
        correction_passed_count=sum(item.correction_state == "passed" for item in metrics),
        orientation_calls=sum(item.orientation_calls for item in metrics),
        orientation_record_ids=orientation_record_ids,
        orientation_record_count=len(orientation_record_ids),
        raw_search_calls=sum(item.raw_search_calls for item in metrics),
        retrieved_passage_count=sum(item.retrieved_passage_count for item in metrics),
        input_tokens=sum(item.input_tokens for item in metrics),
        output_tokens=sum(item.output_tokens for item in metrics),
        latency_ms=sum(item.latency_ms for item in metrics),
        estimated_cost_usd=sum(item.estimated_cost_usd for item in metrics),
    )


@dataclass(frozen=True)
class PilotRuntime:
    run_id: str
    run_directory: Path
    database_path: Path
    output_directory: Path
    trace_directory: Path
    storage: Storage

    @classmethod
    def create(
        cls,
        production_database_path: Path,
        pilot_root: Path = Path("data/pilots"),
        *,
        run_id: str | None = None,
    ) -> "PilotRuntime":
        production_database_path = Path(production_database_path).resolve()
        if not production_database_path.is_file():
            raise FileNotFoundError("production SQLite runtime does not exist")
        run_id = run_id or f"pilot-{uuid4().hex}"
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("pilot run ID is invalid")
        run_directory = Path(pilot_root).resolve() / run_id
        if run_directory.exists():
            raise FileExistsError(run_directory)
        database_path = run_directory / "mentor.sqlite3"
        created = False
        try:
            with closing(
                sqlite3.connect(f"{production_database_path.as_uri()}?mode=ro", uri=True)
            ) as source:
                scope = source.execute(
                    "SELECT value FROM settings WHERE key = 'runtime_scope'"
                ).fetchone()
                if scope is not None and scope[0] != PRODUCTION_RUNTIME_SCOPE:
                    raise ValueError("pilot source must be a production runtime")
                if source.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise ValueError("production SQLite runtime failed integrity check")
                run_directory.mkdir(parents=True, exist_ok=False)
                created = True
                with closing(sqlite3.connect(database_path)) as destination:
                    source.backup(destination)
                    with destination:
                        destination.execute(
                            "DELETE FROM settings WHERE key IN "
                            "('current_snapshot_id', 'active_raw_store_id', 'active_derived_store_id')"
                        )
                        destination.execute(
                            "INSERT INTO settings(key, value) VALUES ('runtime_scope', ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (PILOT_RUNTIME_SCOPE,),
                        )
            output_directory = run_directory / "outputs"
            trace_directory = run_directory / "traces"
            output_directory.mkdir()
            trace_directory.mkdir()
            storage = Storage(database_path, runtime_scope=PILOT_RUNTIME_SCOPE)
            storage.initialize()
        except Exception:
            if created:
                shutil.rmtree(run_directory)
            raise
        return cls(
            run_id,
            run_directory,
            database_path,
            output_directory,
            trace_directory,
            storage,
        )

    def publish(self, snapshot_id: str, *, published_at: float | None = None):
        if self.storage.candidate_artifact_scope(snapshot_id) != PILOT_RUNTIME_SCOPE:
            raise ValueError("pilot runtime can publish only pilot-scoped candidates")
        return self.storage.transition_snapshot(
            snapshot_id,
            "published",
            transitioned_at=published_at,
        )

    def create_server(self, chat_service, *, port: int = 8765):
        from mentor.server import create_server

        return create_server(self.storage, chat_service, port=port)
