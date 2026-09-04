CREATE TABLE proposal_evidence_v3(
 proposal_id INTEGER NOT NULL REFERENCES profile_change_proposals(proposal_id), evidence_id TEXT NOT NULL,
 observation_id TEXT NOT NULL, learner_id TEXT NOT NULL, source_session_id TEXT NOT NULL, objective_id TEXT NOT NULL,
 PRIMARY KEY(proposal_id, evidence_id, observation_id),
 FOREIGN KEY(evidence_id, observation_id, learner_id, source_session_id, objective_id)
 REFERENCES evidence_records(evidence_id, observation_id, learner_id, session_id, objective_id)
);

INSERT INTO proposal_evidence_v3(
 proposal_id,evidence_id,observation_id,learner_id,source_session_id,objective_id
)
 SELECT proposal_id,evidence_id,observation_id,learner_id,session_id,objective_id
 FROM proposal_evidence;

DROP TABLE proposal_evidence;
ALTER TABLE proposal_evidence_v3 RENAME TO proposal_evidence;
