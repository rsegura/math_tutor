import sqlite3

import pytest

from math_tutor.infrastructure.persistence.migrator import migrate


def test_failing_migration_does_not_leave_schema_or_version(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_broken.sql").write_text(
        "CREATE TABLE leaked(value TEXT); INSERT INTO missing(value) VALUES ('x');",
        encoding="utf-8",
    )
    database = tmp_path / "atomic.db"
    with pytest.raises(sqlite3.OperationalError):
        migrate(database, migration_dir=migrations)
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='leaked'").fetchone() is None
    assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone() is None
