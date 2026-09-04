from math_tutor.domain.learning import LearningPlan, LearningSession, PresentationProfile
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository


def test_fresh_repository_reconstructs_provisioned_plan_session_and_versions(tmp_path):
    path = tmp_path / "reconstruction.db"
    migrate(path)
    repo = SQLiteTutoringRepository(path)
    plan = LearningPlan("learner-9", ("count", "add"), ("count",), PresentationProfile.for_age(9), "plan-9", 3)
    session = LearningSession.start(session_id="session-9", plan=plan)
    repo.save_learner("learner-9", curriculum_snapshot="objectives: [count, add]", curriculum_version="curriculum-v2")
    repo.save_plan(plan, policy_version="policy-v4")
    repo.save_session(session, profile_version=7)
    reopened = SQLiteTutoringRepository(path)
    assert reopened.load_plan("plan-9", 3) == plan
    assert reopened.load_state("session-9").session == session
    assert reopened.load_state("session-9").profile_version == 7
    snapshot = reopened.load_learner("learner-9")
    assert snapshot.curriculum_snapshot == "objectives: [count, add]"
    assert snapshot.curriculum_version == "curriculum-v2"
    assert reopened.load_plan_policy_version("plan-9", 3) == "policy-v4"
