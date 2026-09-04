import sqlite3
import threading
from dataclasses import replace

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
