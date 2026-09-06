-- Clips created before the consent-bound API cannot prove authorisation.
-- Make them immediately inaccessible and eligible for the startup sweeper.
UPDATE evidence_clips
SET deletion_state='deleting',
    deletion_claimed_at='1970-01-01T00:00:00+00:00',
    deletion_claim_id=NULL,
    expires_at='1970-01-01T00:00:00+00:00'
WHERE consent_id IS NULL OR consent_snapshot_id IS NULL;
