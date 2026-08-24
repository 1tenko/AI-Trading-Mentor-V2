"""Immutable local identities for candidate corpus compilation."""

from dataclasses import dataclass
from hashlib import sha256
import math
from typing import Sequence

from mentor.knowledge import SourceRevision


@dataclass(frozen=True)
class CallUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (self.input_tokens, self.output_tokens, self.reasoning_tokens)
            )
            or not isinstance(self.cost_usd, int | float)
            or isinstance(self.cost_usd, bool)
            or not math.isfinite(self.cost_usd)
            or self.cost_usd < 0
            or self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError("call usage cannot be negative")


@dataclass(frozen=True)
class TokenPricing:
    """Caller-owned per-million-token prices for reproducible local accounting."""

    input_per_million: float
    output_per_million: float
    reasoning_per_million: float

    def __post_init__(self) -> None:
        values = (self.input_per_million, self.output_per_million, self.reasoning_per_million)
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value < 0
            for value in values
        ) or not any(values):
            raise ValueError("token pricing requires finite non-negative caller rates")

    def cost(self, *, input_tokens: int, output_tokens: int, reasoning_tokens: int) -> float:
        visible_output = output_tokens - reasoning_tokens
        if min(input_tokens, visible_output, reasoning_tokens) < 0:
            raise ValueError("token counts are inconsistent")
        return (
            input_tokens * self.input_per_million
            + visible_output * self.output_per_million
            + reasoning_tokens * self.reasoning_per_million
        ) / 1_000_000

    def require_complete(self, stage: str) -> None:
        """Reject a live stage unless every reproducible token rate is explicit."""
        if any(value <= 0 for value in (
            self.input_per_million,
            self.output_per_million,
            self.reasoning_per_million,
        )):
            raise ValueError(f"{stage} pricing requires positive input, output, and reasoning rates")


def usage_from_response(response: object, *, pricing: TokenPricing | None = None) -> CallUsage:
    usage = getattr(response, "usage", None)
    input_tokens = _nonnegative_int(getattr(usage, "input_tokens", 0))
    output_tokens = _nonnegative_int(getattr(usage, "output_tokens", 0))
    details = getattr(usage, "output_tokens_details", None)
    reasoning_tokens = _nonnegative_int(getattr(details, "reasoning_tokens", 0))
    if reasoning_tokens > output_tokens:
        raise ValueError("reasoning tokens cannot exceed output tokens")
    cost_usd = 0.0 if pricing is None else pricing.cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
    )
    return CallUsage(input_tokens, output_tokens, cost_usd, reasoning_tokens)


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


@dataclass(frozen=True)
class CompilationRun:
    run_id: str
    model_version: str
    prompt_version: str
    schema_version: str
    started_at: float
    status: str = "building"
    completed_at: float | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class CorpusSnapshot:
    snapshot_id: str
    run_id: str
    selected_revision_ids: tuple[str, ...]
    selected_revision_fingerprint: str
    raw_store_id: str | None
    derived_store_id: str | None
    model_version: str
    prompt_version: str
    schema_version: str
    status: str
    created_at: float
    validated_at: float | None = None
    published_at: float | None = None
    failed_at: float | None = None
    failure_reason: str | None = None

    @classmethod
    def create(
        cls,
        *,
        run: CompilationRun,
        selected_revisions: Sequence[SourceRevision],
        raw_store_id: str | None,
        derived_store_id: str | None,
        created_at: float,
    ) -> "CorpusSnapshot":
        revision_ids, fingerprint, snapshot_id = cls.identity_for(
            run.run_id,
            [revision.revision_id for revision in selected_revisions],
            compiler_versions=(run.model_version, run.prompt_version, run.schema_version),
        )
        return cls(
            snapshot_id=snapshot_id,
            run_id=run.run_id,
            selected_revision_ids=revision_ids,
            selected_revision_fingerprint=fingerprint,
            raw_store_id=raw_store_id,
            derived_store_id=derived_store_id,
            model_version=run.model_version,
            prompt_version=run.prompt_version,
            schema_version=run.schema_version,
            status="building",
            created_at=created_at,
        )

    @staticmethod
    def identity_for(
        run_id: str,
        selected_revision_ids: Sequence[str],
        *,
        compiler_versions: tuple[str, str, str] | None = None,
    ) -> tuple[tuple[str, ...], str, str]:
        revision_ids = tuple(sorted(selected_revision_ids))
        if not revision_ids or len(set(revision_ids)) != len(revision_ids):
            raise ValueError("selected revisions must be non-empty and unique")
        fingerprint = sha256("\n".join(revision_ids).encode()).hexdigest()
        identity = f"{run_id}\0{fingerprint}"
        if compiler_versions is not None:
            if any(not isinstance(value, str) or not value for value in compiler_versions):
                raise ValueError("compiler versions must be complete")
            identity += "\0" + "\0".join(compiler_versions)
        snapshot_id = f"snap_{sha256(identity.encode()).hexdigest()}"
        return revision_ids, fingerprint, snapshot_id


@dataclass(frozen=True)
class SourceProcessingResult:
    revision_id: str
    status: str
    record_count: int


@dataclass(frozen=True)
class CandidateGateResult:
    snapshot_id: str
    status: str
    checked_at: float
    failure_reason: str | None = None


@dataclass(frozen=True)
class CompilationMetric:
    stage: str
    source_count: int
    record_count: int
    call_count: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    remote_calls: int
    failure_count: int
    reasoning_tokens: int = 0
    model_version: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
