"""Disabled-by-default scaffolding for the Phase 6.5 teaching comparison."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum
import json
from typing import Callable


class PilotVariant(str, Enum):
    REPAIRED_RETRIEVAL = "repaired_retrieval"
    DIRECT_LESSONS = "direct_lessons"
    PREPARED_HYBRID = "prepared_hybrid"


class PilotDisabled(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class PilotControls:
    model: str
    reasoning: str
    teacher_policy: str
    enabled_scope: tuple[str, ...]
    max_output_tokens: int = 4_000
    max_input_tokens: int = 120_000
    max_tool_calls: int = 2
    max_retries: int = 1

    def __post_init__(self) -> None:
        if not self.model or not self.reasoning or not self.teacher_policy or not self.enabled_scope:
            raise ValueError("pilot controls are incomplete")
        if self.max_output_tokens <= 0 or self.max_input_tokens <= 0 or self.max_tool_calls <= 0 or self.max_retries < 0:
            raise ValueError("pilot call limits are invalid")


@dataclass(frozen=True)
class SourceSection:
    id: str
    library_key: str
    revision_sha256: str
    lesson: str
    timestamp: str
    text: str


@dataclass(frozen=True)
class PreparedNote:
    id: str
    statement: str
    support_section_ids: tuple[str, ...]
    qualification: str


@dataclass(frozen=True)
class LessonContinuity:
    concept: str
    objective: str
    source_versions: tuple[tuple[str, str], ...]
    theo_answers: tuple[str, ...]
    understood: tuple[str, ...]
    uncertain: tuple[str, ...]
    exercise: str
    exercise_timeframe_roles: tuple[str, ...]
    disputes: tuple[tuple[str, str], ...]
    next_action: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "LessonContinuity":
        return cls(
            concept=str(value["concept"]),
            objective=str(value["objective"]),
            source_versions=tuple(tuple(item) for item in value["source_versions"]),
            theo_answers=tuple(value["theo_answers"]),
            understood=tuple(value["understood"]),
            uncertain=tuple(value["uncertain"]),
            exercise=str(value["exercise"]),
            exercise_timeframe_roles=tuple(value["exercise_timeframe_roles"]),
            disputes=tuple(tuple(item) for item in value["disputes"]),
            next_action=str(value["next_action"]),
        )


@dataclass(frozen=True)
class PilotRequest:
    variant: PilotVariant
    model: str
    reasoning: str
    teacher_policy: str
    source_scope: tuple[str, ...]
    dialogue: tuple[str, ...]
    lesson_state: dict[str, object]
    evidence_context: str
    max_output_tokens: int
    max_input_tokens: int
    max_tool_calls: int
    max_retries: int


class BudgetGuard:
    def __init__(self, ceiling_usd: Decimal):
        if ceiling_usd <= 0:
            raise ValueError("budget ceiling must be positive")
        self.ceiling_usd = ceiling_usd
        self.spent_usd = Decimal("0.00")
        self._reservations: dict[str, Decimal] = {}

    @property
    def reserved_usd(self) -> Decimal:
        return sum(self._reservations.values(), Decimal("0.00"))

    @property
    def available_usd(self) -> Decimal:
        return self.ceiling_usd - self.spent_usd - self.reserved_usd

    def reserve(self, call_id: str, amount: Decimal) -> None:
        if not call_id or amount <= 0 or call_id in self._reservations:
            raise ValueError("budget reservation is invalid")
        if self.spent_usd + self.reserved_usd + amount > self.ceiling_usd:
            raise BudgetExceeded("paid pilot budget reservation would exceed the ceiling")
        self._reservations[call_id] = amount

    def settle(self, call_id: str, actual_cost_usd: Decimal | None) -> None:
        reserved = self._reservations.get(call_id)
        if reserved is None:
            raise ValueError("budget reservation does not exist")
        if actual_cost_usd is None:
            return
        if actual_cost_usd < 0 or actual_cost_usd > reserved:
            raise BudgetExceeded("reported call cost exceeds its reservation")
        self.spent_usd += actual_cost_usd
        del self._reservations[call_id]


def prepare_variant(
    variant: PilotVariant,
    *,
    controls: PilotControls,
    state: LessonContinuity,
    dialogue: tuple[str, ...],
    sections: tuple[SourceSection, ...] = (),
    notes: tuple[PreparedNote, ...] = (),
) -> PilotRequest:
    versions = dict(state.source_versions)
    if not set(versions).issubset(controls.enabled_scope):
        raise ValueError("lesson state contains a disabled source scope")
    for section in sections:
        if section.library_key not in controls.enabled_scope:
            raise ValueError("lesson material contains a disabled source scope")
        if versions.get(section.library_key) != section.revision_sha256:
            raise ValueError("lesson material source version is stale")
    section_ids = {section.id for section in sections}
    if any(
        not note.support_section_ids or not set(note.support_section_ids).issubset(section_ids)
        for note in notes
    ):
        raise ValueError("prepared note lacks current source support")
    if variant is PilotVariant.REPAIRED_RETRIEVAL:
        evidence_context = "Use the repaired bounded retrieval path and verify teaching against raw sources."
    elif variant is PilotVariant.DIRECT_LESSONS:
        if not sections:
            raise ValueError("direct lesson variant requires source sections")
        evidence_context = _section_context(sections)
    else:
        if not sections or not notes:
            raise ValueError("prepared hybrid requires notes and source sections")
        note_context = "\n".join(
            f"Prepared derived teaching aid, not source authority [{note.id}]: {note.statement} "
            f"Qualification: {note.qualification} Supports: {', '.join(note.support_section_ids)}"
            for note in notes
        )
        evidence_context = f"{note_context}\n\nOriginal source sections:\n{_section_context(sections)}"
    lesson_state = state.to_dict()
    estimated_input_tokens = (
        len(controls.teacher_policy)
        + sum(map(len, dialogue))
        + len(json.dumps(lesson_state, separators=(",", ":")))
        + len(evidence_context)
        + 2
    ) // 3
    if estimated_input_tokens > controls.max_input_tokens:
        raise ValueError("pilot input exceeds the configured token budget")
    return PilotRequest(
        variant,
        controls.model,
        controls.reasoning,
        controls.teacher_policy,
        controls.enabled_scope,
        dialogue,
        lesson_state,
        evidence_context,
        controls.max_output_tokens,
        controls.max_input_tokens,
        controls.max_tool_calls,
        controls.max_retries,
    )


def dispatch_live(
    *,
    enabled: bool,
    client: Callable[[PilotRequest], dict[str, object]],
    request: PilotRequest,
    budget: BudgetGuard,
    call_id: str,
    reservation_usd: Decimal,
) -> dict[str, object]:
    if not enabled:
        raise PilotDisabled("paid teaching pilot is disabled")
    if budget.available_usd > 0 and reservation_usd != budget.available_usd:
        raise ValueError("reserve the entire remaining pilot budget before live dispatch")
    budget.reserve(call_id, reservation_usd)
    result = client(request)
    raw_cost = result.get("usage_cost_usd")
    budget.settle(call_id, None if raw_cost is None else Decimal(str(raw_cost)))
    return result


def _section_context(sections: tuple[SourceSection, ...]) -> str:
    return "\n\n".join(
        f"Original source [{section.id}] {section.library_key} | {section.lesson} | "
        f"{section.timestamp} | revision {section.revision_sha256}:\n{section.text}"
        for section in sections
    )
