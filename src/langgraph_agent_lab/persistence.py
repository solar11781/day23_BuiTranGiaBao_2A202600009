"""Checkpointer adapter."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any


def _sqlite_path(database_url: str | None) -> str:
    if not database_url:
        return "outputs/checkpoints.sqlite"
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    return database_url


def build_checkpointer(kind: str = "memory", database_url: str | None = None) -> Any | None:
    """Return a LangGraph checkpointer.

    Supported values are ``none``, ``memory``, ``sqlite``, and ``postgres``. SQLite uses
    a concrete sqlite3 connection, because current ``SqliteSaver.from_conn_string`` returns
    a context manager instead of a checkpointer instance.
    """
    resolved_kind = (os.getenv("CHECKPOINTER") or kind or "memory").lower()
    resolved_database_url = os.getenv("DATABASE_URL") or database_url

    if resolved_kind == "none":
        return None
    if resolved_kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    if resolved_kind == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise RuntimeError("SQLite checkpointer requires: pip install '.[sqlite]'") from exc

        db_path = Path(_sqlite_path(resolved_database_url))
        if db_path.parent != Path(""):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        saver = SqliteSaver(conn=conn)
        saver.setup()
        return saver
    if resolved_kind == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:
            raise RuntimeError("Postgres checkpointer requires: pip install '.[postgres]'") from exc
        if not resolved_database_url:
            raise ValueError("DATABASE_URL is required when checkpointer is postgres")
        return PostgresSaver.from_conn_string(resolved_database_url)
    raise ValueError(f"Unknown checkpointer kind: {kind}")
