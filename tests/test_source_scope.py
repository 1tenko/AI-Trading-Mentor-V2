import json

import pytest

from mentor.project_models import AuthorityKind, PedagogicalRole, ThreadSourceBehavior
from mentor.source_scope import SearchPass, research_plan, resolve_source_scope, search_budget
from mentor.storage import Storage


def _project_with_libraries(tmp_path, keys=("gxt.garrett", "gxt.afyz", "gxt.erik")):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT Mastery")
    thread_id = storage.create_thread(
        "Project", behavior=ThreadSourceBehavior.PROJECT, project_id=project.id
    )
    libraries = {}
    for key in keys:
        name = key.split(".")[-1].replace("_", " ").title()
        role = {
            "gxt.garrett": PedagogicalRole.FULL_MODEL_CREATOR,
            "gxt.afyz": PedagogicalRole.FULL_MODEL_EDUCATOR,
        }.get(key, PedagogicalRole.SUPPORTING_PRACTICAL)
        library = storage.create_source_library(
            key, "gxt", name, AuthorityKind.MENTOR, name, role
        )
        storage.set_project_library(project.id, library.id, enabled=True)
        storage.set_library_vector_store(library.id, f"vs_{name.casefold().replace(' ', '_')}", "READY")
        libraries[key] = library
    return storage, project, thread_id, libraries


def test_saved_disabled_library_is_absent_from_effective_scope(tmp_path):
    storage, project, thread_id, libraries = _project_with_libraries(tmp_path)
    storage.set_project_library(project.id, libraries["gxt.afyz"].id, enabled=False)

    scope = resolve_source_scope(storage, storage.thread_context(thread_id), "Teach me GxT.")

    assert scope.library_keys == ("gxt.garrett", "gxt.erik")
    assert scope.vector_store_ids == ("vs_garrett", "vs_erik")
    assert "vs_afyz" not in scope.vector_store_ids


def test_exact_one_turn_override_uses_only_enabled_named_authority(tmp_path):
    storage, project, thread_id, libraries = _project_with_libraries(tmp_path)

    scope = resolve_source_scope(storage, storage.thread_context(thread_id), "Afyz only for this answer")

    assert scope.library_keys == ("gxt.afyz",)
    assert scope.temporary is True
    assert storage.safe_project_libraries(project.id)[0]["enabled"] is True
    storage.set_project_library(project.id, libraries["gxt.afyz"].id, enabled=False)
    with pytest.raises(ValueError, match="not enabled"):
        resolve_source_scope(storage, storage.thread_context(thread_id), "Afyz only")


@pytest.mark.parametrize(
    ("question", "expected"),
    (
        ("What does Afyz teach about X?", ("gxt.afyz",)),
        ("Where exactly does Erik teach X?", ("gxt.erik",)),
        ("Compare Garrett and Afyz on X.", ("gxt.garrett", "gxt.afyz")),
        ("Compare Afyz to Garrett on X.", ("gxt.garrett", "gxt.afyz")),
        ("Do not use all mentors; use Afyz only.", ("gxt.afyz",)),
        ("Compare Garrett and Afyz only.", ("gxt.garrett", "gxt.afyz")),
        ("I don't need all mentors. What does Afyz teach about X?", ("gxt.afyz",)),
        ("I don’t need all mentors. What does Afyz teach about X?", ("gxt.afyz",)),
    ),
)
def test_natural_named_questions_search_only_the_requested_mentors(tmp_path, question, expected):
    storage, _, thread_id, _ = _project_with_libraries(
        tmp_path, ("gxt.garrett", "gxt.afyz", "gxt.erik", "gxt.splash", "gxt.zay")
    )

    scope = resolve_source_scope(storage, storage.thread_context(thread_id), question)

    assert scope.library_keys == expected
    assert scope.temporary is True


def test_all_enabled_mentor_wording_keeps_every_enabled_authority(tmp_path):
    keys = ("gxt.garrett", "gxt.afyz", "gxt.erik", "gxt.splash", "gxt.zay")
    storage, _, thread_id, _ = _project_with_libraries(tmp_path, keys)

    scope = resolve_source_scope(
        storage,
        storage.thread_context(thread_id),
        "Teach me X using all enabled mentors.",
    )

    assert scope.library_keys == keys
    assert [library.pedagogical_role for library in scope.libraries] == [
        PedagogicalRole.FULL_MODEL_CREATOR,
        PedagogicalRole.FULL_MODEL_EDUCATOR,
        PedagogicalRole.SUPPORTING_PRACTICAL,
        PedagogicalRole.SUPPORTING_PRACTICAL,
        PedagogicalRole.SUPPORTING_PRACTICAL,
    ]


def test_compare_and_ignore_override_is_exact_and_does_not_mutate_saved_scope(tmp_path):
    storage, project, thread_id, _ = _project_with_libraries(tmp_path)

    scope = resolve_source_scope(
        storage, storage.thread_context(thread_id), "Compare Garrett and Erik, ignore Afyz."
    )

    assert scope.library_keys == ("gxt.garrett", "gxt.erik")
    assert scope.temporary is True
    assert len([item for item in storage.safe_project_libraries(project.id) if item["enabled"]]) == 3


def test_malformed_override_falls_back_to_saved_scope_without_broadening(tmp_path):
    storage, _, thread_id, _ = _project_with_libraries(tmp_path)

    scope = resolve_source_scope(
        storage, storage.thread_context(thread_id), "Compare Garrett, Erik, and an unknown mentor."
    )

    assert scope.library_keys == ("gxt.garrett", "gxt.afyz", "gxt.erik")
    assert scope.temporary is False


def test_general_and_legacy_threads_never_receive_project_stores(tmp_path):
    storage, _, _, _ = _project_with_libraries(tmp_path)
    general = storage.create_thread("General")
    legacy = storage.create_thread("Legacy", behavior=ThreadSourceBehavior.LEGACY_JACOB)

    assert resolve_source_scope(storage, storage.thread_context(general), "Teach me GxT.").library_keys == ()
    assert resolve_source_scope(storage, storage.thread_context(legacy), "Teach me GxT.").library_keys == ()


def test_more_than_six_enabled_libraries_requires_a_subset(tmp_path):
    keys = tuple(f"gxt.mentor_{index}" for index in range(7))
    storage, _, thread_id, _ = _project_with_libraries(tmp_path, keys)

    with pytest.raises(ValueError, match="up to six"):
        resolve_source_scope(storage, storage.thread_context(thread_id), "Teach me GxT.")


@pytest.mark.parametrize(
    ("depth", "passes", "overall", "results"),
    (("normal", 1, 6, 8), ("deep", 2, 12, 12), ("exhaustive", 3, 18, 16)),
)
def test_exact_search_budgets(depth, passes, overall, results):
    budget = search_budget(depth)
    assert (budget.per_library_passes, budget.overall_passes, budget.results_per_pass) == (
        passes, overall, results
    )


def test_normal_gxt_teaching_plans_a_pass_for_each_enabled_mentor(tmp_path):
    keys = ("gxt.garrett", "gxt.afyz", "gxt.erik", "gxt.splash", "gxt.zay")
    storage, _, thread_id, _ = _project_with_libraries(tmp_path, keys)
    scope = resolve_source_scope(storage, storage.thread_context(thread_id), "Teach me how X works in GxT.")

    plan = research_plan(scope, "Teach me how X works in GxT.", "normal")

    assert plan == tuple(SearchPass(key, 1, 8) for key in keys)


def test_exhaustive_plan_is_bounded_and_complementary(tmp_path):
    storage, _, thread_id, _ = _project_with_libraries(tmp_path)
    scope = resolve_source_scope(storage, storage.thread_context(thread_id), "Compare every mentor.")

    plan = research_plan(scope, "Compare every mentor.", "exhaustive")

    assert len(plan) == 9
    assert {item.pass_number for item in plan} == {1, 2, 3}
    assert all(item.results_per_pass == 16 for item in plan)


def test_source_scope_snapshot_is_safe_and_historically_stable(tmp_path):
    storage, project, thread_id, libraries = _project_with_libraries(tmp_path)
    scope = resolve_source_scope(storage, storage.thread_context(thread_id), "Afyz only")
    storage.record_thread_source_scope(thread_id, 1, scope.safe_snapshot())
    storage.set_project_library(project.id, libraries["gxt.afyz"].id, enabled=False)

    historic = storage.thread_source_scope(thread_id, 1)

    assert historic == {
        "library_keys": ["gxt.afyz"],
        "temporary": True,
        "override": "only",
    }
    assert "vs_" not in json.dumps(historic)

    assert storage.delete_thread(thread_id) is True
    assert storage.thread_source_scope(thread_id, 1) is None


def test_garrett_currentness_is_a_library_internal_hint_not_global_priority(tmp_path):
    storage, _, thread_id, _ = _project_with_libraries(tmp_path)
    scope = resolve_source_scope(
        storage, storage.thread_context(thread_id), "What does Garrett currently teach about X?"
    )

    assert scope.garrett_current_first is True
    assert scope.library_keys == ("gxt.garrett",)


def test_exact_project_timestamp_request_always_plans_source_research(tmp_path):
    storage, _, thread_id, _ = _project_with_libraries(tmp_path, ("gxt.garrett",))
    question = "Where exactly does Garrett state TIMESTAMP_RULE? Give the timestamp."
    scope = resolve_source_scope(storage, storage.thread_context(thread_id), question)

    assert research_plan(scope, question, "normal") == (
        SearchPass("gxt.garrett", 1, 8),
    )
