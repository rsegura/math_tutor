import hashlib
import sqlite3
import threading
import pytest
from pathlib import Path
import shutil
from fastapi.testclient import TestClient

from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, ProvisioningService, RevokeAudioConsent, SessionLimits, StartLearningSession
from math_tutor.application.provisioning import DEFAULT_REGULATION_POLICY
from math_tutor.domain.regulation import PedagogicalStrategy, RegulationPolicy
from math_tutor.domain.learning import LearningPlan, PresentationProfile
from math_tutor.infrastructure.persistence.repositories import _dump
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from math_tutor.application.provisioning import ProvisioningError
from web.app import WebSettings, create_app


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


def test_running_session_bootstraps_its_policy_snapshot_after_plan_update(tmp_path):
    database=tmp_path/'snapshot.db'; migrate(database)
    root=__import__('pathlib').Path('src/math_tutor/curricula')
    catalog,_=load_curriculum_catalogs(root/'primary-math-v1.yaml',root/'activity-templates-v1.yaml')
    repo=SQLiteTutoringRepository(database); service=ProvisioningService(repo,catalog,Purger())
    service.create_learner(CreateLearner('learner','Sol',9))
    old_policy=RegulationPolicy((PedagogicalStrategy.REPEAT_INSTRUCTION,PedagogicalStrategy.REDIRECT_GENTLY,PedagogicalStrategy.VALIDATE_EMOTION,PedagogicalStrategy.TAKE_SHORT_PAUSE),3)
    plan=service.create_learning_plan(CreateLearningPlan('plan','learner',('units-tens',),(),SessionLimits(10,3),regulation_policy=old_policy))
    started=service.start_learning_session(StartLearningSession('learner'))
    service.update_learning_plan(plan.with_changes(expected_version=1,regulation_policy=DEFAULT_REGULATION_POLICY))
    bootstrap=repo.authorise_learner_join(started.tutoring_session_id,started.join_code,now=__import__('datetime').datetime.now(__import__('datetime').timezone.utc))
    assert bootstrap.plan.version == 1
    assert bootstrap.plan.regulation_policy == old_policy


def test_malformed_stored_regulation_policy_fails_closed(tmp_path):
    database=tmp_path/'malformed.db'; migrate(database)
    root=__import__('pathlib').Path('src/math_tutor/curricula')
    catalog,_=load_curriculum_catalogs(root/'primary-math-v1.yaml',root/'activity-templates-v1.yaml')
    repo=SQLiteTutoringRepository(database); service=ProvisioningService(repo,catalog,Purger())
    service.create_learner(CreateLearner('learner','Sol',9))
    service.create_learning_plan(CreateLearningPlan('plan','learner',('units-tens',),(),SessionLimits(10,3)))
    with sqlite3.connect(database) as db:
        db.execute("update provisioned_plans set regulation_policy_json='{}'")
    with pytest.raises(ValueError, match='invalid-regulation-policy'):
        repo.load_current_provisioned_plan('learner')


def test_migration_assigns_versioned_default_policy_to_legacy_plan_rows(tmp_path):
    legacy_migrations=tmp_path/'migrations'; legacy_migrations.mkdir()
    source=Path('src/math_tutor/infrastructure/persistence/migrations')
    for migration in source.glob('*.sql'):
        if int(migration.name.split('_',1)[0]) <= 19:
            shutil.copyfile(migration, legacy_migrations/migration.name)
    database=tmp_path/'legacy-plan.db'; migrate(database,migration_dir=legacy_migrations)
    plan=LearningPlan('learner',('units-tens',),('units-tens',),PresentationProfile.for_age(8),'plan',1)
    with sqlite3.connect(database) as db:
        db.execute("insert into learners(learner_id,curriculum_snapshot,curriculum_version,pseudonym,age_years) values(?,?,?,?,?)",('learner','primary-math-v1','primary-math/v1','Sol',8))
        db.execute("insert into curriculum_snapshots(learner_id,curriculum_version,curriculum_snapshot) values(?,?,?)",('learner','primary-math/v1','primary-math-v1'))
        db.execute("insert into learning_plans(plan_id,version,learner_id,plan_json,policy_version,curriculum_version) values(?,?,?,?,?,?)",('plan',1,'learner',_dump(plan),'provisioning/v1','primary-math/v1'))
        db.execute("insert into provisioned_plans(plan_id,version,learner_id,adaptations_json,duration_minutes,max_activities) values(?,?,?,?,?,?)",('plan',1,'learner','[]',10,3))
    migrate(database)
    loaded=SQLiteTutoringRepository(database).load_current_provisioned_plan('learner')
    assert loaded is not None
    assert loaded.regulation_policy == DEFAULT_REGULATION_POLICY


def test_revoked_stale_consent_object_cannot_create_session_or_partial_join_rows(tmp_path):
    database=tmp_path/'atomic.db'; migrate(database)
    root=__import__('pathlib').Path('src/math_tutor/curricula')
    catalog,_=load_curriculum_catalogs(root/'primary-math-v1.yaml',root/'activity-templates-v1.yaml')
    repo=SQLiteTutoringRepository(database); service=ProvisioningService(repo,catalog,Purger())
    service.create_learner(CreateLearner('learner','Sol',9))
    plan=service.create_learning_plan(CreateLearningPlan('plan','learner',('units-tens',),(),SessionLimits(10,3)))
    stale=service.grant_audio_consent('learner',retention_days=2)
    service.revoke_audio_consent(RevokeAudioConsent('learner',stale.consent_id))
    from math_tutor.domain.learning import LearningSession
    session=LearningSession.start(session_id='forged-session',plan=plan.plan)
    try:
        repo.create_provisioned_session(session,expected_plan_id='plan',expected_plan_version=1,expected_profile_version=1,join_code_hash='a'*64,join_expires_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc),consent_id=stale.consent_id)
    except ValueError:
        pass
    else:
        raise AssertionError('stale consent object bypassed canonical revocation')
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from learning_sessions where session_id='forged-session'").fetchone()[0] == 0
        assert db.execute("select count(*) from learner_join_codes where session_id='forged-session'").fetchone()[0] == 0


def test_session_port_rejects_forged_expected_plan_without_partial_rows(tmp_path):
    database=tmp_path/'forged.db'; migrate(database)
    root=__import__('pathlib').Path('src/math_tutor/curricula')
    catalog,_=load_curriculum_catalogs(root/'primary-math-v1.yaml',root/'activity-templates-v1.yaml')
    repo=SQLiteTutoringRepository(database); service=ProvisioningService(repo,catalog,Purger())
    service.create_learner(CreateLearner('learner','Sol',9))
    plan=service.create_learning_plan(CreateLearningPlan('plan','learner',('units-tens',),(),SessionLimits(10,3)))
    from math_tutor.domain.learning import LearningSession
    session=LearningSession.start(session_id='forged-plan-session',plan=plan.plan)
    try:
        repo.create_provisioned_session(session,expected_plan_id='attacker-plan',expected_plan_version=1,expected_profile_version=1,join_code_hash='b'*64,join_expires_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc),consent_id=None)
    except ValueError as error:
        assert str(error) == 'stale-plan-version'
    else:
        raise AssertionError('forged expected plan crossed the persistence fence')
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from learning_sessions where session_id='forged-plan-session'").fetchone()[0] == 0
        assert db.execute("select count(*) from learner_join_codes where session_id='forged-plan-session'").fetchone()[0] == 0


def test_concurrent_revoke_and_start_are_linearizable(tmp_path):
    database=tmp_path/'race.db'; migrate(database)
    root=__import__('pathlib').Path('src/math_tutor/curricula')
    catalog,_=load_curriculum_catalogs(root/'primary-math-v1.yaml',root/'activity-templates-v1.yaml')
    purger=Purger(); service=ProvisioningService(SQLiteTutoringRepository(database),catalog,purger)
    service.create_learner(CreateLearner('learner','Sol',9))
    service.create_learning_plan(CreateLearningPlan('plan','learner',('units-tens',),(),SessionLimits(10,3)))
    consent=service.grant_audio_consent('learner',retention_days=2)
    barrier=threading.Barrier(2); outcomes=[]
    def start():
        barrier.wait()
        try: outcomes.append(('start',service.start_learning_session(StartLearningSession('learner',consent.consent_id))))
        except ProvisioningError as error: outcomes.append(('start-error',str(error)))
    def revoke():
        barrier.wait(); outcomes.append(('revoke',service.revoke_audio_consent(RevokeAudioConsent('learner',consent.consent_id))))
    threads=[threading.Thread(target=start),threading.Thread(target=revoke)]
    [thread.start() for thread in threads]; [thread.join() for thread in threads]
    with sqlite3.connect(database) as db:
        snapshots=db.execute('select count(*) from session_audio_consent_snapshots').fetchone()[0]
        active=db.execute('select revoked_at from audio_consents where consent_id=?',(consent.consent_id,)).fetchone()[0]
    assert active is not None
    assert snapshots in (0,1)
    if snapshots == 0: assert any(kind=='start-error' for kind,_ in outcomes)


def test_empty_database_http_flow_covers_authorisation_cas_empty_scope_and_revocation(tmp_path):
    database=tmp_path/'http-empty.db'; migrate(database)
    root=__import__('pathlib').Path('src/math_tutor/curricula')
    catalog,_=load_curriculum_catalogs(root/'primary-math-v1.yaml',root/'activity-templates-v1.yaml')
    purger=Purger(); service=ProvisioningService(SQLiteTutoringRepository(database),catalog,purger)
    token='integration-server-token-123456'; api=TestClient(create_app(WebSettings(True,token),provisioning=service))
    learner={'learner_id':'learner-http','pseudonym':'Mar','age_years':8}
    assert api.post('/api/therapist/learners',json=learner).status_code == 401
    headers={'Authorization':f'Bearer {token}'}
    assert api.post('/api/therapist/learners',headers=headers,json=learner).status_code == 201
    empty={'plan_id':'plan-http','expected_version':0,'objective_ids':[],'adaptations':[],'limits':{'duration_minutes':10,'max_activities':3}}
    assert api.post('/api/therapist/learners/learner-http/plans',headers=headers,json=empty).status_code == 422
    plan={**empty,'objective_ids':['units-tens']}
    assert api.post('/api/therapist/learners/learner-http/plans',headers=headers,json=plan).status_code == 201
    stale={'expected_version':0,'objective_ids':['units-tens'],'adaptations':[],'limits':plan['limits']}
    assert api.put('/api/therapist/learners/learner-http/plans/plan-http',headers=headers,json=stale).status_code == 409
    no_consent=api.post('/api/therapist/learners/learner-http/sessions',headers=headers,json={})
    assert no_consent.status_code == 201 and no_consent.json()['audio_consent_snapshot_id'] is None
    consent=api.post('/api/therapist/learners/learner-http/audio-consents',headers=headers,json={'retention_days':2}).json()
    with_consent=api.post('/api/therapist/learners/learner-http/sessions',headers=headers,json={'audio_consent_id':consent['consent_id']})
    assert with_consent.status_code == 201 and token not in with_consent.text
    url=f"/api/therapist/learners/learner-http/audio-consents/{consent['consent_id']}"
    assert api.delete(url,headers=headers).json()['active'] is False
    assert api.delete(url,headers=headers).json()['active'] is False
    assert service.repository.load_state(with_consent.json()['tutoring_session_id']).session.can_continue
