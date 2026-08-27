"""Immutable metadata models and local-only import for backtest datasets."""

import csv
import hashlib
import io
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from openpyxl import load_workbook

if TYPE_CHECKING:
    from mentor.storage import Storage


MAX_DATASET_BYTES = 50 * 1024 * 1024
MAX_DATASET_ROWS = 250_000
MAX_DATASET_COLUMNS = 100
MAX_DATASET_CELLS = 2_000_000
MAX_XLSX_ARCHIVE_MEMBERS = 1_000
MAX_XLSX_ARCHIVE_COMPRESSED_BYTES = MAX_DATASET_BYTES
MAX_XLSX_ARCHIVE_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 100
_DATASET_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_EXTERNAL_RELATIONSHIP_PATTERN = re.compile(br"TargetMode\s*=\s*['\"]External['\"]", re.IGNORECASE)


@dataclass(frozen=True)
class Dataset:
    id: str
    original_name: str
    content_sha256: str
    original_extension: str
    byte_size: int
    source_row_count: int
    status: str
    import_spec_id: int


@dataclass(frozen=True)
class DatasetImportSpec:
    header_row: int
    csv_encoding: str | None = None
    csv_delimiter: str | None = None
    csv_quoting: str | None = None
    parser_version: str | None = None
    row_order_policy: str | None = None
    time_parse_policy: str | None = None
    selected_sheet: str | None = None


@dataclass(frozen=True)
class DatasetColumn:
    ordinal: int
    original_header: str
    inferred_type: str
    null_count: int
    invalid_count: int


@dataclass(frozen=True)
class DatasetMappingVersion:
    id: int
    dataset_id: str
    version: int
    status: str
    parent_mapping_version_id: int | None


@dataclass(frozen=True)
class MappingEntry:
    column_ordinal: int
    semantic_role: str | None = None
    unit: str | None = None
    analysis_label: str | None = None
    source: str = "manual"


@dataclass(frozen=True)
class AnalysisEvidence:
    id: int
    thread_id: int
    origin_turn_number: int
    dataset_id: str
    dataset_sha256: str
    import_spec_id: int
    mapping_version_id: int
    operation: str
    schema_version: str


@dataclass(frozen=True)
class ThreadDatasetScope:
    thread_id: int
    dataset_id: str | None
    selected_at: str


class DatasetImportError(ValueError):
    """A local file failed the import safety boundary."""


@dataclass(frozen=True)
class DatasetImportResult:
    dataset: Dataset
    original_path: Path
    duplicate_row_count: int


def import_local_dataset(
    source_path: Path,
    storage: "Storage",
    datasets_root: Path,
    *,
    selected_sheet: str | None = None,
    dataset_id_factory: Callable[[], str] | None = None,
) -> DatasetImportResult:
    """Copy, validate, and record one local CSV/XLSX upload without retaining rows."""
    source_path = Path(source_path)
    extension = source_path.suffix.lower()
    if extension not in {".csv", ".xlsx"}:
        raise DatasetImportError("only CSV or XLSX files are supported")
    if not source_path.is_file():
        raise DatasetImportError("dataset file does not exist")
    if source_path.stat().st_size > MAX_DATASET_BYTES:
        raise DatasetImportError("dataset exceeds the 50 MiB size limit")

    datasets_root = Path(datasets_root)
    datasets_root.mkdir(parents=True, exist_ok=True)
    temporary_directory = Path(tempfile.mkdtemp(prefix=".import-", dir=datasets_root))
    final_directory: Path | None = None
    try:
        temporary_original = temporary_directory / f"original{extension}"
        content_sha256, byte_size = _copy_with_hash(source_path, temporary_original)
        if extension == ".csv":
            parsed = _parse_csv(temporary_original)
        else:
            parsed = _parse_xlsx(temporary_original, selected_sheet)

        dataset_id = (dataset_id_factory or _new_dataset_id)()
        if not isinstance(dataset_id, str) or _DATASET_ID_PATTERN.fullmatch(dataset_id) is None:
            raise DatasetImportError("dataset identifier is invalid")
        final_directory = datasets_root / dataset_id
        if final_directory.exists():
            raise DatasetImportError("dataset identifier already exists")
        temporary_directory.replace(final_directory)
        original_path = final_directory / temporary_original.name
        dataset = storage.create_dataset(
            dataset_id=dataset_id,
            original_name=source_path.name,
            content_sha256=content_sha256,
            original_extension=extension,
            byte_size=byte_size,
            source_row_count=parsed.row_count,
            status="ready",
            import_spec=DatasetImportSpec(
                header_row=0,
                selected_sheet=parsed.selected_sheet,
                csv_encoding=parsed.csv_encoding,
                csv_delimiter=parsed.csv_delimiter,
                csv_quoting=parsed.csv_quoting,
                parser_version=parsed.parser_version,
                row_order_policy="source",
                time_parse_policy="unambiguous_only",
            ),
            columns=[
                DatasetColumn(index, header, "unknown", null_count, 0)
                for index, (header, null_count) in enumerate(zip(parsed.headers, parsed.null_counts, strict=True))
            ],
        )
        return DatasetImportResult(dataset, original_path, parsed.duplicate_row_count)
    except Exception:
        if final_directory is not None and final_directory.exists():
            shutil.rmtree(final_directory)
        raise
    finally:
        if temporary_directory.exists():
            shutil.rmtree(temporary_directory)


@dataclass(frozen=True)
class _ParsedDataset:
    headers: list[str]
    null_counts: list[int]
    row_count: int
    duplicate_row_count: int
    selected_sheet: str | None
    csv_encoding: str | None
    csv_delimiter: str | None
    csv_quoting: str | None
    parser_version: str


def _new_dataset_id() -> str:
    return f"dataset-{uuid.uuid4().hex}"


def _copy_with_hash(source_path: Path, destination_path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_size = 0
    with source_path.open("rb") as source, destination_path.open("xb") as destination:
        while chunk := source.read(1024 * 1024):
            byte_size += len(chunk)
            if byte_size > MAX_DATASET_BYTES:
                raise DatasetImportError("dataset exceeds the 50 MiB size limit")
            digest.update(chunk)
            destination.write(chunk)
    return digest.hexdigest(), byte_size


def _parse_csv(path: Path) -> _ParsedDataset:
    raw = path.read_bytes()
    encoding = _csv_encoding(raw)
    try:
        text = raw.decode(encoding, errors="strict")
    except UnicodeDecodeError as error:
        raise DatasetImportError("CSV decode failed") from error
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error as error:
        if any(delimiter in text for delimiter in ";\t|"):
            raise DatasetImportError("CSV dialect could not be determined") from error
        dialect = csv.excel
    if dialect.delimiter not in {",", ";", "\t", "|"} or dialect.quotechar != '"':
        raise DatasetImportError("CSV dialect is unsupported")
    try:
        rows = csv.reader(io.StringIO(text, newline=""), dialect=dialect, strict=True)
        headers = _headers(next(rows))
        return _parsed_rows(
            headers,
            rows,
            selected_sheet=None,
            csv_encoding=encoding,
            csv_delimiter=dialect.delimiter,
            csv_quoting=dialect.quotechar,
            parser_version="csv-stdlib-1",
        )
    except StopIteration as error:
        raise DatasetImportError("CSV has no header row") from error
    except csv.Error as error:
        raise DatasetImportError("CSV parse failed") from error


def _csv_encoding(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    return "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"


def _parse_xlsx(path: Path, selected_sheet: str | None) -> _ParsedDataset:
    _preflight_xlsx(path)
    try:
        workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    except Exception as error:
        raise DatasetImportError("XLSX parse failed") from error
    try:
        for worksheet in workbook.worksheets:
            for row in worksheet.iter_rows():
                if any(cell.data_type == "f" for cell in row):
                    raise DatasetImportError("XLSX formula cells are not supported")
        worksheet_names = [worksheet.title for worksheet in workbook.worksheets]
        selected_sheet = selected_sheet or (worksheet_names[0] if worksheet_names else None)
        if selected_sheet not in worksheet_names:
            raise DatasetImportError("selected XLSX worksheet does not exist")
        worksheet = workbook[selected_sheet]
        if worksheet.max_column > MAX_DATASET_COLUMNS or worksheet.max_row > MAX_DATASET_ROWS + 1:
            raise DatasetImportError("XLSX exceeds row or column limits")
        values = worksheet.iter_rows(values_only=True)
        try:
            headers = _headers(next(values))
        except StopIteration as error:
            raise DatasetImportError("XLSX has no header row") from error
        return _parsed_rows(
            headers,
            values,
            selected_sheet=selected_sheet,
            csv_encoding=None,
            csv_delimiter=None,
            csv_quoting=None,
            parser_version="openpyxl-3.1",
        )
    finally:
        workbook.close()


def _preflight_xlsx(path: Path) -> None:
    with path.open("rb") as file:
        if file.read(4) != b"PK\x03\x04":
            raise DatasetImportError("XLSX signature is invalid")
    if not zipfile.is_zipfile(path):
        raise DatasetImportError("XLSX signature is invalid")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            compressed_bytes = sum(member.compress_size for member in members)
            uncompressed_bytes = sum(member.file_size for member in members)
            if (
                len(members) > MAX_XLSX_ARCHIVE_MEMBERS
                or compressed_bytes > MAX_XLSX_ARCHIVE_COMPRESSED_BYTES
                or uncompressed_bytes > MAX_XLSX_ARCHIVE_UNCOMPRESSED_BYTES
                or len(names) != len(set(names))
            ):
                raise DatasetImportError("XLSX archive exceeds safety limits")
            for member in members:
                parts = Path(member.filename).parts
                if (
                    member.flag_bits & 0x1
                    or member.filename.startswith(("/", "\\"))
                    or ".." in parts
                    or "\\" in member.filename
                    or (member.compress_size and member.file_size > member.compress_size * MAX_XLSX_COMPRESSION_RATIO)
                    or (not member.compress_size and member.file_size > 0)
                ):
                    raise DatasetImportError("XLSX archive is unsafe")
            if not {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"}.issubset(names):
                raise DatasetImportError("XLSX OOXML signature is invalid")
            content_types = archive.read("[Content_Types].xml")
            relationship_files = [name for name in names if name.endswith(".rels")]
            if (
                any("vbaproject" in name.lower() for name in names)
                or b"macroEnabled" in content_types
                or any(name.startswith("xl/externalLinks/") for name in names)
                or any(_EXTERNAL_RELATIONSHIP_PATTERN.search(archive.read(name)) for name in relationship_files)
            ):
                raise DatasetImportError("XLSX macros or external links are not supported")
    except zipfile.BadZipFile as error:
        raise DatasetImportError("XLSX archive is invalid") from error


def _headers(row: Iterable[object]) -> list[str]:
    headers = list(row)
    if not headers:
        raise DatasetImportError("dataset has no header row")
    if len(headers) > MAX_DATASET_COLUMNS:
        raise DatasetImportError("dataset exceeds the column limit")
    if any(not isinstance(header, str) or not header.strip() for header in headers):
        raise DatasetImportError("dataset headers must be non-empty text")
    if len(set(headers)) != len(headers):
        raise DatasetImportError("dataset headers must be unique")
    return headers


def _parsed_rows(
    headers: list[str],
    rows: Iterable[Iterable[object]],
    *,
    selected_sheet: str | None,
    csv_encoding: str | None,
    csv_delimiter: str | None,
    csv_quoting: str | None,
    parser_version: str,
) -> _ParsedDataset:
    row_count = 0
    duplicate_row_count = 0
    null_counts = [0] * len(headers)
    row_hashes: set[bytes] = set()
    for row in rows:
        values = list(row)
        if not values or all(value is None or value == "" for value in values):
            continue
        if len(values) != len(headers):
            raise DatasetImportError("dataset rows do not match the header")
        row_count += 1
        if row_count > MAX_DATASET_ROWS:
            raise DatasetImportError("dataset exceeds the row limit")
        if row_count * len(headers) > MAX_DATASET_CELLS:
            raise DatasetImportError("dataset exceeds the cell limit")
        for index, value in enumerate(values):
            if value is None or value == "":
                null_counts[index] += 1
        row_hash = _row_hash(values)
        if row_hash in row_hashes:
            duplicate_row_count += 1
        else:
            row_hashes.add(row_hash)
    return _ParsedDataset(
        headers,
        null_counts,
        row_count,
        duplicate_row_count,
        selected_sheet,
        csv_encoding,
        csv_delimiter,
        csv_quoting,
        parser_version,
    )


def _row_hash(values: list[object]) -> bytes:
    digest = hashlib.sha256()
    for value in values:
        encoded = f"{type(value).__name__}:{value!r}".encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()
