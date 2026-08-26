import sqlite3

import pytest

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


def test_storage_migrates_constrained_profile_items_without_changing_phase_two_data(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Question")
    raw_item = {"role": "user", "content": [{"type": "input_text", "text": "Question"}]}
    storage.append_thread_items(thread_id, [raw_item])
    storage.replace_replay_items(thread_id, [{"type": "reasoning", "encrypted_content": "private"}])
    storage.record_response_diagnostics(thread_id, "resp_1", {"response_id": "resp_1"})
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
    storage.initialize()
    storage.initialize()

    assert storage.thread_items(thread_id) == [raw_item]
    assert storage.replay_items(thread_id) == [{"type": "reasoning", "encrypted_content": "private"}]
    assert storage.response_diagnostics(thread_id) == [{"response_id": "resp_1"}]
    assert storage.display_turns(thread_id)[0]["answer_markdown"] == "Answer"
    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO trader_profile_items(
                    category, subject_key, subject, value, kind, provenance, state, origin_kind,
                    origin_thread_id, origin_turn_number, origin_available
                ) VALUES ('goals/research', 'goal', 'Goal', 'x', 'unknown', 'USER_STATED',
                          'confirmed', 'profile-editor', NULL, NULL, 0)
                """
                )


def test_storage_rejects_profile_values_over_the_storage_bound(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()

    with pytest.raises(sqlite3.IntegrityError):
        storage.create_profile_item(
            category="goals/research",
            subject="Goal",
            value="x" * 501,
            kind="goal",
            provenance="USER_STATED",
            state="confirmed",
            origin_kind="profile-editor",
        )


def test_storage_versions_confirmed_profile_items_and_selects_only_current_records(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    original = storage.create_profile_item(
        category="schedule/horizon",
        subject="Holding Period",
        value="I hold trades for two to five days.",
        kind="preference",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )

    assert original.subject_key == "holding period"
    assert storage.current_confirmed_profile_items() == [original]
    with pytest.raises(sqlite3.IntegrityError):
        storage.create_profile_item(
            category="schedule/horizon",
            subject=" holding   period ",
            value="A second active preference.",
            kind="preference",
            provenance="USER_STATED",
            state="confirmed",
            origin_kind="profile-editor",
        )

    replacement = storage.supersede_profile_item(
        original.id,
        value="I now day trade only.",
        provenance="USER_DECISION",
        origin_kind="confirmation",
    )

    assert storage.profile_item(original.id).state == "superseded"
    assert replacement.state == "confirmed"
    assert replacement.supersedes_item_id == original.id
    assert storage.current_confirmed_profile_items() == [replacement]


def test_storage_archives_conflicts_and_permanently_deletes_profile_items(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    confirmed = storage.create_profile_item(
        category="markets/instruments",
        subject="Market",
        value="I trade ES.",
        kind="fact",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    first = storage.create_profile_item(
        category="style/methodology",
        subject="Entry style",
        value="I prefer breakouts.",
        kind="preference",
        provenance="AI_INFERRED",
        state="tentative",
        origin_kind="chat",
    )
    second = storage.create_profile_item(
        category="style/methodology",
        subject="Entry style",
        value="I prefer mean reversion.",
        kind="preference",
        provenance="AI_INFERRED",
        state="tentative",
        origin_kind="chat",
    )

    storage.archive_profile_item(confirmed.id)
    storage.conflict_profile_items([first.id, second.id])
    assert storage.current_confirmed_profile_items() == []
    assert [storage.profile_item(item_id).state for item_id in (first.id, second.id)] == [
        "conflicting",
        "conflicting",
    ]

    assert storage.delete_profile_item(first.id) is True
    assert storage.profile_item(first.id) is None


def test_storage_thread_deletion_keeps_profile_record_and_marks_its_origin_unavailable(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Question")
    item = storage.create_profile_item(
        category="goals/research",
        subject="Learning goal",
        value="I am studying Jacob's material.",
        kind="goal",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="chat",
        origin_thread_id=thread_id,
        origin_turn_number=1,
    )

    assert storage.delete_thread(thread_id) is True
    retained = storage.profile_item(item.id)
    assert retained.origin_thread_id == thread_id
    assert retained.origin_turn_number == 1
    assert retained.origin_available is False


def test_storage_migrates_a_phase_two_database_without_losing_existing_data(tmp_path):
    database_path = tmp_path / "mentor.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE sources (
                relative_path TEXT PRIMARY KEY, filename TEXT NOT NULL, year INTEGER NOT NULL,
                local_path TEXT NOT NULL, modified_at REAL NOT NULL, file_id TEXT NOT NULL,
                vector_store_file_id TEXT NOT NULL
            );
            CREATE TABLE threads (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE thread_items (
                thread_id INTEGER NOT NULL REFERENCES threads(id), position INTEGER NOT NULL,
                item_json TEXT NOT NULL, PRIMARY KEY(thread_id, position)
            );
            CREATE TABLE thread_replay_items (
                thread_id INTEGER NOT NULL REFERENCES threads(id), position INTEGER NOT NULL,
                item_json TEXT NOT NULL, PRIMARY KEY(thread_id, position)
            );
            CREATE TABLE response_diagnostics (
                response_id TEXT PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES threads(id),
                diagnostic_json TEXT NOT NULL
            );
            CREATE TABLE display_turns (
                thread_id INTEGER NOT NULL REFERENCES threads(id), turn_number INTEGER NOT NULL,
                user_text TEXT NOT NULL, answer_markdown TEXT NOT NULL, citations_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL, diagnostic_json TEXT, response_id TEXT, status TEXT NOT NULL,
                incomplete_reason TEXT, raw_start_position INTEGER, raw_end_position INTEGER,
                PRIMARY KEY(thread_id, turn_number)
            );
            INSERT INTO settings VALUES ('vector_store_id', 'vs_phase_two');
            INSERT INTO sources VALUES ('2026/lesson.txt', 'lesson.txt', 2026, 'C:/lesson.txt', 1.0,
                                        'file_phase_two', 'vsf_phase_two');
            INSERT INTO threads VALUES (7, 'Phase 2 question');
            INSERT INTO thread_items VALUES (7, 0, '{"role": "user", "content": []}');
            INSERT INTO thread_replay_items VALUES (7, 0, '{"type": "reasoning", "encrypted_content": "private"}');
            INSERT INTO response_diagnostics VALUES ('resp_phase_two', 7, '{"response_id": "resp_phase_two"}');
            INSERT INTO display_turns VALUES (7, 1, 'Question', 'Answer', '[]', '[]', NULL,
                                              'resp_phase_two', 'completed', NULL, 0, 0);
            """
        )

    storage = Storage(database_path)
    storage.initialize()

    assert storage.vector_store_id() == "vs_phase_two"
    assert storage.source_for_file("file_phase_two").filename == "lesson.txt"
    assert storage.thread(7).title == "Phase 2 question"
    assert storage.thread_items(7) == [{"role": "user", "content": []}]
    assert storage.replay_items(7) == [{"type": "reasoning", "encrypted_content": "private"}]
    assert storage.response_diagnostics(7) == [{"response_id": "resp_phase_two"}]
    assert storage.display_turns(7)[0]["answer_markdown"] == "Answer"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trader_profile_items'"
        ).fetchone() == (1,)


def test_storage_rolls_back_a_failed_profile_supersession(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    original = storage.create_profile_item(
        category="schedule/horizon",
        subject="Holding period",
        value="I hold for days.",
        kind="preference",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER prevent_profile_successor
            BEFORE INSERT ON trader_profile_items
            WHEN NEW.supersedes_item_id IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'test supersession rollback'); END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="test supersession rollback"):
        storage.supersede_profile_item(
            original.id,
            value="I now day trade.",
            provenance="USER_DECISION",
            origin_kind="confirmation",
        )

    assert storage.profile_item(original.id).state == "confirmed"
    assert storage.current_confirmed_profile_items() == [original]


def test_storage_rolls_back_thread_deletion_after_profile_origin_update_fails(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Question")
    storage.append_thread_items(
        thread_id, [{"role": "user", "content": [{"type": "input_text", "text": "Question"}]}]
    )
    item = storage.create_profile_item(
        category="goals/research",
        subject="Learning goal",
        value="I am studying Jacob's material.",
        kind="goal",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="chat",
        origin_thread_id=thread_id,
        origin_turn_number=1,
    )
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER prevent_thread_item_delete_after_origin_update
            BEFORE DELETE ON thread_items
            BEGIN SELECT RAISE(ABORT, 'test thread rollback'); END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="test thread rollback"):
        storage.delete_thread(thread_id)

    assert storage.profile_item(item.id).origin_available is True
    assert storage.has_thread(thread_id)
    assert storage.thread_items(thread_id)


def test_thread_deletion_removes_thread_owned_profile_tool_operations_only(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Forget profile item")
    target = storage.create_profile_item(
        category="schedule/horizon",
        subject="Holding period",
        value="I hold for days.",
        kind="preference",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    global_item = storage.create_profile_item(
        category="markets/instruments",
        subject="Primary market",
        value="ES",
        kind="fact",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    assert storage.apply_profile_forget_operation(
        tool_call_id="call_forget",
        operation="archive",
        target_item_id=target.id,
        origin_thread_id=thread_id,
        origin_turn_number=1,
    ) == "archived"

    assert storage.delete_thread(thread_id) is True

    with sqlite3.connect(storage.database_path) as connection:
        assert connection.execute("SELECT * FROM profile_tool_operations").fetchall() == []
    assert storage.profile_item(target.id).state == "archived"
    assert storage.current_confirmed_profile_items() == [global_item]


def test_storage_migrates_existing_profile_table_before_creating_tool_call_index(tmp_path):
    database_path = tmp_path / "mentor.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE trader_profile_items (
                id INTEGER PRIMARY KEY,
                category TEXT NOT NULL,
                subject_key TEXT NOT NULL,
                subject TEXT NOT NULL,
                value TEXT NOT NULL,
                kind TEXT NOT NULL,
                provenance TEXT NOT NULL,
                state TEXT NOT NULL,
                origin_kind TEXT NOT NULL,
                origin_thread_id INTEGER,
                origin_turn_number INTEGER,
                origin_available INTEGER NOT NULL,
                supersedes_item_id INTEGER
            )
            """
        )

    storage = Storage(database_path)
    storage.initialize()
    first = storage.create_profile_item(
        category="markets/instruments",
        subject="Primary market",
        value="ES",
        kind="fact",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
        tool_call_id="call_profile",
    )
    second = storage.create_profile_item(
        category="markets/instruments",
        subject="Other market",
        value="NQ",
        kind="fact",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
        tool_call_id="call_profile",
    )

    assert storage.profile_item_for_tool_call("call_profile") == first
    assert second == first
