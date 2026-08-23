"""Immutable local identities for candidate corpus compilation."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Sequence

from mentor.knowledge import SourceRevision


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
            run.run_id, [revision.revision_id for revision in selected_revisions]
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
    def identity_for(run_id: str, selected_revision_ids: Sequence[str]) -> tuple[tuple[str, ...], str, str]:
        revision_ids = tuple(sorted(selected_revision_ids))
        if not revision_ids or len(set(revision_ids)) != len(revision_ids):
            raise ValueError("selected revisions must be non-empty and unique")
        fingerprint = sha256("\n".join(revision_ids).encode()).hexdigest()
        snapshot_id = f"snap_{sha256(f'{run_id}\0{fingerprint}'.encode()).hexdigest()}"
        return revision_ids, fingerprint, snapshot_id


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
    model_version: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
