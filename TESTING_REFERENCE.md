# Testing Workflow

All tests run inside Docker. Never run pytest on the host.

```bash
make test                                  # whole suite
make test ARGS="tests/test_environment.py -x" # existing subset, stop at first failure
make test ARGS="-k 'rule_engine' -vv"      # by keyword, verbose
make test-live-llm                         # opt-in real-provider smokes (keys + network)
make eval-math                             # deterministic offline tutoring acceptance gate
docker compose --profile dev run --rm tooling uv run pytest <anything>   # canonical form
```

The pytest configuration registers `live_llm` and applies `-m "not live_llm"`
by default, including for the canonical Docker command. `make test-live-llm`
selects the implemented one-call OpenRouter Responses smoke. It passes a real
`give_hint` function contract through the production canonical parser and uses
a 64-token output budget. It reports exactly one terminal classification:
`PASS` when that test executed and passed, `SKIP` when its two dedicated
credentials are absent, or `FAIL` for test, collection, configuration, or
no-execution outcomes. No-tests exit status is not converted to success, and
no-run pytest options such as `--collect-only` are rejected.

The target deliberately reads `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`, not
the agent's generic `LLM_*` variables. Set them directly:

```bash
export OPENROUTER_API_KEY='your-openrouter-key'
export OPENROUTER_MODEL='openai/gpt-4o-mini' # must support Responses + tools
make test-live-llm
```

If `.env` has been explicitly configured for OpenRouter
(`LLM_PROVIDER=openrouter`, the canonical
`LLM_BASE_URL=https://openrouter.ai/api/v1`, a compatible model, and its key),
it can be sourced and mapped without printing the secret:

```bash
set -a
. ./.env
set +a
export OPENROUTER_API_KEY="$LLM_API_KEY"
export OPENROUTER_MODEL="$LLM_MODEL"
make test-live-llm
```

## TDD protocol (Red → Green → Refactor)

1. **Red** — write ONE failing test describing the next small behavior. Run it and show the failing output before writing any production code. If it doesn't fail, the test is wrong — fix the test first.
2. **Green** — write the minimum production code that makes that test pass. No extra features, no speculative generality. Run the test and show it passing.
3. **Refactor** — as a separate step, clean up while the suite stays green. Re-run the affected tests after refactoring.
4. Repeat. One behavior per cycle; commits should map to completed cycles.

Anti-patterns that will get a change rejected:

- Writing implementation and tests in the same step ("test-after").
- Kitchen-sink tests asserting many behaviors at once — the one exception is a phase Definition-of-Done gate in `tests/integration/`, which asserts a whole scripted conversation's outcome deliberately and whose every clause is also covered granularly by a unit test.
- Mocking pure functions or domain objects.
- Hardcoding a return value to pass and never adding the follow-up test that forces the real implementation.
- Asserting exact assistant wording — assert behavior (which field was asked about, which tool was called, what was rejected), not text.

## Test taxonomy

The locations below are the target layout and are created with their delivery
phase. `tests/unit/` covers domain, application, harness, infrastructure,
dependency, and import-boundary guards. `tests/contract/` pins LiveKit SDK
surfaces, worker composition, Compose topology, token endpoints, and static
pages. `tests/integration/` drives the pedagogical harness through a scripted
`FakeModelAdapter`, while `tests/e2e/` is reserved for controlled fake
STT/LLM/TTS flows. Persistence tests build every temporary database under
`tmp_path`; repository guardrails remain at the tests root.

| Layer | Target location | Phase / rules |
|---|---|---|
| Unit | `tests/unit/` | Phases 1–2 onward. No network, no LLM, no LiveKit. Covers rules engine, protocol loader, state machine, field updates, tool authorization, projections. |
| Integration | `tests/integration/` | Phases 2–6. Harness driven by `FakeModelAdapter` (scripted responses). Covers tool pipeline, repair loop, stale-generation rejection, idempotency. |
| Contract | `tests/contract/` | Phase 0 onward. Pinned-version expectations against LiveKit SDK surfaces (e.g. dispatch metadata serialization, `llm_node` signature). |
| E2E | `tests/e2e/` | Phase 7. Controlled fake STT/LLM/TTS tests for interruption, cancellation, transcript, and lifecycle boundaries. |
| Live-LLM (optional) | marker `live_llm` | Real provider calls; excluded by default and run explicitly with `make test-live-llm`. Requires keys in `.env` and network access. |
| Evals | `evals/` | Phase 7. Offline scenario runner with behavioral assertions, never exact text. |

## Conversation-recovery coverage

The automated suite exercises the recovery path without provider network or
live audio. Unit and integration tests verify:

- narrow, whole-utterance Spanish help matching and false-positive rejection;
- stop, terminal session limits, and low-confidence STT taking precedence over
  help, with help taking precedence over an LLM call;
- no answer observation or competence mutation for a help turn;
- ordered reviewed hints, canonical-prompt fallback after hint exhaustion, and
  atomic durable support receipts;
- idempotent and concurrent replay of the same turn without consuming another
  hint or emitting another progress event;
- two non-terminal current-generation LLM recoveries, a durable terminal stop
  on the third consecutive failure, reset after valid model/tool results, and
  neutral cancellation or stale generations;
- allowlisted error categories whose logs contain no provider exception body,
  transcript, secret, or exception chain; and
- shutdown with both helper-owned coroutines and externally owned LiveKit
  `Task`/`Future` objects, including bounded timeout and cancellation behavior.

These tests establish deterministic control-flow contracts. They do not
measure conversational quality, provider reliability, speech latency, or
behavior during a supervised live voice session.

## Conversational-regulation contract

The model may classify only the current turn into the closed signal vocabulary
and propose one compatible strategy. The harness requires the exact current
turn id, a finite confidence in `[0, 1]`, a compatible signal/strategy pair,
and authorization by the plan snapshot. It repairs a disallowed proposal once
within the normal model-call budget and otherwise fails closed. The model cannot
propose `engaged`, reset counters, persist narrative, decide mathematical
correctness, or end a session because of confusion, frustration, refusal,
off-task speech, help, or a pause request. Explicit stop, terminal/session caps,
and low-confidence STT retain their deterministic precedence.

Therapist plan requests use this shape (the field is optional and omission uses
the complete default policy):

```json
{
  "regulation": {
    "allowed_strategies": [
      "repeat-instruction", "simplify-language", "give-ordered-hint",
      "redirect-gently", "validate-emotion", "take-short-pause"
    ],
    "max_consecutive_regulation_turns": 4
  }
}
```

The cap is 1–12. A policy is rejected if it has duplicate/unknown strategies,
leaves any signal without a compatible response, or removes both repeat and
simplify fallbacks. Strategy execution returns only canonical reviewed speech.
The cap action asks the learner to choose continuation or a short pause; it is
not a stop and does not consume a mathematical attempt.

Durable review evidence uses closed fields only: signal, confidence band,
executed strategy, activity-local ordinal, and outcome (`unknown`,
`repeated_difficulty`, `answered`, or `stopped`). It never stores the child turn,
LLM rationale, or arbitrary text. A single lower-priority signal is retained as
bounded pending state; high-priority frustration, task rejection, and pause
requests are materialized immediately, while repeated difficulty materializes
the prior event. Replay is idempotent, and stale generation/revision proposals
cannot mutate or release stale speech. The harness emits only the structured
events `conversation_regulation_proposed`, `conversation_regulation_applied`,
and `conversation_regulation_rejected`. Proposal fields are `signal`,
`requested_strategy`, `confidence_band`, and `consecutive_count`. Applied
LLM-applied events add `executed_action`, `outcome`, and
`regulation_revision`; rejected events add `rejection_code`. Rejections before
a valid closed contract contain only `rejection_code`; later rejections retain
the already validated proposal fields. Exact deterministic help emits an
applied event without a proposal and contains `signal=requesting-help`,
`requested_strategy=null`, `executed_action`, and `outcome`; it deliberately
does not perform an extra repository read for count or revision. Values are
closed enums/codes; logs contain no arguments, transcripts, provider bodies,
profile data, or exception chains.

## Offline tutoring acceptance gate

`make eval-math` runs the versioned YAML scenarios in
`evals/math_tutor/scenarios/` with a deterministic fake at the production model
port. It does not create provider clients, load credentials, or use network
services. Each scenario is provisioned through the real learner/plan/session
service and its turns cross `BoundedConversationEngine`, `PedagogicalHarness`,
`TutoringService`, and `SQLiteTutoringRepository`. The gate then grades the
durable aggregate and released voice decisions. Expected fixture values are
used only after execution as assertions; they never populate observed state.

The catalog covers correct answers, conceptual errors, self-correction, low STT
confidence, ambiguous language, hint exhaustion, explicit stop, out-of-scope
objective proposals, and replayed evidence. Regulation scenarios add
paraphrased confusion and repetition, frustration, refusal, off-task speech,
pause, an emotional-but-evaluable answer false positive, a disallowed strategy
repair, the consecutive-turn cap, privacy-safe replay, an executable stale-turn
race, and runtime reconstruction after a simulated crash. The latter two use
explicit `concurrent-stale` and `crash-reopen` execution modes while retaining
deterministic fixtures. Concurrent mode requires exactly two turns, bounds both
coordination waits, cancels and gathers the old task in `finally`, and reports
`scenario-id.concurrent-stale-timeout` instead of hanging the gate. Its schema
rejects unknown and missing fields so fixtures cannot silently drift.

Schema version 4 separates explicit model output and tool arguments from the
expected outcome. The fake adapter only replays that output (substituting the
turn id); it never reads expected answers, outcome labels, or repository answer
state. The schema declares the complete expected durable outcome per scenario:
observation outcomes and order, evidence and proposal counts, attempts, hints,
streaks, bounded repair calls, released decisions, intervention classification,
terminal state, and materialized regulation event signals/strategies. Every
mismatch is a named hard failure of the form
`scenario-id.field`, so `make eval-math` is independently useful as a CI gate
without relying on pytest assertions.

Each regulation turn declares independent, version-controlled
`reviewed_speech` fixture text. The gate compares released speech with that
fixture; it does not call the production renderer to build its oracle. A
`reason=regulated` label alone is not trusted. A test replaces the production
renderer and proves the unchanged fixture catches its free-form mathematical
speech; the fault adapter separately tests the same hard metric.

Privacy-marker scenarios inspect actual SQLite rows in `regulation_state`,
`regulation_events`, `processed_commands`, and `learner_support_receipts`, plus
structured records captured from the `math_tutor` logger tree. Finding the
marker in any artifact is a hard `diagnostic_or_privacy_violations` failure.
Generic telephone-pattern matching is not applied to opaque database hashes,
which can coincidentally resemble numbers; the explicit marker is the exact
persistence/log leak oracle.

`intervention_classifications` reports what the executed behavior did (for
example, `scope-rejected` or `self-correction-recorded`); it is not presented as
a quality rating. Each scenario separately declares an
`intervention_rating_fixture` using `adequate`, `correctable`, or `inadequate`.
These labels are explicit stand-ins for therapist ratings, not measurements
derived from execution. The gate reports the fixtures unchanged as
`intervention_rating_fixtures`, calculates
`adequate_or_correctable_proportion`, and fails with
`intervention_adequacy_below_target` below the design's experimental 80%
acceptance threshold. Real therapist ratings must replace these fixtures for a
supervised validation study.

`model_delay_ms` advances an injected monotonic clock while the real engine call
is awaited. Per-scenario `expected.latency_ms` is a hard maximum; the measured
p95 remains a soft comparison metric. `review_fixture_duration_seconds` is
explicitly fixture metadata, not measured therapist productivity.

The default CLI database lives in a managed temporary directory. An explicit
existing path is never removed unless `--overwrite-eval-db` is supplied and the
path is recognizably an eval artifact. This prevents the gate from deleting an
unrelated SQLite database.

The following are hard safety metrics and make the command exit non-zero when
their count is non-zero:

- `mathematical_speech_errors`
- `unsupported_profile_updates`
- `stt_misattributions`
- `ignored_stops`
- `diagnostic_or_privacy_violations`

Evidence coverage, deterministic p95 latency, and summed review-time fixtures
are reported as comparison metrics. The per-scenario latency budgets and the
80% fixture intervention-rating target are explicit acceptance gates.

Mathematical speech is checked against canonical activity prompts, reviewed
hints and deterministic feedback, with explicit equation consistency checks.
The privacy gate scans system/model-generated speech, non-ingress bounded
context, and persisted narrative for diagnostic labels and PII patterns such as
email, telephone, date of birth, address, or legal-name fields. Raw child input
is not itself counted as a generated privacy violation; scenarios additionally
verify it is neither persisted nor re-emitted where no educational observation
is created.

Mock policy: fake only what crosses the process boundary — model providers (`FakeModelAdapter`), STT/TTS plugins, clock where needed. Everything inside `src/math_tutor/domain/` runs real in every test.

Async tests: `pytest-asyncio` is configured with `asyncio_mode = "auto"` — write `async def test_...` directly, no decorator needed.
