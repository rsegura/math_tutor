# Conversation Recovery Design

## Problem

Help requests currently depend on the LLM. Any provider, parsing, repair, or
contract exception becomes `llm-provider-unavailable`, ends the session, and
speaks a terminal fallback. Shutdown then fails because LiveKit
`delete_room()` already returns a `Task` while the helper expects a coroutine.

## Deterministic learner support

Recognize a narrow Spanish set of help/repeat intents using normalized tokens
and phrase boundaries. Do not match a negative phrase embedded in a
mathematical answer. The normative order is: explicit stop → ended/duration or
activity terminal cap → low STT confidence → help/repeat → LLM.

A help turn never records an incorrect mathematical observation:

1. If an ordered hint remains, execute `GIVE_HINT` through the existing
   registry/application mutation fence and speak the reviewed hint followed by
   the current canonical question.
2. If no hint remains or the hint action is infeasible, repeat the canonical
   prompt without learner/domain-progress mutation. A receipt-only database
   write still occurs. This change does not invent an easier-activity command
   or infer lower competence without the evidence existing policy requires.

The application stores a durable support receipt keyed by session and turn ID.
The first action and receipt commit atomically; replay returns exactly the same
speech/action/activity result before constructing another versioned command.
It cannot consume a second hint. Receipts contain no raw transcript.
For canonical fallback, a receipt-only transaction uses unique
`(session_id, turn_id)`; a concurrent loser loads and returns the winner. For a
hint, progress/event/processed result and receipt commit in one transaction.

Activity cap blocks new selection, hint cap blocks a hint, and attempt cap
blocks answer evaluation. An infeasible support action is not itself terminal
while the session remains active.

## LLM failure recovery

Use closed neutral categories: `ProviderTimeout`, `ProviderRateLimited`,
`ProviderUpstreamUnavailable`, `ProviderInvalidResponse`, and
`HarnessProposalInvalid`, plus `HarnessContractExhausted`. Map SDK type/status
without retaining message or body. Timeout/rate-limit/upstream abort the current
harness attempt. Wire/shape parsing becomes `ProviderInvalidResponse`; parsing
or `ToolRejected(crossed_fence=False)` becomes `HarnessProposalInvalid`; both
may consume the single repair. Post-fence rejection is not repaired. Exhausting
the second call becomes `HarnessContractExhausted`. All internal calls for one
learner turn count as one conversational failure.

The engine maintains an in-job consecutive-failure count and monotonic turn
epoch under a turn-state async lock. This epoch is separate from the consumable
`SessionRuntime` generation. Each turn captures its epoch; only the still-current
epoch may change the counter:

- failures 1–2 return a deterministic non-terminal recovery phrase plus the
  current reviewed prompt, with no learner-failure evidence;
- failure 3 performs one idempotent durable `llm-provider-unavailable` stop;
- a valid current-epoch `ConversationReply`, applied tool, or replayed tool
  resets the count even when its domain generation was legitimately consumed;
- cancellation/stale completions neither increment nor reset it;
- deterministic help preserves it because help did not invoke the LLM.

A worker restart resets this transient outage-smoothing counter.

The sole structured event is `llm_turn_failed`, with allowlisted fields
`failure_code`, `provider`, `model`, opaque `session_id`, and
`consecutive_count`, emitted with `exc_info=False`. Never log API keys,
transcript, prompt, response body, pseudonym, raw exception, cause, or unchecked
validation text.

## Teardown ownership

Existing external `Task`/`Future` objects are awaited directly under `shield`
and are not cancelled because LiveKit owns them. Raw coroutines become
helper-owned tasks; only those are cancelled and drained on timeout. Cleanup is
bounded and later steps still run after an earlier failure.

## Verification

- Existing-Task shutdown succeeds; external Task timeout does not cancel it;
  owned coroutine timeout does cancel/drain it.
- Boundary-aware help matching has no false positives.
- Help creates no wrong observation; hint delivery, exhausted fallback,
  atomic durable replay and stop/cap/STT precedence are tested.
- Counter tests cover two recoveries, third durable stop, success reset, help
  preservation, cancellation neutrality, stale epochs and concurrency, with
  distinct conversation/applied-tool/replayed-tool successes.
- Logging tests place secrets/transcript/body in exception and cause chains and
  prove only allowlisted fields are emitted.
- Full offline suite/eval remain green; supervised voice replay stays manual.
