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
- OpenAI or OpenRouter Responses API for the pedagogical model
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
- `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, and optional `LLM_BASE_URL`
- `TTS_PROVIDER`, `TTS_MODEL`, optional `TTS_VOICE_ID`, and `TTS_API_KEY`
- optionally, bounded `LLM_FIRST_RESPONSE_SECONDS`, `LLM_TOTAL_SECONDS`, and
  `LLM_MAX_OUTPUT_TOKENS` (default 256; accepted range 64–1024)

Supported combinations are Deepgram or OpenAI for STT, OpenAI or OpenRouter for
the LLM, and ElevenLabs or OpenAI for TTS. For OpenAI, set
`LLM_PROVIDER=openai`, use an OpenAI model such as
`gpt-4o-mini-2024-07-18`, and leave `LLM_BASE_URL` empty. For OpenRouter, use:

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=openai/gpt-4o-mini
LLM_API_KEY=your-openrouter-key
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MAX_OUTPUT_TOKENS=256
```

The selected OpenRouter model must support both the Responses API and tool
calling. `LLM_BASE_URL` is active but deliberately restricted: OpenAI must use
the SDK endpoint, and OpenRouter accepts only its canonical HTTPS endpoint.
Gemini is a possible future adapter and is **not implemented**.

### Conversation recovery

The live runtime keeps common requests for help outside the probabilistic model.
Short, self-contained Spanish turns such as `no lo entiendo`, `repítelo`, or
`necesito ayuda` are routed deterministically after stop, session-limit, and
low-confidence-STT checks. The application releases the next reviewed hint, or
repeats the canonical activity prompt when no hint remains. A help turn does
not record an answer, mark the learner wrong, or change a competence estimate.

Each help action has a durable receipt keyed by session and turn. Replaying the
same turn returns the same reviewed speech without consuming a second hint.
When a hint is used, its progress event and receipt commit atomically through
the same mutation fence as other tutoring actions. Learner help text is not
stored in the receipt.

LLM/provider failures are bounded per current voice generation. The first two
consecutive recoverable failures return a reviewed, non-terminal retry message
and repeat the current canonical prompt. A valid conversation reply or an
applied/replayed tool action resets the counter; deterministic help and
low-confidence turns preserve it, while cancelled or superseded generations do
not increment it. A third consecutive failure stops the session durably. Logs
use stable categories (`provider-timeout`, `provider-rate-limited`,
`provider-upstream-unavailable`, `provider-invalid-response`,
`harness-proposal-invalid`, or `harness-contract-exhausted`) and omit provider
exception text, response bodies, transcripts, and exception chains.

Shutdown accepts either a coroutine or the `Task` returned by LiveKit's
`delete_room()`. Existing tasks are awaited without transferring cancellation
ownership, preventing the teardown type error that motivated this change.
These controls are covered by automated tests; they have not yet passed the
supervised voice gate described below.

### Conversational regulation

For turns that are not an exact help phrase, the LLM may propose a provisional
current-turn signal (`confused`, `frustrated`, `task-rejecting`, `off-task`,
`requesting-help`, or `requesting-pause`) and one bounded strategy. The signal
is not a diagnosis or a learner-profile fact. The harness checks current-turn
provenance, confidence, signal/strategy compatibility, and the therapist's
snapshotted plan policy. Application code then selects reviewed Spanish speech
and performs any hint mutation atomically. An explicit stop remains a separate,
deterministic priority path and the model cannot end a session because a child
is frustrated or refuses one task.

Supported therapist strategies are `repeat-instruction`, `simplify-language`,
`give-ordered-hint`, `redirect-gently`, `validate-emotion`, and
`take-short-pause`. After the configured consecutive-turn cap, the runtime asks
whether to continue or pause; it does not mark an answer wrong or terminate the
call. An evaluable mathematical answer closes the pending regulation outcome
and resets the consecutive count. Low-confidence regulation proposals do not
mutate durable state.

The policy is plan data, not an environment variable. Omit `regulation` to use
the complete default policy with a cap of four, or include it when creating or
updating a plan:

```json
{
  "regulation": {
    "allowed_strategies": [
      "repeat-instruction",
      "simplify-language",
      "give-ordered-hint",
      "redirect-gently",
      "validate-emotion",
      "take-short-pause"
    ],
    "max_consecutive_regulation_turns": 4
  }
}
```

The policy must retain at least one compatible strategy for every supported
signal and at least one durable help fallback (`repeat-instruction` or
`simplify-language`); invalid or incomplete policies fail closed.

For ElevenLabs, `TTS_VOICE_ID` may be empty; the runtime then omits `voice_id`
and lets the pinned LiveKit plugin select its default. Successful construction
does not prove that this default is available under a particular ElevenLabs
account or entitlement. OpenAI TTS still requires an explicit voice ID.

`STORE_TRANSCRIPT` appears in `.env.example` but
is not currently passed into the Compose services and must not be treated as
an active runtime control.

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
  -d '{"plan_id":"plan-local-1","objective_ids":["count-to-20","number-sequence-within-20"],"adaptations":["short-instructions"],"limits":{"duration_minutes":10,"max_activities":4},"regulation":{"allowed_strategies":["repeat-instruction","simplify-language","give-ordered-hint","redirect-gently","validate-emotion","take-short-pause"],"max_consecutive_regulation_turns":4}}' \
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

The OpenRouter check is opt-in, makes one bounded Responses request with a real
user-defined tool, and requires dedicated environment variables:

```bash
export OPENROUTER_API_KEY=your-openrouter-key
export OPENROUTER_MODEL=openai/gpt-4o-mini
make test-live-llm
```

It reports `PASS`, `SKIP` (credentials/model absent), or `FAIL` distinctly.
The dated automated results and their exact scope are recorded in
[docs/math-tutor-poc-verification.md](docs/math-tutor-poc-verification.md).

## Privacy and therapist review

The product stores structured observations and evidence-linked interpretations,
not an unrestricted full-session recording. Important regulation evidence is
also closed and structured: signal, confidence band, executed strategy,
ordinal, and outcome. It contains no transcript, free-text rationale, diagnosis,
or model narrative. Lower-priority single events may remain only as bounded
pending state; repeated difficulty and high-priority frustration, rejection,
or pause evidence are materialized for review. Selective evidence clips are
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
