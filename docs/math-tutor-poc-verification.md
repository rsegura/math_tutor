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
| `make test` | `723 passed, 1 deselected in 36.30s`; process exit `0` | Pass |
| `make eval-math` | 10 scenarios; `hard_failures: []`; process exit `0` | Pass |
| `docker compose --profile dev config` | Rendered services `agent`, `livekit`, `tooling`, and `web`, plus network `poc_math_default`; no validation error; process exit `0` | Pass |

The Compose result validates rendering only. It was produced with an empty
interpolated `THERAPIST_API_TOKEN` and does not show that `web` can start:
Compose enables the therapist API, while `WebSettings` rejects a missing,
weak, or placeholder token. A non-placeholder token of at least 24 characters
and 8 distinct characters is a prerequisite for `make up`.

The offline eval additionally reported zero mathematical speech errors,
unsupported profile updates, STT misattributions, ignored stops, and
diagnostic/privacy violations. Its deterministic p95 fixture latency was 380
ms. The adequate-or-correctable proportion was 1.0 against a target of 0.8,
but those intervention ratings are explicit scenario fixtures, not therapist
ratings. The reported 146-second review fixture total is likewise not an
observed professional review time.

The opt-in OpenRouter smoke was re-run on 2026-09-07 using the locally configured
OpenRouter model and credential mapping without printing the credential. It
exercised one Responses API call, the real `give_hint` function contract, the
production canonical parser, and a 64-token output budget: `1 passed in 1.67s`,
followed by `PASS: OpenRouter Responses tool smoke`, process exit `0`. This is
a provider-contract smoke only; it does not validate voice behavior or
educational quality. Re-runs require `OPENROUTER_API_KEY` and an
`OPENROUTER_MODEL` that supports Responses and tool calling. Missing
credentials produce an explicit skip, never a live-provider pass.

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
| Help/repeat does not create wrong-answer evidence | Covered by automated deterministic routing, persistence, replay, and mutation-fence tests; live voice replay pending |
| LLM failure recovery is bounded | Automated tests cover two non-terminal recoveries and a durable stop on the third consecutive current-generation failure; live-provider voice recovery pending |
| LiveKit shutdown accepts an existing `Task` | Covered by lifecycle tests for completion, timeout, cancellation ownership, later cleanup, and idempotency; live teardown replay pending |

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

- An informal adult-operated engineering session connected the live voice stack
  and exposed the motivating failure: repeated requests for help were followed
  by a provider failure, an immediate terminal response, and a shutdown
  `TypeError` because `delete_room()` returned an existing `Task`. This is useful
  defect evidence, not a passed or completed supervised voice gate. The bounded
  recovery and Task-aware teardown have automated coverage but have not been
  replayed under the required attested scenario.
- end-to-end voice latency remains unmeasured under the formal gate;
- interruption and immediate stop are unverified on a real audio session;
- selective clip capture, revocation, deletion, and retention are unverified
  end to end on a real session;
- therapist review time and usability are unmeasured;
- OpenRouter passed its one-call contract smoke; end-to-end voice remains
  unverified.

The implemented recovery contract is deterministic: recognised help/repeat
turns use reviewed hints or the canonical prompt and create no wrong-answer
evidence; a durable receipt makes retries idempotent. Recoverable current-turn
LLM failures 1 and 2 retry without ending the session, while failure 3 performs
one durable terminal stop. Valid replies and applied/replayed tools reset the
counter; help and low-confidence STT preserve it; cancelled or stale turns are
neutral. Logged failure records are limited to an event name plus category,
provider, model, session identifier, and consecutive count, with no provider
message, body, transcript, secret, or exception traceback.

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
