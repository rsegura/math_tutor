ALTER TABLE learners ADD COLUMN pseudonym TEXT;
ALTER TABLE learners ADD COLUMN age_years INTEGER CHECK(age_years BETWEEN 6 AND 13);

CREATE TABLE provisioned_plans(
 plan_id TEXT NOT NULL, version INTEGER NOT NULL, learner_id TEXT NOT NULL REFERENCES learners(learner_id),
 adaptations_json TEXT NOT NULL, duration_minutes INTEGER NOT NULL, max_activities INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(plan_id,version)
);
CREATE UNIQUE INDEX one_plan_version_per_learner ON provisioned_plans(learner_id,version);

CREATE TABLE audio_consents(
 consent_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL REFERENCES learners(learner_id), plan_id TEXT NOT NULL,
 plan_version INTEGER NOT NULL, retention_days INTEGER NOT NULL CHECK(retention_days BETWEEN 1 AND 30),
 granted_at TEXT NOT NULL, revoked_at TEXT, version INTEGER NOT NULL
);
CREATE TABLE session_audio_consent_snapshots(
 snapshot_id TEXT PRIMARY KEY, consent_id TEXT NOT NULL REFERENCES audio_consents(consent_id),
 learner_id TEXT NOT NULL, session_id TEXT NOT NULL UNIQUE REFERENCES learning_sessions(session_id),
 plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL, retention_days INTEGER NOT NULL, granted_at TEXT NOT NULL
);
CREATE TABLE learner_join_codes(
 session_id TEXT PRIMARY KEY REFERENCES learning_sessions(session_id), code_hash TEXT NOT NULL UNIQUE,
 expires_at TEXT NOT NULL, consumed_at TEXT
);
CREATE TABLE consent_purge_audit(
 consent_id TEXT PRIMARY KEY REFERENCES audio_consents(consent_id), last_requested_at TEXT NOT NULL,
 request_count INTEGER NOT NULL DEFAULT 1
);
