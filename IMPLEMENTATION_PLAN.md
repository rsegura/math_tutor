# Primary Mathematics Voice Tutor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a standalone bounded-autonomy mathematics voice tutor for
units, tens, and simple addition/subtraction.

**Architecture:** Implement the product under the canonical namespace
`src/math_tutor/`, with its own domain, application layer, pedagogical harness,
infrastructure, voice composition, schema, prompts, evals, and web boundary.
The LLM proposes pedagogical actions; deterministic code verifies mathematics
and progression; therapist review consolidates material profile changes.

**Tech Stack:** Python 3.12, LiveKit Agents, FastAPI, SQLite, YAML,
pytest/pytest-asyncio, Docker Compose, and OpenAI-compatible adapters.

---

## Delivery constraints

- Work only in the standalone `poc_math` repository and its feature branch.
- Run all Python and pytest commands through Docker, per `AGENTS.md`.
- Follow Red → Green → Refactor for every behaviour.
- Do not store full-session audio.
- Do not emit diagnoses, medical labels, or unsupported efficacy claims.
- Code from `audio_poc` is reference material only. Copy or adapt a reusable
  provider, transport, persistence, or review pattern only in the task that
  requires it, place it under this repository's namespace, remove all medical
  semantics, and cover the adapted behaviour with a failing test first.

### Task 1: Freeze math tutor boundaries and import direction

**Files:**
- Modify: `src/math_tutor/__init__.py`
- Create: `src/math_tutor/domain/__init__.py`
- Create: `src/math_tutor/application/__init__.py`
- Create: `src/math_tutor/harness/__init__.py`
- Create: `src/math_tutor/infrastructure/__init__.py`
- Create: `tests/unit/math_tutor/test_import_boundaries.py`

**Step 1: Write the failing test**

Add a boundary test that parses imports under `src/math_tutor/domain` and
`src/math_tutor/application` and rejects LiveKit, FastAPI, OpenAI, SQLite, and
all infrastructure packages. Assert that the domain imports only the standard
library or `math_tutor.domain`, and the application layer imports only the
math-tutor domain and declared ports.

**Step 2: Verify RED**

Run:

```bash
make test ARGS="tests/unit/math_tutor/test_import_boundaries.py -v"
```

Expected: FAIL because the boundary test and layered packages do not exist.

**Step 3: Add the package skeleton**

Create the four layers with module docstrings documenting the allowed inward
dependencies. Keep `src/` as a source root, not a Python package.

**Step 4: Verify GREEN**

Run the same command; expected: PASS.

**Step 5: Commit**

```bash
git add src/math_tutor tests/unit/math_tutor/test_import_boundaries.py
git commit -m "chore: establish math tutor boundaries"
```

### Task 2: Model curriculum objectives and prerequisite graph

**Files:**
- Create: `src/math_tutor/domain/curriculum.py`
- Create: `tests/unit/math_tutor/domain/test_curriculum.py`

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
make test ARGS="tests/unit/math_tutor/domain/test_curriculum.py -v"
```

Expected: import failure.

**Step 3: Implement the minimum model**

Add immutable `CurriculumBand`, `HintDefinition`, `LearningObjective`, and
`CurriculumCatalog`. The catalog validates references and cycles in its
constructor and exposes `objective(id)` and `prerequisites_met(id, states)`.

**Step 4: Verify GREEN and refactor**

Run the task tests, then:

```bash
make test ARGS="tests/unit/math_tutor/domain tests/unit/math_tutor/test_import_boundaries.py"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/math_tutor/domain tests/unit/math_tutor
git commit -m "feat: model tutoring curriculum graph"
```

### Task 3: Load the first reviewed curriculum slice from YAML

**Files:**
- Create: `src/math_tutor/domain/templates.py`
- Create: `src/math_tutor/infrastructure/curriculum_loader.py`
- Create: `src/math_tutor/curricula/primary-math-v1.yaml`
- Create: `src/math_tutor/curricula/activity-templates-v1.yaml`
- Create: `tests/unit/math_tutor/infrastructure/test_curriculum_loader.py`
- Create: `tests/unit/math_tutor/infrastructure/test_template_catalog.py`

**Step 1: Write failing tests**

Test strict unknown-key rejection, exact version loading, duplicate objectives,
missing Spanish wording, invalid prerequisite references, and a successful
load containing `units-tens`, `compose-two-digit`, `add-within-20`, and
`subtract-within-20`. Add catalog gates requiring 30–50 reviewed,
parameterised templates, at least one template for every objective in the
vertical slice, and resolvable references to each objective's known error
patterns and ordered hints.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/math_tutor/infrastructure/test_curriculum_loader.py tests/unit/math_tutor/infrastructure/test_template_catalog.py -v"
```

**Step 3: Implement loader and minimal content**

Use `yaml.safe_load`; validate exact keys before constructing domain objects.
The curriculum YAML contains 8–12 objectives, reviewed Spanish wording, known
error patterns, and ordered hints. The separate activity catalog contains
30–50 reviewed template definitions with objective ID, activity family,
parameter bounds, difficulty bounds, expected-answer specification, applicable
error-pattern IDs, and hint IDs. It contains no generated learner data.

**Step 4: Verify GREEN**

Run both test files; expected: PASS.

**Step 5: Commit**

```bash
git add src/math_tutor/domain/templates.py src/math_tutor/curricula src/math_tutor/infrastructure tests/unit/math_tutor/infrastructure
git commit -m "feat: load primary math curriculum slice"
```

### Task 4: Add deterministic activities and answer verification

**Files:**
- Create: `src/math_tutor/domain/activities.py`
- Create: `src/math_tutor/domain/mathematics.py`
- Create: `tests/unit/math_tutor/domain/test_activities.py`
- Create: `tests/unit/math_tutor/domain/test_mathematics.py`

**Step 1: Write failing tests**

Cover deterministic generation from `(template_id, seed, difficulty)`, exact
place-value checking, addition/subtraction bounds, rejection of negative
subtraction when the template forbids it, and separation of `CORRECT`,
`INCORRECT`, `AMBIGUOUS`, and `NOT_EVALUABLE`. Add a corpus gate that generates
an activity from every one of the 30–50 catalog entries and proves that its
objective, error-pattern references, hints, operands, and expected answer stay
inside the reviewed definition.

Example:

```python
def test_place_value_answer_is_checked_without_the_llm():
    activity = place_value_activity(number=34)
    result = verify_answer(activity, StructuredAnswer(tens=3, units=4))
    assert result.outcome is AnswerOutcome.CORRECT
```

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/math_tutor/domain/test_activities.py tests/unit/math_tutor/domain/test_mathematics.py -v"
```

**Step 3: Implement minimum deterministic engine**

Create immutable `Activity`, `ActivityTemplate`, `StructuredAnswer`, and
`AnswerCheck`. The generator consumes the reviewed template catalog rather
than hardcoding a few example activities. Keep natural-language interpretation
outside this module. Every generated activity carries the expected structured
answer, objective ID, template ID, and applicable error-pattern and hint IDs.

**Step 4: Verify GREEN**

Run the two test files; expected: PASS.

**Step 5: Commit**

```bash
git add src/math_tutor/domain tests/unit/math_tutor/domain
git commit -m "feat: verify primary math activities deterministically"
```

### Task 5: Model learning plans, observations, and provisional competency

**Files:**
- Create: `src/math_tutor/domain/learning.py`
- Create: `src/math_tutor/domain/evidence.py`
- Create: `tests/unit/math_tutor/domain/test_learning.py`
- Create: `tests/unit/math_tutor/domain/test_evidence.py`

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
make test ARGS="tests/unit/math_tutor/domain/test_learning.py tests/unit/math_tutor/domain/test_evidence.py -v"
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
git add src/math_tutor/domain tests/unit/math_tutor/domain
git commit -m "feat: add evidence-based learning progression"
```

### Task 6: Define the tutoring application ports and mutation fence

**Files:**
- Create: `src/math_tutor/application/ports.py`
- Create: `src/math_tutor/application/results.py`
- Create: `src/math_tutor/application/session_runtime.py`
- Create: `src/math_tutor/application/service.py`
- Create: `tests/unit/math_tutor/application/test_service.py`
- Create: `tests/unit/math_tutor/application/test_mutation_fence.py`

**Step 1: Write failing tests**

Specify the mutation-fence guarantees: active generation, durable idempotency,
expected profile/session version, authorised objective, atomic observation plus
evidence plus event, and fail-closed persistence.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/math_tutor/application -v"
```

**Step 3: Implement service and ports**

Declare repository protocols inward. Implement commands for recording answers,
committing hints, selecting the next activity, proposing evidence, proposing a
profile change, and ending a session. Use
`math_tutor.application.session_runtime` for generation cancellation. If its
concurrency pattern is adapted from
`audio_poc`, copy only the required provider-neutral behaviour into
`math_tutor.application.session_runtime` and test it independently.

**Step 4: Verify GREEN**

Run all math-tutor application tests created so far; expected: PASS.

**Step 5: Commit**

```bash
git add src/math_tutor/application tests/unit/math_tutor/application
git commit -m "feat: add tutoring mutation fence"
```

### Task 7: Implement the bounded pedagogical harness

**Files:**
- Create: `src/math_tutor/harness/contracts.py`
- Create: `src/math_tutor/harness/context.py`
- Create: `src/math_tutor/harness/model.py`
- Create: `src/math_tutor/harness/limits.py`
- Create: `src/math_tutor/harness/registry.py`
- Create: `src/math_tutor/harness/loop.py`
- Create: `src/math_tutor/harness/prompts.py`
- Create: `tests/unit/math_tutor/harness/test_registry.py`
- Create: `tests/unit/math_tutor/harness/test_context.py`
- Create: `tests/unit/math_tutor/harness/test_loop.py`

**Step 1: Write failing tests**

Test the smallest tool surface and its policies:

- `record_answer` requires current-turn evidence and structured answer;
- `give_hint` must use the next reviewed hint and respect the hint cap;
- `adapt_difficulty` moves by at most one step inside active objectives;
- `propose_skill_update` cannot directly consolidate the profile;
- `end_session` respects stop requests immediately;
- mathematical speech is released only after deterministic verification;
- invalid output gets one bounded repair; and
- model/tool steps remain subject to explicit hard budgets.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/math_tutor/harness -v"
```

**Step 3: Implement contracts, registry, context, and loop**

Define provider-neutral `ModelAdapter` and `HarnessLimits` contracts inside the
math-tutor harness. They may adapt the corresponding patterns from `audio_poc`
in this task, without importing that project or copying its domain semantics.
Build context from child-safe static policy, authorised plan, current activity,
structured learner state, bounded recent history, and current turn. Never
include diagnostic labels or unrelated learner data.

**Step 4: Verify GREEN**

Run all math-tutor harness tests created so far; expected: PASS.

**Step 5: Commit**

```bash
git add src/math_tutor/harness tests/unit/math_tutor/harness
git commit -m "feat: add bounded pedagogical harness"
```

### Task 8: Persist tutoring state and selective evidence

**Files:**
- Create: `src/math_tutor/infrastructure/persistence/__init__.py`
- Create: `src/math_tutor/infrastructure/persistence/migrations/0001_initial.sql`
- Create: `src/math_tutor/infrastructure/persistence/migrator.py`
- Create: `src/math_tutor/infrastructure/persistence/repositories.py`
- Create: `tests/unit/math_tutor/infrastructure/test_persistence.py`
- Create: `tests/integration/math_tutor/test_reconstruction.py`

**Step 1: Write failing tests**

Test atomic persistence and reconstruction for learners, plans, sessions,
activities, observations, evidence, provisional estimates, therapist reviews,
and profile revisions. Assert that no column stores full-session audio and that
each clip references one evidence record with bounded duration metadata.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/math_tutor/infrastructure/test_persistence.py tests/integration/math_tutor/test_reconstruction.py -v"
```

**Step 3: Add migration and repositories**

Create schema version `1` for this standalone product. Use append-only revisions
for interpretations and reviews. Store objective observations separately from
revisable estimates. Persist curriculum snapshots and policy versions for
reproducibility. Persistence patterns may be adapted from `audio_poc` only in
this task and must use math-tutor table and field semantics.

**Step 4: Verify GREEN**

Run the two tests plus all persistence tests created in this task; expected:
PASS.

**Step 5: Commit**

```bash
git add src/math_tutor/infrastructure/persistence tests/unit/math_tutor/infrastructure/test_persistence.py tests/integration/math_tutor/test_reconstruction.py
git commit -m "feat: persist tutoring sessions and evidence"
```

### Task 9: Produce evidence-linked summaries and therapist corrections

**Files:**
- Create: `src/math_tutor/application/summary.py`
- Create: `src/math_tutor/application/review.py`
- Create: `tests/unit/math_tutor/application/test_summary.py`
- Create: `tests/integration/math_tutor/test_review.py`

**Step 1: Write failing tests**

Assert that every material summary claim contains evidence IDs, unsupported
interpretations are labelled hypotheses, discarded evidence disappears from
the current view without deleting history, therapist corrections preserve the
original proposal, and dependent estimates are recalculated.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/math_tutor/application/test_summary.py tests/integration/math_tutor/test_review.py -v"
```

**Step 3: Implement summary and correction services**

Generate the authoritative summary from structured rows. If an LLM produces a
readable narrative, validate its claim IDs against that authoritative summary
before display.

**Step 4: Verify GREEN**

Run the task tests; expected: PASS.

**Step 5: Commit**

```bash
git add src/math_tutor/application tests
git commit -m "feat: add evidence-linked tutoring review"
```

### Task 10: Add composition and voice routing

**Files:**
- Create: `src/math_tutor/agent/__init__.py`
- Create: `src/math_tutor/agent/voice_agent.py`
- Create: `src/math_tutor/agent/runtime_factory.py`
- Create: `src/math_tutor/agent/worker.py`
- Create: `src/math_tutor/infrastructure/dispatch.py`
- Modify: `docker/Dockerfile.agent`
- Modify: `docker-compose.yml`
- Modify: `Makefile`
- Create: `web/app.py`
- Create: `web/token_api.py`
- Create: `web/review_api.py`
- Create: `web/static/index.html`
- Create: `web/static/app.js`
- Create: `tests/unit/math_tutor/agent/test_voice_agent.py`
- Create: `tests/contract/math_tutor/test_worker_contract.py`
- Create: `tests/contract/math_tutor/test_token_endpoint.py`
- Create: `tests/contract/math_tutor/test_review_endpoint.py`
- Create: `tests/contract/math_tutor/test_web_app.py`
- Create: `tests/contract/math_tutor/test_voice_client.py`
- Create: `tests/integration/math_tutor/test_voice_boundary.py`

**Step 1: Write failing tests**

Test math-tutor-tagged dispatch metadata, strict provider configuration with no
silent fallback, turn correlation, interruption cancellation, low-confidence
confirmation, and stop priority. Contract tests must prove that the agent image
`CMD` and `make agent` start `math_tutor.agent.worker`, Compose starts the web
service with `uvicorn web.app:app`, the FastAPI app exposes token and review
routes, and the served client requests a room token before connecting to
LiveKit.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/math_tutor/agent/test_voice_agent.py tests/contract/math_tutor tests/integration/math_tutor/test_voice_boundary.py -v"
```

**Step 3: Implement composition**

Create the math-tutor worker and composition root. In this task only, copy or
adapt the required STT correlation, TTS watchdog, terminal closer, provider
factory, and active-generation patterns from `audio_poc`; place them under
`src/math_tutor/`, remove all source-product semantics, and pin their behaviour
with local tests. The standalone worker has no product-mode branch. Replace the
scaffold image `CMD`, Compose commands, and Make target so none remains a
placeholder: the agent launches the real worker and the web service launches
the FastAPI application through Uvicorn. Implement the minimum token and review
APIs plus a static voice client that obtains a token and connects to LiveKit.

**Step 4: Verify GREEN**

Run the task tests and all math-tutor agent and contract tests; expected: PASS.

**Step 5: Commit**

```bash
git add src/math_tutor/agent src/math_tutor/infrastructure/dispatch.py docker/Dockerfile.agent docker-compose.yml Makefile web tests/unit/math_tutor/agent tests/contract/math_tutor tests/integration/math_tutor/test_voice_boundary.py
git commit -m "feat: route voice sessions to tutoring runtime"
```

### Task 11: Capture short evidence clips without retaining full audio

**Files:**
- Create: `src/math_tutor/domain/audio_consent.py`
- Create: `src/math_tutor/infrastructure/evidence_clips.py`
- Create: `src/math_tutor/infrastructure/clip_retention.py`
- Create: `src/math_tutor/infrastructure/persistence/migrations/0002_audio_consent.sql`
- Modify: `src/math_tutor/infrastructure/persistence/migrator.py`
- Modify: `src/math_tutor/infrastructure/persistence/repositories.py`
- Modify: `src/math_tutor/agent/voice_agent.py`
- Modify: `.env.example`
- Create: `tests/unit/math_tutor/infrastructure/test_evidence_clips.py`
- Create: `tests/unit/math_tutor/infrastructure/test_clip_retention.py`
- Create: `tests/integration/math_tutor/test_selective_audio_retention.py`

**Step 1: Write failing tests**

Use fake audio frames and a fake clock to prove that the rolling in-memory
buffer is bounded, non-selected frames are discarded, selected clips contain
only the configured context window, clip duration cannot exceed the cap, and
session close removes pending buffers. Prove that capture is disabled by
default, no encoded audio can be persisted without an active explicit consent
record for that learner and session, revocation blocks further persistence,
retention cannot exceed the 30-day hard ceiling, expired clips are deleted, and
an authorised explicit-delete command removes both file and metadata without
leaving an accessible orphan.

**Step 2: Verify RED**

```bash
make test ARGS="tests/unit/math_tutor/infrastructure/test_evidence_clips.py tests/unit/math_tutor/infrastructure/test_clip_retention.py tests/integration/math_tutor/test_selective_audio_retention.py -v"
```

**Step 3: Implement clip capture**

Maintain a short per-session ring buffer in memory. Persist encoded audio only
when `AUDIO_EVIDENCE_ENABLED=true`, the harness commits an evidence-selection
decision, and an unexpired explicit `AudioConsent` authorises capture for the
learner and session. The feature is default-off. Require a configurable
retention period of 1–30 days when enabled, record `expires_at`, delete expired
clips on startup and periodic sweep, and expose an idempotent explicit-delete
operation for the review API. Consent revocation prevents new clips and queues
existing clips from that consent scope for deletion. Store files under a
configured math-tutor evidence directory with opaque IDs; store no transcript
in filenames or logs. Persist consent scope, revocation, clip expiry, and
deletion audit data through migration `0002_audio_consent.sql`.

**Step 4: Verify GREEN**

Run task tests; expected: PASS.

**Step 5: Commit**

```bash
git add src/math_tutor/domain/audio_consent.py src/math_tutor/agent/voice_agent.py src/math_tutor/infrastructure/evidence_clips.py src/math_tutor/infrastructure/clip_retention.py src/math_tutor/infrastructure/persistence .env.example tests/unit/math_tutor/infrastructure tests/integration/math_tutor/test_selective_audio_retention.py
git commit -m "feat: retain only selected tutoring audio evidence"
```

### Task 12: Add therapist review UI and API

**Files:**
- Modify: `web/review_api.py`
- Create: `web/static/tutoring-review.html`
- Create: `web/static/tutoring-review.js`
- Modify: `web/app.py`
- Create: `tests/contract/math_tutor/test_review_api.py`
- Create: `tests/contract/math_tutor/test_review_page.py`

**Step 1: Write failing contract tests**

Cover learner/session list, evidence-linked detail, authorised access to
unexpired clips, idempotent explicit clip deletion, structured correction
commands, optimistic concurrency, immutable history, no-store headers, and
absence of diagnostic labels.

**Step 2: Verify RED**

```bash
make test ARGS="tests/contract/math_tutor/test_review_api.py tests/contract/math_tutor/test_review_page.py -v"
```

**Step 3: Implement read and correction surfaces**

Keep reads on query-only connections. Send corrections through a narrow writer
service with expected revision. Render objective progress, assistance, evidence,
provisional hypotheses, and next-objective proposals separately.

**Step 4: Verify GREEN**

Run all math-tutor review tests; expected: PASS.

**Step 5: Commit**

```bash
git add web tests/contract
git commit -m "feat: add therapist tutoring review"
```

### Task 13: Build tutoring evals and the acceptance gate

**Files:**
- Create: `evals/math_tutor/runner.py`
- Create: `evals/math_tutor/metrics.py`
- Create: `evals/math_tutor/scenarios/`
- Create: `tests/integration/math_tutor/test_eval_runner.py`
- Modify: `Makefile`
- Modify: `TESTING_REFERENCE.md`

**Step 1: Write failing tests**

Create scenarios for correct answers, conceptual errors, self-correction, low
STT confidence, ambiguous language, hint exhaustion, stop requests, frustration,
out-of-scope objective proposals, and repeated evidence. Grade database state,
never exact model wording.

**Step 2: Verify RED**

```bash
make test ARGS="tests/integration/math_tutor/test_eval_runner.py -v"
```

**Step 3: Implement runner and gate**

Add `make eval-math`. Report mathematical speech errors, unsupported
profile updates, STT misattributions, ignored stop requests, intervention
ratings, evidence coverage, latency, and review-time fixtures. Exit non-zero if
any hard safety invariant fails.

**Step 4: Verify GREEN**

```bash
make test ARGS="tests/integration/math_tutor/test_eval_runner.py -v"
make eval-math
```

Expected: tests PASS; deterministic fake-model eval gate exits `0`.

**Step 5: Commit**

```bash
git add evals/math_tutor tests/integration/math_tutor/test_eval_runner.py Makefile TESTING_REFERENCE.md
git commit -m "test: gate tutoring behaviour with offline evals"
```

### Task 14: Close the PoC verification gate

**Files:**
- Create: `docs/math-tutor-poc-verification.md`
- Modify: `AGENTS.md`
- Create: `docs/DOCKER.md`

**Step 1: Run automated verification**

```bash
make test
make eval-math
docker compose --profile dev config
```

Expected: full tests PASS, the math-tutor fake-model eval gate PASS, and the
Compose configuration is valid.

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
git add AGENTS.md docs/DOCKER.md docs/math-tutor-poc-verification.md
git commit -m "docs: close tutoring poc verification gate"
```

## Final review checklist

- Every implementation path belongs to the standalone math-tutor repository.
- Mathematical output is deterministically verified before speech.
- Low-confidence STT never degrades learner state.
- Stop and pause requests pre-empt pedagogy.
- Material profile updates are evidence-linked and reviewable.
- Objective observations are immutable; interpretations are versioned.
- No full-session audio is persisted.
- Every retained clip has a reason, evidence ID, and bounded duration.
- The therapist can correct the agent and reconstruct the prior state.
- The tutoring acceptance gate fails closed on safety regressions.
