from decimal import Decimal
import json
from pathlib import Path

import pytest

from mentor.teaching_pilot import (
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
    source_versions=(("gxt.garrett", "a" * 64), ("gxt.afyz", "b" * 64)),
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
        library_key="gxt.garrett",
        revision_sha256="a" * 64,
        lesson="Synthetic Garrett lesson",
        timestamp="00:01:00-00:02:00",
        text="Synthetic original source passage.",
    ),
    SourceSection(
        id="afyz-section",
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


def test_live_dispatch_fails_closed_before_touching_client():
    calls = []

    with pytest.raises(PilotDisabled):
        dispatch_live(
            enabled=False,
            client=lambda request: calls.append(request),
            request=prepare_variant(
                PilotVariant.REPAIRED_RETRIEVAL,
                controls=CONTROLS,
                state=STATE,
                dialogue=("Teach me.",),
            ),
            budget=BudgetGuard(Decimal("1.00")),
            call_id="case-1",
            reservation_usd=Decimal("0.25"),
        )

    assert calls == []


def test_budget_is_reserved_before_dispatch_and_unknown_usage_is_not_free():
    budget = BudgetGuard(Decimal("0.30"))
    request = prepare_variant(
        PilotVariant.REPAIRED_RETRIEVAL,
        controls=CONTROLS,
        state=STATE,
        dialogue=("Teach me.",),
    )
    calls = []

    dispatch_live(
        enabled=True,
        client=lambda item: calls.append(item) or {"usage_cost_usd": None},
        request=request,
        budget=budget,
        call_id="case-1",
        reservation_usd=Decimal("0.30"),
    )

    assert len(calls) == 1
    assert budget.reserved_usd == Decimal("0.30")
    with pytest.raises(BudgetExceeded):
        dispatch_live(
            enabled=True,
            client=lambda item: item,
            request=request,
            budget=budget,
            call_id="case-2",
            reservation_usd=Decimal("0.01"),
        )


def test_live_dispatch_rejects_a_non_conservative_partial_budget_reservation():
    budget = BudgetGuard(Decimal("1.00"))
    request = prepare_variant(
        PilotVariant.REPAIRED_RETRIEVAL,
        controls=CONTROLS,
        state=STATE,
        dialogue=("Teach me.",),
    )
    calls = []

    with pytest.raises(ValueError, match="remaining pilot budget"):
        dispatch_live(
            enabled=True,
            client=lambda item: calls.append(item) or {},
            request=request,
            budget=budget,
            call_id="under-reserved",
            reservation_usd=Decimal("0.01"),
        )

    assert calls == []


def test_reported_usage_releases_only_the_unused_reservation():
    budget = BudgetGuard(Decimal("1.00"))
    request = prepare_variant(
        PilotVariant.REPAIRED_RETRIEVAL,
        controls=CONTROLS,
        state=STATE,
        dialogue=("Teach me.",),
    )

    dispatch_live(
        enabled=True,
        client=lambda item: {"usage_cost_usd": "0.12"},
        request=request,
        budget=budget,
        call_id="case-1",
        reservation_usd=Decimal("1.00"),
    )

    assert budget.spent_usd == Decimal("0.12")
    assert budget.reserved_usd == Decimal("0.00")


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
