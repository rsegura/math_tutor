# Conversation Regulation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add therapist-governed, harness-validated conversational regulation so the tutor can respond safely to confusion, frustration, refusal, distraction, and pauses without misclassifying them as mathematical answers or ending prematurely.

**Architecture:** The provider-neutral model proposes a closed regulation tool. The harness validates the current-turn signal and therapist policy, then application/domain code executes reviewed strategies and selectively persists structured events behind the existing mutation fence. Voice orchestration retains only bounded session state; summaries expose regulation evidence separately from competence.

**Tech Stack:** Python 3.12, dataclasses/enums, FastAPI, SQLite migrations, LiveKit Agents, pytest, Docker Compose.

---

### Task 1: Define regulation contracts and deterministic policy

**Files:**
- Create: `src/math_tutor/domain/regulation.py`
- Create: `tests/unit/math_tutor/domain/test_regulation.py`
- Modify: `src/math_tutor/harness/contracts.py`
- Test: `tests/unit/math_tutor/harness/test_loop.py`

**Steps:**
1. Write failing tests for the closed signal/strategy enums, confidence validation, signal-strategy compatibility, absence of a model-authored engagement/reset action, and rejection of extra/free-form model fields.
2. Run `make test ARGS="tests/unit/math_tutor/domain/test_regulation.py tests/unit/math_tutor/harness/test_loop.py -q"` and confirm failure.
3. Add immutable provider-neutral types `ConversationalSignal`, `PedagogicalStrategy`, `ConfidenceBand`, and `RegulationPolicy`; add `ToolName.REGULATE_CONVERSATION` and strict parsing of its exact arguments.
4. Keep the compatibility matrix in pure domain code. Do not add vendor imports or diagnosis-like fields.
5. Re-run the focused tests and commit `feat: define conversation regulation contracts`.

### Task 2: Add backward-compatible therapist policy provisioning

**Files:**
- Modify: `src/math_tutor/application/provisioning.py`
- Modify: `src/math_tutor/application/ports.py`
- Modify: `src/math_tutor/infrastructure/persistence/repositories.py`
- Create: `src/math_tutor/infrastructure/persistence/migrations/0020_regulation_policy.sql`
- Modify: therapist API request/response models under `web/`
- Test: `tests/unit/math_tutor/application/test_provisioning.py`
- Test: `tests/contract/math_tutor/test_therapist_api.py`
- Test: `tests/integration/math_tutor/test_provisioning_flow.py`
- Test: `tests/unit/math_tutor/infrastructure/test_migrator_atomicity.py`

**Steps:**
1. Write failing tests for optional API policy, invalid/duplicate strategies, policy totality across every signal (including empty/subset policies and exact-help coverage), bounded cap, versioned plan update, running-session snapshot semantics, and loading legacy rows with the default policy.
2. Run the focused tests in Docker and confirm the expected failures.
3. Add `regulation_policy` to `ProvisionedPlan`, commands, API DTOs, repository serialization, and session bootstrap. Migration `0020` follows existing `0019_learner_support_receipts.sql`, adds a non-null JSON column with an explicit versioned default, and repository decoding fails closed on malformed values.
4. Ensure omitted policy remains source-compatible for Python callers and wire-compatible for existing curl examples.
5. Re-run focused tests and commit `feat: provision therapist regulation policy`.

### Task 3: Validate and execute canonical regulation strategies

**Files:**
- Create: `src/math_tutor/application/regulation.py`
- Modify: `src/math_tutor/application/service.py`
- Modify: `src/math_tutor/application/ports.py`
- Modify: `src/math_tutor/harness/context.py`
- Modify: `src/math_tutor/harness/registry.py`
- Create: `tests/unit/math_tutor/application/test_regulation.py`
- Modify: `tests/unit/math_tutor/harness/test_context.py`
- Modify: `tests/unit/math_tutor/harness/test_registry.py`

**Steps:**
1. Write failing tests for current-turn binding, finite confidence range/banding (`0.50` and `0.80` boundaries included), policy authorization, compatibility, canonical speech selection, atomic ordered-hint composition, missing-content fallback, cap choice, and no competence mutation.
2. Run the focused tests and confirm failure.
3. Add a `CommitRegulation` command carrying command/session/profile versions, in-memory generation ID, and expected persisted regulation revision. Implement deterministic reviewed response selection. Extract a pure hint-selection/progress helper shared with `CommitHint`; compose all ordered-hint and regulation mutations into one batch and one fence transaction.
4. Make the repository/application result return canonical speech and updated persisted regulation revision. Reject stale turn generations before calling the service and stale persisted revisions/mutation failures before speech release.
5. Re-run focused tests and commit `feat: execute validated regulation strategies`.

### Task 4: Persist selective structured evidence atomically

**Files:**
- Modify: `src/math_tutor/application/regulation.py`
- Modify: `src/math_tutor/application/ports.py`
- Modify: `src/math_tutor/infrastructure/persistence/repositories.py`
- Create: `src/math_tutor/infrastructure/persistence/migrations/0021_regulation_events.sql`
- Create: `tests/integration/math_tutor/test_regulation_persistence.py`
- Modify: `tests/unit/math_tutor/infrastructure/test_migrator_atomicity.py`

**Steps:**
1. Write failing tests for atomic receipt/counter/event/hint mutation, replay idempotency, concurrent stale in-memory generation and persisted revision, initialization/resume, exact turn-1/turn-2/turn-3 pending promotion (deterministic IDs, monotonic per-activity ordinals, outcomes, pointer), ordinal continuity after answer reset and at the consecutive cap, new-activity sequence reset, high-priority immediate materialization, every closure path (`RecordAnswer`, regulation, support, stop, cap), crash recovery leaving `unknown`, and upgrade/replay of an existing migration-0019 support receipt.
2. Run the focused integration tests and confirm failure.
3. Add regulation state/events tables with closed-value constraints and indexes, backfilling zeroed state for existing sessions. In one transaction, validate session/profile versions and regulation revision, insert the command receipt, advance/reset the counter, retain/promote pending structured state, mutate hint progress where required, and close the previous open outcome when applicable. Refactor existing answer/support/stop mutation batches to close/reset the same state atomically. Preserve migration-0019 receipts as authoritative exact replay without retroactive counter changes.
4. Prove no transcript, rationale, model-generated speech, diagnosis, or child profile is stored or logged. Explicitly inspect `processed_commands`, support receipts, regulation tables, and structured logs; reviewed canonical receipt speech is allowed.
5. Re-run focused tests and commit `feat: persist selective regulation evidence`.

### Task 5: Extend provider schema and voice turn orchestration

**Files:**
- Modify: `src/math_tutor/agent/providers/model.py`
- Modify: `src/math_tutor/harness/prompts.py`
- Modify: `src/math_tutor/agent/runtime_factory.py`
- Modify: `src/math_tutor/agent/worker.py`
- Modify: `tests/unit/math_tutor/agent/test_model_providers.py`
- Modify: `tests/unit/math_tutor/agent/test_runtime_factory.py`
- Modify: `tests/integration/math_tutor/test_session_bootstrap_flow.py`

**Steps:**
1. Write failing tests for the exact provider tool schema, bounded context fields, stop precedence, low-STT precedence, deterministic-help precedence, accepted regulation speech, count reset after evaluated answers, and stale in-flight result suppression.
2. Run focused tests and confirm failure.
3. Advertise only plan-authorized strategies to the model, add provisional-state instructions, and pass bounded regulation state/revision. Wire canonical execution into the voice path; invalidate in-memory generations on stop/session close. No engagement/reset tool exists; evaluated mathematical answers perform the reset.
4. Preserve the existing two-provider abstraction and recovery/error taxonomy. Do not add provider-specific logic outside adapters.
5. Re-run focused tests and commit `feat: orchestrate conversational regulation`.

### Task 6: Expose regulation evidence in therapist review

**Files:**
- Modify: `src/math_tutor/application/summary.py`
- Modify: `src/math_tutor/application/review.py`
- Modify: `web/review_api.py`
- Modify: `web/static/tutoring-review.html`
- Modify: `web/static/tutoring-review.js`
- Modify: `tests/unit/math_tutor/application/test_summary.py`
- Modify: `tests/contract/math_tutor/test_review_api.py`
- Modify: `tests/integration/math_tutor/test_review.py`

**Steps:**
1. Write failing tests that regulation events are separate from mathematical claims, marked provisional, linked to event IDs, contain no transcript, and show strategy/outcome/ordinal.
2. Run focused tests and confirm failure.
3. Extend summary source/DTOs with a separate typed `RegulationSummaryItem` collection and the review UI with a concise “Conversation support” section. Regulation event IDs never enter mathematical `evidence_ids`; never turn regulation signals into profile or competency claims.
4. Re-run focused tests and commit `feat: report conversation support evidence`.

### Task 7: Add adversarial evaluations and operational documentation

**Files:**
- Add scenarios under `evals/math_tutor/scenarios/`
- Modify: `tests/integration/math_tutor/test_eval_runner.py`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `TESTING_REFERENCE.md`

**Steps:**
1. Add failing offline scenarios for paraphrases, repetition, frustration, refusal, off-task speech, pause, explicit stop, false-positive answers, disallowed strategy, cap behavior, concurrent stale proposals, replay, and privacy.
2. Run `make eval-math` and `make test ARGS="tests/integration/math_tutor/test_eval_runner.py -q"`; confirm new scenarios fail before fixtures/behavior are complete.
3. Add deterministic fixtures and document therapist policy configuration, behavior boundaries, review evidence, privacy, and relevant closed-code logs/metrics.
4. Run `make test`, `make eval-math`, and `docker compose --profile dev config --quiet`.
5. Confirm domain/application vendor-import guard, migration tests, provider contract tests, and all regression suites pass.
6. Commit `test: verify conversation regulation end to end`.

## Release gate

- No P1/P2 adversarial plan findings remain.
- Every production change was preceded by a failing test.
- Explicit stop never enters model recovery or persuasion.
- Repeated difficulty never creates an incorrect mathematical observation or ends solely because the regulation cap was reached.
- No unreviewed mathematical speech or free-form model speech is released.
- No full transcript or diagnosis-like regulation record is persisted.
- Legacy plans and existing API examples continue to work.
