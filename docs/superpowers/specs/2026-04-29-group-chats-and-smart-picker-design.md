# Group chats + smart chat picker — design

Date: 2026-04-29
Service: `services/telegram-news`
Status: brainstormed, ready for plan

## Goals

1. Service summarizes **group chats and private channels** in addition to the public channels it already supports.
2. The form replaces the freeform `<textarea>` for channels with a **chat picker** that:
   - lets the user search across their own Telegram dialogs (cached from `client.iter_dialogs()`),
   - or paste any standard Telegram link (`@name`, `t.me/name`, `t.me/name/123`, `t.me/c/<id>/<msg>`) and resolves it to a canonical peer.
3. The same picker is used for the `target` field (where the digest is posted), in single-select mode.
4. Per-group runtime overrides for `max_messages_per_channel`, `max_age_days`, and a new `min_message_length` (noise filter).
5. Group-chat-specific noise reduction: drop system actions, drop short text-only messages (configurable threshold), keep replies.
6. Sender attribution: pass `sender_name` from group messages into the LLM prompt so the digest can reflect "who said what."

## Non-goals

- **Invite links** (`t.me/+abc`, `t.me/joinchat/abc`). The user will join chats from the phone first; the service does not call `ImportChatInviteRequest`. The picker error message tells the user to do this.
- **Sender allow/deny lists.** Deferred until basic noise filtering proves insufficient.
- **Bot-admin pre-flight check on `target`.** Out of scope; current "delivery raises on 400/403" behavior stays.
- **Automatic background migration of existing channels.** Existing `@username` rows keep working as-is; canonicalization happens lazily on next save through the new form.

## Architecture

The pipeline (`fetch → summarize → deliver`, per-group cron/interval) is unchanged. Changes are:

1. The form layer learns to produce **canonical peer ids**.
2. The fetch layer learns to **read groups**, attach sender names, and apply richer filtering.
3. The schema gains three nullable per-group override columns and one `display_title` column on `group_channels`.

### Component diagram

```
                ┌──────────────────────────────────────────────┐
                │ web/templates/_chat_picker.html              │
                │  (HTMX + tiny JS, single & multi-select)     │
                └────────┬───────────────────────┬─────────────┘
                         │                       │
                  GET /api/dialogs?q=     POST /api/resolve
                         │                       │
                ┌────────▼───────────────────────▼─────────────┐
                │ web/routes/dialogs.py (new)                  │
                │  - in-memory dialog cache                    │
                │  - URL parser → ResolvedPeer                 │
                └────────┬───────────────────────┬─────────────┘
                         │                       │
                ┌────────▼─────────┐    ┌────────▼─────────────┐
                │ tg.iter_dialogs  │    │ tg/resolve.py (new)  │
                │ (cached at boot) │    │ parse_link + lookup  │
                └──────────────────┘    └──────────────────────┘

  scheduler tick → runner.run_group → tg.fetch_new_messages (now group-aware)
                                       │
                                       ├─ filters msg.action, short text
                                       ├─ resolves sender_name in groups
                                       └─ generates t.me/c/... for private
```

## Component details

### 1. `resolve.py` (new, sibling of `tg.py`)

Pure parsing + thin Telethon lookup. No persistence.

**Public API:**

```python
@dataclass(frozen=True)
class ResolvedPeer:
    peer_id: int          # canonical: -100… for channel/megagroup, -<chat_id> for legacy chat
    title: str
    username: str | None  # None for private
    kind: Literal["channel", "megagroup", "chat"]

def parse_link(raw: str) -> ParseResult:
    """Returns ('username', 'name') | ('peer_id', -100…) | ('error', reason)."""

async def resolve(client: TelegramClient, raw: str) -> ResolvedPeer:
    """Parse + Telethon lookup. Raises with user-friendly message on failure."""
```

**Accepted forms:**

| Input | Parsed as |
|---|---|
| `@name`, `name`, `t.me/name`, `https://t.me/name`, `tg://resolve?domain=name` | `('username', 'name')` |
| `https://t.me/name/123` | `('username', 'name')` (msg-id stripped) |
| `https://t.me/c/1234567890/567` | `('peer_id', -1001234567890)` |
| `-1001234567890` (raw numeric) | `('peer_id', -1001234567890)` |
| `t.me/+abc`, `t.me/joinchat/abc` | error: "invite links not supported" |
| anything else | error: "could not parse" |

**Telethon lookup:**

- For `('username', X)` → `await client.get_entity(X if X.startswith('@') else f'@{X}')`.
- For `('peer_id', P)` where `P` is the canonical `-100…` form: `channel_id = abs(P) - 1_000_000_000_000` → `await client.get_entity(PeerChannel(channel_id))`. If session lacks access_hash, returns user-friendly "chat not visible to current session — add it via 'From my chats'".
- `kind` derived from `entity.broadcast` (true → `"channel"`), `entity.megagroup` (true → `"megagroup"`), else `"chat"` (small legacy chat — `entity` is a `telethon.tl.types.Chat`).

### 2. Dialog cache (`web/routes/dialogs.py`)

In-memory list, populated once via `await client.iter_dialogs(limit=None)` after the FastAPI lifespan confirms `is_user_authorized()`. Stored on `app.state.dialogs_cache`. No TTL; refreshed by an explicit `POST /api/dialogs/refresh` endpoint wired to a "🔄 Обновить список" button next to the picker.

**Endpoints:**

- `GET /api/dialogs?q=<substring>&exclude=<peer_ids>&limit=20` → JSON list of `{peer_id, title, username, kind}`. Substring match is case-insensitive on `title` + `username`. `exclude` lets the picker skip already-selected entries.
- `POST /api/resolve` (form-encoded `link=<raw>`) → JSON `{peer_id, title, username, kind}` or `{error: "..."}`.
- `POST /api/dialogs/refresh` → re-runs `iter_dialogs`, returns count.

### 3. Chat picker widget (`web/templates/_chat_picker.html`)

Reusable Jinja partial. Parameters are passed via a Jinja `{% with %}` wrapper at the call site:

```jinja
{% with name="channel_peers", mode="multi", selected=group.resolved_channels %}
  {% include "_chat_picker.html" %}
{% endwith %}
```

`selected` is a list of dicts: `{peer_id, original_value, title, username, kind}`.

**Tabs:** "Из моих чатов" (default) | "По ссылке". State stored in a hidden field; no client-side framework.

**"Из моих чатов" tab:**

- `<input>` with HTMX `hx-get="/api/dialogs"` `hx-trigger="keyup changed delay:200ms, focus"` `hx-target="#picker-{{ name }}-results"`.
- Results panel renders rows with type icon (📢 channel, 👥 megagroup/chat), title, `@username` if present.
- Click row → row morphs into a "chip" added to the selected list (multi mode) or replaces the current selection (single mode). Search input clears.

**"По ссылке" tab:**

- `<input>` + button "Добавить". Button submits `hx-post="/api/resolve"` → on success, server returns a chip partial; on error, returns the error text into a `<small class="error">`.

**Chip:**

```html
<span class="chip" data-peer-id="-1001234567890">
  📢 Crypto News Daily
  <small>@cryptonews</small>
  <button type="button" onclick="removeChip(this)">✕</button>
  <input type="hidden" name="channel_peers" value="-1001234567890">
  <input type="hidden" name="original_channels" value="@cryptonews">
  <input type="hidden" name="display_titles" value="Crypto News Daily">
</span>
```

Notes on form encoding:
- FastAPI `Form(...)` reads repeated names as a list when typed `list[str]`. We use repeated names without `[]` in the HTML attribute (`name="channel_peers"`) to keep both browser submission and FastAPI parsing simple.
- For `target` (single mode), names are `target_peer`, `target_original`, `target_title` (no list). Adding a new chip removes the old one in JS.

**JS (`web/static/chat_picker.js`)**: ~50 lines for keyboard ↑↓+Enter on the results panel and `removeChip()`. Wired up via a single `<script src="/static/chat_picker.js"></script>` in `base.html`. The auth-redirect middleware in `web/app.py` already exempts `/static/*` (line 92), so a `StaticFiles` mount under `/static` slots in cleanly. No external dependency.

### 4. Schema diff (`db.py`)

```sql
-- new nullable per-group overrides
ALTER TABLE groups ADD COLUMN max_messages_per_channel INTEGER;
ALTER TABLE groups ADD COLUMN max_age_days INTEGER;
ALTER TABLE groups ADD COLUMN min_message_length INTEGER;

-- cached title for picker chips on edit form
ALTER TABLE group_channels ADD COLUMN display_title TEXT;
```

`init_db` already inspects `PRAGMA table_info(...)` for `channel_state` migration; we extend the same pattern: for each new column, only ALTER if the column is missing. This keeps `init_db` idempotent across restarts.

Migration is pure ADD COLUMN, no data rewrite. Existing rows have all new columns NULL → fall back to `cfg.fetcher.*` and to "no display title" (UI shows raw `channel` value with a subdued style).

`group_channels.channel` semantics widen: a row now stores **one of**:

| Stored value | Meaning | Resolved with |
|---|---|---|
| `@name` or bare `name` (legacy, letter-prefixed) | username, unmigrated | `client.get_entity(value)` |
| `-1001234567890` (string, starts with `-100`, abs ≥ 1e12) | canonical channel/megagroup | `PeerChannel(abs(int(value)) - 1_000_000_000_000)` |
| `-12345` (string, starts with `-`, abs < 1e12) | legacy small `Chat` | `PeerChat(abs(int(value)))` |

The fetcher detects which form by the first character + numeric magnitude.

### 5. Cursor migration on group edit

When `groups_upsert` is called with channels that include canonical peer ids replacing previously-stored usernames, we need to preserve cursors so we don't re-fetch the world.

The edit form renders one chip per existing channel; each chip carries two hidden inputs:
- `channel_peers[]` — the canonical (or legacy) value to store now
- `original_channels[]` — the value that was in `group_channels.channel` when the form rendered, or empty string for chips added in this session

These two arrays travel as parallel lists (same indices). Server validates lengths match.

In `groups_upsert` (extended signature accepts `original_channels: list[str]`), before deleting the old `group_channels` rows we build a rename map:

```python
for new, old in zip(channel_peers, original_channels):
    if old and old != new:
        # rename channel_state row in place
        UPDATE channel_state SET channel=:new
          WHERE group_name=:group AND channel=:old
```

All work happens in the same SQLite transaction as the `group_channels` rewrite. Orphaned rows (channel removed from the group) are left untouched — the cursor stays around so re-adding doesn't refetch the world.

### 6. Fetcher changes (`tg.py`)

```python
@dataclass
class Message:
    channel: str            # peer_id as string ("-100…") or legacy "@name"
    channel_title: str
    message_id: int
    date: datetime
    text: str
    link: str
    sender_name: str | None  # None for broadcast channels
```

`fetch_new_messages(client, channel, last_id, max_messages, max_age_days, min_length)`:

1. Resolve entity by inspecting the `channel` string:
   - first char is `@` or a letter → `await client.get_entity(channel)`,
   - first char is `-` and `abs(int(channel)) >= 1_000_000_000_000` → `await client.get_entity(PeerChannel(abs(int(channel)) - 1_000_000_000_000))`,
   - first char is `-` and `abs(int(channel)) < 1_000_000_000_000` → `await client.get_entity(PeerChat(abs(int(channel))))`.
2. `is_broadcast = bool(getattr(entity, 'broadcast', False))`. Sender attribution is enabled for everything **except** broadcast channels (megagroups, supergroups, legacy chats all attribute).
3. For each message returned by `iter_messages`:
   - skip if `msg.action is not None` (system event: join/leave/pin/avatar/call)
   - `text = (msg.message or "").strip()`; skip if empty
   - skip if `len(text) < min_length` **and** `not re.search(r"https?://", text)` (short-but-link messages stay)
   - if `msg.reply_to is not None`, prefix `text` with `"↳ "` (LLM hint)
   - sender: if `is_broadcast`, `sender_name = None`; else `s = await msg.get_sender()`, formatted as `f"{s.first_name or ''} {s.last_name or ''}".strip()` or `s.title` (bots/channels-as-sender) or `"Аноним"` if `s is None`
   - link: if `getattr(entity, 'username', None)`: `f"https://t.me/{entity.username}/{msg.id}"`; else `f"https://t.me/c/{abs(entity.id)}/{msg.id}"` for channels (Telethon entity id for channels is already the post-`-100` form, no further math needed) or `f"https://t.me/c/{entity.id}/{msg.id}"` for legacy chats. *Implementation note:* verify the entity-id form during implementation by printing one example — Telethon's id conventions differ between `Channel` and `Chat` and the `t.me/c/` URL expects the bare internal id.

### 7. Per-group overrides plumbing

`config.Group` gains:

```python
max_messages_per_channel: int | None = None
max_age_days: int | None = None
min_message_length: int | None = None
```

In `runner.run_group`, effective values:

```python
max_msgs = group.max_messages_per_channel or cfg.fetcher.max_messages_per_channel
max_age  = group.max_age_days or cfg.fetcher.max_age_days
min_len  = group.min_message_length if group.min_message_length is not None else 20
```

`min_message_length` global default is `20`, **not** in `config.yaml` — hardcoded for now. If we ever need a knob, we add it later.

### 8. Summarizer changes (`summarize.py`)

The current `SYSTEM_PROMPT` outputs Telegram HTML (not plain text — `CLAUDE.md`'s note about plain bullets is stale). It stays HTML.

**Append one paragraph** to `SYSTEM_PROMPT`:

> Среди источников могут быть групповые чаты — у их сообщений в заголовке указан автор в формате `[Имя]`. Используй имена когда это помогает раскрыть контекст обсуждения (например: «Иван предложил X, Петя возразил»), опускай когда не нужно. Реплаи помечены символом ↳ в начале текста.

**Update `_format_messages_for_prompt`.** Currently:

```python
lines.append(f"[{m.channel} | {m.date:%Y-%m-%d %H:%M} | {m.link}]")
lines.append(m.text)
lines.append("---")
```

Becomes:

```python
header = f"[{m.channel_title} | {m.date:%Y-%m-%d %H:%M}"
if m.sender_name:
    header += f" | {m.sender_name}"
header += f" | {m.link}]"
lines.append(header)
lines.append(m.text)
lines.append("---")
```

Replacing `m.channel` (raw peer-id, useless to LLM) with `m.channel_title` is the substantive change. Sender slot is conditional.

### 9. Lifespan wiring (`web/app.py`)

After successful authorization (either at startup if already authed, or after web auth completes), kick off `await refresh_dialog_cache(client)` and store the result on `app.state.dialogs_cache`. If it fails, log and continue — the picker just shows "не удалось загрузить диалоги, попробуйте обновить".

## Data flow

**Adding a chat via picker:**

```
User types in picker → HTMX GET /api/dialogs?q=foo
  → server filters in-memory cache → returns top 20 → user clicks row
  → HTMX returns chip HTML → chip added to form, hidden input populated
  → form submitted → groups_upsert receives channel_peers[]=-1001234567890
                     and display_titles[]="Foo Chat"
  → row inserted into group_channels with channel="-1001234567890", display_title="Foo Chat"
```

**Adding a chat via paste:**

```
User pastes link → HTMX POST /api/resolve link=https://t.me/c/123/456
  → tg/resolve.parse_link → ('peer_id', -1001234567890)
  → tg/resolve.resolve → client.get_entity(PeerChannel(123)) → ResolvedPeer
  → server returns chip HTML → same flow as picker
```

**Scheduler tick:**

```
runner.run_group(group)
  → for ch in group.channels:
      cursor = get_last_message_id(group.name, ch)
      msgs = await fetch_new_messages(client, ch, cursor, group.max_msgs or cfg, ...)
        → resolve entity, iter_messages, filter, attribute sender, build links
  → summarize_group → LLM digest with sender names + reply markers
  → send_to_channel → bot posts to target
  → cursors updated
```

## Error handling

- **Resolve fails** (link parses but entity not found / no access_hash): picker shows red text under the input; form blocks submission until the row is removed or replaced.
- **Cache miss on `iter_dialogs`** (e.g. session lost auth between boot and form load): picker still works on "По ссылке" tab; "Из моих чатов" shows "нет данных, нажми 🔄".
- **Fetch fails on a single channel during a tick**: existing behavior preserved — the entire group is skipped (cursors not advanced) so we retry next tick.
- **Bot-admin failure on target delivery**: existing `RuntimeError` from `delivery.send_to_channel` propagates; logged. No new behavior.
- **Empty digest after filtering** (everything was sub-min-length): runner already advances cursors and skips delivery; no change.

## Testing

The repo has no test infra. Validation, in order:

1. Manual: `uv run telegram-news serve`, open form, search dialogs, paste links of all 5 supported forms, observe chips render correctly.
2. Manual: edit an existing group through the new form, verify cursor migration via `sqlite3 data/state.db "SELECT * FROM channel_state WHERE group_name='X'"`.
3. Manual: `uv run telegram-news run-once --group <noisy-dev-chat>` after raising its `max_messages_per_channel` to 2000, observe digest contains sender attributions and excludes short/system messages.
4. Manual: try adding an invite link `t.me/+abc` and confirm the user-facing error message.
5. `python3 -m py_compile src/telegram_news/**/*.py` for syntax sanity.

## Files touched

| File | Change |
|---|---|
| `src/telegram_news/tg.py` | extend `Message`, add filtering + sender lookup, group-aware link gen |
| `src/telegram_news/resolve.py` (new) | URL parser + `ResolvedPeer` lookup |
| `src/telegram_news/db.py` | ALTER TABLE migrations, cursor-rename in `groups_upsert` |
| `src/telegram_news/config.py` | three new nullable fields on `Group` |
| `src/telegram_news/runner.py` | thread per-group overrides into `fetch_new_messages` |
| `src/telegram_news/summarize.py` | updated system prompt + message format |
| `src/telegram_news/web/app.py` | dialogs-cache prewarm in lifespan post-auth |
| `src/telegram_news/web/routes/groups.py` | new form fields, accept `channel_peers[]` and override fields |
| `src/telegram_news/web/routes/dialogs.py` (new) | `/api/dialogs`, `/api/resolve`, `/api/dialogs/refresh` |
| `src/telegram_news/web/templates/group_form.html` | use `_chat_picker.html` for source list, single-mode for target, fieldset for overrides |
| `src/telegram_news/web/templates/_chat_picker.html` (new) | reusable picker partial |
| `src/telegram_news/web/templates/_chip.html` (new) | chip render partial (returned by `/api/resolve` and dialog click) |
| `src/telegram_news/web/static/chat_picker.js` (new) | ~50 lines: keyboard nav, `removeChip` |
| `CLAUDE.md` | update "channel" semantics note + new operational notes about picker / overrides |

## Open questions / future work (out of scope here)

- Sender allow/deny-list per group.
- Programmatic invite-link join.
- Background dialog-cache refresh on schedule (vs only manual button).
- Bot-admin precheck on target before save.
- Migration of existing `@username` rows on first authed boot (vs lazy on edit).
