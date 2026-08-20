"""Small SQLite store for private Phase 1 state."""

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
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sources)")}
            if "modified_at" not in columns:
                connection.execute("ALTER TABLE sources ADD COLUMN modified_at REAL")

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
                "SELECT id, title FROM threads ORDER BY id DESC"
            ).fetchall()
        return [Thread(*row) for row in rows]

    def has_thread(self, thread_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
        return row is not None

    def append_thread_items(self, thread_id: int, items: list[dict]) -> None:
        if not items:
            return
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

    def thread_items(self, thread_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT item_json FROM thread_items WHERE thread_id = ? ORDER BY position",
                (thread_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)
