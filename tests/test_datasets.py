import json
import inspect
import sqlite3

import pytest

from mentor.storage import Storage


def _dataset(storage: Storage):
    return storage.create_dataset(
        dataset_id="dataset-alpha",
        original_name="trades.csv",
        content_sha256="a" * 64,
        original_extension=".csv",
        byte_size=42,
        source_row_count=3,
        status="ready",
        import_spec={
            "header_row": 0,
            "csv_encoding": "utf-8",
            "csv_delimiter": ",",
            "csv_quoting": '"',
            "parser_version": "pandas-3.0.5",
            "row_order_policy": "source",
            "time_parse_policy": "unambiguous_only",
        },
        columns=[
            {"ordinal": 0, "original_header": "Result_R", "inferred_type": "number", "null_count": 0, "invalid_count": 0},
            {"ordinal": 1, "original_header": "Session", "inferred_type": "string", "null_count": 0, "invalid_count": 0},
        ],
    )


def _result_envelope(dataset, mapping_version_id: int, operation: str = "summarize_results"):
    return {
        "provenance": "USER_EMPIRICAL_EVIDENCE",
        "dataset_id": dataset.id,
        "dataset_sha256": dataset.content_sha256,
        "mapping_version_id": mapping_version_id,
        "operation": operation,
        "schema_version": "1",
        "counts": {"source_rows": 3, "filtered_rows": 3, "valid_rows": 3, "excluded_rows": 0},
        "metrics": {"valid_rows": 3},
        "limitations": [],
    }


def test_dataset_metadata_is_immutable_and_never_persists_raw_rows(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()

    dataset = _dataset(storage)

    assert storage.dataset(dataset.id) == dataset
    assert dataset.import_spec_id is not None
    with sqlite3.connect(storage.database_path) as connection:
        assert "raw" not in " ".join(
            row[1].lower()
            for table in ("datasets", "dataset_import_specs", "dataset_columns")
            for row in connection.execute(f"PRAGMA table_info({table})")
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE datasets SET original_name = 'changed.csv' WHERE id = ?", (dataset.id,))


def test_mapping_confirmation_copies_an_atomic_immutable_snapshot_and_blocks_drafts_from_analysis(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    draft = storage.create_mapping_draft(
        dataset.id,
        [
            {"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"},
            {"column_ordinal": 1, "semantic_role": "session", "source": "manual"},
        ],
    )

    draft_thread = storage.create_thread("Draft analysis")
    with pytest.raises(ValueError, match="confirmed"):
        storage.record_analysis_evidence(
            thread_id=draft_thread,
            origin_turn_number=1,
            dataset_id=dataset.id,
            mapping_version_id=draft.id,
            operation="summarize_results",
            schema_version="1",
            arguments={"dataset_id": dataset.id},
            result=_result_envelope(dataset, draft.id),
        )

    confirmed = storage.confirm_mapping_version(draft.id)

    assert draft.status == "draft"
    assert confirmed.status == "confirmed"
    assert confirmed.id != draft.id
    assert storage.mapping_entries(confirmed.id) == storage.mapping_entries(draft.id)
    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="provenance|confirmed"):
            connection.execute(
                "INSERT INTO analysis_evidence(thread_id, origin_turn_number, dataset_id, dataset_sha256, import_spec_id, "
                "mapping_version_id, operation, schema_version, arguments_json, result_json) "
                "VALUES (?, 1, ?, ?, ?, ?, 'summarize_results', '1', '{\"dataset_id\":\"dataset-alpha\"}', '{}')",
                (draft_thread, dataset.id, dataset.content_sha256, dataset.import_spec_id, draft.id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE dataset_mapping_versions SET status = 'draft' WHERE id = ?", (confirmed.id,))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO dataset_mapping_entries(mapping_version_id, column_ordinal, semantic_role, unit, analysis_label, source) "
                "VALUES (?, 1, 'trade_return', 'R', NULL, 'manual')",
                (confirmed.id,),
            )


def test_thread_deletion_removes_only_its_dataset_scope_evidence_and_tool_outputs(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    confirmed = storage.confirm_mapping_version(
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
        ).id
    )
    deleted_thread = storage.create_thread("Delete me")
    retained_thread = storage.create_thread("Keep me")
    storage.set_thread_dataset_scope(deleted_thread, dataset.id)
    storage.set_thread_dataset_scope(retained_thread, dataset.id)
    deleted_evidence = storage.record_analysis_evidence(
        thread_id=deleted_thread,
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=confirmed.id,
        operation="summarize_results",
        schema_version="1",
        arguments={"dataset_id": dataset.id},
        result=_result_envelope(dataset, confirmed.id),
    )
    retained_evidence = storage.record_analysis_evidence(
        thread_id=retained_thread,
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=confirmed.id,
        operation="summarize_results",
        schema_version="1",
        arguments={"dataset_id": dataset.id},
        result=_result_envelope(dataset, confirmed.id),
    )
    storage.record_analysis_tool_output(deleted_thread, "call-delete", deleted_evidence.id, _result_envelope(dataset, confirmed.id))
    storage.record_analysis_tool_output(retained_thread, "call-keep", retained_evidence.id, _result_envelope(dataset, confirmed.id))

    assert storage.delete_thread(deleted_thread) is True
    assert storage.dataset(dataset.id) == dataset
    assert storage.thread_dataset_scope(deleted_thread) is None
    assert storage.analysis_evidence(deleted_thread) == []
    assert storage.analysis_tool_outputs(deleted_thread) == []
    assert storage.thread_dataset_scope(retained_thread).dataset_id == dataset.id
    assert [evidence.id for evidence in storage.analysis_evidence(retained_thread)] == [retained_evidence.id]
    assert [output["tool_call_id"] for output in storage.analysis_tool_outputs(retained_thread)] == ["call-keep"]


def test_evidence_provenance_and_result_envelopes_are_immutable_metadata_only(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    confirmed = storage.confirm_mapping_version(
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
        ).id
    )
    thread_id = storage.create_thread("Evidence")
    envelope = _result_envelope(dataset, confirmed.id)
    evidence = storage.record_analysis_evidence(
        thread_id=thread_id,
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=confirmed.id,
        operation="summarize_results",
        schema_version="1",
        arguments={"dataset_id": dataset.id},
        result=envelope,
    )
    storage.record_analysis_tool_output(thread_id, "valid-output", evidence.id, envelope)

    with pytest.raises(ValueError, match="analysis result envelope"):
        storage.record_analysis_evidence(
            thread_id=thread_id,
            origin_turn_number=2,
            dataset_id=dataset.id,
            mapping_version_id=confirmed.id,
            operation="summarize_results",
            schema_version="1",
            arguments={"dataset_id": dataset.id},
            result={"rows": [{"Result_R": 1}]},
        )
    with pytest.raises(ValueError, match="analysis result envelope"):
        storage.record_analysis_tool_output(thread_id, "raw-output", evidence.id, {"body": "private upload"})

    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="provenance"):
            connection.execute(
                "INSERT INTO analysis_evidence(thread_id, origin_turn_number, dataset_id, dataset_sha256, import_spec_id, "
                "mapping_version_id, operation, schema_version, arguments_json, result_json) "
                "VALUES (?, 2, ?, ?, ?, ?, 'summarize_results', '1', '{\"dataset_id\":\"dataset-alpha\"}', ?)",
                (thread_id, dataset.id, "b" * 64, dataset.import_spec_id, confirmed.id, json.dumps(envelope)),
            )
        for import_spec_id, mapping_version_id in ((999, confirmed.id), (dataset.import_spec_id, 999)):
            with pytest.raises(sqlite3.IntegrityError, match="provenance"):
                connection.execute(
                    "INSERT INTO analysis_evidence(thread_id, origin_turn_number, dataset_id, dataset_sha256, import_spec_id, "
                    "mapping_version_id, operation, schema_version, arguments_json, result_json) "
                    "VALUES (?, 2, ?, ?, ?, ?, 'summarize_results', '1', '{\"dataset_id\":\"dataset-alpha\"}', ?)",
                    (thread_id, dataset.id, dataset.content_sha256, import_spec_id, mapping_version_id, json.dumps(envelope)),
                )
        with pytest.raises(sqlite3.IntegrityError, match="envelope"):
            connection.execute(
                "INSERT INTO analysis_evidence(thread_id, origin_turn_number, dataset_id, dataset_sha256, import_spec_id, "
                "mapping_version_id, operation, schema_version, arguments_json, result_json) "
                "VALUES (?, 2, ?, ?, ?, ?, 'summarize_results', '1', '{\"dataset_id\":\"dataset-alpha\"}', ?)",
                (thread_id, dataset.id, dataset.content_sha256, dataset.import_spec_id, confirmed.id, json.dumps({"rows": []})),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE analysis_evidence SET dataset_sha256 = ? WHERE id = ?", ("b" * 64, evidence.id))
        with pytest.raises(sqlite3.IntegrityError, match="analysis result envelope"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'raw-bypass', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    json.dumps({"rows": [{"Result_R": 1}]}),
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE analysis_tool_outputs SET output_json = ? WHERE thread_id = ? AND tool_call_id = 'valid-output'",
                (json.dumps({"rows": [{"Result_R": 1}]}), thread_id),
            )


def test_mapping_versions_keep_confirmed_parent_lineage_and_recover_existing_rows(tmp_path):
    database_path = tmp_path / "mentor.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE datasets (
                id TEXT PRIMARY KEY, original_name TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                original_extension TEXT NOT NULL, byte_size INTEGER NOT NULL, source_row_count INTEGER NOT NULL,
                status TEXT NOT NULL, imported_at TEXT NOT NULL
            );
            CREATE TABLE dataset_import_specs (
                id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL UNIQUE, selected_sheet TEXT,
                header_row INTEGER NOT NULL, csv_encoding TEXT, csv_delimiter TEXT, csv_quoting TEXT,
                parser_version TEXT, row_order_policy TEXT, time_parse_policy TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE dataset_columns (
                dataset_id TEXT NOT NULL, ordinal INTEGER NOT NULL, original_header TEXT NOT NULL,
                inferred_type TEXT NOT NULL, null_count INTEGER NOT NULL, invalid_count INTEGER NOT NULL,
                PRIMARY KEY(dataset_id, ordinal)
            );
            CREATE TABLE dataset_mapping_versions (
                id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL, version INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, confirmed_at TEXT,
                UNIQUE(dataset_id, version)
            );
            CREATE TABLE analysis_tool_outputs (
                thread_id INTEGER NOT NULL, tool_call_id TEXT NOT NULL, evidence_id INTEGER NOT NULL,
                output_json TEXT NOT NULL, PRIMARY KEY(thread_id, tool_call_id)
            );
            INSERT INTO datasets VALUES ('legacy', 'legacy.csv', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                '.csv', 1, 1, 'ready', '2026-08-27T00:00:00Z');
            INSERT INTO dataset_import_specs VALUES (1, 'legacy', NULL, 0, 'utf-8', ',', '"', 'pandas-3.0.5',
                'source', 'unambiguous_only', '2026-08-27T00:00:00Z');
            INSERT INTO dataset_columns VALUES ('legacy', 0, 'Result_R', 'number', 0, 0);
            INSERT INTO dataset_mapping_versions VALUES (1, 'legacy', 1, 'confirmed', '2026-08-27T00:00:00Z', '2026-08-27T00:00:00Z');
            """
        )

    storage = Storage(database_path)
    storage.initialize()
    with sqlite3.connect(database_path) as connection:
        assert "parent_mapping_version_id" in {
            row[1] for row in connection.execute("PRAGMA table_info(dataset_mapping_versions)")
        }
        assert "arguments_json" in {row[1] for row in connection.execute("PRAGMA table_info(analysis_tool_outputs)")}
        assert connection.execute(
            "SELECT parent_mapping_version_id FROM dataset_mapping_versions WHERE id = 1"
        ).fetchone() == (None,)

    fresh_storage = Storage(tmp_path / "fresh.sqlite3")
    fresh_storage.initialize()
    dataset = _dataset(fresh_storage)
    draft = fresh_storage.create_mapping_draft(
        dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
    )
    confirmed = fresh_storage.confirm_mapping_version(draft.id)

    assert confirmed.parent_mapping_version_id == draft.id
    assert fresh_storage.mapping_version(confirmed.id) == confirmed
    with sqlite3.connect(fresh_storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE dataset_mapping_versions SET parent_mapping_version_id = NULL WHERE id = ?", (confirmed.id,)
            )


def test_mapping_drafts_reject_columns_that_are_not_metadata_for_the_dataset(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)

    with pytest.raises(ValueError, match="existing dataset column"):
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 99, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
        )

    with sqlite3.connect(storage.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_mapping_versions").fetchone() == (0,)
    valid_draft = storage.create_mapping_draft(
        dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
    )
    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="existing dataset column"):
            connection.execute(
                "INSERT INTO dataset_mapping_entries(mapping_version_id, column_ordinal, semantic_role, unit, analysis_label, source) "
                "VALUES (?, 99, 'session', NULL, NULL, 'manual')",
                (valid_draft.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="existing dataset column"):
            connection.execute(
                "UPDATE dataset_mapping_entries SET column_ordinal = 99 WHERE mapping_version_id = ? AND column_ordinal = 0",
                (valid_draft.id,),
            )


def test_analysis_tool_arguments_are_bounded_metadata_and_not_raw_payloads(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    confirmed = storage.confirm_mapping_version(
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
        ).id
    )
    thread_id = storage.create_thread("Tool arguments")
    evidence = storage.record_analysis_evidence(
        thread_id=thread_id,
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=confirmed.id,
        operation="summarize_results",
        schema_version="1",
        arguments={"dataset_id": dataset.id},
        result=_result_envelope(dataset, confirmed.id),
    )

    assert "arguments" in inspect.signature(storage.record_analysis_tool_output).parameters
    with pytest.raises(ValueError, match="analysis tool arguments"):
        storage.record_analysis_tool_output(
            thread_id,
            "raw-arguments",
            evidence.id,
            _result_envelope(dataset, confirmed.id),
            arguments={"body": "private upload", "rows": [{"Result_R": 1}]},
        )
    with sqlite3.connect(storage.database_path) as connection:
        assert "arguments_json" in {row[1] for row in connection.execute("PRAGMA table_info(analysis_tool_outputs)")}
        with pytest.raises(sqlite3.IntegrityError, match="arguments"):
            connection.execute(
                "INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json) "
                "VALUES (?, 'raw-arguments-bypass', ?, ?, ?)",
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"body": "private upload", "rows": []}),
                    json.dumps(_result_envelope(dataset, confirmed.id)),
                ),
            )


def test_confirmed_mapping_snapshots_cannot_be_promoted_or_changed_with_direct_sql(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    draft = storage.create_mapping_draft(
        dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
    )
    confirmed = storage.confirm_mapping_version(draft.id)
    other_draft = storage.create_mapping_draft(
        dataset.id, [{"column_ordinal": 1, "semantic_role": "session", "source": "manual"}]
    )

    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE dataset_mapping_versions SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (other_draft.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE dataset_mapping_versions SET status = 'draft' WHERE id = ?", (confirmed.id,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE dataset_mapping_versions SET parent_mapping_version_id = ? WHERE id = ?",
                (draft.id, other_draft.id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE dataset_mapping_versions SET version = 99 WHERE id = ?", (confirmed.id,))
        with pytest.raises(sqlite3.IntegrityError, match="confirmed"):
            connection.execute(
                "UPDATE dataset_mapping_entries SET mapping_version_id = ? WHERE mapping_version_id = ?",
                (confirmed.id, other_draft.id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="confirmed"):
            connection.execute(
                "UPDATE dataset_mapping_entries SET semantic_role = 'session' WHERE mapping_version_id = ?",
                (confirmed.id,),
            )


def test_dataset_columns_cannot_be_deleted_while_a_mapping_references_them(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    storage.create_mapping_draft(
        dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
    )

    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="referenced"):
            connection.execute("DELETE FROM dataset_columns WHERE dataset_id = ? AND ordinal = 0", (dataset.id,))


def test_result_envelopes_reject_raw_values_hidden_in_limitations_or_metrics(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    confirmed = storage.confirm_mapping_version(
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
        ).id
    )
    thread_id = storage.create_thread("Envelope leakage")
    envelope = _result_envelope(dataset, confirmed.id)
    leaky_limitations = {**envelope, "limitations": ["trade row: Result_R=2.5"]}
    leaky_metrics = {**envelope, "metrics": {"mean_return": "Result_R=2.5"}}
    arbitrary_metric = {**envelope, "metrics": {"raw_result_r_2_5": 2.5}}

    with pytest.raises(ValueError, match="analysis result envelope"):
        storage.record_analysis_evidence(
            thread_id=thread_id,
            origin_turn_number=1,
            dataset_id=dataset.id,
            mapping_version_id=confirmed.id,
            operation="summarize_results",
            schema_version="1",
            arguments={"dataset_id": dataset.id},
            result=leaky_limitations,
        )
    with pytest.raises(ValueError, match="analysis result envelope"):
        storage.record_analysis_evidence(
            thread_id=thread_id,
            origin_turn_number=1,
            dataset_id=dataset.id,
            mapping_version_id=confirmed.id,
            operation="summarize_results",
            schema_version="1",
            arguments={"dataset_id": dataset.id},
            result=leaky_metrics,
        )
    with pytest.raises(ValueError, match="analysis result envelope"):
        storage.record_analysis_evidence(
            thread_id=thread_id,
            origin_turn_number=1,
            dataset_id=dataset.id,
            mapping_version_id=confirmed.id,
            operation="summarize_results",
            schema_version="1",
            arguments={"dataset_id": dataset.id},
            result=arbitrary_metric,
        )
    evidence = storage.record_analysis_evidence(
        thread_id=thread_id,
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=confirmed.id,
        operation="summarize_results",
        schema_version="1",
        arguments={"dataset_id": dataset.id},
        result=envelope,
    )
    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="envelope"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'leaky', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    json.dumps(leaky_limitations),
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="envelope"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'leaky-metric', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    json.dumps(leaky_metrics),
                ),
            )


def test_evidence_arguments_are_bounded_metadata_and_not_raw_payloads(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    confirmed = storage.confirm_mapping_version(
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
        ).id
    )
    thread_id = storage.create_thread("Evidence arguments")
    envelope = _result_envelope(dataset, confirmed.id)

    with pytest.raises(ValueError, match="analysis evidence arguments"):
        storage.record_analysis_evidence(
            thread_id=thread_id,
            origin_turn_number=1,
            dataset_id=dataset.id,
            mapping_version_id=confirmed.id,
            operation="summarize_results",
            schema_version="1",
            arguments={"headers": ["Result_R"], "original_filename": "trades.csv", "rows": [[2.5]]},
            result=envelope,
        )
    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="evidence arguments"):
            connection.execute(
                """
                INSERT INTO analysis_evidence(
                    thread_id, origin_turn_number, dataset_id, dataset_sha256, import_spec_id,
                    mapping_version_id, operation, schema_version, arguments_json, result_json
                ) VALUES (?, 1, ?, ?, ?, ?, 'summarize_results', '1', ?, ?)
                """,
                (
                    thread_id,
                    dataset.id,
                    dataset.content_sha256,
                    dataset.import_spec_id,
                    confirmed.id,
                    json.dumps({"body": "private data", "rows": [{"Result_R": 2.5}]}),
                    json.dumps(envelope),
                ),
            )


def test_confirmed_mapping_snapshot_must_exactly_match_its_parent_entries(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    draft = storage.create_mapping_draft(
        dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
    )
    confirmed = storage.confirm_mapping_version(draft.id)

    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="draft"):
            connection.execute(
                """
                INSERT INTO dataset_mapping_versions(dataset_id, version, status, parent_mapping_version_id)
                VALUES (?, 3, 'confirmed', ?)
                """,
                (dataset.id, draft.id),
            )
        connection.execute(
            """
            INSERT INTO dataset_mapping_versions(dataset_id, version, status, parent_mapping_version_id)
            VALUES (?, 4, 'draft', ?)
            """,
            (dataset.id, draft.id),
        )
        forged_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            """
            INSERT INTO dataset_mapping_entries(mapping_version_id, column_ordinal, semantic_role, unit, analysis_label, source)
            VALUES (?, 1, 'session', NULL, NULL, 'manual')
            """,
            (forged_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE dataset_mapping_versions SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (forged_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="confirmed"):
            connection.execute(
                "INSERT INTO dataset_mapping_entries(mapping_version_id, column_ordinal, semantic_role, unit, analysis_label, source) "
                "VALUES (?, 1, 'session', NULL, NULL, 'manual')",
                (confirmed.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="confirmed"):
            connection.execute("DELETE FROM dataset_mapping_entries WHERE mapping_version_id = ?", (confirmed.id,))


def test_migration_backfills_preexisting_tool_outputs_before_immutability_triggers(tmp_path):
    database_path = tmp_path / "legacy-tool-output.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE threads (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE analysis_evidence (
                id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL, origin_turn_number INTEGER NOT NULL,
                display_turn_number INTEGER, dataset_id TEXT NOT NULL, dataset_sha256 TEXT NOT NULL,
                import_spec_id INTEGER NOT NULL, mapping_version_id INTEGER NOT NULL, operation TEXT NOT NULL,
                schema_version TEXT NOT NULL, arguments_json TEXT NOT NULL, result_json TEXT NOT NULL
            );
            CREATE TABLE analysis_tool_outputs (
                thread_id INTEGER NOT NULL, tool_call_id TEXT NOT NULL, evidence_id INTEGER NOT NULL,
                output_json TEXT NOT NULL, PRIMARY KEY(thread_id, tool_call_id)
            );
            INSERT INTO threads VALUES (1, 'legacy');
            INSERT INTO analysis_evidence VALUES (
                1, 1, 1, NULL, 'legacy-dataset',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                1, 1, 'summarize_results', '1', '{"dataset_id":"legacy-dataset"}', '{}'
            );
            INSERT INTO analysis_tool_outputs VALUES (1, 'legacy-call', 1, '{}');
            """
        )

    Storage(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT arguments_json FROM analysis_tool_outputs WHERE tool_call_id = 'legacy-call'"
        ).fetchone() == ('{"dataset_id":"legacy-dataset","mapping_version_id":1,"operation":"summarize_results"}',)


def test_analysis_identifiers_and_metrics_are_safe_finite_and_unique(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    confirmed = storage.confirm_mapping_version(
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
        ).id
    )
    thread_id = storage.create_thread("Identifier guards")
    envelope = _result_envelope(dataset, confirmed.id)

    with pytest.raises(ValueError, match="identifier"):
        storage.record_analysis_evidence(
            thread_id=thread_id,
            origin_turn_number=1,
            dataset_id=dataset.id,
            mapping_version_id=confirmed.id,
            operation="trades.csv",
            schema_version="1",
            arguments={"dataset_id": dataset.id},
            result=_result_envelope(dataset, confirmed.id, operation="trades.csv"),
        )
    with pytest.raises(ValueError, match="identifier"):
        storage.record_analysis_evidence(
            thread_id=thread_id,
            origin_turn_number=1,
            dataset_id=dataset.id,
            mapping_version_id=confirmed.id,
            operation="summarize_results",
            schema_version="source-file.csv",
            arguments={"dataset_id": dataset.id},
            result={**envelope, "schema_version": "source-file.csv"},
        )
    with pytest.raises(ValueError, match="analysis result envelope"):
        storage.record_analysis_evidence(
            thread_id=thread_id,
            origin_turn_number=1,
            dataset_id=dataset.id,
            mapping_version_id=confirmed.id,
            operation="summarize_results",
            schema_version="1",
            arguments={"dataset_id": dataset.id},
            result={**envelope, "metrics": {"mean_return": float("inf")}},
        )
    evidence = storage.record_analysis_evidence(
        thread_id=thread_id,
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=confirmed.id,
        operation="summarize_results",
        schema_version="1",
        arguments={"dataset_id": dataset.id},
        result=envelope,
    )
    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="identifier"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'unsafe-identifier', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    json.dumps({**envelope, "operation": "raw-file.csv"}),
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="identifier"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'unsafe-version', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    json.dumps({**envelope, "schema_version": "raw-file.csv"}),
                ),
            )
        duplicate_metrics = json.dumps(envelope).replace(
            '"metrics": {"valid_rows": 3}', '"metrics": {"valid_rows": 3, "valid_rows": 1}'
        )
        with pytest.raises(sqlite3.IntegrityError, match="metrics"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'duplicate-metric', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    duplicate_metrics,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="metrics"):
            connection.execute(
                """
                INSERT INTO analysis_evidence(
                    thread_id, origin_turn_number, dataset_id, dataset_sha256, import_spec_id,
                    mapping_version_id, operation, schema_version, arguments_json, result_json
                ) VALUES (?, 2, ?, ?, ?, ?, 'summarize_results', '1', ?, ?)
                """,
                (
                    thread_id,
                    dataset.id,
                    dataset.content_sha256,
                    dataset.import_spec_id,
                    confirmed.id,
                    json.dumps({"dataset_id": dataset.id}),
                    duplicate_metrics,
                ),
            )
        infinite_metrics = json.dumps(envelope).replace(
            '"metrics": {"valid_rows": 3}', '"metrics": {"mean_return": 1e999}'
        )
        with pytest.raises(sqlite3.IntegrityError, match="metrics"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'infinite-metric', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    infinite_metrics,
                ),
            )


def test_parent_drafts_referenced_by_confirmed_snapshots_are_immutable(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    parent = storage.create_mapping_draft(
        dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
    )
    storage.confirm_mapping_version(parent.id)

    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="parent"):
            connection.execute(
                "UPDATE dataset_mapping_entries SET semantic_role = 'session' WHERE mapping_version_id = ?",
                (parent.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="parent"):
            connection.execute(
                "INSERT INTO dataset_mapping_entries(mapping_version_id, column_ordinal, semantic_role, unit, analysis_label, source) "
                "VALUES (?, 1, 'session', NULL, NULL, 'manual')",
                (parent.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="parent"):
            connection.execute("DELETE FROM dataset_mapping_entries WHERE mapping_version_id = ?", (parent.id,))
        with pytest.raises(sqlite3.IntegrityError, match="parent"):
            connection.execute("DELETE FROM dataset_mapping_versions WHERE id = ?", (parent.id,))


def test_dataset_hash_and_tool_call_ids_are_safe_opaque_metadata(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()

    with pytest.raises(ValueError, match="dataset identifier"):
        storage.create_dataset(
            dataset_id="..\\trades.csv",
            original_name="trades.csv",
            content_sha256="a" * 64,
            original_extension=".csv",
            byte_size=1,
            source_row_count=1,
            status="ready",
            import_spec={"header_row": 0},
            columns=[],
        )
    with pytest.raises(ValueError, match="content hash"):
        storage.create_dataset(
            dataset_id="dataset-beta",
            original_name="trades.csv",
            content_sha256="A" * 64,
            original_extension=".csv",
            byte_size=1,
            source_row_count=1,
            status="ready",
            import_spec={"header_row": 0},
            columns=[],
        )

    dataset = _dataset(storage)
    confirmed = storage.confirm_mapping_version(
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
        ).id
    )
    thread_id = storage.create_thread("Opaque IDs")
    evidence = storage.record_analysis_evidence(
        thread_id=thread_id,
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=confirmed.id,
        operation="summarize_results",
        schema_version="1",
        arguments={"dataset_id": dataset.id},
        result=_result_envelope(dataset, confirmed.id),
    )
    with pytest.raises(ValueError, match="tool call identifier"):
        storage.record_analysis_tool_output(
            thread_id,
            "call/raw-export.csv",
            evidence.id,
            _result_envelope(dataset, confirmed.id),
        )
    storage.record_analysis_tool_output(thread_id, "fc_safe-123", evidence.id, _result_envelope(dataset, confirmed.id))

    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="dataset identifier"):
            connection.execute(
                """
                INSERT INTO datasets(id, original_name, content_sha256, original_extension, byte_size, source_row_count, status)
                VALUES ('raw/file.csv', 'trades.csv', ?, '.csv', 1, 1, 'ready')
                """,
                ("b" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="content hash"):
            connection.execute(
                """
                INSERT INTO datasets(id, original_name, content_sha256, original_extension, byte_size, source_row_count, status)
                VALUES ('dataset-gamma', 'trades.csv', ?, '.csv', 1, 1, 'ready')
                """,
                ("B" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="tool call identifier"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'call/raw-export.csv', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    json.dumps(_result_envelope(dataset, confirmed.id)),
                ),
            )
