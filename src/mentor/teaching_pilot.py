"""Disabled-by-default scaffolding for the Phase 6.5 teaching comparison."""

from dataclasses import asdict, dataclass
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
from enum import Enum
import json
from pathlib import Path
from typing import Any


PILOT_LEDGER_PATH = Path(__file__).resolve().parents[2] / "data" / "pilots" / "phase-6-5" / "spend-ledger.json"
_CANONICAL_BUDGET = object()


class PilotVariant(str, Enum):
    REPAIRED_RETRIEVAL = "repaired_retrieval"
    DIRECT_LESSONS = "direct_lessons"
    PREPARED_HYBRID = "prepared_hybrid"


class PilotDisabled(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    cache_write_per_million: Decimal
    output_per_million: Decimal
    file_search_call: Decimal


_MODEL_PRICING = {
    "gpt-5.6-sol": ModelPricing(
        Decimal("4.00"), Decimal("0.40"), Decimal("5.00"), Decimal("20.00"), Decimal("0.0025")
    ),
    "gpt-5.6-luna": ModelPricing(
        Decimal("0.20"), Decimal("0.02"), Decimal("0.25"), Decimal("1.20"), Decimal("0.0025")
    ),
}


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
    source_id: str
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
    source_versions: tuple[tuple[str, str, str], ...]
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


@dataclass(frozen=True)
class BoundedRequest:
    """One fully serialized provider call with a conservative cost ceiling."""

    payload: dict[str, object]
    max_input_tokens: int
    additional_input_tokens: int = 0

    def reservation_usd(self) -> Decimal:
        model = self.payload.get("model")
        pricing = _MODEL_PRICING.get(model)
        if pricing is None:
            raise ValueError("paid pilot model has no current pricing configuration")
        if self.payload.get("service_tier") != "default" or self.payload.get("store") is not False:
            raise ValueError("paid pilot calls require default service tier and store=False")
        if any(key in self.payload for key in ("previous_response_id", "conversation", "prompt")):
            raise ValueError("paid pilot request contains unbounded provider-held input")
        if _contains_unbounded_input(self.payload.get("input")):
            raise ValueError("paid pilot request contains unbounded file or image input")
        max_output = self.payload.get("max_output_tokens")
        if not isinstance(max_output, int) or max_output <= 0:
            raise ValueError("paid pilot calls require a positive max_output_tokens")
        tools = self.payload.get("tools") or []
        if not isinstance(tools, list):
            raise ValueError("paid pilot tools must be a list")
        unsupported = [tool for tool in tools if not isinstance(tool, dict) or tool.get("type") != "file_search"]
        if unsupported:
            raise ValueError("paid pilot request contains an unpriced tool")
        max_tool_calls = self.payload.get("max_tool_calls", 0)
        if not isinstance(max_tool_calls, int) or max_tool_calls < 0:
            raise ValueError("paid pilot max_tool_calls is invalid")
        if tools and max_tool_calls <= 0:
            raise ValueError("paid pilot File Search must have a positive max_tool_calls bound")
        max_results = 0
        for tool in tools:
            results = tool.get("max_num_results")
            if not isinstance(results, int) or results <= 0:
                raise ValueError("paid pilot File Search must bound max_num_results")
            max_results = max(max_results, results)
        # UTF-8 bytes conservatively upper-bound user-supplied BPE tokens. Add
        # fixed request framing plus the provider's documented maximum 4,096
        # tokens for every possible File Search result chunk.
        serialized_bytes = len(json.dumps(self.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if self.additional_input_tokens < 0:
            raise ValueError("additional input token bound cannot be negative")
        input_bound = (
            serialized_bytes + 2_048 + self.additional_input_tokens
            + max_tool_calls * max_results * 4_096
        )
        if input_bound > self.max_input_tokens:
            raise ValueError("paid pilot input upper bound exceeds max_input_tokens")
        input_rate = max(pricing.input_per_million, pricing.cache_write_per_million)
        total = (
            Decimal(input_bound) * input_rate / Decimal(1_000_000)
            + Decimal(max_output) * pricing.output_per_million / Decimal(1_000_000)
            + Decimal(max_tool_calls) * pricing.file_search_call
        )
        return total.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)

    @property
    def pricing(self) -> ModelPricing:
        return _MODEL_PRICING[str(self.payload["model"])]


def _contains_unbounded_input(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("type") in {"input_file", "input_image", "computer_screenshot", "item_reference"}:
            return True
        return any(_contains_unbounded_input(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_unbounded_input(item) for item in value)
    return False


class BudgetGuard:
    def __init__(
        self, ceiling_usd: Decimal, ledger_path: Path | None = None, *, _canonical: object | None = None
    ):
        if ceiling_usd <= 0 or ceiling_usd > Decimal("5.00"):
            raise ValueError("budget ceiling must be positive and no greater than USD 5.00")
        self.ceiling_usd = ceiling_usd
        self.ledger_path = ledger_path
        self._canonical = _canonical is _CANONICAL_BUDGET
        self.spent_usd = Decimal("0.00")
        self._reservations: dict[str, Decimal] = {}
        self._blocks: dict[str, dict[str, Decimal]] = {}
        self._settled: dict[str, Decimal] = {}
        self._load()

    @classmethod
    def for_paid_pilot(cls) -> "BudgetGuard":
        return cls(Decimal("5.00"), PILOT_LEDGER_PATH, _canonical=_CANONICAL_BUDGET)

    @property
    def reserved_usd(self) -> Decimal:
        block_total = sum(
            (sum(calls.values(), Decimal("0.00")) for calls in self._blocks.values()),
            Decimal("0.00"),
        )
        return sum(self._reservations.values(), block_total)

    @property
    def available_usd(self) -> Decimal:
        return self.ceiling_usd - self.spent_usd - self.reserved_usd

    def reserve(self, call_id: str, amount: Decimal) -> None:
        with self._locked_state():
            if (
                not call_id or amount <= 0 or call_id in self._reservations or call_id in self._settled
                or any(call_id in block for block in self._blocks.values())
            ):
                raise ValueError("budget reservation is invalid")
            if self.spent_usd + self.reserved_usd + amount > self.ceiling_usd:
                raise BudgetExceeded("paid pilot budget reservation would exceed the ceiling")
            self._reservations[call_id] = amount
            self._save()

    def settle(self, call_id: str, actual_cost_usd: Decimal | None) -> None:
        with self._locked_state():
            reserved = self._reservations.get(call_id)
            if reserved is None:
                raise ValueError("budget reservation does not exist")
            if actual_cost_usd is None:
                return
            if actual_cost_usd < 0 or actual_cost_usd > reserved:
                raise BudgetExceeded("reported call cost exceeds its reservation")
            self.spent_usd += actual_cost_usd
            del self._reservations[call_id]
            self._settled[call_id] = actual_cost_usd
            self._save()

    def reserve_block(self, block_id: str, requests: dict[str, BoundedRequest]) -> Decimal:
        if self.ledger_path is None:
            raise ValueError("paid comparison blocks require a persistent budget ledger")
        with self._locked_state():
            if not block_id or not requests or block_id in self._blocks:
                raise ValueError("balanced comparison block is invalid")
            existing = set(self._reservations) | set(self._settled)
            existing.update(call_id for block in self._blocks.values() for call_id in block)
            if any(not call_id or call_id in existing for call_id in requests):
                raise ValueError("balanced comparison call identity is invalid")
            allocations = {call_id: request.reservation_usd() for call_id, request in requests.items()}
            required = sum(allocations.values(), Decimal("0"))
            if required > self.available_usd:
                raise BudgetExceeded("balanced comparison block cannot fit the remaining pilot budget")
            self._blocks[block_id] = allocations
            self._save()
            return required

    def block_available(self, block_id: str, call_id: str) -> Decimal | None:
        return self._blocks.get(block_id, {}).get(call_id)

    def close_block(self, block_id: str) -> None:
        with self._locked_state():
            if block_id not in self._blocks:
                return
            del self._blocks[block_id]
            self._save()

    def activate(self, block_id: str, call_id: str, required: Decimal) -> None:
        with self._locked_state():
            allocation = self.block_available(block_id, call_id)
            if allocation is None:
                raise ValueError("paid call was not allocated in the balanced comparison block")
            if required > allocation:
                raise BudgetExceeded("paid call exceeds its preflight allocation")
            del self._blocks[block_id][call_id]
            if not self._blocks[block_id]:
                del self._blocks[block_id]
            self._reservations[call_id] = allocation
            self._save()

    def _validate_state(self) -> None:
        amounts = [self.spent_usd, *self._reservations.values(), *self._settled.values()]
        amounts.extend(amount for block in self._blocks.values() for amount in block.values())
        if any(not amount.is_finite() or amount < 0 for amount in amounts):
            raise ValueError("budget ledger contains an invalid amount")
        if any(not call_id for call_id in self._reservations):
            raise ValueError("budget ledger contains an invalid call identity")
        block_call_ids = [call_id for block in self._blocks.values() for call_id in block]
        if any(not block_id or not calls for block_id, calls in self._blocks.items()):
            raise ValueError("budget ledger contains an invalid comparison block")
        if any(not call_id for call_id in block_call_ids):
            raise ValueError("budget ledger contains an invalid call identity")
        all_call_ids = [*self._reservations, *self._settled, *block_call_ids]
        if len(all_call_ids) != len(set(all_call_ids)):
            raise ValueError("budget ledger contains a duplicate call identity")
        if self.spent_usd + self.reserved_usd > self.ceiling_usd:
            raise ValueError("budget ledger exceeds the authorized ceiling")
        if sum(self._settled.values(), Decimal("0.00")) != self.spent_usd:
            raise ValueError("budget ledger spend history is inconsistent")

    def _load(self) -> None:
        if self.ledger_path is None or not self.ledger_path.exists():
            return
        state = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        if Decimal(state["ceiling_usd"]) != self.ceiling_usd:
            raise ValueError("budget ledger ceiling does not match")
        self.spent_usd = Decimal(state["spent_usd"])
        self._reservations = {str(key): Decimal(value) for key, value in state["reservations"].items()}
        self._blocks = {
            str(block_id): {str(key): Decimal(value) for key, value in calls.items()}
            for block_id, calls in state.get("blocks", {}).items()
        }
        self._settled = {str(key): Decimal(value) for key, value in state.get("settled", {}).items()}
        self._validate_state()

    @contextmanager
    def _locked_state(self):
        if not self._canonical:
            yield
            return
        import msvcrt

        lock_path = self.ledger_path.with_suffix(f"{self.ledger_path.suffix}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock:
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("canonical pilot budget ledger is busy") from error
            try:
                self._load()
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def _save(self) -> None:
        if self.ledger_path is None:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.ledger_path.with_suffix(f"{self.ledger_path.suffix}.tmp")
        temporary.write_text(json.dumps({
            "ceiling_usd": str(self.ceiling_usd),
            "spent_usd": str(self.spent_usd),
            "reservations": {key: str(value) for key, value in self._reservations.items()},
            "blocks": {
                block_id: {call_id: str(value) for call_id, value in calls.items()}
                for block_id, calls in self._blocks.items()
            },
            "settled": {key: str(value) for key, value in self._settled.items()},
        }, indent=2), encoding="utf-8")
        temporary.replace(self.ledger_path)


def prepare_variant(
    variant: PilotVariant,
    *,
    controls: PilotControls,
    state: LessonContinuity,
    dialogue: tuple[str, ...],
    sections: tuple[SourceSection, ...] = (),
    notes: tuple[PreparedNote, ...] = (),
) -> PilotRequest:
    source_ids = [source_id for source_id, _, _ in state.source_versions]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("lesson state contains a duplicate source identity")
    versions = {
        source_id: (library_key, revision_sha256)
        for source_id, library_key, revision_sha256 in state.source_versions
    }
    if not {library_key for library_key, _ in versions.values()}.issubset(controls.enabled_scope):
        raise ValueError("lesson state contains a disabled source scope")
    for section in sections:
        if section.library_key not in controls.enabled_scope:
            raise ValueError("lesson material contains a disabled source scope")
        if versions.get(section.source_id) != (section.library_key, section.revision_sha256):
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
    client: Any,
    request: BoundedRequest,
    budget: BudgetGuard,
    call_id: str,
    block_id: str,
) -> Any:
    if not enabled:
        raise PilotDisabled("paid teaching pilot is disabled")
    if not budget._canonical or budget.ledger_path != PILOT_LEDGER_PATH:
        raise ValueError("paid dispatch requires the canonical cumulative pilot ledger")
    if not hasattr(client, "with_options") or not hasattr(client, "responses"):
        raise TypeError("paid pilot requires an OpenAI client with explicit retry control")
    budget.activate(block_id, call_id, request.reservation_usd())
    bounded_client = client.with_options(max_retries=0)
    result = bounded_client.responses.create(**request.payload)
    actual_cost = _actual_cost(result, request.pricing)
    budget.settle(call_id, actual_cost)
    return result


def _actual_cost(response: Any, pricing: ModelPricing) -> Decimal | None:
    usage = _field(response, "usage")
    input_tokens = _field(usage, "input_tokens")
    output_tokens = _field(usage, "output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    details = _field(usage, "input_tokens_details")
    cached = _field(details, "cached_tokens") or 0
    cache_write = _field(details, "cache_write_tokens") or 0
    if not isinstance(cached, int) or not isinstance(cache_write, int):
        return None
    uncached = max(input_tokens - cached - cache_write, 0)
    output = _field(response, "output") or []
    file_search_calls = sum(
        _field(item, "type") == "file_search_call" for item in output
    )
    total = (
        Decimal(uncached) * pricing.input_per_million / Decimal(1_000_000)
        + Decimal(cached) * pricing.cached_input_per_million / Decimal(1_000_000)
        + Decimal(cache_write) * pricing.cache_write_per_million / Decimal(1_000_000)
        + Decimal(output_tokens) * pricing.output_per_million / Decimal(1_000_000)
        + Decimal(file_search_calls) * pricing.file_search_call
    )
    return total.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _section_context(sections: tuple[SourceSection, ...]) -> str:
    return "\n\n".join(
        f"Original source [{section.id}] {section.library_key} | {section.lesson} | "
        f"{section.timestamp} | revision {section.revision_sha256}:\n{section.text}"
        for section in sections
    )
