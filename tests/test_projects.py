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
from mentor.project_service import ProjectService
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


def test_phase6_preserves_phase3_library_sources_and_uses_namespaced_tables(tmp_path):
    database_path = tmp_path / "mentor.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE library_sources (
                source_id TEXT PRIMARY KEY,
                collection_id TEXT NOT NULL,
                identity_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                author TEXT NOT NULL,
                course TEXT NOT NULL,
                lesson_title TEXT NOT NULL,
                year INTEGER,
                original_filename TEXT NOT NULL,
                local_provenance TEXT NOT NULL
            );
            INSERT INTO library_sources VALUES (
                'source-phase3', 'jacob', '2026/lesson', 'transcript', 'Jacob',
                'Mentorship', 'Lesson', 2026, 'lesson.txt', 'private-local-reference'
            );
            """
        )

    storage = Storage(database_path)
    storage.initialize()
    storage.initialize()

    with sqlite3.connect(database_path) as connection:
        phase3_columns = [row[1] for row in connection.execute("PRAGMA table_info(library_sources)")]
        revision_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(mentor_library_source_revisions)"
        ).fetchall()
        assert phase3_columns == [
            "source_id", "collection_id", "identity_key", "source_type", "author",
            "course", "lesson_title", "year", "original_filename", "local_provenance",
        ]
        assert connection.execute(
            "SELECT source_id, lesson_title FROM library_sources"
        ).fetchall() == [("source-phase3", "Lesson")]
        assert [row[1] for row in connection.execute(
            "PRAGMA table_info(mentor_library_sources)"
        )][:3] == ["id", "library_id", "source_key"]
        assert any(
            row[2] == "mentor_library_sources" and row[3] == "source_id" and row[4] == "id"
            for row in revision_foreign_keys
        )
        assert not any(
            row[2] == "library_sources" for row in revision_foreign_keys
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_phase6_retires_only_the_empty_obsolete_revision_table(tmp_path):
    database_path = tmp_path / "mentor.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE library_source_revisions (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES library_sources(id),
                sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                staged_path TEXT NOT NULL,
                canonical_role TEXT,
                file_id TEXT,
                vector_store_file_id TEXT,
                index_state TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    Storage(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'library_source_revisions'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'mentor_library_source_revisions'"
        ).fetchone() == ("mentor_library_source_revisions",)


def test_phase6_stops_if_the_obsolete_revision_table_contains_data(tmp_path):
    database_path = tmp_path / "mentor.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE library_source_revisions (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES library_sources(id),
                sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                staged_path TEXT NOT NULL,
                canonical_role TEXT,
                file_id TEXT,
                vector_store_file_id TEXT,
                index_state TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO library_source_revisions(
                source_id, sha256, byte_size, relative_path, staged_path, index_state
            ) VALUES (1, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 1,
                      'lesson.txt', 'private-path', 'STAGED');
            """
        )

    with pytest.raises(RuntimeError, match="contains data; migration stopped"):
        Storage(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM library_source_revisions").fetchone() == (1,)


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


def test_project_service_lists_only_general_safe_summaries(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    service = ProjectService(storage)

    project = service.create_project("GxT Mastery")
    thread = service.create_project_thread(project.id, "Learn the model")
    summaries = service.project_summaries()

    assert summaries == [{
        "id": project.id,
        "name": "GxT Mastery",
        "status": "ACTIVE",
        "summary": {
            "objective": None,
            "experiment": None,
            "progress": None,
            "next_action": None,
            "unresolved_question": None,
        },
    }]
    assert service.project_thread(project.id, thread.id) == thread
    other = service.create_project("Other")
    with pytest.raises(ValueError, match="does not belong"):
        service.project_thread(other.id, thread.id)


def test_archiving_project_preserves_general_and_project_threads(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    service = ProjectService(storage)
    general_id = storage.create_thread("General")
    project = service.create_project("GxT Mastery")
    project_thread = service.create_project_thread(project.id, "Project chat")

    archived = service.update_project(project.id, status="ARCHIVED")

    assert archived.status is ProjectStatus.ARCHIVED
    assert storage.thread_context(general_id) is not None
    assert storage.thread_context(project_thread.id) is not None
    with pytest.raises(ValueError, match="archived"):
        service.create_project_thread(project.id, "Another")


def test_project_detail_and_source_toggle_are_project_local_and_persistent(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    service = ProjectService(storage)
    project = service.create_project("GxT")
    other = service.create_project("Other")
    garrett = storage.create_source_library(
        "gxt.garrett", "gxt", "Garrett", AuthorityKind.MENTOR, "Garrett — GxT"
    )
    storage.set_project_library(project.id, garrett.id, enabled=True)

    assert service.project_detail(project.id)["libraries"] == [{
        "library_key": "gxt.garrett", "display_name": "Garrett — GxT",
        "enabled": True, "source_count": 0, "index_status": "NONE",
    }]
    updated = service.set_library_enabled(project.id, "gxt.garrett", enabled=False)
    assert updated["enabled"] is False
    assert ProjectService(Storage(storage.database_path)).project_detail(project.id)["libraries"][0]["enabled"] is False
    with pytest.raises(ValueError, match="not available"):
        service.set_library_enabled(other.id, "gxt.garrett", enabled=True)


def test_archived_project_cannot_change_saved_source_settings(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    service = ProjectService(storage)
    project = service.create_project("GxT")
    garrett = storage.create_source_library(
        "gxt.garrett", "gxt", "Garrett", AuthorityKind.MENTOR, "Garrett — GxT"
    )
    storage.set_project_library(project.id, garrett.id, enabled=True)
    service.update_project(project.id, status="ARCHIVED")

    with pytest.raises(ValueError, match="archived"):
        service.set_library_enabled(project.id, "gxt.garrett", enabled=False)


def test_general_project_summaries_expose_no_private_project_detail(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    service = ProjectService(storage)
    project = service.create_project("GxT")
    thread = service.create_project_thread(project.id, "Research")
    service.apply_state_event(
        project.id, event_key="objective", kind="OBJECTIVE",
        payload={"operation": "SET", "value": "Build one tested model"},
        origin_thread_id=thread.id, origin_turn_number=1,
    )

    summary = service.general_summaries()[0]

    assert summary["summary"]["objective"] == "Build one tested model"
    assert not ({"mastery", "recent_research", "playbook", "libraries", "threads"} & summary.keys())
