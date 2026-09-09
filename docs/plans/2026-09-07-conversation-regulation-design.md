# Conversation Regulation Design

**Status:** Proposed for adversarial review  
**Date:** 2026-09-07

## Problem

The tutor currently handles a small set of help phrases deterministically. That is safe, but insufficient for young learners with attention or neurological difficulties: confusion, frustration, task rejection, distraction, fatigue, and requests for help can be expressed in many ways and evolve over several turns. Repeated difficulty must not be treated as a wrong mathematical answer or as permission to end the session.

## Product outcome

The tutor recognizes conversational difficulty, responds supportively, and changes how it presents the same reviewed mathematics. The therapist controls which regulation strategies are available and later reviews only important structured events. An explicit request to stop always ends immediately.

This is pedagogical regulation, not clinical assessment. Signals are provisional interpretations of the current interaction and must never be presented or persisted as diagnoses.

## Responsibilities

| Component | Responsibility |
|---|---|
| LLM adapter | Propose one typed conversational signal and one permitted strategy, or an existing mathematical tool. It does not mutate state or produce mathematical facts. |
| Harness | Validate current-turn evidence, enum membership, confidence, therapist policy, caps, precedence, and speech/content provenance. Reject invalid proposals before speech release. |
| Domain/application | Execute an authorised strategy deterministically, advance counters atomically, and persist selected structured evidence. It never accepts an LLM conclusion as mathematical evidence. |
| Therapist plan | Define allowed regulation strategies and bounded limits. Defaults preserve existing plans. |
| Review summary | Expose important regulation events separately from mathematical progress, without full transcript storage. |

Dependency direction remains `infrastructure -> application -> domain`; vendor SDKs remain outside domain/application.

## Closed contracts

`ConversationalSignal` is one of:

- `confused`
- `frustrated`
- `task-rejecting`
- `off-task`
- `requesting-help`
- `requesting-pause`

`PedagogicalStrategy` is one of:

- `repeat-instruction`
- `simplify-language`
- `give-ordered-hint`
- `redirect-gently`
- `validate-emotion`
- `take-short-pause`

The model returns a `regulate_conversation` tool proposal with exactly:

```json
{
  "turn_id": "current-turn-id",
  "signal": "frustrated",
  "confidence": 0.82,
  "strategy": "validate-emotion"
}
```

There is deliberately no model-authored `engaged` transition. Only a deterministically evaluated mathematical answer resets accumulated difficulty, preventing a model label from erasing the support history.

No free-form rationale or speech crosses the boundary. The current transcript is already ephemeral turn evidence and is not copied into the durable regulation record.

## Policy

Each provisioned plan gains a `regulation` object:

```json
{
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
```

For backwards compatibility the HTTP field is optional. Existing stored plans load a versioned default policy with the safe strategies above and a cap of four. Plan updates snapshot policy into the new plan version; running sessions continue using their plan snapshot.

Signal/strategy compatibility is a deterministic matrix. `confused` and `requesting-help` permit repeat, simplify, or ordered hint; `frustrated` permits validation, simplify, or pause; `task-rejecting` permits validation, gentle redirect, or pause; `off-task` permits gentle redirect; `requesting-pause` permits pause. The MVP deliberately excludes concrete examples and choice generation because current reviewed activity assets do not contain those representations.

Provisioning enforces policy totality: the allowed set must have a non-empty compatibility intersection for every supported signal. Therefore an empty or partial policy that leaves any signal without an executable response is rejected as `incomplete-regulation-policy`. In particular, exact deterministic help always selects the first authorised compatible action in the fixed order `give-ordered-hint`, `simplify-language`, `repeat-instruction`; it never enters model repair. Tests cover empty policies and each missing-signal family. Explicit stop remains outside this policy and can never be disabled.

`give-ordered-hint` does not call `CommitHint`. A shared pure hint-selection/progress helper is used by both commands, while `CommitRegulation` submits hint progress, regulation receipt/state/event, and prior-outcome closure as one `MutationBatch` behind one version/generation fence.

The regulation-turn cap is not an automatic end condition. At the cap, the harness uses the reviewed neutral prompt “¿Quieres continuar o hacer una pausa?”. The result records the closed executed action `cap-choice`, never the model-proposed strategy, because that strategy was not executed. Further difficulty repeats that bounded prompt without advancing the counter; only explicit stop, session time, or existing lifecycle caps end the session.

## Precedence and state machine

For every accepted turn:

1. Explicit stop is detected before the LLM and ends immediately.
2. Existing terminal session caps are enforced.
3. Low-confidence STT requests a repeat and creates no mathematical or regulation evidence.
4. Exact deterministic help phrases continue down the existing fast path.
5. The LLM may propose a mathematical tool or `regulate_conversation`.
6. The harness validates and executes exactly one action before releasing speech.

Durable session-scoped `regulation_state`, introduced by migration `0021_regulation_state.sql`, contains `revision`, `consecutive_turns`, a per-activity monotonic `activity_sequence`, and (from the following event migration) an optional fully structured `pending_event`; it is initialized at zero when a provisioned session is created and reconstructed on resume. Selecting a new activity resets `activity_sequence` to zero. `revision` is a persisted CAS value advanced by every accepted state-changing turn. It is distinct from the existing in-memory, one-shot LLM generation ID: the latter suppresses late model results, while session/profile versions plus `regulation_revision` reject stale persistence attempts. An evaluated mathematical answer atomically resets only `consecutive_turns` and closes a pending event as `answered`; it does not reset `activity_sequence`.

## Canonical responses

Speech is selected by deterministic code from reviewed templates keyed by strategy, age presentation profile, and adaptation flags. `repeat-instruction` uses the active canonical prompt, `give-ordered-hint` uses its next canonical hint, and the remaining MVP strategies use reviewed non-mathematical phrases. The LLM cannot author speech. An exhausted hint deterministically falls back to `simplify-language`; other missing content fails closed before speech release.

The first MVP does not autonomously switch objectives or create activities. That requires a separate pedagogical policy and is intentionally outside this change.

## Durable evidence

Persist only events useful to therapist review:

- `event_id`, `session_id`, `activity_id`, `turn_id`
- provisional `signal` and bounded `confidence_band` (`low`, `medium`, `high`)
- executed `strategy`
- ordinal within the activity and timestamp
- outcome on the following accepted mutation: `answered`, `repeated_difficulty`, `stopped`, or `unknown`

Do not persist transcript, model rationale, diagnosis, or model-generated speech. Reviewed canonical speech may remain in an idempotency receipt, as it does for existing learner support, so replay returns the exact child-facing result. Every accepted regulation turn first stores a minimal structured `pending_event` inside durable regulation state. It is promoted to a reviewable event immediately for frustration, task rejection, or pause; for confusion/help/off-task it is promoted only when the next regulation turn makes the sequence length two. This permits selective retention without storing raw text. Persistence and the session counter update happen in one repository transaction with an idempotent command receipt.

Promotion is exact and deterministic. Event IDs are `regulation-{session_id}-{turn_id}`. Ordinal is the stable chronological position of every accepted, non-replayed regulation mutation within the activity, sourced from `activity_sequence`; it advances even when the consecutive regulation cap has been reached, and never resets on an answer. After a first low-priority turn, state holds its structured pending item (including ordinal 1) with no review row. On a second consecutive regulation turn, the same transaction inserts the first row with outcome `repeated_difficulty`, inserts the second row (ordinal 2) with outcome `unknown` (open), and points pending state at the second row. A third regulation turn closes the second as `repeated_difficulty`, inserts the third as the new open row, and moves the pointer. A high-priority first turn is inserted immediately as the open row. A qualifying answer/stop closes the current materialized row; if only an unmaterialized single low-priority item exists, it is discarded on answer and promoted with outcome `stopped` on explicit stop. Replaying any turn returns its receipt without inserting, promoting, closing, or incrementing either counter.

Every state-changing path participates in outcome closure: an evaluable `RecordAnswer` closes as `answered` and resets; `CommitRegulation` closes the previous item as `repeated_difficulty`; deterministic `SupportLearner` is refactored through `CommitRegulation`; `EndSession` closes as `stopped`; time/activity caps close as `unknown`. Ambiguous/not-evaluable answers and low-confidence STT are not accepted regulation outcomes and do not close or reset anything. The next qualifying mutation closes the prior open event exactly once. Crash recovery retains an open outcome as `unknown`; replay is idempotent.

Confidence is finite numeric input, rejects booleans, NaN, infinity, and values outside `[0,1]`. Valid values map deterministically: `low` for `0.00 <= x < 0.50`, `medium` for `0.50 <= x < 0.80`, and `high` for `0.80 <= x <= 1.00`. A well-formed low-confidence proposal is a valid non-mutating outcome: it releases the reviewed repeat prompt, performs no repair call, does not increment provider-recovery failures, and persists nothing. Malformed/non-finite/out-of-range confidence is a contract error and follows the one-repair path.

## Upgrade compatibility

Migration `0021` creates zeroed regulation state for every existing session, so active sessions resume safely. Migration `0023` upgrades the immutable migration-0019 support receipts into turn-scoped regulation receipts while preserving legacy rows. Every executed regulation action—including hint, repeat, simplify, and cap choice—stores canonical speech, action, revision, and decision reason atomically with its mutation. A receipt is checked before a new generation, policy decision, counter change, event promotion/closure, or hint selection, including after process restart. If two workers miss that preflight concurrently, the conflict loser performs a bounded receipt lookup and returns the exact compatible turn winner instead of entering provider recovery. Replays return the exact prior decision; same command IDs still retain fingerprint collision checks. Upgrade tests cover an active pre-0020 session, a pre-existing migration-0019 receipt, replay after upgrade, and the first new help turn.

`SessionSummary` gains a separate typed `RegulationSummaryItem` collection sourced from regulation events. Regulation event IDs never enter mathematical `evidence_ids` and narrative validation cannot convert them into competence/profile claims.

## Failure behavior

- Invalid signal, strategy, evidence id, policy, compatibility, malformed confidence, or extra field: one repair attempt, then existing closed provider-recovery behavior. A well-formed confidence below `0.50` is handled without repair as described above.
- Database/mutation rejection after crossing the fence: no proposed speech is released; use existing safe recovery.
- Missing canonical content: safe deterministic fallback, never model-authored mathematics.
- Provider timeout/error: existing bounded recovery; no regulation evidence is fabricated.
- Explicit stop during an in-flight model call invalidates its generation and wins.

## Evaluation and observability

Offline scenarios cover paraphrased confusion, repeated frustration, refusal, distraction, pause, false positives (a valid answer containing emotional words), explicit stop, strategy not allowed, cap behavior, stale concurrent proposals, replay, crash between turns, and absence of transcript persistence.

Metrics/log fields are closed codes only: proposed signal, accepted/rejected strategy, rejection code, regulation count, and outcome. No transcript or child profile is logged.

## Non-goals

- Diagnosing neurological, emotional, or learning conditions.
- Inferring stable learner traits from conversational signals.
- Autonomous curriculum/objective changes.
- Storing full conversation.
- Allowing model-authored mathematical explanation.
