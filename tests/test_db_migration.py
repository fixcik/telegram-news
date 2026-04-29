import sqlite3
from pathlib import Path

import pytest

from telegram_news.db import init_db


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_init_db_adds_new_columns(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    cols_groups = _columns(db, "groups")
    cols_gc = _columns(db, "group_channels")

    assert "max_messages_per_channel" in cols_groups
    assert "max_age_days" in cols_groups
    assert "min_message_length" in cols_groups
    assert "display_title" in cols_gc


def test_init_db_idempotent(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    init_db(db)  # must not raise
    cols = _columns(db, "groups")
    assert "max_messages_per_channel" in cols


def test_init_db_migrates_legacy_groups_table(tmp_path):
    """Existing DB without the new columns gets them on first run."""
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE bots (
                name TEXT PRIMARY KEY, token TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE groups (
                name TEXT PRIMARY KEY, cron TEXT, interval_hours REAL,
                interval_anchor TEXT, interests TEXT NOT NULL, instructions TEXT,
                bot_name TEXT NOT NULL REFERENCES bots(name),
                target TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE group_channels (
                group_name TEXT NOT NULL REFERENCES groups(name) ON DELETE CASCADE,
                channel TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (group_name, channel)
            );
            CREATE TABLE digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER NOT NULL, body TEXT NOT NULL
            );
            CREATE TABLE channel_state (
                group_name TEXT NOT NULL, channel TEXT NOT NULL,
                last_message_id INTEGER NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_name, channel)
            );
            INSERT INTO bots(name, token) VALUES ('b1', 'tok');
            INSERT INTO groups(name, cron, interests, bot_name, target)
                VALUES ('g1', '0 11 * * *', 'x', 'b1', '@t');
        """)
    init_db(db)
    assert "max_messages_per_channel" in _columns(db, "groups")
    assert "display_title" in _columns(db, "group_channels")
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT name FROM groups").fetchone()
    assert row[0] == "g1"
