import hashlib
import json

import pytest

from mentor.project_models import AuthorityKind, CanonicalRole
from mentor.source_libraries import (
    CrossLibraryDuplicateError,
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
