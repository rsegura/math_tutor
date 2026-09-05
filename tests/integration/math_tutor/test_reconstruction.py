from math_tutor.application.ports import ActivityProgress, MutationBatch, StoredActivity
from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.domain.learning import CompetencyState, LearningPlan, LearningSession, PresentationProfile, SkillEstimate
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
    repo.save_session(session, profile_version=1)
    reopened = SQLiteTutoringRepository(path)
    assert reopened.load_plan("plan-9", 3) == plan
    assert reopened.load_state("session-9").session == session
    assert reopened.load_state("session-9").profile_version == 1
    snapshot = reopened.load_learner("learner-9")
    assert snapshot.curriculum_snapshot == "objectives: [count, add]"
    assert snapshot.curriculum_version == "curriculum-v2"
    assert reopened.load_plan_policy_version("plan-9", 3) == "policy-v4"


def test_full_session_aggregate_survives_repository_reopen(tmp_path):
    path = tmp_path / "aggregate.db"
    migrate(path)
    repo = SQLiteTutoringRepository(path)
    plan = LearningPlan("learner-9", ("count",), ("count",), PresentationProfile.for_age(9), "plan-9")
    session = LearningSession.start(session_id="session-9", plan=plan)
    repo.save_learner("learner-9", curriculum_snapshot="objectives: [count]", curriculum_version="curriculum-v2")
    repo.save_plan(plan, policy_version="policy-v4")
    repo.save_session(session, profile_version=1)
    repo.save_estimate(SkillEstimate("learner-9", "count", CompetencyState.NOT_OBSERVED))
    activity = Activity("template-1", "count", 1, "Cuenta", {"n": 1}, StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 1}), (), ())
    repo.commit_once(MutationBatch("bootstrap-activity", "fp", "session-9", 1, 1,
        CommandResult("bootstrap-activity", CommandStatus.APPLIED, "created"),
        activities=(StoredActivity("activity-9", activity),),
        activity_progress=(ActivityProgress("activity-9", 0, 0, 1),),
        expected_absent_activity_ids=("activity-9",)))
    aggregate = SQLiteTutoringRepository(path).load_session_aggregate("session-9")
    assert aggregate.session == session
    assert aggregate.plan == plan
    assert aggregate.curriculum_version == "curriculum-v2"
    assert aggregate.policy_version == "policy-v4"
    assert aggregate.estimates[0].objective_id == "count"
    assert aggregate.activity_progress[0].activity_id == "activity-9"
