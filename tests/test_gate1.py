from hashlib import sha256
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentor.compilation import CompilationRun, CorpusSnapshot, SourceProcessingResult, TokenPricing
from mentor.derived_records import Claim, RecordDependency
from mentor.gate1 import (
    CONSERVATIVE_SOL_PRICING,
    GATE1_PRIOR_SPEND_USD,
    GATE1_PRICING_CHECKED_ON,
    HARD_SPEND_CEILING_USD,
    BudgetedOpenAIClient,
    Gate1Runner,
    SpendLedger,
)
from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import Storage


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


def test_gate1_preflight_blocks_an_over_ceiling_dry_run_without_creating_a_pilot(tmp_path):
    database_path, manifest_path, production, production_snapshot = _production_runtime(tmp_path)
    client_calls = []
    pilot_root = tmp_path / "private-pilots"

    with pytest.raises(ValueError, match="estimated Gate 1 cost"):
        Gate1Runner(
            production_database_path=database_path,
            manifest_path=manifest_path,
            pilot_root=pilot_root,
            spend_limit_usd=4.75,
            client_factory=lambda: client_calls.append("called"),
            today=lambda: GATE1_PRICING_CHECKED_ON,
            expected_manifest_sha256=_manifest_hash(manifest_path),
        ).run()

    assert client_calls == []
    assert not pilot_root.exists()
    assert production.current_snapshot() == production_snapshot


def test_gate1_stops_before_runtime_or_client_when_estimate_exceeds_limit(tmp_path):
    database_path, manifest_path, production, production_snapshot = _production_runtime(tmp_path)
    client_calls = []
    pilot_root = tmp_path / "private-pilots"

    with pytest.raises(ValueError, match="estimated Gate 1 cost"):
        Gate1Runner(
            production_database_path=database_path,
            manifest_path=manifest_path,
            pilot_root=pilot_root,
            spend_limit_usd=4.75,
            client_factory=lambda: client_calls.append("called"),
            today=lambda: GATE1_PRICING_CHECKED_ON,
            expected_manifest_sha256=_manifest_hash(manifest_path),
        ).run(execute=True)

    assert client_calls == []
    assert not pilot_root.exists()
    assert production.current_snapshot() == production_snapshot


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


def test_execute_builds_and_publishes_only_in_pilot_then_runs_evaluation(tmp_path):
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
    )

    assert calls[0]["max_output_tokens"] == 8_000
    assert ledger.spent_usd == pytest.approx(CONSERVATIVE_SOL_PRICING.cost(
        input_tokens=100, output_tokens=20, reasoning_tokens=10
    ))

    tiny = SpendLedger(0.0001, CONSERVATIVE_SOL_PRICING)
    blocked = BudgetedOpenAIClient(SimpleNamespace(responses=Responses()), tiny)
    with pytest.raises(RuntimeError, match="spend ceiling"):
        blocked.responses.create(model="gpt-5.6-sol", input="Synthetic request")
    assert len(calls) == 1


def test_spend_ledger_counts_a_prior_gate1_run_against_the_same_hard_ceiling():
    assert GATE1_PRIOR_SPEND_USD == pytest.approx(4.742720)
    assert HARD_SPEND_CEILING_USD == pytest.approx(25.0)
    ledger = SpendLedger(HARD_SPEND_CEILING_USD, CONSERVATIVE_SOL_PRICING, prior_spend_usd=GATE1_PRIOR_SPEND_USD)

    assert ledger.spent_usd == pytest.approx(4.742720)
    with pytest.raises(RuntimeError, match="spend ceiling"):
        ledger.ensure("extraction", 20.257281)


def test_budgeted_client_enforces_stage_budget_before_a_paid_call():
    calls = []

    class Responses:
        def create(self, **request):
            calls.append(request)
            return SimpleNamespace(usage=None, output=())

    ledger = SpendLedger(20.0, CONSERVATIVE_SOL_PRICING)
    ledger.set_stage_limits({"extraction": 0.01})
    client = BudgetedOpenAIClient(SimpleNamespace(responses=Responses()), ledger)

    with pytest.raises(RuntimeError, match="extraction stage budget"):
        client.responses.create(
            model="gpt-5.6-sol",
            instructions="Prompt version: source-extraction-v1",
            input="Synthetic request",
        )

    assert calls == []


def test_execute_rejects_stale_pricing_before_any_paid_client(tmp_path):
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
