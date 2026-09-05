CREATE TABLE legacy_review_command_identities(
 command_id TEXT PRIMARY KEY REFERENCES processed_commands(command_id),
 legacy_fingerprint TEXT NOT NULL,
 review_id TEXT NOT NULL,
 review_version INTEGER NOT NULL,
 learner_id TEXT NOT NULL,
 session_id TEXT NOT NULL,
 source_session_id TEXT NOT NULL,
 action_kind TEXT NOT NULL,
 target_id TEXT NOT NULL,
 reason TEXT NOT NULL,
 expected_review_version INTEGER NOT NULL,
 expected_profile_version INTEGER NOT NULL,
 profile_revision_id TEXT NOT NULL REFERENCES profile_revisions(revision_id),
 FOREIGN KEY(review_id, review_version) REFERENCES therapist_reviews(review_id, version),
 FOREIGN KEY(session_id, learner_id) REFERENCES learning_sessions(session_id, learner_id),
 FOREIGN KEY(source_session_id, learner_id) REFERENCES learning_sessions(session_id, learner_id)
);

INSERT INTO legacy_review_command_identities(
 command_id,legacy_fingerprint,review_id,review_version,learner_id,
 session_id,source_session_id,action_kind,target_id,reason,
 expected_review_version,expected_profile_version,profile_revision_id
)
SELECT command.command_id,MIN(command.command_fingerprint),MIN(review.review_id),
       MIN(review.version),MIN(review.learner_id),MIN(review.session_id),
       MIN(CASE
         WHEN json_extract(review.review_json,'$."$type"')='discard-evidence-review/v1'
         THEN json_extract(review.review_json,'$.fields.evidence_source_session_id')
         ELSE review.session_id END),
       MIN(CASE
         WHEN json_extract(review.review_json,'$."$type"')='discard-evidence-review/v1'
         THEN 'discard-evidence' ELSE 'correct-skill-estimate' END),
       MIN(CASE
         WHEN json_extract(review.review_json,'$."$type"')='discard-evidence-review/v1'
         THEN json_extract(review.review_json,'$.fields.evidence_id')
         ELSE json_extract(review.review_json,'$.fields.objective_id') END),
       MIN(json_extract(review.review_json,'$.fields.reason')),
       MIN(review.version)-1,MIN(profile.profile_version)-1,
       MIN(profile.revision_id)
FROM processed_commands command
JOIN therapist_reviews review
  ON review.review_id=json_extract(command.result_json,'$.fields.review_id')
 AND review.version=json_extract(command.result_json,'$.fields.review_version')
JOIN profile_revisions profile
  ON profile.revision_id=command.command_id || ':profile'
 AND profile.learner_id=review.learner_id
WHERE json_extract(command.result_json,'$."$type"')='review-result/v1'
  AND json_extract(command.result_json,'$.fields.status."$enum"')='review-status/v1'
  AND json_extract(command.result_json,'$.fields.status.value')='applied'
  AND json_extract(review.review_json,'$."$type"') IN (
    'discard-evidence-review/v1','correct-skill-estimate-review/v1'
  )
  AND NOT EXISTS(
    SELECT 1 FROM review_command_identities current
    WHERE current.command_id=command.command_id
  )
GROUP BY command.command_id
HAVING COUNT(*)=1
   AND MIN(review.session_id) IS NOT NULL
   AND MIN(json_extract(review.review_json,'$.fields.reason')) IS NOT NULL;

CREATE TRIGGER legacy_review_command_identities_immutable_update
BEFORE UPDATE ON legacy_review_command_identities
BEGIN SELECT RAISE(ABORT, 'legacy review command identity is immutable'); END;

CREATE TRIGGER legacy_review_command_identities_immutable_delete
BEFORE DELETE ON legacy_review_command_identities
BEGIN SELECT RAISE(ABORT, 'legacy review command identity is immutable'); END;
