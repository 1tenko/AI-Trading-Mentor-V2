import json
import inspect
import sqlite3
import threading
import zipfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from openpyxl import Workbook

import mentor.datasets as dataset_module
from mentor.datasets import (
    DatasetImportError,
    MappingEntry,
    MappingSuggestion,
    create_inspected_mapping_draft,
    inspect_local_dataset,
    import_local_dataset,
    mapping_suggestions,
    model_mapping_context,
)
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


def _importer(storage: Storage, tmp_path: Path):
    return lambda source, **kwargs: import_local_dataset(
        source, storage, tmp_path / "datasets", dataset_id_factory=lambda: "dataset-imported", **kwargs
    )


def _workbook(path: Path, *, sheet_name: str = "Trades", formula: bool = False) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    worksheet.append(["Result_R", "Session"])
    worksheet.append(["=1+1" if formula else 1.5, "London"])
    workbook.save(path)


def _rewrite_zip_member(path: Path, member_name: str, transform) -> None:
    rewritten = path.with_suffix(".rewritten.xlsx")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(rewritten, "w") as destination:
        for member in source.infolist():
            contents = source.read(member.filename)
            destination.writestr(member, transform(contents) if member.filename == member_name else contents)
    rewritten.replace(path)


def test_local_csv_import_copies_bytes_records_dialect_and_reports_duplicate_rows(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_bytes(b'Result_R;Session\r\n1.5;London\r\n1.5;London\r\n')

    result = _importer(storage, tmp_path)(source)

    assert result.dataset.source_row_count == 2
    assert result.duplicate_row_count == 1
    assert result.dataset.content_sha256 == sha256(source.read_bytes()).hexdigest()
    assert result.original_path.read_bytes() == source.read_bytes()
    assert result.original_path.parent.name == result.dataset.id
    with sqlite3.connect(storage.database_path) as connection:
        assert connection.execute(
            "SELECT csv_encoding, csv_delimiter, csv_quoting, selected_sheet FROM dataset_import_specs"
        ).fetchone() == ("utf-8", ";", '"', None)
        assert connection.execute("SELECT original_header FROM dataset_columns ORDER BY ordinal").fetchall() == [
            ("Result_R",),
            ("Session",),
        ]


def test_local_xlsx_import_uses_only_the_selected_sheet_and_preserves_original(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.xlsx"
    _workbook(source)
    from openpyxl import load_workbook

    selected = load_workbook(source)
    selected.create_sheet("Ignored")
    selected["Ignored"].append(["Other"])
    selected["Ignored"].append(["not imported"])
    selected.save(source)

    result = _importer(storage, tmp_path)(source, selected_sheet="Trades")

    assert result.dataset.source_row_count == 1
    assert result.duplicate_row_count == 0
    assert result.original_path.read_bytes() == source.read_bytes()
    with sqlite3.connect(storage.database_path) as connection:
        assert connection.execute("SELECT selected_sheet FROM dataset_import_specs").fetchone() == ("Trades",)
        assert connection.execute("SELECT original_header FROM dataset_columns ORDER BY ordinal").fetchall() == [
            ("Result_R",),
            ("Session",),
        ]


def test_local_inspection_previews_raw_headers_and_values_before_any_mapping(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R,Session,Trade Date\n1.5,London,2026-01-02\n,New York,01/02/2026\n", encoding="utf-8")
    dataset = _importer(storage, tmp_path)(source).dataset

    inspection = inspect_local_dataset(storage, dataset.id)

    assert inspection.mapping_version is None
    assert inspection.preview == [
        {"Result_R": "1.5", "Session": "London", "Trade Date": "2026-01-02"},
        {"Result_R": "", "Session": "New York", "Trade Date": "01/02/2026"},
    ]
    assert [(column.original_header, column.value_type, column.blank_count, column.invalid_count) for column in inspection.columns] == [
        ("Result_R", "number", 1, 0),
        ("Session", "categorical", 0, 0),
        ("Trade Date", "datetime", 0, 1),
    ]
    assert inspection.columns[2].unavailable_reason == "ambiguous_date"
    assert {(suggestion.column_ordinal, suggestion.semantic_role, suggestion.unit) for suggestion in mapping_suggestions(inspection)} == {
        (0, "trade_return", "R"),
        (1, "session", None),
        (2, "trade_timestamp", None),
    }


def test_local_inspection_keeps_blank_and_invalid_numeric_cells_in_health(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R,Note\n1.5,ok\nnot-a-number,bad\n,blank\n", encoding="utf-8")
    dataset = _importer(storage, tmp_path)(source).dataset

    column = inspect_local_dataset(storage, dataset.id).columns[0]

    assert (column.value_type, column.valid_count, column.blank_count, column.invalid_count) == ("number", 1, 1, 1)
    assert column.unavailable_reason == "invalid_values_excluded"


def test_local_inspection_uses_controlled_outcome_normalization(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Outcome\nWin\nloss\nBE\nunknown\n", encoding="utf-8")
    dataset = _importer(storage, tmp_path)(source).dataset

    column = inspect_local_dataset(storage, dataset.id).columns[0]

    assert (column.value_type, column.valid_count, column.invalid_count, column.distinct_count) == ("categorical", 3, 1, 3)
    assert column.unavailable_reason == "invalid_values_excluded"


def test_mapping_draft_needs_confirmation_and_exposes_only_safe_opaque_model_fields(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R,Desk Secret\n1.5,London\n-0.5,New York\n", encoding="utf-8")
    dataset = _importer(storage, tmp_path)(source).dataset
    inspection = inspect_local_dataset(storage, dataset.id)

    draft = create_inspected_mapping_draft(
        storage,
        inspection,
        [
            MappingEntry(0, semantic_role="trade_return", unit="R", source="alias"),
            MappingEntry(1, analysis_label="Trading session", source="manual"),
        ],
    )

    assert draft.status == "draft"
    assert model_mapping_context(storage, draft.id) == []
    confirmed = storage.confirm_mapping_version(draft.id)
    context = model_mapping_context(storage, confirmed.id)
    assert context == [
        {
            "field_id": context[0]["field_id"],
            "semantic_role": "trade_return",
            "label": None,
            "value_type": "number",
            "unit": "R",
            "health": {"valid_count": 2, "blank_count": 0, "invalid_count": 0, "ambiguous_date_count": 0, "unavailable_reason": None},
            "aggregate_labels_allowed": False,
        },
        {
            "field_id": context[1]["field_id"],
            "label": "Trading session",
            "value_type": "categorical",
            "semantic_role": None,
            "unit": None,
            "health": {"valid_count": 2, "blank_count": 0, "invalid_count": 0, "ambiguous_date_count": 0, "unavailable_reason": None},
            "aggregate_labels_allowed": False,
        }
    ]
    assert all(item["field_id"].startswith("field_") for item in context)
    assert "Desk Secret" not in json.dumps(context)
    assert "Result_R" not in json.dumps(context)


def test_mapping_edits_and_clears_create_new_health_snapshots_and_reject_unsafe_disclosure(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text(
        "Result_R,Session,Flag\n1,London,true\n-1,New York,false\n0,Asia,true\n", encoding="utf-8"
    )
    dataset = _importer(storage, tmp_path)(source).dataset
    inspection = inspect_local_dataset(storage, dataset.id)
    draft = create_inspected_mapping_draft(
        storage,
        inspection,
            [
                MappingEntry(0, semantic_role="trade_return", unit="R"),
                MappingEntry(1, semantic_role="session", analysis_label="Session", model_disclosure=True),
                MappingEntry(2, analysis_label="Trade flag", model_disclosure=True),
            ],
    )
    confirmed = storage.confirm_mapping_version(draft.id)

    cleared = create_inspected_mapping_draft(
        storage,
        inspection,
        [MappingEntry(0, analysis_label="Return", source="manual")],
    )
    cleared_confirmed = storage.confirm_mapping_version(cleared.id)

    assert cleared_confirmed.version > confirmed.version
    assert storage.mapping_entries(confirmed.id)[0].semantic_role == "trade_return"
    assert storage.mapping_entries(cleared_confirmed.id)[0].semantic_role is None
    assert storage.mapping_entries(confirmed.id)[1].valid_count == 3
    assert storage.mapping_entries(confirmed.id)[1].value_type == "categorical"
    assert storage.mapping_entries(confirmed.id)[1].aggregate_labels_allowed is True
    with pytest.raises(ValueError, match="unique"):
        create_inspected_mapping_draft(
            storage,
            inspection,
            [MappingEntry(0, semantic_role="trade_return", unit="R"), MappingEntry(1, semantic_role="trade_return", unit="R")],
        )
    categories = tmp_path / "categories.csv"
    categories.write_text("Category\n" + "\n".join(f"group-{index}" for index in range(21)) + "\n", encoding="utf-8")
    category_dataset = import_local_dataset(
        categories, storage, dataset_id_factory=lambda: "dataset-categories"
    ).dataset
    with pytest.raises(ValueError, match="at most 20"):
        create_inspected_mapping_draft(
            storage,
            inspect_local_dataset(storage, category_dataset.id),
            [MappingEntry(0, analysis_label="Category", model_disclosure=True)],
        )


def test_mapping_storage_rejects_forged_or_missing_inspection_snapshot_fields(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")
    dataset = _importer(storage, tmp_path)(source).dataset
    inspection = inspect_local_dataset(storage, dataset.id)

    with pytest.raises(ValueError, match="inspection snapshot"):
        storage.create_mapping_draft(
            dataset.id, [MappingEntry(0, semantic_role="trade_return", unit="R")]
        )
    with pytest.raises(ValueError, match="inspection snapshot"):
        storage.create_mapping_draft(
            dataset.id,
            [
                MappingEntry(
                    0,
                    semantic_role="trade_return",
                    unit="R",
                    field_id="field_forged",
                    value_type="number",
                    valid_count=99,
                )
            ],
        )
    draft = create_inspected_mapping_draft(
        storage, inspection, [MappingEntry(0, semantic_role="trade_return", unit="R")]
    )
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute(
            "UPDATE dataset_mapping_entries SET field_id = 'field_forged' WHERE mapping_version_id = ?",
            (draft.id,),
        )
    with pytest.raises(ValueError, match="inspection snapshot"):
        storage.confirm_mapping_version(draft.id)


def test_mapping_storage_rejects_spoofed_semantic_role_unit_and_source(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")
    dataset = _importer(storage, tmp_path)(source).dataset
    draft = create_inspected_mapping_draft(
        storage,
        inspect_local_dataset(storage, dataset.id),
        [MappingEntry(0, semantic_role="trade_return", unit="R")],
    )
    entry = storage.mapping_entries(draft.id)[0]

    for spoofed in (
        replace(entry, semantic_role="made_up"),
        replace(entry, unit="made_up"),
        replace(entry, source="made_up"),
    ):
        with pytest.raises(ValueError, match="mapping semantic"):
            storage.create_mapping_draft(dataset.id, [spoofed])
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute(
            "UPDATE dataset_mapping_entries SET semantic_role = 'made_up' WHERE mapping_version_id = ?",
            (draft.id,),
        )
    with pytest.raises(ValueError, match="mapping semantic"):
        storage.confirm_mapping_version(draft.id)


def test_manual_trade_outcome_mapping_uses_controlled_values_not_header_alias(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Status\nWin\nunknown\n", encoding="utf-8")
    dataset = _importer(storage, tmp_path)(source).dataset
    inspection = inspect_local_dataset(storage, dataset.id)

    assert (inspection.columns[0].valid_count, inspection.columns[0].invalid_count) == (2, 0)
    draft = create_inspected_mapping_draft(
        storage,
        inspection,
        [MappingEntry(0, semantic_role="trade_outcome")],
    )
    entry = storage.mapping_entries(draft.id)[0]

    assert (entry.valid_count, entry.invalid_count, entry.distinct_count) == (1, 1, 1)
    assert entry.unavailable_reason == "invalid_values_excluded"
    assert storage.mapping_entries(storage.confirm_mapping_version(draft.id).id)[0] == entry


def test_explicit_trade_outcome_mapping_overrides_date_like_header_inference(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Outcome Date\nWin\nunknown\n", encoding="utf-8")
    dataset = _importer(storage, tmp_path)(source).dataset

    draft = create_inspected_mapping_draft(
        storage,
        inspect_local_dataset(storage, dataset.id),
        [MappingEntry(0, semantic_role="trade_outcome")],
    )
    entry = storage.mapping_entries(draft.id)[0]

    assert (entry.value_type, entry.valid_count, entry.invalid_count) == ("categorical", 1, 1)
    assert entry.unavailable_reason == "invalid_values_excluded"


def test_tradedate_alias_keeps_ambiguous_and_impossible_dates_distinct(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("TradeDate\n2026-01-02\n01/02/2026\n2026-99-99\n", encoding="utf-8")
    dataset = _importer(storage, tmp_path)(source).dataset

    inspection = inspect_local_dataset(storage, dataset.id)
    column = inspection.columns[0]

    assert (column.value_type, column.valid_count, column.invalid_count, column.ambiguous_date_count) == (
        "datetime",
        1,
        2,
        1,
    )
    assert column.unavailable_reason == "ambiguous_date"
    assert mapping_suggestions(inspection) == [MappingSuggestion(0, "trade_timestamp", None)]


@pytest.mark.parametrize(
    ("filename", "contents", "message"),
    [
        ("bad.csv", b"Result_R\n\xff\n", "decode"),
        ("bad.csv", b'Result_R\n"unterminated\n', "CSV"),
        ("bad.txt", b"Result_R\n1\n", "CSV or XLSX"),
    ],
)
def test_rejected_csv_or_extension_leaves_no_dataset_or_temporary_files(tmp_path, filename, contents, message):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / filename
    source.write_bytes(contents)

    with pytest.raises(DatasetImportError, match=message):
        _importer(storage, tmp_path)(source)

    assert storage.dataset("dataset-imported") is None
    assert not (tmp_path / "datasets").exists() or not list((tmp_path / "datasets").iterdir())


def test_xlsx_formula_macro_external_link_and_archive_limit_are_rejected_before_import(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    formula = tmp_path / "formula.xlsx"
    _workbook(formula, formula=True)
    external_link = tmp_path / "external-link.xlsx"
    _workbook(external_link)
    with zipfile.ZipFile(external_link, "a") as archive:
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            "<Relationships><Relationship TargetMode='External'/></Relationships>",
        )
    external_target = tmp_path / "external-target.xlsx"
    _workbook(external_target)
    with zipfile.ZipFile(external_target, "a") as archive:
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            "<Relationships><Relationship Id='rId1' Target='https://example.invalid/data'/></Relationships>",
        )
    macro = tmp_path / "macro.xlsx"
    _workbook(macro)
    with zipfile.ZipFile(macro, "a") as archive:
        archive.writestr("xl/vbaProject.bin", b"not a macro we will execute")
    oversized = tmp_path / "oversized.xlsx"
    _workbook(oversized)

    with pytest.raises(DatasetImportError, match="formula"):
        _importer(storage, tmp_path)(formula)
    with pytest.raises(DatasetImportError, match="external"):
        _importer(storage, tmp_path)(external_link)
    with pytest.raises(DatasetImportError, match="external"):
        _importer(storage, tmp_path)(external_target)
    with pytest.raises(DatasetImportError, match="macro"):
        _importer(storage, tmp_path)(macro)
    monkeypatch.setattr("mentor.datasets.MAX_XLSX_ARCHIVE_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(DatasetImportError, match="archive"):
        _importer(storage, tmp_path)(oversized)

    assert storage.dataset("dataset-imported") is None
    assert not (tmp_path / "datasets").exists() or not list((tmp_path / "datasets").iterdir())


def test_non_xlsx_signature_and_unsafe_generated_identifier_cannot_create_dataset_paths(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    non_xlsx = tmp_path / "not-a-workbook.xlsx"
    non_xlsx.write_bytes(b"not a zip archive")
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")

    with pytest.raises(DatasetImportError, match="signature"):
        _importer(storage, tmp_path)(non_xlsx)
    with pytest.raises(DatasetImportError, match="identifier"):
        import_local_dataset(source, storage, tmp_path / "datasets", dataset_id_factory=lambda: "../escape")

    assert not (tmp_path / "escape").exists()
    assert storage.dataset("dataset-imported") is None


def test_import_rolls_back_original_when_metadata_recording_fails(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")

    def fail_metadata(**_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(storage, "create_dataset", fail_metadata)

    with pytest.raises(RuntimeError, match="database unavailable"):
        _importer(storage, tmp_path)(source)

    assert not (tmp_path / "datasets").exists() or not list((tmp_path / "datasets").iterdir())


def test_retry_with_existing_dataset_id_preserves_the_existing_immutable_original(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")
    first = _importer(storage, tmp_path)(source)

    with pytest.raises(DatasetImportError, match="identifier"):
        _importer(storage, tmp_path)(source)

    assert first.original_path.read_bytes() == source.read_bytes()
    assert storage.dataset(first.dataset.id) == first.dataset


def test_xlsx_formula_and_column_limits_cannot_be_hidden_by_forged_dimension(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    formula = tmp_path / "dimension-formula.xlsx"
    _workbook(formula, formula=True)
    _rewrite_zip_member(
        formula,
        "xl/worksheets/sheet1.xml",
        lambda xml: xml.replace(b'<dimension ref="A1:B2"/>', b'<dimension ref="A1:A1"/>'),
    )
    columns = tmp_path / "dimension-columns.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append([f"column_{index}" for index in range(101)])
    worksheet.append(list(range(101)))
    workbook.save(columns)
    _rewrite_zip_member(
        columns,
        "xl/worksheets/sheet1.xml",
        lambda xml: xml.replace(b'<dimension ref="A1:CY2"/>', b'<dimension ref="A1:A1"/>'),
    )

    with pytest.raises(DatasetImportError, match="formula"):
        _importer(storage, tmp_path)(formula)
    with pytest.raises(DatasetImportError, match="column"):
        _importer(storage, tmp_path)(columns)


def test_xlsx_macro_relationship_and_drive_qualified_member_are_rejected_by_preflight(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    macro_relationship = tmp_path / "macro-relationship.xlsx"
    _workbook(macro_relationship)
    _rewrite_zip_member(
        macro_relationship,
        "xl/_rels/workbook.xml.rels",
        lambda xml: xml.replace(
            b"</Relationships>",
            b"<Relationship Type='http://schemas.microsoft.com/office/2006/relationships/vbaProject' "
            b"Target='vbaProject.bin'/></Relationships>",
        ),
    )
    drive_member = tmp_path / "drive-member.xlsx"
    _workbook(drive_member)
    with zipfile.ZipFile(drive_member, "a") as archive:
        archive.writestr("C:/outside.xml", "<outside/>")

    with pytest.raises(DatasetImportError, match="macro"):
        _importer(storage, tmp_path)(macro_relationship)
    with pytest.raises(DatasetImportError, match="archive"):
        _importer(storage, tmp_path)(drive_member)


def test_keyboard_interrupt_cleans_promoted_bytes_and_metadata_before_reraising(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")
    create_dataset = storage.create_dataset

    def interrupt_after_recording(**kwargs):
        create_dataset(**kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(storage, "create_dataset", interrupt_after_recording)

    with pytest.raises(KeyboardInterrupt):
        _importer(storage, tmp_path)(source)

    assert storage.dataset("dataset-imported") is None
    assert not (tmp_path / "datasets").exists() or not list((tmp_path / "datasets").iterdir())


def test_import_rejects_caller_controlled_or_unc_dataset_roots(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")

    with pytest.raises(DatasetImportError, match="root"):
        import_local_dataset(
            source, storage, tmp_path / "outside", dataset_id_factory=lambda: "dataset-imported"
        )
    with pytest.raises(DatasetImportError, match="root"):
        import_local_dataset(
            source, storage, Path(r"\\server\share\datasets"), dataset_id_factory=lambda: "dataset-imported"
        )

    assert not (tmp_path / "outside").exists()


def test_interrupt_after_durable_completion_preserves_the_committed_dataset_on_next_start(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")
    complete_import = storage.complete_dataset_import

    def interrupt_after_completion(dataset_id):
        complete_import(dataset_id)
        raise KeyboardInterrupt()

    monkeypatch.setattr(storage, "complete_dataset_import", interrupt_after_completion)

    with pytest.raises(KeyboardInterrupt):
        _importer(storage, tmp_path)(source)

    original = tmp_path / "datasets" / "dataset-imported" / "original.csv"
    assert original.read_bytes() == source.read_bytes()
    assert storage.dataset("dataset-imported") is not None
    monkeypatch.setattr(storage, "complete_dataset_import", complete_import)
    import_local_dataset(source, storage, tmp_path / "datasets", dataset_id_factory=lambda: "dataset-next")
    assert original.read_bytes() == source.read_bytes()
    assert storage.dataset("dataset-imported") is not None


def test_next_start_recovers_staging_directory_when_interrupted_during_filesystem_cleanup(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")
    create_dataset = storage.create_dataset

    def interrupt_after_recording(**kwargs):
        create_dataset(**kwargs)
        raise KeyboardInterrupt()

    interrupted_directory = tmp_path / "datasets" / "dataset-imported"
    real_rmtree = __import__("shutil").rmtree

    def interrupt_cleanup(path, *args, **kwargs):
        if Path(path) == interrupted_directory:
            raise KeyboardInterrupt()
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(storage, "create_dataset", interrupt_after_recording)
    monkeypatch.setattr("mentor.datasets.shutil.rmtree", interrupt_cleanup)

    with pytest.raises(KeyboardInterrupt):
        _importer(storage, tmp_path)(source)

    assert interrupted_directory.exists()
    assert storage.pending_dataset_import_ids() == ["dataset-imported"]
    monkeypatch.setattr("mentor.datasets.shutil.rmtree", real_rmtree)
    monkeypatch.setattr(storage, "create_dataset", create_dataset)
    import_local_dataset(source, storage, tmp_path / "datasets", dataset_id_factory=lambda: "dataset-next")
    assert not interrupted_directory.exists()
    assert storage.pending_dataset_import_ids() == []


@pytest.mark.parametrize("target", ["//server/share", r"\\server\share", " https://example.invalid", "\thttps://example.invalid"])
def test_xlsx_rejects_authority_and_whitespace_prefixed_external_relationship_targets(tmp_path, target):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "external-target.xlsx"
    _workbook(source)
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            f"<Relationships><Relationship Id='rId1' Target='{target}'/></Relationships>",
        )

    with pytest.raises(DatasetImportError, match="external|relationship"):
        _importer(storage, tmp_path)(source)


def test_next_start_recovers_rename_then_interrupt_staging_orphan_without_touching_completed_dataset(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")
    completed = import_local_dataset(source, storage, tmp_path / "datasets", dataset_id_factory=lambda: "dataset-keep")
    incomplete_directory = tmp_path / "datasets" / "dataset-imported"
    real_replace = Path.replace
    real_rmtree = __import__("shutil").rmtree

    def rename_then_interrupt(path, target):
        result = real_replace(path, target)
        if Path(target) == incomplete_directory:
            raise KeyboardInterrupt()
        return result

    def leave_staging_directory(path, *args, **kwargs):
        if Path(path) == incomplete_directory:
            raise OSError("simulate process shutdown during cleanup")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", rename_then_interrupt)
    monkeypatch.setattr("mentor.datasets.shutil.rmtree", leave_staging_directory)

    with pytest.raises(KeyboardInterrupt):
        _importer(storage, tmp_path)(source)

    assert (incomplete_directory / ".pending-import").exists()
    assert storage.pending_dataset_import_ids() == ["dataset-imported"]
    monkeypatch.setattr(Path, "replace", real_replace)
    monkeypatch.setattr("mentor.datasets.shutil.rmtree", real_rmtree)
    import_local_dataset(source, storage, tmp_path / "datasets", dataset_id_factory=lambda: "dataset-next")

    assert not incomplete_directory.exists()
    assert storage.pending_dataset_import_ids() == []
    assert completed.original_path.read_bytes() == source.read_bytes()
    assert storage.dataset(completed.dataset.id) == completed.dataset


def test_xlsx_rejects_duplicate_relationship_id_that_hides_a_formula_target(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "duplicate-relationship.xlsx"
    _workbook(source)
    with zipfile.ZipFile(source) as archive:
        formula_sheet = archive.read("xl/worksheets/sheet1.xml").replace(
            b"<v>1.5</v>", b"<f>1+1</f><v>2</v>"
        )
    _rewrite_zip_member(
        source,
        "xl/_rels/workbook.xml.rels",
        lambda xml: xml.replace(
            b'Target="/xl/worksheets/sheet1.xml"', b'Target="/xl/worksheets/formula.xml"'
        ).replace(
            b"</Relationships>",
            b'<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            b'Id="rId1" Target="/xl/worksheets/sheet1.xml"/></Relationships>',
        ),
    )
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("xl/worksheets/formula.xml", formula_sheet)

    with pytest.raises(DatasetImportError, match="relationship"):
        _importer(storage, tmp_path)(source)


def test_overlapping_import_cannot_recover_an_active_staging_dataset(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    source = tmp_path / "trades.csv"
    source.write_text("Result_R\n1.5\n", encoding="utf-8")
    entered_create = threading.Event()
    release_create = threading.Event()
    create_dataset = storage.create_dataset
    recover_pending_imports = dataset_module._recover_pending_dataset_imports
    recovery_seen = threading.Event()
    failures: list[BaseException] = []

    def block_first_create(**kwargs):
        if kwargs["dataset_id"] == "dataset-first":
            entered_create.set()
            assert release_create.wait(10)
        return create_dataset(**kwargs)

    def run_import(dataset_id):
        try:
            import_local_dataset(source, storage, tmp_path / "datasets", dataset_id_factory=lambda: dataset_id)
        except BaseException as error:
            failures.append(error)

    def watch_recovery(root, active_storage):
        if "dataset-first" in active_storage.pending_dataset_import_ids():
            recovery_seen.set()
        return recover_pending_imports(root, active_storage)

    monkeypatch.setattr(storage, "create_dataset", block_first_create)
    monkeypatch.setattr(dataset_module, "_recover_pending_dataset_imports", watch_recovery)
    first = threading.Thread(target=run_import, args=("dataset-first",))
    second = threading.Thread(target=run_import, args=("dataset-second",))
    first.start()
    assert entered_create.wait(10)
    second.start()
    try:
        assert not recovery_seen.wait(0.25)
    finally:
        release_create.set()
    first.join(10)
    second.join(10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    for dataset_id in ("dataset-first", "dataset-second"):
        assert storage.dataset(dataset_id) is not None
        assert (tmp_path / "datasets" / dataset_id / "original.csv").read_bytes() == source.read_bytes()


def _result_envelope(dataset, mapping_version_id: int, operation: str = "summarize_results"):
    return {
        "provenance": "USER_EMPIRICAL_EVIDENCE",
        "dataset_id": dataset.id,
        "dataset_sha256": dataset.content_sha256,
        "mapping_version_id": mapping_version_id,
        "operation": operation,
        "schema_version": "1",
        "filters": [],
        "metric_definitions": {
            "outcome_rate_denominator": "wins + losses + breakevens",
            "quantile_method": "linear",
            "return_unit": "R",
            "row_order": "source",
        },
        "counts": {"source_rows": 3, "filtered_rows": 3, "valid_rows": 3, "excluded_rows": 0},
        "exclusions": [],
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
    leaky_filter = {
        **envelope,
        "filters": [{"field_id": "Result_R", "operator": "eq", "value_sha256": "a" * 64}],
    }
    leaky_definition = {
        **envelope,
        "metric_definitions": {**envelope["metric_definitions"], "return_unit": "Result_R=2.5"},
    }

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
    for leaky_envelope in (leaky_filter, leaky_definition):
        with pytest.raises(ValueError, match="analysis result envelope"):
            storage.record_analysis_evidence(
                thread_id=thread_id,
                origin_turn_number=1,
                dataset_id=dataset.id,
                mapping_version_id=confirmed.id,
                operation="summarize_results",
                schema_version="1",
                arguments={"dataset_id": dataset.id},
                result=leaky_envelope,
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
        with pytest.raises(sqlite3.IntegrityError, match="details|envelope"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'leaky-filter', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    json.dumps(leaky_filter),
                ),
            )


def test_analysis_envelopes_bind_the_confirmed_mapping_return_unit_in_python_and_sqlite(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    confirmed = storage.confirm_mapping_version(
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "percentage", "source": "manual"}]
        ).id
    )
    thread_id = storage.create_thread("Unit guard")
    envelope = _result_envelope(dataset, confirmed.id)
    percentage_envelope = {
        **envelope,
        "metric_definitions": {**envelope["metric_definitions"], "return_unit": "percentage"},
    }

    with pytest.raises(ValueError, match="analysis result envelope"):
        storage.record_analysis_evidence(
            thread_id=thread_id,
            origin_turn_number=1,
            dataset_id=dataset.id,
            mapping_version_id=confirmed.id,
            operation="summarize_results",
            schema_version="1",
            arguments={"dataset_id": dataset.id},
            result=envelope,
        )
    evidence = storage.record_analysis_evidence(
        thread_id=thread_id,
        origin_turn_number=1,
        dataset_id=dataset.id,
        mapping_version_id=confirmed.id,
        operation="summarize_results",
        schema_version="1",
        arguments={"dataset_id": dataset.id},
        result=percentage_envelope,
    )
    forged_r_output = {
        **percentage_envelope,
        "metric_definitions": {**percentage_envelope["metric_definitions"], "return_unit": "R"},
        "metrics": {"cumulative_return": 1.0},
    }
    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="details|envelope"):
            connection.execute(
                """
                INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                VALUES (?, 'forged-r-unit', ?, ?, ?)
                """,
                (
                    thread_id,
                    evidence.id,
                    json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                    json.dumps(forged_r_output),
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


def test_dataset_and_tool_identifiers_reject_null_and_blob_sql_bypasses(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    dataset = _dataset(storage)
    confirmed = storage.confirm_mapping_version(
        storage.create_mapping_draft(
            dataset.id, [{"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R", "source": "manual"}]
        ).id
    )
    thread_id = storage.create_thread("Type guards")
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
    with pytest.raises(ValueError, match="dataset identifier"):
        storage.create_dataset(
            dataset_id=None,  # type: ignore[arg-type]
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
            dataset_id="dataset-bytes",
            original_name="trades.csv",
            content_sha256=b"a" * 64,  # type: ignore[arg-type]
            original_extension=".csv",
            byte_size=1,
            source_row_count=1,
            status="ready",
            import_spec={"header_row": 0},
            columns=[],
        )
    with pytest.raises(ValueError, match="tool call identifier"):
        storage.record_analysis_tool_output(
            thread_id,
            b"call_safe",  # type: ignore[arg-type]
            evidence.id,
            _result_envelope(dataset, confirmed.id),
        )

    with sqlite3.connect(storage.database_path) as connection:
        for dataset_id, content_sha256 in (
            (None, "b" * 64),
            (b"dataset-blob", "b" * 64),
            ("dataset-null-hash", None),
            ("dataset-blob-hash", b"b" * 64),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO datasets(id, original_name, content_sha256, original_extension, byte_size, source_row_count, status)
                    VALUES (?, 'trades.csv', ?, '.csv', 1, 1, 'ready')
                    """,
                    (dataset_id, content_sha256),
                )
        for tool_call_id in (None, b"call_blob"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO analysis_tool_outputs(thread_id, tool_call_id, evidence_id, arguments_json, output_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        tool_call_id,
                        evidence.id,
                        json.dumps({"dataset_id": dataset.id, "mapping_version_id": confirmed.id, "operation": "summarize_results"}),
                        json.dumps(_result_envelope(dataset, confirmed.id)),
                    ),
                )
