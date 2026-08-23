import sqlite3

import pytest

from mentor.knowledge import Collection, Source, SourceRevision
from mentor.storage import Storage


def test_storage_registers_a_source_once(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")

    storage.register_source(
        relative_path="2025/lesson.txt",
        filename="lesson.txt",
        year=2025,
        local_path="C:/transcripts/2025/lesson.txt",
        modified_at=1_700_000_000.0,
        file_id="file_jacob",
        vector_store_file_id="vsf_jacob",
    )
    storage.register_source(
        relative_path="2025/lesson.txt",
        filename="lesson.txt",
        year=2025,
        local_path="C:/transcripts/2025/lesson.txt",
        modified_at=1_700_000_000.0,
        file_id="file_jacob",
        vector_store_file_id="vsf_jacob",
    )

    assert storage.vector_store_id() == "vs_jacob"
    assert storage.source_count() == 1
    assert storage.source_counts_by_year() == {2025: 1, 2026: 0}
    assert storage.source_for_file("file_jacob").modified_at == 1_700_000_000.0


def test_storage_uses_the_first_question_as_a_label_for_untitled_conversations(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("New conversation")
    storage.append_thread_items(
        thread_id,
        [{"role": "user", "content": [{"type": "input_text", "text": "  Explain   Jacob's exact strategy please.  "}]}],
    )

    assert storage.threads()[0].title == "Explain Jacob's exact strategy please."


def test_storage_omits_empty_untitled_drafts_from_the_conversation_list(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.create_thread("New conversation")
    titled_id = storage.create_thread("New conversation")
    storage.append_thread_items(
        titled_id,
        [{"role": "user", "content": [{"type": "input_text", "text": "A real question"}]}],
    )

    assert [thread.title for thread in storage.threads()] == ["A real question"]


def test_storage_backfills_safe_display_turns_without_changing_legacy_replay_items(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("New conversation")
    replay_items = [
        {"role": "user", "content": [{"type": "input_text", "text": "  What is SMT?  "}]},
        {"type": "reasoning", "encrypted_content": "private replay state"},
        {
            "type": "file_search_call",
            "queries": ["SMT"],
            "results": [
                {
                    "file_id": "file_smt",
                    "filename": "lesson.txt",
                    "text": "SMT teaching",
                    "attributes": {"year": "2026"},
                }
            ],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "# SMT\nA divergence.",
                    "annotations": [{"type": "file_citation", "file_id": "file_smt", "filename": "lesson.txt"}],
                }
            ],
        },
    ]
    storage.append_thread_items(thread_id, replay_items)
    storage.record_response_diagnostics(
        thread_id,
        "resp_legacy",
        {"response_id": "resp_legacy", "model": "gpt-5.6-sol", "reasoning_effort": "high"},
    )
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute("UPDATE threads SET title = 'New conversation' WHERE id = ?", (thread_id,))

    storage.initialize()

    turns = storage.display_turns(thread_id)
    assert storage.thread_items(thread_id) == replay_items
    assert storage.threads()[0].title == "What is SMT?"
    assert storage.thread(thread_id).title == "What is SMT?"
    assert turns == [
        {
            "turn_number": 1,
            "user_text": "What is SMT?",
            "answer_markdown": "# SMT\nA divergence.",
            "citations": [{"file_id": "file_smt", "filename": "lesson.txt"}],
            "evidence": [
                {
                    "file_id": "file_smt",
                    "filename": "lesson.txt",
                    "excerpt": "SMT teaching",
                    "year": "2026",
                    "metadata": {"year": "2026"},
                }
            ],
            "diagnostics": {"response_id": "resp_legacy", "model": "gpt-5.6-sol", "reasoning_effort": "high"},
            "response_id": "resp_legacy",
            "status": "completed",
            "incomplete_reason": None,
        }
    ]

    storage.initialize()
    assert storage.display_turns(thread_id) == turns
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute("UPDATE threads SET title = 'New conversation' WHERE id = ?", (thread_id,))
    storage.initialize()
    assert storage.thread(thread_id).title == "What is SMT?"


def test_storage_deletes_only_thread_owned_state_in_one_transaction(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    storage.register_source(
        relative_path="lesson.txt",
        filename="lesson.txt",
        year=2026,
        local_path="C:/transcripts/lesson.txt",
        modified_at=1.0,
        file_id="file_jacob",
        vector_store_file_id="vsf_jacob",
    )
    thread_id = storage.create_thread("Question")
    storage.append_thread_items(thread_id, [{"role": "user", "content": [{"type": "input_text", "text": "Question"}]}])
    storage.record_display_turn(
        thread_id,
        user_text="Question",
        answer_markdown="Answer",
        citations=[],
        evidence=[],
        diagnostics={"response_id": "resp_1"},
        response_id="resp_1",
        status="completed",
        incomplete_reason=None,
    )
    storage.record_response_diagnostics(thread_id, "resp_1", {"response_id": "resp_1"})
    storage.replace_replay_items(
        thread_id, [{"type": "compaction", "encrypted_content": "server-only replay state"}]
    )

    assert storage.delete_thread(thread_id) is True
    assert storage.delete_thread(thread_id) is False
    assert storage.has_thread(thread_id) is False
    assert storage.thread_items(thread_id) == []
    assert storage.replay_items(thread_id) == []
    assert storage.display_turns(thread_id) == []
    assert storage.response_diagnostics(thread_id) == []
    assert storage.vector_store_id() == "vs_jacob"
    assert storage.source_count() == 1


def test_storage_rolls_back_a_thread_delete_if_any_owned_row_cannot_be_removed(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Question")
    storage.append_thread_items(thread_id, [{"role": "user", "content": [{"type": "input_text", "text": "Question"}]}])
    storage.record_display_turn(
        thread_id,
        user_text="Question",
        answer_markdown="Answer",
        citations=[],
        evidence=[],
        diagnostics=None,
        response_id=None,
        status="completed",
        incomplete_reason=None,
    )
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER prevent_thread_item_delete
            BEFORE DELETE ON thread_items
            BEGIN SELECT RAISE(ABORT, 'test rollback'); END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="test rollback"):
        storage.delete_thread(thread_id)

    assert storage.has_thread(thread_id)
    assert storage.thread_items(thread_id)
    assert storage.display_turns(thread_id)


def test_storage_adds_library_tables_to_a_populated_legacy_database_without_changing_phase_two_state(tmp_path):
    database_path = tmp_path / "mentor.sqlite3"
    _create_populated_phase_two_database(database_path)
    storage = Storage(database_path)

    storage.initialize()

    assert storage.vector_store_id() == "vs_legacy"
    assert storage.source_for_file("file_legacy").relative_path == "2026/synthetic-lesson.txt"
    assert storage.thread_items(1)[0]["content"][0]["text"] == "Question"
    assert storage.replay_items(1) == [{"type": "reasoning", "encrypted_content": "legacy replay"}]
    assert storage.display_turns(1)[0]["citations"] == [
        {"file_id": "file_legacy", "filename": "synthetic-lesson.txt"}
    ]
    assert storage.response_diagnostics(1) == [{"response_id": "resp_legacy"}]
    with sqlite3.connect(storage.database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"collections", "library_sources", "source_revisions"} <= tables

    storage.initialize()
    with sqlite3.connect(storage.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM collections").fetchone()[0] == 0


def _create_populated_phase_two_database(database_path):
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE sources (
                relative_path TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                year INTEGER NOT NULL CHECK(year IN (2025, 2026)),
                local_path TEXT NOT NULL,
                modified_at REAL NOT NULL,
                file_id TEXT NOT NULL,
                vector_store_file_id TEXT NOT NULL
            );
            CREATE TABLE threads (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE thread_items (
                thread_id INTEGER NOT NULL REFERENCES threads(id),
                position INTEGER NOT NULL,
                item_json TEXT NOT NULL,
                PRIMARY KEY(thread_id, position)
            );
            CREATE TABLE thread_replay_items (
                thread_id INTEGER NOT NULL REFERENCES threads(id),
                position INTEGER NOT NULL,
                item_json TEXT NOT NULL,
                PRIMARY KEY(thread_id, position)
            );
            CREATE TABLE response_diagnostics (
                response_id TEXT PRIMARY KEY,
                thread_id INTEGER NOT NULL REFERENCES threads(id),
                diagnostic_json TEXT NOT NULL
            );
            CREATE TABLE display_turns (
                thread_id INTEGER NOT NULL REFERENCES threads(id),
                turn_number INTEGER NOT NULL,
                user_text TEXT NOT NULL,
                answer_markdown TEXT NOT NULL,
                citations_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                diagnostic_json TEXT,
                response_id TEXT,
                status TEXT NOT NULL,
                incomplete_reason TEXT,
                raw_start_position INTEGER,
                raw_end_position INTEGER,
                PRIMARY KEY(thread_id, turn_number)
            );
            """
        )
        connection.execute("INSERT INTO settings VALUES ('vector_store_id', 'vs_legacy')")
        connection.execute(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "2026/synthetic-lesson.txt",
                "synthetic-lesson.txt",
                2026,
                "C:/synthetic/2026/synthetic-lesson.txt",
                1_700_000_000.0,
                "file_legacy",
                "vsf_legacy",
            ),
        )
        connection.execute("INSERT INTO threads VALUES (1, 'Legacy question')")
        connection.execute(
            "INSERT INTO thread_items VALUES (1, 0, ?)",
            ('{"role":"user","content":[{"type":"input_text","text":"Question"}]}',),
        )
        connection.execute(
            "INSERT INTO thread_replay_items VALUES (1, 0, ?)",
            ('{"type":"reasoning","encrypted_content":"legacy replay"}',),
        )
        connection.execute(
            "INSERT INTO response_diagnostics VALUES ('resp_legacy', 1, ?)",
            ('{"response_id":"resp_legacy"}',),
        )
        connection.execute(
            "INSERT INTO display_turns VALUES (1, 1, 'Question', 'Answer', ?, '[]', ?, "
            "'resp_legacy', 'completed', NULL, 0, 0)",
            (
                '[{"file_id":"file_legacy","filename":"synthetic-lesson.txt"}]',
                '{"response_id":"resp_legacy"}',
            ),
        )


def test_storage_persists_an_immutable_library_source_revision_idempotently(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    collection = Collection("collection_synthetic", "Synthetic", "trading", True, "2026")
    source = Source.create(
        collection_id=collection.collection_id,
        identity_key="legacy:file_synthetic",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Synthetic lesson",
        year=2026,
        original_filename="synthetic.txt",
        local_provenance="C:/synthetic/synthetic.txt",
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256="b" * 64,
        byte_size=42,
        local_locator="C:/synthetic/synthetic.txt",
        observed_at=1_700_000_000.0,
        lifecycle_state="active",
    )

    storage.store_collection(collection)
    storage.store_source(source)
    storage.store_source_revision(revision)
    storage.store_source_revision(revision)

    assert storage.collection(collection.collection_id) == collection
    assert storage.library_source(source.source_id) == source
    assert storage.source_revision(revision.revision_id) == revision
    with sqlite3.connect(storage.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_revisions").fetchone()[0] == 1
