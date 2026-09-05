CREATE TABLE legacy_review_action_payloads(
 command_id TEXT PRIMARY KEY REFERENCES legacy_review_command_identities(command_id),
 corrected_state TEXT NOT NULL
);

INSERT INTO legacy_review_action_payloads(command_id, corrected_state)
SELECT identity.command_id,
       json_extract(review.review_json,'$.fields.corrected_state.value')
FROM legacy_review_command_identities identity
JOIN therapist_reviews review
  ON review.review_id=identity.review_id
 AND review.version=identity.review_version
WHERE identity.action_kind='correct-skill-estimate'
  AND json_extract(review.review_json,'$."$type"')='correct-skill-estimate-review/v1'
  AND json_extract(review.review_json,'$.fields.corrected_state."$enum"')='competency-state/v1'
  AND json_extract(review.review_json,'$.fields.corrected_state.value') IS NOT NULL;

CREATE TRIGGER legacy_review_action_payloads_immutable_update
BEFORE UPDATE ON legacy_review_action_payloads
BEGIN SELECT RAISE(ABORT, 'legacy review action payload is immutable'); END;

CREATE TRIGGER legacy_review_action_payloads_immutable_delete
BEFORE DELETE ON legacy_review_action_payloads
BEGIN SELECT RAISE(ABORT, 'legacy review action payload is immutable'); END;
