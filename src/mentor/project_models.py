"""Small typed contracts shared by Phase 6 project and source boundaries."""

from dataclasses import dataclass
from enum import StrEnum


class ThreadSourceBehavior(StrEnum):
    LEGACY_JACOB = "LEGACY_JACOB"
    GENERAL_NEUTRAL = "GENERAL_NEUTRAL"
    PROJECT = "PROJECT"


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class AuthorityKind(StrEnum):
    MENTOR = "MENTOR"
    USER_NOTES = "USER_NOTES"
    SYSTEM = "SYSTEM"


class CanonicalRole(StrEnum):
    CURRENT_CANONICAL_ADVANCED = "CURRENT_CANONICAL_ADVANCED"
    CURRENT_CANONICAL_FOUNDATION = "CURRENT_CANONICAL_FOUNDATION"
    GARRETT_ARCHIVAL_AND_COMPLEMENTARY = "GARRETT_ARCHIVAL_AND_COMPLEMENTARY"


class ResearchDepth(StrEnum):
    NORMAL = "NORMAL"
    DEEP = "DEEP"
    EXHAUSTIVE = "EXHAUSTIVE"


@dataclass(frozen=True)
class StrategyProject:
    id: int
    name: str
    status: ProjectStatus


@dataclass(frozen=True)
class ThreadContext:
    id: int
    title: str
    thread_source_behavior: ThreadSourceBehavior
    project_id: int | None


@dataclass(frozen=True)
class SourceLibrary:
    id: int
    library_key: str
    corpus_key: str
    authority_name: str
    authority_kind: AuthorityKind
    display_name: str
    status: str


@dataclass(frozen=True)
class ProjectSourceScope:
    project_id: int
    library_keys: tuple[str, ...]
    temporary: bool = False


@dataclass(frozen=True)
class SearchBudget:
    per_library_passes: int
    overall_passes: int
    results_per_pass: int

    def __post_init__(self) -> None:
        if min(self.per_library_passes, self.overall_passes, self.results_per_pass) < 1:
            raise ValueError("search budget values must be positive")


@dataclass(frozen=True)
class PendingPromotion:
    id: int
    project_id: int
    proposed_rule: str
    status: str
