from decimal import Decimal
import json
from pathlib import Path

import pytest
import mentor.teaching_pilot as teaching_pilot

from mentor.teaching_pilot import (
    BoundedRequest,
    BudgetExceeded,
    BudgetGuard,
    LessonContinuity,
    PilotControls,
    PilotDisabled,
    PilotVariant,
    PreparedNote,
    SourceSection,
    dispatch_live,
    prepare_variant,
)


CONTROLS = PilotControls(
    model="gpt-5.6-sol",
    reasoning="high",
    teacher_policy="Teach from supplied evidence without flattening mentors.",
    enabled_scope=("gxt.garrett", "gxt.afyz"),
)
STATE = LessonContinuity(
    concept="continuation signatures",
    objective="Distinguish signature from confirmation.",
    source_versions=(
        ("garrett-phases", "gxt.garrett", "a" * 64),
        ("afyz-continuations", "gxt.afyz", "b" * 64),
    ),
    theo_answers=("Consolidation is a continuation signature.",),
    understood=("A signature is contextual.",),
    uncertain=("Which lower-timeframe confirmation is required?",),
    exercise="Mark a higher-timeframe leg and its lower-timeframe confirmation.",
    exercise_timeframe_roles=("higher: location and draw", "lower: confirmation"),
    disputes=(("consolidation label", "verified"),),
    next_action="Complete the marked-chart observations in text.",
)
SECTIONS = (
    SourceSection(
        id="garrett-section",
        source_id="garrett-phases",
        library_key="gxt.garrett",
        revision_sha256="a" * 64,
        lesson="Synthetic Garrett lesson",
        timestamp="00:01:00-00:02:00",
        text="Synthetic original source passage.",
    ),
    SourceSection(
        id="afyz-section",
        source_id="afyz-continuations",
        library_key="gxt.afyz",
        revision_sha256="b" * 64,
        lesson="Synthetic Afyz lesson",
        timestamp="00:03:00-00:04:00",
        text="Synthetic second source passage.",
    ),
)
NOTES = (
    PreparedNote(
        id="note-one",
        statement="Synthetic derived teaching aid.",
        support_section_ids=("garrett-section", "afyz-section"),
        qualification="Derived orientation; verify against original sections.",
    ),
)


def test_three_variants_hold_teacher_dialogue_and_permissions_constant():
    requests = [
        prepare_variant(
            variant,
            controls=CONTROLS,
            state=STATE,
            dialogue=("Teach me the topic.", "Which timeframe?"),
            sections=SECTIONS if variant is not PilotVariant.REPAIRED_RETRIEVAL else (),
            notes=NOTES if variant is PilotVariant.PREPARED_HYBRID else (),
        )
        for variant in PilotVariant
    ]

    assert {request.model for request in requests} == {"gpt-5.6-sol"}
    assert {request.reasoning for request in requests} == {"high"}
    assert {request.teacher_policy for request in requests} == {CONTROLS.teacher_policy}
    assert {request.dialogue for request in requests} == {("Teach me the topic.", "Which timeframe?")}
    assert {request.source_scope for request in requests} == {CONTROLS.enabled_scope}
    assert {(request.max_input_tokens, request.max_output_tokens, request.max_tool_calls, request.max_retries) for request in requests} == {
        (120_000, 4_000, 2, 1)
    }
    assert "Synthetic original source passage" not in requests[0].evidence_context
    assert "Synthetic original source passage" in requests[1].evidence_context
    assert "Synthetic derived teaching aid" in requests[2].evidence_context
    assert "derived teaching aid, not source authority" in requests[2].evidence_context.casefold()


def test_disabled_mentor_invalidates_cached_lesson_material():
    disabled = PilotControls(
        model=CONTROLS.model,
        reasoning=CONTROLS.reasoning,
        teacher_policy=CONTROLS.teacher_policy,
        enabled_scope=("gxt.garrett",),
    )

    with pytest.raises(ValueError, match="disabled source scope"):
        prepare_variant(
            PilotVariant.DIRECT_LESSONS,
            controls=disabled,
            state=STATE,
            dialogue=("Continue.",),
            sections=SECTIONS,
        )


def test_prepared_note_requires_at_least_one_current_source_section():
    unsupported = PreparedNote(
        id="unsupported",
        statement="Unsupported derived aid.",
        support_section_ids=(),
        qualification="None.",
    )

    with pytest.raises(ValueError, match="current source support"):
        prepare_variant(
            PilotVariant.PREPARED_HYBRID,
            controls=CONTROLS,
            state=STATE,
            dialogue=("Teach me.",),
            sections=SECTIONS,
            notes=(unsupported,),
        )


def test_direct_lesson_payload_is_rejected_when_it_exceeds_the_input_budget():
    oversized = SourceSection(
        id="oversized",
        source_id="garrett-phases",
        library_key="gxt.garrett",
        revision_sha256="a" * 64,
        lesson="Synthetic oversized lesson",
        timestamp="00:00:00-00:01:00",
        text="x" * 400_000,
    )

    with pytest.raises(ValueError, match="input exceeds"):
        prepare_variant(
            PilotVariant.DIRECT_LESSONS,
            controls=CONTROLS,
            state=STATE,
            dialogue=("Teach me.",),
            sections=(oversized,),
        )


def test_lesson_continuity_round_trip_retains_exercise_roles_and_dispute_status():
    restored = LessonContinuity.from_dict(STATE.to_dict())

    assert restored == STATE
    assert restored.exercise_timeframe_roles == (
        "higher: location and draw",
        "lower: confirmation",
    )
    assert restored.disputes == (("consolidation label", "verified"),)


def test_live_dispatch_fails_closed_before_touching_client(tmp_path, monkeypatch):
    calls = []

    with pytest.raises(PilotDisabled):
        dispatch_live(
            enabled=False,
            client=_FakeClient(calls, {"usage": None}),
            request=_bounded_request(),
            budget=_paid_budget(tmp_path, monkeypatch),
            call_id="case-1",
            block_id="scenario",
        )

    assert calls == []


def test_budget_is_reserved_before_dispatch_and_unknown_usage_is_not_free(tmp_path, monkeypatch):
    request = _bounded_request()
    budget = _paid_budget(tmp_path, monkeypatch)
    prior = budget.ceiling_usd - request.reservation_usd()
    budget.reserve("prior-spend", prior)
    budget.settle("prior-spend", prior)
    budget.reserve_block("scenario", {"case-1": request})
    calls = []

    dispatch_live(
        enabled=True,
        client=_FakeClient(calls, {"usage": None}),
        request=request,
        budget=budget,
        call_id="case-1",
        block_id="scenario",
    )

    assert len(calls) == 1
    assert budget.reserved_usd == request.reservation_usd()
    assert budget.available_usd == Decimal("0")


def test_live_dispatch_rejects_insufficient_budget_before_client_is_called(tmp_path):
    budget = BudgetGuard(Decimal("0.001"), tmp_path / "ledger.json")
    request = _bounded_request()
    calls = []

    with pytest.raises(BudgetExceeded):
        budget.reserve_block("scenario", {"under-reserved": request})

    assert calls == []


def test_reported_usage_releases_only_the_unused_reservation(tmp_path, monkeypatch):
    budget = _paid_budget(tmp_path, monkeypatch)
    request = _bounded_request()
    budget.reserve_block("scenario", {"case-1": request})

    dispatch_live(
        enabled=True,
        client=_FakeClient([], {
            "model": "gpt-5.6-sol",
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "output": [],
        }),
        request=request,
        budget=budget,
        call_id="case-1",
        block_id="scenario",
    )

    assert budget.spent_usd == Decimal("0.000800")
    assert budget.reserved_usd == Decimal("0.00")


def test_two_distinct_garrett_revisions_are_validated_independently():
    state = LessonContinuity(
        **{
            **STATE.to_dict(),
            "source_versions": (
                ("garrett-phases", "gxt.garrett", "a" * 64),
                ("garrett-universal", "gxt.garrett", "c" * 64),
                ("afyz-continuations", "gxt.afyz", "b" * 64),
            ),
        }
    )
    sections = (
        SECTIONS[0],
        SourceSection(
            id="garrett-second-section",
            source_id="garrett-universal",
            library_key="gxt.garrett",
            revision_sha256="c" * 64,
            lesson="Second Garrett transcript",
            timestamp="00:05:00-00:06:00",
            text="Independent synthetic passage.",
        ),
        SECTIONS[1],
    )

    request = prepare_variant(
        PilotVariant.DIRECT_LESSONS,
        controls=CONTROLS,
        state=state,
        dialogue=("Teach me.",),
        sections=sections,
    )

    assert "Independent synthetic passage" in request.evidence_context


def test_wrong_hash_for_second_same_mentor_source_is_rejected():
    bad = SourceSection(
        id="garrett-second-section",
        source_id="garrett-universal",
        library_key="gxt.garrett",
        revision_sha256="d" * 64,
        lesson="Second Garrett transcript",
        timestamp="00:05:00-00:06:00",
        text="Independent synthetic passage.",
    )
    state = LessonContinuity(
        **{
            **STATE.to_dict(),
            "source_versions": (
                ("garrett-phases", "gxt.garrett", "a" * 64),
                ("garrett-universal", "gxt.garrett", "c" * 64),
                ("afyz-continuations", "gxt.afyz", "b" * 64),
            ),
        }
    )

    with pytest.raises(ValueError, match="source version is stale"):
        prepare_variant(
            PilotVariant.DIRECT_LESSONS,
            controls=CONTROLS,
            state=state,
            dialogue=("Teach me.",),
            sections=(SECTIONS[0], bad, SECTIONS[1]),
        )


def test_budget_ledger_survives_restart_with_spend_and_unknown_reservation(tmp_path):
    ledger = tmp_path / "spend.json"
    first = BudgetGuard(Decimal("5.00"), ledger)
    first.reserve("known", Decimal("0.50"))
    first.settle("known", Decimal("0.10"))
    first.reserve("unknown", Decimal("0.40"))

    restarted = BudgetGuard(Decimal("5.00"), ledger)

    assert restarted.spent_usd == Decimal("0.10")
    assert restarted.reserved_usd == Decimal("0.40")
    assert restarted.available_usd == Decimal("4.50")
    with pytest.raises(ValueError, match="invalid"):
        restarted.reserve("known", Decimal("0.01"))


def test_balanced_block_allocations_prevent_one_variant_consuming_another(tmp_path, monkeypatch):
    request = _bounded_request(max_output_tokens=3_000)
    budget = _paid_budget(tmp_path, monkeypatch)
    budget.reserve_block("scenario", {
        "variant-a": request,
        "variant-b": request,
    })
    dispatch_live(
        enabled=True,
        client=_FakeClient([], {"usage": None}),
        request=request,
        budget=budget,
        block_id="scenario",
        call_id="variant-a",
    )

    with pytest.raises(ValueError, match="not allocated"):
        dispatch_live(
            enabled=True,
            client=_FakeClient([], {"usage": None}),
            request=request,
            budget=budget,
            block_id="scenario",
            call_id="variant-a-retry-unplanned",
        )

    assert budget.block_available("scenario", "variant-b") == request.reservation_usd()


def test_retries_and_repairs_share_one_cumulative_ceiling(tmp_path, monkeypatch):
    budget = _paid_budget(tmp_path, monkeypatch)
    request = _bounded_request(max_output_tokens=3_000)
    budget.reserve_block("scenario", {"primary-attempt-1": request, "citation-repair": request})
    dispatch_live(
        enabled=True,
        client=_FakeClient([], {"usage": None}),
        request=request,
        budget=budget,
        call_id="primary-attempt-1",
        block_id="scenario",
    )

    dispatch_live(
        enabled=True,
        client=_FakeClient([], {"usage": None}),
        request=request,
        budget=budget,
        call_id="citation-repair",
        block_id="scenario",
    )

    with pytest.raises(ValueError, match="not allocated"):
        dispatch_live(
            enabled=True,
            client=_FakeClient([], {"usage": None}),
            request=request,
            budget=budget,
            call_id="undeclared-second-repair",
            block_id="scenario",
        )


def test_adapter_enforces_response_limits_and_disables_sdk_retries(tmp_path, monkeypatch):
    seen = []

    class Responses:
        def create(self, **payload):
            seen.append(payload)
            return {"model": "gpt-5.6-sol", "usage": {"input_tokens": 10, "output_tokens": 5}, "output": []}

    class Client:
        def __init__(self):
            self.responses = Responses()
            self.retry_values = []

        def with_options(self, *, max_retries):
            self.retry_values.append(max_retries)
            return self

    client = Client()
    budget = _paid_budget(tmp_path, monkeypatch)
    budget.reserve_block("scenario", {"bounded": _bounded_request()})
    dispatch_live(
        enabled=True,
        client=client,
        request=_bounded_request(),
        budget=budget,
        call_id="bounded",
        block_id="scenario",
    )

    assert client.retry_values == [0]
    assert seen[0]["max_output_tokens"] == 1_000
    assert seen[0]["max_tool_calls"] == 1
    assert seen[0]["reasoning"] == {"effort": "high"}
    assert seen[0]["service_tier"] == "default"
    assert seen[0]["store"] is False


def test_future_continuation_input_is_included_in_preflight_bound():
    current = _bounded_request()
    future = BoundedRequest(
        payload=current.payload,
        max_input_tokens=current.max_input_tokens,
        additional_input_tokens=4_000,
    )

    assert future.reservation_usd() > current.reservation_usd()


def _bounded_request(*, max_output_tokens=1_000):
    return BoundedRequest(
        payload={
            "model": "gpt-5.6-sol",
            "instructions": "Teach from evidence.",
            "input": "Synthetic question.",
            "reasoning": {"effort": "high"},
            "tools": [{"type": "file_search", "vector_store_ids": ["vs_synthetic"], "max_num_results": 2}],
            "max_output_tokens": max_output_tokens,
            "max_tool_calls": 1,
            "service_tier": "default",
            "store": False,
        },
        max_input_tokens=120_000,
    )


def test_paid_dispatch_rejects_nonpersistent_budget_and_callable_client(tmp_path, monkeypatch):
    request = _bounded_request()
    in_memory = BudgetGuard(Decimal("1.00"))
    with pytest.raises(ValueError, match="persistent"):
        in_memory.reserve_block("scenario", {"call": request})

    persistent = _paid_budget(tmp_path, monkeypatch)
    persistent.reserve_block("scenario", {"call": request})
    with pytest.raises(TypeError, match="OpenAI client"):
        dispatch_live(
            enabled=True,
            client=lambda payload: {"usage": None},
            request=request,
            budget=persistent,
            block_id="scenario",
            call_id="call",
        )


def test_pricing_is_bound_to_supported_payload_model():
    request = _bounded_request()
    bad_payload = {**request.payload, "model": "arbitrary-expensive-model"}
    with pytest.raises(ValueError, match="pricing"):
        BoundedRequest(payload=bad_payload, max_input_tokens=120_000).reservation_usd()


@pytest.mark.parametrize("unbounded", [
    {"previous_response_id": "resp_remote"},
    {"conversation": "conv_remote"},
    {"prompt": {"id": "pmpt_remote"}},
])
def test_server_held_context_is_rejected_from_hard_bound(unbounded):
    request = _bounded_request()
    with pytest.raises(ValueError, match="provider-held"):
        BoundedRequest(
            payload={**request.payload, **unbounded},
            max_input_tokens=120_000,
        ).reservation_usd()


@pytest.mark.parametrize("remote_input", [
    {"type": "input_file", "file_id": "file_remote"},
    {"type": "input_image", "image_url": "https://example.invalid/large.png"},
    {"type": "item_reference", "id": "item_remote"},
])
def test_remote_file_and_image_input_are_rejected_from_hard_bound(remote_input):
    request = _bounded_request()
    with pytest.raises(ValueError, match="file or image"):
        BoundedRequest(
            payload={**request.payload, "input": [{"role": "user", "content": [remote_input]}]},
            max_input_tokens=120_000,
        ).reservation_usd()


def test_budget_ceiling_cannot_exceed_authorized_total():
    with pytest.raises(ValueError, match="5.00"):
        BudgetGuard(Decimal("5.01"))


def test_paid_dispatch_rejects_a_second_noncanonical_ledger(tmp_path, monkeypatch):
    canonical = _paid_budget(tmp_path, monkeypatch)
    request = _bounded_request()
    canonical.reserve_block("canonical", {"canonical-call": request})
    other = BudgetGuard(Decimal("5.00"), tmp_path / "other.json")
    other.reserve_block("other", {"other-call": request})
    with pytest.raises(ValueError, match="canonical cumulative"):
        dispatch_live(
            enabled=True,
            client=_FakeClient([], {"usage": None}),
            request=request,
            budget=other,
            block_id="other",
            call_id="other-call",
        )


def test_two_canonical_guards_cannot_spend_against_stale_state(tmp_path, monkeypatch):
    first = _paid_budget(tmp_path, monkeypatch)
    second = BudgetGuard.for_paid_pilot()
    request = _bounded_request(max_output_tokens=3_000)
    amount = request.reservation_usd()
    first.reserve_block("first", {"first-call": request})
    second.reserve_block("second", {"second-call": request})

    restarted = BudgetGuard.for_paid_pilot()
    assert restarted.reserved_usd == amount * 2


def test_unused_retry_allocations_release_only_when_block_is_closed(tmp_path):
    request = _bounded_request()
    ledger = tmp_path / "ledger.json"
    budget = BudgetGuard(Decimal("5.00"), ledger)
    budget.reserve_block("scenario", {"primary": request, "retry": request})
    budget.close_block("scenario")

    restarted = BudgetGuard(Decimal("5.00"), ledger)
    assert restarted.reserved_usd == Decimal("0.00")


def test_closing_an_exhausted_retry_block_is_safe_and_keeps_active_reservation(tmp_path):
    request = _bounded_request()
    ledger = tmp_path / "ledger.json"
    budget = BudgetGuard(Decimal("5.00"), ledger)
    budget.reserve_block("scenario", {"only-attempt": request})
    budget.activate("scenario", "only-attempt", request.reservation_usd())
    budget.close_block("scenario")

    restarted = BudgetGuard(Decimal("5.00"), ledger)
    assert restarted.reserved_usd == request.reservation_usd()


@pytest.mark.parametrize("amount", ["-0.01", "NaN", "Infinity"])
def test_corrupt_budget_ledger_fails_closed(tmp_path, amount):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({
        "ceiling_usd": "5.00",
        "spent_usd": amount,
        "reservations": {},
        "blocks": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid amount"):
        BudgetGuard(Decimal("5.00"), ledger)


def test_budget_ledger_rejects_spend_not_backed_by_settled_calls(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({
        "ceiling_usd": "5.00",
        "spent_usd": "4.50",
        "reservations": {},
        "blocks": {},
        "settled": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="spend history"):
        BudgetGuard(Decimal("5.00"), ledger)


def _paid_budget(tmp_path, monkeypatch):
    ledger = tmp_path / "canonical-ledger.json"
    monkeypatch.setattr(teaching_pilot, "PILOT_LEDGER_PATH", ledger)
    return BudgetGuard.for_paid_pilot()


def test_duplicate_source_identity_is_rejected_instead_of_overwritten():
    duplicate = LessonContinuity(
        **{
            **STATE.to_dict(),
            "source_versions": (
                ("garrett-phases", "gxt.garrett", "a" * 64),
                ("garrett-phases", "gxt.garrett", "c" * 64),
                ("afyz-continuations", "gxt.afyz", "b" * 64),
            ),
        }
    )
    with pytest.raises(ValueError, match="duplicate source identity"):
        prepare_variant(
            PilotVariant.DIRECT_LESSONS,
            controls=CONTROLS,
            state=duplicate,
            dialogue=("Teach me.",),
            sections=SECTIONS,
        )


class _FakeClient:
    def __init__(self, calls, result):
        self.calls = calls
        self.result = result
        self.responses = self

    def with_options(self, *, max_retries):
        assert max_retries == 0
        return self

    def create(self, **payload):
        self.calls.append(payload)
        return self.result


def test_frozen_benchmark_has_twelve_cases_and_separate_critical_failures():
    fixture = Path(__file__).parent / "fixtures" / "teaching_pilot_cases.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    assert len(payload["cases"]) == 12
    assert sum(len(case["turns"]) > 1 for case in payload["cases"]) >= 2
    assert any(case["held_out"] for case in payload["cases"])
    assert set(payload["critical_failures"]) == {
        "unsupported_correction",
        "fabricated_or_mismatched_source_timestamp",
        "meaning_reversing_condition_omitted",
        "cross_scope_leakage",
        "unusable_ready_exercise",
    }
    assert all("reference_answer" not in case for case in payload["cases"])
