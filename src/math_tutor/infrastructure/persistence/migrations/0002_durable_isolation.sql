CREATE TABLE learning_plans_v2(
 plan_id TEXT NOT NULL, version INTEGER NOT NULL, learner_id TEXT NOT NULL REFERENCES learners(learner_id),
 plan_json TEXT NOT NULL, policy_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(plan_id, version), UNIQUE(plan_id, version, learner_id)
);
CREATE TABLE learning_sessions_v2(
 session_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL REFERENCES learners(learner_id), plan_id TEXT NOT NULL,
 plan_version INTEGER NOT NULL, session_json TEXT NOT NULL, version INTEGER NOT NULL, profile_version INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(session_id, learner_id),
 FOREIGN KEY(plan_id, plan_version, learner_id) REFERENCES learning_plans_v2(plan_id, version, learner_id)
);
CREATE TABLE activities_v2(
 session_id TEXT NOT NULL, activity_id TEXT NOT NULL, objective_id TEXT NOT NULL, activity_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(session_id, activity_id),
 UNIQUE(session_id, activity_id, objective_id), FOREIGN KEY(session_id) REFERENCES learning_sessions_v2(session_id)
);
CREATE TABLE activity_progress_v2(
 session_id TEXT NOT NULL, activity_id TEXT NOT NULL, progress_json TEXT NOT NULL, version INTEGER NOT NULL,
 PRIMARY KEY(session_id, activity_id), FOREIGN KEY(session_id, activity_id) REFERENCES activities_v2(session_id, activity_id)
);
CREATE TABLE observations_v2(
 observation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, learner_id TEXT NOT NULL, objective_id TEXT NOT NULL,
 activity_id TEXT NOT NULL, observation_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(observation_id, learner_id, session_id, objective_id),
 FOREIGN KEY(session_id, learner_id) REFERENCES learning_sessions_v2(session_id, learner_id),
 FOREIGN KEY(session_id, activity_id, objective_id) REFERENCES activities_v2(session_id, activity_id, objective_id)
);
CREATE TABLE evidence_records_v2(
 evidence_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL UNIQUE, learner_id TEXT NOT NULL,
 session_id TEXT NOT NULL, objective_id TEXT NOT NULL, reason_for_retention TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(evidence_id, observation_id, learner_id, session_id, objective_id),
 UNIQUE(evidence_id, learner_id, session_id),
 FOREIGN KEY(observation_id, learner_id, session_id, objective_id) REFERENCES observations_v2(observation_id, learner_id, session_id, objective_id)
);
CREATE TABLE evidence_interpretations_v2(
 evidence_id TEXT NOT NULL REFERENCES evidence_records_v2(evidence_id), version INTEGER NOT NULL,
 interpretation TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(evidence_id, version)
);
CREATE TABLE skill_estimates_v2(
 learner_id TEXT NOT NULL REFERENCES learners(learner_id), objective_id TEXT NOT NULL,
 estimate_json TEXT NOT NULL, version INTEGER NOT NULL, PRIMARY KEY(learner_id, objective_id)
);
CREATE TABLE profile_change_proposals_v2(
 proposal_id INTEGER PRIMARY KEY AUTOINCREMENT, learner_id TEXT NOT NULL, session_id TEXT NOT NULL,
 objective_id TEXT NOT NULL, proposal_json TEXT NOT NULL, estimate_version INTEGER NOT NULL,
 policy_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(session_id, learner_id) REFERENCES learning_sessions_v2(session_id, learner_id),
 FOREIGN KEY(learner_id, objective_id) REFERENCES skill_estimates_v2(learner_id, objective_id)
);
CREATE TABLE proposal_evidence_v2(
 proposal_id INTEGER NOT NULL REFERENCES profile_change_proposals_v2(proposal_id), evidence_id TEXT NOT NULL,
 observation_id TEXT NOT NULL, learner_id TEXT NOT NULL, session_id TEXT NOT NULL, objective_id TEXT NOT NULL,
 PRIMARY KEY(proposal_id, evidence_id, observation_id),
 FOREIGN KEY(evidence_id, observation_id, learner_id, session_id, objective_id)
 REFERENCES evidence_records_v2(evidence_id, observation_id, learner_id, session_id, objective_id)
);
CREATE TABLE therapist_reviews_v2(
 review_id TEXT NOT NULL, version INTEGER NOT NULL, learner_id TEXT NOT NULL REFERENCES learners(learner_id), session_id TEXT,
 review_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(review_id, version),
 FOREIGN KEY(session_id, learner_id) REFERENCES learning_sessions_v2(session_id, learner_id)
);
CREATE TABLE profile_revisions_v2(
 revision_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL REFERENCES learners(learner_id), profile_version INTEGER NOT NULL,
 revision_json TEXT NOT NULL, policy_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(learner_id, profile_version)
);
CREATE TABLE tutoring_events_v2(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT NOT NULL, kind TEXT NOT NULL, session_id TEXT NOT NULL,
 objective_id TEXT, activity_id TEXT, detail TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(session_id) REFERENCES learning_sessions_v2(session_id),
 FOREIGN KEY(session_id, activity_id, objective_id) REFERENCES activities_v2(session_id, activity_id, objective_id)
);
CREATE TABLE evidence_clips_v2(
 clip_id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL UNIQUE, learner_id TEXT NOT NULL, session_id TEXT NOT NULL,
 duration_seconds REAL NOT NULL CHECK(duration_seconds > 0 AND duration_seconds <= 30), storage_key TEXT NOT NULL,
 expires_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(evidence_id, learner_id, session_id) REFERENCES evidence_records_v2(evidence_id, learner_id, session_id)
);

INSERT INTO learning_plans_v2 SELECT * FROM learning_plans;
INSERT INTO learning_sessions_v2 SELECT * FROM learning_sessions;
INSERT INTO activities_v2(session_id,activity_id,objective_id,activity_json,created_at)
 SELECT session_id,activity_id,json_extract(activity_json,'$.fields.objective_id'),activity_json,created_at FROM activities;
INSERT INTO activity_progress_v2 SELECT * FROM activity_progress;
INSERT INTO observations_v2(observation_id,session_id,learner_id,objective_id,activity_id,observation_json,version,created_at)
 SELECT observation_id,session_id,learner_id,objective_id,json_extract(observation_json,'$.fields.activity_id'),observation_json,version,created_at FROM observations;
INSERT INTO evidence_records_v2(evidence_id,observation_id,learner_id,session_id,objective_id,reason_for_retention,created_at)
 SELECT e.evidence_id,e.observation_id,e.learner_id,o.session_id,e.objective_id,e.reason_for_retention,e.created_at
 FROM evidence_records e JOIN observations o ON o.observation_id=e.observation_id;
INSERT INTO evidence_interpretations_v2 SELECT * FROM evidence_interpretations;
INSERT INTO skill_estimates_v2 SELECT * FROM skill_estimates;
INSERT INTO profile_change_proposals_v2(proposal_id,learner_id,session_id,objective_id,proposal_json,estimate_version,policy_version,created_at)
 SELECT p.proposal_id,p.learner_id,
  COALESCE((SELECT o.session_id FROM evidence_records e JOIN observations o ON o.observation_id=e.observation_id
   WHERE e.evidence_id=json_extract(p.proposal_json,'$.fields.evidence_ids."$tuple"[0]')),
   (SELECT session_id FROM learning_sessions s WHERE s.learner_id=p.learner_id ORDER BY created_at LIMIT 1)),
  p.objective_id,p.proposal_json,p.estimate_version,p.policy_version,p.created_at FROM profile_change_proposals p;
INSERT INTO proposal_evidence_v2(proposal_id,evidence_id,observation_id,learner_id,session_id,objective_id)
 SELECT p.proposal_id,e.value,o.value,p.learner_id,p.session_id,p.objective_id
 FROM profile_change_proposals_v2 p
 JOIN json_each(json_extract(p.proposal_json,'$.fields.evidence_ids."$tuple"')) e
 JOIN json_each(json_extract(p.proposal_json,'$.fields.observation_ids."$tuple"')) o ON o.key=e.key;
INSERT INTO therapist_reviews_v2 SELECT * FROM therapist_reviews;
INSERT INTO profile_revisions_v2 SELECT * FROM profile_revisions;
INSERT INTO tutoring_events_v2 SELECT * FROM tutoring_events;
INSERT INTO evidence_clips_v2 SELECT * FROM evidence_clips;

DROP TABLE evidence_clips;
DROP TABLE tutoring_events;
DROP TABLE profile_revisions;
DROP TABLE therapist_reviews;
DROP TABLE profile_change_proposals;
DROP TABLE skill_estimates;
DROP TABLE evidence_interpretations;
DROP TABLE evidence_records;
DROP TABLE observations;
DROP TABLE activity_progress;
DROP TABLE activities;
DROP TABLE learning_sessions;
DROP TABLE learning_plans;
ALTER TABLE learning_plans_v2 RENAME TO learning_plans;
ALTER TABLE learning_sessions_v2 RENAME TO learning_sessions;
ALTER TABLE activities_v2 RENAME TO activities;
ALTER TABLE activity_progress_v2 RENAME TO activity_progress;
ALTER TABLE observations_v2 RENAME TO observations;
ALTER TABLE evidence_records_v2 RENAME TO evidence_records;
ALTER TABLE evidence_interpretations_v2 RENAME TO evidence_interpretations;
ALTER TABLE skill_estimates_v2 RENAME TO skill_estimates;
ALTER TABLE profile_change_proposals_v2 RENAME TO profile_change_proposals;
ALTER TABLE proposal_evidence_v2 RENAME TO proposal_evidence;
ALTER TABLE therapist_reviews_v2 RENAME TO therapist_reviews;
ALTER TABLE profile_revisions_v2 RENAME TO profile_revisions;
ALTER TABLE tutoring_events_v2 RENAME TO tutoring_events;
ALTER TABLE evidence_clips_v2 RENAME TO evidence_clips;
