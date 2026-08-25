from hashlib import sha256
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import mentor.gate1 as gate1_module

from mentor.compilation import CompilationRun, CorpusSnapshot, SourceProcessingResult, TokenPricing
from mentor.derived_records import Claim, RecordDependency
from mentor.gate1 import (
    CONSERVATIVE_SOL_PRICING,
    GATE1_PRIOR_SPEND_USD,
    GATE1_PRICING_CHECKED_ON,
    HARD_SPEND_CEILING_USD,
    BudgetedOpenAIClient,
    Gate1Runner,
    PilotRemoteStorageLedger,
    SpendLedger,
    estimate_gate1_cost,
)
from mentor.compiler import EXTRACTION_INITIAL_MAX_OUTPUT_TOKENS, ExtractionFailure, SourceExtractor
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import Storage
from mentor.vector_stores import UploadedFile, VectorStore


ROLES = (
    ("foundation",),
    ("procedure",),
    ("cross_year_2025", "synthesis_evolution", "conflict_uncertainty"),
    ("cross_year_2026",),
    ("exception_condition",),
    ("foundation",),
)


def _manifest_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _production_runtime(tmp_path: Path):
    database_path = tmp_path / "data" / "mentor.sqlite3"
    storage = Storage(database_path)
    storage.initialize()
    collection = Collection("collection_synthetic", "Synthetic", "test", True, "test")
    storage.store_collection(collection)
    entries = []
    revisions = []
    for index, roles in enumerate(ROLES):
        source = Source.create(
            collection_id=collection.collection_id,
            identity_key=f"pilot-{index}",
            source_type="transcript",
            author="Synthetic Author",
            course="Synthetic Course",
            lesson_title=f"Synthetic lesson {index}",
            year=2025 if index in {0, 1, 2, 4} else 2026,
            original_filename=f"lesson-{index}.txt",
            local_provenance=f"synthetic/{index}",
        )
        transcript = f"Synthetic bounded source {index}."
        transcript_path = tmp_path / "corpus" / f"lesson-{index}.txt"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(transcript, encoding="utf-8")
        revision = SourceRevision.create(
            source=source,
            content_sha256=sha256(transcript.encode()).hexdigest(),
            byte_size=len(transcript.encode()),
            local_locator=str(transcript_path),
            observed_at=1.0,
            lifecycle_state="active",
            remote_file_id=f"file_{index}",
        )
        storage.store_source(source)
        storage.store_source_revision(revision)
        revisions.append(revision)
        entries.append(
            {
                "revision_id": revision.revision_id,
                "structural_roles": list(roles),
                "source_name": source.lesson_title,
                "year": source.year,
            }
        )
    run = CompilationRun("production-run", "model", "prompt", "schema", 1.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=revisions,
        raw_store_id="vs_production_raw",
        derived_store_id="vs_production_derived",
        created_at=1.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    first = revisions[0]
    storage.store_derived_record(Claim.create(
        snapshot_id=snapshot.snapshot_id,
        anchors=("anc_production_candidate",),
        dependencies=(RecordDependency("source_revision", first.revision_id),),
        validation_state="validated",
        lifecycle_state="active",
        qualification="Synthetic candidate support.",
        subject="candidate",
        predicate="supports",
        object="publication",
    ))
    storage.record_candidate_gate(
        snapshot.snapshot_id,
        tuple(SourceProcessingResult(item.revision_id, "processed", int(item == first)) for item in revisions),
        checked_at=2.0,
    )
    storage.transition_snapshot(snapshot.snapshot_id, "validating", transitioned_at=2.0)
    production_snapshot = storage.transition_snapshot(
        snapshot.snapshot_id, "published", transitioned_at=3.0
    )
    manifest_path = tmp_path / "data" / "pilots" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "phase-3-pilot-manifest-v1",
                "status": "selected_not_compiled",
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )
    return database_path, manifest_path, storage, production_snapshot


def test_gate1_preflight_reports_a_pathological_whole_run_without_blocking_a_dry_run(tmp_path):
    database_path, manifest_path, production, production_snapshot = _production_runtime(tmp_path)
    client_calls = []
    pilot_root = tmp_path / "private-pilots"

    report = Gate1Runner(
        production_database_path=database_path,
        manifest_path=manifest_path,
        pilot_root=pilot_root,
        spend_limit_usd=25.0,
        client_factory=lambda: client_calls.append("called"),
        today=lambda: GATE1_PRICING_CHECKED_ON,
        expected_manifest_sha256=_manifest_hash(manifest_path),
    ).run()

    assert report.estimated_upper_bound_usd > 25.0
    assert client_calls == []
    assert not pilot_root.exists()
    assert production.current_snapshot() == production_snapshot


def test_gate1_cost_plan_reserves_validation_for_every_schema_permitted_extraction_candidate(tmp_path):
    database_path, manifest_path, production, _production_snapshot = _production_runtime(tmp_path)
    sources, _manifest = gate1_module._resolve_manifest(
        manifest_path, production, _manifest_hash(manifest_path)
    )

    plan = estimate_gate1_cost(sources, CONSERVATIVE_SOL_PRICING)

    assert plan.estimated_candidate_count == len(sources) * 12
    assert next(stage for stage in plan.stages if stage.stage == "validation").call_count == len(sources) * 12


def test_gate1_rejects_manifest_metadata_drift_instead_of_substituting(tmp_path):
    database_path, manifest_path, production, production_snapshot = _production_runtime(tmp_path)
    approved_hash = _manifest_hash(manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["entries"][0]["source_name"] = "Different lesson"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest bytes changed"):
        Gate1Runner(
            production_database_path=database_path,
            manifest_path=manifest_path,
            today=lambda: GATE1_PRICING_CHECKED_ON,
            expected_manifest_sha256=approved_hash,
        ).run()

    assert production.current_snapshot() == production_snapshot


def test_execute_builds_and_publishes_only_in_pilot_then_runs_evaluation(tmp_path, monkeypatch):
    monkeypatch.setattr(gate1_module, "GATE1_PRIOR_SPEND_USD", 0.0)
    database_path, manifest_path, production, production_snapshot = _production_runtime(tmp_path)
    observed = {}

    class FakeCompiler:
        def __init__(self, storage):
            self.storage = storage

        def build(self, request):
            observed["request"] = request
            snapshot = CorpusSnapshot.create(
                run=request.run,
                selected_revisions=[source.revision for source in request.sources],
                raw_store_id="vs_pilot_raw",
                derived_store_id="vs_pilot_derived",
                created_at=4.0,
            )
            self.storage.create_compilation_candidate(request.run, snapshot)
            self.storage.record_candidate_artifact_scope(snapshot.snapshot_id, "pilot")
            first = request.sources[0].revision
            self.storage.store_derived_record(Claim.create(
                snapshot_id=snapshot.snapshot_id,
                anchors=("anc_pilot_candidate",),
                dependencies=(RecordDependency("source_revision", first.revision_id),),
                validation_state="validated",
                lifecycle_state="active",
                qualification="Synthetic pilot candidate support.",
                subject="candidate",
                predicate="supports",
                object="publication",
            ))
            self.storage.record_candidate_gate(
                snapshot.snapshot_id,
                tuple(
                    SourceProcessingResult(
                        source.revision.revision_id, "processed", int(source.revision == first)
                    )
                    for source in request.sources
                ),
                checked_at=5.0,
            )
            self.storage.transition_snapshot(snapshot.snapshot_id, "validating", transitioned_at=5.0)
            return SimpleNamespace(
                ready=True,
                snapshot=snapshot,
                failures=(),
                records=(),
                stage_metrics=(),
                total_metric=None,
                raw_artifact=SimpleNamespace(store_id="vs_pilot_raw", file_ids=()),
                derived_artifact=SimpleNamespace(store_id="vs_pilot_derived", file_ids=()),
            )

    def compiler_factory(storage, _client, pricing):
        observed["runtime_scope"] = storage.runtime_scope
        observed["pricing"] = pricing
        return FakeCompiler(storage)

    def evaluator(pilot, _client):
        observed["evaluated_snapshot"] = pilot.storage.current_snapshot()
        return {"status": "synthetic-complete"}

    report = Gate1Runner(
        production_database_path=database_path,
        manifest_path=manifest_path,
        pilot_root=tmp_path / "private-pilots",
        run_id="gate1-test",
        client_factory=lambda: SimpleNamespace(responses=SimpleNamespace()),
        compiler_factory=compiler_factory,
        evaluator=evaluator,
        today=lambda: GATE1_PRICING_CHECKED_ON,
        expected_manifest_sha256=_manifest_hash(manifest_path),
    ).run(execute=True)

    assert report.executed is True
    assert report.estimated_upper_bound_usd > HARD_SPEND_CEILING_USD
    assert observed["runtime_scope"] == "pilot"
    assert observed["pricing"] == CONSERVATIVE_SOL_PRICING
    assert observed["request"].artifact_scope.value == "pilot"
    assert tuple(source.revision.revision_id for source in observed["request"].sources) == report.revision_ids
    assert observed["evaluated_snapshot"].raw_store_id == "vs_pilot_raw"
    assert production.current_snapshot() == production_snapshot
    assert report.output_path.is_file()
    assert "vs_pilot_raw" in report.output_path.read_text(encoding="utf-8")


def test_budgeted_client_caps_output_and_accounts_actual_usage_before_next_call():
    calls = []

    class Responses:
        def create(self, **request):
            calls.append(request)
            return SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=100,
                    output_tokens=20,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=10),
                ),
                output=(),
            )

    ledger = SpendLedger(1.0, CONSERVATIVE_SOL_PRICING)
    client = BudgetedOpenAIClient(SimpleNamespace(responses=Responses()), ledger)

    client.responses.create(
        model="gpt-5.6-sol",
        instructions="Prompt version: source-extraction-v1",
        input="Synthetic request",
        max_output_tokens=EXTRACTION_INITIAL_MAX_OUTPUT_TOKENS,
    )

    assert calls[0]["max_output_tokens"] == EXTRACTION_INITIAL_MAX_OUTPUT_TOKENS
    assert ledger.spent_usd == pytest.approx(CONSERVATIVE_SOL_PRICING.cost(
        input_tokens=100, output_tokens=20, reasoning_tokens=10
    ))

    tiny = SpendLedger(0.0001, CONSERVATIVE_SOL_PRICING)
    blocked = BudgetedOpenAIClient(SimpleNamespace(responses=Responses()), tiny)
    with pytest.raises(RuntimeError, match="spend ceiling"):
        blocked.responses.create(model="gpt-5.6-sol", input="Synthetic request")
    assert len(calls) == 1


def test_budgeted_client_writes_private_response_envelope_diagnostics(tmp_path):
    class Responses:
        def create(self, **_request):
            return SimpleNamespace(
                id="resp_synthetic",
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                output_text="{\"partial\":true}",
                output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="{\"partial\":true}")])],
                usage=SimpleNamespace(input_tokens=3, output_tokens=2),
            )

    diagnostic_path = tmp_path / "private" / "response-envelopes.jsonl"
    diagnostic_path.parent.mkdir()
    client = BudgetedOpenAIClient(
        SimpleNamespace(responses=Responses()),
        SpendLedger(1.0, CONSERVATIVE_SOL_PRICING),
        diagnostic_path,
    )

    client.responses.create(
        model="gpt-5.6-sol",
        instructions="Prompt version: source-extraction-v5",
        input="Synthetic request",
        text={"format": {"name": "source-extraction-schema-v5"}},
        max_output_tokens=EXTRACTION_INITIAL_MAX_OUTPUT_TOKENS,
    )

    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["status"] == "incomplete"
    assert diagnostic["incomplete_details"] == {"reason": "max_output_tokens"}
    assert diagnostic["prompt_version"] == "source-extraction-v5"
    assert diagnostic["schema_version"] == "source-extraction-schema-v5"
    assert diagnostic["requested_max_output_tokens"] == EXTRACTION_INITIAL_MAX_OUTPUT_TOKENS
    assert "reasoning" not in diagnostic


def test_extraction_retry_is_blocked_before_the_second_paid_call_when_it_exceeds_the_ceiling():
    calls = []

    class Responses:
        def create(self, **request):
            calls.append(request)
            return SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                output_text='{"truncated":',
                output=[SimpleNamespace(type="message", content=[])],
                usage=SimpleNamespace(
                    input_tokens=1,
                    output_tokens=1,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=0),
                ),
            )

    client = BudgetedOpenAIClient(
        SimpleNamespace(responses=Responses()),
        SpendLedger(0.75, CONSERVATIVE_SOL_PRICING),
    )
    extractor = SourceExtractor(
        client, model="gpt-5.6-sol", live_mode=True, pricing=CONSERVATIVE_SOL_PRICING,
    )
    source = Source.create(
        collection_id="collection_synthetic", identity_key="retry-budget", source_type="transcript",
        author="Synthetic", course="Synthetic", lesson_title="Synthetic", year=2026,
        original_filename="synthetic.txt", local_provenance="synthetic.txt",
    )
    revision = SourceRevision.create(
        source=source, content_sha256="a" * 64, byte_size=10, local_locator="synthetic.txt",
        observed_at=1.0, lifecycle_state="active",
    )

    with pytest.raises(ExtractionFailure, match="spend ceiling") as error:
        extractor.extract(revision=revision, snapshot_id="snap_synthetic", transcript="Synthetic")

    assert len(calls) == 1
    assert error.value.attempt_count == 1
    assert error.value.usage.input_tokens == 1


def test_budgeted_client_records_transport_versions_and_conservatively_charges_unknown_usage(tmp_path):
    class FailingResponses:
        def create(self, **_request):
            raise RuntimeError("synthetic transport failure")

    diagnostic_path = tmp_path / "private" / "response-envelopes.jsonl"
    diagnostic_path.parent.mkdir()
    ledger = SpendLedger(1.0, CONSERVATIVE_SOL_PRICING)
    client = BudgetedOpenAIClient(SimpleNamespace(responses=FailingResponses()), ledger, diagnostic_path)

    with pytest.raises(RuntimeError, match="transport failure"):
        client.responses.create(
            model="gpt-5.6-sol",
            instructions="Prompt version: source-extraction-v5",
            input="Synthetic request",
            text={"format": {"name": "source-extraction-schema-v5"}},
            max_output_tokens=EXTRACTION_INITIAL_MAX_OUTPUT_TOKENS,
        )

    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["prompt_version"] == "source-extraction-v5"
    assert diagnostic["schema_version"] == "source-extraction-schema-v5"
    assert diagnostic["requested_max_output_tokens"] == EXTRACTION_INITIAL_MAX_OUTPUT_TOKENS
    assert ledger.events[-1]["status"] == "usage_unknown"
    assert ledger.spent_usd == ledger.events[-1]["cost_usd"]


def test_spend_ledger_counts_a_prior_gate1_run_against_the_same_hard_ceiling():
    assert GATE1_PRIOR_SPEND_USD == pytest.approx(13.853585)
    assert HARD_SPEND_CEILING_USD == pytest.approx(30.0)
    ledger = SpendLedger(HARD_SPEND_CEILING_USD, CONSERVATIVE_SOL_PRICING, prior_spend_usd=GATE1_PRIOR_SPEND_USD)

    assert ledger.spent_usd == pytest.approx(13.853585)
    with pytest.raises(RuntimeError, match="spend ceiling"):
        ledger.ensure("extraction", 16.146416)


def test_gate1_keeps_its_deliberately_conservative_token_pricing_contract():
    assert CONSERVATIVE_SOL_PRICING == TokenPricing(5.0, 30.0, 30.0)


def test_budgeted_client_blocks_the_next_operation_when_its_own_bound_exceeds_remaining_ceiling():
    calls = []

    class Responses:
        def create(self, **request):
            calls.append(request)
            return SimpleNamespace(usage=None, output=())

    ledger = SpendLedger(30.0, CONSERVATIVE_SOL_PRICING, prior_spend_usd=29.99)
    client = BudgetedOpenAIClient(SimpleNamespace(responses=Responses()), ledger)

    with pytest.raises(RuntimeError, match="spend ceiling"):
        client.responses.create(
            model="gpt-5.6-sol",
            instructions="Prompt version: source-extraction-v1",
            input="Synthetic request",
        )

    assert calls == []


def test_actual_cheap_call_releases_its_unused_reservation_for_the_next_call():
    ledger = SpendLedger(1.0, CONSERVATIVE_SOL_PRICING)

    first = ledger.reserve("extraction", 0.9)
    ledger.settle(first, stage="extraction", actual_cost_usd=0.01)
    second = ledger.reserve("validation", 0.9)

    assert ledger.spent_usd == pytest.approx(0.01)
    ledger.settle(second, stage="validation", actual_cost_usd=0.01)
    assert ledger.spent_usd == pytest.approx(0.02)


def test_multiple_incrementally_bounded_calls_proceed_but_an_expensive_later_call_stops_safely():
    ledger = SpendLedger(30.0, CONSERVATIVE_SOL_PRICING, prior_spend_usd=29.0)
    for stage in ("extraction", "validation", "validation"):
        ticket = ledger.reserve(stage, 0.5)
        ledger.settle(ticket, stage=stage, actual_cost_usd=0.1)

    with pytest.raises(RuntimeError, match="spend ceiling"):
        ledger.reserve("synthesis", 0.8)

    assert ledger.spent_usd == pytest.approx(29.3)
    assert ledger.spent_usd <= HARD_SPEND_CEILING_USD


def test_unknown_cost_nonresponses_operation_is_rejected_before_it_can_call_the_provider():
    client = BudgetedOpenAIClient(
        SimpleNamespace(responses=SimpleNamespace(), vector_stores=SimpleNamespace()),
        SpendLedger(30.0, CONSERVATIVE_SOL_PRICING),
    )

    with pytest.raises(RuntimeError, match="no defensible per-operation upper bound"):
        _ = client.vector_stores


def test_pilot_storage_ledger_requires_expiry_and_charges_actual_vector_store_usage(tmp_path):
    ledger = SpendLedger(30.0, CONSERVATIVE_SOL_PRICING)
    storage = PilotRemoteStorageLedger(tmp_path / "private" / "remote-storage-ledger.json", ledger)

    assert storage.observe("raw_store", VectorStore("vs_raw", "completed", 1, 86_401, 2**30)) is True
    assert storage.observe("derived_file", UploadedFile("file_derived", 42, 1, 86_401)) is True

    report = json.loads((tmp_path / "private" / "remote-storage-ledger.json").read_text())
    assert ledger.spent_usd == pytest.approx(0.10)
    assert {item["resource_kind"] for item in report["resources"]} == {"vector_store", "file"}
    assert all(item["cleanup_status"] == "automatic_expiry_configured" for item in report["resources"])

    with pytest.raises(RuntimeError, match="unknown storage cost"):
        storage.observe("derived_store", VectorStore("vs_unknown", "completed", 1, 86_401, None))

    assert storage.observe("derived_file", UploadedFile("file_long", 42, 1, 172_801)) is False


def test_bounded_remote_storage_is_opt_in_and_does_not_make_production_calls_expiring():
    blocked = BudgetedOpenAIClient(
        SimpleNamespace(responses=SimpleNamespace(), vector_stores=SimpleNamespace()),
        SpendLedger(30.0, CONSERVATIVE_SOL_PRICING),
    )
    with pytest.raises(RuntimeError, match="no defensible per-operation upper bound"):
        _ = blocked.vector_stores

    permitted = BudgetedOpenAIClient(
        SimpleNamespace(responses=SimpleNamespace(), vector_stores=SimpleNamespace()),
        SpendLedger(30.0, CONSERVATIVE_SOL_PRICING), allow_bounded_remote_storage=True,
    )
    assert permitted.vector_stores is not None


def test_execute_rejects_stale_pricing_before_any_paid_client(tmp_path, monkeypatch):
    monkeypatch.setattr(gate1_module, "GATE1_PRIOR_SPEND_USD", 0.0)
    database_path, manifest_path, production, production_snapshot = _production_runtime(tmp_path)
    client_calls = []

    with pytest.raises(ValueError, match="pricing check is stale"):
        Gate1Runner(
            production_database_path=database_path,
            manifest_path=manifest_path,
            pilot_root=tmp_path / "private-pilots",
            client_factory=lambda: client_calls.append("called"),
            today=lambda: GATE1_PRICING_CHECKED_ON + timedelta(days=8),
            expected_manifest_sha256=_manifest_hash(manifest_path),
        ).run(execute=True)

    assert client_calls == []
    assert production.current_snapshot() == production_snapshot
