import sqlite3
import threading
from dataclasses import replace

import pytest

from math_tutor.application.ports import (
    ActivityProgress, ActivityProgressExpectation, CommitOutcome, MutationBatch,
    ObservationExpectation, PersistedTutoringState, StoredActivity, TutoringEvent,
)
from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import EvidenceRecord, Observation, ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.learning import CompetencyState, LearningPlan, LearningSession, PresentationProfile, ProposedProfileChange, SkillEstimate
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository


def session(version=1):
    plan = LearningPlan("learner-1", ("objective-1",), ("objective-1",), PresentationProfile.for_age(8), "plan-1")
    return LearningSession.start(session_id="session-1", plan=plan) if version == 1 else LearningSession("session-1", "learner-1", "plan-1", 1, ("objective-1",), ("objective-1",), version=version)


def activity(activity_id="activity-1"):
    return Activity("template-1", "objective-1", 1, "¿Cuánto es 2 + 3?", {"a": 2, "b": 3}, StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 5}), ("addition-error",), ("hint-1",))


def observation(version_text="cinco"):
    return Observation("observation-1", "learner-1", "session-1", "objective-1", "activity-1", ObservationOutcome.CORRECT, .96, 0, TranscriptionReliabilityPolicy(.7), version_text)


def repository(tmp_path):
    path = tmp_path / "math.db"
    migrate(path)
    repo = SQLiteTutoringRepository(path)
    repo.save_learner("learner-1", curriculum_snapshot="curriculum-yaml", curriculum_version="curriculum-v1")
    repo.save_plan(LearningPlan("learner-1", ("objective-1",), ("objective-1",), PresentationProfile.for_age(8), "plan-1"), policy_version="policy-v1")
    repo.save_session(session(), profile_version=1)
    repo.save_estimate(SkillEstimate("learner-1", "objective-1", CompetencyState.NOT_OBSERVED))
    return repo


def test_session_bootstrap_cannot_promote_or_lower_canonical_profile_version(tmp_path):
    repo = repository(tmp_path)
    plan = repo.load_plan("plan-1", 1)

    with pytest.raises(ValueError, match="profile version must match canonical learner profile"):
        repo.save_session(
            LearningSession.start(session_id="session-high", plan=plan),
            profile_version=99,
        )

    with repo._connect() as db:
        assert db.execute(
            "SELECT version FROM learner_profile_versions WHERE learner_id='learner-1'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM learning_sessions WHERE learner_id='learner-1'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM profile_revisions WHERE learner_id='learner-1'"
        ).fetchone()[0] == 0
        db.execute(
            "UPDATE learner_profile_versions SET version=2 WHERE learner_id='learner-1'"
        )
        db.execute(
            "UPDATE learning_sessions SET profile_version=2 WHERE learner_id='learner-1'"
        )

    with pytest.raises(ValueError, match="profile version must match canonical learner profile"):
        repo.save_session(
            LearningSession.start(session_id="session-stale", plan=plan),
            profile_version=1,
        )

    with repo._connect() as db:
        assert db.execute(
            "SELECT version FROM learner_profile_versions WHERE learner_id='learner-1'"
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT profile_version FROM learning_sessions WHERE session_id='session-1'"
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT COUNT(*) FROM learning_sessions WHERE learner_id='learner-1'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM profile_revisions WHERE learner_id='learner-1'"
        ).fetchone()[0] == 0


def batch(command_id="command-1", fingerprint="fp-1"):
    obs = observation()
    evidence = EvidenceRecord.initial(evidence_id="evidence-1", learner_id="learner-1", observation=obs, interpretation="suma independiente", reason_for_retention="first-success")
    proposal = ProposedProfileChange("learner-1", "objective-1", CompetencyState.NOT_OBSERVED, CompetencyState.EXPLORING, ("evidence-1",), ("observation-1",), 1, "policy-v1")
    result = CommandResult(command_id, CommandStatus.APPLIED, "applied", {"spoken": "Muy bien"})
    return MutationBatch(command_id, fingerprint, "session-1", 1, 1, result, session(version=2), (StoredActivity("activity-1", activity()),), (obs,), (evidence,), (proposal,), (TutoringEvent("answer-recorded", "session-1", "objective-1", "activity-1", "correct"),), activity_progress=(ActivityProgress("activity-1", 1, 0, 1),), expected_absent_activity_ids=("activity-1",))


def bootstrap_progress(repo, *items):
    stored = tuple(StoredActivity(item.activity_id, activity(item.activity_id)) for item in items)
    result = CommandResult("bootstrap", CommandStatus.APPLIED, "created")
    mutation = MutationBatch("bootstrap", "bootstrap", "session-1", 1, 1, result,
                             activities=stored, activity_progress=items,
                             expected_absent_activity_ids=tuple(item.activity_id for item in items))
    assert repo.commit_once(mutation).outcome is CommitOutcome.APPLIED


def test_migration_is_idempotent_and_schema_has_no_full_session_audio(tmp_path):
    path = tmp_path / "math.db"
    migrate(path); migrate(path)
    connection = sqlite3.connect(path)
    tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    assert {"learners", "learning_plans", "learning_sessions", "activities", "observations", "evidence_records", "evidence_interpretations", "profile_change_proposals", "skill_estimates", "therapist_reviews", "profile_revisions", "tutoring_events", "processed_commands", "activity_progress", "evidence_clips"} <= tables
    columns = [row[1].lower() for table in tables for row in connection.execute(f"pragma table_info({table})")]
    assert "full_session_audio" not in columns
    assert "audio_blob" not in columns


def test_commit_once_is_atomic_and_replays_canonical_result(tmp_path):
    repo = repository(tmp_path)
    first = repo.commit_once(batch())
    replay = repo.commit_once(batch())
    collision = repo.commit_once(batch(fingerprint="different"))
    assert first.outcome is CommitOutcome.APPLIED
    assert replay.outcome is CommitOutcome.REPLAYED
    assert replay.result == first.result
    assert replay.stored_fingerprint == "fp-1"
    assert collision.outcome is CommitOutcome.COLLISION
    assert collision.result == first.result
    state = repo.load_state("session-1")
    assert state == PersistedTutoringState(session(2), 1, (ActivityProgress("activity-1", 1, 0, 1),))
    assert repo.load_activity("session-1", "activity-1") == activity()
    assert repo.load_observation("session-1", "observation-1").observation == observation()
    assert repo.load_evidence("learner-1", "objective-1")[0].current_interpretation == "suma independiente"


def test_failed_expectation_rolls_back_whole_batch(tmp_path):
    repo = repository(tmp_path)
    broken = MutationBatch(**{**batch().__dict__}) if False else batch()
    broken = MutationBatch(
        command_id="command-conflict", command_fingerprint="fp", session_id="session-1",
        expected_session_version=99, expected_profile_version=1,
        result=broken.result, session=broken.session, activities=broken.activities,
        observations=broken.observations, evidence=broken.evidence,
        profile_change_proposals=broken.profile_change_proposals, events=broken.events,
        activity_progress=broken.activity_progress, expected_absent_activity_ids=broken.expected_absent_activity_ids,
    )
    decision = repo.commit_once(broken)
    assert decision == repo.commit_once(broken)
    assert decision.outcome is CommitOutcome.CONFLICT
    assert repo.load_activity("session-1", "activity-1") is None
    assert repo.load_command_result("command-conflict") is None
    assert repo.load_state("session-1").session.version == 1


def test_progress_observation_and_absence_expectations_are_checked_inside_transaction(tmp_path):
    repo = repository(tmp_path)
    assert repo.commit_once(batch()).outcome is CommitOutcome.APPLIED
    obs2 = Observation("observation-2", "learner-1", "session-1", "objective-1", "activity-1", ObservationOutcome.CORRECT, .9, 0, TranscriptionReliabilityPolicy(.7), "5")
    result = CommandResult("command-2", CommandStatus.APPLIED, "applied")
    conflict = MutationBatch("command-2", "fp-2", "session-1", 2, 1, result, session(version=3), observations=(obs2,), expected_observations=(ObservationExpectation("observation-1", 2),), expected_activity_progress=(ActivityProgressExpectation("activity-1", 1),))
    assert repo.commit_once(conflict).reason == "stale-observation-version"
    assert repo.load_observation("session-1", "observation-2") is None
    absent = MutationBatch("command-3", "fp-3", "session-1", 2, 1, result, expected_absent_activity_ids=("activity-1",))
    assert repo.commit_once(absent).reason == "activity-id-already-exists"


def test_activity_progress_upsert_preserves_unmentioned_rows(tmp_path):
    repo = repository(tmp_path)
    bootstrap_progress(repo, ActivityProgress("a", 0, 0, 1), ActivityProgress("b", 0, 0, 2))
    result = CommandResult("c", CommandStatus.APPLIED, "applied")
    mutation = MutationBatch("c", "fp", "session-1", 1, 1, result, activity_progress=(ActivityProgress("a", 1, 0, 1, version=2),), expected_activity_progress=(ActivityProgressExpectation("a", 1),))
    assert repo.commit_once(mutation).outcome is CommitOutcome.APPLIED
    assert {p.activity_id: p.version for p in repo.load_state("session-1").activity_progress} == {"a": 2, "b": 1}


def test_clip_metadata_is_bounded_and_references_exactly_one_evidence(tmp_path):
    repo = repository(tmp_path)
    repo.commit_once(batch())
    repo.save_evidence_clip("clip-1", "evidence-1", "learner-1", "session-1", duration_seconds=20, storage_key="clips/clip-1.enc", expires_at="2026-09-10T00:00:00Z")
    clip = repo.load_evidence_clip("clip-1")
    assert clip.evidence_id == "evidence-1" and clip.duration_seconds == 20
    for duration in (0, 31):
        try:
            repo.save_evidence_clip("bad", "evidence-1", "learner-1", "session-1", duration_seconds=duration, storage_key="x", expires_at="2026-09-10T00:00:00Z")
        except ValueError:
            pass
        else:
            raise AssertionError("unbounded clip accepted")


def test_reviews_and_profile_revisions_are_append_only_and_reconstructible(tmp_path):
    repo = repository(tmp_path)
    repo.append_therapist_review("review-1", 1, "learner-1", "session-1", {"decision": "confirm"})
    repo.append_therapist_review("review-1", 2, "learner-1", "session-1", {"decision": "correct"})
    repo.append_profile_revision("revision-1", "learner-1", 2, {"state": "exploring"}, policy_version="policy-v1")
    assert [item.version for item in repo.load_therapist_reviews("review-1")] == [1, 2]
    assert repo.load_profile_revisions("learner-1")[0].revision == {"state": "exploring"}
    try:
        repo.append_therapist_review("review-1", 2, "learner-1", "session-1", {"decision": "overwrite"})
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("review revision was overwritten")


def test_proposals_and_events_are_reconstructible(tmp_path):
    repo = repository(tmp_path)
    repo.commit_once(batch())
    proposals = repo.load_profile_change_proposals("learner-1", "objective-1")
    events = repo.load_events("session-1")
    assert proposals[0].policy_version == "policy-v1"
    assert events[0].kind == "answer-recorded"


def test_curriculum_snapshots_are_immutable_across_catalog_updates(tmp_path):
    repo = repository(tmp_path)
    repo.save_learner("learner-1", curriculum_snapshot="new-yaml", curriculum_version="curriculum-v2")
    assert repo.load_curriculum_snapshot("learner-1", "curriculum-v1") == "curriculum-yaml"
    assert repo.load_curriculum_snapshot("learner-1", "curriculum-v2") == "new-yaml"


def test_session_aggregate_uses_curriculum_authorised_for_its_plan(tmp_path):
    path = tmp_path / "history.db"
    migrate(path)
    repo = SQLiteTutoringRepository(path)
    repo.save_learner("learner-1", curriculum_snapshot="snapshot-v1", curriculum_version="v1")
    plan_v1 = LearningPlan("learner-1", ("objective-1",), ("objective-1",), PresentationProfile.for_age(8), "plan-1", 1)
    repo.save_plan(plan_v1, policy_version="policy-v1")
    repo.save_session(LearningSession.start(session_id="session-v1", plan=plan_v1), profile_version=1)

    repo.save_learner("learner-1", curriculum_snapshot="snapshot-v2", curriculum_version="v2")
    plan_v2 = replace(plan_v1, version=2)
    repo.save_plan(plan_v2, policy_version="policy-v1")
    repo.save_session(LearningSession.start(session_id="session-v2", plan=plan_v2), profile_version=1)

    old = repo.load_session_aggregate("session-v1")
    new = repo.load_session_aggregate("session-v2")
    assert (old.curriculum_version, old.curriculum_snapshot) == ("v1", "snapshot-v1")
    assert (new.curriculum_version, new.curriculum_snapshot) == ("v2", "snapshot-v2")


def test_review_series_has_immutable_owner_and_strictly_sequential_versions(tmp_path):
    repo = repository(tmp_path)
    repo.append_therapist_review("review-1", 1, "learner-1", "session-1", {"decision": "confirm"})

    invalid = (
        (3, "learner-1", "session-1"),
        (1, "learner-1", "session-1"),
        (2, "learner-1", None),
    )
    for version, learner_id, session_id in invalid:
        try:
            repo.append_therapist_review("review-1", version, learner_id, session_id, {"decision": "invalid"})
        except (ValueError, sqlite3.IntegrityError):
            pass
        else:
            raise AssertionError("invalid review series mutation accepted")

    repo.save_learner("learner-2", curriculum_snapshot="curriculum-yaml", curriculum_version="curriculum-v1")
    foreign_plan = LearningPlan(
        "learner-2", ("objective-1",), ("objective-1",),
        PresentationProfile.for_age(8), "plan-2",
    )
    repo.save_plan(foreign_plan, policy_version="policy-v1")
    repo.save_session(
        LearningSession.start(session_id="session-2", plan=foreign_plan),
        profile_version=1,
    )
    try:
        repo.append_therapist_review(
            "review-1", 2, "learner-2", "session-2", {"decision": "foreign"}
        )
    except ValueError as error:
        assert "owner" in str(error)
    else:
        raise AssertionError("review series crossed learner ownership")

    repo.append_therapist_review("review-1", 2, "learner-1", "session-1", {"decision": "correct"})
    assert [record.version for record in repo.load_therapist_reviews("review-1")] == [1, 2]


def test_concurrent_review_append_has_one_winner(tmp_path):
    repo = repository(tmp_path)
    repo.append_therapist_review("review-1", 1, "learner-1", "session-1", {"decision": "initial"})
    barrier = threading.Barrier(2)
    outcomes = []

    def append(decision):
        barrier.wait()
        try:
            repo.append_therapist_review("review-1", 2, "learner-1", "session-1", {"decision": decision})
            outcomes.append("applied")
        except (ValueError, sqlite3.IntegrityError):
            outcomes.append("rejected")

    threads = [threading.Thread(target=append, args=(decision,)) for decision in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["applied", "rejected"]
    assert [record.version for record in repo.load_therapist_reviews("review-1")] == [1, 2]


def test_review_owner_and_versions_are_immutable_at_schema_boundary(tmp_path):
    repo = repository(tmp_path)
    repo.append_therapist_review("review-1", 1, "learner-1", "session-1", {"decision": "initial"})
    with sqlite3.connect(repo.database) as connection:
        for statement in (
            "UPDATE therapist_review_series SET session_id=NULL WHERE review_id='review-1'",
            "DELETE FROM therapist_review_series WHERE review_id='review-1'",
            "UPDATE therapist_reviews SET session_id=NULL WHERE review_id='review-1' AND version=1",
            "DELETE FROM therapist_reviews WHERE review_id='review-1' AND version=1",
        ):
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("review history was mutable through raw storage")


def test_batch_rejects_cross_session_and_cross_learner_payloads(tmp_path):
    repo = repository(tmp_path)
    wrong_session = replace(batch(), session=replace(session(2), session_id="other"))
    assert repo.commit_once(wrong_session).reason == "batch-session-mismatch"
    wrong_observation = replace(batch(), observations=(replace(observation(), learner_id="other"),))
    assert repo.commit_once(wrong_observation).reason == "batch-learner-mismatch"
    assert repo.load_command_result("command-1") is None


def test_every_progress_update_requires_exact_cas_expectation(tmp_path):
    repo = repository(tmp_path)
    bootstrap_progress(repo, ActivityProgress("a", 0, 0, 1))
    result = CommandResult("c", CommandStatus.APPLIED, "applied")
    blind = MutationBatch("c", "fp", "session-1", 1, 1, result,
                          activity_progress=(ActivityProgress("a", 1, 0, 1, version=2),))
    assert repo.commit_once(blind).reason == "missing-activity-progress-expectation"
    stale = replace(blind, expected_activity_progress=(ActivityProgressExpectation("a", 9),))
    assert repo.commit_once(stale).reason == "stale-activity-progress-version"
    assert repo.load_state("session-1").progress_for("a").version == 1


def test_bootstrap_helpers_never_blindly_overwrite_versioned_state(tmp_path):
    repo = repository(tmp_path)
    estimate = SkillEstimate("learner-1", "objective-1", CompetencyState.EXPLORING, version=2)
    try:
        repo.save_estimate(estimate)
    except ValueError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("blind estimate overwrite accepted")


def test_clip_ownership_is_derived_from_canonical_evidence(tmp_path):
    repo = repository(tmp_path)
    repo.commit_once(batch())
    repo.save_evidence_clip("clip-derived", "evidence-1", duration_seconds=20,
                            storage_key="clips/derived.enc", expires_at="2026-09-10T00:00:00Z")
    clip = repo.load_evidence_clip("clip-derived")
    assert (clip.learner_id, clip.session_id) == ("learner-1", "session-1")
    try:
        repo.save_evidence_clip("clip-bad", "evidence-1", "other", "session-1",
                                duration_seconds=20, storage_key="x", expires_at="2026-09-10T00:00:00Z")
    except ValueError as error:
        assert "ownership" in str(error)
    else:
        raise AssertionError("caller-controlled clip owner accepted")


def test_interpretation_revisions_append_with_version_cas(tmp_path):
    repo = repository(tmp_path)
    repo.commit_once(batch())
    repo.append_interpretation_revision("evidence-1", expected_version=1,
                                        interpretation="corregida", reason="terapeuta")
    assert [r.version for r in repo.load_evidence("learner-1", "objective-1")[0].interpretations] == [1, 2]
    try:
        repo.append_interpretation_revision("evidence-1", expected_version=1,
                                            interpretation="stale", reason="stale")
    except ValueError as error:
        assert "stale" in str(error)
    else:
        raise AssertionError("stale interpretation append accepted")


def test_late_failure_rolls_back_all_prior_writes_and_command(tmp_path):
    repo = repository(tmp_path)
    valid = batch()
    duplicate = replace(valid.evidence[0], evidence_id="evidence-2")
    broken = replace(valid, evidence=(valid.evidence[0], duplicate))
    try:
        repo.commit_once(broken)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("late duplicate observation write did not fail")
    assert repo.load_activity("session-1", "activity-1") is None
    assert repo.load_observation("session-1", "observation-1") is None
    assert repo.load_command_result("command-1") is None


def test_session_must_reference_plan_version_owned_by_same_learner(tmp_path):
    repo = repository(tmp_path)
    foreign = LearningSession("foreign-session", "learner-1", "missing-plan", 1,
                              ("objective-1",), ("objective-1",))
    try:
        repo.save_session(foreign, profile_version=1)
    except ValueError as error:
        assert "plan" in str(error)
    else:
        raise AssertionError("session without owned plan version accepted")


def test_observation_objective_must_match_its_canonical_activity(tmp_path):
    repo = repository(tmp_path)
    canonical = replace(activity(), objective_id="objective-1")
    repo.commit_once(replace(batch(), activities=(StoredActivity("activity-1", canonical),), observations=(), evidence=(), profile_change_proposals=(), activity_progress=()))
    wrong = replace(observation("wrong"), observation_id="observation-wrong", objective_id="objective-2")
    attempt = replace(batch(command_id="wrong-objective", fingerprint="wrong-objective"), activities=(), observations=(wrong,), evidence=(), profile_change_proposals=(), activity_progress=(), expected_absent_activity_ids=(), expected_session_version=2, session=session(3))
    assert repo.commit_once(attempt).reason == "batch-objective-mismatch"
    assert repo.load_observation("session-1", "observation-wrong") is None


def test_proposal_rejects_missing_and_cross_owned_evidence(tmp_path):
    repo = repository(tmp_path)
    base = batch()
    missing = replace(base.profile_change_proposals[0], evidence_ids=("missing",), observation_ids=("observation-1",))
    assert repo.commit_once(replace(base, profile_change_proposals=(missing,))).reason == "batch-proposal-evidence-mismatch"

    repo.commit_once(base)
    other = replace(base.profile_change_proposals[0], learner_id="learner-2")
    later = replace(base, command_id="other", command_fingerprint="other", expected_session_version=2, session=session(3), activities=(), observations=(), evidence=(), activity_progress=(), expected_absent_activity_ids=(), profile_change_proposals=(other,))
    assert repo.commit_once(later).reason == "batch-learner-mismatch"


def test_generalized_proposal_preserves_evidence_provenance_across_sessions(tmp_path):
    path = tmp_path / "generalized.db"
    migrate(path)
    repo = SQLiteTutoringRepository(path)
    plan = LearningPlan(
        "learner-1", ("objective-1",), ("objective-1",),
        PresentationProfile.for_age(8), "plan-1",
    )
    repo.save_learner(
        "learner-1", curriculum_snapshot="curriculum-yaml",
        curriculum_version="curriculum-v1",
    )
    repo.save_plan(plan, policy_version="policy-v1")
    first_session = LearningSession.start(session_id="session-1", plan=plan)
    second_session = LearningSession.start(session_id="session-2", plan=plan)
    repo.save_session(first_session, profile_version=1)
    repo.save_session(second_session, profile_version=1)
    repo.save_estimate(SkillEstimate(
        "learner-1", "objective-1", CompetencyState.INDEPENDENT,
    ))

    first_activity = activity("activity-1")
    first_observation = observation()
    first_evidence = EvidenceRecord.initial(
        evidence_id="evidence-1", learner_id="learner-1",
        observation=first_observation,
    )
    first = MutationBatch(
        "first-session", "fp-first", "session-1", 1, 1,
        CommandResult("first-session", CommandStatus.APPLIED, "stored"),
        activities=(StoredActivity("activity-1", first_activity),),
        observations=(first_observation,), evidence=(first_evidence,),
        activity_progress=(ActivityProgress("activity-1", 1, 0, 1),),
        expected_absent_activity_ids=("activity-1",),
    )
    assert repo.commit_once(first).outcome is CommitOutcome.APPLIED

    second_activity = activity("activity-2")
    second_observation = replace(
        observation(), observation_id="observation-2", session_id="session-2",
        activity_id="activity-2",
    )
    second_evidence = EvidenceRecord.initial(
        evidence_id="evidence-2", learner_id="learner-1",
        observation=second_observation,
    )
    proposal = ProposedProfileChange(
        "learner-1", "objective-1", CompetencyState.INDEPENDENT,
        CompetencyState.GENERALIZED,
        ("evidence-1", "evidence-2"),
        ("observation-1", "observation-2"), 1, "policy-v1",
    )
    second = MutationBatch(
        "second-session", "fp-second", "session-2", 1, 1,
        CommandResult("second-session", CommandStatus.APPLIED, "stored"),
        activities=(StoredActivity("activity-2", second_activity),),
        observations=(second_observation,), evidence=(second_evidence,),
        profile_change_proposals=(proposal,),
        activity_progress=(ActivityProgress("activity-2", 1, 0, 1),),
        expected_absent_activity_ids=("activity-2",),
    )

    assert repo.commit_once(second).outcome is CommitOutcome.APPLIED
    assert repo.load_profile_change_proposals("learner-1", "objective-1") == (proposal,)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT evidence_id,source_session_id FROM proposal_evidence "
            "ORDER BY evidence_id"
        ).fetchall()
    assert rows == [("evidence-1", "session-1"), ("evidence-2", "session-2")]


def test_cross_session_proposal_still_rejects_foreign_objective_evidence(tmp_path):
    repo = repository(tmp_path)
    base = batch()
    repo.commit_once(replace(
        base, profile_change_proposals=(),
    ))
    foreign_objective = replace(
        base.profile_change_proposals[0], evidence_ids=("evidence-1",),
        observation_ids=("observation-1",), objective_id="objective-2",
    )
    later = replace(
        base, command_id="foreign-objective", command_fingerprint="foreign-objective",
        expected_session_version=2, session=session(3), activities=(), observations=(),
        evidence=(), activity_progress=(), expected_absent_activity_ids=(),
        profile_change_proposals=(foreign_objective,),
    )
    assert repo.commit_once(later).reason == "batch-objective-mismatch"


def test_cross_session_proposal_rejects_foreign_learner_evidence(tmp_path):
    path = tmp_path / "foreign-learner.db"
    migrate(path)
    repo = SQLiteTutoringRepository(path)
    for learner_id in ("learner-1", "learner-2"):
        plan = LearningPlan(
            learner_id, ("objective-1",), ("objective-1",),
            PresentationProfile.for_age(8), f"plan-{learner_id}",
        )
        repo.save_learner(
            learner_id, curriculum_snapshot="curriculum-yaml",
            curriculum_version="curriculum-v1",
        )
        repo.save_plan(plan, policy_version="policy-v1")
        repo.save_session(
            LearningSession.start(session_id=f"session-{learner_id}", plan=plan),
            profile_version=1,
        )
        repo.save_estimate(SkillEstimate(
            learner_id, "objective-1", CompetencyState.NOT_OBSERVED,
        ))

    foreign_activity = activity("foreign-activity")
    foreign_observation = replace(
        observation(), observation_id="foreign-observation",
        learner_id="learner-2", session_id="session-learner-2",
        activity_id="foreign-activity",
    )
    foreign_evidence = EvidenceRecord.initial(
        evidence_id="foreign-evidence", learner_id="learner-2",
        observation=foreign_observation,
    )
    assert repo.commit_once(MutationBatch(
        "foreign-source", "foreign-source", "session-learner-2", 1, 1,
        CommandResult("foreign-source", CommandStatus.APPLIED, "stored"),
        activities=(StoredActivity("foreign-activity", foreign_activity),),
        observations=(foreign_observation,), evidence=(foreign_evidence,),
        activity_progress=(ActivityProgress("foreign-activity", 1, 0, 1),),
        expected_absent_activity_ids=("foreign-activity",),
    )).outcome is CommitOutcome.APPLIED

    proposal = ProposedProfileChange(
        "learner-1", "objective-1", CompetencyState.NOT_OBSERVED,
        CompetencyState.EXPLORING, ("foreign-evidence",),
        ("foreign-observation",), 1, "policy-v1",
    )
    attempt = MutationBatch(
        "cross-learner", "cross-learner", "session-learner-1", 1, 1,
        CommandResult("cross-learner", CommandStatus.APPLIED, "stored"),
        profile_change_proposals=(proposal,),
    )

    assert repo.commit_once(attempt).reason == "batch-proposal-evidence-mismatch"
    assert repo.load_profile_change_proposals("learner-1", "objective-1") == ()


def test_commit_once_serializes_two_real_connections(tmp_path):
    repo = repository(tmp_path)
    barrier = threading.Barrier(2)
    outcomes = []

    def commit(candidate):
        barrier.wait()
        outcomes.append(SQLiteTutoringRepository(repo.database).commit_once(candidate).outcome)

    first = threading.Thread(target=commit, args=(batch(),))
    second = threading.Thread(target=commit, args=(batch(fingerprint="collision"),))
    first.start(); second.start(); first.join(); second.join()
    assert sorted(item.value for item in outcomes) == ["applied", "collision"]
