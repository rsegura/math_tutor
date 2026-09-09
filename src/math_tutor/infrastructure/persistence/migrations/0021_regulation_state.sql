CREATE TABLE regulation_state (
    session_id TEXT PRIMARY KEY REFERENCES learning_sessions(session_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    consecutive_turns INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_turns >= 0),
    activity_sequence INTEGER NOT NULL DEFAULT 0 CHECK (activity_sequence >= 0)
);

INSERT INTO regulation_state(session_id)
SELECT session_id FROM learning_sessions;
