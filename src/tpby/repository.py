from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from .domain import GroupKind, GroupSnapshot, MediaInput, SourceTask, StoredTask

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    deal_media_hash TEXT NOT NULL,
    code_chat_id INTEGER,
    code_message_id INTEGER,
    disposition TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code_chat_id, code_message_id)
);
CREATE TABLE IF NOT EXISTS media (
    sha256 TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    mime_type TEXT
);
CREATE TABLE IF NOT EXISTS task_media (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    sha256 TEXT NOT NULL REFERENCES media(sha256),
    source_message_id INTEGER,
    position INTEGER NOT NULL,
    PRIMARY KEY(task_id, sha256)
);
CREATE INDEX IF NOT EXISTS task_media_hash_idx ON task_media(sha256);

CREATE TABLE IF NOT EXISTS groups_ (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('DEAL1', 'DEAL2', 'UP')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS group_tasks (
    group_id INTEGER NOT NULL REFERENCES groups_(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    PRIMARY KEY(group_id, task_id)
);
CREATE TABLE IF NOT EXISTS hidden_media (
    group_id INTEGER NOT NULL REFERENCES groups_(id),
    sha256 TEXT NOT NULL,
    PRIMARY KEY(group_id, sha256)
);
CREATE TABLE IF NOT EXISTS blacklist_hashes (
    sha256 TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS telegram_messages (
    chat_kind TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    group_id INTEGER REFERENCES groups_(id),
    sha256 TEXT,
    PRIMARY KEY(chat_kind, message_id)
);
CREATE TABLE IF NOT EXISTS failures (
    id INTEGER PRIMARY KEY,
    code_chat_id INTEGER,
    code_message_id INTEGER,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code_chat_id, code_message_id)
);
CREATE TABLE IF NOT EXISTS bypasses (
    code_chat_id INTEGER NOT NULL,
    code_message_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(code_chat_id, code_message_id)
);
"""


class Repository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def reset_for_rebuild(self) -> None:
        with self.connection:
            for table in (
                "telegram_messages",
                "hidden_media",
                "group_tasks",
                "groups_",
                "task_media",
                "tasks",
                "media",
                "blacklist_hashes",
                "failures",
                "bypasses",
            ):
                self.connection.execute(f"DELETE FROM {table}")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.connection:
            yield

    def is_code_message_processed(self, chat_id: int, message_id: int) -> bool:
        task = self.connection.execute(
            "SELECT 1 FROM tasks WHERE code_chat_id=? AND code_message_id=?",
            (chat_id, message_id),
        ).fetchone()
        failure = self.connection.execute(
            "SELECT 1 FROM failures WHERE code_chat_id=? AND code_message_id=?",
            (chat_id, message_id),
        ).fetchone()
        bypass = self.connection.execute(
            "SELECT 1 FROM bypasses WHERE code_chat_id=? AND code_message_id=?",
            (chat_id, message_id),
        ).fetchone()
        return task is not None or failure is not None or bypass is not None

    def record_bypass(self, chat_id: int, message_id: int, code: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO bypasses(code_chat_id, code_message_id, code) "
                "VALUES (?, ?, ?)",
                (chat_id, message_id, code),
            )

    def record_failure(self, chat_id: int, message_id: int, reason: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO failures(code_chat_id, code_message_id, reason) "
                "VALUES (?, ?, ?)",
                (chat_id, message_id, reason),
            )

    def historical_hashes(self, hashes: set[str]) -> set[str]:
        if not hashes:
            return set()
        placeholders = ",".join("?" for _ in hashes)
        rows = self.connection.execute(
            f"SELECT DISTINCT sha256 FROM task_media WHERE sha256 IN ({placeholders})",
            tuple(hashes),
        )
        return {row[0] for row in rows}

    def blacklist_matches(self, hashes: set[str]) -> set[str]:
        if not hashes:
            return set()
        placeholders = ",".join("?" for _ in hashes)
        rows = self.connection.execute(
            f"SELECT sha256 FROM blacklist_hashes WHERE sha256 IN ({placeholders})",
            tuple(hashes),
        )
        return {row[0] for row in rows}

    def add_blacklist_hashes(self, hashes: set[str]) -> None:
        self.connection.executemany(
            "INSERT OR IGNORE INTO blacklist_hashes(sha256) VALUES (?)",
            ((value,) for value in hashes),
        )

    def insert_task(self, task: SourceTask, disposition: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO tasks(code, deal_media_hash, code_chat_id, code_message_id, disposition) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                task.code,
                task.deal_media_hash,
                task.code_chat_id,
                task.code_message_id,
                disposition,
            ),
        )
        task_id = int(cursor.lastrowid)
        for position, media in enumerate(task.media):
            self.connection.execute(
                "INSERT INTO media(sha256, path, mime_type) VALUES (?, ?, ?) "
                "ON CONFLICT(sha256) DO UPDATE SET path=excluded.path, "
                "mime_type=COALESCE(excluded.mime_type, media.mime_type)",
                (media.sha256, str(media.path), media.mime_type),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO task_media(task_id, sha256, source_message_id, position) "
                "VALUES (?, ?, ?, ?)",
                (task_id, media.sha256, media.telegram_message_id, position),
            )
        return task_id

    def create_group(
        self, kind: GroupKind, task_ids: Sequence[int], *, active: bool = True
    ) -> int:
        cursor = self.connection.execute(
            "INSERT INTO groups_(kind, active) VALUES (?, ?)", (kind, int(active))
        )
        group_id = int(cursor.lastrowid)
        self.connection.executemany(
            "INSERT INTO group_tasks(group_id, task_id) VALUES (?, ?)",
            ((group_id, task_id) for task_id in dict.fromkeys(task_ids)),
        )
        return group_id

    def activate_group(self, group_id: int) -> None:
        self.connection.execute("UPDATE groups_ SET active=1 WHERE id=?", (group_id,))

    def discard_staged_group(self, group_id: int, task_id: int | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM telegram_messages WHERE group_id=?", (group_id,)
            )
            self.connection.execute(
                "DELETE FROM hidden_media WHERE group_id=?", (group_id,)
            )
            self.connection.execute(
                "DELETE FROM group_tasks WHERE group_id=?", (group_id,)
            )
            self.connection.execute("DELETE FROM groups_ WHERE id=?", (group_id,))
            if task_id is not None:
                self.connection.execute(
                    "DELETE FROM task_media WHERE task_id=?", (task_id,)
                )
                self.connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def deactivate_groups(self, group_ids: Sequence[int]) -> None:
        if not group_ids:
            return
        placeholders = ",".join("?" for _ in group_ids)
        self.connection.execute(
            f"UPDATE groups_ SET active=0 WHERE id IN ({placeholders})",
            tuple(group_ids),
        )

    def active_groups_touching(
        self, hashes: set[str], kinds: Sequence[GroupKind] | None = None
    ) -> list[int]:
        if not hashes:
            return []
        hash_marks = ",".join("?" for _ in hashes)
        params: list[object] = list(hashes)
        kind_sql = ""
        if kinds:
            kind_marks = ",".join("?" for _ in kinds)
            kind_sql = f" AND g.kind IN ({kind_marks})"
            params.extend(kinds)
        rows = self.connection.execute(
            "SELECT DISTINCT g.id FROM groups_ g "
            "JOIN group_tasks gt ON gt.group_id=g.id "
            "JOIN task_media tm ON tm.task_id=gt.task_id "
            f"WHERE g.active=1 AND tm.sha256 IN ({hash_marks}){kind_sql}",
            params,
        )
        return [int(row[0]) for row in rows]

    def connected_groups(
        self, seed_hashes: set[str], kinds: Sequence[GroupKind]
    ) -> list[int]:
        hashes = set(seed_hashes)
        found: set[int] = set()
        while True:
            group_ids = set(self.active_groups_touching(hashes, kinds))
            new_ids = group_ids - found
            if not new_ids:
                return sorted(found)
            found.update(new_ids)
            for group_id in new_ids:
                hashes.update(self.get_group(group_id).hashes)

    def task_ids_for_groups(self, group_ids: Sequence[int]) -> list[int]:
        if not group_ids:
            return []
        marks = ",".join("?" for _ in group_ids)
        rows = self.connection.execute(
            f"SELECT DISTINCT task_id FROM group_tasks WHERE group_id IN ({marks})",
            tuple(group_ids),
        )
        return [int(row[0]) for row in rows]

    def hidden_hashes_for_groups(self, group_ids: Sequence[int]) -> set[str]:
        if not group_ids:
            return set()
        marks = ",".join("?" for _ in group_ids)
        rows = self.connection.execute(
            f"SELECT DISTINCT sha256 FROM hidden_media WHERE group_id IN ({marks})",
            tuple(group_ids),
        )
        return {str(row[0]) for row in rows}

    def add_hidden_hashes(self, group_id: int, hashes: set[str]) -> None:
        self.connection.executemany(
            "INSERT OR IGNORE INTO hidden_media(group_id, sha256) VALUES (?, ?)",
            ((group_id, value) for value in hashes),
        )

    def get_group(self, group_id: int) -> GroupSnapshot:
        group = self.connection.execute(
            "SELECT kind FROM groups_ WHERE id=?", (group_id,)
        ).fetchone()
        if group is None:
            raise KeyError(group_id)
        task_rows = self.connection.execute(
            "SELECT t.id, t.code, t.deal_media_hash FROM tasks t "
            "JOIN group_tasks gt ON gt.task_id=t.id WHERE gt.group_id=? ORDER BY t.id",
            (group_id,),
        ).fetchall()
        tasks: list[StoredTask] = []
        for task in task_rows:
            media_rows = self.connection.execute(
                "SELECT m.sha256, m.path, m.mime_type, tm.source_message_id "
                "FROM task_media tm JOIN media m ON m.sha256=tm.sha256 "
                "WHERE tm.task_id=? ORDER BY tm.position",
                (task["id"],),
            ).fetchall()
            media = tuple(
                MediaInput(
                    sha256=row["sha256"],
                    path=Path(row["path"]),
                    mime_type=row["mime_type"],
                    telegram_message_id=row["source_message_id"],
                )
                for row in media_rows
            )
            tasks.append(
                StoredTask(task["id"], task["code"], media, task["deal_media_hash"])
            )
        hidden = self.connection.execute(
            "SELECT sha256 FROM hidden_media WHERE group_id=?", (group_id,)
        )
        return GroupSnapshot(
            group_id,
            group["kind"],
            tuple(tasks),
            frozenset(row[0] for row in hidden),
        )

    def group_is_active(self, group_id: int) -> bool:
        row = self.connection.execute(
            "SELECT active FROM groups_ WHERE id=?", (group_id,)
        ).fetchone()
        return bool(row and row[0])

    def replace_message_index(
        self, chat_kind: str, group_id: int, messages: Sequence[tuple[int, str | None]]
    ) -> None:
        self.connection.execute(
            "DELETE FROM telegram_messages WHERE group_id=?", (group_id,)
        )
        self.connection.executemany(
            "INSERT INTO telegram_messages(chat_kind, message_id, group_id, sha256) "
            "VALUES (?, ?, ?, ?)",
            (
                (chat_kind, message_id, group_id, sha256)
                for message_id, sha256 in messages
            ),
        )

    def messages_for_groups(self, group_ids: Sequence[int]) -> list[tuple[str, int]]:
        if not group_ids:
            return []
        marks = ",".join("?" for _ in group_ids)
        rows = self.connection.execute(
            f"SELECT chat_kind, message_id FROM telegram_messages "
            f"WHERE group_id IN ({marks})",
            tuple(group_ids),
        )
        return [(row[0], int(row[1])) for row in rows]

    def remove_message_index_for_groups(
        self, group_ids: Sequence[int], chat_kind: str | None = None
    ) -> None:
        if not group_ids:
            return
        marks = ",".join("?" for _ in group_ids)
        kind_clause = " AND chat_kind=?" if chat_kind is not None else ""
        params: tuple[object, ...] = tuple(group_ids)
        if chat_kind is not None:
            params += (chat_kind,)
        self.connection.execute(
            f"DELETE FROM telegram_messages WHERE group_id IN ({marks}){kind_clause}",
            params,
        )

    def lookup_message(self, chat_kind: str, message_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT group_id, sha256 FROM telegram_messages "
            "WHERE chat_kind=? AND message_id=?",
            (chat_kind, message_id),
        ).fetchone()
