CREATE TABLE learner_learning_state_versions(
 learner_id TEXT PRIMARY KEY REFERENCES learners(learner_id),
 version INTEGER NOT NULL CHECK(version >= 1)
);
INSERT INTO learner_learning_state_versions(learner_id,version)
SELECT learner_id,1 FROM learners;
CREATE TRIGGER learner_learning_state_version_on_learner AFTER INSERT ON learners
BEGIN INSERT INTO learner_learning_state_versions(learner_id,version) VALUES(NEW.learner_id,1); END;
CREATE TRIGGER learner_learning_state_versions_immutable_delete BEFORE DELETE ON learner_learning_state_versions
BEGIN SELECT RAISE(ABORT,'learner learning state version is durable'); END;

DROP TRIGGER next_objective_proposals_immutable_update;
DROP TRIGGER next_objective_proposals_immutable_delete;
DROP TRIGGER next_objective_decisions_immutable_update;
DROP TRIGGER next_objective_decisions_immutable_delete;
DROP TRIGGER next_objective_proposal_owner_insert;
DROP TRIGGER next_objective_profile_version_required_insert;
DROP TRIGGER next_objective_decision_owner_insert;
DROP TRIGGER next_objective_decision_profile_required_insert;
ALTER TABLE next_objective_proposals ADD COLUMN source_learning_state_version INTEGER;
ALTER TABLE next_objective_decisions ADD COLUMN expected_learning_state_version INTEGER;
UPDATE next_objective_proposals
SET source_learning_state_version=(SELECT version FROM learner_learning_state_versions s WHERE s.learner_id=next_objective_proposals.learner_id)
WHERE stale_reason IS NULL
 AND NOT EXISTS(SELECT 1 FROM next_objective_decisions d WHERE d.proposal_id=next_objective_proposals.proposal_id)
 AND source_profile_version=(SELECT version FROM learner_profile_versions p WHERE p.learner_id=next_objective_proposals.learner_id)
 AND (source_plan_id,source_plan_version)=(SELECT plan_id,version FROM provisioned_plans p WHERE p.learner_id=next_objective_proposals.learner_id ORDER BY version DESC LIMIT 1)
 AND NOT EXISTS(SELECT 1 FROM next_objective_proposal_evidence pe LEFT JOIN evidence_records e ON e.evidence_id=pe.evidence_id AND e.learner_id=next_objective_proposals.learner_id WHERE pe.proposal_id=next_objective_proposals.proposal_id AND e.evidence_id IS NULL);
UPDATE next_objective_proposals SET stale_reason=COALESCE(stale_reason,'legacy-learning-state-unverifiable')
WHERE NOT EXISTS(SELECT 1 FROM next_objective_decisions d WHERE d.proposal_id=next_objective_proposals.proposal_id)
 AND source_learning_state_version IS NULL;

ALTER TABLE next_objective_proposal_evidence RENAME TO next_objective_proposal_evidence_pre18;
ALTER TABLE next_objective_decisions RENAME TO next_objective_decisions_pre18;
ALTER TABLE next_objective_proposals RENAME TO next_objective_proposals_pre18;
CREATE TABLE next_objective_proposals(
 proposal_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, source_session_id TEXT NOT NULL,
 source_plan_id TEXT NOT NULL, source_plan_version INTEGER NOT NULL, objective_id TEXT NOT NULL,
 rationale TEXT NOT NULL, created_at TEXT NOT NULL, source_profile_version INTEGER, stale_reason TEXT,
 source_learning_state_version INTEGER,
 UNIQUE(learner_id,source_session_id,source_plan_id,source_plan_version,source_profile_version,source_learning_state_version,objective_id),
 FOREIGN KEY(source_session_id,learner_id) REFERENCES learning_sessions(session_id,learner_id),
 FOREIGN KEY(source_plan_id,source_plan_version) REFERENCES learning_plans(plan_id,version));
CREATE TABLE next_objective_proposal_evidence(
 proposal_id TEXT NOT NULL REFERENCES next_objective_proposals(proposal_id), position INTEGER NOT NULL CHECK(position >= 0),
 evidence_id TEXT NOT NULL, learner_id TEXT NOT NULL, evidence_session_id TEXT NOT NULL,
 PRIMARY KEY(proposal_id,position), UNIQUE(proposal_id,evidence_id),
 FOREIGN KEY(evidence_id,learner_id,evidence_session_id) REFERENCES evidence_records(evidence_id,learner_id,session_id));
CREATE TABLE next_objective_decisions(
 proposal_id TEXT NOT NULL REFERENCES next_objective_proposals(proposal_id), revision INTEGER NOT NULL CHECK(revision >= 1),
 command_id TEXT NOT NULL UNIQUE, learner_id TEXT NOT NULL REFERENCES learners(learner_id), status TEXT NOT NULL CHECK(status IN ('approved','rejected')),
 reason TEXT NOT NULL, expected_plan_version INTEGER NOT NULL, resulting_plan_version INTEGER, decided_at TEXT NOT NULL,
 expected_revision INTEGER NOT NULL DEFAULT 0, expected_profile_version INTEGER, expected_learning_state_version INTEGER,
 PRIMARY KEY(proposal_id,revision));
INSERT INTO next_objective_proposals SELECT * FROM next_objective_proposals_pre18;
INSERT INTO next_objective_proposal_evidence SELECT * FROM next_objective_proposal_evidence_pre18;
INSERT INTO next_objective_decisions SELECT * FROM next_objective_decisions_pre18;
DROP TABLE next_objective_proposal_evidence_pre18;
DROP TABLE next_objective_decisions_pre18;
DROP TABLE next_objective_proposals_pre18;

CREATE TRIGGER next_objective_proposals_immutable_update BEFORE UPDATE ON next_objective_proposals BEGIN SELECT RAISE(ABORT,'next objective proposals are append only'); END;
CREATE TRIGGER next_objective_proposals_immutable_delete BEFORE DELETE ON next_objective_proposals BEGIN SELECT RAISE(ABORT,'next objective proposals are append only'); END;
CREATE TRIGGER next_objective_decisions_immutable_update BEFORE UPDATE ON next_objective_decisions BEGIN SELECT RAISE(ABORT,'next objective decisions are append only'); END;
CREATE TRIGGER next_objective_decisions_immutable_delete BEFORE DELETE ON next_objective_decisions BEGIN SELECT RAISE(ABORT,'next objective decisions are append only'); END;
CREATE TRIGGER next_objective_proposal_owner_insert BEFORE INSERT ON next_objective_proposals WHEN NOT EXISTS(SELECT 1 FROM learning_plans p WHERE p.plan_id=NEW.source_plan_id AND p.version=NEW.source_plan_version AND p.learner_id=NEW.learner_id) BEGIN SELECT RAISE(ABORT,'next objective proposal plan owner mismatch'); END;
CREATE TRIGGER next_objective_profile_version_required_insert BEFORE INSERT ON next_objective_proposals WHEN NEW.source_profile_version IS NULL OR NEW.source_profile_version < 1 OR NEW.source_learning_state_version IS NULL OR NEW.source_learning_state_version < 1 OR NEW.stale_reason IS NOT NULL BEGIN SELECT RAISE(ABORT,'next objective proposal requires current snapshot versions'); END;
CREATE TRIGGER next_objective_decision_owner_insert BEFORE INSERT ON next_objective_decisions WHEN NOT EXISTS(SELECT 1 FROM next_objective_proposals p WHERE p.proposal_id=NEW.proposal_id AND p.learner_id=NEW.learner_id) BEGIN SELECT RAISE(ABORT,'next objective decision owner mismatch'); END;
CREATE TRIGGER next_objective_decision_profile_required_insert BEFORE INSERT ON next_objective_decisions WHEN NEW.expected_profile_version IS NULL OR NEW.expected_profile_version < 1 OR NEW.expected_learning_state_version IS NULL OR NEW.expected_learning_state_version < 1 BEGIN SELECT RAISE(ABORT,'next objective decision requires snapshot versions'); END;

CREATE TRIGGER learning_state_observation_insert AFTER INSERT ON observations BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=NEW.learner_id; END;
CREATE TRIGGER learning_state_observation_update AFTER UPDATE ON observations BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=NEW.learner_id; END;
CREATE TRIGGER learning_state_observation_delete AFTER DELETE ON observations BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=OLD.learner_id; END;
CREATE TRIGGER learning_state_evidence_insert AFTER INSERT ON evidence_records BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=NEW.learner_id; END;
CREATE TRIGGER learning_state_evidence_update AFTER UPDATE ON evidence_records BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=NEW.learner_id; END;
CREATE TRIGGER learning_state_evidence_delete AFTER DELETE ON evidence_records BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=OLD.learner_id; END;
CREATE TRIGGER learning_state_interpretation_insert AFTER INSERT ON evidence_interpretations BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=(SELECT learner_id FROM evidence_records WHERE evidence_id=NEW.evidence_id); END;
CREATE TRIGGER learning_state_interpretation_update AFTER UPDATE ON evidence_interpretations BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=(SELECT learner_id FROM evidence_records WHERE evidence_id=NEW.evidence_id); END;
CREATE TRIGGER learning_state_interpretation_delete AFTER DELETE ON evidence_interpretations BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=(SELECT learner_id FROM evidence_records WHERE evidence_id=OLD.evidence_id); END;
CREATE TRIGGER learning_state_estimate_insert AFTER INSERT ON skill_estimates BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=NEW.learner_id; END;
CREATE TRIGGER learning_state_estimate_update AFTER UPDATE ON skill_estimates BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=NEW.learner_id; END;
CREATE TRIGGER learning_state_estimate_delete AFTER DELETE ON skill_estimates BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=OLD.learner_id; END;
CREATE TRIGGER learning_state_review_insert AFTER INSERT ON therapist_reviews BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=NEW.learner_id; END;
CREATE TRIGGER learning_state_profile_revision_insert AFTER INSERT ON profile_revisions BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=NEW.learner_id; END;
CREATE TRIGGER learning_state_profile_version_update AFTER UPDATE ON learner_profile_versions BEGIN UPDATE learner_learning_state_versions SET version=version+1 WHERE learner_id=NEW.learner_id; END;
