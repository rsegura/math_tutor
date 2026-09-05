from datetime import datetime, timedelta, timezone

import pytest

from math_tutor.application.provisioning import (
    CreateLearner,
    CreateLearningPlan,
    ProvisioningError,
    ProvisioningService,
    RevokeAudioConsent,
    SessionLimits,
    StartLearningSession,
)
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository


class Purger:
    def __init__(self): self.calls = []
    def purge_consent_scope(self, consent_id, session_ids):
        self.calls.append((consent_id, session_ids))


def service(tmp_path, purger=None):
    path = tmp_path / "provisioning.db"
    migrate(path)
    root = __import__("pathlib").Path("src/math_tutor/curricula")
    curriculum, _ = load_curriculum_catalogs(root / "primary-math-v1.yaml", root / "activity-templates-v1.yaml")
    return ProvisioningService(SQLiteTutoringRepository(path), curriculum, purger or Purger(), join_ttl=timedelta(minutes=5))


def test_learner_contains_only_minimum_pseudonymous_profile(tmp_path):
    svc = service(tmp_path)
    learner = svc.create_learner(CreateLearner("learner-opaque", "Luna", 8))
    assert learner.learner_id == "learner-opaque"
    assert learner.pseudonym == "Luna"
    assert not hasattr(learner, "legal_name") and not hasattr(learner, "date_of_birth")


def test_invalid_direct_command_values_fail_with_typed_error(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(ProvisioningError, match="invalid-learner"):
        svc.create_learner(CreateLearner("learner-opaque", "Luna", "eight"))


@pytest.mark.parametrize("values", [
    (" learner", "Luna", 8),
    ("learner", " ", 8),
    ("learner", "Luna", True),
])
def test_direct_learner_commands_reject_coercive_or_untrimmed_values_without_writes(tmp_path, values):
    svc = service(tmp_path)
    with pytest.raises(ProvisioningError, match="invalid-learner"):
        svc.create_learner(CreateLearner(*values))
    assert svc.repository.load_learner("learner") is None


def test_direct_plan_command_rejects_invalid_ids_versions_and_scope_without_writes(tmp_path):
    svc = service(tmp_path)
    svc.create_learner(CreateLearner("learner", "Luna", 8))
    invalid = (
        lambda: CreateLearningPlan(" plan", "learner", ("units-tens",), (), SessionLimits(10, 3)),
        lambda: CreateLearningPlan("plan", "learner", (" units-tens",), (), SessionLimits(10, 3)),
        lambda: CreateLearningPlan("plan", "learner", ("units-tens",), (" ",), SessionLimits(10, 3)),
        lambda: CreateLearningPlan("plan", "learner", ("units-tens",), (), SessionLimits(10, 3), expected_version=True),
        lambda: CreateLearningPlan("plan", "learner", ("units-tens",), (), SessionLimits(10, 3), expected_version=-1),
    )
    for build in invalid:
        with pytest.raises(ProvisioningError):
            svc.create_learning_plan(build())
    assert svc.repository.load_current_provisioned_plan("learner") is None


@pytest.mark.parametrize("values", [(True, 3), ("10", 3), (10, False), (10, 0)])
def test_direct_session_limits_are_strict_and_bounded(values):
    with pytest.raises(ProvisioningError, match="invalid-session-limits"):
        SessionLimits(*values)


def test_plan_rejects_unknown_objectives_and_adaptations(tmp_path):
    svc = service(tmp_path)
    svc.create_learner(CreateLearner("learner-1", "Luna", 8))
    with pytest.raises(ProvisioningError, match="unknown-objective"):
        svc.create_learning_plan(CreateLearningPlan("plan-1", "learner-1", ("missing",), (), SessionLimits(10, 3)))
    with pytest.raises(ProvisioningError, match="invalid-adaptation"):
        svc.create_learning_plan(CreateLearningPlan("plan-1", "learner-1", ("units-tens",), ("diagnose",), SessionLimits(10, 3)))


def test_plan_updates_use_compare_and_swap(tmp_path):
    svc = service(tmp_path)
    svc.create_learner(CreateLearner("learner-1", "Luna", 8))
    plan = svc.create_learning_plan(CreateLearningPlan("plan-1", "learner-1", ("units-tens",), ("short-instructions",), SessionLimits(10, 3)))
    assert svc.update_learning_plan(plan.with_changes(expected_version=1, objective_ids=("units-tens", "compose-two-digit"))).version == 2
    with pytest.raises(ProvisioningError, match="stale-plan-version"):
        svc.update_learning_plan(plan.with_changes(expected_version=1))


def test_plan_creation_requires_absent_version_expectation(tmp_path):
    svc = service(tmp_path)
    svc.create_learner(CreateLearner("learner-1", "Luna", 8))
    with pytest.raises(ProvisioningError, match="stale-plan-version"):
        svc.create_learning_plan(CreateLearningPlan("plan-1", "learner-1", ("units-tens",), (), SessionLimits(10, 3), expected_version=1))


def test_session_without_consent_is_allowed_and_join_secret_is_not_stored(tmp_path):
    svc = service(tmp_path)
    svc.create_learner(CreateLearner("learner-1", "Luna", 8))
    svc.create_learning_plan(CreateLearningPlan("plan-1", "learner-1", ("units-tens",), (), SessionLimits(10, 3)))
    started = svc.start_learning_session(StartLearningSession("learner-1"), now=datetime(2026, 9, 5, tzinfo=timezone.utc))
    assert started.audio_consent_snapshot_id is None
    assert started.join_code
    assert started.join_code not in svc.repository.load_join_code_hash(started.tutoring_session_id)


def test_revoke_is_idempotent_and_purges_all_bound_sessions(tmp_path):
    purger = Purger(); svc = service(tmp_path, purger)
    svc.create_learner(CreateLearner("learner-1", "Luna", 8))
    svc.create_learning_plan(CreateLearningPlan("plan-1", "learner-1", ("units-tens",), (), SessionLimits(10, 3)))
    consent = svc.grant_audio_consent("learner-1", retention_days=2)
    session = svc.start_learning_session(StartLearningSession("learner-1", consent.consent_id))
    command = RevokeAudioConsent("learner-1", consent.consent_id)
    first = svc.revoke_audio_consent(command); second = svc.revoke_audio_consent(command)
    assert first.version == second.version == 2
    assert purger.calls == [(consent.consent_id, (session.tutoring_session_id,))] * 2


def test_direct_consent_retention_rejects_boolean_without_a_write(tmp_path):
    svc = service(tmp_path)
    svc.create_learner(CreateLearner("learner-1", "Luna", 8))
    svc.create_learning_plan(CreateLearningPlan("plan-1", "learner-1", ("units-tens",), (), SessionLimits(10, 3)))
    with pytest.raises(ProvisioningError, match="retention days"):
        svc.grant_audio_consent("learner-1", retention_days=True)
