"""Deterministic per-turn source access for Strategy Project conversations."""

from dataclasses import dataclass
import re

from mentor.project_models import PedagogicalRole, SearchBudget, ThreadContext, ThreadSourceBehavior
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
    rf"(?:^\s*|\buse\s+)(?P<label>{_LABEL_PATTERN})\s+only(?:\s+for\s+this\s+answer)?\b",
    re.IGNORECASE,
)
_COMPARE = re.compile(
    rf"\bcompare\s+(?P<first>{_LABEL_PATTERN})\s+(?:and|with|to|versus|vs\.?)\s+(?P<second>{_LABEL_PATTERN})"
    rf"(?:\s*,?\s*ignore\s+(?P<ignored>{_LABEL_PATTERN}))?\b",
    re.IGNORECASE,
)
_ALL_ENABLED = re.compile(
    r"\b(?:use\s+)?all(?:\s+five)?(?:\s+enabled)?\s+mentors\b|\b(?:each|every)\s+mentor\b",
    re.IGNORECASE,
)
_NEGATED_ALL = re.compile(
    r"\b(?:do not|don['’]t|dont|not)\s+(?:(?:need|want|use)\s+)?(?:use\s+)?"
    r"all(?:\s+five)?(?:\s+enabled)?\s+mentors\b",
    re.IGNORECASE,
)
_CROSS_MENTOR = re.compile(r"\b(?:other|all|each|every)\s+(?:enabled\s+)?mentors?\b", re.IGNORECASE)
_CURRENT_GARRETT = re.compile(r"\bgarrett\b.*\b(?:current|currently|now)\b|\b(?:current|currently)\b.*\bgarrett\b", re.IGNORECASE)
_SOURCE_INTENT = re.compile(
    r"\b(?:gxt|mentor|source|transcript|citation|teach|teaching|explain|compare|comparison|"
    r"concept|model|system|according|timestamp|video|lesson)\b",
    re.IGNORECASE,
)
_MENTOR_REFERENCE = re.compile(rf"\b(?:{_LABEL_PATTERN})\b", re.IGNORECASE)
_LEARNER_CHECK = re.compile(
    r"\b(?:is|was)\s+(?:that|this|my\s+(?:answer|analysis))\s+(?:correct|right|wrong)\b|"
    r"\bwhat\s+(?:did|have)\s+i\s+(?:get|got)\s+(?:right|wrong)\b",
    re.IGNORECASE,
)
_PRACTICE_REQUEST = re.compile(r"\b(?:exercise|practice)\b", re.IGNORECASE)
_APPLICATION_REQUEST = re.compile(
    r"\bdoes\s+(?:that|this)\s+mean\s+i\s+should\b", re.IGNORECASE
)
_TIMEFRAME_REQUEST = re.compile(
    r"\bwhich\s+timeframe\b|\btimeframe\b.*\b(?:use|role|selection|exact)\b",
    re.IGNORECASE,
)
_VISUAL_CAPABILITY = re.compile(
    r"\bcan\s+you\s+(?:verify|inspect|see|look\s+at|analy[sz]e)\b.*"
    r"\b(?:chart\s+on\s+my\s+screen|screenshot|image)\b",
    re.IGNORECASE,
)
_SCOPE_SELECTION_ONLY = re.compile(
    rf"^\s*(?:use\s+)?(?:{_LABEL_PATTERN})\s+only"
    rf"(?:\s+for\s+this\s+answer)?\s*[.!]?\s*$",
    re.IGNORECASE,
)
_CONTEXTUAL_SOURCE_INTENT = re.compile(
    r"\b(?:lesson|exercise|timeframe|signature|correction|correct|incorrect|wrong|"
    r"contradict|contradiction|challenge|verify|support|evidence|claim|video|timestamp)\b",
    re.IGNORECASE,
)
_INLINE_QUOTE = re.compile(
    r'"[^"]*"|“[^”]*”|(?<!\w)\'[^\']+\'(?!\w)', re.DOTALL
)
_BLOCK_QUOTE = re.compile(r"(?m)^\s*>.*$")


@dataclass(frozen=True)
class ScopedLibrary:
    library_key: str
    display_name: str
    vector_store_id: str
    pedagogical_role: PedagogicalRole


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


@dataclass(frozen=True)
class ConversationResearchContext:
    query: str
    scope_text: str
    source_required: bool
    inherited_library_keys: tuple[str, ...] = ()


def conversation_research_context(
    question: str, recent_turns: list[dict] | tuple[dict, ...] = ()
) -> ConversationResearchContext:
    """Resolve a follow-up into bounded search context; historic prose is never evidence."""
    source_turn = next(
        (
            turn for turn in reversed(recent_turns)
            if turn.get("citations") or (turn.get("diagnostics") or {}).get("project_source_research")
        ),
        None,
    )
    affirmative = _has_source_intent(question)
    contextual = (
        source_turn is not None
        and _CONTEXTUAL_SOURCE_INTENT.search(question) is not None
        and (_VISUAL_CAPABILITY.search(question) is None or affirmative)
    )
    source_required = affirmative or contextual
    query = question
    if contextual:
        snippets = []
        for turn in recent_turns[-2:]:
            user_text = " ".join(str(turn.get("user_text") or "").split())[:600]
            answer = " ".join(str(turn.get("answer_markdown") or "").split())[:800]
            if user_text:
                snippets.append(f"Theo previously asked: {user_text}")
            if answer:
                snippets.append(f"The prior mentor answered (unverified context): {answer}")
        if snippets:
            query = (
                f"{question}\n\nRecent lesson context for query disambiguation only; verify all claims "
                f"against raw sources:\n" + "\n".join(snippets)
            )
    prior_scope = (source_turn or {}).get("source_scope") or {}
    inherited = tuple(prior_scope.get("library_keys") or ()) if contextual else ()
    scope_text = _INLINE_QUOTE.sub("", _BLOCK_QUOTE.sub("", question))
    return ConversationResearchContext(query, scope_text, source_required, inherited)


def search_budget(depth: str) -> SearchBudget:
    try:
        return _BUDGETS[depth.casefold()]
    except (AttributeError, KeyError):
        raise ValueError("research depth is invalid") from None


def resolve_source_scope(
    storage: Storage,
    thread: ThreadContext,
    question: str,
    *,
    inherited_library_keys: tuple[str, ...] = (),
) -> ResolvedSourceScope:
    if thread.thread_source_behavior is not ThreadSourceBehavior.PROJECT:
        return ResolvedSourceScope(None, ())
    if thread.project_id is None:
        raise ValueError("project conversation has no project")
    available = {
        row[0]: ScopedLibrary(row[0], row[1], row[2], PedagogicalRole(row[3]))
        for row in storage.project_library_access(thread.project_id)
        if row[4]
    }
    selected = set(available)
    temporary = False
    override = "saved"
    normalized = " ".join(question.split())
    scope_text = _NEGATED_ALL.sub("", normalized)
    explicit_scope = False
    if match := _COMPARE.search(scope_text):
        explicit_scope = True
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
    elif match := _ONLY.search(scope_text):
        explicit_scope = True
        key = _LABELS[match.group("label").casefold()]
        if key not in available:
            raise ValueError(f"{match.group('label').title()} is not enabled for this project.")
        selected, temporary, override = {key}, True, "only"
    elif _ALL_ENABLED.search(scope_text):
        explicit_scope = True
        temporary, override = True, "all_enabled"
    elif not _CROSS_MENTOR.search(scope_text) and re.search(r"\bcompare\b", scope_text, re.IGNORECASE) is None:
        requested = {
            key
            for label, key in _LABELS.items()
            if re.search(rf"(?<!\w){re.escape(label)}(?!\w)", scope_text, re.IGNORECASE)
        }
        if requested:
            explicit_scope = True
            unavailable = requested - available.keys()
            if unavailable:
                raise ValueError("A requested mentor is not enabled for this project.")
            selected, temporary, override = requested, True, "only" if len(requested) == 1 else "compare"
    if not explicit_scope and inherited_library_keys:
        continued = {key for key in inherited_library_keys if key in available}
        if continued:
            selected, temporary, override = continued, True, "continued"
    if len(selected) > 6:
        raise ValueError("Choose up to six source libraries for this answer.")
    libraries = tuple(library for key, library in available.items() if key in selected)
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
    if not scope.libraries or not _has_source_intent(question):
        return ()
    budget = search_budget(depth)
    return tuple(
        SearchPass(library.library_key, pass_number, budget.results_per_pass)
        for pass_number in range(1, budget.per_library_passes + 1)
        for library in scope.libraries
    )[: budget.overall_passes]


def _has_source_intent(question: str) -> bool:
    if _SCOPE_SELECTION_ONLY.fullmatch(question):
        return False
    return any(pattern.search(question) for pattern in (
        _SOURCE_INTENT,
        _MENTOR_REFERENCE,
        _LEARNER_CHECK,
        _PRACTICE_REQUEST,
        _APPLICATION_REQUEST,
        _TIMEFRAME_REQUEST,
    ))
