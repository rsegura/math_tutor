from dataclasses import replace

import pytest

from math_tutor.application.summary import (
    NarrativeClaim,
    NarrativeValidationError,
    RegulationSummaryItem,
    SummaryActivityRef,
    SessionSummarySource,
    SummaryService,
)
from math_tutor.application.ports import RegulationEvent, RegulationOutcome
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
from math_tutor.domain.regulation import (
    ConfidenceBand,
    ConversationalSignal,
    ExecutedRegulationAction,
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
        (proposal,), tuple(discarded), ("add",),
        (SummaryActivityRef("learner-1", "session-1", f"activity-{record.evidence_id}", "add"),),
        ("session-1",),
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
        service.validate_narrative(summary, (NarrativeClaim("invented", "observation", False, "Texto", ("evidence-1",)),))
    with pytest.raises(NarrativeValidationError):
        service.validate_narrative(summary, (NarrativeClaim(summary.claims[0].claim_id, summary.claims[0].kind, summary.claims[0].is_hypothesis, "Texto", ("invented",)),))


def test_narrative_can_only_rephrase_the_authoritative_claim_links():
    service = SummaryService(Source(source()))
    summary = service.build("session-1")
    claim = summary.claims[0]

    narrative = service.validate_narrative(
        summary, (NarrativeClaim(claim.claim_id, claim.kind, claim.is_hypothesis, "Redacción revisable", claim.evidence_ids),)
    )

    assert narrative[0].claim_id == claim.claim_id


def test_regulation_events_are_separate_provisional_summary_items():
    event = RegulationEvent(
        "regulation-session-1-turn-2", "session-1", "activity-evidence-1",
        "turn-2", ConversationalSignal.FRUSTRATED, ConfidenceBand.HIGH,
        ExecutedRegulationAction.VALIDATE_EMOTION, 2,
        RegulationOutcome.ANSWERED, "2026-09-08T10:00:00+00:00",
    )

    summary = SummaryService(Source(replace(source(), regulation_events=(event,)))).build("session-1")

    assert summary.regulation_support == (
        RegulationSummaryItem(
            event_id="regulation-session-1-turn-2",
            signal=ConversationalSignal.FRUSTRATED,
            confidence_band=ConfidenceBand.HIGH,
            executed_action=ExecutedRegulationAction.VALIDATE_EMOTION,
            outcome=RegulationOutcome.ANSWERED,
            ordinal=2,
            provisional=True,
        ),
    )
    assert "regulation-session-1-turn-2" not in {
        evidence_id for claim in summary.claims for evidence_id in claim.evidence_ids
    }


def test_regulation_event_cannot_be_laundered_into_a_narrative_claim():
    event = RegulationEvent(
        "regulation-session-1-turn-2", "session-1", "activity-evidence-1",
        "turn-2", ConversationalSignal.CONFUSED, ConfidenceBand.MEDIUM,
        ExecutedRegulationAction.SIMPLIFY_LANGUAGE, 2,
        RegulationOutcome.REPEATED_DIFFICULTY, "2026-09-08T10:00:00+00:00",
    )
    service = SummaryService(Source(replace(source(), regulation_events=(event,))))
    summary = service.build("session-1")

    with pytest.raises(NarrativeValidationError, match="unknown"):
        service.validate_narrative(summary, (
            NarrativeClaim(event.event_id, "observation", False, "Tiene una dificultad", (event.event_id,)),
        ))


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
        ("add",), (
            SummaryActivityRef("learner-1", "session-old", "activity-old", "add"),
            SummaryActivityRef("learner-1", "session-1", "activity-current", "add"),
        ), ("session-1",),
    )

    summary = SummaryService(Source(value)).build("session-1")

    observations = [item for item in summary.claims if item.kind == "observation"]
    proposal_claim = next(item for item in summary.claims if item.kind == "profile-proposal")
    assert [item.evidence_ids for item in observations] == [("current",)]
    assert proposal_claim.evidence_ids == ("old", "current")


def test_narrative_cannot_launder_hypothesis_as_fact():
    service = SummaryService(Source(source()))
    summary = service.build("session-1")
    hypothesis = next(item for item in summary.claims if item.is_hypothesis)

    with pytest.raises(NarrativeValidationError, match="classification"):
        service.validate_narrative(summary, (
            NarrativeClaim(hypothesis.claim_id, hypothesis.kind, False, "Hecho", hypothesis.evidence_ids),
        ))


@pytest.mark.parametrize("corrupt", ["evidence-learner", "activity-reference", "activity-owner", "proposal-reference", "proposal-session", "estimate-owner"])
def test_summary_fails_closed_on_malformed_port_source(corrupt):
    value = source()
    record = value.evidence[0]
    if corrupt == "evidence-learner":
        bad_observation = replace(record.observation, learner_id="other")
        value = replace(value, evidence=(replace(record, learner_id="other", observation=bad_observation),))
    elif corrupt == "activity-reference":
        value = replace(value, activity_refs=())
    elif corrupt == "activity-owner":
        value = replace(value, activity_refs=(replace(value.activity_refs[0], learner_id="other"),))
    elif corrupt == "proposal-reference":
        value = replace(value, proposals=(replace(value.proposals[0], evidence_ids=("missing",)),))
    elif corrupt == "proposal-session":
        value = replace(value, proposal_session_ids=("session-other",))
    else:
        value = replace(value, estimates=(replace(value.estimates[0], learner_id="other"),))

    with pytest.raises(ValueError, match="malformed-summary-source"):
        SummaryService(Source(value)).build("session-1")


def test_proposal_disappears_when_any_supporting_evidence_is_discarded():
    first = evidence("first")
    second = evidence("second")
    proposal = ProposedProfileChange(
        "learner-1", "add", CompetencyState.NOT_OBSERVED,
        CompetencyState.EXPLORING, ("first", "second"),
        (first.observation.observation_id, second.observation.observation_id),
        1, "policy-v1",
    )
    value = SessionSummarySource(
        "session-1", "learner-1", 1, 1, (first, second), (), (proposal,),
        ("first",), ("add",), (
            SummaryActivityRef("learner-1", "session-1", "activity-first", "add"),
            SummaryActivityRef("learner-1", "session-1", "activity-second", "add"),
        ), ("session-1",),
    )

    summary = SummaryService(Source(value)).build("session-1")

    assert not [claim for claim in summary.claims if claim.kind == "profile-proposal"]
