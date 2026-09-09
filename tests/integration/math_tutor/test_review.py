from dataclasses import replace
from pathlib import Path

from math_tutor.application.review import (
    CorrectSkillEstimate,
    CorrectSkillEstimateReview,
    DiscardEvidenceReview,
    ProfileRecalculation,
    ReviewResult,
    DiscardEvidence,
    ReviewStatus,
    TherapistReviewService,
)
from math_tutor.application.summary import SummaryService
from math_tutor.application.ports import MutationBatch, StoredActivity
from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import EvidenceRecord, Observation, ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.learning import (
    AssistanceThreshold,
    CompetencyState,
    LearningPlan,
    LearningSession,
    PresentationProfile,
    ProgressionPolicy,
    SkillEstimate,
    ProposedProfileChange,
)
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from math_tutor.infrastructure.persistence.repositories import _dump


def policy():
    return ProgressionPolicy(tuple(
        AssistanceThreshold(state, 0) for state in (
            CompetencyState.EXPLORING, CompetencyState.WITH_INTENSIVE_HELP,
            CompetencyState.WITH_LIGHT_HELP, CompetencyState.INDEPENDENT,
            CompetencyState.GENERALIZED,
        )
    ), min_successes=2, min_distinct_activities=2)


def repository(tmp_path, *, migration_dir=None):
    path = tmp_path / "review.db"
    migrate(path, migration_dir=migration_dir)
    repo = SQLiteTutoringRepository(path)
    repo.save_learner("learner-1", curriculum_snapshot="curriculum", curriculum_version="v1")
    plan = LearningPlan("learner-1", ("add",), ("add",), PresentationProfile.for_age(8), "plan-1")
    repo.save_plan(plan, policy_version=policy().version)
    session = LearningSession.start(session_id="session-1", plan=plan)
    repo.save_session(session, profile_version=1)
    initial = SkillEstimate(
        "learner-1", "add", CompetencyState.EXPLORING, version=2,
        supporting_evidence_ids=("evidence-1", "evidence-2"),
        supporting_observation_ids=("observation-1", "observation-2"),
    )
    repo.save_estimate(initial)
    for index in (1, 2):
        activity = Activity(
            "template", "add", 1, "Suma", {"index": index},
            StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": index}), (), (),
        )
        observation = Observation(
            f"observation-{index}", "learner-1", "session-1", "add", f"activity-{index}",
            ObservationOutcome.CORRECT, .95, 0, TranscriptionReliabilityPolicy(.7), str(index),
        )
        retained = EvidenceRecord.initial(
            evidence_id=f"evidence-{index}", learner_id="learner-1", observation=observation,
            interpretation="respuesta independiente", reason_for_retention="progression",
        )
        proposal = ()
        if index == 2:
            proposal = (ProposedProfileChange(
                "learner-1", "add", CompetencyState.EXPLORING,
                CompetencyState.WITH_INTENSIVE_HELP,
                ("evidence-1", "evidence-2"),
                ("observation-1", "observation-2"), 2, policy().version,
            ),)
        repo.commit_once(MutationBatch(
            f"answer-{index}", f"fp-{index}", "session-1", 1, 1,
            CommandResult(f"answer-{index}", CommandStatus.APPLIED, "saved"),
            activities=(StoredActivity(f"activity-{index}", activity),),
            observations=(observation,), evidence=(retained,),
            profile_change_proposals=proposal,
            expected_absent_activity_ids=(f"activity-{index}",),
        ))
    return repo


def add_second_session(repo):
    plan = repo.load_plan("plan-1", 1)
    session = LearningSession.start(session_id="session-2", plan=plan)
    repo.save_session(session, profile_version=1)
    activity = Activity(
        "template", "add", 1, "Suma", {"index": 3},
        StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 3}), (), (),
    )
    observation = Observation(
        "observation-3", "learner-1", "session-2", "add", "activity-3",
        ObservationOutcome.CORRECT, .95, 0, TranscriptionReliabilityPolicy(.7), "3",
    )
    evidence = EvidenceRecord.initial(
        evidence_id="evidence-3", learner_id="learner-1", observation=observation,
        interpretation="respuesta independiente", reason_for_retention="progression",
    )
    repo.commit_once(MutationBatch(
        "answer-3", "fp-3", "session-2", 1, 1,
        CommandResult("answer-3", CommandStatus.APPLIED, "saved"),
        activities=(StoredActivity("activity-3", activity),), observations=(observation,),
        evidence=(evidence,), expected_absent_activity_ids=("activity-3",),
    ))


def discard(*, command_id="review-command", fingerprint="review-fp", expected_review_version=0, expected_profile_version=1, learner_id="learner-1", evidence_id="evidence-2", review_id="review-1", session_id="session-1", reason="STT atribuido incorrectamente"):
    return DiscardEvidence(
        command_id=command_id, command_fingerprint=fingerprint,
        review_id=review_id, learner_id=learner_id, session_id=session_id,
        evidence_id=evidence_id, reason=reason,
        expected_review_version=expected_review_version,
        expected_profile_version=expected_profile_version,
    )


def test_discard_is_append_only_idempotent_and_recalculates_estimate(tmp_path):
    repo = repository(tmp_path)
    result = TherapistReviewService(repo, policy()).discard_evidence(discard())

    assert result.status is ReviewStatus.APPLIED
    assert result.estimate.state is CompetencyState.NOT_OBSERVED
    assert result.estimate.version == 3
    assert repo.load_estimate("learner-1", "add") == result.estimate
    assert [row.version for row in repo.load_therapist_reviews("review-1")] == [1]
    assert repo.load_profile_revisions("learner-1")[-1].profile_version == 2
    summary = SummaryService(repo).build("session-1")
    assert "evidence-2" not in {item for claim in summary.claims for item in claim.evidence_ids}
    assert "evidence-2" in summary.historical_evidence_ids

    replay = TherapistReviewService(repo, policy()).discard_evidence(discard())
    assert replay == result
    assert [row.version for row in repo.load_therapist_reviews("review-1")] == [1]


def test_summary_reports_conversation_support_without_mathematical_evidence_links(tmp_path):
    repo = repository(tmp_path)
    with repo._connect() as db:
        db.execute(
            "INSERT INTO regulation_events(event_id,session_id,activity_id,turn_id,signal,confidence_band,strategy,ordinal,outcome) VALUES(?,?,?,?,?,?,?,?,?)",
            ("regulation-session-1-turn-2", "session-1", "activity-1", "turn-2",
             "frustrated", "high", "validate-emotion", 2, "answered"),
        )

    summary = SummaryService(repo).build("session-1")

    assert len(summary.regulation_support) == 1
    item = summary.regulation_support[0]
    assert (item.event_id, item.signal.value, item.confidence_band.value) == (
        "regulation-session-1-turn-2", "frustrated", "high",
    )
    assert (item.executed_action.value, item.outcome.value, item.ordinal, item.provisional) == (
        "validate-emotion", "answered", 2, True,
    )
    assert item.event_id not in {
        evidence_id for claim in summary.claims for evidence_id in claim.evidence_ids
    }


def test_summary_preserves_durable_regulation_order_when_timestamps_tie(tmp_path):
    repo = repository(tmp_path)
    with repo._connect() as db:
        for turn_id, ordinal in (("turn-z", 1), ("turn-a", 2)):
            db.execute(
                "INSERT INTO regulation_events(event_id,session_id,activity_id,turn_id,signal,confidence_band,strategy,ordinal,outcome,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f"regulation-session-1-{turn_id}", "session-1", "activity-1",
                 turn_id, "confused", "high", "simplify-language", ordinal,
                 "unknown", "2026-09-08 10:00:00"),
            )

    summary = SummaryService(repo).build("session-1")

    assert [item.ordinal for item in summary.regulation_support] == [1, 2]


def test_review_command_collision_and_stale_versions_fail_closed(tmp_path):
    repo = repository(tmp_path)
    service = TherapistReviewService(repo, policy())
    assert service.discard_evidence(discard()).status is ReviewStatus.APPLIED
    collision = service.discard_evidence(discard(reason="payload cambiado"))
    assert collision.status is ReviewStatus.COLLISION
    assert collision.estimate is None
    assert service.discard_evidence(discard(command_id="stale", fingerprint="stale", expected_review_version=0, expected_profile_version=1, evidence_id="evidence-1")).status is ReviewStatus.CONFLICT


def test_review_enforces_canonical_ownership(tmp_path):
    repo = repository(tmp_path)
    result = TherapistReviewService(repo, policy()).discard_evidence(discard(learner_id="other"))

    assert result.status is ReviewStatus.REJECTED
    assert result.reason == "evidence-owner-mismatch"
    assert repo.load_therapist_reviews("review-1") == ()


def test_skill_correction_preserves_original_proposal_and_versions_profile(tmp_path):
    repo = repository(tmp_path)
    command = CorrectSkillEstimate(
        command_id="correct-1", command_fingerprint="correct-fp",
        review_id="review-correction", learner_id="learner-1",
        session_id="session-1", objective_id="add",
        corrected_state=CompetencyState.NEEDS_REVIEW,
        reason="La ayuda fue mayor de la registrada",
        expected_review_version=0, expected_profile_version=1,
    )

    result = TherapistReviewService(repo, policy()).correct_skill_estimate(command)

    assert result.status is ReviewStatus.APPLIED
    assert result.estimate.state is CompetencyState.NEEDS_REVIEW
    stored = repo.load_therapist_reviews("review-correction")[0].review
    assert stored.original_proposed_state is CompetencyState.WITH_INTENSIVE_HELP
    assert stored.original_evidence_ids == ("evidence-1", "evidence-2")
    assert repo.load_profile_change_proposals("learner-1", "add")[0].to_state is CompetencyState.WITH_INTENSIVE_HELP
    assert repo.load_profile_revisions("learner-1")[-1].revision.recalculated_state is CompetencyState.NEEDS_REVIEW


def test_distinct_command_cannot_discard_already_discarded_evidence_again(tmp_path):
    repo = repository(tmp_path)
    service = TherapistReviewService(repo, policy())
    assert service.discard_evidence(discard()).status is ReviewStatus.APPLIED

    repeated = service.discard_evidence(discard(
        command_id="review-command-2", fingerprint="review-fp-2",
        expected_review_version=1, expected_profile_version=2,
    ))

    assert repeated.status is ReviewStatus.REJECTED
    assert repeated.reason == "evidence-already-discarded"
    assert [row.version for row in repo.load_therapist_reviews("review-1")] == [1]


def test_review_command_id_cannot_alias_a_tutoring_command(tmp_path):
    repo = repository(tmp_path)

    result = TherapistReviewService(repo, policy()).discard_evidence(discard(
        command_id="answer-1", fingerprint="fp-1",
    ))

    assert result.status is ReviewStatus.COLLISION
    assert repo.load_therapist_reviews("review-1") == ()


def test_review_recalculates_from_all_learner_objective_evidence_across_sessions(tmp_path):
    repo = repository(tmp_path)
    add_second_session(repo)
    source = repo.load_summary_source("session-2")
    assert source.estimates[0].supporting_evidence_ids == ("evidence-1", "evidence-2")
    assert {item.evidence_id for item in source.evidence} == {
        "evidence-1", "evidence-2", "evidence-3"
    }
    command = replace(
        discard(), command_id="cross-session", command_fingerprint="cross-fp",
        review_id="review-cross", session_id="session-2", evidence_id="evidence-1",
    )

    result = TherapistReviewService(repo, policy()).discard_evidence(command)

    assert result.status is ReviewStatus.APPLIED
    assert result.estimate.state is CompetencyState.EXPLORING
    stored = repo.load_therapist_reviews("review-cross")[0].review
    assert stored.evidence_source_session_id == "session-1"
    summary = SummaryService(repo).build("session-2")
    assert "evidence-1" in summary.historical_evidence_ids
    assert "evidence-1" in summary.discarded_evidence_ids


def test_profile_cas_is_learner_global_across_sessions(tmp_path):
    repo = repository(tmp_path)
    add_second_session(repo)
    first = TherapistReviewService(repo, policy()).discard_evidence(discard())
    assert first.status is ReviewStatus.APPLIED

    stale = TherapistReviewService(repo, policy()).discard_evidence(replace(
        discard(), command_id="stale-other-session", command_fingerprint="stale-other",
        review_id="review-other", session_id="session-2", evidence_id="evidence-1",
    ))

    assert stale.status is ReviewStatus.CONFLICT
    assert repo.load_summary_source("session-1").profile_version == 2
    assert repo.load_summary_source("session-2").profile_version == 2
    assert repo.load_therapist_reviews("review-other") == ()


def test_review_integrity_race_returns_conflict_and_rolls_back(tmp_path):
    repo = repository(tmp_path)
    before = repo.load_estimate("learner-1", "add")
    repo.append_profile_revision(
        "concurrent-revision", "learner-1", 2, {"external": True},
        policy_version=policy().version,
    )

    result = TherapistReviewService(repo, policy()).discard_evidence(discard())

    assert result.status is ReviewStatus.CONFLICT
    assert result.reason == "concurrent-review-conflict"
    assert repo.load_estimate("learner-1", "add") == before
    assert repo.load_therapist_reviews("review-1") == ()
    assert repo.load_summary_source("session-1").profile_version == 1


def test_caller_fingerprint_does_not_control_legitimate_replay(tmp_path):
    repo = repository(tmp_path)
    service = TherapistReviewService(repo, policy())
    first = service.discard_evidence(discard(fingerprint="caller-a"))

    replay = service.discard_evidence(discard(fingerprint="caller-b"))

    assert replay == first
    assert [item.version for item in repo.load_therapist_reviews("review-1")] == [1]


def test_same_command_id_cannot_cross_review_identity_or_expose_result(tmp_path):
    repo = repository(tmp_path)
    service = TherapistReviewService(repo, policy())
    applied = service.discard_evidence(discard())

    collision = service.discard_evidence(discard(
        review_id="review-other", fingerprint="review-fp",
    ))

    assert applied.estimate is not None
    assert collision.status is ReviewStatus.COLLISION
    assert collision.review_id == "review-other"
    assert collision.estimate is None
    assert repo.load_therapist_reviews("review-other") == ()


def test_same_caller_fingerprint_cannot_spoof_owner_session_or_evidence(tmp_path):
    repo = repository(tmp_path)
    add_second_session(repo)
    service = TherapistReviewService(repo, policy())
    assert service.discard_evidence(discard()).status is ReviewStatus.APPLIED

    variants = (
        discard(learner_id="other"),
        discard(session_id="session-2"),
        discard(evidence_id="evidence-1"),
    )
    for spoofed in variants:
        collision = service.discard_evidence(spoofed)
        assert collision.status is ReviewStatus.COLLISION
        assert collision.estimate is None


def test_v5_applied_review_replays_after_identity_migration_and_spoofs_collide(tmp_path):
    migration_source = Path("src/math_tutor/infrastructure/persistence/migrations")
    frozen_v5 = tmp_path / "frozen-v5-migrations"
    frozen_v5.mkdir()
    for migration in sorted(migration_source.glob("000[1-5]_*.sql")):
        (frozen_v5 / migration.name).write_bytes(migration.read_bytes())
    repo = repository(tmp_path, migration_dir=frozen_v5)
    command = discard(fingerprint="legacy-caller-fingerprint")
    current = repo.load_estimate("learner-1", "add")
    recalculated = SkillEstimate(
        "learner-1", "add", CompetencyState.NOT_OBSERVED,
        version=current.version + 1,
    )
    review = DiscardEvidenceReview(
        "evidence-2", "session-1", command.reason,
        "respuesta independiente", ObservationOutcome.CORRECT.value,
    )
    revision = ProfileRecalculation(
        "add", current.state, recalculated.state, ("evidence-2",),
    )
    stored_result = ReviewResult(
        ReviewStatus.APPLIED, "evidence-discarded", "review-1", 1,
        recalculated,
    )
    repo.append_therapist_review(
        "review-1", 1, "learner-1", "session-1", review
    )
    repo.append_profile_revision(
        "review-command:profile", "learner-1", 2, revision,
        policy_version=policy().version,
    )
    with repo._connect() as db:
        db.execute(
            "UPDATE skill_estimates SET estimate_json=?,version=3 WHERE learner_id='learner-1' AND objective_id='add'",
            (_dump(recalculated),),
        )
        db.execute("UPDATE learner_profile_versions SET version=2 WHERE learner_id='learner-1'")
        db.execute("UPDATE learning_sessions SET profile_version=2 WHERE learner_id='learner-1'")
        db.execute(
            "INSERT INTO processed_commands(command_id,command_fingerprint,result_json) VALUES(?,?,?)",
            (command.command_id, command.command_fingerprint, _dump(stored_result)),
        )

    migrate(repo.database)
    service = TherapistReviewService(SQLiteTutoringRepository(repo.database), policy())
    exact = service.discard_evidence(replace(
        command, command_fingerprint="different-retry-caller-value"
    ))
    assert exact == stored_result

    for altered in (
        replace(command, review_id="other-review"),
        replace(command, learner_id="other-learner"),
        replace(command, session_id="session-2"),
        replace(command, reason="payload alterado"),
        replace(command, evidence_id="evidence-1"),
    ):
        collision = service.discard_evidence(altered)
        assert collision.status is ReviewStatus.COLLISION
        assert collision.estimate is None


def test_v5_corrected_state_is_bound_during_legacy_identity_migration(tmp_path):
    migration_source = Path("src/math_tutor/infrastructure/persistence/migrations")
    frozen_v5 = tmp_path / "frozen-v5-correction-migrations"
    frozen_v5.mkdir()
    for migration in sorted(migration_source.glob("000[1-5]_*.sql")):
        (frozen_v5 / migration.name).write_bytes(migration.read_bytes())
    repo = repository(tmp_path, migration_dir=frozen_v5)
    command = CorrectSkillEstimate(
        command_id="legacy-correct", command_fingerprint="legacy-arbitrary",
        review_id="legacy-correct-review", learner_id="learner-1",
        session_id="session-1", objective_id="add",
        corrected_state=CompetencyState.NEEDS_REVIEW,
        reason="La ayuda fue mayor", expected_review_version=0,
        expected_profile_version=1,
    )
    current = repo.load_estimate("learner-1", "add")
    corrected = SkillEstimate(
        "learner-1", "add", CompetencyState.NEEDS_REVIEW,
        current.version + 1, current.supporting_evidence_ids,
        current.supporting_observation_ids,
    )
    review = CorrectSkillEstimateReview(
        "add", CompetencyState.EXPLORING,
        CompetencyState.WITH_INTENSIVE_HELP,
        ("evidence-1", "evidence-2"), policy().version,
        CompetencyState.NEEDS_REVIEW, command.reason,
    )
    revision = ProfileRecalculation(
        "add", current.state, corrected.state, (),
    )
    stored_result = ReviewResult(
        ReviewStatus.APPLIED, "skill-estimate-corrected",
        command.review_id, 1, corrected,
    )
    repo.append_therapist_review(
        command.review_id, 1, "learner-1", "session-1", review
    )
    repo.append_profile_revision(
        "legacy-correct:profile", "learner-1", 2, revision,
        policy_version=policy().version,
    )
    with repo._connect() as db:
        db.execute(
            "UPDATE skill_estimates SET estimate_json=?,version=? WHERE learner_id='learner-1' AND objective_id='add'",
            (_dump(corrected), corrected.version),
        )
        db.execute("UPDATE learner_profile_versions SET version=2 WHERE learner_id='learner-1'")
        db.execute("UPDATE learning_sessions SET profile_version=2 WHERE learner_id='learner-1'")
        db.execute(
            "INSERT INTO processed_commands(command_id,command_fingerprint,result_json) VALUES(?,?,?)",
            (command.command_id, command.command_fingerprint, _dump(stored_result)),
        )

    migrate(repo.database)
    service = TherapistReviewService(SQLiteTutoringRepository(repo.database), policy())
    assert service.correct_skill_estimate(replace(
        command, command_fingerprint="retry-caller-value"
    )) == stored_result

    collision = service.correct_skill_estimate(replace(
        command, corrected_state=CompetencyState.GENERALIZED,
    ))
    assert collision.status is ReviewStatus.COLLISION
    assert collision.estimate is None
