"""One-turn bounded orchestration around a probabilistic model."""
from collections.abc import Mapping
import asyncio
import inspect
from math_tutor.harness.context import HarnessContext
from math_tutor.harness.contracts import ConversationReply, HarnessDecision, SpeechKind, ToolName, ToolProposal
from math_tutor.harness.limits import HarnessLimits
from math_tutor.harness.model import ModelAdapter, ProviderInvalidResponse, ProviderTimeout
from math_tutor.harness.prompts import REPAIR_PROMPT, REVIEWED_SOCIAL_REPLIES, SYSTEM_PROMPT
from math_tutor.harness.registry import PedagogicalToolRegistry, ToolRejected
from math_tutor.domain.regulation import ConfidenceBand, ConversationalSignal, PedagogicalStrategy, compatible_strategies

class HarnessProposalInvalid(RuntimeError):
    code = "harness-proposal-invalid"
    def __init__(self) -> None: super().__init__(self.code)

class HarnessContractExhausted(RuntimeError):
    code = "harness-contract-exhausted"
    def __init__(self, last_failure: HarnessProposalInvalid) -> None:
        super().__init__(self.code)
        self.last_failure = last_failure

# Backward-compatible import while callers migrate to the closed contract.
HarnessBudgetExceeded = HarnessContractExhausted

def _parse(output: object) -> ToolProposal | ConversationReply:
    if not isinstance(output, Mapping): raise ValueError("model-output-must-be-object")
    if output.get("type") == "tool":
        if set(output) != {"type", "name", "arguments"}: raise ValueError("invalid-tool-output")
        try:
            proposal = ToolProposal(ToolName(output["name"]), output.get("arguments", {}))
            if proposal.name is ToolName.REGULATE_CONVERSATION:
                arguments = proposal.arguments
                if set(arguments) != {"turn_id", "signal", "confidence", "strategy"}:
                    raise ValueError
                if not isinstance(arguments["turn_id"], str) or not arguments["turn_id"].strip():
                    raise ValueError
                signal = ConversationalSignal(arguments["signal"])
                strategy = PedagogicalStrategy(arguments["strategy"])
                ConfidenceBand.from_confidence(arguments["confidence"])
                if strategy not in compatible_strategies(signal):
                    raise ValueError
            return proposal
        except (KeyError, TypeError, ValueError): raise ValueError("invalid-tool-output") from None
    if output.get("type") == "reply":
        if set(output) != {"type", "speech", "speech_kind"}: raise ValueError("invalid-reply-output")
        try: speech, kind = output["speech"], SpeechKind(output["speech_kind"])
        except (KeyError, TypeError, ValueError): raise ValueError("invalid-reply-output") from None
        if not isinstance(speech, str) or not speech.strip(): raise ValueError("invalid-reply-output")
        if kind is SpeechKind.MATHEMATICAL: raise ValueError("mathematical-speech-requires-fence")
        if speech not in REVIEWED_SOCIAL_REPLIES: raise ValueError("reply-not-reviewed")
        return ConversationReply(speech, kind)
    raise ValueError("unknown-model-output")

class PedagogicalHarness:
    def __init__(self, model: ModelAdapter, registry: PedagogicalToolRegistry, limits: HarnessLimits) -> None: self._model, self._registry, self._limits = model, registry, limits
    def run(self, context: HarnessContext, *, stop_requested: bool=False) -> HarnessDecision:
        if stop_requested: return self._registry.execute(ToolProposal(ToolName.END_SESSION, {"reason":"stop-requested"}), context)
        if inspect.iscoroutinefunction(self._model.complete):
            raise TypeError("async model adapter requires run_async")
        last_error = None
        tool_steps = 0
        for call_index in range(self._limits.max_model_calls):
            repair = call_index > 0
            try:
                output = self._model.complete(
                    prompt=REPAIR_PROMPT if repair else SYSTEM_PROMPT,
                    context=context,
                    repair=repair,
                    validation_error=None if last_error is None else str(last_error),
                )
                if inspect.isawaitable(output):
                    close = getattr(output, "close", None)
                    if close is not None: close()
                    raise TypeError("awaitable model result requires run_async")
                action = _parse(output)
                if isinstance(action, ConversationReply): return HarnessDecision(speech=action.speech)
                if tool_steps >= self._limits.max_tool_steps:
                    invalid = HarnessProposalInvalid()
                    raise HarnessContractExhausted(invalid) from invalid
                try:
                    decision = self._registry.execute(action, context)
                    tool_steps += 1
                    return decision
                except ToolRejected as exc:
                    if exc.crossed_fence:
                        tool_steps += 1
                        raise HarnessProposalInvalid() from None
                    last_error = HarnessProposalInvalid()
            except ToolRejected:
                raise HarnessProposalInvalid() from None
            except (ValueError, ProviderInvalidResponse):
                last_error = HarnessProposalInvalid()
        assert last_error is not None
        raise HarnessContractExhausted(last_error) from last_error

    async def run_async(self, context: HarnessContext, *, stop_requested: bool=False, total_seconds: float = 10.0) -> HarnessDecision:
        if not 2 <= total_seconds <= 15: raise ValueError("LLM total deadline must be between 2 and 15 seconds")
        if stop_requested: return self._registry.execute(ToolProposal(ToolName.END_SESSION, {"reason":"stop-requested"}), context)
        try:
            async with asyncio.timeout(total_seconds):
                last_error = None
                for call_index in range(self._limits.max_model_calls):
                    try:
                        value = self._model.complete(prompt=REPAIR_PROMPT if call_index else SYSTEM_PROMPT,context=context,repair=bool(call_index),validation_error=None if last_error is None else str(last_error))
                        output = await value if inspect.isawaitable(value) else value
                        action = _parse(output)
                        if isinstance(action, ConversationReply): return HarnessDecision(speech=action.speech)
                        try: return self._registry.execute(action, context)
                        except ToolRejected as exc:
                            if exc.crossed_fence: raise HarnessProposalInvalid() from None
                            last_error = HarnessProposalInvalid()
                    except ToolRejected: raise HarnessProposalInvalid() from None
                    except (ValueError, ProviderInvalidResponse): last_error = HarnessProposalInvalid()
                raise HarnessContractExhausted(last_error or HarnessProposalInvalid()) from (last_error or None)
        except TimeoutError:
            raise ProviderTimeout() from None
