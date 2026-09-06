DROP TRIGGER next_objective_proposals_immutable_update;
DROP TRIGGER next_objective_proposals_immutable_delete;
DROP TRIGGER next_objective_proposal_owner_insert;
DROP TRIGGER next_objective_profile_version_required_insert;
DROP TRIGGER next_objective_decisions_immutable_update;
DROP TRIGGER next_objective_decisions_immutable_delete;
DROP TRIGGER next_objective_decision_owner_insert;
DROP TRIGGER next_objective_decision_profile_required_insert;

ALTER TABLE next_objective_proposal_evidence RENAME TO next_objective_proposal_evidence_pre17;
ALTER TABLE next_objective_decisions RENAME TO next_objective_decisions_pre17;
ALTER TABLE next_objective_proposals RENAME TO next_objective_proposals_pre17;

CREATE TABLE next_objective_proposals(
 proposal_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, source_session_id TEXT NOT NULL,
 source_plan_id TEXT NOT NULL, source_plan_version INTEGER NOT NULL, objective_id TEXT NOT NULL,
 rationale TEXT NOT NULL, created_at TEXT NOT NULL, source_profile_version INTEGER, stale_reason TEXT,
 UNIQUE(learner_id,source_session_id,source_plan_id,source_plan_version,source_profile_version,objective_id),
 FOREIGN KEY(source_session_id,learner_id) REFERENCES learning_sessions(session_id,learner_id),
 FOREIGN KEY(source_plan_id,source_plan_version) REFERENCES learning_plans(plan_id,version)
);
CREATE TABLE next_objective_proposal_evidence(
 proposal_id TEXT NOT NULL REFERENCES next_objective_proposals(proposal_id),
 position INTEGER NOT NULL CHECK(position >= 0), evidence_id TEXT NOT NULL,
 learner_id TEXT NOT NULL, evidence_session_id TEXT NOT NULL,
 PRIMARY KEY(proposal_id,position), UNIQUE(proposal_id,evidence_id),
 FOREIGN KEY(evidence_id,learner_id,evidence_session_id) REFERENCES evidence_records(evidence_id,learner_id,session_id)
);
CREATE TABLE next_objective_decisions(
 proposal_id TEXT NOT NULL REFERENCES next_objective_proposals(proposal_id), revision INTEGER NOT NULL CHECK(revision >= 1),
 command_id TEXT NOT NULL UNIQUE, learner_id TEXT NOT NULL REFERENCES learners(learner_id),
 status TEXT NOT NULL CHECK(status IN ('approved','rejected')), reason TEXT NOT NULL,
 expected_plan_version INTEGER NOT NULL, resulting_plan_version INTEGER, decided_at TEXT NOT NULL,
 expected_revision INTEGER NOT NULL DEFAULT 0, expected_profile_version INTEGER,
 PRIMARY KEY(proposal_id,revision)
);

INSERT INTO next_objective_proposals SELECT * FROM next_objective_proposals_pre17;
INSERT INTO next_objective_proposal_evidence SELECT * FROM next_objective_proposal_evidence_pre17;
INSERT INTO next_objective_decisions SELECT * FROM next_objective_decisions_pre17;
DROP TABLE next_objective_proposal_evidence_pre17;
DROP TABLE next_objective_decisions_pre17;
DROP TABLE next_objective_proposals_pre17;

CREATE TRIGGER next_objective_proposals_immutable_update BEFORE UPDATE ON next_objective_proposals
BEGIN SELECT RAISE(ABORT,'next objective proposals are append only'); END;
CREATE TRIGGER next_objective_proposals_immutable_delete BEFORE DELETE ON next_objective_proposals
BEGIN SELECT RAISE(ABORT,'next objective proposals are append only'); END;
CREATE TRIGGER next_objective_decisions_immutable_update BEFORE UPDATE ON next_objective_decisions
BEGIN SELECT RAISE(ABORT,'next objective decisions are append only'); END;
CREATE TRIGGER next_objective_decisions_immutable_delete BEFORE DELETE ON next_objective_decisions
BEGIN SELECT RAISE(ABORT,'next objective decisions are append only'); END;
CREATE TRIGGER next_objective_proposal_owner_insert BEFORE INSERT ON next_objective_proposals WHEN NOT EXISTS(
 SELECT 1 FROM learning_plans p WHERE p.plan_id=NEW.source_plan_id AND p.version=NEW.source_plan_version AND p.learner_id=NEW.learner_id)
BEGIN SELECT RAISE(ABORT,'next objective proposal plan owner mismatch'); END;
CREATE TRIGGER next_objective_profile_version_required_insert BEFORE INSERT ON next_objective_proposals
WHEN NEW.source_profile_version IS NULL OR NEW.source_profile_version < 1 OR NEW.stale_reason IS NOT NULL
BEGIN SELECT RAISE(ABORT,'next objective proposal requires a current profile version'); END;
CREATE TRIGGER next_objective_decision_owner_insert BEFORE INSERT ON next_objective_decisions WHEN NOT EXISTS(
 SELECT 1 FROM next_objective_proposals p WHERE p.proposal_id=NEW.proposal_id AND p.learner_id=NEW.learner_id)
BEGIN SELECT RAISE(ABORT,'next objective decision owner mismatch'); END;
CREATE TRIGGER next_objective_decision_profile_required_insert BEFORE INSERT ON next_objective_decisions
WHEN NEW.expected_profile_version IS NULL OR NEW.expected_profile_version < 1
BEGIN SELECT RAISE(ABORT,'next objective decision requires a profile version'); END;
