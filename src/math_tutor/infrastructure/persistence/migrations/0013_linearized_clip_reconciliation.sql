ALTER TABLE audio_clip_write_intents ADD COLUMN reconciliation_state TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE audio_clip_write_intents ADD COLUMN reconciliation_claim_id TEXT;
ALTER TABLE audio_clip_write_intents ADD COLUMN reconciliation_claimed_at TEXT;

CREATE TABLE audio_orphan_delete_claims(
 storage_key TEXT PRIMARY KEY,
 claim_id TEXT NOT NULL,
 claimed_at TEXT NOT NULL
);
