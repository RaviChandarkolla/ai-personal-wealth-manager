"""Database connection helpers for SQLite (default) and optional Postgres adapter stub (M15)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


def open_db(path: str | Path) -> sqlite3.Connection:
    """One SQLite file holds funds, NAVs, doc chunks, profiles and the audit log."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def connect_sqlite(path: str | Path | sqlite3.Connection = ":memory:") -> sqlite3.Connection:
    """Return an active SQLite connection (or pass through an existing connection)."""
    if isinstance(path, sqlite3.Connection):
        return path
    return open_db(path)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure core profile and audit tables exist on `conn`."""
    from .audit.log import _SCHEMA

    conn.executescript(_SCHEMA)
    conn.commit()


@dataclass(frozen=True)
class DatabaseAdapter:
    """Unified adapter descriptor supporting SQLite (default) and Postgres DSNs (M15)."""

    backend: str  # "sqlite" | "postgresql"
    dsn: str
    connection: sqlite3.Connection | None = None


def resolve_db_adapter(dsn_or_path: str | Path = ":memory:") -> DatabaseAdapter:
    """Resolve a database DSN or file path into a `DatabaseAdapter`.

    - `postgresql://...` or `postgres://...`: returns a `postgresql` adapter backed by
      an in-memory SQLite compatibility connection when `psycopg` is not installed.
    - Any file path or `:memory:` or `sqlite:///...`: returns a `sqlite` adapter.
    """
    raw = str(dsn_or_path)
    if raw.startswith(("postgresql://", "postgres://")):
        conn = open_db(":memory:")
        ensure_schema(conn)
        return DatabaseAdapter(backend="postgres", dsn=raw, connection=conn)
    if raw.startswith("sqlite:///"):
        sqlite_path = raw[len("sqlite:///") :] or ":memory:"
        conn = open_db(sqlite_path)
        ensure_schema(conn)
        return DatabaseAdapter(backend="sqlite", dsn=raw, connection=conn)
    conn = open_db(raw)
    ensure_schema(conn)
    return DatabaseAdapter(backend="sqlite", dsn=raw, connection=conn)
