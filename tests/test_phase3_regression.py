import json
from pathlib import Path

from mentor.evaluation import EvaluationCase, EvaluationMetrics, run_evaluation


REQUIRED_CASE_CATEGORIES = {
    "source_authority",
    "provenance",
    "coverage",
    "anchor_precision",
    "evolution",
    "conflict",
    "orientation",
    "baseline_comparison",
    "exhaustive",
    "exact_timestamp",
    "adversarial_correction",
}


def test_synthetic_phase3_cases_cover_structural_and_semantic_regression_categories():
    fixture_path = Path(__file__).parent / "fixtures" / "phase3_evaluation_cases.json"
    values = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = tuple(EvaluationCase(**value) for value in values)

    assert {case.category for case in cases} == REQUIRED_CASE_CATEGORIES
    assert len({case.case_id for case in cases}) == len(cases)
    assert all("Jacob" not in case.prompt for case in cases)

    report = run_evaluation(
        "deterministic",
        cases,
        lambda _case: EvaluationMetrics(
            correctness_state="passed",
            completeness_state="passed",
            source_discipline_state="passed",
            citation_count=1,
            connection_state="passed",
            evolution_state="passed",
            correction_state="passed",
            orientation_calls=1,
            orientation_record_ids=("rec_synthetic",),
            raw_search_calls=1,
            retrieved_passage_count=1,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            estimated_cost_usd=0.0,
        ),
    )

    assert report.summary.case_count == len(REQUIRED_CASE_CATEGORIES)
    assert report.summary.failed_count == 0
