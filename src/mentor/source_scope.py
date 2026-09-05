"""Deterministic per-turn source access for Strategy Project conversations."""

from dataclasses import dataclass
import re

from mentor.project_models import SearchBudget, ThreadContext, ThreadSourceBehavior
from mentor.storage import Storage


_BUDGETS = {
    "normal": SearchBudget(1, 6, 8),
    "deep": SearchBudget(2, 12, 12),
    "exhaustive": SearchBudget(3, 18, 16),
}
_LABELS = {
    "garrett": "gxt.garrett",
    "afyz": "gxt.afyz",
    "erik": "gxt.erik",
    "splash": "gxt.splash",
    "zay": "gxt.zay",
    "theo notes": "gxt.theo_notes",
    "jacob": "jacob.speculates",
}
_LABEL_PATTERN = "|".join(re.escape(label) for label in sorted(_LABELS, key=len, reverse=True))
_ONLY = re.compile(
    rf"^\s*(?:use\s+)?(?P<label>{_LABEL_PATTERN})\s+only(?:\s+for\s+this\s+answer)?[.!?]?\s*$",
    re.IGNORECASE,
)
_COMPARE = re.compile(
    rf"\bcompare\s+(?P<first>{_LABEL_PATTERN})\s+and\s+(?P<second>{_LABEL_PATTERN})"
    rf"(?:\s*,?\s*ignore\s+(?P<ignored>{_LABEL_PATTERN}))?\b",
    re.IGNORECASE,
)
_ALL_ENABLED = re.compile(r"^\s*use\s+all\s+enabled\s+mentors\s+again[.!?]?\s*$", re.IGNORECASE)
_CURRENT_GARRETT = re.compile(r"\bgarrett\b.*\b(?:current|currently|now)\b|\b(?:current|currently)\b.*\bgarrett\b", re.IGNORECASE)
_SOURCE_INTENT = re.compile(
    r"\b(?:gxt|mentor|source|teach|teaching|explain|compare|comparison|concept|model|system|according|timestamp|video)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ScopedLibrary:
    library_key: str
    display_name: str
    vector_store_id: str


@dataclass(frozen=True)
class ResolvedSourceScope:
    project_id: int | None
    libraries: tuple[ScopedLibrary, ...]
    temporary: bool = False
    override: str = "saved"
    garrett_current_first: bool = False

    @property
    def library_keys(self) -> tuple[str, ...]:
        return tuple(library.library_key for library in self.libraries)

    @property
    def vector_store_ids(self) -> tuple[str, ...]:
        return tuple(library.vector_store_id for library in self.libraries)

    def safe_snapshot(self) -> dict[str, object]:
        return {
            "library_keys": list(self.library_keys),
            "temporary": self.temporary,
            "override": self.override,
        }


@dataclass(frozen=True)
class SearchPass:
    library_key: str
    pass_number: int
    results_per_pass: int


def search_budget(depth: str) -> SearchBudget:
    try:
        return _BUDGETS[depth.casefold()]
    except (AttributeError, KeyError):
        raise ValueError("research depth is invalid") from None


def resolve_source_scope(
    storage: Storage, thread: ThreadContext, question: str
) -> ResolvedSourceScope:
    if thread.thread_source_behavior is not ThreadSourceBehavior.PROJECT:
        return ResolvedSourceScope(None, ())
    if thread.project_id is None:
        raise ValueError("project conversation has no project")
    available = {
        row[0]: ScopedLibrary(row[0], row[1], row[2])
        for row in storage.project_library_access(thread.project_id)
        if row[3]
    }
    selected = set(available)
    temporary = False
    override = "saved"
    normalized = " ".join(question.split())
    if _ALL_ENABLED.fullmatch(normalized):
        temporary, override = True, "all_enabled"
    elif match := _ONLY.fullmatch(normalized):
        key = _LABELS[match.group("label").casefold()]
        if key not in available:
            raise ValueError(f"{match.group('label').title()} is not enabled for this project.")
        selected, temporary, override = {key}, True, "only"
    elif match := _COMPARE.search(normalized):
        requested = {
            _LABELS[match.group("first").casefold()],
            _LABELS[match.group("second").casefold()],
        }
        unavailable = requested - available.keys()
        if unavailable:
            raise ValueError("A requested mentor is not enabled for this project.")
        selected = requested
        ignored = match.group("ignored")
        if ignored:
            selected.discard(_LABELS[ignored.casefold()])
        temporary, override = True, "compare"
    if len(selected) > 6:
        raise ValueError("Choose up to six source libraries for this answer.")
    libraries = tuple(available[key] for key in sorted(selected))
    return ResolvedSourceScope(
        thread.project_id,
        libraries,
        temporary,
        override,
        bool(_CURRENT_GARRETT.search(normalized)),
    )


def research_plan(
    scope: ResolvedSourceScope, question: str, depth: str
) -> tuple[SearchPass, ...]:
    if not scope.libraries or not _SOURCE_INTENT.search(question):
        return ()
    budget = search_budget(depth)
    return tuple(
        SearchPass(library.library_key, pass_number, budget.results_per_pass)
        for pass_number in range(1, budget.per_library_passes + 1)
        for library in scope.libraries
    )[: budget.overall_passes]
