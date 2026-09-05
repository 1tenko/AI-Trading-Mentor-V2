import hashlib
import json
from types import SimpleNamespace

import pytest

from mentor.project_models import AuthorityKind, CanonicalRole
from mentor.source_libraries import (
    CrossLibraryDuplicateError,
    MAX_SOURCE_BYTES,
    SourceImportService,
    garrett_canonical_role,
    library_definition_for_browser_path,
)
from mentor.storage import Storage


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("GxT/Garrett/lesson.txt", "gxt.garrett"),
        ("GxT/Afyz/lesson.txt", "gxt.afyz"),
        ("GxT/Erik/lesson.txt", "gxt.erik"),
        ("GxT/Splash/lesson.txt", "gxt.splash"),
        ("GxT/Zay/lesson.txt", "gxt.zay"),
        ("GxT/Theo Notes/idea.txt", "gxt.theo_notes"),
    ],
)
def test_browser_path_maps_to_one_exact_first_class_library(path, key):
    assert library_definition_for_browser_path(path).library_key == key


def test_library_identity_includes_corpus_and_does_not_merge_same_authority(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    gxt = storage.create_source_library(
        "gxt.afyz", "gxt", "Afyz", AuthorityKind.MENTOR, "Afyz — GxT"
    )
    other = storage.create_source_library(
        "other.afyz", "other-method", "Afyz", AuthorityKind.MENTOR, "Afyz — Other Method"
    )

    assert gxt.id != other.id
    assert storage.source_library("gxt.afyz").corpus_key == "gxt"
    with pytest.raises(ValueError, match="already exists"):
        storage.create_source_library("gxt.afyz", "gxt", "Afyz", AuthorityKind.MENTOR, "Duplicate")


def test_same_library_hash_dedupes_but_cross_library_hash_conflicts(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    service = SourceImportService(storage)
    service.ensure_library("gxt.afyz")
    service.ensure_library("gxt.erik")
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("same synthetic lesson", encoding="utf-8")
    second.write_text("same synthetic lesson", encoding="utf-8")

    original = service.register_local_revision("gxt.afyz", first, "Topic/first.txt")
    duplicate = service.register_local_revision("gxt.afyz", second, "Topic/second.txt")

    assert duplicate.id == original.id
    assert storage.library_revision_count("gxt.afyz") == 1
    with pytest.raises(CrossLibraryDuplicateError, match="another library"):
        service.register_local_revision("gxt.erik", second, "Topic/second.txt")


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        ("Anomaly Mentorship/GxT Advanced/01.txt", CanonicalRole.CURRENT_CANONICAL_ADVANCED),
        ("Anomaly Mentorship/Beginner/01.txt", CanonicalRole.CURRENT_CANONICAL_FOUNDATION),
        ("Older Course/01.txt", CanonicalRole.GARRETT_ARCHIVAL_AND_COMPLEMENTARY),
    ],
)
def test_garrett_role_is_exactly_path_derived(relative_path, expected):
    assert garrett_canonical_role(relative_path) is expected


def test_non_garrett_revision_refuses_garrett_canonical_role(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    service = SourceImportService(storage)
    service.ensure_library("gxt.afyz")
    source = tmp_path / "lesson.txt"
    source.write_text("synthetic", encoding="utf-8")

    with pytest.raises(ValueError, match="Garrett canonical role"):
        service.register_local_revision(
            "gxt.afyz",
            source,
            "lesson.txt",
            canonical_role=CanonicalRole.CURRENT_CANONICAL_ADVANCED,
        )


def test_failed_revision_has_no_active_file_ownership(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    service = SourceImportService(storage)
    service.ensure_library("gxt.splash")
    source = tmp_path / "lesson.txt"
    source.write_text("synthetic", encoding="utf-8")
    revision = service.register_local_revision(
        "gxt.splash", source, "lesson.txt", file_id="file_failed", index_state="FAILED"
    )

    assert revision.index_state == "FAILED"
    assert service.library_for_file("file_failed") is None


def test_project_library_summary_is_safe_and_contains_no_private_identifiers(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    service = SourceImportService(storage)
    project = storage.create_project("GxT")
    library = service.ensure_library("gxt.zay")
    storage.set_project_library(project.id, library.id, enabled=True)
    source = tmp_path / "secret-folder" / "lesson.txt"
    source.parent.mkdir()
    source.write_text("private synthetic body", encoding="utf-8")
    service.register_local_revision(
        "gxt.zay", source, "Lessons/lesson.txt", file_id="file_private", index_state="READY"
    )

    payload = storage.safe_project_libraries(project.id)
    serialized = json.dumps(payload)

    assert payload == [{
        "library_key": "gxt.zay",
        "display_name": "Zay — GxT",
        "enabled": True,
        "source_count": 1,
        "index_status": "NONE",
    }]
    assert "private synthetic body" not in serialized
    assert str(source) not in serialized
    assert "file_private" not in serialized
    assert hashlib.sha256(b"private synthetic body").hexdigest() not in serialized


class FakeSourceOpenAI:
    def __init__(self, *, fail_indexing=False):
        self.fail_indexing = fail_indexing
        self.uploaded = []
        self.stores = []
        self.files = SimpleNamespace(create=self.create_file)
        self.vector_stores = SimpleNamespace(
            create=self.create_store,
            files=SimpleNamespace(create=self.attach, retrieve=self.retrieve),
        )

    def create_store(self, *, name, metadata):
        self.stores.append((name, metadata))
        return SimpleNamespace(id=f"vs_{len(self.stores)}")

    def create_file(self, *, file, purpose):
        self.uploaded.append((file.read(), purpose))
        return SimpleNamespace(id=f"file_{len(self.uploaded)}")

    def attach(self, vector_store_id, *, file_id, attributes):
        return SimpleNamespace(id=f"vsf_{file_id}")

    def retrieve(self, file_id, *, vector_store_id):
        return SimpleNamespace(status="failed" if self.fail_indexing else "completed")


def test_browser_import_stages_and_finalizes_without_remote_calls(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    client = FakeSourceOpenAI()
    service = SourceImportService(storage, client, staging_root=tmp_path / "imports")

    created = service.create_staging_import(project.id)
    service.stage_browser_file(
        created["id"], "GxT/Erik/Youtube/lesson.txt", 1, b"private synthetic transcript"
    )
    summary = service.finalize_manifest(created["id"])

    assert summary["state"] == "READY_FOR_CONFIRMATION"
    assert summary["libraries"] == [{
        "library_key": "gxt.erik", "display_name": "Erik — GxT",
        "total": 1, "new": 1, "duplicates": 0, "conflicts": 0,
    }]
    assert client.uploaded == []
    assert client.stores == []
    assert "private synthetic transcript" not in json.dumps(summary)
    assert str(tmp_path) not in json.dumps(summary)


def test_browser_import_summary_keeps_every_declared_authority_separate(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    client = FakeSourceOpenAI()
    service = SourceImportService(storage, client, staging_root=tmp_path / "imports")
    batch = service.create_staging_import(project.id)
    paths = (
        "GxT/Garrett/Anomaly Mentorship/Beginner/one.txt",
        "GxT/Afyz/Course/two.txt",
        "GxT/Erik/Youtube/three.txt",
        "GxT/Splash/Q&A/four.txt",
        "GxT/Zay/Notes/five.txt",
        "GxT/Theo Notes/Research/six.txt",
    )
    for ordinal, path in enumerate(paths, 1):
        service.stage_browser_file(batch["id"], path, ordinal, f"body {ordinal}".encode())

    summary = service.finalize_manifest(batch["id"])

    assert [item["library_key"] for item in summary["libraries"]] == [
        "gxt.afyz", "gxt.erik", "gxt.garrett", "gxt.splash", "gxt.theo_notes", "gxt.zay"
    ]
    assert all(item["total"] == item["new"] == 1 for item in summary["libraries"])
    assert client.uploaded == []
    assert client.stores == []


def test_confirmed_import_indexes_then_registers_an_immutable_ready_revision(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    client = FakeSourceOpenAI()
    service = SourceImportService(storage, client, staging_root=tmp_path / "imports")
    batch = service.create_staging_import(project.id)
    service.stage_browser_file(batch["id"], "GxT/Garrett/Anomaly Mentorship/Beginner/one.txt", 1, b"A")
    service.finalize_manifest(batch["id"])

    result = service.confirm_import(batch["id"], confirm=True, sleep=lambda _: None)

    assert result["state"] == "COMPLETE"
    assert result["imported"] == 1
    assert storage.library_revision_count("gxt.garrett") == 1
    revision = storage.library_revision(1)
    assert revision[4] == CanonicalRole.CURRENT_CANONICAL_FOUNDATION
    assert revision[7] == "READY"
    assert client.uploaded == [(b"A", "assistants")]


def test_failed_indexing_is_visible_safe_and_not_searchable(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    client = FakeSourceOpenAI(fail_indexing=True)
    service = SourceImportService(storage, client, staging_root=tmp_path / "imports")
    batch = service.create_staging_import(project.id)
    service.stage_browser_file(batch["id"], "GxT/Splash/Q&A/one.txt", 1, b"private failure body")
    service.finalize_manifest(batch["id"])

    with pytest.raises(RuntimeError, match="indexing failed"):
        service.confirm_import(batch["id"], confirm=True, sleep=lambda _: None)

    status = service.import_status(batch["id"])
    assert status["state"] == "FAILED"
    assert status["error"] == "A source could not be indexed. You can retry the import."
    assert storage.library_revision_count("gxt.splash") == 0
    assert "private failure body" not in json.dumps(status)
    assert "file_1" not in json.dumps(status)
    assert "vs_1" not in json.dumps(status)
    private_manifest = storage.library_import_batch(batch["id"])[3]
    assert private_manifest["files"][0]["remote_file_id"] == "file_1"
    assert private_manifest["files"][0]["remote_vector_store_file_id"] == "vsf_file_1"
    assert private_manifest["files"][0]["remote_status"] == "FAILED"

    client.fail_indexing = False
    retried = service.confirm_import(batch["id"], confirm=True, sleep=lambda _: None)
    assert retried["state"] == "COMPLETE"
    assert storage.library_revision_count("gxt.splash") == 1


@pytest.mark.parametrize(
    "path",
    ["../Erik/one.txt", "/GxT/Erik/one.txt", "GxT/Unknown/one.txt", "GxT/Erik/one.pdf"],
)
def test_browser_staging_rejects_unsafe_or_unknown_paths(tmp_path, path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    service = SourceImportService(storage, staging_root=tmp_path / "imports")
    batch = service.create_staging_import(project.id)

    with pytest.raises(ValueError):
        service.stage_browser_file(batch["id"], path, 1, b"text")


def test_browser_staging_rejects_oversized_sources(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    project = storage.create_project("GxT")
    service = SourceImportService(storage, staging_root=tmp_path / "imports")
    batch = service.create_staging_import(project.id)

    with pytest.raises(ValueError, match="10 MiB"):
        service.stage_browser_file(batch["id"], "GxT/Erik/one.txt", 1, b"x" * (MAX_SOURCE_BYTES + 1))
