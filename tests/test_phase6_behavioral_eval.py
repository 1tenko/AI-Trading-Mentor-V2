import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.phase6_live_behavioral_eval import (
    FILE_SEARCH_CALL_USD,
    _response_cost,
    evaluate_case,
    prior_spend,
    remove_remote_resources,
    selected_case_names,
    validate_paid_run,
)


def test_behavioral_eval_refuses_execution_without_explicit_opt_in():
    environment = dict(os.environ)
    environment.pop("RUN_OPENAI_PHASE6_LIVE_EVAL", None)
    environment.pop("PHASE6_OPENAI_BUDGET_USD", None)
    environment.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [sys.executable, "scripts/phase6_live_behavioral_eval.py"],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert "Refusing paid execution" in result.stderr


def test_paid_run_budget_counts_prior_text_and_file_search_cost(tmp_path):
    audit_dir = tmp_path / "audits"
    audit_dir.mkdir()
    (audit_dir / "one.json").write_text(json.dumps({
        "estimated_text_cost_usd": 0.25, "response_calls": 4,
    }), encoding="utf-8")

    spent = prior_spend([audit_dir])

    assert spent == pytest.approx(0.25 + 4 * FILE_SEARCH_CALL_USD)
    approved = {"RUN_OPENAI_PHASE6_LIVE_EVAL": "1", "PHASE6_OPENAI_BUDGET_USD": "5"}
    assert validate_paid_run(approved, spent, 1.0) == pytest.approx(4.74)
    with pytest.raises(SystemExit, match="cumulative"):
        validate_paid_run(approved, 4.5, 0.51)
    with pytest.raises(SystemExit, match="explicit cumulative budget"):
        validate_paid_run({"RUN_OPENAI_PHASE6_LIVE_EVAL": "1"}, spent, 1.0)


def test_live_cost_projection_uses_current_standard_sol_rates():
    response = type("Response", (), {
        "usage": {
            "input_tokens": 1_000,
            "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
            "output_tokens": 1_000,
        }
    })()

    assert _response_cost(response) == pytest.approx(0.02338)


def test_shared_core_rubric_requires_all_mentor_searches_citations_and_attribution():
    text = (
        "SHARED_X is shared. AFYZ_ONLY_Y is Afyz-only. SPLASH_ONLY_Z is Splash-only. "
        "Garrett teaches GARRETT_RULE_A, while Afyz instead teaches AFYZ_RULE_B."
    )
    libraries = {f"gxt.{name}" for name in ("garrett", "afyz", "erik", "splash", "zay")}

    assert evaluate_case(
        "five_mentor_teaching", text=text,
        search_calls={key: 2 for key in libraries}, citation_libraries=libraries,
    ) == []
    assert "missing citation library: gxt.erik" in evaluate_case(
        "five_mentor_teaching", text=text,
        search_calls={key: 2 for key in libraries}, citation_libraries=libraries - {"gxt.erik"},
    )


def test_currentness_and_absence_rubrics_reject_overclaiming():
    assert evaluate_case(
        "garrett_currentness",
        text=("FOUNDATION_CORE remains Garrett's foundation. ADVANCED_QUALIFIER is the current "
              "advanced qualification. HISTORICAL_ONLY_A is retained as historical context."),
        search_calls={"gxt.garrett": 1}, citation_libraries={"gxt.garrett"},
    ) == []
    assert evaluate_case(
        "scoped_absence",
        text="Afyz teaches AFYZ_ONLY_Y; Garrett rejects AFYZ_ONLY_Y.",
        search_calls={"gxt.afyz": 1, "gxt.garrett": 1},
        citation_libraries={"gxt.afyz"},
    )


def test_coaching_rubric_accepts_faithful_user_facing_next_action():
    assert evaluate_case(
        "coaching_continuity",
        text=("AI recommendation: continue the experiment and label 20 examples. "
              "Theo can override the plan explicitly."),
        search_calls={}, citation_libraries=set(),
    ) == []


class _DeleteAPI:
    def __init__(self):
        self.deleted = []

    def delete(self, resource_id):
        self.deleted.append(resource_id)


class _FakeClient:
    def __init__(self):
        self.vector_stores = _DeleteAPI()
        self.files = _DeleteAPI()


def test_remote_cleanup_deletes_every_store_and_file_even_after_failure():
    client = _FakeClient()

    assert remove_remote_resources(client, ["vs_one", "vs_two"], ["file_one", "file_two"]) == []
    assert client.vector_stores.deleted == ["vs_two", "vs_one"]
    assert client.files.deleted == ["file_two", "file_one"]


def test_live_eval_can_resume_only_a_named_failed_case():
    assert selected_case_names({"PHASE6_LIVE_EVAL_CASES": "exact_timestamp"}) == ("exact_timestamp",)
    assert selected_case_names({"PHASE6_LIVE_EVAL_CASES": "coaching_continuity"}) == ("coaching_continuity",)
    with pytest.raises(SystemExit, match="unknown live evaluation case"):
        selected_case_names({"PHASE6_LIVE_EVAL_CASES": "not_a_case"})
