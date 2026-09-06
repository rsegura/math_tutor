ALTER TABLE evidence_clips ADD COLUMN consent_id TEXT REFERENCES audio_consents(consent_id);
ALTER TABLE evidence_clips ADD COLUMN consent_snapshot_id TEXT REFERENCES session_audio_consent_snapshots(snapshot_id);
ALTER TABLE evidence_clips ADD COLUMN captured_at TEXT;
ALTER TABLE evidence_clips ADD COLUMN deletion_state TEXT NOT NULL DEFAULT 'live' CHECK(deletion_state IN ('live','deleting'));
ALTER TABLE evidence_clips ADD COLUMN deletion_claimed_at TEXT;
ALTER TABLE evidence_clips ADD COLUMN deletion_claim_id TEXT;

CREATE INDEX evidence_clips_expiry_live ON evidence_clips(expires_at) WHERE deletion_state='live';
CREATE INDEX evidence_clips_consent_live ON evidence_clips(consent_id) WHERE deletion_state='live';

CREATE TABLE audio_clip_deletion_tombstones(
 clip_id TEXT PRIMARY KEY,
 evidence_id TEXT NOT NULL,
 consent_id TEXT,
 reason TEXT NOT NULL,
 deleted_at TEXT NOT NULL
);
