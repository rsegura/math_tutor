import hashlib
import sqlite3

from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, ProvisioningService, RevokeAudioConsent, SessionLimits, StartLearningSession
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository


class Purger:
    def __init__(self): self.scopes=[]
    def purge_consent_scope(self, consent_id, session_ids): self.scopes.append((consent_id, session_ids))


def test_empty_database_provisions_consent_and_no_consent_sessions(tmp_path):
    database=tmp_path/"empty.db"; migrate(database)
    root=__import__('pathlib').Path('src/math_tutor/curricula')
    catalog,_=load_curriculum_catalogs(root/'primary-math-v1.yaml',root/'activity-templates-v1.yaml')
    repo=SQLiteTutoringRepository(database); purger=Purger(); service=ProvisioningService(repo,catalog,purger)
    service.create_learner(CreateLearner('opaque-learner','Sol',9))
    service.create_learning_plan(CreateLearningPlan('opaque-plan','opaque-learner',('units-tens',),('slow-pace',),SessionLimits(12,5)))
    with sqlite3.connect(database) as db:
        db.execute("UPDATE learner_profile_versions SET version=2 WHERE learner_id='opaque-learner'")
    without=service.start_learning_session(StartLearningSession('opaque-learner'))
    consent=service.grant_audio_consent('opaque-learner',retention_days=3)
    with_audio=service.start_learning_session(StartLearningSession('opaque-learner',consent.consent_id))
    assert without.audio_consent_snapshot_id is None
    assert with_audio.audio_consent_snapshot_id is not None
    with sqlite3.connect(database) as db:
        assert db.execute('select profile_version from learning_sessions where session_id=?',(without.tutoring_session_id,)).fetchone()[0] == 2
        assert db.execute('select code_hash from learner_join_codes where session_id=?',(with_audio.tutoring_session_id,)).fetchone()[0] == hashlib.sha256(with_audio.join_code.encode()).hexdigest()
        assert not {'legal_name','date_of_birth','join_code'} & {row[1] for row in db.execute('pragma table_info(learners)')}
    service.revoke_audio_consent(RevokeAudioConsent('opaque-learner',consent.consent_id))
    service.revoke_audio_consent(RevokeAudioConsent('opaque-learner',consent.consent_id))
    assert repo.load_state(with_audio.tutoring_session_id).session.can_continue
    assert purger.scopes == [(consent.consent_id,(with_audio.tutoring_session_id,))]*2
