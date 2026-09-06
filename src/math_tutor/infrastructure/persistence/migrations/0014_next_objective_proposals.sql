CREATE TABLE next_objective_proposals(
 proposal_id TEXT PRIMARY KEY,
 learner_id TEXT NOT NULL,
 source_session_id TEXT NOT NULL,
 source_plan_id TEXT NOT NULL,
 source_plan_version INTEGER NOT NULL,
 objective_id TEXT NOT NULL,
 rationale TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(learner_id,source_session_id,source_plan_id,source_plan_version,objective_id),
 FOREIGN KEY(source_session_id,learner_id) REFERENCES learning_sessions(session_id,learner_id),
 FOREIGN KEY(source_plan_id,source_plan_version) REFERENCES learning_plans(plan_id,version)
);

CREATE TABLE next_objective_proposal_evidence(
 proposal_id TEXT NOT NULL REFERENCES next_objective_proposals(proposal_id),
 position INTEGER NOT NULL CHECK(position >= 0),
 evidence_id TEXT NOT NULL,
 learner_id TEXT NOT NULL,
 evidence_session_id TEXT NOT NULL,
 PRIMARY KEY(proposal_id,position),
 UNIQUE(proposal_id,evidence_id),
 FOREIGN KEY(evidence_id,learner_id,evidence_session_id) REFERENCES evidence_records(evidence_id,learner_id,session_id)
);

CREATE TABLE next_objective_decisions(
 proposal_id TEXT NOT NULL REFERENCES next_objective_proposals(proposal_id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 command_id TEXT NOT NULL UNIQUE,
 learner_id TEXT NOT NULL REFERENCES learners(learner_id),
 status TEXT NOT NULL CHECK(status IN ('approved','rejected')),
 reason TEXT NOT NULL,
 expected_plan_version INTEGER NOT NULL,
 resulting_plan_version INTEGER,
 decided_at TEXT NOT NULL,
 PRIMARY KEY(proposal_id,revision)
);

CREATE TRIGGER next_objective_proposals_immutable_update BEFORE UPDATE ON next_objective_proposals
BEGIN SELECT RAISE(ABORT,'next objective proposals are append only'); END;
CREATE TRIGGER next_objective_proposals_immutable_delete BEFORE DELETE ON next_objective_proposals
BEGIN SELECT RAISE(ABORT,'next objective proposals are append only'); END;
CREATE TRIGGER next_objective_decisions_immutable_update BEFORE UPDATE ON next_objective_decisions
BEGIN SELECT RAISE(ABORT,'next objective decisions are append only'); END;
CREATE TRIGGER next_objective_decisions_immutable_delete BEFORE DELETE ON next_objective_decisions
BEGIN SELECT RAISE(ABORT,'next objective decisions are append only'); END;

CREATE TRIGGER next_objective_proposal_owner_insert
BEFORE INSERT ON next_objective_proposals WHEN NOT EXISTS(
 SELECT 1 FROM learning_plans p WHERE p.plan_id=NEW.source_plan_id AND p.version=NEW.source_plan_version AND p.learner_id=NEW.learner_id
)
BEGIN SELECT RAISE(ABORT,'next objective proposal plan owner mismatch'); END;

CREATE TRIGGER next_objective_decision_owner_insert
BEFORE INSERT ON next_objective_decisions WHEN NOT EXISTS(
 SELECT 1 FROM next_objective_proposals p WHERE p.proposal_id=NEW.proposal_id AND p.learner_id=NEW.learner_id
)
BEGIN SELECT RAISE(ABORT,'next objective decision owner mismatch'); END;
