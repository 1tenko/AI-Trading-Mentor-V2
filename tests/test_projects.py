import shutil
import sqlite3

import pytest

from mentor.project_models import (
    AuthorityKind,
    CanonicalRole,
    ProjectStatus,
    ResearchDepth,
    ThreadSourceBehavior,
)
from mentor.storage import Storage


def test_phase6_enums_reject_unknown_values():
    for enum_type in (
        AuthorityKind,
        CanonicalRole,
        ProjectStatus,
        ResearchDepth,
        ThreadSourceBehavior,
    ):
        with pytest.raises(ValueError):
            enum_type("NOT_A_REAL_STATE")


def test_phase5_database_migrates_additively_and_idempotently(tmp_path):
    original = tmp_path / "phase5.sqlite3"
    with sqlite3.connect(original) as connection:
        connection.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE sources (
                relative_path TEXT PRIMARY KEY, filename TEXT NOT NULL,
                year INTEGER NOT NULL, local_path TEXT NOT NULL,
                modified_at REAL NOT NULL, file_id TEXT NOT NULL,
                vector_store_file_id TEXT NOT NULL
            );
            CREATE TABLE threads (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE thread_items (
                thread_id INTEGER NOT NULL REFERENCES threads(id),
                position INTEGER NOT NULL, item_json TEXT NOT NULL,
                PRIMARY KEY(thread_id, position)
            );
            INSERT INTO settings VALUES ('vector_store_id', 'vs_jacob');
            INSERT INTO sources VALUES (
                '2026/lesson.txt', 'lesson.txt', 2026, 'C:/private/lesson.txt',
                1.0, 'file_jacob', 'vsf_jacob'
            );
            INSERT INTO threads VALUES (7, 'Accepted Phase 5 chat');
            INSERT INTO thread_items VALUES (
                7, 0, '{"role":"user","content":[{"type":"input_text","text":"Question"}]}'
            );
            """
        )
    migrated = tmp_path / "copy" / "mentor.sqlite3"
    migrated.parent.mkdir()
    shutil.copy2(original, migrated)

    storage = Storage(migrated)
    storage.initialize()
    storage.initialize()

    assert storage.vector_store_id() == "vs_jacob"
    assert storage.source_count() == 1
    assert storage.thread_items(7)[0]["role"] == "user"
    assert storage.thread_context(7).thread_source_behavior is ThreadSourceBehavior.LEGACY_JACOB
    with sqlite3.connect(migrated) as connection:
        assert connection.execute("SELECT COUNT(*) FROM strategy_projects").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM source_libraries").fetchone() == (0,)


def test_fresh_threads_are_neutral_and_project_threads_require_an_owner(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()

    general_id = storage.create_thread("General")
    assert storage.thread_context(general_id).thread_source_behavior is ThreadSourceBehavior.GENERAL_NEUTRAL

    with pytest.raises(ValueError, match="project thread requires a project"):
        storage.create_thread("Invalid", behavior=ThreadSourceBehavior.PROJECT)

    project = storage.create_project("GxT Mastery")
    project_thread = storage.create_thread(
        "Study",
        behavior=ThreadSourceBehavior.PROJECT,
        project_id=project.id,
    )
    context = storage.thread_context(project_thread)
    assert context.project_id == project.id
    assert context.thread_source_behavior is ThreadSourceBehavior.PROJECT


def test_project_and_thread_contracts_fail_closed(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()

    with pytest.raises(ValueError, match="project name"):
        storage.create_project("   ")
    with pytest.raises(ValueError, match="project does not exist"):
        storage.create_thread(
            "Unknown owner",
            behavior=ThreadSourceBehavior.PROJECT,
            project_id=999,
        )
    with pytest.raises(ValueError, match="cannot have a project"):
        storage.create_thread("Invalid general", project_id=1)
    project = storage.create_project("Isolation")
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO project_source_libraries(project_id, library_id, enabled) VALUES (?, ?, 1)",
                (project.id, 999),
            )


def test_legacy_jacob_dry_run_is_read_only_and_safe(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    storage.register_source(
        relative_path="2026/lesson.txt",
        filename="lesson.txt",
        year=2026,
        local_path=str(tmp_path / "missing-private-lesson.txt"),
        modified_at=1.0,
        file_id="file_jacob",
        vector_store_file_id="vsf_jacob",
    )

    before = storage.phase6_table_counts()
    first = storage.migrate_legacy_jacob_dry_run()
    second = storage.migrate_legacy_jacob_dry_run()

    assert first == second
    assert first == {
        "legacy_source_count": 1,
        "mappable_source_count": 0,
        "missing_local_source_count": 1,
        "vector_store_configured": True,
        "has_discrepancy": True,
    }
    assert storage.phase6_table_counts() == before
