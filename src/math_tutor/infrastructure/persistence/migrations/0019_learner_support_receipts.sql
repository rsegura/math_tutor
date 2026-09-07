CREATE TABLE learner_support_receipts(
 session_id TEXT NOT NULL REFERENCES learning_sessions(session_id),
 turn_id TEXT NOT NULL,
 activity_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('hint','repeat')),
 speech TEXT NOT NULL CHECK(length(trim(speech)) > 0),
 PRIMARY KEY(session_id,turn_id),
 FOREIGN KEY(session_id,activity_id) REFERENCES activities(session_id,activity_id)
);
CREATE TRIGGER learner_support_receipts_immutable_update BEFORE UPDATE ON learner_support_receipts
BEGIN SELECT RAISE(ABORT,'learner support receipts are append only'); END;
CREATE TRIGGER learner_support_receipts_immutable_delete BEFORE DELETE ON learner_support_receipts
BEGIN SELECT RAISE(ABORT,'learner support receipts are append only'); END;
