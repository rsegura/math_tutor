"""Small idempotent SQLite migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def migrate(database: str | Path, *, migration_dir: str | Path | None = None) -> None:
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    migrations = Path(migration_dir) if migration_dir is not None else Path(__file__).with_name("migrations")
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        for migration in sorted(migrations.glob("*.sql")):
            version = int(migration.name.split("_", 1)[0])
            if version in applied:
                continue
            connection.execute("BEGIN IMMEDIATE")
            try:
                statement = ""
                for character in migration.read_text(encoding="utf-8"):
                    statement += character
                    if sqlite3.complete_statement(statement):
                        if statement.strip():
                            connection.execute(statement)
                        statement = ""
                if statement.strip():
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    finally:
        connection.close()
