ALTER TABLE regulation_state ADD COLUMN pending_event_id TEXT;
ALTER TABLE regulation_state ADD COLUMN pending_turn_id TEXT;
ALTER TABLE regulation_state ADD COLUMN pending_activity_id TEXT;
ALTER TABLE regulation_state ADD COLUMN pending_signal TEXT CHECK (pending_signal IN ('confused','frustrated','task-rejecting','off-task','requesting-help','requesting-pause'));
ALTER TABLE regulation_state ADD COLUMN pending_confidence_band TEXT CHECK (pending_confidence_band IN ('low','medium','high'));
ALTER TABLE regulation_state ADD COLUMN pending_strategy TEXT CHECK (pending_strategy IN ('repeat-instruction','simplify-language','give-ordered-hint','redirect-gently','validate-emotion','take-short-pause','cap-choice'));
ALTER TABLE regulation_state ADD COLUMN pending_ordinal INTEGER CHECK (pending_ordinal IS NULL OR pending_ordinal >= 1);
ALTER TABLE regulation_state ADD COLUMN pending_materialized INTEGER NOT NULL DEFAULT 0 CHECK (pending_materialized IN (0,1));

CREATE TABLE regulation_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES learning_sessions(session_id) ON DELETE CASCADE,
    activity_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    signal TEXT NOT NULL CHECK (signal IN ('confused','frustrated','task-rejecting','off-task','requesting-help','requesting-pause')),
    confidence_band TEXT NOT NULL CHECK (confidence_band IN ('low','medium','high')),
    strategy TEXT NOT NULL CHECK (strategy IN ('repeat-instruction','simplify-language','give-ordered-hint','redirect-gently','validate-emotion','take-short-pause','cap-choice')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    outcome TEXT NOT NULL DEFAULT 'unknown' CHECK (outcome IN ('answered','repeated_difficulty','stopped','unknown')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, turn_id),
    UNIQUE(session_id, activity_id, ordinal)
);

CREATE INDEX regulation_events_session_idx ON regulation_events(session_id, created_at, event_id);
CREATE INDEX regulation_events_activity_idx ON regulation_events(session_id, activity_id, ordinal);
