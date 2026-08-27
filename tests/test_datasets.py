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
            result={"valid_rows": 3},
        )

    confirmed = storage.confirm_mapping_version(draft.id)

    assert draft.status == "draft"
    assert confirmed.status == "confirmed"
    assert confirmed.id != draft.id
    assert storage.mapping_entries(confirmed.id) == storage.mapping_entries(draft.id)
    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="confirmed"):
            connection.execute(
                "INSERT INTO analysis_evidence(thread_id, origin_turn_number, dataset_id, dataset_sha256, import_spec_id, "
                "mapping_version_id, operation, schema_version, arguments_json, result_json) "
                "VALUES (?, 1, ?, ?, ?, ?, 'summarize_results', '1', '{}', '{}')",
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
        result={"valid_rows": 3},
    )
    retained_evidence = storage.record_analysis_evidence(
        thread_id=retained_thread,
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=confirmed.id,
        operation="summarize_results",
        schema_version="1",
        arguments={"dataset_id": dataset.id},
        result={"valid_rows": 3},
    )
    storage.record_analysis_tool_output(deleted_thread, "call-delete", deleted_evidence.id, {"valid_rows": 3})
    storage.record_analysis_tool_output(retained_thread, "call-keep", retained_evidence.id, {"valid_rows": 3})

    assert storage.delete_thread(deleted_thread) is True
    assert storage.dataset(dataset.id) == dataset
    assert storage.thread_dataset_scope(deleted_thread) is None
    assert storage.analysis_evidence(deleted_thread) == []
    assert storage.analysis_tool_outputs(deleted_thread) == []
    assert storage.thread_dataset_scope(retained_thread).dataset_id == dataset.id
    assert [evidence.id for evidence in storage.analysis_evidence(retained_thread)] == [retained_evidence.id]
    assert [output["tool_call_id"] for output in storage.analysis_tool_outputs(retained_thread)] == ["call-keep"]
