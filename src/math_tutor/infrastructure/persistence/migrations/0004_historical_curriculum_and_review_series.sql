CREATE TABLE migration_guard_v4(ok INTEGER NOT NULL CHECK(ok = 1));

-- Frozen v1 databases kept the same authoritative snapshot on learners but did
-- not necessarily mirror it into curriculum_snapshots.
INSERT OR IGNORE INTO curriculum_snapshots(
 learner_id, curriculum_version, curriculum_snapshot
)
SELECT learner_id, curriculum_version, curriculum_snapshot FROM learners;

-- A pre-v4 plan can only be attributed honestly when its learner has exactly one
-- immutable curriculum snapshot. Ambiguous databases must be repaired explicitly.
INSERT INTO migration_guard_v4(ok)
SELECT CASE WHEN NOT EXISTS(
 SELECT 1 FROM learning_plans plan
 WHERE (SELECT COUNT(*) FROM curriculum_snapshots snapshot
        WHERE snapshot.learner_id = plan.learner_id) <> 1
) THEN 1 ELSE 0 END;

ALTER TABLE learning_plans ADD COLUMN curriculum_version TEXT;
UPDATE learning_plans
SET curriculum_version = (
 SELECT snapshot.curriculum_version FROM curriculum_snapshots snapshot
 WHERE snapshot.learner_id = learning_plans.learner_id
);

CREATE TRIGGER learning_plans_curriculum_required_insert
BEFORE INSERT ON learning_plans WHEN NEW.curriculum_version IS NULL
BEGIN SELECT RAISE(ABORT, 'learning plan curriculum version is required'); END;
CREATE TRIGGER learning_plans_curriculum_required_update
BEFORE UPDATE OF curriculum_version ON learning_plans WHEN NEW.curriculum_version IS NULL
BEGIN SELECT RAISE(ABORT, 'learning plan curriculum version is required'); END;
CREATE TRIGGER learning_plans_curriculum_known_insert
BEFORE INSERT ON learning_plans WHEN NOT EXISTS(
 SELECT 1 FROM curriculum_snapshots snapshot
 WHERE snapshot.learner_id=NEW.learner_id
   AND snapshot.curriculum_version=NEW.curriculum_version
)
BEGIN SELECT RAISE(ABORT, 'learning plan curriculum snapshot does not exist'); END;
CREATE TRIGGER learning_plans_curriculum_known_update
BEFORE UPDATE OF learner_id,curriculum_version ON learning_plans WHEN NOT EXISTS(
 SELECT 1 FROM curriculum_snapshots snapshot
 WHERE snapshot.learner_id=NEW.learner_id
   AND snapshot.curriculum_version=NEW.curriculum_version
)
BEGIN SELECT RAISE(ABORT, 'learning plan curriculum snapshot does not exist'); END;
CREATE TRIGGER curriculum_snapshot_referenced_delete
BEFORE DELETE ON curriculum_snapshots WHEN EXISTS(
 SELECT 1 FROM learning_plans plan
 WHERE plan.learner_id=OLD.learner_id
   AND plan.curriculum_version=OLD.curriculum_version
)
BEGIN SELECT RAISE(ABORT, 'curriculum snapshot is referenced by a learning plan'); END;

CREATE TABLE therapist_review_series(
 review_id TEXT PRIMARY KEY,
 learner_id TEXT NOT NULL REFERENCES learners(learner_id),
 session_id TEXT,
 latest_version INTEGER NOT NULL CHECK(latest_version >= 1),
 FOREIGN KEY(session_id, learner_id) REFERENCES learning_sessions(session_id, learner_id)
);

INSERT INTO migration_guard_v4(ok)
SELECT CASE WHEN NOT EXISTS(
 SELECT review_id FROM therapist_reviews
 GROUP BY review_id
 HAVING COUNT(DISTINCT learner_id) <> 1
    OR COUNT(DISTINCT COALESCE(session_id, char(0))) <> 1
    OR MIN(version) <> 1 OR COUNT(*) <> MAX(version)
) THEN 1 ELSE 0 END;

INSERT INTO therapist_review_series(review_id, learner_id, session_id, latest_version)
SELECT review_id, MIN(learner_id), MIN(session_id), MAX(version)
FROM therapist_reviews GROUP BY review_id;

CREATE TRIGGER therapist_review_series_contract_insert
BEFORE INSERT ON therapist_reviews WHEN NOT EXISTS(
 SELECT 1 FROM therapist_review_series series
 WHERE series.review_id=NEW.review_id
   AND series.learner_id=NEW.learner_id
   AND series.session_id IS NEW.session_id
   AND series.latest_version=NEW.version
)
BEGIN SELECT RAISE(ABORT, 'therapist review violates its series contract'); END;
CREATE TRIGGER therapist_review_series_owner_immutable
BEFORE UPDATE OF learner_id,session_id ON therapist_review_series
BEGIN SELECT RAISE(ABORT, 'therapist review series owner is immutable'); END;
CREATE TRIGGER therapist_review_series_immutable_delete
BEFORE DELETE ON therapist_review_series
BEGIN SELECT RAISE(ABORT, 'therapist review series is immutable'); END;
CREATE TRIGGER therapist_reviews_immutable_update
BEFORE UPDATE ON therapist_reviews
BEGIN SELECT RAISE(ABORT, 'therapist reviews are append only'); END;
CREATE TRIGGER therapist_reviews_immutable_delete
BEFORE DELETE ON therapist_reviews
BEGIN SELECT RAISE(ABORT, 'therapist reviews are append only'); END;

DROP TABLE migration_guard_v4;
