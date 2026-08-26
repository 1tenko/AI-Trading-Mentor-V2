"""Small SQLite store for private Trading Mentor state."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Source:
    relative_path: str
    filename: str
    year: int
    local_path: str
    modified_at: float
    file_id: str


@dataclass(frozen=True)
class Thread:
    id: int
    title: str


@dataclass(frozen=True)
class TraderProfileItem:
    id: int
    category: str
    subject_key: str
    subject: str
    value: str
    kind: str
    provenance: str
    state: str
    origin_kind: str
    origin_thread_id: int | None
    origin_turn_number: int | None
    origin_available: bool
    supersedes_item_id: int | None


class Storage:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    relative_path TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    year INTEGER NOT NULL CHECK(year IN (2025, 2026)),
                    local_path TEXT NOT NULL,
                    modified_at REAL NOT NULL,
                    file_id TEXT NOT NULL,
                    vector_store_file_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS threads (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thread_items (
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    position INTEGER NOT NULL,
                    item_json TEXT NOT NULL,
                    PRIMARY KEY(thread_id, position)
                );
                CREATE TABLE IF NOT EXISTS thread_replay_items (
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    position INTEGER NOT NULL,
                    item_json TEXT NOT NULL,
                    PRIMARY KEY(thread_id, position)
                );
                CREATE TABLE IF NOT EXISTS response_diagnostics (
                    response_id TEXT PRIMARY KEY,
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    diagnostic_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS display_turns (
                    thread_id INTEGER NOT NULL REFERENCES threads(id),
                    turn_number INTEGER NOT NULL,
                    user_text TEXT NOT NULL,
                    answer_markdown TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    diagnostic_json TEXT,
                    profile_update_json TEXT,
                    response_id TEXT,
                    status TEXT NOT NULL,
                    incomplete_reason TEXT,
                    raw_start_position INTEGER,
                    raw_end_position INTEGER,
                    PRIMARY KEY(thread_id, turn_number)
                );
                CREATE TABLE IF NOT EXISTS trader_profile_items (
                    id INTEGER PRIMARY KEY,
                    category TEXT NOT NULL CHECK(category IN (
                        'goals/research', 'markets/instruments', 'schedule/horizon',
                        'style/methodology', 'execution/risk/constraints', 'experience/learning',
                        'preferences/discretion', 'strengths/difficulties/principles'
                    )),
                    subject_key TEXT NOT NULL CHECK(
                        length(subject_key) BETWEEN 1 AND 120
                        AND subject_key = lower(trim(subject_key))
                        AND instr(subject_key, '  ') = 0
                    ),
                    subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 120),
                    value TEXT NOT NULL CHECK(length(value) BETWEEN 1 AND 500),
                    kind TEXT NOT NULL CHECK(kind IN (
                        'fact', 'preference', 'constraint', 'goal', 'principle', 'learning-state'
                    )),
                    provenance TEXT NOT NULL CHECK(provenance IN (
                        'USER_STATED', 'USER_CONFIRMED', 'AI_INFERRED', 'USER_DECISION'
                    )),
                    state TEXT NOT NULL CHECK(state IN (
                        'confirmed', 'tentative', 'superseded', 'conflicting', 'archived'
                    )),
                    origin_kind TEXT NOT NULL CHECK(origin_kind IN ('chat', 'profile-editor', 'confirmation')),
                    origin_thread_id INTEGER,
                    origin_turn_number INTEGER,
                    origin_available INTEGER NOT NULL CHECK(origin_available IN (0, 1)),
                    supersedes_item_id INTEGER REFERENCES trader_profile_items(id) ON DELETE SET NULL,
                    tool_call_id TEXT,
                    CHECK(
                        (origin_thread_id IS NULL AND origin_turn_number IS NULL AND origin_available = 0)
                        OR (origin_thread_id IS NOT NULL AND origin_turn_number > 0)
                    )
                );
                CREATE UNIQUE INDEX IF NOT EXISTS unique_current_profile_subject
                    ON trader_profile_items(category, subject_key) WHERE state = 'confirmed';
                CREATE INDEX IF NOT EXISTS profile_origin_thread
                    ON trader_profile_items(origin_thread_id);
                CREATE TABLE IF NOT EXISTS profile_tool_operations (
                    tool_call_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL CHECK(operation IN ('archive', 'delete')),
                    target_item_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('archived', 'deleted')),
                    origin_thread_id INTEGER NOT NULL,
                    origin_turn_number INTEGER NOT NULL
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sources)")}
            if "modified_at" not in columns:
                connection.execute("ALTER TABLE sources ADD COLUMN modified_at REAL")
            profile_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(trader_profile_items)")
            }
            if "tool_call_id" not in profile_columns:
                connection.execute("ALTER TABLE trader_profile_items ADD COLUMN tool_call_id TEXT")
            display_turn_columns = {row[1] for row in connection.execute("PRAGMA table_info(display_turns)")}
            if "profile_update_json" not in display_turn_columns:
                connection.execute("ALTER TABLE display_turns ADD COLUMN profile_update_json TEXT")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS unique_profile_tool_call "
                "ON trader_profile_items(tool_call_id) WHERE tool_call_id IS NOT NULL"
            )
            self._backfill_display_turns(connection)

    def set_vector_store(self, vector_store_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES ('vector_store_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (vector_store_id,),
            )

    def vector_store_id(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'vector_store_id'"
            ).fetchone()
        return None if row is None else str(row[0])

    def register_source(
        self,
        *,
        relative_path: str,
        filename: str,
        year: int,
        local_path: str,
        modified_at: float,
        file_id: str,
        vector_store_file_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sources(
                    relative_path, filename, year, local_path, modified_at, file_id, vector_store_file_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO NOTHING
                """,
                (
                    relative_path,
                    filename,
                    year,
                    local_path,
                    modified_at,
                    file_id,
                    vector_store_file_id,
                ),
            )

    def has_source(self, relative_path: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sources WHERE relative_path = ?", (relative_path,)
            ).fetchone()
        return row is not None

    def source_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM sources").fetchone()
        return int(row[0])

    def source_counts_by_year(self) -> dict[int, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT year, COUNT(*) FROM sources GROUP BY year"
            ).fetchall()
        counts = dict(rows)
        return {year: counts.get(year, 0) for year in (2025, 2026)}

    def source_for_file(self, file_id: str) -> Source | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT relative_path, filename, year, local_path, modified_at, file_id "
                "FROM sources WHERE file_id = ?",
                (file_id,),
            ).fetchone()
        return None if row is None else Source(*row)

    def update_source_modified_at(self, relative_path: str, modified_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sources SET modified_at = ? WHERE relative_path = ?",
                (modified_at, relative_path),
            )

    def create_thread(self, title: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute("INSERT INTO threads(title) VALUES (?)", (title,))
        return int(cursor.lastrowid)

    def threads(self) -> list[Thread]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT threads.id, threads.title, ("
                "SELECT item_json FROM thread_items "
                "WHERE thread_id = threads.id ORDER BY position LIMIT 1"
                ") FROM threads ORDER BY threads.id DESC"
            ).fetchall()
        return [
            Thread(row[0], label)
            for row in rows
            if (label := _thread_label(row[1], row[2])) != "New conversation"
        ]

    def has_thread(self, thread_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
        return row is not None

    def thread(self, thread_id: int) -> Thread | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, title FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
        return None if row is None else Thread(*row)

    def append_thread_items(self, thread_id: int, items: list[dict]) -> tuple[int, int] | None:
        if not items:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) FROM thread_items WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            start = int(row[0]) + 1
            connection.executemany(
                "INSERT INTO thread_items(thread_id, position, item_json) VALUES (?, ?, ?)",
                [
                    (thread_id, start + index, json.dumps(item))
                    for index, item in enumerate(items)
                ],
            )
            title = _user_text(items[0])
            if title:
                connection.execute(
                    "UPDATE threads SET title = ? WHERE id = ? AND title = 'New conversation'",
                    (_compact_title(title), thread_id),
                )
        return start, start + len(items) - 1

    def thread_items(self, thread_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT item_json FROM thread_items WHERE thread_id = ? ORDER BY position",
                (thread_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def replay_items(self, thread_id: int) -> list[dict]:
        """Return the model-only replay state, falling back to complete raw history."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT item_json FROM thread_replay_items WHERE thread_id = ? ORDER BY position",
                (thread_id,),
            ).fetchall()
        return self.thread_items(thread_id) if not rows else [json.loads(row[0]) for row in rows]

    def replace_replay_items(self, thread_id: int, items: list[dict]) -> None:
        """Atomically replace only the server-owned model replay state."""
        with self._connect() as connection:
            connection.execute("DELETE FROM thread_replay_items WHERE thread_id = ?", (thread_id,))
            connection.executemany(
                "INSERT INTO thread_replay_items(thread_id, position, item_json) VALUES (?, ?, ?)",
                [(thread_id, position, json.dumps(item)) for position, item in enumerate(items)],
            )

    def append_replay_items(self, thread_id: int, items: list[dict]) -> None:
        if not items:
            return
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM thread_replay_items WHERE thread_id = ? LIMIT 1", (thread_id,)
            ).fetchone()
            if exists is None:
                return
            row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) FROM thread_replay_items WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            start = int(row[0]) + 1
            connection.executemany(
                "INSERT INTO thread_replay_items(thread_id, position, item_json) VALUES (?, ?, ?)",
                [(thread_id, start + index, json.dumps(item)) for index, item in enumerate(items)],
            )

    def record_response_diagnostics(
        self, thread_id: int, response_id: str, diagnostic: dict
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO response_diagnostics(response_id, thread_id, diagnostic_json) "
                "VALUES (?, ?, ?)",
                (response_id, thread_id, json.dumps(diagnostic)),
            )

    def response_diagnostics(self, thread_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT diagnostic_json FROM response_diagnostics WHERE thread_id = ? ORDER BY rowid",
                (thread_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def record_display_turn(
        self,
        thread_id: int,
        *,
        user_text: str,
        answer_markdown: str,
        citations: list[dict],
        evidence: list[dict],
        diagnostics: dict | None,
        response_id: str | None,
        status: str,
        incomplete_reason: str | None,
        profile_update: dict[str, str] | None = None,
        raw_start_position: int | None = None,
        raw_end_position: int | None = None,
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(turn_number), 0) FROM display_turns WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO display_turns(
                    thread_id, turn_number, user_text, answer_markdown,
                    citations_json, evidence_json, diagnostic_json, profile_update_json, response_id,
                    status, incomplete_reason, raw_start_position, raw_end_position
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    int(row[0]) + 1,
                    user_text,
                    answer_markdown,
                    json.dumps(citations),
                    json.dumps(evidence),
                    None if diagnostics is None else json.dumps(diagnostics),
                    None if profile_update is None else json.dumps(profile_update),
                    response_id,
                    status,
                    incomplete_reason,
                    raw_start_position,
                    raw_end_position,
                ),
            )

    def display_turns(self, thread_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT turn_number, user_text, answer_markdown, citations_json,
                       evidence_json, diagnostic_json, profile_update_json, response_id, status,
                       incomplete_reason
                FROM display_turns WHERE thread_id = ? ORDER BY turn_number
                """,
                (thread_id,),
            ).fetchall()
        return [
            {
                "turn_number": row[0],
                "user_text": row[1],
                "answer_markdown": row[2],
                "citations": json.loads(row[3]),
                "evidence": json.loads(row[4]),
                "diagnostics": None if row[5] is None else json.loads(row[5]),
                "response_id": row[7],
                "status": row[8],
                "incomplete_reason": row[9],
                **({"profile_update": json.loads(row[6])} if row[6] is not None else {}),
            }
            for row in rows
        ]

    def create_profile_item(
        self,
        *,
        category: str,
        subject: str,
        value: str,
        kind: str,
        provenance: str,
        state: str,
        origin_kind: str,
        origin_thread_id: int | None = None,
        origin_turn_number: int | None = None,
        origin_available: bool | None = None,
        supersedes_item_id: int | None = None,
        tool_call_id: str | None = None,
    ) -> TraderProfileItem:
        with self._connect() as connection:
            if tool_call_id is not None:
                row = connection.execute(
                    "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                    "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                    "supersedes_item_id FROM trader_profile_items WHERE tool_call_id = ?",
                    (tool_call_id,),
                ).fetchone()
                if row is not None:
                    return _profile_item_from_row(row)
            return self._insert_profile_item(
                connection,
                category=category,
                subject=subject,
                value=value,
                kind=kind,
                provenance=provenance,
                state=state,
                origin_kind=origin_kind,
                origin_thread_id=origin_thread_id,
                origin_turn_number=origin_turn_number,
                origin_available=origin_available,
                supersedes_item_id=supersedes_item_id,
                tool_call_id=tool_call_id,
            )

    def profile_item(self, item_id: int) -> TraderProfileItem | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items WHERE id = ?",
                (item_id,),
            ).fetchone()
        return None if row is None else _profile_item_from_row(row)

    def profile_item_for_tool_call(self, tool_call_id: str) -> TraderProfileItem | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
        return None if row is None else _profile_item_from_row(row)

    def current_confirmed_profile_items(self) -> list[TraderProfileItem]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items WHERE state = 'confirmed' "
                "ORDER BY category, subject_key, id"
            ).fetchall()
        return [_profile_item_from_row(row) for row in rows]

    def profile_items(self) -> list[TraderProfileItem]:
        """Return local profile records for the browser-safe profile projection."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items ORDER BY id"
            ).fetchall()
        return [_profile_item_from_row(row) for row in rows]

    def save_questionnaire_answers(self, changes) -> dict[str, TraderProfileItem]:
        """Apply validated fixed questionnaire fields as one local transaction."""
        saved: dict[str, TraderProfileItem] = {}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for field, value in changes:
                subject_key = _profile_subject_key(field.subject)
                row = connection.execute(
                    "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                    "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                    "supersedes_item_id FROM trader_profile_items "
                    "WHERE category = ? AND subject_key = ? AND state = 'confirmed'",
                    (field.category, subject_key),
                ).fetchone()
                current = None if row is None else _profile_item_from_row(row)
                if not value:
                    if current is not None:
                        connection.execute("UPDATE trader_profile_items SET state = 'archived' WHERE id = ?", (current.id,))
                    continue
                if current is not None and current.value == value:
                    saved[field.key] = current
                    continue
                if current is not None:
                    connection.execute("UPDATE trader_profile_items SET state = 'superseded' WHERE id = ?", (current.id,))
                saved[field.key] = self._insert_profile_item(
                    connection,
                    category=field.category,
                    subject=field.subject,
                    value=value,
                    kind=field.kind,
                    provenance="USER_STATED",
                    state="confirmed",
                    origin_kind="profile-editor",
                    origin_thread_id=None,
                    origin_turn_number=None,
                    origin_available=None,
                    supersedes_item_id=None if current is None else current.id,
                    tool_call_id=None,
                )
        return saved

    def supersede_profile_item(
        self,
        item_id: int,
        *,
        value: str,
        provenance: str,
        origin_kind: str,
        subject: str | None = None,
        kind: str | None = None,
        origin_thread_id: int | None = None,
        origin_turn_number: int | None = None,
        origin_available: bool | None = None,
    ) -> TraderProfileItem:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
                "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
                "supersedes_item_id FROM trader_profile_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            predecessor = _profile_item_from_row(row)
            connection.execute(
                "UPDATE trader_profile_items SET state = 'superseded' WHERE id = ?", (item_id,)
            )
            return self._insert_profile_item(
                connection,
                category=predecessor.category,
                subject=predecessor.subject if subject is None else subject,
                value=value,
                kind=predecessor.kind if kind is None else kind,
                provenance=provenance,
                state="confirmed",
                origin_kind=origin_kind,
                origin_thread_id=origin_thread_id,
                origin_turn_number=origin_turn_number,
                origin_available=origin_available,
                supersedes_item_id=item_id,
                tool_call_id=None,
            )

    def archive_profile_item(self, item_id: int) -> bool:
        return self._set_profile_state(item_id, "archived")

    def conflict_profile_items(self, item_ids: list[int]) -> int:
        if len(item_ids) < 2:
            raise ValueError("a conflict requires at least two distinct profile items")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ", ".join("?" for _ in item_ids)
            rows = connection.execute(
                "SELECT id, category, subject_key, state FROM trader_profile_items "
                f"WHERE id IN ({placeholders})",
                item_ids,
            ).fetchall()
            if len(rows) != len(item_ids):
                raise ValueError("all conflicting profile items must exist and be distinct")
            category, subject_key = rows[0][1:3]
            if any(
                row[3] not in ("confirmed", "tentative")
                or row[1] != category
                or row[2] != subject_key
                for row in rows
            ):
                raise ValueError(
                    "conflicting profile items must be current or tentative with the same category and subject"
                )
            cursor = connection.execute(
                f"UPDATE trader_profile_items SET state = 'conflicting' WHERE id IN ({placeholders})",
                item_ids,
            )
        return cursor.rowcount

    def delete_profile_item(self, item_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM trader_profile_items WHERE id = ?", (item_id,))
        return cursor.rowcount == 1

    def apply_profile_forget_operation(
        self,
        *,
        tool_call_id: str,
        operation: str,
        target_item_id: int,
        origin_thread_id: int,
        origin_turn_number: int,
    ) -> str:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT operation, target_item_id, status FROM profile_tool_operations WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != operation or existing[1] != target_item_id:
                    raise ValueError("tool call id cannot target a different profile operation")
                return str(existing[2])
            row = connection.execute(
                "SELECT state FROM trader_profile_items WHERE id = ?", (target_item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(target_item_id)
            if operation == "archive":
                if row[0] != "confirmed":
                    raise ValueError("only a confirmed profile item can be archived by chat")
                connection.execute(
                    "UPDATE trader_profile_items SET state = 'archived' WHERE id = ?", (target_item_id,)
                )
                status = "archived"
            elif operation == "delete":
                connection.execute("DELETE FROM trader_profile_items WHERE id = ?", (target_item_id,))
                status = "deleted"
            else:
                raise ValueError("unsupported profile operation")
            connection.execute(
                "INSERT INTO profile_tool_operations(\n"
                "tool_call_id, operation, target_item_id, status, origin_thread_id, origin_turn_number\n"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (tool_call_id, operation, target_item_id, status, origin_thread_id, origin_turn_number),
            )
            return status

    def profile_operation_status(self, tool_call_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM profile_tool_operations WHERE tool_call_id = ?", (tool_call_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    def profile_mutation_exists_for_origin(self, thread_id: int, turn_number: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM trader_profile_items WHERE origin_thread_id = ? AND origin_turn_number = ? "
                "UNION SELECT 1 FROM profile_tool_operations WHERE origin_thread_id = ? AND origin_turn_number = ? "
                "LIMIT 1",
                (thread_id, turn_number, thread_id, turn_number),
            ).fetchone()
        return row is not None

    def delete_thread(self, thread_id: int) -> bool:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if exists is None:
                return False
            connection.execute(
                "UPDATE trader_profile_items SET origin_available = 0 WHERE origin_thread_id = ?",
                (thread_id,),
            )
            connection.execute(
                "DELETE FROM profile_tool_operations WHERE origin_thread_id = ?", (thread_id,)
            )
            connection.execute("DELETE FROM display_turns WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM response_diagnostics WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM thread_replay_items WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM thread_items WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        return True

    def _set_profile_state(self, item_id: int, state: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE trader_profile_items SET state = ? WHERE id = ?", (state, item_id)
            )
        return cursor.rowcount == 1

    def _insert_profile_item(
        self,
        connection: sqlite3.Connection,
        *,
        category: str,
        subject: str,
        value: str,
        kind: str,
        provenance: str,
        state: str,
        origin_kind: str,
        origin_thread_id: int | None,
        origin_turn_number: int | None,
        origin_available: bool | None,
        supersedes_item_id: int | None,
        tool_call_id: str | None,
    ) -> TraderProfileItem:
        subject = " ".join(subject.split())
        value = value.strip()
        cursor = connection.execute(
            """
            INSERT INTO trader_profile_items(
                category, subject_key, subject, value, kind, provenance, state, origin_kind,
                origin_thread_id, origin_turn_number, origin_available, supersedes_item_id, tool_call_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                category,
                _profile_subject_key(subject),
                subject,
                value,
                kind,
                provenance,
                state,
                origin_kind,
                origin_thread_id,
                origin_turn_number,
                int(origin_thread_id is not None if origin_available is None else origin_available),
                supersedes_item_id,
                tool_call_id,
            ),
        )
        row = connection.execute(
            "SELECT id, category, subject_key, subject, value, kind, provenance, state, "
            "origin_kind, origin_thread_id, origin_turn_number, origin_available, "
            "supersedes_item_id FROM trader_profile_items WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return _profile_item_from_row(row)

    def _backfill_display_turns(self, connection: sqlite3.Connection) -> None:
        thread_ids = connection.execute("SELECT id FROM threads").fetchall()
        for (thread_id,) in thread_ids:
            items = [
                (row[0], json.loads(row[1]))
                for row in connection.execute(
                    "SELECT position, item_json FROM thread_items WHERE thread_id = ? ORDER BY position",
                    (thread_id,),
                )
            ]
            first_user = next((item for _, item in items if _user_text(item)), None)
            if first_user is not None:
                connection.execute(
                    "UPDATE threads SET title = ? WHERE id = ? AND title = 'New conversation'",
                    (_compact_title(_user_text(first_user) or ""), thread_id),
                )
            existing = connection.execute(
                "SELECT 1 FROM display_turns WHERE thread_id = ? LIMIT 1", (thread_id,)
            ).fetchone()
            if existing is not None:
                continue
            diagnostics = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT diagnostic_json FROM response_diagnostics WHERE thread_id = ? ORDER BY rowid",
                    (thread_id,),
                )
            ]
            diagnostic_index = 0
            starts = [index for index, (_, item) in enumerate(items) if _user_text(item) is not None]
            for turn_number, start_index in enumerate(starts, start=1):
                end_index = starts[turn_number] if turn_number < len(starts) else len(items)
                raw_items = [item for _, item in items[start_index:end_index]]
                user_text = _user_text(raw_items[0]) or ""
                answer_markdown, citations, evidence = _display_content(raw_items[1:])
                diagnostic = diagnostics[diagnostic_index] if answer_markdown and diagnostic_index < len(diagnostics) else None
                if diagnostic is not None:
                    diagnostic_index += 1
                status = str((diagnostic or {}).get("status") or ("completed" if answer_markdown else "incomplete"))
                response_id = (diagnostic or {}).get("response_id")
                connection.execute(
                    """
                    INSERT INTO display_turns(
                        thread_id, turn_number, user_text, answer_markdown,
                        citations_json, evidence_json, diagnostic_json, response_id,
                        status, incomplete_reason, raw_start_position, raw_end_position
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        turn_number,
                        user_text.strip(),
                        answer_markdown,
                        json.dumps(citations),
                        json.dumps(evidence),
                        None if diagnostic is None else json.dumps(diagnostic),
                        response_id,
                        status,
                        (diagnostic or {}).get("incomplete_reason"),
                        items[start_index][0],
                        items[end_index - 1][0],
                    ),
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _thread_label(title: str, first_item_json: str | None) -> str:
    if title != "New conversation" or not first_item_json:
        return title
    try:
        item = json.loads(first_item_json)
        text = item["content"][0]["text"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return title
    compact = " ".join(str(text).split())
    return f"{compact[:55]}…" if len(compact) > 56 else compact or title


def _user_text(item: dict) -> str | None:
    if item.get("role") != "user":
        return None
    for content in item.get("content") or []:
        if content.get("type") == "input_text" and isinstance(content.get("text"), str):
            return content["text"]
    return None


def _compact_title(text: str) -> str:
    compact = " ".join(text.split())
    return f"{compact[:55]}…" if len(compact) > 56 else compact


def _profile_subject_key(subject: str) -> str:
    return " ".join(subject.split()).casefold()


def _profile_item_from_row(row: tuple) -> TraderProfileItem:
    return TraderProfileItem(*row[:11], bool(row[11]), row[12])


def _display_content(items: list[dict]) -> tuple[str, list[dict], list[dict]]:
    text_parts: list[str] = []
    citations: list[dict] = []
    evidence: list[dict] = []
    for item in items:
        if item.get("type") == "file_search_call":
            for result in item.get("results") or []:
                attributes = result.get("attributes") or {}
                evidence.append(
                    {
                        "file_id": result["file_id"],
                        "filename": result.get("filename", "Unknown source"),
                        "excerpt": result.get("text", ""),
                        "year": attributes.get("year"),
                        "metadata": {str(key): str(value) for key, value in attributes.items()},
                    }
                )
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        for content in item.get("content") or []:
            if content.get("type") != "output_text":
                continue
            text_parts.append(content.get("text", ""))
            for annotation in content.get("annotations") or []:
                if annotation.get("type") == "file_citation":
                    citation = {
                        "file_id": annotation["file_id"],
                        "filename": annotation.get("filename", "Unknown source"),
                    }
                    if citation not in citations:
                        citations.append(citation)
    return "".join(text_parts), citations, evidence
