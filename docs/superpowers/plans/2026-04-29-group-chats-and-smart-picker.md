# Group chats + smart chat picker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add group-chat support to telegram-news, replace the freeform channel textarea with a smart picker (search across user's dialogs + paste any standard t.me link), and add per-group runtime overrides.

**Architecture:** A new `resolve.py` module canonicalises Telegram links/peer-ids; `tg.fetch_new_messages` becomes group-aware (filtering, sender attribution, private-chat link generation); a `web/routes/dialogs.py` router exposes the dialog cache to a reusable `_chat_picker.html` partial used by both source-list (multi) and target (single) form fields. Per-group overrides for `max_messages_per_channel`, `max_age_days`, and a new `min_message_length` live as nullable columns on the `groups` table.

**Tech Stack:** Python 3.11, FastAPI + Jinja2, HTMX (existing), Telethon, SQLite, APScheduler, OpenAI SDK against OpenRouter. Tests via `pytest` (newly added).

**Spec:** `docs/superpowers/specs/2026-04-29-group-chats-and-smart-picker-design.md`

---

## File map

| File | New / Modify | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | add `pytest` to optional dev deps |
| `tests/__init__.py` | New | pytest discovery |
| `tests/test_resolve.py` | New | URL parser unit tests |
| `tests/test_db_migration.py` | New | idempotent ADD COLUMN + cursor rename |
| `src/telegram_news/resolve.py` | New | parse_link + resolve (Telethon lookup) |
| `src/telegram_news/tg.py` | Modify | extend `Message`, group-aware fetch + filtering + sender + private link gen |
| `src/telegram_news/dialog_cache.py` | New | in-memory cache of `iter_dialogs` + substring filter |
| `src/telegram_news/db.py` | Modify | schema migration helper, cursor rename in `groups_upsert` |
| `src/telegram_news/config.py` | Modify | three new nullable fields on `Group` |
| `src/telegram_news/runner.py` | Modify | thread per-group overrides into fetcher |
| `src/telegram_news/summarize.py` | Modify | extended system prompt + new message-line format |
| `src/telegram_news/web/app.py` | Modify | StaticFiles mount, dialog-cache prewarm in lifespan |
| `src/telegram_news/web/routes/dialogs.py` | New | `/api/dialogs`, `/api/resolve`, `/api/dialogs/refresh` |
| `src/telegram_news/web/routes/groups.py` | Modify | accept new form fields, cursor-migration param |
| `src/telegram_news/web/templates/_chat_picker.html` | New | tabs, search input, results panel, chip list |
| `src/telegram_news/web/templates/_chip.html` | New | one-row chip partial returned by `/api/resolve` |
| `src/telegram_news/web/templates/group_form.html` | Modify | use picker for source list (multi) and target (single) + override fieldset |
| `src/telegram_news/web/templates/base.html` | Modify | `<script>` for chat_picker.js |
| `src/telegram_news/web/static/chat_picker.js` | New | keyboard nav + removeChip |
| `CLAUDE.md` | Modify | document new channel/peer semantics, picker, overrides |

All work happens in the repo at `services/telegram-news` (which is itself a git repo). All `git` commands below are relative to that directory.

---

### Task 1: Add pytest infra

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`

- [ ] **Step 1: Add pytest to dev deps in `pyproject.toml`**

Append after `dependencies = [...]`:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Sync deps**

```bash
uv sync --group dev
```

Expected: `pytest` installed; no errors.

- [ ] **Step 3: Create empty `tests/__init__.py`**

```python
```

- [ ] **Step 4: Verify pytest discovers nothing yet (no tests defined) but does not error**

```bash
uv run pytest -q
```

Expected: `no tests ran` exit code 5 (acceptable — no tests yet).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/__init__.py
git commit -m "test: add pytest dev dep"
```

---

### Task 2: URL parser — failing tests

**Files:**
- Create: `tests/test_resolve.py`

- [ ] **Step 1: Write failing tests covering all five parse cases**

```python
# tests/test_resolve.py
import pytest

from telegram_news.resolve import parse_link, ParseError


def test_username_at_form():
    assert parse_link("@channelname") == ("username", "channelname")


def test_username_bare():
    assert parse_link("channelname") == ("username", "channelname")


def test_username_url_https():
    assert parse_link("https://t.me/channelname") == ("username", "channelname")


def test_username_url_http():
    assert parse_link("http://t.me/channelname") == ("username", "channelname")


def test_username_url_tg_resolve():
    assert parse_link("tg://resolve?domain=channelname") == ("username", "channelname")


def test_username_message_link():
    assert parse_link("https://t.me/channelname/123") == ("username", "channelname")


def test_private_message_link():
    assert parse_link("https://t.me/c/1234567890/567") == ("peer_id", -1001234567890)


def test_raw_negative_id():
    assert parse_link("-1001234567890") == ("peer_id", -1001234567890)


def test_invite_link_plus_form_rejected():
    with pytest.raises(ParseError, match="invite"):
        parse_link("https://t.me/+abcDEF123")


def test_invite_link_joinchat_form_rejected():
    with pytest.raises(ParseError, match="invite"):
        parse_link("https://t.me/joinchat/abcDEF123")


def test_garbage_rejected():
    with pytest.raises(ParseError):
        parse_link("not a link at all !!!")


def test_whitespace_trimmed():
    assert parse_link("  @channelname  ") == ("username", "channelname")
```

- [ ] **Step 2: Run tests; expect ImportError (module does not exist)**

```bash
uv run pytest tests/test_resolve.py -v
```

Expected: collection error / `ModuleNotFoundError: telegram_news.resolve`.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_resolve.py
git commit -m "test(resolve): URL parser cases for group/channel link resolution"
```

---

### Task 3: Implement `parse_link`

**Files:**
- Create: `src/telegram_news/resolve.py`

- [ ] **Step 1: Implement `parse_link` and the supporting types**

```python
# src/telegram_news/resolve.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from telethon import TelegramClient


class ParseError(ValueError):
    """Raised when a string cannot be interpreted as a Telegram peer reference."""


@dataclass(frozen=True)
class ResolvedPeer:
    peer_id: int
    title: str
    username: str | None
    kind: Literal["channel", "megagroup", "chat"]


# https://t.me/c/<internal_id>/<msg_id>?  msg_id is optional and ignored.
_PRIVATE_RE = re.compile(r"^(?:https?://)?t\.me/c/(\d+)(?:/\d+)?/?$")
# https://t.me/<username>/<msg_id>?
_PUBLIC_URL_RE = re.compile(r"^(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{3,})(?:/\d+)?/?$")
# tg://resolve?domain=<username>
_TG_RESOLVE_RE = re.compile(r"^tg://resolve\?domain=([A-Za-z][A-Za-z0-9_]{3,})$")
# Bare or @-prefixed username.
_BARE_USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,})$")
# Raw -100xxxx numeric id.
_RAW_NUMERIC_RE = re.compile(r"^-100\d{6,}$")
# Invite forms we explicitly reject.
_INVITE_RE = re.compile(r"^(?:https?://)?t\.me/(?:\+|joinchat/)\S+$")


def parse_link(raw: str) -> tuple[Literal["username", "peer_id"], object]:
    """Parse a link/handle into ('username', name) or ('peer_id', -100…).

    Raises ParseError for invite links and unrecognised input.
    """
    s = (raw or "").strip()
    if not s:
        raise ParseError("empty input")

    if _INVITE_RE.match(s):
        raise ParseError(
            "invite links not supported — join the chat from the Telegram app, "
            "then pick it from 'From my chats'"
        )

    m = _PRIVATE_RE.match(s)
    if m:
        return ("peer_id", -1_000_000_000_000 - int(m.group(1)))

    if _RAW_NUMERIC_RE.match(s):
        return ("peer_id", int(s))

    m = _TG_RESOLVE_RE.match(s)
    if m:
        return ("username", m.group(1))

    m = _PUBLIC_URL_RE.match(s)
    if m:
        return ("username", m.group(1))

    m = _BARE_USERNAME_RE.match(s)
    if m:
        return ("username", m.group(1))

    raise ParseError(f"could not interpret as Telegram link: {s!r}")
```

- [ ] **Step 2: Run parser tests; all should pass**

```bash
uv run pytest tests/test_resolve.py -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/telegram_news/resolve.py
git commit -m "feat(resolve): parse_link supports public/private/raw forms, rejects invites"
```

---

### Task 4: `resolve()` function (Telethon lookup)

**Files:**
- Modify: `src/telegram_news/resolve.py`

No automated test for this — needs a live Telethon session. Manual smoke test in Task 22.

- [ ] **Step 1: Append the async `resolve()` function**

```python
# Append to src/telegram_news/resolve.py

from telethon.tl.types import PeerChannel  # noqa: E402  (kept here to localise the dependency)


async def resolve(client: "TelegramClient", raw: str) -> ResolvedPeer:
    """Parse + Telethon lookup. Raises ParseError on bad input,
    RuntimeError on lookup failure with user-friendly message."""
    kind_tag, value = parse_link(raw)

    try:
        if kind_tag == "username":
            entity = await client.get_entity(f"@{value}")
        else:
            internal_id = abs(int(value)) - 1_000_000_000_000
            entity = await client.get_entity(PeerChannel(internal_id))
    except (ValueError, TypeError) as e:
        raise RuntimeError(
            "chat not visible to current session — add it via 'From my chats'"
        ) from e

    if getattr(entity, "broadcast", False):
        kind: Literal["channel", "megagroup", "chat"] = "channel"
    elif getattr(entity, "megagroup", False):
        kind = "megagroup"
    else:
        kind = "chat"

    title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(entity.id)
    username = getattr(entity, "username", None)

    # Canonical peer_id we store: -100… for channels/megagroups, -<chat_id> for legacy Chat
    if kind in ("channel", "megagroup"):
        peer_id = -1_000_000_000_000 - entity.id  # entity.id for Channel is post-`-100` form
    else:
        peer_id = -entity.id

    return ResolvedPeer(peer_id=peer_id, title=title, username=username, kind=kind)
```

- [ ] **Step 2: Re-run tests; should still all pass (no behavioural change to `parse_link`)**

```bash
uv run pytest tests/test_resolve.py -v
```

Expected: all green.

- [ ] **Step 3: Syntax check the whole package**

```bash
python3 -m py_compile src/telegram_news/*.py src/telegram_news/web/*.py src/telegram_news/web/routes/*.py
```

Expected: no output (success).

- [ ] **Step 4: Commit**

```bash
git add src/telegram_news/resolve.py
git commit -m "feat(resolve): add async resolve() against Telethon client"
```

---

### Task 5: Schema migration — failing test

**Files:**
- Create: `tests/test_db_migration.py`

- [ ] **Step 1: Write failing test for idempotent ADD COLUMN**

```python
# tests/test_db_migration.py
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
    # No duplicates / no errors implies idempotency; assert presence as sanity.
    assert "max_messages_per_channel" in cols


def test_init_db_migrates_legacy_groups_table(tmp_path):
    """Existing DB without the new columns gets them on first run."""
    db = tmp_path / "state.db"
    # Simulate a pre-migration DB.
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
    assert row[0] == "g1"  # row preserved
```

- [ ] **Step 2: Run; expect failure (columns not yet added by `init_db`)**

```bash
uv run pytest tests/test_db_migration.py -v
```

Expected: `AssertionError: 'max_messages_per_channel' not in {...}` etc.

- [ ] **Step 3: Commit**

```bash
git add tests/test_db_migration.py
git commit -m "test(db): assert init_db adds per-group override + display_title columns"
```

---

### Task 6: Schema migration — implementation

**Files:**
- Modify: `src/telegram_news/db.py`

- [ ] **Step 1: Update `SCHEMA` and add a migration helper**

Replace the `SCHEMA` constant + `init_db` body in `src/telegram_news/db.py`:

```python
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
        # Drop legacy single-cursor channel_state if present.
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
        # Idempotent ADD COLUMN for pre-existing DBs that already have these tables
        # without the new columns. New CREATE TABLE statements above include them;
        # ALTERs cover the upgrade-from-old-schema case.
        _ensure_column(conn, "groups", "max_messages_per_channel", "INTEGER")
        _ensure_column(conn, "groups", "max_age_days", "INTEGER")
        _ensure_column(conn, "groups", "min_message_length", "INTEGER")
        _ensure_column(conn, "group_channels", "display_title", "TEXT")
```

- [ ] **Step 2: Run migration tests; all should pass**

```bash
uv run pytest tests/test_db_migration.py -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/telegram_news/db.py
git commit -m "feat(db): per-group override + display_title columns, idempotent migration"
```

---

### Task 7: `Group` dataclass — per-group overrides

**Files:**
- Modify: `src/telegram_news/config.py:60-70`

- [ ] **Step 1: Add three fields to `Group`**

Replace the `Group` dataclass:

```python
@dataclass
class Group:
    name: str
    interests: str
    channels: list[str]
    bot: str
    target: str
    cron: str | None = None
    interval_hours: float | None = None
    interval_anchor: str | None = None
    instructions: str | None = None
    max_messages_per_channel: int | None = None
    max_age_days: int | None = None
    min_message_length: int | None = None
```

- [ ] **Step 2: Update `db._row_to_group` to populate the new fields**

In `src/telegram_news/db.py`, replace `_row_to_group`:

```python
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
        max_messages_per_channel=row["max_messages_per_channel"],
        max_age_days=row["max_age_days"],
        min_message_length=row["min_message_length"],
    )
```

- [ ] **Step 3: Update `groups_upsert` to write the new columns**

Replace the SQL in `groups_upsert`:

```python
def groups_upsert(db_path: str | Path, group: Group) -> None:
    """Insert or update a group, replacing its channels list."""
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO groups(name, cron, interval_hours, interval_anchor,
                               interests, instructions, bot_name, target,
                               max_messages_per_channel, max_age_days,
                               min_message_length)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                cron = excluded.cron,
                interval_hours = excluded.interval_hours,
                interval_anchor = excluded.interval_anchor,
                interests = excluded.interests,
                instructions = excluded.instructions,
                bot_name = excluded.bot_name,
                target = excluded.target,
                max_messages_per_channel = excluded.max_messages_per_channel,
                max_age_days = excluded.max_age_days,
                min_message_length = excluded.min_message_length,
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
                group.max_messages_per_channel,
                group.max_age_days,
                group.min_message_length,
            ),
        )
        conn.execute("DELETE FROM group_channels WHERE group_name = ?", (group.name,))
        for pos, ch in enumerate(group.channels):
            conn.execute(
                "INSERT INTO group_channels(group_name, channel, position) "
                "VALUES (?, ?, ?)",
                (group.name, ch, pos),
            )
```

(We do **not** add cursor migration in this step — that comes in Task 8 with its own test.)

- [ ] **Step 4: Syntax check**

```bash
python3 -m py_compile src/telegram_news/*.py
```

Expected: no output.

- [ ] **Step 5: Run all tests**

```bash
uv run pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_news/config.py src/telegram_news/db.py
git commit -m "feat(group): per-group max_messages, max_age_days, min_message_length"
```

---

### Task 8: Cursor migration on group edit — failing test

**Files:**
- Modify: `tests/test_db_migration.py`

- [ ] **Step 1: Append a test for cursor rename**

Add at the bottom of `tests/test_db_migration.py`:

```python
from telegram_news.config import Bot, Group
from telegram_news.db import (
    bots_upsert, groups_upsert, get_last_message_id, set_last_message_id,
)


def test_groups_upsert_renames_cursor(tmp_path):
    """When channels rename from '@foo' to '-100123', the cursor follows."""
    db = tmp_path / "state.db"
    init_db(db)
    bots_upsert(db, "b1", "tok")
    g = Group(
        name="g1", interests="x", channels=["@foo", "@bar"],
        bot="b1", target="@t", cron="0 11 * * *",
    )
    groups_upsert(db, g)
    set_last_message_id(db, "g1", "@foo", 100)
    set_last_message_id(db, "g1", "@bar", 200)

    # Replace @foo with canonical id, keep @bar as-is.
    g2 = Group(
        name="g1", interests="x", channels=["-1001234567890", "@bar"],
        bot="b1", target="@t", cron="0 11 * * *",
    )
    groups_upsert(
        db, g2,
        original_channels=["@foo", "@bar"],  # parallel to g2.channels
    )

    assert get_last_message_id(db, "g1", "-1001234567890") == 100
    assert get_last_message_id(db, "g1", "@bar") == 200
    # The old cursor row for @foo should be gone (renamed in place).
    assert get_last_message_id(db, "g1", "@foo") == 0
```

- [ ] **Step 2: Run; expect failure (signature does not accept `original_channels`)**

```bash
uv run pytest tests/test_db_migration.py::test_groups_upsert_renames_cursor -v
```

Expected: `TypeError: groups_upsert() got an unexpected keyword argument 'original_channels'`.

- [ ] **Step 3: Commit failing test**

```bash
git add tests/test_db_migration.py
git commit -m "test(db): groups_upsert renames channel_state cursor on canonicalisation"
```

---

### Task 9: Cursor migration on group edit — implementation

**Files:**
- Modify: `src/telegram_news/db.py`

- [ ] **Step 1: Extend `groups_upsert` with the optional `original_channels` parameter**

Replace `groups_upsert` again:

```python
def groups_upsert(
    db_path: str | Path,
    group: Group,
    original_channels: list[str] | None = None,
    display_titles: list[str | None] | None = None,
) -> None:
    """Insert or update a group, replacing its channels list.

    `original_channels`: parallel to `group.channels`. For each i where
    `original_channels[i]` is non-empty and differs from `group.channels[i]`,
    the channel_state cursor row is renamed in place so we don't re-fetch.
    `display_titles`: parallel cached titles, written into group_channels.display_title.
    """
    if original_channels is not None and len(original_channels) != len(group.channels):
        raise ValueError("original_channels length must match group.channels")
    if display_titles is not None and len(display_titles) != len(group.channels):
        raise ValueError("display_titles length must match group.channels")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO groups(name, cron, interval_hours, interval_anchor,
                               interests, instructions, bot_name, target,
                               max_messages_per_channel, max_age_days,
                               min_message_length)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                cron = excluded.cron,
                interval_hours = excluded.interval_hours,
                interval_anchor = excluded.interval_anchor,
                interests = excluded.interests,
                instructions = excluded.instructions,
                bot_name = excluded.bot_name,
                target = excluded.target,
                max_messages_per_channel = excluded.max_messages_per_channel,
                max_age_days = excluded.max_age_days,
                min_message_length = excluded.min_message_length,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                group.name, group.cron, group.interval_hours, group.interval_anchor,
                group.interests, group.instructions, group.bot, group.target,
                group.max_messages_per_channel, group.max_age_days, group.min_message_length,
            ),
        )

        # Rename cursors before rewriting group_channels.
        if original_channels is not None:
            for new_ch, old_ch in zip(group.channels, original_channels):
                if old_ch and old_ch != new_ch:
                    conn.execute(
                        "UPDATE channel_state SET channel = ? "
                        "WHERE group_name = ? AND channel = ?",
                        (new_ch, group.name, old_ch),
                    )

        conn.execute("DELETE FROM group_channels WHERE group_name = ?", (group.name,))
        for pos, ch in enumerate(group.channels):
            title = display_titles[pos] if display_titles is not None else None
            conn.execute(
                "INSERT INTO group_channels(group_name, channel, display_title, position) "
                "VALUES (?, ?, ?, ?)",
                (group.name, ch, title, pos),
            )
```

- [ ] **Step 2: Run all tests**

```bash
uv run pytest -q
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/telegram_news/db.py
git commit -m "feat(db): rename channel_state cursor on canonicalisation in groups_upsert"
```

---

### Task 10: Add `display_title` accessor

**Files:**
- Modify: `src/telegram_news/db.py`

- [ ] **Step 1: Add a function to read channels-with-titles for the form**

Append after `_channels_for`:

```python
def channels_with_titles(db_path: str | Path, group_name: str) -> list[dict]:
    """Return [{'channel': ..., 'display_title': ...}, ...] in stored order.

    Used by the edit form to render existing chips with titles.
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT channel, display_title FROM group_channels "
            "WHERE group_name = ? ORDER BY position, channel",
            (group_name,),
        ).fetchall()
    return [{"channel": r["channel"], "display_title": r["display_title"]} for r in rows]
```

- [ ] **Step 2: Syntax check**

```bash
python3 -m py_compile src/telegram_news/db.py
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add src/telegram_news/db.py
git commit -m "feat(db): channels_with_titles accessor for edit form"
```

---

### Task 11: Extend `Message` dataclass + group-aware `fetch_new_messages`

**Files:**
- Modify: `src/telegram_news/tg.py`

This is the largest single edit. No automated test (needs Telethon + a session) — manual smoke in Task 22.

- [ ] **Step 1: Replace the entire body of `src/telegram_news/tg.py`**

```python
# src/telegram_news/tg.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import PeerChannel, PeerChat

from .config import Config

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://", re.IGNORECASE)


@dataclass
class Message:
    channel: str            # canonical: "-100…", "-<chat_id>", or legacy "@username"
    channel_title: str
    message_id: int
    date: datetime
    text: str
    link: str
    sender_name: str | None  # None for broadcast channels


def make_client(cfg: Config) -> TelegramClient:
    Path(cfg.telegram.session_path).parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        cfg.telegram.session_path,
        cfg.telegram.api_id,
        cfg.telegram.api_hash,
    )


async def _resolve_entity(client: TelegramClient, channel: str):
    """Inverse of resolve.parse_link: take stored DB value -> Telethon entity."""
    s = channel.strip()
    if s.startswith("@") or (s and s[0].isalpha()):
        return await client.get_entity(s if s.startswith("@") else f"@{s}")
    n = int(s)
    abs_n = abs(n)
    if abs_n >= 1_000_000_000_000:
        return await client.get_entity(PeerChannel(abs_n - 1_000_000_000_000))
    return await client.get_entity(PeerChat(abs_n))


def _format_sender(sender) -> str:
    if sender is None:
        return "Аноним"
    name = " ".join(
        x for x in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if x
    ).strip()
    if name:
        return name
    title = getattr(sender, "title", None)
    if title:
        return title
    username = getattr(sender, "username", None)
    if username:
        return f"@{username}"
    return "Аноним"


def _build_link(entity, msg_id: int) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    # Private channel/megagroup: t.me/c/<entity.id>/<msg>.
    # For Telethon `Channel` objects, entity.id is already the post-`-100` form,
    # so no further math is required. For legacy `Chat` (which has no `t.me/c/`
    # public URL), we still use the same form as a best-effort link.
    return f"https://t.me/c/{abs(entity.id)}/{msg_id}"


async def fetch_new_messages(
    client: TelegramClient,
    channel: str,
    last_message_id: int,
    max_messages: int,
    max_age_days: int,
    min_length: int = 20,
) -> list[Message]:
    """Fetch messages newer than last_message_id, with group-aware filtering."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    out: list[Message] = []

    entity = await _resolve_entity(client, channel)
    is_broadcast = bool(getattr(entity, "broadcast", False))
    title = getattr(entity, "title", None) or getattr(entity, "username", None) or channel

    async for msg in client.iter_messages(entity, limit=max_messages):
        if msg.id <= last_message_id:
            break
        if msg.date < cutoff:
            break
        if msg.action is not None:
            continue  # join/leave/pin/avatar/call

        text = (msg.message or "").strip()
        if not text:
            continue
        if len(text) < min_length and not _URL_RE.search(text):
            continue

        # Reply marker
        if msg.reply_to is not None:
            text = "↳ " + text

        # Sender attribution: only for non-broadcast.
        if is_broadcast:
            sender_name: str | None = None
        else:
            try:
                sender = await msg.get_sender()
            except Exception:
                sender = None
            sender_name = _format_sender(sender)

        out.append(
            Message(
                channel=channel,
                channel_title=title,
                message_id=msg.id,
                date=msg.date,
                text=text,
                link=_build_link(entity, msg.id),
                sender_name=sender_name,
            )
        )

    out.reverse()  # chronological
    return out
```

- [ ] **Step 2: Syntax check**

```bash
python3 -m py_compile src/telegram_news/tg.py
```

Expected: no output.

- [ ] **Step 3: Run unit tests (Telethon-touching code is not exercised here)**

```bash
uv run pytest -q
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/telegram_news/tg.py
git commit -m "feat(tg): group-aware fetch with sender attribution, filtering, private-link gen"
```

---

### Task 12: `runner.run_group` threads per-group overrides

**Files:**
- Modify: `src/telegram_news/runner.py:38-44`

- [ ] **Step 1: Replace the `fetch_new_messages` call inside `run_group`**

Find the for-loop around line 35–48 and replace:

```python
    max_msgs = group.max_messages_per_channel or cfg.fetcher.max_messages_per_channel
    max_age = group.max_age_days or cfg.fetcher.max_age_days
    min_len = group.min_message_length if group.min_message_length is not None else 20

    for channel in group.channels:
        last_id = get_last_message_id(cfg.storage.db_path, group.name, channel)
        try:
            msgs = await fetch_new_messages(
                client,
                channel,
                last_id,
                max_msgs,
                max_age,
                min_len,
            )
        except Exception:
            log.exception("Fetch failed for %s in group %s", channel, group.name)
            fetch_failed = True
            continue

        log.info("  %s: %d new messages", channel, len(msgs))
        all_messages.extend(msgs)
        if msgs:
            max_id_by_channel[channel] = max(m.message_id for m in msgs)
```

- [ ] **Step 2: Syntax check**

```bash
python3 -m py_compile src/telegram_news/runner.py
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add src/telegram_news/runner.py
git commit -m "feat(runner): apply per-group max_messages, max_age_days, min_length"
```

---

### Task 13: Summarizer — system prompt + line format

**Files:**
- Modify: `src/telegram_news/summarize.py:12-46` and `src/telegram_news/summarize.py:48-55`

- [ ] **Step 1: Append a paragraph to `SYSTEM_PROMPT`**

In `summarize.py`, find the closing `"""` of `SYSTEM_PROMPT` (around line 46) and replace the trailing block:

```python
SYSTEM_PROMPT = """\
[... keep existing content ...]
- В обычном тексте символы '<', '>' и '&' заменяй на '&lt;', '&gt;', '&amp;'.
- В URL внутри href ничего не экранируй — копируй как есть.
- Не вкладывай теги-ссылки друг в друга.

Среди источников могут быть групповые чаты — у их сообщений в заголовке указан \
автор в формате `[Имя]`. Используй имена когда это помогает раскрыть контекст \
обсуждения (например: «Иван предложил X, Петя возразил»), опускай когда не нужно. \
Реплаи помечены символом ↳ в начале текста.
"""
```

(Editor note: do not duplicate the closing `"""`. Just insert the paragraph above the existing closing `"""`.)

- [ ] **Step 2: Replace `_format_messages_for_prompt`**

```python
def _format_messages_for_prompt(messages: list[Message]) -> str:
    lines: list[str] = []
    for m in messages:
        header = f"[{m.channel_title} | {m.date:%Y-%m-%d %H:%M}"
        if m.sender_name:
            header += f" | {m.sender_name}"
        header += f" | {m.link}]"
        lines.append(header)
        lines.append(m.text)
        lines.append("---")
    return "\n".join(lines)
```

- [ ] **Step 3: Syntax check**

```bash
python3 -m py_compile src/telegram_news/summarize.py
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add src/telegram_news/summarize.py
git commit -m "feat(summarize): include channel title and sender name in prompt context"
```

---

### Task 14: Dialog cache module

**Files:**
- Create: `src/telegram_news/dialog_cache.py`

- [ ] **Step 1: Implement the cache + filter**

```python
# src/telegram_news/dialog_cache.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from telethon import TelegramClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedDialog:
    peer_id: int            # canonical: -100… or -<chat_id>
    title: str
    username: str | None
    kind: Literal["channel", "megagroup", "chat"]


class DialogCache:
    def __init__(self) -> None:
        self._items: list[CachedDialog] = []

    @property
    def count(self) -> int:
        return len(self._items)

    async def refresh(self, client: TelegramClient) -> int:
        items: list[CachedDialog] = []
        async for d in client.iter_dialogs(limit=None):
            entity = d.entity
            if getattr(entity, "broadcast", False):
                kind: Literal["channel", "megagroup", "chat"] = "channel"
            elif getattr(entity, "megagroup", False):
                kind = "megagroup"
            elif hasattr(entity, "title"):
                kind = "chat"
            else:
                continue  # users / bots — not interesting as sources
            title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(entity.id)
            username = getattr(entity, "username", None)
            if kind in ("channel", "megagroup"):
                peer_id = -1_000_000_000_000 - entity.id
            else:
                peer_id = -entity.id
            items.append(CachedDialog(peer_id=peer_id, title=title, username=username, kind=kind))
        self._items = items
        log.info("Dialog cache refreshed: %d entries", len(items))
        return len(items)

    def search(self, q: str, exclude: set[int], limit: int) -> list[CachedDialog]:
        q_lower = (q or "").strip().lower()
        out: list[CachedDialog] = []
        for d in self._items:
            if d.peer_id in exclude:
                continue
            if q_lower:
                hay = f"{d.title} {d.username or ''}".lower()
                if q_lower not in hay:
                    continue
            out.append(d)
            if len(out) >= limit:
                break
        return out
```

- [ ] **Step 2: Add a small unit test**

Create `tests/test_dialog_cache.py`:

```python
from telegram_news.dialog_cache import CachedDialog, DialogCache


def _seed(cache, items):
    cache._items = items


def test_search_substring_case_insensitive():
    c = DialogCache()
    _seed(c, [
        CachedDialog(-1, "Crypto News Daily", "cryptonews", "channel"),
        CachedDialog(-2, "Dev Chat", None, "megagroup"),
        CachedDialog(-3, "Random", "random_room", "megagroup"),
    ])
    out = c.search("crypto", exclude=set(), limit=10)
    assert [d.peer_id for d in out] == [-1]


def test_search_excludes_selected():
    c = DialogCache()
    _seed(c, [
        CachedDialog(-1, "Foo", None, "channel"),
        CachedDialog(-2, "Foo", None, "channel"),
    ])
    assert [d.peer_id for d in c.search("foo", exclude={-1}, limit=10)] == [-2]


def test_search_respects_limit():
    c = DialogCache()
    _seed(c, [CachedDialog(i, f"Foo{i}", None, "channel") for i in range(20)])
    assert len(c.search("foo", exclude=set(), limit=5)) == 5


def test_search_empty_query_returns_all_until_limit():
    c = DialogCache()
    _seed(c, [CachedDialog(i, f"x{i}", None, "channel") for i in range(3)])
    assert len(c.search("", exclude=set(), limit=10)) == 3
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/test_dialog_cache.py -v
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/telegram_news/dialog_cache.py tests/test_dialog_cache.py
git commit -m "feat: dialog cache with substring search and exclude"
```

---

### Task 15: Routes — `/api/dialogs`, `/api/resolve`, `/api/dialogs/refresh`

**Files:**
- Create: `src/telegram_news/web/routes/dialogs.py`

- [ ] **Step 1: Implement the router**

```python
# src/telegram_news/web/routes/dialogs.py
from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ...resolve import ParseError, resolve

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/dialogs")
async def list_dialogs(request: Request, q: str = "", exclude: str = "", limit: int = 20):
    cache = getattr(request.app.state, "dialog_cache", None)
    if cache is None or cache.count == 0:
        return JSONResponse({"items": [], "cache_empty": True})
    excluded_ids: set[int] = set()
    for tok in exclude.split(","):
        tok = tok.strip()
        if tok:
            try:
                excluded_ids.add(int(tok))
            except ValueError:
                pass
    items = cache.search(q=q, exclude=excluded_ids, limit=max(1, min(limit, 50)))
    return JSONResponse({"items": [asdict(d) for d in items], "cache_empty": False})


@router.post("/resolve", response_class=HTMLResponse)
async def resolve_link(request: Request, link: str = Form(...), name: str = Form("channel_peers")):
    """Resolve a pasted link and return a chip HTML fragment.

    `name` selects the field-name set used in the chip's hidden inputs:
    `channel_peers` (multi) vs `target_peer` (single).
    """
    client = request.app.state.client
    try:
        peer = await resolve(client, link)
    except ParseError as e:
        return HTMLResponse(
            f'<small class="error">{_html_escape(str(e))}</small>',
            status_code=400,
        )
    except RuntimeError as e:
        return HTMLResponse(
            f'<small class="error">{_html_escape(str(e))}</small>',
            status_code=400,
        )
    return request.app.state.templates.TemplateResponse(
        request, "_chip.html",
        {"peer": peer, "name": name, "original": ""},
    )


@router.post("/dialogs/refresh")
async def refresh_dialogs(request: Request):
    cache = getattr(request.app.state, "dialog_cache", None)
    client = request.app.state.client
    if cache is None:
        return JSONResponse({"error": "cache not initialised"}, status_code=503)
    try:
        n = await cache.refresh(client)
    except Exception as e:
        log.exception("Dialog cache refresh failed")
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"count": n})


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )
```

- [ ] **Step 2: Syntax check**

```bash
python3 -m py_compile src/telegram_news/web/routes/dialogs.py
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add src/telegram_news/web/routes/dialogs.py
git commit -m "feat(web): /api/dialogs, /api/resolve, /api/dialogs/refresh"
```

---

### Task 16: `web/app.py` — StaticFiles mount, dialog-cache prewarm

**Files:**
- Modify: `src/telegram_news/web/app.py`

- [ ] **Step 1: Add the static-files mount and dialog-cache prewarm**

Replace the imports block (lines 1–22) with:

```python
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config
from ..db import init_db, load_groups_for_runtime
from ..dialog_cache import DialogCache
from ..scheduler_ctl import SchedulerCtl
from ..tg import make_client
from .log_bus import LogBus, LogBusHandler
from .routes import auth as auth_routes
from .routes import bots as bots_routes
from .routes import dashboard as dashboard_routes
from .routes import dialogs as dialogs_routes
from .routes import groups as groups_routes
from .routes import logs as logs_routes
```

- [ ] **Step 2: Add the static-files dir constant after `TEMPLATES_DIR`**

```python
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
```

- [ ] **Step 3: In the lifespan, after the auth check, prewarm the dialog cache**

Replace the lifespan body's authorization block:

```python
        if await client.is_user_authorized():
            scheduler_ctl.populate_all(load_groups_for_runtime(cfg.storage.db_path))
            log.info("User authorized; scheduler populated from DB")
            try:
                await dialog_cache.refresh(client)
            except Exception:
                log.exception("Initial dialog cache refresh failed; picker will be empty until /api/dialogs/refresh")
        else:
            log.warning("User not authorized; scheduler will populate after /auth")
```

And earlier in the lifespan body (right after `scheduler_ctl = SchedulerCtl(...)`):

```python
        dialog_cache = DialogCache()
```

And in the `app.state.*` block, add:

```python
        app.state.dialog_cache = dialog_cache
```

- [ ] **Step 4: Mount the static dir + include the dialogs router**

After `app = FastAPI(title="telegram-news", lifespan=lifespan)`:

```python
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.state.cfg = cfg
```

After the existing `app.include_router(...)` calls, add:

```python
    app.include_router(dialogs_routes.router)
```

- [ ] **Step 5: Also prewarm cache on successful web-auth**

Open `src/telegram_news/web/routes/auth.py` and find the spot where `populate_all(...)` is called after a successful sign-in. Right after that call, add (use the same indent):

```python
    cache = getattr(request.app.state, "dialog_cache", None)
    if cache is not None:
        try:
            await cache.refresh(request.app.state.client)
        except Exception:
            log.exception("Dialog cache refresh after auth failed")
```

(If `auth.py` does not import `log`, add `log = logging.getLogger(__name__)` near the top.)

- [ ] **Step 6: Syntax check**

```bash
python3 -m py_compile src/telegram_news/web/app.py src/telegram_news/web/routes/auth.py
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add src/telegram_news/web/app.py src/telegram_news/web/routes/auth.py
git commit -m "feat(web): mount /static, prewarm DialogCache on auth"
```

---

### Task 17: Templates — chip partial

**Files:**
- Create: `src/telegram_news/web/templates/_chip.html`

- [ ] **Step 1: Write the chip partial**

```html
{# Variables: peer (ResolvedPeer or CachedDialog), name (form field), original (string) #}
{% set icon = '📢' if peer.kind == 'channel' else '👥' %}
<span class="chip" data-peer-id="{{ peer.peer_id }}">
  <span class="chip-icon">{{ icon }}</span>
  <span class="chip-title">{{ peer.title }}</span>
  {% if peer.username %}<small class="chip-username">@{{ peer.username }}</small>{% endif %}
  <button type="button" onclick="removeChip(this)" aria-label="Удалить">✕</button>
  <input type="hidden" name="{{ name }}" value="{{ peer.peer_id }}">
  <input type="hidden" name="{{ name }}__original" value="{{ original }}">
  <input type="hidden" name="{{ name }}__title" value="{{ peer.title }}">
</span>
```

(Field-name convention: `{name}` for the value, `{name}__original` for the parallel original, `{name}__title` for the parallel display title. The `__` suffix avoids clashing with arbitrary form names and is parsed server-side.)

- [ ] **Step 2: Commit**

```bash
git add src/telegram_news/web/templates/_chip.html
git commit -m "feat(web): chip partial used by picker and /api/resolve"
```

---

### Task 18: Templates — chat picker partial

**Files:**
- Create: `src/telegram_news/web/templates/_chat_picker.html`

- [ ] **Step 1: Write the picker partial**

```html
{#
  Variables in scope:
    name      — form field base name ("channel_peers" or "target_peer")
    mode      — "single" | "multi"
    selected  — list of {peer_id, title, username, kind, original}
                (already-stored entries on edit forms; empty list on new)
#}
<div class="chat-picker"
     data-name="{{ name }}"
     data-mode="{{ mode }}">

  <div class="chip-list" id="picker-{{ name }}-chips">
    {% for s in selected %}
      {% set peer_obj = {'peer_id': s.peer_id, 'title': s.title, 'username': s.username, 'kind': s.kind} %}
      {% with peer=peer_obj, name=name, original=s.original %}
        {% include "_chip.html" %}
      {% endwith %}
    {% endfor %}
  </div>

  <div class="picker-tabs" role="tablist">
    <button type="button" class="picker-tab active" data-tab="dialogs">Из моих чатов</button>
    <button type="button" class="picker-tab" data-tab="link">По ссылке</button>
    <button type="button" class="picker-refresh"
            hx-post="/api/dialogs/refresh"
            hx-swap="none"
            title="Перечитать список чатов">🔄</button>
  </div>

  <div class="picker-pane" data-pane="dialogs">
    <input type="text"
           class="picker-search"
           placeholder="Поиск по своим чатам…"
           autocomplete="off"
           hx-get="/api/dialogs"
           hx-trigger="keyup changed delay:200ms, focus"
           hx-target="#picker-{{ name }}-results"
           hx-include="closest .chat-picker"
           hx-vals='js:{"q": event.target.value, "exclude": Array.from(document.querySelectorAll("#picker-{{ name }}-chips [data-peer-id]")).map(e=>e.dataset.peerId).join(",")}'>
    <div class="picker-results" id="picker-{{ name }}-results"></div>
  </div>

  <div class="picker-pane hidden" data-pane="link">
    <input type="text" class="picker-link-input" placeholder="@username, t.me/foo, t.me/c/123/456 или -1001234567890">
    <button type="button"
            class="picker-link-add"
            hx-post="/api/resolve"
            hx-target="#picker-{{ name }}-chips"
            hx-swap="beforeend"
            hx-include="previous .picker-link-input"
            hx-vals='{"name": "{{ name }}"}'>Добавить</button>
    <div class="picker-link-error"></div>
  </div>
</div>
```

(Note: HTMX submits the link input via `hx-include="previous .picker-link-input"`. The endpoint expects `link=<value>`; the input's `name` attribute is `link` — set this in the template:)

Replace the link-input line with:

```html
    <input type="text" name="link" class="picker-link-input" placeholder="@username, t.me/foo, t.me/c/123/456 или -1001234567890">
```

- [ ] **Step 2: Commit**

```bash
git add src/telegram_news/web/templates/_chat_picker.html
git commit -m "feat(web): _chat_picker.html partial (multi/single-select with tabs)"
```

---

### Task 19: Picker JS

**Files:**
- Create: `src/telegram_news/web/static/chat_picker.js`
- Modify: `src/telegram_news/web/templates/base.html`

- [ ] **Step 1: Write the JS**

```javascript
// src/telegram_news/web/static/chat_picker.js
(function () {
  // Tabs
  document.addEventListener("click", function (e) {
    const tabBtn = e.target.closest(".picker-tab");
    if (tabBtn) {
      const picker = tabBtn.closest(".chat-picker");
      const tab = tabBtn.dataset.tab;
      picker.querySelectorAll(".picker-tab").forEach((b) => b.classList.toggle("active", b === tabBtn));
      picker.querySelectorAll(".picker-pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== tab));
    }
  });

  // Click on a result row in the dialogs pane → add chip
  document.addEventListener("click", function (e) {
    const row = e.target.closest(".picker-result");
    if (!row) return;
    const picker = row.closest(".chat-picker");
    const name = picker.dataset.name;
    const mode = picker.dataset.mode;
    const chips = picker.querySelector(".chip-list");
    if (mode === "single") chips.innerHTML = "";
    chips.insertAdjacentHTML("beforeend", row.dataset.chipHtml);
    // Clear search and results; refocus
    const search = picker.querySelector(".picker-search");
    if (search) {
      search.value = "";
      picker.querySelector(".picker-results").innerHTML = "";
      search.focus();
    }
  });

  // Resolve-link returns a chip directly via HTMX (already appended to chip-list).
  // For single-mode, prune older chips after HTMX swap.
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    const target = evt.detail.target;
    if (!target || !target.classList.contains("chip-list")) return;
    const picker = target.closest(".chat-picker");
    if (picker && picker.dataset.mode === "single") {
      const chips = target.querySelectorAll(".chip");
      while (chips.length > 1) {
        chips[0].remove();
      }
    }
  });

  window.removeChip = function (btn) {
    const chip = btn.closest(".chip");
    if (chip) chip.remove();
  };
})();
```

- [ ] **Step 2: Update the dialogs route to return HTML rows (not JSON) for HTMX consumption**

The picker uses HTMX to swap `picker-results` directly. Update `web/routes/dialogs.py` `list_dialogs` to return HTML when the `Accept` header indicates HTMX (or always, since the JSON form isn't used):

Replace the body of `list_dialogs`:

```python
@router.get("/dialogs", response_class=HTMLResponse)
async def list_dialogs(request: Request, q: str = "", exclude: str = "", limit: int = 20):
    cache = getattr(request.app.state, "dialog_cache", None)
    if cache is None or cache.count == 0:
        return HTMLResponse(
            '<div class="picker-empty">Список чатов не загружен — нажми 🔄</div>'
        )
    excluded_ids: set[int] = set()
    for tok in exclude.split(","):
        tok = tok.strip()
        if tok:
            try:
                excluded_ids.add(int(tok))
            except ValueError:
                pass
    items = cache.search(q=q, exclude=excluded_ids, limit=max(1, min(limit, 50)))
    return request.app.state.templates.TemplateResponse(
        request, "_picker_results.html", {"items": items},
    )
```

- [ ] **Step 3: Create `_picker_results.html`**

Create `src/telegram_news/web/templates/_picker_results.html`:

```html
{# Variables: items (list of CachedDialog) #}
{% if not items %}
  <div class="picker-empty">Ничего не найдено</div>
{% else %}
  {% for d in items %}
    {% set icon = '📢' if d.kind == 'channel' else '👥' %}
    {% set chip_html %}{% with peer=d, name='__NAME__', original='' %}{% include "_chip.html" %}{% endwith %}{% endset %}
    <div class="picker-result"
         tabindex="0"
         data-peer-id="{{ d.peer_id }}"
         data-chip-html="{{ chip_html | replace('__NAME__', '') | e }}">
      <span class="picker-result-icon">{{ icon }}</span>
      <span class="picker-result-title">{{ d.title }}</span>
      {% if d.username %}<small>@{{ d.username }}</small>{% endif %}
    </div>
  {% endfor %}
{% endif %}
```

This is fiddly. Let me simplify: instead of stuffing chip HTML into a data-attribute, the row just carries `data-peer-id` / `data-title` / `data-username` / `data-kind`, and the JS builds the chip via a small client-side template.

Replace the picker-results template with:

```html
{# Variables: items (list of CachedDialog) #}
{% if not items %}
  <div class="picker-empty">Ничего не найдено</div>
{% else %}
  {% for d in items %}
    {% set icon = '📢' if d.kind == 'channel' else '👥' %}
    <div class="picker-result"
         tabindex="0"
         data-peer-id="{{ d.peer_id }}"
         data-title="{{ d.title }}"
         data-username="{{ d.username or '' }}"
         data-kind="{{ d.kind }}">
      <span class="picker-result-icon">{{ icon }}</span>
      <span class="picker-result-title">{{ d.title }}</span>
      {% if d.username %}<small>@{{ d.username }}</small>{% endif %}
    </div>
  {% endfor %}
{% endif %}
```

And update the JS click handler to build the chip in-line:

Replace the click handler in `chat_picker.js`:

```javascript
  document.addEventListener("click", function (e) {
    const row = e.target.closest(".picker-result");
    if (!row) return;
    const picker = row.closest(".chat-picker");
    const name = picker.dataset.name;
    const mode = picker.dataset.mode;
    const chips = picker.querySelector(".chip-list");
    if (mode === "single") chips.innerHTML = "";

    const peerId = row.dataset.peerId;
    const title = row.dataset.title;
    const username = row.dataset.username;
    const kind = row.dataset.kind;
    const icon = kind === "channel" ? "📢" : "👥";
    const usernameHtml = username ? `<small class="chip-username">@${escapeHtml(username)}</small>` : "";

    chips.insertAdjacentHTML("beforeend", `
      <span class="chip" data-peer-id="${peerId}">
        <span class="chip-icon">${icon}</span>
        <span class="chip-title">${escapeHtml(title)}</span>
        ${usernameHtml}
        <button type="button" onclick="removeChip(this)" aria-label="Удалить">✕</button>
        <input type="hidden" name="${name}" value="${peerId}">
        <input type="hidden" name="${name}__original" value="">
        <input type="hidden" name="${name}__title" value="${escapeHtml(title)}">
      </span>
    `);

    const search = picker.querySelector(".picker-search");
    if (search) {
      search.value = "";
      picker.querySelector(".picker-results").innerHTML = "";
      search.focus();
    }
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
    );
  }
```

- [ ] **Step 4: Add `<script>` to `base.html`**

In `src/telegram_news/web/templates/base.html`, find the `<head>` or end of `<body>` and add (right before `</body>` is best):

```html
    <script src="/static/chat_picker.js"></script>
```

- [ ] **Step 5: Syntax check + restart**

```bash
python3 -m py_compile src/telegram_news/web/routes/dialogs.py
```

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_news/web/static/chat_picker.js \
        src/telegram_news/web/templates/_picker_results.html \
        src/telegram_news/web/templates/base.html \
        src/telegram_news/web/routes/dialogs.py
git commit -m "feat(web): client-side chip insertion + HTML picker results"
```

---

### Task 20: `group_form.html` — use the picker + override fieldset

**Files:**
- Modify: `src/telegram_news/web/templates/group_form.html`

- [ ] **Step 1: Replace the target-input block (lines 71–77)**

Replace:

```html
    <label>
      Канал-цель
      <input type="text" name="target" required
             placeholder="@my_crypto_digest или -1001234567890"
             value="{{ group.target if group else '' }}">
    </label>
```

with:

```html
    <label>
      Канал-цель (куда постить дайджест)
      {% set target_selected = [] %}
      {% if group and group.target %}
        {% set target_selected = [{'peer_id': group.target, 'title': (group.target_title or group.target), 'username': none, 'kind': 'channel', 'original': group.target}] %}
      {% endif %}
      {% with name='target_peer', mode='single', selected=target_selected %}
        {% include "_chat_picker.html" %}
      {% endwith %}
    </label>
```

(`group.target_title` is added in Task 21 from the route layer; for unmigrated groups it falls back to the raw target string.)

- [ ] **Step 2: Replace the channels-textarea block (lines 79–83)**

```html
  <label>
    Каналы-источники
    {% with name='channel_peers', mode='multi', selected=(group.resolved_channels if group else []) %}
      {% include "_chat_picker.html" %}
    {% endwith %}
  </label>
```

(`group.resolved_channels` is built by the route layer in Task 21.)

- [ ] **Step 3: Add the override fieldset after the instructions textarea (around line 94)**

```html
  <fieldset>
    <legend>Лимиты (опционально, переопределяют глобальные дефолты)</legend>
    <div class="grid">
      <label style="font-weight: 400;">
        <small>Сообщений на канал</small>
        <input type="number" min="1" name="max_messages_per_channel"
               placeholder="200"
               value="{{ group.max_messages_per_channel or '' if group else '' }}">
      </label>
      <label style="font-weight: 400;">
        <small>Дней назад</small>
        <input type="number" min="1" name="max_age_days"
               placeholder="2"
               value="{{ group.max_age_days or '' if group else '' }}">
      </label>
      <label style="font-weight: 400;">
        <small>Мин. длина сообщения</small>
        <input type="number" min="0" name="min_message_length"
               placeholder="20"
               value="{{ group.min_message_length if group and group.min_message_length is not none else '' }}">
      </label>
    </div>
  </fieldset>
```

- [ ] **Step 4: Commit**

```bash
git add src/telegram_news/web/templates/group_form.html
git commit -m "feat(web): group_form uses chat picker for source+target, adds limits fieldset"
```

---

### Task 21: `web/routes/groups.py` — accept new fields

**Files:**
- Modify: `src/telegram_news/web/routes/groups.py`

- [ ] **Step 1: Replace `_split_channels` with no-op (we now accept structured fields)**

Delete `_split_channels`.

- [ ] **Step 2: Replace `_build_group_from_form` signature and body**

```python
def _build_group_from_form(
    name: str,
    schedule_kind: str,
    cron: str,
    interval_hours: str,
    interval_anchor: str,
    interests: str,
    instructions: str,
    bot: str,
    target_peer: str,
    target_original: str,
    target_title: str,
    channel_peers: list[str],
    channel_originals: list[str],
    channel_titles: list[str],
    max_messages: str,
    max_age: str,
    min_length: str,
) -> tuple[Group | None, list[str], list[str], str | None]:
    """Returns (group, original_channels_parallel, display_titles_parallel, error)."""
    cron_v: str | None = None
    interval_v: float | None = None
    anchor_v: str | None = None

    if schedule_kind == "cron":
        cron = cron.strip()
        if not cron:
            return None, [], [], "Поле cron пустое"
        try:
            CronTrigger.from_crontab(cron)
        except ValueError as e:
            return None, [], [], f"Невалидный cron: {e}"
        cron_v = cron
    elif schedule_kind == "interval":
        if not interval_hours.strip():
            return None, [], [], "Поле interval_hours пустое"
        try:
            interval_v = float(interval_hours)
        except ValueError:
            return None, [], [], f"interval_hours должен быть числом, получено {interval_hours!r}"
        if interval_v <= 0:
            return None, [], [], "interval_hours должен быть > 0"
        if interval_anchor.strip():
            try:
                anchor_v = validate_anchor(interval_anchor.strip())
            except ValueError as e:
                return None, [], [], str(e)
    else:
        return None, [], [], f"Неизвестный schedule_kind: {schedule_kind!r}"

    if not channel_peers:
        return None, [], [], "Не выбран ни один источник"
    if len(channel_peers) != len(channel_originals) or len(channel_peers) != len(channel_titles):
        return None, [], [], "Внутренняя ошибка формы: длины списков не совпадают"

    if not name.strip():
        return None, [], [], "Имя группы пустое"
    if not bot.strip():
        return None, [], [], "Не выбран бот"
    if not target_peer.strip():
        return None, [], [], "Канал-цель не задан"

    def _maybe_int(s: str, ctx: str) -> int | None:
        s = s.strip()
        if not s:
            return None
        try:
            v = int(s)
        except ValueError:
            raise ValueError(f"{ctx}: ожидается целое число, получено {s!r}")
        if v < 0:
            raise ValueError(f"{ctx}: должно быть >= 0")
        return v

    try:
        max_msgs_v = _maybe_int(max_messages, "max_messages_per_channel")
        max_age_v = _maybe_int(max_age, "max_age_days")
        min_len_v = _maybe_int(min_length, "min_message_length")
    except ValueError as e:
        return None, [], [], str(e)

    group = Group(
        name=name.strip(),
        interests=interests,
        channels=channel_peers,
        bot=bot.strip(),
        target=target_peer.strip(),
        cron=cron_v,
        interval_hours=interval_v,
        interval_anchor=anchor_v,
        instructions=instructions.strip() or None,
        max_messages_per_channel=max_msgs_v,
        max_age_days=max_age_v,
        min_message_length=min_len_v,
    )
    return group, channel_originals, channel_titles, None
```

- [ ] **Step 3: Update both POST handlers to accept the new form fields**

Replace `new_submit`:

```python
@router.post("/new")
async def new_submit(
    request: Request,
    name: str = Form(...),
    schedule_kind: str = Form(...),
    cron: str = Form(""),
    interval_hours: str = Form(""),
    interval_anchor: str = Form(""),
    interests: str = Form(""),
    instructions: str = Form(""),
    bot: str = Form(...),
    target_peer: str = Form(...),
    target_peer__original: str = Form(""),
    target_peer__title: str = Form(""),
    channel_peers: list[str] = Form(default=[]),
    channel_peers__original: list[str] = Form(default=[]),
    channel_peers__title: list[str] = Form(default=[]),
    max_messages_per_channel: str = Form(""),
    max_age_days: str = Form(""),
    min_message_length: str = Form(""),
):
    cfg = request.app.state.cfg
    bots = bots_list(cfg.storage.db_path)

    group, originals, titles, err = _build_group_from_form(
        name, schedule_kind, cron, interval_hours, interval_anchor,
        interests, instructions, bot,
        target_peer, target_peer__original, target_peer__title,
        channel_peers, channel_peers__original, channel_peers__title,
        max_messages_per_channel, max_age_days, min_message_length,
    )
    if err:
        return _render_form(request, mode="new", group=None, bots=bots, error=err)

    if groups_get(cfg.storage.db_path, group.name):
        return _render_form(
            request, mode="new", group=None, bots=bots,
            error=f"Группа с именем '{group.name}' уже существует",
        )

    groups_upsert(
        cfg.storage.db_path, group,
        original_channels=originals, display_titles=titles,
    )
    request.app.state.scheduler_ctl.add_group(group)
    return RedirectResponse(
        f"/?flash={quote(f'Группа {group.name} создана')}", status_code=303,
    )
```

And `edit_submit` similarly (same parameter set, calls `groups_upsert` with originals + titles).

- [ ] **Step 4: Update `_render_form` to thread `resolved_channels` and `target_title` onto the `group` object**

Add a helper:

```python
def _annotate_group_for_form(db_path, group: Group | None) -> Group | None:
    if group is None:
        return None
    from ...db import channels_with_titles
    rows = channels_with_titles(db_path, group.name)
    resolved = []
    for r in rows:
        resolved.append({
            "peer_id": r["channel"],
            "title": r["display_title"] or r["channel"],
            "username": None,
            "kind": "channel",  # best-effort default for existing rows
            "original": r["channel"],
        })
    # Attach as attributes Jinja can read.
    group.resolved_channels = resolved
    group.target_title = group.target  # we don't yet cache target title
    return group
```

Use this in `_render_form` and in the `edit_form` GET handler. (Yes, we attach attributes to the dataclass — fine since Python dataclasses without `slots=True` allow it.)

- [ ] **Step 5: Syntax check**

```bash
python3 -m py_compile src/telegram_news/web/routes/groups.py
```

Expected: no output.

- [ ] **Step 6: Run all tests**

```bash
uv run pytest -q
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/telegram_news/web/routes/groups.py
git commit -m "feat(web): groups form accepts chip-list, parallel originals/titles, override limits"
```

---

### Task 22: Manual smoke test

**Files:** none (validation only)

This task does not change code; it produces evidence that the feature works end-to-end. The agent doing this task should report verbatim what they observed.

- [ ] **Step 1: Start the service**

```bash
uv run telegram-news serve
```

Open `http://127.0.0.1:8080`.

- [ ] **Step 2: Confirm authorisation; confirm dialog cache prewarmed**

In the logs, look for:

```
INFO  Dialog cache refreshed: <N> entries
```

Expected: N > 0 (the user's dialog count).

- [ ] **Step 3: Create a new group, add channels via picker**

- Click "Новая группа".
- In the source picker: type a fragment of one of your channel/group titles. Confirm rows appear, click one — chip appears.
- In the source picker: switch to "По ссылке" tab; paste `https://t.me/durov` (or any public username link) → click "Добавить" → chip appears with title.
- In the source picker: paste a link to a private group you're in (`https://t.me/c/<id>/<msg>`) → chip appears.
- In the source picker: paste an invite link `https://t.me/+abc123` → red error "invite links not supported".
- In the source picker: paste a username for a channel you're not in (e.g. `@some_unknown_chan`) → either resolves successfully (Telethon can resolve any public username) or shows a friendly error.
- In target: pick the channel where the bot will post.
- Set `max_messages_per_channel = 50`, `max_age_days = 1`, `min_message_length = 30` (override fields).
- Set bot, schedule, interests; submit.

- [ ] **Step 4: Verify DB state**

```bash
sqlite3 services/telegram-news/data/state.db \
  "SELECT name, max_messages_per_channel, max_age_days, min_message_length, target FROM groups"
sqlite3 services/telegram-news/data/state.db \
  "SELECT group_name, channel, display_title FROM group_channels ORDER BY group_name, position"
```

Expected: per-group values populated; channels stored as canonical `-100…` ids (and any unmigrated `@` rows from earlier groups still present, untouched). `display_title` non-NULL for new entries.

- [ ] **Step 5: Trigger a one-shot run**

In the UI, click "Run Now" on the new group. (Or `uv run telegram-news run-once --group <name>` in another terminal — but **only** if `serve` is stopped to avoid the session-file race noted in CLAUDE.md.)

Watch logs for:

```
INFO  Running group=<name> channels=<n>
INFO    -100…: <count> new messages
```

If the group target has been wired up correctly, the digest message arrives in the target channel. Open one of the message links from the digest body and confirm it leads to the right post (use a private message — should be `t.me/c/...`).

- [ ] **Step 6: Edit an existing legacy group**

If you have a pre-existing group with `@username` channels:

- Open it via "Edit".
- Confirm chips render with the raw `@username` as title (no display_title cached) and grey style.
- Don't change anything; submit.
- Re-open: still `@username`. **OK.**
- Now remove one chip and re-add the same chat via the picker. Submit.
- Open the DB: that one channel should now be stored as `-100…` and `display_title` populated.
- Cursor was preserved (no flood of old messages on next run): confirm by checking `channel_state` row `last_message_id` is unchanged.

- [ ] **Step 7: Document any anomalies**

Report any deviations from the expected behaviour. Common issues to watch for:
- Telethon `entity.id` math for `t.me/c/...` link generation may need adjusting if the link points to the wrong post.
- Sender names appearing for broadcast channels (should not).
- Dialog cache empty on UI load (cache prewarm did not run).

- [ ] **Step 8: Commit smoke-test notes (if any)**

If issues were discovered and fixed, commit each fix as a separate task following the same TDD-where-possible flow.

---

### Task 23: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Replace the "Operational gotchas" bullet about plain text**

The current bullet says system prompt enforces plain text. That's stale. Replace it:

```markdown
- **System prompt outputs Telegram HTML** (limited tags: `<b>`, `<i>`, `<u>`, `<s>`, `<a href="…">`, `<code>`, `<blockquote>`). `delivery.send_to_channel` posts with `parse_mode="HTML"` (verify in `delivery.py`). Don't switch to MarkdownV2 without escaping every special char per Telegram's rules.
```

- [ ] **Step 2: Add a new section after "Operational gotchas"**

```markdown
## Channel/peer storage

- A row in `group_channels.channel` holds **one of**:
  - canonical `-100…` numeric peer-id as a string (channels and supergroups, post-migration via the new chat picker),
  - `-<chat_id>` string (small legacy `Chat`),
  - or legacy `@username` (from before the picker; still works through `client.get_entity`).
- `tg._resolve_entity` decides which form a value is by inspecting its first character + magnitude.
- New entries from the picker always store the canonical numeric form plus a cached `display_title`.
- Cursor (`channel_state`) rename happens in the same transaction as `groups_upsert` when an edit replaces an old identifier with the canonical form, so a chat-rename does not re-fetch the world.

## Group chats vs broadcast channels

- `tg.fetch_new_messages` enables sender attribution for everything except broadcast channels (`entity.broadcast`).
- Skips: `msg.action` (system events) and short messages (`< min_length`) without URLs.
- Replies are kept and prefixed with `↳` so the LLM knows.

## Per-group overrides

`groups` table has nullable columns `max_messages_per_channel`, `max_age_days`, `min_message_length`. NULL → use `cfg.fetcher.*` (or 20 for `min_message_length`). Editable in the form's "Лимиты" fieldset.

## Chat picker / dialog cache

- `dialog_cache.DialogCache` is a singleton on `app.state`, populated via `iter_dialogs(limit=None)` after authorisation (lifespan + post-web-auth).
- Manual refresh: 🔄 button on the picker → `POST /api/dialogs/refresh`.
- The picker (`_chat_picker.html`) is reused for both group source-list (multi) and target field (single).
- Pasted links go through `resolve.parse_link` → `resolve.resolve(client, …)` → chip HTML.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md update for groups, picker, peer storage, overrides"
```

---

## Self-review notes (run before handoff)

The plan's self-review caught:

- **Spec coverage:** every section of the spec maps to a numbered task; no orphans. The "automatic migration of existing data" non-goal is honoured (Task 6 only adds columns; Task 9 migrates cursors only on user-initiated save).
- **Type consistency:** `ResolvedPeer` (resolve.py) and `CachedDialog` (dialog_cache.py) intentionally have parallel structure — both expose `peer_id, title, username, kind` so templates that take either work uniformly. The chip partial accepts a `peer` dict with that shape.
- **Field-name convention** for chips finalised as `name`, `name__original`, `name__title` — used consistently across templates, JS, and the `groups.py` form handler.
- **Telethon `entity.id` math for `t.me/c/...`** is flagged as needing a manual sanity-check during smoke (Task 22 step 5), because Telethon's id conventions for `Channel` differ from raw API ids; the code uses the post-`-100` form which is correct for Telethon `Channel` objects (not raw MTProto).
- **Project has no prior pytest** — Task 1 adds it; subsequent tasks use it where they can (parser, db migration, dialog cache filter); Telethon-touching code uses manual smoke tests in Task 22.
