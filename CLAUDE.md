# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A homelab daemon that periodically reads a curated set of Telegram channels (via a Telethon **user-session**, not Bot API), groups their messages, summarizes each group with an LLM through OpenRouter, and posts the resulting digest to dedicated Telegram channels via per-group bots. Everything is driven by a small FastAPI web UI on `127.0.0.1:8080` that runs in the same process as the scheduler.

## Common commands

```bash
# Install / update deps (uv, not poetry/pip)
uv pip install -e .

# Main entrypoint: web UI + APScheduler in one asyncio process
uv run telegram-news serve

# CLI fallbacks
uv run telegram-news auth                       # Telethon login from terminal
uv run telegram-news run-once                   # one-shot pass over all groups
uv run telegram-news run-once --group Crypto    # one-shot for a single group
uv run telegram-news import-yaml                # bootstrap groups.yaml + .env into SQLite

# Sanity-check syntax across all modules (no deps needed)
python3 -m py_compile src/telegram_news/*.py src/telegram_news/web/*.py src/telegram_news/web/routes/*.py
```

There are no tests in this repo. Validation is manual via the UI and `run-once`.

## Big-picture architecture

The pipeline is **fetch → summarize → deliver**, run per group on a per-group schedule. Groups, bots, and channel cursors live in **SQLite** (`data/state.db`); the UI is the source of truth. Yaml is only used once for initial bootstrap (`import-yaml`), then ignored at runtime.

### Process model (`telegram-news serve`)

`__main__.py:serve` calls `web.app.create_app(cfg)` and `uvicorn.run`. The FastAPI lifespan in `web/app.py` is the seam where everything is wired up:

1. `make_client(cfg)` + `await client.connect()` — Telethon connects but does **not** prompt for auth (no `start()`).
2. `AsyncIOScheduler` is created and wrapped in `SchedulerCtl` (`scheduler_ctl.py`).
3. If `is_user_authorized()`, scheduler is populated from DB groups; otherwise a middleware redirects every non-`/auth/*` request to `/auth` until login completes, at which point `auth.py:auth_sign_in` populates the scheduler.
4. `app.state.{cfg, client, scheduler, scheduler_ctl, templates, pending_auth}` are how routes access shared state.

Web routes use `asyncio.create_task(run_group(cfg, client, group))` for the **Run Now** button — fire-and-forget on the same Telethon client used by the scheduler.

### Data flow per scheduled tick

`scheduler_ctl.SchedulerCtl.add_group` registers a job that calls `runner.run_group(cfg, client, group)`. That function:

1. For each `group.channels`, reads `last_message_id` from `channel_state` keyed by **(group_name, channel)** and fetches via `tg.fetch_new_messages` (capped by `cfg.fetcher.max_messages_per_channel` / `max_age_days`).
2. If anything failed to fetch, the whole group is skipped — cursors are not advanced (next tick retries).
3. If no new messages, delivery is skipped silently (we don't spam empty digests).
4. Otherwise: `summarize.summarize_group` calls OpenRouter with system prompt + per-group `interests` + optional per-group `instructions`. Output is plain text with `•` bullets and inline URLs (no markdown/HTML — keeps Bot API delivery robust without escaping headaches).
5. `delivery.send_to_channel` posts via Bot API (httpx) to `group.target` using the bot token loaded from `bots` table by `group.bot`. Splits at paragraph boundaries if > 4096 chars.
6. `digests` row written; `(group_name, channel) → max_message_id` upserted into `channel_state`.

### Schedule model

Each group has **either** `cron` (5-field POSIX cron) **or** `interval_hours` (+ optional `interval_anchor: "HH:MM"`) — never both, validated at form submit and `import-yaml`. `scheduler_ctl.build_trigger` maps to `CronTrigger.from_crontab(...)` or `IntervalTrigger(hours=..., start_date=...)`. The interval branch exists specifically to express "every N hours from anchor time" without cron's month-boundary glitches at `*/2`.

### Why per-(group, channel) cursors

Same channel can live in multiple groups with different schedules. Global cursors would mean the more-frequent group eats messages before the slower one sees them. The composite-PK `channel_state` table makes each group's view of a channel independent.

### Auth flow (web)

Telethon's interactive auth is split across HTTP requests:

- `POST /auth/send-code` → `client.send_code_request(phone)`, store `(phone, phone_code_hash)` in `app.state.pending_auth`
- `POST /auth/sign-in` → `client.sign_in(phone, code, phone_code_hash=...)`; if `SessionPasswordNeededError`, set `pending_auth.needs_password=True` and re-render the password form; on next submit call `client.sign_in(password=...)`
- On success, `pending_auth` is cleared and `scheduler_ctl.populate_all(...)` runs

The CLI `auth` command exists as a fallback (uses `client.start()` which prompts in terminal).

## Files most worth knowing

- `src/telegram_news/runner.py` — the execution path; `run_group` is shared by both scheduler and Run Now
- `src/telegram_news/scheduler_ctl.py` — single owner of APScheduler; all add/update/remove of jobs goes here. `build_trigger` and `describe_schedule` live here too.
- `src/telegram_news/db.py` — schema (5 tables) + all CRUD. Always uses `PRAGMA foreign_keys = ON` per connection. `init_db` migrates the legacy single-cursor `channel_state` schema by dropping it.
- `src/telegram_news/config.py` — only **infra** config (schedule defaults, telegram, openrouter, fetcher, storage, web). `Bot` and `Group` dataclasses are reused as DTOs by the DB layer. Helpers `validate_anchor`, `validate_schedule`.
- `src/telegram_news/web/app.py` — FastAPI factory + lifespan + auth-redirect middleware. Templates path = `web/templates/`.
- `src/telegram_news/yaml_import.py` — only path that still reads `groups.yaml` and `*_BOT_TOKEN` env vars; idempotent UPSERT into DB.
- `src/telegram_news/daemon.py` — empty placeholder (the old `run_daemon` was replaced by the FastAPI lifespan). Safe to delete when no imports reference it.

## Operational gotchas

- **Bot tokens live in SQLite after `import-yaml`**, not in `.env`. The `.env` keeps only `TG_API_ID`, `TG_API_HASH`, `OPENROUTER_API_KEY`. Tokens are masked (`•••• + last 4`) in `/bots`.
- **Bot must be admin** of `group.target` with "Post Messages" permission, otherwise Bot API returns 400 / 403. `delivery.send_to_channel` raises a `RuntimeError` with the API response text.
- **Public channel target** = `@username`. **Private channel target** = numeric `-100…` id (get it from `https://api.telegram.org/bot<TOKEN>/getUpdates` after a manual post).
- **OpenRouter model id** lives in `config.yaml:openrouter.model` (default `deepseek/deepseek-v4-flash`). Change in one place. The OpenAI SDK is used with `base_url=https://openrouter.ai/api/v1`.
- **Telethon session file** is `data/session.session` (its own SQLite). It is **separate** from the app's `data/state.db`. Don't mix them.
- **Single-process assumption**: don't run `serve` and `run-once` simultaneously — Telethon's session file is SQLite and two clients on the same session race.
- **UI has no auth of its own.** It is meant to be reached over `127.0.0.1` only. Exposing publicly without a reverse-proxy/basic-auth in front is unsafe.
- **No CSRF protection.** Same reason — single-user localhost.
- **System prompt outputs Telegram HTML** (limited tags: `<b>`, `<i>`, `<u>`, `<s>`, `<a href="…">`, `<code>`, `<blockquote>`). `delivery.send_to_channel` posts with `parse_mode="HTML"` (verify in `delivery.py`). Don't switch to MarkdownV2 without escaping every special char per Telegram's rules.
- **cSpell warnings on Russian content** (interests/instructions, brand names like `deepseek`, `durov`) are noise — ignore.

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
