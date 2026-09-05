"""Authorised provisioning use cases, independent of HTTP and voice vendors."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from math_tutor.application.ports import ClipPurgePort, ProvisioningRepository
from math_tutor.domain.audio_consent import AudioConsent
from math_tutor.domain.curriculum import CurriculumCatalog
from math_tutor.domain.learning import LearningPlan, LearningSession, PresentationProfile


class ProvisioningError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LearnerProfile:
    learner_id: str
    pseudonym: str
    age_years: int

    def __post_init__(self) -> None:
        if not isinstance(self.learner_id, str) or not self.learner_id or self.learner_id != self.learner_id.strip():
            raise ProvisioningError("invalid-learner")
        if not isinstance(self.pseudonym, str) or not self.pseudonym or self.pseudonym != self.pseudonym.strip():
            raise ProvisioningError("invalid-learner")
        if isinstance(self.age_years, bool) or not isinstance(self.age_years, int) or not 6 <= self.age_years <= 13:
            raise ProvisioningError("invalid-learner")


@dataclass(frozen=True, slots=True)
class SessionLimits:
    duration_minutes: int
    max_activities: int

    def __post_init__(self) -> None:
        if isinstance(self.duration_minutes, bool) or not isinstance(self.duration_minutes, int) or not 5 <= self.duration_minutes <= 30:
            raise ProvisioningError("invalid-session-limits")
        if isinstance(self.max_activities, bool) or not isinstance(self.max_activities, int) or not 1 <= self.max_activities <= 20:
            raise ProvisioningError("invalid-session-limits")


@dataclass(frozen=True, slots=True)
class CreateLearner:
    learner_id: str
    pseudonym: str
    age_years: int


@dataclass(frozen=True, slots=True)
class CreateLearningPlan:
    plan_id: str
    learner_id: str
    objective_ids: tuple[str, ...]
    adaptations: tuple[str, ...]
    limits: SessionLimits
    expected_version: int = 0


@dataclass(frozen=True, slots=True)
class UpdateLearningPlan:
    plan_id: str
    learner_id: str
    objective_ids: tuple[str, ...]
    adaptations: tuple[str, ...]
    limits: SessionLimits
    expected_version: int


@dataclass(frozen=True, slots=True)
class ProvisionedPlan:
    plan: LearningPlan
    adaptations: tuple[str, ...]
    limits: SessionLimits

    @property
    def version(self) -> int: return self.plan.version
    @property
    def plan_id(self) -> str: return self.plan.plan_id

    def with_changes(self, *, expected_version: int, objective_ids: tuple[str, ...] | None = None, adaptations: tuple[str, ...] | None = None, limits: SessionLimits | None = None) -> UpdateLearningPlan:
        return UpdateLearningPlan(self.plan.plan_id, self.plan.learner_id, objective_ids or self.plan.authorised_objective_ids, self.adaptations if adaptations is None else adaptations, self.limits if limits is None else limits, expected_version)


@dataclass(frozen=True, slots=True)
class StartLearningSession:
    learner_id: str
    audio_consent_id: str | None = None


@dataclass(frozen=True, slots=True)
class StartedSession:
    tutoring_session_id: str
    join_code: str
    join_expires_at: datetime
    plan_id: str
    plan_version: int
    audio_consent_snapshot_id: str | None


@dataclass(frozen=True, slots=True)
class RevokeAudioConsent:
    learner_id: str
    consent_id: str


class ProvisioningService:
    ALLOWED_ADAPTATIONS = frozenset({"short-instructions", "slow-pace", "extra-repetition", "concrete-examples", "reduced-choice"})

    def __init__(self, repository: ProvisioningRepository, curriculum: CurriculumCatalog, clip_purger: ClipPurgePort, *, join_ttl: timedelta = timedelta(minutes=5)):
        if not timedelta(seconds=30) <= join_ttl <= timedelta(minutes=15):
            raise ValueError("join TTL must be between 30 seconds and 15 minutes")
        self.repository, self.curriculum, self.clip_purger, self.join_ttl = repository, curriculum, clip_purger, join_ttl

    def create_learner(self, command: CreateLearner) -> LearnerProfile:
        try:
            pseudonym = command.pseudonym.strip()
        except AttributeError as error:
            raise ProvisioningError("invalid-learner") from error
        learner = LearnerProfile(command.learner_id, pseudonym, command.age_years)
        try:
            self.repository.create_learner_profile(learner, curriculum_snapshot="primary-math-v1", curriculum_version="primary-math/v1")
        except ValueError as error:
            raise ProvisioningError("learner-already-exists") from error
        return learner

    def _validate_scope(self, objectives: tuple[str, ...], adaptations: tuple[str, ...]) -> None:
        if not objectives:
            raise ProvisioningError("empty-objectives")
        known = {item.id for item in self.curriculum.objectives}
        if set(objectives) - known:
            raise ProvisioningError("unknown-objective")
        if len(set(objectives)) != len(objectives):
            raise ProvisioningError("duplicate-objective")
        if set(adaptations) - self.ALLOWED_ADAPTATIONS:
            raise ProvisioningError("invalid-adaptation")
        if len(set(adaptations)) != len(adaptations):
            raise ProvisioningError("duplicate-adaptation")

    def create_learning_plan(self, command: CreateLearningPlan) -> ProvisionedPlan:
        if command.expected_version != 0:
            raise ProvisioningError("stale-plan-version")
        learner = self.repository.load_learner_profile(command.learner_id)
        if learner is None: raise ProvisioningError("learner-not-found")
        self._validate_scope(command.objective_ids, command.adaptations)
        plan = LearningPlan(command.learner_id, command.objective_ids, command.objective_ids, PresentationProfile.for_age(learner.age_years), command.plan_id, 1)
        result = ProvisionedPlan(plan, tuple(command.adaptations), command.limits)
        try:
            self.repository.create_provisioned_plan(result)
        except ValueError as error:
            raise ProvisioningError("plan-already-exists") from error
        return result

    def update_learning_plan(self, command: UpdateLearningPlan) -> ProvisionedPlan:
        learner = self.repository.load_learner_profile(command.learner_id)
        if learner is None: raise ProvisioningError("learner-not-found")
        self._validate_scope(command.objective_ids, command.adaptations)
        plan = LearningPlan(command.learner_id, command.objective_ids, command.objective_ids, PresentationProfile.for_age(learner.age_years), command.plan_id, command.expected_version + 1)
        result = ProvisionedPlan(plan, tuple(command.adaptations), command.limits)
        if not self.repository.update_provisioned_plan(result, expected_version=command.expected_version):
            raise ProvisioningError("stale-plan-version")
        return result

    def grant_audio_consent(self, learner_id: str, *, retention_days: int, now: datetime | None = None) -> AudioConsent:
        plan = self.repository.load_current_provisioned_plan(learner_id)
        if plan is None: raise ProvisioningError("current-plan-not-found")
        at = now or datetime.now(timezone.utc)
        try:
            consent = AudioConsent(secrets.token_urlsafe(18), learner_id, plan.plan.plan_id, plan.version, retention_days, at)
        except ValueError as error:
            raise ProvisioningError(str(error)) from error
        self.repository.save_audio_consent(consent)
        return consent

    def revoke_audio_consent(self, command: RevokeAudioConsent, *, now: datetime | None = None) -> AudioConsent:
        consent = self.repository.revoke_audio_consent(command.consent_id, command.learner_id, now or datetime.now(timezone.utc))
        if consent is None: raise ProvisioningError("consent-not-found")
        sessions = self.repository.list_consent_session_ids(command.consent_id)
        self.clip_purger.purge_consent_scope(command.consent_id, sessions)
        return consent

    def start_learning_session(self, command: StartLearningSession, *, now: datetime | None = None) -> StartedSession:
        plan = self.repository.load_current_provisioned_plan(command.learner_id)
        if plan is None: raise ProvisioningError("current-plan-not-found")
        if not plan.plan.authorised_objective_ids: raise ProvisioningError("empty-objectives")
        consent = None
        if command.audio_consent_id is not None:
            consent = self.repository.load_audio_consent(command.audio_consent_id)
            if consent is None or not consent.active or consent.learner_id != command.learner_id or (consent.plan_id, consent.plan_version) != (plan.plan_id, plan.version):
                raise ProvisioningError("invalid-audio-consent")
        at = now or datetime.now(timezone.utc)
        session_id, join_code = secrets.token_urlsafe(18), secrets.token_urlsafe(24)
        session = LearningSession.start(session_id=session_id, plan=plan.plan)
        profile_version = self.repository.load_profile_version(command.learner_id)
        if profile_version is None:
            raise ProvisioningError("learner-profile-not-found")
        snapshot_id = self.repository.create_provisioned_session(session, profile_version=profile_version, join_code_hash=hashlib.sha256(join_code.encode()).hexdigest(), join_expires_at=at + self.join_ttl, consent=consent)
        return StartedSession(session_id, join_code, at + self.join_ttl, plan.plan_id, plan.version, snapshot_id)
