# Math Tutor Voice PoC verification record

Status date: 2026-09-07

Overall status: **automated gate passed; supervised learner trial blocked**.
This record distinguishes reproducible automated evidence from professional or
voice checks that have not happened. It is not an efficacy or safety approval.

## Automated evidence

The following commands were run from
`/Users/robertosegura/Develop/poc_math` on 2026-09-07.

| Command | Observed result | Status |
|---|---|---|
| `make test` | `612 passed in 32.63s`; process exit `0` | Pass |
| `make eval-math` | 10 scenarios; `hard_failures: []`; process exit `0` | Pass |
| `docker compose --profile dev config` | Rendered services `agent`, `livekit`, `tooling`, and `web`, plus network `poc_math_default`; no validation error; process exit `0` | Pass |

The offline eval additionally reported zero mathematical speech errors,
unsupported profile updates, STT misattributions, ignored stops, and
diagnostic/privacy violations. Its deterministic p95 fixture latency was 380
ms. The adequate-or-correctable proportion was 1.0 against a target of 0.8,
but those intervention ratings are explicit scenario fixtures, not therapist
ratings. The reported 146-second review fixture total is likewise not an
observed professional review time.

No live-provider test was run as part of this gate. `make test-live-llm` remains
opt-in and the repository currently documents that no live-provider tests are
implemented.

## Acceptance criteria

| Criterion | Evidence | Status |
|---|---|---|
| Spoken calculations and solutions are mathematically correct | Offline eval: `mathematical_speech_errors: 0` across its 10 scripted scenarios | Pass for offline catalog only |
| No material profile update lacks evidence | Offline eval: `unsupported_profile_updates: 0` | Pass for offline catalog only |
| Low-confidence STT is never treated as learner failure | Offline eval: `stt_misattributions: 0` | Pass for offline catalog only |
| Pause and stop requests are respected | Offline eval: `ignored_stops: 0`; real voice interruption has not been exercised | Partial; voice gate pending |
| At least 80% of interventions are adequate or correctable | Eval fixture result 1.0; no therapist ratings exist | Pending professional attestation |
| Session report reviewed in under five minutes | Only fixture duration exists; no timed therapist review occurred | Pending professional attestation |
| No diagnostic claims | Offline eval: `diagnostic_or_privacy_violations: 0` | Pass for offline catalog only |
| Compose configuration is valid | `docker compose --profile dev config`, exit `0` | Pass |

An offline pass is intentionally scoped to the reviewed fixture catalog. It
does not promote a partial criterion to a supervised-use approval.

## Implementation-plan final checklist

| Checklist item | Current verification status |
|---|---|
| All paths belong to the standalone math-tutor repository | Confirmed for the commands and Compose paths recorded above |
| Mathematical output is verified before speech | Covered by automated tests/eval; live speech pending |
| Low-confidence STT never degrades learner state | Covered by automated tests/eval; live STT pending |
| Stop and pause pre-empt pedagogy | Covered by automated tests/eval; natural voice interruption pending |
| Material profile updates are evidence-linked and reviewable | Covered by automated tests/eval; therapist workflow attestation pending |
| Objective observations are immutable; interpretations are versioned | Covered by the passing automated suite; professional reconstruction exercise pending |
| No full-session audio is persisted | Code/test policy present; real-session inspection pending |
| Every retained clip has a reason, evidence ID, and bounded duration | Covered by automated tests; real selective-capture inspection pending |
| Therapist can correct the agent and reconstruct prior state | UI/API tests pass; tabletop operator attestation pending |
| Acceptance gate fails closed on safety regressions | Covered by the automated suite and successful offline gate run |

Items marked pending are not treated as passed for supervised operation.

## Professional tabletop verification

Status: **not run**.

Required scope: a therapist must review the curriculum slice, activity
templates, ordered hints, stop behavior, summary claims, and correction
workflow. Every finding must identify severity, owner, disposition, and whether
it blocks a learner trial.

Operator/attestation: no therapist operator is recorded; no dated attestation,
rating sheet, findings log, or approval signature exists.

Open exception: the eval's `intervention_rating_fixtures` and
`review_fixture_duration_seconds` are synthetic acceptance fixtures. They
cannot substitute for therapist ratings or a timed report review.

Result: **blocking**. Do not begin a trial with children until a qualified
therapist completes this review and all blocking findings are closed or receive
an explicit, accountable disposition.

## Supervised voice gate

Status: **not run**.

Required scope: an adult-operated 10–15 minute session using the live voice
stack must verify conversational flow, natural interruption, a repeat after
low-confidence STT, selective evidence clips only, immediate stop, and a
therapist review completed in under five minutes.

Operator/attestation: no voice-gate operator, therapist reviewer, session ID,
timestamp, provider configuration, recording/evidence manifest, measured
latency trace, or signed result is recorded.

Open exceptions:

- live STT/LLM/TTS connectivity and end-to-end latency are unverified;
- interruption and immediate stop are unverified on a real audio session;
- selective clip capture, revocation, deletion, and retention are unverified
  end to end on a real session;
- therapist review time and usability are unmeasured;
- no live-provider smoke tests currently exist.

Result: **blocking**. A voice gate uses an adult operator only; it is not itself
permission to involve children.

## Preconditions for any trial with children

The tabletop and supervised voice gates above must pass. In addition, consent,
safeguarding, retention, and specialist-review requirements require separate,
recorded approval. At minimum the trial owner must record:

- named accountable operators and qualified specialist approval;
- approved narrow objectives, protocol, stop/escalation procedure, and adult
  supervision;
- active, scoped consent and withdrawal handling;
- approved data minimization, clip-retention/deletion behavior, and access
  controls;
- closure or accepted disposition of every blocking finding.

None of those operator attestations or approvals is present in this record.
Therefore the current repository state is suitable for further engineering and
adult-operated validation only, not a learner trial.

## Re-running and updating this record

Re-run the automated commands in [DOCKER.md](DOCKER.md) after any relevant
change and replace the evidence above with the newly observed output. Human
gates require names/roles, date, environment and provider versions, scenario or
session identifiers, measured results, findings, and an explicit pass/fail
attestation. Never infer a pass from missing evidence.
