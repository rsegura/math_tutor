ALTER TABLE evidence_clips ADD COLUMN deletion_reason TEXT;

CREATE TABLE audio_clip_write_intents(
 clip_id TEXT PRIMARY KEY,
 evidence_id TEXT NOT NULL UNIQUE,
 learner_id TEXT NOT NULL,
 session_id TEXT NOT NULL,
 consent_id TEXT NOT NULL REFERENCES audio_consents(consent_id),
 consent_snapshot_id TEXT NOT NULL REFERENCES session_audio_consent_snapshots(snapshot_id),
 duration_seconds REAL NOT NULL CHECK(duration_seconds > 0 AND duration_seconds <= 30),
 storage_key TEXT NOT NULL UNIQUE,
 captured_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(evidence_id,learner_id,session_id) REFERENCES evidence_records(evidence_id,learner_id,session_id)
);

CREATE TABLE audio_orphan_deletion_audit(
 storage_key TEXT PRIMARY KEY,
 deleted_at TEXT NOT NULL,
 reason TEXT NOT NULL
);
