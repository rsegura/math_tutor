DROP TRIGGER next_objective_proposals_immutable_update;

ALTER TABLE next_objective_proposals ADD COLUMN source_profile_version INTEGER;
ALTER TABLE next_objective_proposals ADD COLUMN stale_reason TEXT;
ALTER TABLE next_objective_decisions ADD COLUMN expected_profile_version INTEGER;

UPDATE next_objective_proposals
SET source_profile_version=(SELECT version FROM learner_profile_versions v WHERE v.learner_id=next_objective_proposals.learner_id)
WHERE NOT EXISTS(SELECT 1 FROM next_objective_decisions d WHERE d.proposal_id=next_objective_proposals.proposal_id)
  AND (SELECT COUNT(*) FROM learner_profile_versions v WHERE v.learner_id=next_objective_proposals.learner_id)=1
  AND (
    (SELECT version FROM learner_profile_versions v WHERE v.learner_id=next_objective_proposals.learner_id)=1
    OR (
      (SELECT COUNT(*) FROM profile_revisions r WHERE r.learner_id=next_objective_proposals.learner_id)
        =(SELECT version-1 FROM learner_profile_versions v WHERE v.learner_id=next_objective_proposals.learner_id)
      AND NOT EXISTS(
        SELECT 1 FROM profile_revisions r
        WHERE r.learner_id=next_objective_proposals.learner_id
          AND datetime(r.created_at)>datetime(next_objective_proposals.created_at)
      )
    )
  );

UPDATE next_objective_proposals
SET stale_reason='legacy-profile-version-unverifiable'
WHERE source_profile_version IS NULL;

CREATE TRIGGER next_objective_proposals_immutable_update BEFORE UPDATE ON next_objective_proposals
BEGIN SELECT RAISE(ABORT,'next objective proposals are append only'); END;

CREATE TRIGGER next_objective_profile_version_required_insert
BEFORE INSERT ON next_objective_proposals
WHEN NEW.source_profile_version IS NULL OR NEW.source_profile_version < 1 OR NEW.stale_reason IS NOT NULL
BEGIN SELECT RAISE(ABORT,'next objective proposal requires a current profile version'); END;

CREATE TRIGGER next_objective_decision_profile_required_insert
BEFORE INSERT ON next_objective_decisions
WHEN NEW.expected_profile_version IS NULL OR NEW.expected_profile_version < 1
BEGIN SELECT RAISE(ABORT,'next objective decision requires a profile version'); END;
