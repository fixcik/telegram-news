from __future__ import annotations

import asyncio
import logging

import typer
import uvicorn

from .config import load_config
from .db import init_db
from .runner import run_once
from .tg import make_client
from .web.app import create_app
from .yaml_import import import_yaml

app = typer.Typer(help="Telegram channels → grouped LLM digests")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command()
def auth() -> None:
    """One-time interactive Telethon login (phone → code → 2FA) via CLI."""
    _setup_logging()
    cfg = load_config()
    init_db(cfg.storage.db_path)

    async def _go() -> None:
        client = make_client(cfg)
        await client.start()
        me = await client.get_me()
        typer.echo(f"Authorized as {me.first_name} (@{me.username}, id={me.id})")
        await client.disconnect()

    asyncio.run(_go())


@app.command("run-once")
def run_once_cmd(
    group: str | None = typer.Option(
        None, "--group", "-g", help="Run only this group (by name)"
    ),
) -> None:
    """Run a fetch→summarize→deliver pass for all groups (or one), then exit."""
    _setup_logging()
    cfg = load_config()
    init_db(cfg.storage.db_path)

    async def _go() -> None:
        client = make_client(cfg)
        await client.start()
        try:
            await run_once(cfg, client, only_group=group)
        finally:
            await client.disconnect()

    asyncio.run(_go())


@app.command("import-yaml")
def import_yaml_cmd(
    groups_path: str = typer.Option(
        "groups.yaml", "--groups", help="Path to groups.yaml"
    ),
) -> None:
    """One-time bootstrap: import bots and groups from groups.yaml + .env into DB."""
    _setup_logging()
    cfg = load_config()
    init_db(cfg.storage.db_path)
    bots_n, groups_n, channels_n = import_yaml(cfg, groups_path)
    typer.echo(
        f"Imported: bots={bots_n} groups={groups_n} channels={channels_n}"
    )


@app.command()
def serve() -> None:
    """Run web UI + scheduler in a single process on cfg.web.host:port."""
    _setup_logging()
    cfg = load_config()
    web_app = create_app(cfg)
    uvicorn.run(
        web_app,
        host=cfg.web.host,
        port=cfg.web.port,
        log_level="info",
    )


if __name__ == "__main__":
    app()
