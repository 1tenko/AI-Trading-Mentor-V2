"""Local trader-profile validation, lifecycle operations, and prompt selection."""

import re
from dataclasses import dataclass
from typing import Iterable

from .storage import Storage, TraderProfileItem


@dataclass(frozen=True)
class QuestionnaireField:
    key: str
    section: str
    question: str
    helper: str
    category: str
    subject: str
    kind: str
    priority: int


@dataclass(frozen=True)
class QuestionnaireAnswer:
    item: TraderProfileItem
    unknown: bool


@dataclass(frozen=True)
class StrategyProfileSelection:
    items: tuple[TraderProfileItem, ...]
    context: str
    character_count: int


QUESTIONNAIRE_FIELDS = (
    QuestionnaireField("q1", "Your goals", "What are you actually trying to achieve with trading?", "What would success look like for you?", "goals/research", "trading objective", "goal", 1),
    QuestionnaireField("q2", "Your goals", "What markets are you willing or interested in trading?", "For example: ES, NQ, YM, GC, crypto, forex.", "markets/instruments", "markets willing to trade", "preference", 2),
    QuestionnaireField("q3", "Your trading life", "When can you realistically be at the screen to trade?", "Which sessions or times work for you?", "schedule/horizon", "trading availability", "constraint", 3),
    QuestionnaireField("q4", "Your trading life", "What kind of trader do you think you want to be?", "For example: scalper, intraday, swing trader.", "style/methodology", "preferred trading style", "preference", 4),
    QuestionnaireField("q5", "Your trading life", "How long would you ideally like your trades to last?", "Seconds, minutes, hours, or longer. 'idk' is fine.", "schedule/horizon", "preferred holding duration", "preference", 5),
    QuestionnaireField("q6", "Your trading life", "How many trades would you ideally like to take?", "Include how you feel about no-trade days.", "execution/risk/constraints", "preferred trade frequency", "preference", 12),
    QuestionnaireField("q7", "How you like to trade", "How much discretion do you want in your strategy?", "Mechanical rules, some judgement, or highly discretionary.", "preferences/discretion", "discretion preference", "preference", 6),
    QuestionnaireField("q8", "How you like to trade", "What do you think you are naturally good at in trading?", "'I don't know yet' is a valid answer.", "strengths/difficulties/principles", "trading strengths", "fact", 16),
    QuestionnaireField("q9", "How you like to trade", "What tends to go wrong for you in trading?", "For example: overtrading, hesitation, impatience, or breaking risk rules.", "strengths/difficulties/principles", "recurring trading difficulties", "fact", 17),
    QuestionnaireField("q10", "How you like to trade", "What trading concepts or models do you want your eventual strategy to draw from?", "List what you want to work with right now.", "style/methodology", "concepts/models to build from", "learning-state", 15),
    QuestionnaireField("q11", "How you like to trade", "Which concepts do you currently trust most, and which are you still unsure about?", "It is fine if the answer is 'I don't know'.", "experience/learning", "trusted and uncertain concepts", "learning-state", 18),
    QuestionnaireField("q12", "How you like to trade", "What does your ideal trade or setup look like?", "Describe the kind of trade you would like to take repeatedly.", "style/methodology", "ideal setup", "preference", 14),
    QuestionnaireField("q13", "How you like to trade", "What risk or funding constraints should your strategy respect?", "For example: drawdown rules, risk per trade, daily loss limit.", "execution/risk/constraints", "risk and funding constraints", "constraint", 7),
    QuestionnaireField("q14", "How you like to trade", "What matters most to you in a strategy?", "Think about win rate, reward:risk, simplicity, drawdown, or consistency.", "goals/research", "strategy priorities", "preference", 8),
    QuestionnaireField("q15", "How you like to trade", "What are your absolute strategy deal-breakers?", "For example: too much screen time or overnight positions.", "execution/risk/constraints", "strategy deal-breakers", "constraint", 9),
    QuestionnaireField("q16", "Your research process", "How much backtesting or data collection are you realistically willing to do?", "For example: manual replay, spreadsheets, or 30 examples at a time.", "goals/research", "backtesting commitment", "constraint", 13),
    QuestionnaireField("q17", "Your research process", "What do you currently suspect might actually have an edge?", "This is an unproven hypothesis, not a fact.", "goals/research", "suspected edge", "learning-state", 19),
    QuestionnaireField("q18", "Your research process", "What are you most confused or uncertain about right now?", "For example: setup focus, bias, or which variables matter.", "strengths/difficulties/principles", "current trading uncertainties", "learning-state", 11),
    QuestionnaireField("q19", "Your ideal system", "If someone could design your perfect trading strategy tomorrow, what would it look like?", "Describe it without worrying whether it is realistic yet.", "style/methodology", "ideal strategy", "preference", 10),
    QuestionnaireField("q20", "Your ideal system", "When there are trade-offs, what should the Mentor optimise around for you?", "For example: simplicity, consistency, mechanical rules, or fewer trades.", "strengths/difficulties/principles", "optimisation principles", "principle", 20),
    QuestionnaireField("additional_information", "Additional information", "Anything else the Mentor should know about you as a trader?", "You can leave this blank.", "strengths/difficulties/principles", "additional trader context", "fact", 21),
)
QUESTIONNAIRE_BY_KEY = {field.key: field for field in QUESTIONNAIRE_FIELDS}
_UNKNOWN_ANSWER = re.compile(r"^(idk|i\s*(do not|don't|dont)\s*know|not sure|unsure|not decided|haven'?t decided|have not decided)$", re.I)


CATEGORIES = (
    "goals/research",
    "markets/instruments",
    "schedule/horizon",
    "style/methodology",
    "execution/risk/constraints",
    "experience/learning",
    "preferences/discretion",
    "strengths/difficulties/principles",
)
KINDS = ("fact", "preference", "constraint", "goal", "principle", "learning-state")
PROVENANCE = ("USER_STATED", "USER_CONFIRMED", "AI_INFERRED", "USER_DECISION")
STATES = ("confirmed", "tentative", "superseded", "conflicting", "archived")
ORIGIN_KINDS = ("chat", "profile-editor", "confirmation")

_SOURCE_QUESTION_TERMS = ("timestamp", "transcript", "citation", "source", "exact quote", "jacob")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_TOKEN_STOP_WORDS = {"a", "an", "and", "do", "for", "how", "i", "in", "is", "it", "of", "the", "this", "to", "what"}
_DISTINCTIVE_VALUES = frozenset({"es", "nq", "london", "scalping"})
_SUBJECT_FAMILIES = {
    "market/instrument": ("market", "instrument", "primary market", "secondary market", "markets traded"),
    "available session": ("available session", "trading session", "session"),
    "available time": ("available time", "trading time"),
    "holding horizon": ("holding horizon", "holding period", "holding time"),
    "execution/risk constraint": ("maximum risk", "risk limit", "risk constraint", "execution constraint"),
    "discretion constraint": ("discretion constraint", "discretion preference", "alert preference"),
    "research goal": ("research goal", "research focus", "backtest focus", "current research objective", "market research"),
    "trading goal": ("trading goal",),
    "learning goal": ("learning goal",),
    "learning state/difficulty": ("learning state", "difficulty", "strength", "struggle"),
    "style/methodology": ("entry style", "exit style", "trading style", "methodology"),
}
_INTENT_POLICIES = {
    "research/backtest/strategy development": {
        "categories": frozenset({"goals/research", "markets/instruments", "schedule/horizon", "execution/risk/constraints", "preferences/discretion", "style/methodology"}),
        "structural": frozenset({
            ("goals/research", "goal", "research goal"),
            ("goals/research", "constraint", "research goal"),
            ("markets/instruments", "fact", "market/instrument"),
            ("markets/instruments", "preference", "market/instrument"),
            ("markets/instruments", "constraint", "market/instrument"),
            ("schedule/horizon", "constraint", "available session"),
            ("schedule/horizon", "preference", "available session"),
            ("schedule/horizon", "constraint", "available time"),
            ("schedule/horizon", "preference", "available time"),
            ("schedule/horizon", "constraint", "holding horizon"),
            ("schedule/horizon", "preference", "holding horizon"),
            ("execution/risk/constraints", "constraint", "execution/risk constraint"),
            ("execution/risk/constraints", "principle", "execution/risk constraint"),
            ("preferences/discretion", "constraint", "discretion constraint"),
            ("preferences/discretion", "preference", "discretion constraint"),
        }),
    },
    "execution/trading plan": {
        "categories": frozenset({"markets/instruments", "schedule/horizon", "execution/risk/constraints", "preferences/discretion", "style/methodology", "goals/research"}),
        "structural": frozenset({
            ("markets/instruments", kind, "market/instrument") for kind in ("fact", "preference", "constraint")
        } | {
            ("schedule/horizon", kind, family)
            for kind in ("constraint", "preference")
            for family in ("available session", "available time", "holding horizon")
        } | {
            ("execution/risk/constraints", kind, "execution/risk constraint") for kind in ("constraint", "principle")
        } | {
            ("preferences/discretion", kind, "discretion constraint") for kind in ("constraint", "preference")
        } | {("goals/research", "goal", "trading goal")}),
    },
    "learning/methodology": {
        "categories": frozenset({"experience/learning", "strengths/difficulties/principles", "style/methodology", "goals/research"}),
        "structural": frozenset({
            ("goals/research", "goal", "learning goal"),
            ("experience/learning", "learning-state", "learning state/difficulty"),
            ("strengths/difficulties/principles", "principle", "learning state/difficulty"),
        }),
    },
}
_INTENT_POLICIES["general trading advice"] = {
    "categories": frozenset(CATEGORIES),
    "structural": frozenset(),
}


class ProfileValidationError(ValueError):
    """Raised before an invalid or ambiguous profile write reaches storage."""


@dataclass(frozen=True)
class ProfileSelection:
    items: tuple[TraderProfileItem, ...]
    item_ids: tuple[int, ...]
    context: str
    character_count: int
    intents: tuple[str, ...]
    reasons: tuple[str, ...]
    tiers: tuple[int, ...]


class ProfileService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def create_item(
        self,
        *,
        category: str,
        subject: str,
        value: str,
        kind: str,
        provenance: str,
        state: str,
        origin_kind: str,
        origin_thread_id: int | None = None,
        origin_turn_number: int | None = None,
        origin_available: bool | None = None,
        tool_call_id: str | None = None,
    ) -> TraderProfileItem:
        category, subject, value, kind, provenance, state, origin_kind = _validate_item(
            category=category,
            subject=subject,
            value=value,
            kind=kind,
            provenance=provenance,
            state=state,
            origin_kind=origin_kind,
            origin_thread_id=origin_thread_id,
            origin_turn_number=origin_turn_number,
            origin_available=origin_available,
        )
        if provenance == "USER_CONFIRMED":
            raise ProfileValidationError("USER_CONFIRMED records must use confirm_item")
        if tool_call_id:
            existing = self.storage.profile_item_for_tool_call(tool_call_id)
            if existing is not None:
                return existing
        if state == "confirmed" and self._current_for(category, subject):
            raise ProfileValidationError("a confirmed record already exists; use supersede_item")
        return self.storage.create_profile_item(
            category=category,
            subject=subject,
            value=value,
            kind=kind,
            provenance=provenance,
            state=state,
            origin_kind=origin_kind,
            origin_thread_id=origin_thread_id,
            origin_turn_number=origin_turn_number,
            origin_available=origin_available,
            tool_call_id=tool_call_id,
        )

    def propose_item(self, **item: str) -> TraderProfileItem:
        return self.create_item(
            **item,
            provenance="AI_INFERRED",
            state="tentative",
        )

    def confirm_item(
        self,
        item_id: int,
        *,
        origin_kind: str,
        origin_thread_id: int | None = None,
        origin_turn_number: int | None = None,
        origin_available: bool | None = None,
    ) -> TraderProfileItem:
        item = self._item(item_id)
        if item.state != "tentative" or item.provenance != "AI_INFERRED":
            raise ProfileValidationError("only a tentative AI_INFERRED record can be confirmed")
        _validate_origin(origin_kind, origin_thread_id, origin_turn_number, origin_available)
        if self._current_for(item.category, item.subject):
            raise ProfileValidationError("a confirmed record already exists for this subject")
        return self.storage.supersede_profile_item(
            item_id,
            value=item.value,
            provenance="USER_CONFIRMED",
            origin_kind=origin_kind,
            origin_thread_id=origin_thread_id,
            origin_turn_number=origin_turn_number,
            origin_available=origin_available,
        )

    def supersede_item(
        self,
        item_id: int,
        *,
        value: str,
        provenance: str,
        origin_kind: str,
        origin_thread_id: int | None = None,
        origin_turn_number: int | None = None,
        origin_available: bool | None = None,
    ) -> TraderProfileItem:
        predecessor = self._item(item_id)
        if predecessor.state != "confirmed":
            raise ProfileValidationError("only a confirmed record is a safe successor target")
        _, _, value, _, provenance, _, origin_kind = _validate_item(
            category=predecessor.category,
            subject=predecessor.subject,
            value=value,
            kind=predecessor.kind,
            provenance=provenance,
            state="confirmed",
            origin_kind=origin_kind,
            origin_thread_id=origin_thread_id,
            origin_turn_number=origin_turn_number,
            origin_available=origin_available,
        )
        if provenance == "USER_CONFIRMED":
            raise ProfileValidationError("USER_CONFIRMED records must use confirm_item")
        return self.storage.supersede_profile_item(
            item_id,
            value=value,
            provenance=provenance,
            origin_kind=origin_kind,
            origin_thread_id=origin_thread_id,
            origin_turn_number=origin_turn_number,
            origin_available=origin_available,
        )

    def archive_item(self, item_id: int) -> bool:
        self._item(item_id)
        return self.storage.archive_profile_item(item_id)

    def conflict_items(self, item_ids: Iterable[int]) -> int:
        requested_ids = list(dict.fromkeys(item_ids))
        try:
            return self.storage.conflict_profile_items(requested_ids)
        except ValueError as error:
            raise ProfileValidationError(str(error)) from error

    def resolve_conflict(self, item_id: int) -> TraderProfileItem:
        """Make the user's explicitly chosen conflicting item current again."""
        predecessor = self._item(item_id)
        if predecessor.state != "conflicting":
            raise ProfileValidationError("only a conflicting profile item can be resolved")
        if self._current_for(predecessor.category, predecessor.subject):
            raise ProfileValidationError("a confirmed record already exists for this subject")
        return self.storage.supersede_profile_item(
            item_id,
            value=predecessor.value,
            provenance="USER_DECISION",
            origin_kind="confirmation",
        )

    def delete_item(self, item_id: int) -> bool:
        self._item(item_id)
        return self.storage.delete_profile_item(item_id)

    def questionnaire_answers(self) -> dict[str, QuestionnaireAnswer | None]:
        current = {
            (item.category, item.subject_key): item
            for item in self.storage.current_confirmed_profile_items()
        }
        return {
            field.key: (
                None
                if (item := current.get((field.category, _subject_key(field.subject)))) is None
                else QuestionnaireAnswer(item, _is_unknown(item.value))
            )
            for field in QUESTIONNAIRE_FIELDS
        }

    def save_questionnaire_answers(self, answers: dict[str, str]) -> dict[str, TraderProfileItem]:
        if not isinstance(answers, dict):
            raise ProfileValidationError("questionnaire answers must be an object")
        unknown = set(answers).difference(QUESTIONNAIRE_BY_KEY)
        if unknown:
            raise ProfileValidationError("unknown questionnaire field")
        changes = []
        for key, value in answers.items():
            if not isinstance(value, str):
                raise ProfileValidationError("questionnaire answers must be text")
            field = QUESTIONNAIRE_BY_KEY[key]
            normalized = value.strip()
            if normalized:
                _bounded_text("value", normalized, 500)
            changes.append((field, normalized))
        try:
            saved = self.storage.save_questionnaire_answers(changes)
        except (KeyError, ValueError) as error:
            raise ProfileValidationError(str(error)) from error
        return {key: item for key, item in saved.items()}

    def forget_item(
        self,
        *,
        item_id: int,
        operation: str,
        tool_call_id: str,
        origin_thread_id: int,
        origin_turn_number: int,
    ) -> str:
        if operation not in {"archive", "delete"}:
            raise ProfileValidationError("chat may only archive or delete a profile item")
        if not isinstance(item_id, int) or item_id <= 0:
            raise ProfileValidationError("profile target must be a positive item id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise ProfileValidationError("profile tool call id is required")
        _validate_origin("chat", origin_thread_id, origin_turn_number, True)
        try:
            return self.storage.apply_profile_forget_operation(
                tool_call_id=tool_call_id,
                operation=operation,
                target_item_id=item_id,
                origin_thread_id=origin_thread_id,
                origin_turn_number=origin_turn_number,
            )
        except (KeyError, ValueError) as error:
            raise ProfileValidationError(str(error)) from error

    def _item(self, item_id: int) -> TraderProfileItem:
        item = self.storage.profile_item(item_id)
        if item is None:
            raise ProfileValidationError(f"profile item {item_id} does not exist")
        return item

    def _current_for(self, category: str, subject: str) -> list[TraderProfileItem]:
        subject_key = _subject_key(subject)
        return [
            item
            for item in self.storage.current_confirmed_profile_items()
            if item.category == category and item.subject_key == subject_key
        ]


def select_profile_context(
    question: str, confirmed_items: Iterable[TraderProfileItem]
) -> ProfileSelection:
    """Return a bounded, deterministic profile orientation for one question."""
    intents = _question_intents(question)
    policies = tuple(_INTENT_POLICIES[intent] for intent in intents if intent in _INTENT_POLICIES)
    if not policies:
        return ProfileSelection((), (), "", 0, intents, (), ())

    question_tokens = _profile_tokens(question)
    question_families = _question_subject_families(question)
    deduplicated: dict[tuple[str, str], TraderProfileItem] = {}
    for item in confirmed_items:
        if item.state != "confirmed" or not any(item.category in policy["categories"] for policy in policies):
            continue
        key = (item.category, item.subject_key)
        if key not in deduplicated or item.id < deduplicated[key].id:
            deduplicated[key] = item

    applicable = [
        (item, _applicability(item, policies, question.casefold(), question_tokens, question_families))
        for item in deduplicated.values()
    ]
    applicable = [(item, result) for item, result in applicable if result is not None]
    applicable = _keep_specific_category_matches(applicable)
    applicable.sort(key=_selection_sort_key)

    selected: list[TraderProfileItem] = []
    reasons: list[str] = []
    tiers: list[int] = []
    lines: list[str] = []
    length = 0
    for item, (tier, reason) in applicable:
        line = f"- {item.subject}: {item.value}"
        next_length = length + (1 if lines else 0) + len(line)
        if len(selected) == 6 or next_length > 1200:
            break
        selected.append(item)
        reasons.append(reason)
        tiers.append(tier)
        lines.append(line)
        length = next_length
    context = "\n".join(lines)
    return ProfileSelection(
        tuple(selected),
        tuple(item.id for item in selected),
        context,
        len(context),
        intents,
        tuple(reasons),
        tuple(tiers),
    )


def strategy_profile_context(confirmed_items: Iterable[TraderProfileItem]) -> StrategyProfileSelection:
    """Render current questionnaire answers for an explicit strategy-design request."""
    fields = {
        (field.category, _subject_key(field.subject)): field
        for field in QUESTIONNAIRE_FIELDS
    }
    pairs = [
        (fields[(item.category, item.subject_key)], item)
        for item in confirmed_items
        if item.state == "confirmed" and (item.category, item.subject_key) in fields
    ]
    pairs.sort(key=lambda pair: (pair[0].priority, pair[0].key, pair[1].id))
    lines = ["Trader Strategy Profile — user context, not source evidence:"]
    selected: list[TraderProfileItem] = []
    for field, item in pairs:
        value = "User is currently unsure / has not decided." if _is_unknown(item.value) else item.value
        line = f"- {field.subject}: {value}"
        if len("\n".join([*lines, line])) > 6000:
            continue
        lines.append(line)
        selected.append(item)
    context = "\n".join(lines) if selected else ""
    return StrategyProfileSelection(tuple(selected), context, len(context))


def _is_unknown(value: str) -> bool:
    return bool(_UNKNOWN_ANSWER.fullmatch(" ".join(value.casefold().split())))


def _validate_item(
    *,
    category: str,
    subject: str,
    value: str,
    kind: str,
    provenance: str,
    state: str,
    origin_kind: str,
    origin_thread_id: int | None,
    origin_turn_number: int | None,
    origin_available: bool | None,
) -> tuple[str, str, str, str, str, str, str]:
    _controlled("category", category, CATEGORIES)
    _controlled("kind", kind, KINDS)
    _controlled("provenance", provenance, PROVENANCE)
    _controlled("state", state, STATES)
    _validate_origin(origin_kind, origin_thread_id, origin_turn_number, origin_available)
    subject = _bounded_text("subject", subject, 120)
    value = _bounded_text("value", value, 500)
    if provenance == "AI_INFERRED" and state != "tentative":
        raise ProfileValidationError("AI_INFERRED records must be tentative")
    if state == "tentative" and provenance != "AI_INFERRED":
        raise ProfileValidationError("tentative records must be AI_INFERRED")
    if state == "confirmed" and provenance == "AI_INFERRED":
        raise ProfileValidationError("AI_INFERRED records must be tentative")
    return category, subject, value, kind, provenance, state, origin_kind


def _controlled(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ProfileValidationError(f"{name} must be one of: {', '.join(allowed)}")


def _bounded_text(name: str, value: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ProfileValidationError(f"{name} must be text")
    normalized = " ".join(value.split()) if name == "subject" else value.strip()
    if not normalized or len(normalized) > limit:
        raise ProfileValidationError(f"{name} must contain 1 to {limit} characters")
    return normalized


def _validate_origin(
    origin_kind: str,
    origin_thread_id: int | None,
    origin_turn_number: int | None,
    origin_available: bool | None,
) -> None:
    _controlled("origin_kind", origin_kind, ORIGIN_KINDS)
    if (origin_thread_id is None) != (origin_turn_number is None):
        raise ProfileValidationError("origin thread and turn must be provided together")
    if origin_thread_id is not None and (origin_thread_id <= 0 or origin_turn_number is None or origin_turn_number <= 0):
        raise ProfileValidationError("origin thread and turn must be positive")
    if origin_thread_id is None and origin_available is True:
        raise ProfileValidationError("an unavailable origin cannot be marked available")
    if origin_available is not None and not isinstance(origin_available, bool):
        raise ProfileValidationError("origin_available must be boolean")


def _subject_key(subject: str) -> str:
    return " ".join(subject.split()).casefold()


def _question_intents(question: str) -> tuple[str, ...]:
    normalized = question.casefold()
    source_lookup = any(term in normalized for term in _SOURCE_QUESTION_TERMS) or "mean by" in normalized
    families = _question_subject_families(question)
    application = _has_personal_application(normalized) and bool(families or _distinctive_references(_profile_tokens(question)))
    if source_lookup and not application:
        return ("source/exact teaching lookup",)

    intents: list[str] = []
    if any(term in normalized for term in ("backtest", "research", "study")):
        intents.append("research/backtest/strategy development")
    if application or any(term in normalized for term in ("plan", "trade plan", "execute", "sizing")):
        intents.append("execution/trading plan")
    if any(term in normalized for term in ("learn", "teach", "methodology", "explain")):
        intents.append("learning/methodology")
    if any(term in normalized for term in ("trade", "trading", "strategy", "setup", "market", "entry", "risk")):
        intents.append("general trading advice")
    if source_lookup:
        intents.append("source/exact teaching lookup")
    return tuple(dict.fromkeys(intents)) or ("unrelated/non-trading",)


def _applicability(item, policies, question, question_tokens, question_families):
    family = _subject_family(item.subject_key)
    if family is None:
        return None
    if _explicit_reference(item, family, question, question_tokens):
        return (1, "explicit_reference")
    if family in question_families or item.subject_key in question:
        return (4, "subject_match")
    structural = any((item.category, item.kind, family) in policy["structural"] for policy in policies)
    if not structural:
        return None
    if item.kind == "goal" and item.provenance in {"USER_STATED", "USER_CONFIRMED", "USER_DECISION"}:
        return (3, "current_goal")
    if item.kind in {"constraint", "principle"} or family in {"available session", "available time", "holding horizon", "market/instrument"}:
        return (2, "structural_constraint")
    return (5, "policy_context")


def _keep_specific_category_matches(applicable):
    categories_with_specific_match = {
        item.category for item, result in applicable if result[1] in {"explicit_reference", "subject_match"}
    }
    return [
        (item, result)
        for item, result in applicable
        if item.category not in categories_with_specific_match
        or result[1] in {"explicit_reference", "subject_match"}
    ]


def _selection_sort_key(pair):
    item, (tier, _) = pair
    decision_tie_break = 0 if tier == 3 and item.provenance == "USER_DECISION" else 1
    return (tier, decision_tie_break, item.category, item.subject_key, item.id)


def _question_subject_families(question: str) -> frozenset[str]:
    normalized = _subject_key(question)
    return frozenset(
        family
        for family, aliases in _SUBJECT_FAMILIES.items()
        if any(alias in normalized for alias in aliases)
    )


def _subject_family(subject_key: str) -> str | None:
    for family, aliases in _SUBJECT_FAMILIES.items():
        if subject_key in aliases:
            return family
    for family, aliases in _SUBJECT_FAMILIES.items():
        if any(subject_key.startswith(f"{alias} ") for alias in aliases):
            return family
    return None


def _explicit_reference(item: TraderProfileItem, family: str, question: str, question_tokens: set[str]) -> bool:
    if family not in {"market/instrument", "available session", "style/methodology"}:
        return item.subject_key in question
    values = _distinctive_references(_profile_tokens(item.value))
    return bool(values.intersection(question_tokens)) or item.subject_key in question


def _distinctive_references(tokens: set[str]) -> frozenset[str]:
    return frozenset(tokens.intersection(_DISTINCTIVE_VALUES))


def _has_personal_application(question: str) -> bool:
    return any(signal in question for signal in ("my ", "for me", "given i", "given my", "apply this to my"))


def _profile_tokens(text: str) -> set[str]:
    return {
        _singularize(token)
        for token in _TOKEN_PATTERN.findall(text.casefold())
        if token not in _TOKEN_STOP_WORDS
    }


def _singularize(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") else token
