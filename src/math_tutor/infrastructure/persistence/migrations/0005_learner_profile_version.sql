-- Task 9 initially persisted discard reviews before recording the canonical
-- source session explicitly. Recover it from the immutable evidence owner.
UPDATE therapist_reviews
SET review_json=json_set(
 review_json,
 '$.fields.evidence_source_session_id',
 (SELECT evidence.session_id FROM evidence_records evidence
  WHERE evidence.evidence_id=json_extract(
   therapist_reviews.review_json, '$.fields.evidence_id'
  ))
)
WHERE json_extract(review_json, '$."$type"')='discard-evidence-review/v1'
  AND json_type(review_json, '$.fields.evidence_source_session_id') IS NULL;

CREATE TABLE learner_profile_versions(
 learner_id TEXT PRIMARY KEY REFERENCES learners(learner_id),
 version INTEGER NOT NULL CHECK(version >= 1)
);

INSERT INTO learner_profile_versions(learner_id, version)
SELECT learner.learner_id,
       MAX(
         1,
         COALESCE((SELECT MAX(session.profile_version)
                   FROM learning_sessions session
                   WHERE session.learner_id=learner.learner_id), 1),
         COALESCE((SELECT MAX(revision.profile_version)
                   FROM profile_revisions revision
                   WHERE revision.learner_id=learner.learner_id), 1)
       )
FROM learners learner;

UPDATE learning_sessions
SET profile_version=(
 SELECT version FROM learner_profile_versions profile
 WHERE profile.learner_id=learning_sessions.learner_id
);

CREATE TRIGGER learner_profile_versions_immutable_delete
BEFORE DELETE ON learner_profile_versions
BEGIN SELECT RAISE(ABORT, 'learner profile version is durable'); END;
