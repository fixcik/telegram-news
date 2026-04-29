from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import Bot, Group

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_state (
    group_name TEXT NOT NULL,
    channel TEXT NOT NULL,
    last_message_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (group_name, channel)
);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER NOT NULL,
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bots (
    name TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS groups (
    name TEXT PRIMARY KEY,
    cron TEXT,
    interval_hours REAL,
    interval_anchor TEXT,
    interests TEXT NOT NULL,
    instructions TEXT,
    bot_name TEXT NOT NULL REFERENCES bots(name),
    target TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL DEFAULT 0,
    max_messages_per_channel INTEGER,
    max_age_days INTEGER,
    min_message_length INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS group_channels (
    group_name TEXT NOT NULL REFERENCES groups(name) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    display_title TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_name, channel)
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db(db_path: str | Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        # Drop legacy channel_state schema (single cursor per channel) if present.
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='channel_state'"
        ).fetchone()
        if row:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(channel_state)").fetchall()}
            if "group_name" not in cols:
                log.warning(
                    "Migrating channel_state to per-group schema; existing cursors reset"
                )
                conn.execute("DROP TABLE channel_state")
        conn.executescript(SCHEMA)
        _ensure_column(conn, "groups", "max_messages_per_channel", "INTEGER")
        _ensure_column(conn, "groups", "max_age_days", "INTEGER")
        _ensure_column(conn, "groups", "min_message_length", "INTEGER")
        _ensure_column(conn, "group_channels", "display_title", "TEXT")


@contextmanager
def connect(db_path: str | Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- channel_state (per-group cursors) ----------

def get_last_message_id(db_path: str | Path, group_name: str, channel: str) -> int:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_message_id FROM channel_state "
            "WHERE group_name = ? AND channel = ?",
            (group_name, channel),
        ).fetchone()
        return row["last_message_id"] if row else 0


def set_last_message_id(
    db_path: str | Path, group_name: str, channel: str, message_id: int
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channel_state(group_name, channel, last_message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(group_name, channel) DO UPDATE
            SET last_message_id = excluded.last_message_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (group_name, channel, message_id),
        )


# ---------- digests ----------

def save_digest(
    db_path: str | Path, group_name: str, message_count: int, body: str
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO digests(group_name, message_count, body) VALUES (?, ?, ?)",
            (group_name, message_count, body),
        )


def last_digest_at(db_path: str | Path, group_name: str) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT created_at FROM digests WHERE group_name = ? "
            "ORDER BY id DESC LIMIT 1",
            (group_name,),
        ).fetchone()
        return row["created_at"] if row else None


# ---------- bots ----------

def bots_list(db_path: str | Path) -> list[Bot]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name, token FROM bots ORDER BY name"
        ).fetchall()
    return [Bot(name=r["name"], token=r["token"]) for r in rows]


def bots_get(db_path: str | Path, name: str) -> Bot | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT name, token FROM bots WHERE name = ?", (name,)
        ).fetchone()
    return Bot(name=row["name"], token=row["token"]) if row else None


def bots_upsert(db_path: str | Path, name: str, token: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO bots(name, token) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE
            SET token = excluded.token, updated_at = CURRENT_TIMESTAMP
            """,
            (name, token),
        )


def bots_delete(db_path: str | Path, name: str) -> None:
    """Delete a bot. Caller must verify no group references it (FK is RESTRICT)."""
    with connect(db_path) as conn:
        conn.execute("DELETE FROM bots WHERE name = ?", (name,))


def bots_referencing_groups(db_path: str | Path, name: str) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM groups WHERE bot_name = ?", (name,)
        ).fetchall()
    return [r["name"] for r in rows]


def load_bots_for_runtime(db_path: str | Path) -> dict[str, Bot]:
    return {b.name: b for b in bots_list(db_path)}


# ---------- groups ----------

def _row_to_group(row: sqlite3.Row, channels: list[str]) -> Group:
    return Group(
        name=row["name"],
        interests=row["interests"],
        channels=channels,
        bot=row["bot_name"],
        target=row["target"],
        cron=row["cron"],
        interval_hours=row["interval_hours"],
        interval_anchor=row["interval_anchor"],
        instructions=row["instructions"],
    )


def _channels_for(conn: sqlite3.Connection, group_name: str) -> list[str]:
    rows = conn.execute(
        "SELECT channel FROM group_channels WHERE group_name = ? "
        "ORDER BY position, channel",
        (group_name,),
    ).fetchall()
    return [r["channel"] for r in rows]


def groups_list(db_path: str | Path) -> list[Group]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM groups ORDER BY position, name"
        ).fetchall()
        return [_row_to_group(r, _channels_for(conn, r["name"])) for r in rows]


def groups_get(db_path: str | Path, name: str) -> Group | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM groups WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        return _row_to_group(row, _channels_for(conn, name))


def groups_upsert(db_path: str | Path, group: Group) -> None:
    """Insert or update a group, replacing its channels list."""
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO groups(name, cron, interval_hours, interval_anchor,
                               interests, instructions, bot_name, target)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                cron = excluded.cron,
                interval_hours = excluded.interval_hours,
                interval_anchor = excluded.interval_anchor,
                interests = excluded.interests,
                instructions = excluded.instructions,
                bot_name = excluded.bot_name,
                target = excluded.target,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                group.name,
                group.cron,
                group.interval_hours,
                group.interval_anchor,
                group.interests,
                group.instructions,
                group.bot,
                group.target,
            ),
        )
        conn.execute("DELETE FROM group_channels WHERE group_name = ?", (group.name,))
        for pos, ch in enumerate(group.channels):
            conn.execute(
                "INSERT INTO group_channels(group_name, channel, position) "
                "VALUES (?, ?, ?)",
                (group.name, ch, pos),
            )


def groups_delete(db_path: str | Path, name: str) -> None:
    with connect(db_path) as conn:
        # group_channels removed by CASCADE; channel_state per-group cursor stays
        # so that re-creating the group doesn't re-fetch the world.
        conn.execute("DELETE FROM groups WHERE name = ?", (name,))


def load_groups_for_runtime(db_path: str | Path) -> list[Group]:
    return groups_list(db_path)
