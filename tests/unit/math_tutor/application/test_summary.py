from dataclasses import replace

import pytest

from math_tutor.application.summary import (
    NarrativeClaim,
    NarrativeValidationError,
    SessionSummarySource,
    SummaryService,
)
from math_tutor.domain.evidence import (
    EvidenceRecord,
    Observation,
    ObservationOutcome,
    TranscriptionReliabilityPolicy,
)
from math_tutor.domain.learning import (
    CompetencyState,
    ProposedProfileChange,
    SkillEstimate,
)


def evidence(evidence_id="evidence-1", *, interpretation="comprende la suma"):
    observation = Observation(
        f"observation-{evidence_id}", "learner-1", "session-1", "add",
        f"activity-{evidence_id}", ObservationOutcome.CORRECT, .95, 0,
        TranscriptionReliabilityPolicy(.7), "cinco",
    )
    return EvidenceRecord.initial(
        evidence_id=evidence_id, learner_id="learner-1",
        observation=observation, interpretation=interpretation,
        reason_for_retention="first-independent-success",
    )


class Source:
    def __init__(self, value):
        self.value = value

    def load_summary_source(self, session_id):
        return self.value if session_id == "session-1" else None


def source(*, discarded=()):
    record = evidence()
    proposal = ProposedProfileChange(
        "learner-1", "add", CompetencyState.NOT_OBSERVED,
        CompetencyState.EXPLORING, (record.evidence_id,),
        (record.observation.observation_id,), 1, "policy-v1",
    )
    return SessionSummarySource(
        "session-1", "learner-1", 2, 1, (record,),
        (SkillEstimate("learner-1", "add", CompetencyState.NOT_OBSERVED),),
        (proposal,), tuple(discarded),
    )


def test_every_material_summary_claim_links_existing_canonical_evidence():
    summary = SummaryService(Source(source())).build("session-1")

    assert summary.claims
    assert all(claim.evidence_ids for claim in summary.claims)
    assert {item for claim in summary.claims for item in claim.evidence_ids} <= {"evidence-1"}


def test_interpretations_and_profile_proposals_are_labelled_hypotheses():
    summary = SummaryService(Source(source())).build("session-1")

    interpreted = [claim for claim in summary.claims if claim.kind in {"interpretation", "profile-proposal"}]
    assert interpreted
    assert all(claim.is_hypothesis for claim in interpreted)


def test_discarded_evidence_disappears_from_current_view_not_authoritative_history():
    summary = SummaryService(Source(source(discarded=("evidence-1",)))).build("session-1")

    assert summary.claims == ()
    assert summary.historical_evidence_ids == ("evidence-1",)
    assert summary.discarded_evidence_ids == ("evidence-1",)


def test_narrative_is_rejected_if_it_invents_claim_or_evidence_ids():
    service = SummaryService(Source(source()))
    summary = service.build("session-1")

    with pytest.raises(NarrativeValidationError):
        service.validate_narrative(summary, (NarrativeClaim("invented", "Texto", ("evidence-1",)),))
    with pytest.raises(NarrativeValidationError):
        service.validate_narrative(summary, (NarrativeClaim(summary.claims[0].claim_id, "Texto", ("invented",)),))


def test_narrative_can_only_rephrase_the_authoritative_claim_links():
    service = SummaryService(Source(source()))
    summary = service.build("session-1")
    claim = summary.claims[0]

    narrative = service.validate_narrative(
        summary, (NarrativeClaim(claim.claim_id, "Redacción revisable", claim.evidence_ids),)
    )

    assert narrative[0].claim_id == claim.claim_id


def test_cross_session_support_is_linked_without_reporting_old_turn_as_current():
    current = evidence("current")
    old = replace(
        evidence("old"),
        observation=replace(evidence("old").observation, session_id="session-old"),
    )
    proposal = ProposedProfileChange(
        "learner-1", "add", CompetencyState.NOT_OBSERVED,
        CompetencyState.EXPLORING, (old.evidence_id, current.evidence_id),
        (old.observation.observation_id, current.observation.observation_id),
        1, "policy-v1",
    )
    value = SessionSummarySource(
        "session-1", "learner-1", 1, 1, (old, current), (), (proposal,), (),
    )

    summary = SummaryService(Source(value)).build("session-1")

    observations = [item for item in summary.claims if item.kind == "observation"]
    proposal_claim = next(item for item in summary.claims if item.kind == "profile-proposal")
    assert [item.evidence_ids for item in observations] == [("current",)]
    assert proposal_claim.evidence_ids == ("old", "current")
