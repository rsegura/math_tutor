from datetime import datetime, timedelta, timezone
from pathlib import Path

from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, ProvisioningService, RevokeAudioConsent, SessionLimits, StartLearningSession
from math_tutor.infrastructure.clip_retention import ClipRetentionService, RetentionSettings
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.evidence_clips import OpaqueClipStore, SelectedAudio
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository


class DeferredPurger:
    def purge_consent_scope(self, consent_id, session_ids): pass


def setup_authorized(tmp_path):
    database = tmp_path / "math.db"
    migrate(database)
    root = Path("src/math_tutor/curricula")
    curriculum, _ = load_curriculum_catalogs(root / "primary-math-v1.yaml", root / "activity-templates-v1.yaml")
    repo = SQLiteTutoringRepository(database)
    service = ProvisioningService(repo, curriculum, DeferredPurger())
    service.create_learner(CreateLearner("learner-clip", "Sol", 9))
    service.create_learning_plan(CreateLearningPlan("plan-clip", "learner-clip", ("units-tens",), (), SessionLimits(10, 3)))
    consent = service.grant_audio_consent("learner-clip", retention_days=2, now=datetime(2026, 9, 6, tzinfo=timezone.utc))
    session = service.start_learning_session(StartLearningSession("learner-clip", consent.consent_id))
    # Evidence existence is the deterministic harness commitment fence.
    with repo._connect() as db:
        db.execute("INSERT INTO activities(session_id,activity_id,objective_id,activity_json) VALUES(?,?,?,?)", (session.tutoring_session_id,"activity-clip","units-tens",'{"type":"unused"}'))
        db.execute("INSERT INTO observations(observation_id,session_id,learner_id,objective_id,activity_id,observation_json,version) VALUES(?,?,?,?,?,?,1)", ("observation-clip",session.tutoring_session_id,"learner-clip","units-tens","activity-clip",'{"type":"unused"}'))
        db.execute("INSERT INTO evidence_records(evidence_id,observation_id,learner_id,session_id,objective_id,reason_for_retention) VALUES(?,?,?,?,?,?)", ("evidence-clip","observation-clip","learner-clip",session.tutoring_session_id,"units-tens","first-independent-success"))
    return repo, service, consent, session


def test_clip_requires_feature_flag_commit_and_active_scoped_consent(tmp_path):
    repo, _, consent, session = setup_authorized(tmp_path)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    off = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "off"), RetentionSettings(), now=lambda: now)
    selected = SelectedAudio(b"encoded", 2)
    assert off.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected, evidence_selection_committed=True) is None
    enabled = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=2, evidence_directory=tmp_path / "clips"), now=lambda: now)
    assert enabled.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected, evidence_selection_committed=False) is None
    clip_id = enabled.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected, evidence_selection_committed=True)
    assert clip_id and repo.load_evidence_clip(clip_id).expires_at.startswith("2026-09-08")


def test_revocation_purges_file_and_live_metadata_idempotently(tmp_path):
    repo, _, consent, session = setup_authorized(tmp_path)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    retention = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=2), now=lambda: now)
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=SelectedAudio(b"encoded", 2), evidence_selection_committed=True)
    service = ProvisioningService(repo, __import__('math_tutor.infrastructure.curriculum_loader',fromlist=['load_curriculum_catalogs']).load_curriculum_catalogs(Path('src/math_tutor/curricula/primary-math-v1.yaml'),Path('src/math_tutor/curricula/activity-templates-v1.yaml'))[0], retention)
    service.revoke_audio_consent(RevokeAudioConsent("learner-clip", consent.consent_id), now=now)
    service.revoke_audio_consent(RevokeAudioConsent("learner-clip", consent.consent_id), now=now)
    assert repo.load_evidence_clip(clip_id) is None
    assert not list((tmp_path / "clips").glob("*.bin"))
    assert retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=SelectedAudio(b"new", 1), evidence_selection_committed=True) is None


def test_expired_and_manual_deletion_are_retry_safe(tmp_path):
    repo, _, _, session = setup_authorized(tmp_path)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    retention = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=1), now=lambda: now)
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=SelectedAudio(b"encoded", 2), evidence_selection_committed=True)
    assert retention.delete_clip(clip_id, authorized=True) == 1
    assert retention.delete_clip(clip_id, authorized=True) == 0
    assert repo.load_evidence_clip(clip_id) is None


def test_expiry_sweep_and_partial_metadata_failure_are_retry_safe(tmp_path):
    repo, _, _, session = setup_authorized(tmp_path)
    clock = [datetime(2026, 9, 6, 12, tzinfo=timezone.utc)]
    retention = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=1), now=lambda: clock[0])
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=SelectedAudio(b"encoded", 2), evidence_selection_committed=True)
    clock[0] += timedelta(days=1, seconds=1)
    original = repo.complete_evidence_clip_deletion
    failures = [True]
    def fail_once(*args, **kwargs):
        if failures:
            failures.pop()
            raise OSError("database temporarily unavailable")
        return original(*args, **kwargs)
    repo.complete_evidence_clip_deletion = fail_once
    try:
        retention.sweep_expired()
    except OSError:
        pass
    else:
        raise AssertionError("partial deletion failure was hidden")
    assert retention.sweep_expired() == 1
    assert repo.load_evidence_clip(clip_id) is None


def test_cross_process_claim_is_compare_and_set(tmp_path):
    repo, _, _, session = setup_authorized(tmp_path)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    retention = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=1), now=lambda: now)
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=SelectedAudio(b"encoded", 2), evidence_selection_committed=True)
    assert len(repo.claim_evidence_clips(clip_id=clip_id, claimed_at=now, claim_id="process-a")) == 1
    assert SQLiteTutoringRepository(repo.database).claim_evidence_clips(clip_id=clip_id, claimed_at=now, claim_id="process-b") == ()
