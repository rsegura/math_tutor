from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import wave
import struct

from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, ProvisioningService, RevokeAudioConsent, SessionLimits, StartLearningSession
from math_tutor.infrastructure.clip_retention import ClipRetentionService, RetentionSettings
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.evidence_clips import AudioFrame, OpaqueClipStore, SelectedAudio, SessionAudioBuffers
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from math_tutor.infrastructure.persistence.repositories import _dump
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import Observation, ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.templates import ExpectedAnswerKind


class DeferredPurger:
    def purge_consent_scope(self, consent_id, session_ids): pass


def selected_audio(seconds=1):
    target = BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(1); audio.setframerate(8_000)
        audio.writeframes(b"\x80" * (8_000 * seconds))
    return SelectedAudio(target.getvalue(), seconds)


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
    activity = Activity("template-clip", "units-tens", 1, "¿Cuántas unidades?", {"number": 4}, StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 4}), (), ())
    observation = Observation("observation-clip", "learner-clip", session.tutoring_session_id, "units-tens", "activity-clip", ObservationOutcome.CORRECT, .95, 0, TranscriptionReliabilityPolicy(.7), "cuatro")
    with repo._connect() as db:
        db.execute("INSERT INTO activities(session_id,activity_id,objective_id,activity_json) VALUES(?,?,?,?)", (session.tutoring_session_id,"activity-clip","units-tens",_dump(activity)))
        db.execute("INSERT INTO observations(observation_id,session_id,learner_id,objective_id,activity_id,observation_json,version) VALUES(?,?,?,?,?,?,1)", ("observation-clip",session.tutoring_session_id,"learner-clip","units-tens","activity-clip",_dump(observation)))
        db.execute("INSERT INTO evidence_records(evidence_id,observation_id,learner_id,session_id,objective_id,reason_for_retention) VALUES(?,?,?,?,?,?)", ("evidence-clip","observation-clip","learner-clip",session.tutoring_session_id,"units-tens","first-independent-success"))
    return repo, service, consent, session


def test_clip_requires_feature_flag_commit_and_active_scoped_consent(tmp_path):
    repo, _, consent, session = setup_authorized(tmp_path)
    now = datetime.now(timezone.utc)
    off = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "off"), RetentionSettings(), now=lambda: now)
    selected = selected_audio()
    assert off.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected, evidence_selection_committed=True) is None
    enabled = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=2, evidence_directory=tmp_path / "clips"), now=lambda: now)
    assert enabled.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected, evidence_selection_committed=False) is None
    clip_id = enabled.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected, evidence_selection_committed=True)
    assert clip_id and repo.load_evidence_clip(clip_id).expires_at.startswith(
        (now + timedelta(days=2)).date().isoformat()
    )


def test_revocation_purges_file_and_live_metadata_idempotently(tmp_path):
    repo, _, consent, session = setup_authorized(tmp_path)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    retention = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=2), now=lambda: now)
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected_audio(), evidence_selection_committed=True)
    service = ProvisioningService(repo, __import__('math_tutor.infrastructure.curriculum_loader',fromlist=['load_curriculum_catalogs']).load_curriculum_catalogs(Path('src/math_tutor/curricula/primary-math-v1.yaml'),Path('src/math_tutor/curricula/activity-templates-v1.yaml'))[0], retention)
    service.revoke_audio_consent(RevokeAudioConsent("learner-clip", consent.consent_id), now=now)
    service.revoke_audio_consent(RevokeAudioConsent("learner-clip", consent.consent_id), now=now)
    assert repo.load_evidence_clip(clip_id) is None
    assert not list((tmp_path / "clips").glob("*.wav"))
    assert retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected_audio(), evidence_selection_committed=True) is None


def test_expired_and_manual_deletion_are_retry_safe(tmp_path):
    repo, _, _, session = setup_authorized(tmp_path)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    retention = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=1), now=lambda: now)
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected_audio(), evidence_selection_committed=True)
    assert retention.delete_clip(clip_id, authorized=True) == 1
    assert retention.delete_clip(clip_id, authorized=True) == 0
    assert repo.load_evidence_clip(clip_id) is None


def test_expiry_sweep_and_partial_metadata_failure_are_retry_safe(tmp_path):
    repo, _, _, session = setup_authorized(tmp_path)
    clock = [datetime(2026, 9, 6, 12, tzinfo=timezone.utc)]
    retention = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=1), now=lambda: clock[0])
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected_audio(), evidence_selection_committed=True)
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
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected_audio(), evidence_selection_committed=True)
    assert len(repo.claim_evidence_clips(clip_id=clip_id, claimed_at=now, claim_id="process-a")) == 1
    assert SQLiteTutoringRepository(repo.database).claim_evidence_clips(clip_id=clip_id, claimed_at=now, claim_id="process-b") == ()


def test_legacy_clip_write_bypass_is_rejected_and_deleting_rows_are_not_reviewable(tmp_path):
    repo, _, _, session = setup_authorized(tmp_path)
    try:
        repo.save_evidence_clip("legacy-clip", "evidence-clip", duration_seconds=1, storage_key="legacy.bin", expires_at="2030-01-01T00:00:00Z")
    except ValueError as error:
        assert str(error) == "consent-gated clip API required"
    else:
        raise AssertionError("legacy clip write bypassed consent")
    with repo._connect() as db:
        db.execute("INSERT INTO evidence_clips(clip_id,evidence_id,learner_id,session_id,duration_seconds,storage_key,expires_at,consent_id,consent_snapshot_id,deletion_state) VALUES(?,?,?,?,?,?,?,?,?,'deleting')", ("opaque_legacy_clip","evidence-clip","learner-clip",session.tutoring_session_id,1,"opaque_legacy_clip.wav","2030-01-01T00:00:00Z",None,None))
    assert repo.load_session_aggregate(session.tutoring_session_id).clips == ()


def test_revocation_recheck_clears_live_pcm_and_blocks_future_frames(tmp_path):
    repo, service, consent, session = setup_authorized(tmp_path)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    authorize = lambda session_id, at: repo.authorize_session_clip_buffer(
        learner_id="learner-clip", session_id=session_id,
        snapshot_id=session.audio_consent_snapshot_id, at=at)
    buffers = SessionAudioBuffers(enabled=True, authorize=authorize, sample_rate=1, sample_width=2)
    buffers.append(session.tutoring_session_id, AudioFrame(struct.pack("<h", 1), 1, now, 1, 1, 2))
    assert buffers.buffered_duration(session.tutoring_session_id) == 1
    service.revoke_audio_consent(RevokeAudioConsent("learner-clip", consent.consent_id), now=now)
    buffers.append(session.tutoring_session_id, AudioFrame(struct.pack("<h", 2), 1, now + timedelta(seconds=1), 1, 1, 2))
    assert buffers.buffered_duration(session.tutoring_session_id) == 0


def test_incremental_migration_quarantines_preconsent_clip_rows(tmp_path):
    repo, _, _, session = setup_authorized(tmp_path)
    store = OpaqueClipStore(tmp_path / "legacy-files")
    (store.root / "opaque_preconsent_clip.wav").write_bytes(b"legacy")
    with repo._connect() as db:
        db.execute("INSERT INTO evidence_clips(clip_id,evidence_id,learner_id,session_id,duration_seconds,storage_key,expires_at) VALUES(?,?,?,?,?,?,?)", ("opaque_preconsent_clip","evidence-clip","learner-clip",session.tutoring_session_id,1,"opaque_preconsent_clip.wav","2030-01-01T00:00:00+00:00"))
        db.execute("DELETE FROM schema_migrations WHERE version=11")
    migrate(repo.database)
    with repo._connect() as db:
        row = db.execute("SELECT deletion_state,expires_at FROM evidence_clips WHERE clip_id='opaque_preconsent_clip'").fetchone()
    assert tuple(row) == ("deleting", "1970-01-01T00:00:00+00:00")
    assert repo.load_evidence_clip("opaque_preconsent_clip") is None
    ClipRetentionService(repo, store, RetentionSettings(enabled=False)).sweep_expired()
    with repo._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM evidence_clips WHERE clip_id='opaque_preconsent_clip'").fetchone()[0] == 0
        assert db.execute("SELECT reason FROM audio_clip_deletion_tombstones WHERE clip_id='opaque_preconsent_clip'").fetchone()[0] == "expired"
    assert not (store.root / "opaque_preconsent_clip.wav").exists()


def test_active_consent_does_not_expire_when_its_clip_retention_window_passes(tmp_path):
    repo, _, _, session = setup_authorized(tmp_path)
    now = datetime.now(timezone.utc)
    retention = ClipRetentionService(repo, OpaqueClipStore(tmp_path / "clips"), RetentionSettings(enabled=True, retention_days=1), now=lambda: now)
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected_audio(), evidence_selection_committed=True)
    assert clip_id is not None
    assert repo.load_evidence_clip(clip_id).expires_at.startswith(
        (now + timedelta(days=1)).date().isoformat()
    )


def test_restart_recovers_stale_revocation_delete_before_clip_expiry(tmp_path):
    repo, _, _, session = setup_authorized(tmp_path)
    clock = [datetime(2026, 9, 6, 12, tzinfo=timezone.utc)]
    store = OpaqueClipStore(tmp_path / "clips")
    retention = ClipRetentionService(repo, store, RetentionSettings(enabled=True, retention_days=2), now=lambda: clock[0])
    clip_id = retention.persist_selected(evidence_id="evidence-clip", learner_id="learner-clip", session_id=session.tutoring_session_id, consent_snapshot_id=session.audio_consent_snapshot_id, selected=selected_audio(), evidence_selection_committed=True)
    claimed = repo.claim_evidence_clips(clip_id=clip_id, claimed_at=clock[0], claim_id="crashed", reason="consent-revoked")
    assert len(claimed) == 1
    store.delete(claimed[0].storage_key)  # process dies before durable completion
    clock[0] += timedelta(minutes=6)
    restarted = ClipRetentionService(SQLiteTutoringRepository(repo.database), store, RetentionSettings(enabled=True, retention_days=2), now=lambda: clock[0])
    assert restarted.maintenance() == 1
    with repo._connect() as db:
        tombstone = db.execute("SELECT reason FROM audio_clip_deletion_tombstones WHERE clip_id=?", (clip_id,)).fetchone()
    assert tombstone[0] == "consent-revoked"


def test_startup_reconciles_crash_after_intent_and_after_file_write(tmp_path):
    repo, _, consent, session = setup_authorized(tmp_path)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    store = OpaqueClipStore(tmp_path / "clips")
    clip_id = "opaque_pending_clip_12345"
    repo.create_clip_write_intent(clip_id=clip_id, evidence_id="evidence-clip", consent_id=consent.consent_id, snapshot_id=session.audio_consent_snapshot_id, duration_seconds=1, storage_key=f"{clip_id}.wav", captured_at=now, expires_at=now + timedelta(days=1))
    store.write(clip_id, selected_audio().payload)  # crash before finalize
    unknown = store.root / "opaque_unknown_clip_12345.wav"
    unknown.write_bytes(selected_audio().payload)
    now += timedelta(minutes=6)
    retention = ClipRetentionService(repo, store, RetentionSettings(enabled=True), now=lambda: now)
    assert retention.reconcile_startup() == 2
    assert not store.list_storage_keys()
    with repo._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audio_clip_write_intents").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM audio_orphan_deletion_audit").fetchone()[0] == 1


def test_reconciliation_never_deletes_fresh_intent_or_newly_live_file(tmp_path):
    repo_a, _, consent, session = setup_authorized(tmp_path)
    repo_b = SQLiteTutoringRepository(repo_a.database)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    store = OpaqueClipStore(tmp_path / "clips")
    clip_id = "opaque_inflight_clip_12345"
    storage_key = f"{clip_id}.wav"
    assert repo_a.create_clip_write_intent(
        clip_id=clip_id, evidence_id="evidence-clip", consent_id=consent.consent_id,
        snapshot_id=session.audio_consent_snapshot_id, duration_seconds=1,
        storage_key=storage_key, captured_at=now, expires_at=now + timedelta(days=1),
    )
    store.write(clip_id, selected_audio().payload)
    reconciler = ClipRetentionService(repo_b, store, RetentionSettings(enabled=True), now=lambda: now)
    assert reconciler.reconcile_startup() == 0
    assert storage_key in store.list_storage_keys()
    assert repo_a.finalize_clip_write_intent(clip_id)
    assert reconciler.reconcile_startup() == 0
    assert storage_key in store.list_storage_keys()
