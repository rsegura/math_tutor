"""Small idempotent SQLite migration runner."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


_PUBLISHED_V2_SHA256 = "23a2c991a273acebd8708d587658ebfcfce4f38abf21661e94b8bcfe76763364"
_V2_INFERRED_PROPOSAL_SESSION = """COALESCE((SELECT o.session_id FROM evidence_records e JOIN observations o ON o.observation_id=e.observation_id
   WHERE e.evidence_id=json_extract(p.proposal_json,'$.fields.evidence_ids.\"$tuple\"[0]')),
   (SELECT session_id FROM learning_sessions s WHERE s.learner_id=p.learner_id ORDER BY created_at LIMIT 1))"""
_V2_EVENT_PROPOSAL_SESSION = """(SELECT CASE WHEN COUNT(*)=1 THEN MAX(event.session_id) END
   FROM tutoring_events event
   JOIN processed_commands command ON command.command_id=event.command_id
   JOIN learning_sessions owner ON owner.session_id=event.session_id
   WHERE event.kind='profile-change-proposed'
    AND owner.learner_id=p.learner_id
    AND event.objective_id=p.objective_id
    AND event.detail=json_extract(p.proposal_json,'$.fields.to_state.value')
    AND json_extract(command.result_json,'$.fields.payload')=json(p.proposal_json))"""
_V2_PROPOSAL_LINK_SELECT = """SELECT p.proposal_id,e.value,o.value,p.learner_id,p.session_id,p.objective_id
 FROM profile_change_proposals_v2 p"""
_V2_PROVENANCE_LINK_SELECT = """SELECT p.proposal_id,e.value,o.value,p.learner_id,
  (SELECT source.session_id FROM evidence_records_v2 source
   WHERE source.evidence_id=e.value AND source.observation_id=o.value),p.objective_id
 FROM profile_change_proposals_v2 p"""


def _upgrade_compatible_sql(sql: str, version: int, digest: str) -> str:
    """Adapt a published v2 migration without rewriting its historical file."""
    if version != 2 or digest != _PUBLISHED_V2_SHA256:
        return sql
    if _V2_INFERRED_PROPOSAL_SESSION not in sql or _V2_PROPOSAL_LINK_SELECT not in sql:
        raise RuntimeError("published v2 migration does not match compatibility contract")
    return sql.replace(
        _V2_INFERRED_PROPOSAL_SESSION,
        _V2_EVENT_PROPOSAL_SESSION,
    ).replace(
        _V2_PROPOSAL_LINK_SELECT,
        _V2_PROVENANCE_LINK_SELECT,
    )


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
                contents = migration.read_bytes()
                sql = contents.decode("utf-8")
                sql = _upgrade_compatible_sql(
                    sql, version, hashlib.sha256(contents).hexdigest()
                )
                for character in sql:
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
