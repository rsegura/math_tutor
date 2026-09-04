PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS learners(
 learner_id TEXT PRIMARY KEY, curriculum_snapshot TEXT NOT NULL, curriculum_version TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS curriculum_snapshots(
 learner_id TEXT NOT NULL REFERENCES learners(learner_id), curriculum_version TEXT NOT NULL,
 curriculum_snapshot TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(learner_id, curriculum_version)
);
CREATE TABLE IF NOT EXISTS learning_plans(
 plan_id TEXT NOT NULL, version INTEGER NOT NULL, learner_id TEXT NOT NULL REFERENCES learners(learner_id),
 plan_json TEXT NOT NULL, policy_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(plan_id, version)
);
CREATE TABLE IF NOT EXISTS learning_sessions(
 session_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL REFERENCES learners(learner_id), plan_id TEXT NOT NULL,
 plan_version INTEGER NOT NULL, session_json TEXT NOT NULL, version INTEGER NOT NULL, profile_version INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS activities(
 session_id TEXT NOT NULL REFERENCES learning_sessions(session_id), activity_id TEXT NOT NULL, activity_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(session_id, activity_id)
);
CREATE TABLE IF NOT EXISTS activity_progress(
 session_id TEXT NOT NULL REFERENCES learning_sessions(session_id), activity_id TEXT NOT NULL,
 progress_json TEXT NOT NULL, version INTEGER NOT NULL, PRIMARY KEY(session_id, activity_id)
);
CREATE TABLE IF NOT EXISTS observations(
 observation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES learning_sessions(session_id), learner_id TEXT NOT NULL,
 objective_id TEXT NOT NULL, observation_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS evidence_records(
 evidence_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL UNIQUE REFERENCES observations(observation_id), learner_id TEXT NOT NULL,
 objective_id TEXT NOT NULL, reason_for_retention TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS evidence_interpretations(
 evidence_id TEXT NOT NULL REFERENCES evidence_records(evidence_id), version INTEGER NOT NULL,
 interpretation TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(evidence_id, version)
);
CREATE TABLE IF NOT EXISTS skill_estimates(
 learner_id TEXT NOT NULL, objective_id TEXT NOT NULL, estimate_json TEXT NOT NULL, version INTEGER NOT NULL,
 PRIMARY KEY(learner_id, objective_id)
);
CREATE TABLE IF NOT EXISTS profile_change_proposals(
 proposal_id INTEGER PRIMARY KEY AUTOINCREMENT, learner_id TEXT NOT NULL, objective_id TEXT NOT NULL,
 proposal_json TEXT NOT NULL, estimate_version INTEGER NOT NULL, policy_version TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS therapist_reviews(
 review_id TEXT NOT NULL, version INTEGER NOT NULL, learner_id TEXT NOT NULL, session_id TEXT,
 review_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(review_id, version)
);
CREATE TABLE IF NOT EXISTS profile_revisions(
 revision_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
 revision_json TEXT NOT NULL, policy_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(learner_id, profile_version)
);
CREATE TABLE IF NOT EXISTS tutoring_events(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT NOT NULL, kind TEXT NOT NULL, session_id TEXT NOT NULL,
 objective_id TEXT, activity_id TEXT, detail TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS processed_commands(
 command_id TEXT PRIMARY KEY, command_fingerprint TEXT NOT NULL, result_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS evidence_clips(
 clip_id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL UNIQUE REFERENCES evidence_records(evidence_id), learner_id TEXT NOT NULL,
 session_id TEXT NOT NULL, duration_seconds REAL NOT NULL CHECK(duration_seconds > 0 AND duration_seconds <= 30),
 storage_key TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
