# Provider Abstraction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add provider-neutral LLM construction with working OpenAI and OpenRouter adapters, plus optional ElevenLabs voice selection.

**Architecture:** Preserve `harness.model.ModelAdapter` as the inward-facing port and move vendor transport/configuration into focused provider modules selected by a fail-closed factory. Share OpenResponses translation where behavior is genuinely identical, while keeping explicit provider profiles and endpoints.

**Tech Stack:** Python 3.12, OpenAI Python SDK, OpenRouter OpenResponses API, LiveKit Agents, ElevenLabs plugin, pytest, Docker Compose.

---

### Task 1: Define provider-neutral configuration contracts

**Files:**
- Create: `src/math_tutor/agent/providers/__init__.py`
- Create: `src/math_tutor/agent/providers/settings.py`
- Modify: `src/math_tutor/agent/runtime_factory.py`
- Test: `tests/unit/math_tutor/agent/test_provider_settings.py`
- Modify: `tests/contract/math_tutor/test_worker_contract.py`

**Steps:**

1. Write failing tests for `openai`, `openrouter`, rejected unknown providers,
   the exact canonical base-URL matrix, sanitized secret representations and
   errors, whitespace normalization, and provider-specific optional
   `TTS_VOICE_ID` behavior.
2. Run the focused tests in Docker and observe failure.
3. Extract validated settings without importing vendor SDKs. Store the resolved
   base URL, mark API keys `repr=False`, and either migrate every import or
   temporarily re-export settings/errors from `runtime_factory`.
4. Remove the unused `LLM_TEMPERATURE` example/config claim.
5. Run the focused tests and refactor only after green.
6. Commit with `refactor: define provider-neutral runtime settings`.

### Task 2: Extract OpenResponses adapters and factory

**Files:**
- Create: `src/math_tutor/agent/providers/model.py`
- Modify: `src/math_tutor/agent/runtime_factory.py`
- Modify: `src/math_tutor/agent/worker.py`
- Test: `tests/unit/math_tutor/agent/test_model_providers.py`
- Modify: `tests/unit/math_tutor/agent/test_runtime_factory.py`

**Steps:**

1. Write failing tests proving OpenAI and OpenRouter construct the correct SDK
   clients and translate canonical tool/text results. Use representative
   OpenRouter Responses fixtures, not only shared `SimpleNamespace` doubles.
2. Test failed/incomplete/error responses, multiple calls, mixed unexpected
   output, unknown output, and invalid arguments fail closed without leaking
   secrets, transcripts, prompts, or response bodies.
3. Implement a shared OpenResponses adapter plus explicit OpenAI/OpenRouter
   factory profiles. The default OpenRouter endpoint is
   `https://openrouter.ai/api/v1`.
4. Inject a lazy model-factory callback into `BoundedConversationEngine` but do
   not call it in `__init__`. Construct and retain the provider-free
   `PedagogicalToolRegistry` there. In `decide()`, evaluate terminal state, caps,
   and `is_stop_request` first. Execute a stop as
   `ToolProposal(ToolName.END_SESSION, {"reason": "stop-requested"})` through
   that existing registry/fence. Create/cache only the adapter and
   `PedagogicalHarness(adapter, existing_registry, limits)` immediately before
   a real model call, guarded by an async lock. Retain direct `model=` injection
   for offline tests. Prove immediate stop creates zero adapters, terminates
   durably with equivalent reason/decision, and traverses the registry rather
   than a direct service shortcut. Prove the first normal use creates one
   adapter, later turns reuse it, and direct concurrent calls to the lazy
   initializer create at most one. Do not construct the client early in
   `worker.py`.
5. Update `ModelAdapter` to the explicit sync-or-awaitable contract. Prove
   production uses `run_async()` and make the sync path reject awaitables with
   a clear error. Detect declared async callables before invoking them and
   explicitly close any unexpected coroutine returned by a nominally sync
   adapter before raising.
6. Run focused tests and commit with
   `refactor: add interchangeable llm provider adapters`.

### Task 3: Make ElevenLabs voice selection optional

**Files:**
- Create: `src/math_tutor/agent/providers/voice.py`
- Modify: `src/math_tutor/agent/runtime_factory.py`
- Test: `tests/unit/math_tutor/agent/test_voice_providers.py`
- Modify: `tests/contract/math_tutor/test_worker_contract.py`

**Steps:**

1. Write failing tests for empty and explicit ElevenLabs voice IDs.
2. Verify empty configuration currently fails.
3. Move STT/TTS construction behind a voice-provider factory. Omit
   `voice_id` entirely for an empty ElevenLabs setting; forward it when set.
4. Keep an explicit voice required for OpenAI TTS. Test missing kwargs rather
   than accepting `voice_id=None`; document that construction does not prove
   free-account access to the plugin default.
5. Run focused tests and commit with
   `feat: allow default ElevenLabs voice selection`.

### Task 4: Update configuration and operator documentation

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Modify: `docs/DOCKER.md`
- Modify: `docs/math-tutor-poc-verification.md`
- Test: `tests/test_environment.py`

**Steps:**

1. Add or update documentation contract tests for OpenRouter and optional
   ElevenLabs voice configuration.
2. Document exact OpenAI/OpenRouter examples and state that Gemini is an
   unimplemented future adapter.
3. Remove stale claims that `LLM_BASE_URL` is unwired if it becomes active.
4. Add a `live_llm` OpenRouter smoke that calls Responses with one real
   user-defined tool and validates canonical parsing. It must skip without
   credentials and clearly report PASS/SKIP/FAIL.
5. Document that the selected OpenRouter model must support Responses and tool
   calling, and that Gemini remains unimplemented.
6. Run documentation/config tests and `docker compose --profile dev config`.
7. Commit with `docs: configure OpenRouter model provider`.

### Task 5: Close the provider verification gate

**Files:**
- Modify as required only for defects revealed by verification.

**Steps:**

1. Run `make test`.
2. Run `make eval-math`.
3. Run `docker compose --profile dev config`.
4. Run the OpenRouter opt-in live smoke. Record PASS, SKIP/no credentials, or
   FAIL exactly; never convert a missing-credential skip into a live pass.
5. Review dependency direction and confirm no vendor SDK imports entered
   domain, application, or harness.
6. Commit only genuine fixes arising from the gate.
