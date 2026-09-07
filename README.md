# Math Tutor Voice PoC

A Spanish-speaking voice tutor whose product vision covers primary-school
mathematics (years 1–6) for learners who may need additional support. The
implemented curriculum is currently a narrow `initial` vertical slice,
approximately aligned with years 1–2: counting and number sequences to 20,
comparison, units and tens, composing and decomposing two-digit numbers, the
number line, and addition and subtraction to 20. This proof of concept explores
a focused question: can a probabilistic language model make a conversation
natural while deterministic software retains control of mathematical
correctness, progression, safety, and durable state?

The current build supports bounded tutoring sessions, evidence-linked progress,
selective audio evidence, and therapist review. It is suitable for engineering
and adult-operated validation only. It has **not** passed the professional or
supervised-voice gates required for a trial involving children.

## How it works

```text
learner voice -> STT -> LLM proposal -> pedagogical harness
                                      -> deterministic math/domain rules
                                      -> persisted evidence -> therapist review
                                                        |
                                  validated Spanish reply -> TTS
```

The LLM interprets a turn and proposes either speech or a bounded pedagogical
tool call. It cannot mark an answer correct, mutate learner state, or choose an
unauthorised objective. The harness validates schemas, permissions, attempt and
session limits, progression rules, and stop conditions before domain code may
execute a change or release related speech. Mathematical answers are checked by
deterministic code. Domain and application code remain independent of voice and
model-provider SDKs, so the core can be tested offline.

## Technology

- Python 3.12 and `uv`
- LiveKit Agents and a local LiveKit server
- Deepgram or OpenAI speech-to-text
- OpenAI Responses API for the pedagogical model
- ElevenLabs or OpenAI text-to-speech
- FastAPI and a small static learner/reviewer UI
- SQLite for learner, session, evidence, consent, and review state
- YAML curriculum and activity-template catalogs
- Docker Compose, Make, and pytest

Provider choices are explicit and fail closed; arbitrary provider names are not
accepted.

## Repository layout

| Path | Purpose |
|---|---|
| `src/math_tutor/domain/` | Mathematics, curriculum, learning, evidence, and consent rules |
| `src/math_tutor/application/` | Use cases, session mutations, summaries, review, and provisioning |
| `src/math_tutor/harness/` | Bounded model contract and proposal-validation loop |
| `src/math_tutor/agent/` | LiveKit worker, provider adapters, and voice lifecycle |
| `src/math_tutor/infrastructure/` | SQLite, dispatch, curriculum loading, and evidence-clip adapters |
| `src/math_tutor/curricula/` | Versioned primary-math curriculum and activity templates |
| `web/` | FastAPI composition root and learner/therapist web surfaces |
| `evals/math_tutor/` | Deterministic fake-model acceptance scenarios and metrics |
| `tests/` | Unit, contract, and integration suites |
| `docs/` | Operations and verification evidence |

See [DESIGN.md](DESIGN.md) for the approved architecture and
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for its staged implementation.

## Prerequisites

- Docker with Docker Compose v2
- GNU Make
- Provider credentials only when running the live voice worker

Docker is the only supported environment for Python, `uv`, and pytest. Do not
run those tools directly on the host.

## Configuration

Create a local environment file:

```bash
cp .env.example .env
```

Before `make up`, replace `THERAPIST_API_TOKEN` with a private, non-placeholder
server secret containing at least 24 characters and at least 8 distinct
characters. The web process fails closed when the token is absent or weak. Do
not expose it to the learner client or commit `.env`.

For a live voice session, also configure:

- `STT_PROVIDER`, `STT_MODEL`, `STT_API_KEY`
- `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`
- `TTS_PROVIDER`, `TTS_MODEL`, `TTS_VOICE_ID`, `TTS_API_KEY`
- optionally, bounded `LLM_FIRST_RESPONSE_SECONDS` and `LLM_TOTAL_SECONDS`

Supported combinations are Deepgram or OpenAI for STT, OpenAI for the LLM, and
ElevenLabs or OpenAI for TTS. `LLM_TEMPERATURE` and `STORE_TRANSCRIPT` appear in
`.env.example` but are not currently passed into the Compose services and must
not be treated as active runtime controls.

Audio evidence is disabled by default. Enabling
`AUDIO_EVIDENCE_ENABLED=true` only enables the capability; it does not grant
consent. Retention must be configured between 1 and 30 days. See
[docs/DOCKER.md](docs/DOCKER.md) for all wired variables and runtime details.

## Run locally

```bash
make up
```

This starts LiveKit and the web service. It does not start the voice agent.

- Learner join page: <http://localhost:8080/>
- Therapist review page: <http://localhost:8080/tutoring-review.html>
- Local LiveKit WebSocket: `ws://localhost:7880`

Start the live voice worker separately after supplying provider credentials:

```bash
make agent
```

### Provision a local tutoring session

The learner page does not create learners, plans, or sessions. With `make up`
running, use the therapist API and the exact token configured in `.env`. Keep
the token in a shell variable so it is not copied into each request:

```bash
printf 'Local THERAPIST_API_TOKEN: '
read -r -s TUTOR_TOKEN
printf '\n'

curl --fail-with-body \
  -H "Authorization: Bearer ${TUTOR_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"learner_id":"learner-local-1","pseudonym":"Luna","age_years":7}' \
  http://localhost:8080/api/therapist/learners

curl --fail-with-body \
  -H "Authorization: Bearer ${TUTOR_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"plan_id":"plan-local-1","objective_ids":["count-to-20","number-sequence-within-20"],"adaptations":["short-instructions"],"limits":{"duration_minutes":10,"max_activities":4}}' \
  http://localhost:8080/api/therapist/learners/learner-local-1/plans

curl --fail-with-body \
  -H "Authorization: Bearer ${TUTOR_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://localhost:8080/api/therapist/learners/learner-local-1/sessions
```

The final response contains `tutoring_session_id`, `join_code`,
`join_expires_at`, `plan_id`, `plan_version`, and
`audio_consent_snapshot_id`. Copy the returned `tutoring_session_id` and
`join_code` into <http://localhost:8080/> while the join code is valid. This
minimal flow starts without audio evidence consent; do not add consent merely
to exercise the learner UI.

Stop the stack with `make down`. Use `make build` after image-affecting changes.
When dependencies change, edit `pyproject.toml`, then run `make lock && make
build`; never edit `uv.lock` by hand.

## Tests and offline evaluation

```bash
make test
make test ARGS="tests/unit -k curriculum"
make eval-math
docker compose --profile dev config
```

`make test` and `make eval-math` run in the network-independent tooling
container and require no provider credentials. The eval uses explicit fake
model outputs and deterministic scenarios to fail closed on mathematical,
state, stop-handling, and safety regressions. Its fixture ratings and fixture
review durations are not observations from a therapist.

Real-provider checks are opt-in and require credentials and network access:

```bash
make test-live-llm
```

No live-provider tests are currently implemented. The dated automated results
and their exact scope are recorded in
[docs/math-tutor-poc-verification.md](docs/math-tutor-poc-verification.md).

## Privacy and therapist review

The product stores structured observations and evidence-linked interpretations,
not an unrestricted full-session recording. Selective evidence clips are
default-off and require active consent scoped to the learner and session. Clips
must have a reason and evidence identifier, are duration-bounded, expire under
the retention policy, support explicit deletion, and become ineligible for new
capture immediately after consent is revoked.

The authenticated therapist surface lets a professional inspect progress,
assistance, evidence, hypotheses, change history, and retained clips. Its
implemented correction actions are to discard an evidence item and to correct
a skill estimate; both require a reason and preserve history. The professional
can also approve or reject a generated next-objective proposal. Reports keep
objective observations separate from versioned interpretations. Model
proposals do not become authoritative merely because they appear in a report.

## Current limitations and human gates

Automated tests and offline evals do not establish educational efficacy,
clinical safety, provider reliability, or readiness for children. Before any
learner trial, the project still requires:

- a qualified therapist's tabletop review of curriculum, hints, reports, and
  correction workflows;
- an adult-operated supervised voice session covering latency, interruption,
  low-confidence STT, stop behavior, and selective evidence capture;
- documented consent, safeguarding, escalation, retention, deletion, and
  access-control approval;
- specialist sign-off and accountable disposition of every blocking finding.

Until those gates are recorded as passed, use this repository only for further
engineering and adult-operated validation. Refer to the
[verification record](docs/math-tutor-poc-verification.md) rather than inferring
approval from a green automated build.
