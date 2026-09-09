DROP TRIGGER learner_support_receipts_immutable_update;
DROP TRIGGER learner_support_receipts_immutable_delete;
ALTER TABLE learner_support_receipts RENAME TO legacy_learner_support_receipts;

CREATE TABLE learner_support_receipts(
 session_id TEXT NOT NULL REFERENCES learning_sessions(session_id),
 turn_id TEXT NOT NULL,
 activity_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN (
  'hint','repeat','repeat-instruction','simplify-language','give-ordered-hint',
  'redirect-gently','validate-emotion','take-short-pause','cap-choice'
 )),
 speech TEXT NOT NULL CHECK(length(trim(speech)) > 0),
 regulation_revision INTEGER CHECK(regulation_revision IS NULL OR regulation_revision >= 1),
 decision_reason TEXT CHECK(decision_reason IS NULL OR length(trim(decision_reason)) > 0),
 PRIMARY KEY(session_id,turn_id),
 FOREIGN KEY(session_id,activity_id) REFERENCES activities(session_id,activity_id)
);

INSERT INTO learner_support_receipts(session_id,turn_id,activity_id,action,speech,regulation_revision,decision_reason)
SELECT session_id,turn_id,activity_id,action,speech,NULL,NULL
FROM legacy_learner_support_receipts;

DROP TABLE legacy_learner_support_receipts;

CREATE TRIGGER learner_support_receipts_immutable_update BEFORE UPDATE ON learner_support_receipts
BEGIN SELECT RAISE(ABORT,'learner support receipts are append only'); END;
CREATE TRIGGER learner_support_receipts_immutable_delete BEFORE DELETE ON learner_support_receipts
BEGIN SELECT RAISE(ABORT,'learner support receipts are append only'); END;
