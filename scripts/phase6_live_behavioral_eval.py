"""Isolated, synthetic-only Phase 6 behavioral proof with mandatory cleanup."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Iterable, Mapping
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mentor.chat_service import ChatService, EvaluationConfig  # noqa: E402
from mentor.config import load_config  # noqa: E402
from mentor.project_models import CanonicalRole, ThreadSourceBehavior  # noqa: E402
from mentor.source_libraries import SourceImportService  # noqa: E402
from mentor.storage import Storage  # noqa: E402


MODEL = "gpt-5.6-sol"
MAX_APPROVED_USD = 5.0
FILE_SEARCH_CALL_USD = 0.0025
NEXT_CALL_RESERVE_USD = 0.15
PROJECTED_RUN_USD = 2.0
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "phase6"
SOURCES = {
    "gxt.garrett": (
        ("garrett.txt", "Archive/garrett.txt", CanonicalRole.GARRETT_ARCHIVAL_AND_COMPLEMENTARY),
        ("garrett_foundation.txt", "Anomaly Mentorship/Beginner/garrett_foundation.txt", CanonicalRole.CURRENT_CANONICAL_FOUNDATION),
        ("garrett_advanced.txt", "Anomaly Mentorship/GxT Advanced/garrett_advanced.txt", CanonicalRole.CURRENT_CANONICAL_ADVANCED),
    ),
    "gxt.afyz": (("afyz.txt", "afyz.txt", None),),
    "gxt.erik": (("erik.txt", "erik.txt", None),),
    "gxt.splash": (("splash.txt", "splash.txt", None),),
    "gxt.zay": (("zay.txt", "zay.txt", None),),
}


@dataclass(frozen=True)
class Rubric:
    terms: tuple[str | tuple[str, ...], ...]
    searches: tuple[str, ...]
    citations: tuple[str, ...]
    forbidden: tuple[str, ...] = ()
    minimum_searches: int = 1


RUBRICS = {
    "five_mentor_teaching": Rubric(
        ("SHARED_X", "AFYZ_ONLY_Y", "SPLASH_ONLY_Z", "GARRETT_RULE_A", "AFYZ_RULE_B"),
        tuple(SOURCES), tuple(SOURCES), minimum_searches=2,
    ),
    "scoped_absence": Rubric(
        ("AFYZ_ONLY_Y",), ("gxt.afyz", "gxt.garrett"), ("gxt.afyz",),
        ("Garrett rejects AFYZ_ONLY_Y", "Garrett disproves AFYZ_ONLY_Y"),
    ),
    "garrett_currentness": Rubric(
        ("FOUNDATION_CORE", "ADVANCED_QUALIFIER", "HISTORICAL_ONLY_A"),
        ("gxt.garrett",), ("gxt.garrett",),
    ),
    "exact_timestamp": Rubric(
        ("TIMESTAMP_RULE", "01:00"), ("gxt.garrett",), ("gxt.garrett",),
    ),
    "coaching_continuity": Rubric(
        (("LABEL_TWENTY_EXAMPLES", "label 20 examples"), "override"), (), (),
    ),
}

LIVE_CASES = {
    "five_mentor_teaching": (
        tuple(SOURCES),
        "Teach me synthetic setup Alpha in GxT. Preserve exact uppercase rule tokens, shared teaching, mentor-specific additions, and disagreement.",
        "deep",
    ),
    "scoped_absence": (
        ("gxt.afyz", "gxt.garrett"),
        "Compare Garrett and Afyz on AFYZ_ONLY_Y. State only what this scoped search supports.",
        "normal",
    ),
    "garrett_currentness": (
        ("gxt.garrett",),
        "What does Garrett currently teach about synthetic setup Alpha, and how does it relate to his Foundation and historical material? Preserve exact uppercase rule tokens.",
        "normal",
    ),
    "exact_timestamp": (
        ("gxt.garrett",),
        "Where exactly does Garrett state TIMESTAMP_RULE? Give the timestamp and preserve the exact uppercase rule token.",
        "normal",
    ),
    "coaching_continuity": (
        (),
        "I am tempted to change direction before finishing the experiment. What should I do today, and can I override the plan?",
        "normal",
    ),
}


def evaluate_case(
    name: str, *, text: str, search_calls: Mapping[str, int],
    citation_libraries: Iterable[str],
) -> list[str]:
    rubric = RUBRICS[name]
    citations = set(citation_libraries)
    failures = []
    for requirement in rubric.terms:
        options = (requirement,) if isinstance(requirement, str) else requirement
        if not any(option in text for option in options):
            failures.append(f"missing term: {' or '.join(options)}")
    failures += [
        f"missing search library: {key}" for key in rubric.searches
        if int(search_calls.get(key, 0)) < rubric.minimum_searches
    ]
    failures += [f"missing citation library: {key}" for key in rubric.citations if key not in citations]
    failures += [f"forbidden overclaim: {term}" for term in rubric.forbidden if term.casefold() in text.casefold()]
    return failures


def prior_spend(audit_directories: Iterable[Path]) -> float:
    total = 0.0
    for directory in audit_directories:
        for path in directory.glob("*.json") if directory.is_dir() else ():
            try:
                audit = json.loads(path.read_text(encoding="utf-8"))
                total += float(audit.get("estimated_text_cost_usd") or 0)
                known_search = audit.get("known_file_search_call_cost_usd")
                total += (
                    float(known_search) if known_search is not None
                    else int(audit.get("response_calls") or 0) * FILE_SEARCH_CALL_USD
                )
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                raise RuntimeError(f"Cannot reconcile prior Phase 6 spend audit: {path.name}") from None
    return round(total, 6)


def validate_paid_run(environment: Mapping[str, str], spent: float, projected: float) -> float:
    if environment.get("RUN_OPENAI_PHASE6_LIVE_EVAL") != "1":
        raise SystemExit("Refusing paid execution: set RUN_OPENAI_PHASE6_LIVE_EVAL=1 explicitly.")
    try:
        ceiling = float(environment["PHASE6_OPENAI_BUDGET_USD"])
    except (KeyError, TypeError, ValueError):
        raise SystemExit("Refusing paid execution: set an explicit cumulative budget at or below $5.") from None
    if not 0 < ceiling <= MAX_APPROVED_USD:
        raise SystemExit("Refusing paid execution: explicit cumulative budget must be at or below $5.")
    if spent < 0 or projected <= 0 or spent + projected > ceiling:
        raise SystemExit("Refusing paid execution: projected cumulative Phase 6 spend exceeds the explicit ceiling.")
    return round(ceiling - spent, 6)


def selected_case_names(environment: Mapping[str, str]) -> tuple[str, ...]:
    requested = environment.get("PHASE6_LIVE_EVAL_CASES", "").strip()
    names = tuple(part.strip() for part in requested.split(",") if part.strip()) or tuple(LIVE_CASES)
    unknown = set(names) - LIVE_CASES.keys()
    if unknown:
        raise SystemExit(f"Refusing unknown live evaluation case: {sorted(unknown)[0]}")
    return names


def remove_remote_resources(client, store_ids: Iterable[str], file_ids: Iterable[str]) -> list[str]:
    errors = []
    for store_id in reversed(list(store_ids)):
        try:
            client.vector_stores.delete(store_id)
        except Exception as error:  # cleanup must continue across every resource
            errors.append(f"vector_store:{type(error).__name__}")
    for file_id in reversed(list(file_ids)):
        try:
            client.files.delete(file_id)
        except Exception as error:
            errors.append(f"file:{type(error).__name__}")
    return errors


def _dict(value):
    return value if isinstance(value, dict) else value.model_dump(mode="json")


def _response_cost(response) -> float:
    usage = _dict(response.usage) if response.usage is not None else {}
    details = usage.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    cache_write = int(details.get("cache_write_tokens") or 0)
    inputs = int(usage.get("input_tokens") or 0)
    outputs = int(usage.get("output_tokens") or 0)
    return (
        max(inputs - cached - cache_write, 0) * 4 / 1_000_000
        + cached * 0.4 / 1_000_000
        + cache_write * 5 / 1_000_000
        + outputs * 20 / 1_000_000
    )


class _MeteredResponses:
    def __init__(self, responses, prior: float, ceiling: float):
        self.responses = responses
        self.prior = prior
        self.ceiling = ceiling
        self.items = []
        self.file_search_calls = 0

    @property
    def cost(self) -> float:
        return sum(_response_cost(response) for response in self.items) + self.file_search_calls * FILE_SEARCH_CALL_USD

    def create(self, **request):
        if self.prior + self.cost + NEXT_CALL_RESERVE_USD > self.ceiling:
            raise RuntimeError("Cumulative Phase 6 spend guard stopped before the next paid call.")
        response = self.responses.create(**request)
        self.items.append(response)
        self.file_search_calls += sum(
            _dict(item).get("type") == "file_search_call" for item in response.output
        )
        return response


def _wait_ready(client, store_id: str, file_id: str) -> str:
    for _ in range(120):
        item = client.vector_stores.files.retrieve(file_id, vector_store_id=store_id)
        if item.status == "completed":
            return item.id
        if item.status in {"failed", "cancelled"}:
            raise RuntimeError("Synthetic source indexing failed.")
        time.sleep(0.5)
    raise TimeoutError("Synthetic source indexing did not finish.")


def _install_synthetic_sources(
    storage: Storage, client, project_id: int, stores: dict, files: list[str],
    library_keys: Iterable[str],
) -> None:
    service = SourceImportService(storage)
    for key in library_keys:
        definitions = SOURCES[key]
        library = service.ensure_library(key)
        store = client.vector_stores.create(name=f"Phase 6 live evaluation {key}", metadata={"library_key": key})
        stores[key] = store.id
        storage.set_library_vector_store(library.id, store.id, "READY")
        storage.set_project_library(project_id, library.id, enabled=True)
        for filename, relative_path, role in definitions:
            path = FIXTURE_ROOT / filename
            with path.open("rb") as source:
                uploaded = client.files.create(file=source, purpose="assistants")
            files.append(uploaded.id)
            attributes = {"library_key": key, "source_revision_key": f"synthetic-{filename}", "timestamps_available": "true"}
            if role is not None:
                attributes["canonical_role"] = role.value
            client.vector_stores.files.create(store.id, file_id=uploaded.id, attributes=attributes)
            vector_file_id = _wait_ready(client, store.id, uploaded.id)
            service.register_local_revision(
                key, path, relative_path, canonical_role=role, file_id=uploaded.id,
                vector_store_file_id=vector_file_id, index_state="READY",
            )


def _citation_libraries(storage: Storage, answer) -> set[str]:
    return {
        library.library_key
        for citation in answer.citations
        if (library := storage.source_library_for_file(citation.file_id)) is not None
    }


def _run_case(storage, responses, project_id: int, name: str, prompt: str, depth: str):
    thread_id = storage.create_thread(name, behavior=ThreadSourceBehavior.PROJECT, project_id=project_id)
    if name == "coaching_continuity":
        storage.apply_project_state_event(
            project_id=project_id, event_key="live-experiment", kind="EXPERIMENT",
            payload={"operation": "SET", "value": "TEST_FROZEN_ENTRY"},
            origin_thread_id=thread_id, origin_turn_number=1,
        )
        storage.apply_project_state_event(
            project_id=project_id, event_key="live-next", kind="NEXT_ACTION",
            payload={"operation": "SET", "value": "LABEL_TWENTY_EXAMPLES"},
            origin_thread_id=thread_id, origin_turn_number=1,
        )
    started = time.perf_counter()
    answer = ChatService(storage, SimpleNamespace(responses=responses), model=MODEL).reply(
        thread_id, prompt, EvaluationConfig(research_depth=depth)
    )
    failures = evaluate_case(
        name, text=answer.text, search_calls=answer.diagnostics.mentor_search_calls,
        citation_libraries=_citation_libraries(storage, answer),
    )
    return {
        "name": name,
        "passed": not failures,
        "failures": failures,
        "search_calls": answer.diagnostics.mentor_search_calls,
        "citation_count": len(answer.citations),
        "input_tokens": answer.diagnostics.input_tokens,
        "output_tokens": answer.diagnostics.output_tokens,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "answer": answer.text,
    }


def main() -> int:
    audits = [
        ROOT / "data" / "phase6-contract-audits",
        ROOT / "data" / "phase6-live-eval",  # retained pre-final evaluation audits
        ROOT / "data" / "phase6-live-evals",
    ]
    spent_before = prior_spend(audits)
    selected = selected_case_names(os.environ)
    projected = max(0.5, PROJECTED_RUN_USD * len(selected) / len(LIVE_CASES))
    remaining = validate_paid_run(os.environ, spent_before, projected)
    from openai import OpenAI

    client = OpenAI(api_key=load_config(os.environ, ROOT / ".env").api_key)
    ceiling = float(os.environ["PHASE6_OPENAI_BUDGET_USD"])
    meter = _MeteredResponses(client.responses, spent_before, ceiling)
    run_id = uuid4().hex
    run_dir = ROOT / "data" / "phase6-live-evals"
    run_dir.mkdir(parents=True, exist_ok=True)
    database = run_dir / f"{run_id}.sqlite3"
    storage = Storage(database)
    storage.initialize()
    project = storage.create_project("Synthetic GxT evaluation")
    stores: dict[str, str] = {}
    files: list[str] = []
    results = []
    cleanup_errors = []
    outcome = "FAILED"
    started = time.perf_counter()
    try:
        required_libraries = tuple(dict.fromkeys(
            key for name in selected for key in LIVE_CASES[name][0]
        ))
        _install_synthetic_sources(storage, client, project.id, stores, files, required_libraries)
        for name in selected:
            enabled, prompt, depth = LIVE_CASES[name]
            for key in required_libraries:
                storage.set_project_library(
                    project.id, storage.source_library(key).id, enabled=key in enabled
                )
            results.append(_run_case(storage, meter, project.id, name, prompt, depth))
        if any(not result["passed"] for result in results):
            raise RuntimeError("One or more synthetic behavioral rubrics failed.")
        outcome = "PASSED"
    finally:
        cleanup_errors = remove_remote_resources(client, stores.values(), files)
        audit = {
            "run_id": run_id,
            "outcome": outcome,
            "model": MODEL,
            "prior_spend_usd": spent_before,
            "remaining_before_run_usd": remaining,
            "approved_cumulative_ceiling_usd": ceiling,
            "response_calls": len(meter.items),
            "file_search_calls": meter.file_search_calls,
            "estimated_text_cost_usd": round(sum(_response_cost(item) for item in meter.items), 6),
            "known_file_search_call_cost_usd": round(meter.file_search_calls * FILE_SEARCH_CALL_USD, 6),
            "estimated_run_cost_usd": round(meter.cost, 6),
            "estimated_cumulative_cost_usd": round(spent_before + meter.cost, 6),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "results": results,
            "cleanup": "complete" if not cleanup_errors else cleanup_errors,
        }
        (run_dir / f"{run_id}.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
        if cleanup_errors:
            raise RuntimeError(f"Synthetic remote cleanup failed: {cleanup_errors}")
    print(json.dumps({
        "result": outcome, "cases": len(results), "estimated_run_cost_usd": round(meter.cost, 6),
        "estimated_cumulative_cost_usd": round(spent_before + meter.cost, 6), "cleanup": "complete",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
