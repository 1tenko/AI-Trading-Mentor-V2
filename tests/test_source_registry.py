from hashlib import sha256

from mentor.source_registry import JACOB_COLLECTION_ID, backfill_jacob_registry, discover_transcripts
from mentor.storage import Storage


def test_discover_transcripts_preserves_paths_and_assigns_years(tmp_path):
    (tmp_path / "2025").mkdir()
    (tmp_path / "2025" / "lesson.txt").write_text("old lesson", encoding="utf-8")
    (tmp_path / "May").mkdir()
    (tmp_path / "May" / "lesson.txt").write_text("new lesson", encoding="utf-8")
    (tmp_path / "May" / "ignore.md").write_text("ignore", encoding="utf-8")

    transcripts = discover_transcripts(tmp_path)

    assert [(item.relative_path, item.year) for item in transcripts] == [
        ("2025/lesson.txt", 2025),
        ("May/lesson.txt", 2026),
    ]
    assert transcripts[0].modified_at == (tmp_path / "2025" / "lesson.txt").stat().st_mtime


def test_backfill_preserves_remote_linkage_for_byte_identical_legacy_input(tmp_path):
    transcripts = tmp_path / "transcripts"
    path = transcripts / "2025" / "lesson.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original bytes")
    storage = _storage_with_legacy_source(tmp_path, path, "2025/lesson.txt", "file_legacy", "vsf_legacy")

    backfill_jacob_registry(transcripts, storage)
    backfill_jacob_registry(transcripts, storage)

    source = storage.library_source_for_identity(JACOB_COLLECTION_ID, "legacy:file_legacy")
    assert source is not None
    assert [(revision.content_sha256, revision.lifecycle_state, revision.remote_file_id,
             revision.remote_vector_store_file_id) for revision in storage.source_revisions(source.source_id)] == [
        (sha256(b"original bytes").hexdigest(), "active", "file_legacy", "vsf_legacy")
    ]
    assert storage.source_change(source.source_id) is None


def test_backfill_marks_a_changed_file_pending_on_its_first_migration(tmp_path):
    transcripts = tmp_path / "transcripts"
    path = transcripts / "2025" / "lesson.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"replacement bytes")
    storage = _storage_with_legacy_source(
        tmp_path,
        path,
        "2025/lesson.txt",
        "file_legacy",
        "vsf_legacy",
        modified_at=path.stat().st_mtime - 1,
    )

    backfill_jacob_registry(transcripts, storage)

    source = storage.library_source_for_identity(JACOB_COLLECTION_ID, "legacy:file_legacy")
    assert source is not None
    assert [(revision.content_sha256, revision.lifecycle_state, revision.remote_file_id,
             revision.remote_vector_store_file_id) for revision in storage.source_revisions(source.source_id)] == [
        (sha256(b"replacement bytes").hexdigest(), "replacement_pending", None, None)
    ]
    assert storage.source_change(source.source_id).lifecycle_state == "replacement_pending"


def test_backfill_records_changed_bytes_as_a_pending_replacement(tmp_path):
    transcripts = tmp_path / "transcripts"
    path = transcripts / "May" / "lesson.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original bytes")
    storage = _storage_with_legacy_source(tmp_path, path, "May/lesson.txt", "file_legacy", "vsf_legacy")
    backfill_jacob_registry(transcripts, storage)
    path.write_bytes(b"replacement bytes")

    backfill_jacob_registry(transcripts, storage)
    backfill_jacob_registry(transcripts, storage)

    source = storage.library_source_for_identity(JACOB_COLLECTION_ID, "legacy:file_legacy")
    assert source is not None
    revisions = storage.source_revisions(source.source_id)
    assert [(revision.content_sha256, revision.lifecycle_state, revision.remote_file_id)
            for revision in revisions] == [
        (sha256(b"original bytes").hexdigest(), "active", "file_legacy"),
        (sha256(b"replacement bytes").hexdigest(), "replacement_pending", None),
    ]
    assert storage.source_change(source.source_id).lifecycle_state == "replacement_pending"


def test_backfill_makes_removed_and_unreadable_legacy_inputs_visible(tmp_path):
    transcripts = tmp_path / "transcripts"
    removed = transcripts / "2025" / "removed.txt"
    unreadable = transcripts / "May" / "unreadable.txt"
    removed.parent.mkdir(parents=True)
    unreadable.parent.mkdir(parents=True)
    removed.write_bytes(b"removed bytes")
    unreadable.write_bytes(b"unreadable bytes")
    storage = _storage_with_legacy_source(tmp_path, removed, "2025/removed.txt", "file_removed", "vsf_removed")
    _register_legacy_source(storage, unreadable, "May/unreadable.txt", "file_unreadable", "vsf_unreadable")
    backfill_jacob_registry(transcripts, storage)
    removed.unlink()
    unreadable.unlink()
    unreadable.mkdir()

    backfill_jacob_registry(transcripts, storage)

    removed_source = storage.library_source_for_identity(JACOB_COLLECTION_ID, "legacy:file_removed")
    unreadable_source = storage.library_source_for_identity(JACOB_COLLECTION_ID, "legacy:file_unreadable")
    assert storage.source_change(removed_source.source_id).lifecycle_state == "removed"
    assert storage.source_change(unreadable_source.source_id).lifecycle_state == "unreadable"


def test_backfill_uses_legacy_file_identity_when_filenames_match(tmp_path):
    transcripts = tmp_path / "transcripts"
    first = transcripts / "2025" / "lesson.txt"
    second = transcripts / "May" / "lesson.txt"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first bytes")
    second.write_bytes(b"second bytes")
    storage = _storage_with_legacy_source(tmp_path, first, "2025/lesson.txt", "file_first", "vsf_first")
    _register_legacy_source(storage, second, "May/lesson.txt", "file_second", "vsf_second")

    backfill_jacob_registry(transcripts, storage)

    first_source = storage.library_source_for_identity(JACOB_COLLECTION_ID, "legacy:file_first")
    second_source = storage.library_source_for_identity(JACOB_COLLECTION_ID, "legacy:file_second")
    assert first_source.source_id != second_source.source_id
    assert first_source.original_filename == second_source.original_filename == "lesson.txt"


def _storage_with_legacy_source(
    tmp_path, path, relative_path, file_id, vector_store_file_id, modified_at=None
):
    storage = Storage(tmp_path / "data" / "mentor.sqlite3")
    storage.initialize()
    _register_legacy_source(
        storage, path, relative_path, file_id, vector_store_file_id, modified_at=modified_at
    )
    return storage


def _register_legacy_source(
    storage, path, relative_path, file_id, vector_store_file_id, modified_at=None
):
    storage.register_source(
        relative_path=relative_path,
        filename=path.name,
        year=2025 if "2025" in relative_path else 2026,
        local_path=str(path),
        modified_at=path.stat().st_mtime if modified_at is None else modified_at,
        file_id=file_id,
        vector_store_file_id=vector_store_file_id,
    )
