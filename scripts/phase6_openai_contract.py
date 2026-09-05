"""Disposable synthetic proof for Phase 6 native File Search ownership/citations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mentor.config import load_config  # noqa: E402


MODEL = "gpt-5.6-sol"
MAX_APPROVED_USD = 5.0
FIXTURES = {
    "gxt.garrett": "garrett.txt",
    "gxt.afyz": "afyz.txt",
    "gxt.erik": "erik.txt",
    "gxt.splash": "splash.txt",
    "gxt.zay": "zay.txt",
}


def _dict(value):
    return value if isinstance(value, dict) else value.model_dump(mode="json")


def _output(response) -> list[dict]:
    return [_dict(item) for item in response.output]


def _result_file_ids(output: list[dict]) -> set[str]:
    return {
        result["file_id"]
        for item in output if item.get("type") == "file_search_call"
        for result in item.get("results") or []
    }


def _citation_file_ids(output: list[dict]) -> set[str]:
    return {
        annotation["file_id"]
        for item in output if item.get("type") == "message"
        for content in item.get("content") or [] if content.get("type") == "output_text"
        for annotation in content.get("annotations") or [] if annotation.get("type") == "file_citation"
    }


def _text(output: list[dict]) -> str:
    return "".join(
        content.get("text", "")
        for item in output if item.get("type") == "message"
        for content in item.get("content") or [] if content.get("type") == "output_text"
    )


def _usage(response) -> tuple[int, int, int]:
    usage = _dict(response.usage) if response.usage is not None else {}
    return (
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        int(usage.get("total_tokens") or 0),
    )


def _wait_ready(client, store_id: str, file_id: str) -> None:
    for _ in range(120):
        status = client.vector_stores.files.retrieve(file_id, vector_store_id=store_id).status
        if status == "completed":
            return
        if status in {"failed", "cancelled"}:
            raise RuntimeError("Synthetic File Search fixture failed to index.")
        time.sleep(0.5)
    raise TimeoutError("Synthetic File Search fixture did not finish indexing.")


def _response(client, *, input_items, store_ids, max_output_tokens=1200):
    return client.responses.create(
        model=MODEL,
        instructions=(
            "This is a synthetic API contract test. Use only the supplied File Search stores. "
            "Keep exact synthetic tokens and mentor attribution. Attach native file citations to every source claim."
        ),
        input=input_items,
        tools=[{"type": "file_search", "vector_store_ids": store_ids, "max_num_results": 8}],
        tool_choice={"type": "file_search"},
        include=["reasoning.encrypted_content", "file_search_call.results"],
        reasoning={"effort": "high"},
        max_output_tokens=max_output_tokens,
        store=False,
    )


def main() -> int:
    if os.environ.get("RUN_OPENAI_PHASE6_CONTRACT_TEST") != "1":
        raise SystemExit("Refusing paid execution: set RUN_OPENAI_PHASE6_CONTRACT_TEST=1 explicitly.")
    ceiling = float(os.environ.get("PHASE6_OPENAI_BUDGET_USD", "0"))
    if not 0 < ceiling <= MAX_APPROVED_USD:
        raise SystemExit("Set PHASE6_OPENAI_BUDGET_USD to an explicit value from 0 to 5.")
    from openai import OpenAI

    config = load_config(os.environ, ROOT / ".env")
    client = OpenAI(api_key=config.api_key)
    run_id = uuid4().hex
    fixture_root = ROOT / "tests" / "fixtures" / "phase6"
    stores: dict[str, str] = {}
    files: dict[str, str] = {}
    responses = []
    outcome = "FAILED"
    selected_mode = None
    failure = None
    started = time.perf_counter()
    cleanup_errors: list[str] = []
    try:
        for key, filename in FIXTURES.items():
            store = client.vector_stores.create(
                name=f"Phase 6 synthetic contract {key}", metadata={"library_key": key}
            )
            stores[key] = store.id
            with (fixture_root / filename).open("rb") as body:
                uploaded = client.files.create(file=body, purpose="assistants")
            files[key] = uploaded.id
            client.vector_stores.files.create(
                store.id,
                file_id=uploaded.id,
                attributes={"library_key": key, "source_revision_key": f"synthetic-{key}"},
            )
            _wait_ready(client, store.id, uploaded.id)

        research_outputs: list[dict] = []
        for key in FIXTURES:
            response = _response(
                client,
                input_items=f"Research only {key}'s statement about synthetic setup A. Return a concise attributed evidence note.",
                store_ids=[stores[key]],
            )
            responses.append(response)
            output = _output(response)
            if _result_file_ids(output) != {files[key]}:
                raise RuntimeError(f"Source isolation failed for {key}.")
            if _citation_file_ids(output) != {files[key]}:
                raise RuntimeError(f"Native citation ownership failed for {key}.")
            research_outputs.extend(output)

        subset = ["gxt.garrett", "gxt.afyz"]
        subset_response = _response(
            client,
            input_items="Report the synthetic conditions from the enabled mentors only.",
            store_ids=[stores[key] for key in subset],
        )
        responses.append(subset_response)
        if not _result_file_ids(_output(subset_response)).issubset({files[key] for key in subset}):
            raise RuntimeError("A disabled synthetic store was reachable.")

        final_response = client.responses.create(
            model=MODEL,
            instructions=(
                "Reconcile the synthetic mentor evidence. Preserve SHARED_X as shared, AFYZ_ONLY_Y as Afyz-only, "
                "and SPLASH_ONLY_Z as Splash-only. Attach native file citations to each mentor claim."
            ),
            input=[
                {"role": "user", "content": [{"type": "input_text", "text": "Compare every enabled synthetic mentor."}]},
                *({key: value for key, value in item.items() if key not in {"status", "created_by"}} for item in research_outputs),
                {"role": "user", "content": [{"type": "input_text", "text": "Give the final attributed comparison now."}]},
            ],
            tools=[],
            include=["reasoning.encrypted_content"],
            reasoning={"effort": "high"},
            max_output_tokens=1800,
            store=False,
        )
        responses.append(final_response)
        final_output = _output(final_response)
        final_text = _text(final_output)
        if not all(token in final_text for token in ("SHARED_X", "AFYZ_ONLY_Y", "SPLASH_ONLY_Z")):
            raise RuntimeError("Serial reconciliation lost a synthetic concept token.")
        composite_citations = _citation_file_ids([*research_outputs, *final_output])
        if composite_citations != set(files.values()):
            raise RuntimeError("The composite answer did not retain all native source citations.")
        selected_mode = "serial_per_library_composite_citations"
        outcome = "PASSED"
    except Exception as error:
        failure = type(error).__name__
        raise
    finally:
        for store_id in reversed(list(stores.values())):
            try:
                client.vector_stores.delete(store_id)
            except Exception as error:
                cleanup_errors.append(f"vector_store:{type(error).__name__}")
        for file_id in reversed(list(files.values())):
            try:
                client.files.delete(file_id)
            except Exception as error:
                cleanup_errors.append(f"file:{type(error).__name__}")
        totals = [_usage(response) for response in responses]
        input_tokens = sum(value[0] for value in totals)
        output_tokens = sum(value[1] for value in totals)
        estimated_cost = input_tokens * 4 / 1_000_000 + output_tokens * 20 / 1_000_000
        audit = {
            "run_id": run_id,
            "outcome": outcome,
            "failure_type": failure,
            "selected_mode": selected_mode,
            "model": MODEL,
            "response_calls": len(responses),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": sum(value[2] for value in totals),
            "estimated_text_cost_usd": round(estimated_cost, 6),
            "approved_ceiling_usd": ceiling,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "vector_store_ids": list(stores.values()),
            "file_ids": list(files.values()),
            "cleanup": "complete" if not cleanup_errors else cleanup_errors,
        }
        audit_dir = ROOT / "data" / "phase6-contract-audits"
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / f"{run_id}.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
        if estimated_cost > ceiling:
            cleanup_errors.append("estimated_cost_exceeded_explicit_ceiling")
        if cleanup_errors:
            raise RuntimeError(f"Synthetic contract cleanup/budget failed: {cleanup_errors}")
    print(json.dumps({"result": outcome, "mode": selected_mode, "estimated_text_cost_usd": round(estimated_cost, 6)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
