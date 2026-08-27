from dataclasses import replace

import pytest

from mentor.profile import (
    QUESTIONNAIRE_FIELDS,
    ProfileService,
    ProfileValidationError,
    full_questionnaire_profile_context,
    questionnaire_field_state,
    select_profile_context,
    strategy_profile_context,
)
from mentor.storage import Storage


def test_profile_service_normalizes_explicit_user_records_and_rejects_invalid_lifecycle_pairs(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)

    item = profile.create_item(
        category="schedule/horizon",
        subject="  Holding   period ",
        value="  I hold trades for two to five days.  ",
        kind="preference",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )

    assert (item.subject, item.subject_key, item.value, item.state) == (
        "Holding period",
        "holding period",
        "I hold trades for two to five days.",
        "confirmed",
    )
    with pytest.raises(ProfileValidationError, match="AI_INFERRED.*tentative"):
        profile.create_item(
            category="schedule/horizon",
            subject="Risk",
            value="Risk is low.",
            kind="constraint",
            provenance="AI_INFERRED",
            state="confirmed",
            origin_kind="chat",
        )
    with pytest.raises(ProfileValidationError, match="category"):
        profile.create_item(
            category="unknown",
            subject="Risk",
            value="Risk is low.",
            kind="constraint",
            provenance="USER_STATED",
            state="confirmed",
            origin_kind="profile-editor",
        )


def test_profile_service_only_supersedes_a_confirmed_unambiguous_predecessor(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    predecessor = profile.create_item(
        category="schedule/horizon",
        subject="Holding period",
        value="I hold for days.",
        kind="preference",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    tentative = profile.propose_item(
        category="style/methodology",
        subject="Entry style",
        value="I prefer breakouts.",
        kind="preference",
        origin_kind="chat",
    )

    successor = profile.supersede_item(
        predecessor.id,
        value="I now day trade only.",
        provenance="USER_DECISION",
        origin_kind="confirmation",
    )

    assert storage.profile_item(predecessor.id).state == "superseded"
    assert successor.state == "confirmed"
    assert successor.supersedes_item_id == predecessor.id
    with pytest.raises(ProfileValidationError, match="confirmed"):
        profile.supersede_item(
            tentative.id,
            value="I prefer mean reversion.",
            provenance="USER_DECISION",
            origin_kind="confirmation",
        )


def test_profile_service_confirms_only_a_tentative_inference_and_preserves_history(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    proposal = profile.propose_item(
        category="goals/research",
        subject="Learning goal",
        value="I am studying Jacob's material.",
        kind="goal",
        origin_kind="chat",
    )

    confirmed = profile.confirm_item(proposal.id, origin_kind="confirmation")

    assert storage.profile_item(proposal.id).state == "superseded"
    assert confirmed == storage.current_confirmed_profile_items()[0]
    assert (confirmed.provenance, confirmed.state, confirmed.supersedes_item_id) == (
        "USER_CONFIRMED",
        "confirmed",
        proposal.id,
    )
    with pytest.raises(ProfileValidationError, match="tentative"):
        profile.confirm_item(confirmed.id, origin_kind="confirmation")


def test_profile_service_conflict_requires_distinct_current_competitors_and_is_atomic(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    first = profile.propose_item(
        category="style/methodology",
        subject="Entry style",
        value="I prefer breakouts.",
        kind="preference",
        origin_kind="chat",
    )
    second = profile.propose_item(
        category="style/methodology",
        subject="Entry style",
        value="I prefer mean reversion.",
        kind="preference",
        origin_kind="chat",
    )
    different_subject = profile.propose_item(
        category="style/methodology",
        subject="Exit style",
        value="I use fixed targets.",
        kind="preference",
        origin_kind="chat",
    )
    different_category = profile.propose_item(
        category="markets/instruments",
        subject="Primary market",
        value="I trade ES.",
        kind="preference",
        origin_kind="chat",
    )
    historical = profile.create_item(
        category="schedule/horizon",
        subject="Available session",
        value="London open",
        kind="constraint",
        provenance="USER_STATED",
        state="archived",
        origin_kind="profile-editor",
    )

    for invalid_ids in (
        [first.id, different_subject.id],
        [first.id, different_category.id],
        [first.id, historical.id],
        [first.id, first.id],
    ):
        with pytest.raises(ProfileValidationError):
            profile.conflict_items(invalid_ids)
        assert [storage.profile_item(item.id).state for item in (first, second, different_subject, different_category, historical)] == [
            "tentative",
            "tentative",
            "tentative",
            "tentative",
            "archived",
        ]

    assert profile.conflict_items([first.id, second.id]) == 2
    assert [storage.profile_item(item.id).state for item in (first, second)] == [
        "conflicting",
        "conflicting",
    ]


def test_profile_service_defers_conflict_eligibility_to_atomic_storage(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    first = profile.propose_item(
        category="style/methodology",
        subject="Entry style",
        value="I prefer breakouts.",
        kind="preference",
        origin_kind="chat",
    )
    second = profile.propose_item(
        category="style/methodology",
        subject="Entry style",
        value="I prefer mean reversion.",
        kind="preference",
        origin_kind="chat",
    )

    monkeypatch.setattr(storage, "profile_item", lambda _item_id: pytest.fail("preflight read"))

    assert profile.conflict_items([first.id, second.id]) == 2


def test_profile_selection_uses_only_relevant_current_records_and_never_source_questions(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    goal = profile.create_item(
        category="goals/research",
        subject="Research goal",
        value="I am building a backtest.",
        kind="goal",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    style = profile.create_item(
        category="style/methodology",
        subject="Entry style",
        value="I prefer breakouts.",
        kind="preference",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    tentative = profile.propose_item(
        category="execution/risk/constraints",
        subject="Risk",
        value="I risk one percent.",
        kind="constraint",
        origin_kind="chat",
    )
    archived = profile.create_item(
        category="preferences/discretion",
        subject="Answer format",
        value="I prefer bullets.",
        kind="preference",
        provenance="USER_STATED",
        state="archived",
        origin_kind="profile-editor",
    )

    selected = select_profile_context(
        "How should I structure a backtest for this breakout setup?",
        [archived, tentative, style, goal],
    )

    assert selected.item_ids == (goal.id,)
    assert selected.character_count == len(selected.context)
    assert "Research goal: I am building a backtest." in selected.context
    assert select_profile_context("What did Jacob say at timestamp 12:23?", [goal, style]).item_ids == ()


def test_profile_selection_is_deterministic_deduplicated_and_bounded(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    items = [
        profile.create_item(
            category="goals/research",
            subject=f"Research goal {index}",
            value=f"Goal {index}",
            kind="goal",
            provenance="USER_STATED",
            state="confirmed",
            origin_kind="profile-editor",
        )
        for index in range(7)
    ]
    duplicate = replace(items[0], id=999)

    selected = select_profile_context("Help me research and backtest this.", list(reversed(items)) + [duplicate])

    assert selected.item_ids == tuple(item.id for item in items[:6])
    assert len(selected.items) == 6
    assert selected.character_count <= 1200
    assert duplicate.id not in selected.item_ids


def test_profile_selection_excludes_unrelated_records_in_a_relevant_category(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    matching_market = profile.create_item(
        category="goals/research",
        subject="Market research",
        value="I study ES opening range setups.",
        kind="goal",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    matching_setup = profile.create_item(
        category="goals/research",
        subject="Research focus",
        value="I backtest opening range breakouts.",
        kind="goal",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    unrelated = profile.create_item(
        category="goals/research",
        subject="Learning goal",
        value="I am improving trading psychology.",
        kind="goal",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )

    selected = select_profile_context(
        "How do I backtest ES opening range breakouts?",
        [unrelated, matching_setup, matching_market],
    )

    assert selected.item_ids == (matching_market.id, matching_setup.id)
    assert unrelated.id not in selected.item_ids


def test_profile_selection_keeps_research_intent_context_across_schedule_and_risk(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    research = profile.create_item(
        category="goals/research",
        subject="Research focus",
        value="I backtest opening range breakouts.",
        kind="goal",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    unrelated_research = profile.create_item(
        category="goals/research",
        subject="Learning goal",
        value="I am improving trading psychology.",
        kind="goal",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    available_session = profile.create_item(
        category="schedule/horizon",
        subject="Available session",
        value="I trade the London open on weekdays.",
        kind="constraint",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    risk = profile.create_item(
        category="execution/risk/constraints",
        subject="Maximum risk",
        value="I risk one percent per position.",
        kind="constraint",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )

    selected = select_profile_context(
        "How do I backtest opening range breakouts?",
        [unrelated_research, available_session, risk, research],
    )

    assert selected.item_ids == (risk.id, available_session.id, research.id)
    assert unrelated_research.id not in selected.item_ids


def _confirmed(profile, *, category, subject, value, kind="constraint", provenance="USER_STATED"):
    return profile.create_item(
        category=category,
        subject=subject,
        value=value,
        kind=kind,
        provenance=provenance,
        state="confirmed",
        origin_kind="profile-editor",
    )


def test_profile_selector_contract_a_research_selects_available_session_without_literal_overlap(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    session = _confirmed(ProfileService(storage), category="schedule/horizon", subject="Available session", value="London open")

    assert select_profile_context("Design a robust backtest.", [session]).item_ids == (session.id,)


def test_profile_selector_contract_b_research_selects_risk_constraint_without_literal_overlap(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    risk = _confirmed(ProfileService(storage), category="execution/risk/constraints", subject="Maximum risk", value="One percent")

    assert select_profile_context("Help me research this setup.", [risk]).item_ids == (risk.id,)


def test_profile_selector_contract_c_same_category_needs_item_applicability(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    holding = _confirmed(profile, category="schedule/horizon", subject="Holding period", value="Two days", kind="preference")
    session = _confirmed(profile, category="schedule/horizon", subject="Available session", value="London open")

    assert select_profile_context("How should I plan a swing trade holding period?", [session, holding]).item_ids == (holding.id,)


def test_profile_selector_contract_d_subject_affinity_selects_holding_period(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    holding = _confirmed(profile, category="schedule/horizon", subject="Holding period", value="Two to five days", kind="preference")
    session = _confirmed(profile, category="schedule/horizon", subject="Available session", value="London open")

    assert select_profile_context("What holding horizon fits this trade plan?", [session, holding]).item_ids == (holding.id,)


def test_profile_selector_contract_e_distinctive_value_is_an_explicit_reference(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    es = _confirmed(profile, category="markets/instruments", subject="Primary market", value="ES", kind="preference")
    nq = _confirmed(profile, category="markets/instruments", subject="Secondary market", value="NQ", kind="preference")

    selected = select_profile_context("Help me make a plan for ES.", [nq, es])

    assert selected.item_ids == (es.id,)
    assert selected.reasons == ("explicit_reference",)
    assert selected.tiers == (1,)


def test_profile_selector_contract_f_generic_value_tokens_are_not_explicit_references(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    style = _confirmed(
        ProfileService(storage),
        category="style/methodology",
        subject="Entry style",
        value="Daily trading strategy",
        kind="preference",
    )

    assert select_profile_context("Explain a good trading strategy.", [style]).item_ids == ()


def test_profile_selector_contract_g_exact_source_lookup_has_no_profile_context(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    item = _confirmed(ProfileService(storage), category="markets/instruments", subject="Primary market", value="ES", kind="preference")

    assert select_profile_context("What does Jacob mean by Asset Synchronization?", [item]).item_ids == ()


def test_profile_selector_contract_h_source_application_can_select_personal_constraint(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    item = _confirmed(ProfileService(storage), category="schedule/horizon", subject="Available session", value="London open")

    assert select_profile_context(
        "What does Jacob mean by Asset Synchronization, and how does it apply to my London session?", [item]
    ).item_ids == (item.id,)


def test_profile_selector_contract_i_unrelated_question_selects_none(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    item = _confirmed(ProfileService(storage), category="execution/risk/constraints", subject="Maximum risk", value="One percent")

    assert select_profile_context("What is the capital of France?", [item]).item_ids == ()


def test_profile_selector_contract_j_nonconfirmed_records_never_select(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    tentative = profile.propose_item(
        category="schedule/horizon", subject="Available session", value="London open", kind="constraint", origin_kind="chat"
    )

    assert select_profile_context("Design a backtest.", [tentative]).item_ids == ()


def test_profile_selector_contract_k_priority_precedes_six_item_cap(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    generic = [
            _confirmed(profile, category="goals/research", subject=f"Research goal {index}", value=f"Goal {index}", kind="goal")
        for index in range(6)
    ]
    es = _confirmed(profile, category="markets/instruments", subject="Primary market", value="ES", kind="preference")

    selected = select_profile_context("Research a plan for ES.", generic + [es])

    assert len(selected.items) == 6
    assert selected.item_ids[0] == es.id


def test_profile_selector_contract_l_order_and_safe_diagnostics_are_deterministic(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    session = _confirmed(profile, category="schedule/horizon", subject="Available session", value="London open")
    risk = _confirmed(profile, category="execution/risk/constraints", subject="Maximum risk", value="One percent")

    first = select_profile_context("Design a backtest.", [risk, session])
    second = select_profile_context("Design a backtest.", [session, risk])

    assert first == second
    assert first.item_ids == tuple(item.id for item in first.items)
    assert first.character_count == len(first.context)
    assert first.reasons == ("structural_constraint", "structural_constraint")


def test_profile_selector_prioritizes_current_user_decision_within_goal_tier(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    stated = _confirmed(profile, category="goals/research", subject="Research goal alpha", value="Alpha", kind="goal")
    decision = _confirmed(
        profile,
        category="goals/research",
        subject="Research goal beta",
        value="Beta",
        kind="goal",
        provenance="USER_DECISION",
    )

    assert select_profile_context("Help me research.", [stated, decision]).item_ids == (decision.id, stated.id)


def test_profile_selector_does_not_treat_nontrading_script_tests_as_research(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    session = _confirmed(
        ProfileService(storage),
        category="schedule/horizon",
        subject="Available session",
        value="London open",
    )

    assert select_profile_context("Can you test this Python script?", [session]).item_ids == ()


def test_profile_selector_does_not_treat_general_trading_advice_as_an_execution_plan(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    market = _confirmed(
        profile,
        category="markets/instruments",
        subject="Primary market",
        value="ES",
        kind="preference",
    )
    session = _confirmed(
        profile,
        category="schedule/horizon",
        subject="Available session",
        value="London open",
    )
    risk = _confirmed(
        profile,
        category="execution/risk/constraints",
        subject="Maximum risk",
        value="One percent",
    )

    assert select_profile_context("Give me general trading advice.", [market, session, risk]).item_ids == ()


def test_questionnaire_has_all_fixed_human_fields_and_never_exposes_schema_choices():
    assert len(QUESTIONNAIRE_FIELDS) == 21
    assert [field.key for field in QUESTIONNAIRE_FIELDS[:20]] == [f"q{number}" for number in range(1, 21)]
    assert QUESTIONNAIRE_FIELDS[-1].key == "additional_information"
    assert QUESTIONNAIRE_FIELDS[0].subject == "trading objective"
    assert QUESTIONNAIRE_FIELDS[1].subject == "markets willing to trade"
    assert "consistent profitability" in QUESTIONNAIRE_FIELDS[0].helper
    assert "high-quality trades" in QUESTIONNAIRE_FIELDS[19].helper


def test_questionnaire_recognises_explicit_uncertainty_with_natural_suffixes(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)

    profile.save_questionnaire_answers({"q4": "I don't know yet"})

    assert profile.questionnaire_answers()["q4"].unknown is True


def test_questionnaire_batch_save_is_atomic_versioned_and_preserves_explicit_unknown(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)

    saved = profile.save_questionnaire_answers({"q1": "Build steady income.", "q4": "idk", "q2": "ES"})

    assert saved["q1"].value == "Build steady income."
    assert saved["q4"].value == "idk"
    assert profile.questionnaire_answers()["q4"].unknown is True
    revised = profile.save_questionnaire_answers({"q1": "Pass a prop evaluation.", "q2": "NQ"})
    assert revised["q1"].supersedes_item_id == saved["q1"].id
    assert profile.questionnaire_answers()["q1"].item.value == "Pass a prop evaluation."
    with pytest.raises(ProfileValidationError):
        profile.save_questionnaire_answers({"q1": "x" * 501, "q3": "London open"})
    assert profile.questionnaire_answers()["q3"] is None


def test_questionnaire_blank_new_answer_is_absent_and_clearing_archives_current_answer(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)

    assert profile.save_questionnaire_answers({"q5": "   "}) == {}
    current = profile.save_questionnaire_answers({"q5": "20 minutes"})["q5"]
    assert profile.save_questionnaire_answers({"q5": ""}) == {}
    assert profile.questionnaire_answers()["q5"] is None
    assert storage.profile_item(current.id).state == "archived"


def test_strategy_profile_context_is_bounded_and_represents_unknowns_without_asserting_them(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    profile.save_questionnaire_answers({
        "q1": "Build a consistent strategy.",
        "q2": "ES and NQ",
        "q4": "I don't know",
        "q13": "One percent maximum risk.",
        "q19": "A simple repeatable system.",
    })

    context = strategy_profile_context(storage.current_confirmed_profile_items())

    assert "Trader Strategy Profile" in context.context
    assert "[EXPLICITLY UNKNOWN] preferred trading style" in context.context
    assert "I don't know" not in context.context
    assert context.character_count <= 6000


def test_ordinary_profile_context_renders_explicit_uncertainty_as_unresolved(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    profile.save_questionnaire_answers({"q4": "idk"})

    selected = select_profile_context("What trading style should I research?", storage.current_confirmed_profile_items())

    assert "preferred trading style: User is currently unsure / has not decided." in selected.context
    assert "preferred trading style: idk" not in selected.context


def test_questionnaire_field_state_distinguishes_answered_unknown_and_unanswered_without_profile_writes(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    profile.save_questionnaire_answers({"q8": "idk", "q20": "Keep it simple."})
    current = storage.current_confirmed_profile_items()

    unknown = questionnaire_field_state("Based on my profile, what am I naturally good at?", current)
    blank = questionnaire_field_state("Which concepts do I currently trust most?", current)
    answered = questionnaire_field_state("What should you optimise around for me?", current)

    assert unknown is not None
    assert unknown.state == "explicitly_unknown"
    assert "trading strengths" in unknown.context
    assert "must not become a current profile fact" in unknown.context
    assert "Your Trader Profile says this is currently unresolved" in unknown.context
    assert blank is not None
    assert blank.state == "unanswered"
    assert "trusted and uncertain concepts" in blank.context
    assert "has not answered" in blank.context
    assert "You have not answered this in your Trader Profile" in blank.context
    assert answered is not None
    assert answered.state == "answered"
    assert "Keep it simple." in answered.context
    assert storage.current_confirmed_profile_items() == current


def test_questionnaire_field_state_is_bounded_and_only_emitted_for_a_direct_field_question(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    profile.save_questionnaire_answers({"q8": "idk"})

    state = questionnaire_field_state("What do you think I am naturally good at?", storage.current_confirmed_profile_items())

    assert state is not None
    assert state.character_count <= 600
    assert questionnaire_field_state("Explain Jacob's Asset Synchronization.", storage.current_confirmed_profile_items()) is None
    assert questionnaire_field_state("How does a reversal setup work?", storage.current_confirmed_profile_items()) is None
    assert questionnaire_field_state("How does a reversal setup work for me?", storage.current_confirmed_profile_items()) is None
    assert questionnaire_field_state("What trade setup is good for me?", storage.current_confirmed_profile_items()) is None


def test_strategy_profile_context_includes_every_short_questionnaire_answer_within_budget(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    profile.save_questionnaire_answers({field.key: field.key for field in QUESTIONNAIRE_FIELDS})

    context = strategy_profile_context(storage.current_confirmed_profile_items())

    assert len(context.items) == len(QUESTIONNAIRE_FIELDS)
    assert context.character_count <= 6000


def test_full_questionnaire_context_preserves_answered_unknown_and_unanswered_states_within_budget(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    profile.save_questionnaire_answers({
        "q1": "Ideally I want at least 70% win rate and at least 2R.",
        "q6": "I want at least one opportunity per day.",
        "q13": "idk",
        "q14": "High win rate, minimum 2R.",
        "q16": "idk",
        "q20": "idk",
    })

    context = full_questionnaire_profile_context(storage.current_confirmed_profile_items())

    assert context.answered_count == 3
    assert context.explicitly_unknown_count == 3
    assert context.unanswered_count == 15
    assert "[ANSWERED] trading objective: Ideally I want at least 70% win rate and at least 2R." in context.context
    assert "[ANSWERED] strategy priorities: High win rate, minimum 2R." in context.context
    assert "[EXPLICITLY UNKNOWN] risk and funding constraints" in context.context
    assert "[EXPLICITLY UNKNOWN] backtesting commitment" in context.context
    assert "[EXPLICITLY UNKNOWN] optimisation principles" in context.context
    assert "[UNANSWERED] trusted and uncertain concepts" in context.context
    assert context.character_count <= 6000


def test_full_questionnaire_context_keeps_every_field_state_when_answer_detail_exceeds_budget(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    profile.save_questionnaire_answers({field.key: "x" * 500 for field in QUESTIONNAIRE_FIELDS})

    context = full_questionnaire_profile_context(storage.current_confirmed_profile_items())

    assert context.character_count <= 6000
    assert context.unanswered_count == 0
    assert context.context.count("[ANSWERED]") + context.context.count("[ANSWERED — detail omitted for context budget]") == 21
    assert "[ANSWERED — detail omitted for context budget]" in context.context
    assert "[ANSWERED] risk and funding constraints: " in context.context
    assert "[ANSWERED] strategy priorities: " in context.context
    assert "[ANSWERED] strategy deal-breakers: " in context.context
    assert "[ANSWERED] backtesting commitment: " in context.context
