# Primary Mathematics Voice Tutor — Viability and Design

Date: 2026-09-04  
Status: Approved design  
Starting point: the existing voice medical pre-screening PoC

## 1. Decision and product boundary

It is viable to reuse the existing voice-agent architecture for a primary
mathematics tutor serving children with learning difficulties associated with
neurological injuries. The reusable asset is the controlled-execution
architecture, not the screening domain model.

The first product is not an autonomous teacher or a diagnostic medical device.
It is a supervised, adaptive practice assistant that:

- works on objectives selected by a therapist;
- chooses activities, order, difficulty, and hints within those objectives;
- observes both answers and the assistance required;
- proposes interpretations and next objectives for professional review; and
- produces a compact, evidence-linked session summary.

The therapist may be present, but the primary supervision mode is asynchronous
review after the session. Relevant profile changes remain provisional until
the applicable deterministic rules or the therapist approve them.

## 2. Recommended autonomy model

The initial PoC uses bounded autonomy:

| Actor | Authority |
|---|---|
| Therapist | Select objectives, functional adaptations, exclusions, and session limits; approve material profile changes |
| LLM | Interpret explanations and propose the next pedagogical intervention |
| Harness | Validate evidence, mathematical correctness, permissions, assistance limits, progression rules, and stop conditions |
| Domain | Maintain objective state and calculate provisional progression deterministically |

The autonomy roadmap is:

1. Therapist selects objectives; agent adapts exercises and hints.
2. Agent proposes objective changes; therapist approves or corrects them.
3. Agent applies low-risk objective changes supported by sufficient evidence.
4. Agent conducts a supervised initial assessment and proposes an explainable
   learning plan.

## 3. Curriculum and learner model

The architecture can represent content from first through sixth year of
Spanish primary education. Age controls language, tone, presentation, and
interaction constraints. It does not determine mathematical level.

Content is organised as a competency graph with prerequisites rather than six
rigid course silos. A learner may work at different curriculum bands in
different competencies.

Suggested bands are:

- Initial (years 1–2): counting, place value, comparison, simple addition and
  subtraction.
- Intermediate (years 3–4): larger place values, carrying and borrowing,
  multiplication, division, word problems, and introductory fractions.
- Advanced (years 5–6): fractions, decimals, percentages, proportionality,
  geometry, and problem solving.

The first vertical slice implements only number sense, units and tens, and
simple addition and subtraction. The curriculum representation may support the
full range, but breadth is not part of the first validation.

Each learning objective defines:

- its indicative curriculum band and prerequisites;
- permitted activity families and difficulty parameters;
- known conceptual error patterns;
- hints ordered from least to most assistance;
- acceptable evidence;
- progression and regression rules;
- functional adaptations; and
- pause and escalation conditions.

The learner profile stores functional adaptations and educational state, not
unnecessary clinical history. It distinguishes current performance from
sustained learning and the confidence of the measurement.

Suggested competency states are:

- `NOT_OBSERVED`
- `EXPLORING`
- `WITH_INTENSIVE_HELP`
- `WITH_LIGHT_HELP`
- `INDEPENDENT`
- `GENERALIZED`
- `NEEDS_REVIEW`

No competency advances on one answer alone. Advancement requires multiple
pieces of evidence across varied activities and, for stronger claims, across
sessions.

## 4. Architecture

The existing structure remains:

```text
VOICE
  -> STT + confidence
  -> LLM interpretation and proposal
  -> PEDAGOGICAL HARNESS
       - validate response and evidence
       - verify mathematics
       - authorize intervention
       - limit hints, attempts, and difficulty changes
       - control adaptation
  -> EDUCATIONAL DOMAIN
       - learning plan
       - objectives and prerequisites
       - activities and observations
       - provisional progression
  -> DURABLE STATE + SELECTIVE EVIDENCE
  -> SUMMARY + THERAPIST REVIEW
```

The main domain components are:

- `CurriculumCatalog`
- `LearningPlan`
- `LearningSession`
- `Activity`
- `Observation`
- `EvidenceRecord`
- `SkillEstimate`
- `TherapistReview`
- `LearnerProfile`

Candidate agent actions include presenting an activity, recording an answer,
requesting clarification, giving a hint, adapting difficulty, recording an
observation, proposing evidence, proposing a competency update, and ending the
session. The final tool surface should remain small: deterministic activity
generation and answer checking need not become model tools.

## 5. Session flow

Initial sessions target 10–15 minutes:

1. The therapist configures objectives, adaptations, and limits.
2. The agent welcomes the learner and verifies audio and instruction
   comprehension.
3. One or two easy activities calibrate the session.
4. The agent practises adaptively within the authorised objectives.
5. The agent closes with a descriptive, non-diagnostic summary.
6. The system builds observations, evidence, and provisional updates.
7. The therapist reviews, confirms, or corrects them.

For each activity, the harness distinguishes:

- an incorrect mathematical answer;
- an incomplete response;
- an ambiguous explanation;
- unreliable speech recognition;
- an instruction-comprehension problem;
- a known conceptual error; and
- fatigue, frustration, or a request to stop.

It never penalises a low-confidence transcription. Difficulty moves by at most
one step at a time and never increases on one answer. After repeated difficulty
the system changes formulation or representation, simplifies the activity,
changes objective temporarily, offers a pause, or ends the session. A request
to pause or stop takes priority over the educational plan.

Speech to the child remains descriptive. The agent does not give diagnoses,
compare the child with peers, promise progress, expose unreviewed hypotheses,
or use labels such as “low level.”

## 6. Evidence and privacy

The system does not retain full-session audio. It stores structured
transcription and short optional audio clips only for selected evidence.

An evidence record includes the objective, activity, expected answer,
transcribed response, optional clip, STT confidence, interpreted answer,
deterministic mathematical result, help provided, proposed observation, reason
for retention, and the minimum neighbouring context required to interpret it.

Typical clips span the prompt or hint, the learner response, and an immediate
clarification—normally 10–30 seconds.

The LLM may propose evidence, but mechanical rules decide what is retained.
Retention candidates include:

- a first independent success;
- a repeated conceptual error;
- a change in strategy;
- evidence used to change difficulty or competency state;
- significant assistance;
- contradiction with earlier sessions;
- low STT confidence;
- fatigue, frustration, or a stop request; and
- a therapist-requested sample.

Greetings, routine instructions, duplicate evidence, irrelevant social talk,
and uninformative silence are not retained.

The session summary is derived from structured observations and accepted
evidence rather than generated directly from the entire transcript. Every
material claim links to supporting evidence; unsupported interpretations are
labelled as hypotheses.

## 7. Therapist review and correction

Review actions are structured and versioned:

- confirm an observation;
- correct answer interpretation;
- mark an STT failure;
- change a proposed competency state;
- rate assistance as excessive or insufficient;
- discard evidence;
- add a functional adaptation;
- approve or reject a next objective; and
- record fatigue, frustration, or another interference.

Objective observations—answers, attempts, assistance, timing, interruptions—
are immutable. Interpretations and competency estimates are revisable. A
correction preserves the original proposal, the corrected value, and the
reason, then recalculates dependent profile state.

Therapist corrections become evaluation cases for future harness versions.
Agreement rate, rejected progression changes, incorrect attribution to the
learner, intervention quality, and review time measure whether more autonomy
has been earned.

## 8. Failure handling

The existing bounded-retry and escalation principles remain. In addition, the
pedagogical harness must:

- confirm uncertain STT rather than infer an error;
- refuse out-of-scope curriculum changes;
- verify every mathematical claim before speech;
- cap attempts and hints per activity;
- stop repeated unproductive loops;
- respect pauses and termination immediately;
- leave uncertain competency changes pending review; and
- retain an auditable reason for every adaptation.

## 9. Validation strategy

This PoC evaluates technical and pedagogical utility; it does not claim
clinical efficacy.

Validation proceeds through:

1. Offline simulations and scripted speech cases.
2. Therapist-operated sessions evaluating the agent’s decisions.
3. A small, supervised learner trial with consent and narrow objectives.
4. A broader pilot only after prior failure classes are closed.

Four dimensions are measured:

- Mathematical correctness: deterministic answer, procedure, hint, and scope
  checks.
- Pedagogical quality: therapist ratings of interventions, language,
  difficulty, persistence, and recommendations.
- Learner experience: instruction comprehension, repetitions, interruptions,
  abandonment, blocking, adult intervention, latency, and explicit
  frustration.
- Professional utility: review time, confirmed observations, corrected
  proposals, useful evidence, and unsupported claims.

Initial acceptance targets are:

- 100% of spoken calculations and solutions are mathematically correct;
- no material profile update lacks evidence;
- low-confidence STT is never treated as learner failure;
- all pause and stop requests are respected;
- at least 80% of interventions are rated adequate or correctable by the
  therapist (an experimental threshold to refine with specialists);
- a session report can be reviewed in under five minutes; and
- the agent emits no diagnostic claims.

The first content set should contain roughly 30–50 reviewed, parameterised
activity templates covering the initial vertical slice and representative
conceptual errors.

## 10. Viability conclusion

The proposal is technically viable and well matched to the current
architecture. Voice transport, the LLM/harness/domain separation, provider
adapters, durable state, audit, bounded execution, review UI concepts,
observability, and offline evaluation are reusable.

The principal effort is domain work: the competency graph, reviewed activities
and hints, progression rules, separation of conceptual error from communication
difficulty, specialist evaluation, and child-appropriate interaction design.
The recommended bounded-autonomy PoC provides meaningful agent behaviour while
preserving professional authority and producing the evidence required for
future autonomy.
