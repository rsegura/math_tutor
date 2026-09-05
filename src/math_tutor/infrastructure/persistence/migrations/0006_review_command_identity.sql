CREATE TABLE review_command_identities(
 command_id TEXT PRIMARY KEY REFERENCES processed_commands(command_id),
 command_fingerprint TEXT NOT NULL,
 review_id TEXT NOT NULL,
 learner_id TEXT NOT NULL REFERENCES learners(learner_id),
 session_id TEXT NOT NULL,
 source_session_id TEXT NOT NULL,
 action_kind TEXT NOT NULL,
 target_id TEXT NOT NULL,
 FOREIGN KEY(session_id, learner_id) REFERENCES learning_sessions(session_id, learner_id),
 FOREIGN KEY(source_session_id, learner_id) REFERENCES learning_sessions(session_id, learner_id)
);

CREATE TRIGGER review_command_identities_immutable_update
BEFORE UPDATE ON review_command_identities
BEGIN SELECT RAISE(ABORT, 'review command identity is immutable'); END;

CREATE TRIGGER review_command_identities_immutable_delete
BEFORE DELETE ON review_command_identities
BEGIN SELECT RAISE(ABORT, 'review command identity is immutable'); END;
