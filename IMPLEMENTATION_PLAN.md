# Primary Mathematics Voice Tutor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a bounded-autonomy mathematics tutoring vertical for units, tens, and simple addition/subtraction while preserving the existing medical screening product.

**Architecture:** Introduce tutoring as a separate bounded context under `src/tutoring/`; do not generalise or destabilise the screening domain prematurely. Reuse provider-neutral model contracts, voice transport primitives, persistence infrastructure patterns, and review concepts, while giving tutoring its own domain, harness, schema, prompts, evals, and composition root. The LLM proposes pedagogical actions; deterministic code verifies mathematics and progression; therapist review consolidates material profile changes.

**Tech Stack:** Python 3.12, LiveKit Agents, FastAPI, SQLite, YAML, pytest/pytest-asyncio, Docker Compose, existing OpenAI-compatible adapters.

---

## Delivery constraints

- Use a dedicated Git worktree before implementation.
- Run all Python and pytest commands through Docker, per `AGENTS.md`.
- Follow Red → Green → Refactor for every behaviour.
- Keep `HARNESS_MODE=custom` screening behaviour unchanged.
- Add `PRODUCT_MODE=screening|math_tutor`; default to `screening` until the
  tutoring live gate closes.
- Do not store full-session audio.
- Do not claim clinical efficacy or emit diagnostic statements.

### Task 1: Freeze tutoring boundaries and import direction

**Files:**
- Create: `src/tutoring/__init__.py`
- Create: `src/tutoring/domain/__init__.py`
- Create: `src/tutoring/application/__init__.py`
- Create: `src/tutoring/harness/__init__.py`
- Create: `src/tutoring/infrastructure/__init__.py`
- Modify: `tests/unit/test_import_boundaries.py`

**Step 1: Write the failing test**

Add a boundary test that parses imports under `src/tutoring/domain` and
`src/tutoring/application` and rejects LiveKit, FastAPI, OpenAI, SQLite, and
the existing screening domain. Assert that tutoring domain imports only stdlib
or `tutoring.domain`, and tutoring application imports only tutoring domain and
declared ports.

**Step 2: Verify RED**

Run:

```bash
make test ARGS="tests/unit/test_import_boundaries.py -k tutoring -v"
```

Expected: FAIL because the tutoring package does not exist.

**Step 3: Add the package skeleton**

Create the four layers with module docstrings documenting the allowed inward
dependencies. Do not move existing screening code.

**Step 4: Verify GREEN**

Run the same command; expected: PASS.

**Step 5: Commit**

```bash
git add src/tutoring tests/unit/test_import_boundaries.py
git commit -m "chore: establish tutoring bounded context"
```

### Task 2: Model curriculum objectives and prerequisite graph

**Files:**
- Create: `src/tutoring/domain/curriculum.py`
- Create: `tests/unit/tutoring/domain/test_curriculum.py`

**Step 1: Write failing tests**

Cover one behaviour per test:

```python
def test_catalog_rejects_unknown_prerequisite():
    with pytest.raises(InvalidCurriculum):
        CurriculumCatalog((objective("place-value", requires=("missing",)),))


def test_catalog_rejects_cycles():
    with pytest.raises(InvalidCurriculum):
        CurriculumCatalog((
            objective("units-tens", requires=("compose-2d",)),
            objective("compose-2d", requires=("units-tens",)),
        ))
```

Also test unique IDs, supported bands, non-empty activity families, ordered
hints, and deterministic topological ordering.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/tutoring/domain/test_curriculum.py -v"
```

Expected: import failure.

**Step 3: Implement the minimum model**

Add immutable `CurriculumBand`, `HintDefinition`, `LearningObjective`, and
`CurriculumCatalog`. The catalog validates references and cycles in its
constructor and exposes `objective(id)` and `prerequisites_met(id, states)`.

**Step 4: Verify GREEN and refactor**

Run the task tests, then:

```bash
make test ARGS="tests/unit/tutoring/domain tests/unit/test_import_boundaries.py"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/tutoring/domain tests/unit/tutoring
git commit -m "feat: model tutoring curriculum graph"
```

### Task 3: Load the first reviewed curriculum slice from YAML

**Files:**
- Create: `src/tutoring/infrastructure/curriculum_loader.py`
- Create: `src/tutoring/curricula/primary-math-v1.yaml`
- Create: `tests/unit/tutoring/infrastructure/test_curriculum_loader.py`

**Step 1: Write failing tests**

Test strict unknown-key rejection, exact version loading, duplicate objectives,
missing Spanish wording, invalid prerequisite references, and a successful
load containing `units-tens`, `compose-two-digit`, `add-within-20`, and
`subtract-within-20`.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/tutoring/infrastructure/test_curriculum_loader.py -v"
```

**Step 3: Implement loader and minimal content**

Use `yaml.safe_load`; validate exact keys before constructing domain objects.
The YAML must contain objective metadata and reviewed hints, not generated
student data. Start with 8–12 objectives that form the prerequisite chain for
the first vertical slice.

**Step 4: Verify GREEN**

Run the test file; expected: PASS.

**Step 5: Commit**

```bash
git add src/tutoring/curricula src/tutoring/infrastructure tests/unit/tutoring/infrastructure
git commit -m "feat: load primary math curriculum slice"
```

### Task 4: Add deterministic activities and answer verification

**Files:**
- Create: `src/tutoring/domain/activities.py`
- Create: `src/tutoring/domain/mathematics.py`
- Create: `tests/unit/tutoring/domain/test_activities.py`
- Create: `tests/unit/tutoring/domain/test_mathematics.py`

**Step 1: Write failing tests**

Cover deterministic generation from `(template_id, seed, difficulty)`, exact
place-value checking, addition/subtraction bounds, rejection of negative
subtraction when the template forbids it, and separation of `CORRECT`,
`INCORRECT`, `AMBIGUOUS`, and `NOT_EVALUABLE`.

Example:

```python
def test_place_value_answer_is_checked_without_the_llm():
    activity = place_value_activity(number=34)
    result = verify_answer(activity, StructuredAnswer(tens=3, units=4))
    assert result.outcome is AnswerOutcome.CORRECT
```

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/tutoring/domain/test_activities.py tests/unit/tutoring/domain/test_mathematics.py -v"
```

**Step 3: Implement minimum deterministic engine**

Create immutable `Activity`, `ActivityTemplate`, `StructuredAnswer`, and
`AnswerCheck`. Keep natural-language interpretation outside this module.
Every generated activity carries the expected structured answer and objective
ID.

**Step 4: Verify GREEN**

Run the two test files; expected: PASS.

**Step 5: Commit**

```bash
git add src/tutoring/domain tests/unit/tutoring/domain
git commit -m "feat: verify primary math activities deterministically"
```

### Task 5: Model learning plans, observations, and provisional competency

**Files:**
- Create: `src/tutoring/domain/learning.py`
- Create: `src/tutoring/domain/evidence.py`
- Create: `tests/unit/tutoring/domain/test_learning.py`
- Create: `tests/unit/tutoring/domain/test_evidence.py`

**Step 1: Write failing tests**

Test that:

- only therapist-authorised objectives can become active;
- age changes language/presentation metadata but not competency;
- one success cannot advance a competency;
- low-confidence STT yields `NOT_EVALUABLE` and cannot lower competency;
- repeated evidence across varied activities may propose one-step advancement;
- stop requests terminate immediately;
- objective facts are immutable while interpretations are versioned.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/tutoring/domain/test_learning.py tests/unit/tutoring/domain/test_evidence.py -v"
```

**Step 3: Implement the domain state machine**

Add `CompetencyState`, `LearningPlan`, `LearningSession`, `Observation`,
`EvidenceRecord`, `SkillEstimate`, and `ProposedProfileChange`. Put progression
thresholds in an injected `ProgressionPolicy`; do not hardcode therapist-facing
labels in prompts.

**Step 4: Verify GREEN**

Run all tutoring domain tests; expected: PASS.

**Step 5: Commit**

```bash
git add src/tutoring/domain tests/unit/tutoring/domain
git commit -m "feat: add evidence-based learning progression"
```

### Task 6: Define the tutoring application ports and mutation fence

**Files:**
- Create: `src/tutoring/application/ports.py`
- Create: `src/tutoring/application/results.py`
- Create: `src/tutoring/application/tutoring_service.py`
- Create: `tests/unit/tutoring/application/test_tutoring_service.py`
- Create: `tests/unit/tutoring/application/test_tutoring_fence.py`

**Step 1: Write failing tests**

Adapt the existing fence guarantees to tutoring: active generation, durable
idempotency, expected profile/session version, authorised objective, atomic
observation plus evidence plus event, and fail-closed persistence.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/tutoring/application -v"
```

**Step 3: Implement service and ports**

Declare repository protocols inward. Implement commands for recording answers,
committing hints, selecting the next activity, proposing evidence, proposing a
profile change, and ending a session. Reuse `application.session_runtime` for
generation cancellation, but keep tutoring mutations separate from
`ScreeningService`.

**Step 4: Verify GREEN**

Run tutoring application and existing fence tests; expected: PASS.

**Step 5: Commit**

```bash
git add src/tutoring/application tests/unit/tutoring/application
git commit -m "feat: add tutoring mutation fence"
```

### Task 7: Implement the bounded pedagogical harness

**Files:**
- Create: `src/tutoring/harness/contracts.py`
- Create: `src/tutoring/harness/context.py`
- Create: `src/tutoring/harness/registry.py`
- Create: `src/tutoring/harness/loop.py`
- Create: `src/tutoring/harness/prompts.py`
- Create: `tests/unit/tutoring/harness/test_registry.py`
- Create: `tests/unit/tutoring/harness/test_context.py`
- Create: `tests/unit/tutoring/harness/test_loop.py`

**Step 1: Write failing tests**

Test the smallest tool surface and its policies:

- `record_answer` requires current-turn evidence and structured answer;
- `give_hint` must use the next reviewed hint and respect the hint cap;
- `adapt_difficulty` moves by at most one step inside active objectives;
- `propose_skill_update` cannot directly consolidate the profile;
- `end_session` respects stop requests immediately;
- mathematical speech is released only after deterministic verification;
- invalid output gets one bounded repair; and
- model/tool steps remain subject to existing hard budgets.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/tutoring/harness -v"
```

**Step 3: Implement contracts, registry, context, and loop**

Reuse provider-neutral `llm_harness.contracts.ModelAdapter` and
`llm_harness.runtime.limits.HarnessLimits`. Build context from child-safe static
policy, authorised plan, current activity, structured learner state, bounded
recent history, and current turn. Never include diagnostic labels or unrelated
clinical history.

**Step 4: Verify GREEN**

Run tutoring harness tests and the existing harness suite; expected: PASS.

**Step 5: Commit**

```bash
git add src/tutoring/harness tests/unit/tutoring/harness
git commit -m "feat: add bounded pedagogical harness"
```

### Task 8: Persist tutoring state and selective evidence

**Files:**
- Create: `src/infrastructure/persistence/migrations/0003_tutoring.sql`
- Modify: `src/infrastructure/persistence/migrator.py`
- Create: `src/tutoring/infrastructure/persistence.py`
- Create: `tests/unit/tutoring/infrastructure/test_persistence.py`
- Create: `tests/integration/test_tutoring_reconstruction.py`

**Step 1: Write failing tests**

Test atomic persistence and reconstruction for learners, plans, sessions,
activities, observations, evidence, provisional estimates, therapist reviews,
and profile revisions. Assert that no column stores full-session audio and that
each clip references one evidence record with bounded duration metadata.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/tutoring/infrastructure/test_persistence.py tests/integration/test_tutoring_reconstruction.py -v"
```

**Step 3: Add migration and repositories**

Increment `SCHEMA_VERSION` to `3`. Use append-only revisions for interpretations
and reviews. Store objective observations separately from revisable estimates.
Persist curriculum snapshots and policy versions for reproducibility.

**Step 4: Verify GREEN**

Run the two tests plus existing migration and reconstruction tests; expected:
PASS.

**Step 5: Commit**

```bash
git add src/infrastructure/persistence src/tutoring/infrastructure tests
git commit -m "feat: persist tutoring sessions and evidence"
```

### Task 9: Produce evidence-linked summaries and therapist corrections

**Files:**
- Create: `src/tutoring/application/summary.py`
- Create: `src/tutoring/application/review.py`
- Create: `tests/unit/tutoring/application/test_summary.py`
- Create: `tests/integration/test_tutoring_review.py`

**Step 1: Write failing tests**

Assert that every material summary claim contains evidence IDs, unsupported
interpretations are labelled hypotheses, discarded evidence disappears from
the current view without deleting history, therapist corrections preserve the
original proposal, and dependent estimates are recalculated.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/tutoring/application/test_summary.py tests/integration/test_tutoring_review.py -v"
```

**Step 3: Implement summary and correction services**

Generate the authoritative summary from structured rows. If an LLM produces a
readable narrative, validate its claim IDs against that authoritative summary
before display.

**Step 4: Verify GREEN**

Run the task tests; expected: PASS.

**Step 5: Commit**

```bash
git add src/tutoring/application tests
git commit -m "feat: add evidence-linked tutoring review"
```

### Task 10: Add tutoring composition and voice routing

**Files:**
- Create: `src/agent/tutoring_agent.py`
- Create: `src/agent/tutoring_runtime_factory.py`
- Modify: `src/agent/worker.py`
- Modify: `src/infrastructure/dispatch.py`
- Modify: `web/app.py`
- Create: `tests/unit/agent/test_tutoring_agent.py`
- Modify: `tests/contract/test_worker_contract.py`
- Modify: `tests/contract/test_token_endpoint.py`
- Create: `tests/integration/test_tutoring_voice_boundary.py`

**Step 1: Write failing tests**

Test strict `PRODUCT_MODE`, product-tagged dispatch metadata, no silent fallback,
turn correlation, interruption cancellation, low-confidence confirmation, stop
priority, and unchanged screening defaults.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/agent/test_tutoring_agent.py tests/contract/test_worker_contract.py tests/contract/test_token_endpoint.py tests/integration/test_tutoring_voice_boundary.py -v"
```

**Step 3: Implement composition**

Reuse the current STT correlation, TTS watchdog, terminal closer, provider
factories, and active-generation runtime through small extracted helpers where
tests prove identical semantics. Do not make `ScreeningAgent` branch internally
on product behaviour.

**Step 4: Verify GREEN**

Run the task tests and all existing agent/contract tests; expected: PASS.

**Step 5: Commit**

```bash
git add src/agent src/infrastructure/dispatch.py web/app.py tests
git commit -m "feat: route voice sessions to tutoring runtime"
```

### Task 11: Capture short evidence clips without retaining full audio

**Files:**
- Create: `src/tutoring/infrastructure/evidence_clips.py`
- Modify: `src/agent/tutoring_agent.py`
- Create: `tests/unit/tutoring/infrastructure/test_evidence_clips.py`
- Create: `tests/integration/test_selective_audio_retention.py`

**Step 1: Write failing tests**

Use fake audio frames and a fake clock to prove that the rolling in-memory
buffer is bounded, non-selected frames are discarded, selected clips contain
only the configured context window, clip duration cannot exceed the cap, and
session close removes pending buffers.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/tutoring/infrastructure/test_evidence_clips.py tests/integration/test_selective_audio_retention.py -v"
```

**Step 3: Implement clip capture**

Maintain a short per-session ring buffer in memory. Persist encoded audio only
after the harness commits an evidence-selection decision. Store files under a
configured tutoring evidence directory with opaque IDs; store no transcript in
filenames or logs.

**Step 4: Verify GREEN**

Run task tests; expected: PASS.

**Step 5: Commit**

```bash
git add src/agent/tutoring_agent.py src/tutoring/infrastructure tests
git commit -m "feat: retain only selected tutoring audio evidence"
```

### Task 12: Add therapist review UI and API

**Files:**
- Create: `web/tutoring_review.py`
- Create: `web/static/tutoring-review.html`
- Create: `web/static/tutoring-review.js`
- Modify: `web/app.py`
- Create: `tests/contract/test_tutoring_review_api.py`
- Create: `tests/contract/test_tutoring_review_page.py`

**Step 1: Write failing contract tests**

Cover learner/session list, evidence-linked detail, authorised clip access,
structured correction commands, optimistic concurrency, immutable history,
no-store headers, and absence of diagnostic labels.

**Step 2: Verify RED**

```bash
make test ARGS="tests/contract/test_tutoring_review_api.py tests/contract/test_tutoring_review_page.py -v"
```

**Step 3: Implement read and correction surfaces**

Keep reads on query-only connections. Send corrections through a narrow writer
service with expected revision. Render objective progress, assistance, evidence,
provisional hypotheses, and next-objective proposals separately.

**Step 4: Verify GREEN**

Run task tests and existing review tests; expected: PASS.

**Step 5: Commit**

```bash
git add web tests/contract
git commit -m "feat: add therapist tutoring review"
```

### Task 13: Build tutoring evals and the acceptance gate

**Files:**
- Create: `evals/tutoring/runner.py`
- Create: `evals/tutoring/metrics.py`
- Create: `evals/tutoring/scenarios/`
- Create: `tests/integration/test_tutoring_eval_runner.py`
- Modify: `Makefile`
- Modify: `docs/agents/TESTING.md`

**Step 1: Write failing tests**

Create scenarios for correct answers, conceptual errors, self-correction, low
STT confidence, ambiguous language, hint exhaustion, stop requests, frustration,
out-of-scope objective proposals, and repeated evidence. Grade database state,
never exact model wording.

**Step 2: Verify RED**

```bash
make test ARGS="tests/integration/test_tutoring_eval_runner.py -v"
```

**Step 3: Implement runner and gate**

Add `make eval-tutoring`. Report mathematical speech errors, unsupported
profile updates, STT misattributions, ignored stop requests, intervention
ratings, evidence coverage, latency, and review-time fixtures. Exit non-zero if
any hard safety invariant fails.

**Step 4: Verify GREEN**

```bash
make test ARGS="tests/integration/test_tutoring_eval_runner.py -v"
make eval-tutoring
```

Expected: tests PASS; deterministic fake-model eval gate exits `0`.

**Step 5: Commit**

```bash
git add evals tests/integration Makefile docs/agents/TESTING.md
git commit -m "test: gate tutoring behaviour with offline evals"
```

### Task 14: Close the PoC verification gate

**Files:**
- Create: `docs/tutoring-poc-verification.md`
- Modify: `AGENTS.md`
- Modify: `docs/agents/DOCKER.md`

**Step 1: Run automated verification**

```bash
make test
make eval
make eval-tutoring
docker compose --profile dev config
```

Expected: full tests PASS, screening baseline does not regress, tutoring fake
eval gate PASS, Compose configuration valid.

**Step 2: Run professional tabletop verification**

Have a therapist review the curriculum slice, activity templates, ordered
hints, stop behaviour, summary claims, and correction workflow. Record every
finding and whether it blocks a learner trial.

**Step 3: Run a supervised voice gate**

Verify a 10–15 minute session, natural interruption, low-confidence repeat,
selected evidence clips only, immediate stop, and a review completed in under
five minutes. Do not involve children until consent, safeguarding, retention,
and specialist-review requirements are separately approved.

**Step 4: Document evidence honestly**

Write exact commands, outputs, operator attestations, open exceptions, and
whether each acceptance criterion passed. Do not mark unrun gates as passing.

**Step 5: Commit**

```bash
git add AGENTS.md docs/agents/DOCKER.md docs/tutoring-poc-verification.md
git commit -m "docs: close tutoring poc verification gate"
```

## Final review checklist

- Existing screening tests and evals are unchanged or intentionally updated.
- Mathematical output is deterministically verified before speech.
- Low-confidence STT never degrades learner state.
- Stop and pause requests pre-empt pedagogy.
- Material profile updates are evidence-linked and reviewable.
- Objective observations are immutable; interpretations are versioned.
- No full-session audio is persisted.
- Every retained clip has a reason, evidence ID, and bounded duration.
- The therapist can correct the agent and reconstruct the prior state.
- The tutoring acceptance gate fails closed on safety regressions.
