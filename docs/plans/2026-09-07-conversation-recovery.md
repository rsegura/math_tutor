# Conversation Recovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Keep tutoring alive through deterministic help turns and two current-generation LLM failures, while fixing Task-aware shutdown.

**Architecture:** Route bounded help through an atomic application receipt and existing mutation fence, classify provider failures without sensitive content, serialize a generation-aware recovery counter, and respect awaitable ownership during teardown.

**Tech Stack:** Python 3.12, asyncio, LiveKit Agents 1.7.1, OpenResponses, SQLite, pytest, Docker.

---

### Task 1: Make terminal teardown Task-aware

**Files:** `src/math_tutor/agent/lifecycle.py`, `tests/unit/math_tutor/agent/test_lifecycle.py`

1. Reproduce `delete_room()` returning an existing Task.
2. Await external Future/Task under shield without cancelling it; schedule and
   cancel/drain only helper-owned coroutine work.
3. Test external success and timeout/non-ownership, owned timeout/cancellation,
   closer cancellation, later cleanup and idempotency.
4. Run focused tests and commit `fix: accept LiveKit tasks during shutdown`.

### Task 2: Add atomic deterministic learner-support routing

**Files:**
- Modify `src/math_tutor/agent/voice_agent.py`, runtime/application ports and
  service, SQLite repository/serialization as required
- Create one numbered migration for durable support receipts
- Add unit, persistence and integration tests

1. Test boundary-aware phrases/false positives and exact
   stop→terminal cap→low confidence→help→LLM precedence.
2. Test no incorrect observation, ordered hint, exhausted canonical fallback,
   concurrent/replayed idempotency and no second hint.
3. Add an application operation whose first hint mutation and support receipt
   commit atomically. Check the receipt before constructing new versioned state;
   store speech/action/activity but no transcript.
4. Execute hint only through the existing fence; store/replay canonical prompt
   without learner/progress mutation when no hint is feasible. The receipt-only
   transaction has unique `(session_id, turn_id)`; a racing loser returns the
   winner. Hint progress/event/result/receipt commit together. Do not select
   easier activities.
5. Commit `feat: recover deterministic learner help turns` after focused tests.

### Task 3: Add bounded and observable LLM recovery

**Files:** provider model, harness loop/contracts, runtime engine and focused tests

1. Test neutral `ProviderTimeout`, `ProviderRateLimited`,
   `ProviderUpstreamUnavailable`, `ProviderInvalidResponse`, and
   `HarnessProposalInvalid`, plus `HarnessContractExhausted` types.
2. Map SDK type/status without preserving messages/bodies. Abort provider
   availability errors. Allow one repair for invalid wire/shape output and for
   parsing or `ToolRejected(crossed_fence=False)` proposal failures; never repair
   a post-fence rejection. Exhaustion becomes `HarnessContractExhausted`; count
   one learner turn as one failure.
3. Add a monotonic engine turn epoch separate from `SessionRuntime` generation.
   Under a turn-state lock, change the counter only for the current epoch.
   Cancellation/stale epochs are neutral; help preserves; conversation replies,
   applied tools and replayed tools reset even after legitimate generation
   consumption.
4. Return non-terminal reviewed recovery for failures 1–2; on failure 3 execute
   one idempotent durable stop.
5. Emit only `llm_turn_failed` and its five allowlisted fields with
   `exc_info=False`; inject sensitive cause chains in negative tests.
6. Commit `fix: recover bounded llm provider failures` after focused tests.

### Task 4: Verify and document behavior

**Files:** `README.md`, verification record and testing reference as applicable

1. Document exact behavior without claiming supervised validation.
2. Run full tests, math eval, Compose config and available provider smoke.
3. Record PASS/SKIP/FAIL honestly.
4. Commit `docs: describe bounded conversation recovery`.
