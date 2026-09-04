"""One-turn bounded orchestration around a probabilistic model."""
from collections.abc import Mapping
from math_tutor.harness.context import HarnessContext
from math_tutor.harness.contracts import ConversationReply, HarnessDecision, SpeechKind, ToolName, ToolProposal
from math_tutor.harness.limits import HarnessLimits
from math_tutor.harness.model import ModelAdapter
from math_tutor.harness.prompts import REPAIR_PROMPT, REVIEWED_SOCIAL_REPLIES, SYSTEM_PROMPT
from math_tutor.harness.registry import PedagogicalToolRegistry, ToolRejected

class HarnessBudgetExceeded(RuntimeError): pass

def _parse(output: object) -> ToolProposal | ConversationReply:
    if not isinstance(output, Mapping): raise ValueError("model-output-must-be-object")
    if output.get("type") == "tool":
        try: return ToolProposal(ToolName(output["name"]), output.get("arguments", {}))
        except (KeyError, TypeError, ValueError): raise ValueError("invalid-tool-output") from None
    if output.get("type") == "reply":
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
        last_error = None
        for call_index in range(self._limits.max_model_calls):
            repair = call_index > 0
            try:
                action = _parse(self._model.complete(prompt=REPAIR_PROMPT if repair else SYSTEM_PROMPT, context=context, repair=repair))
                if isinstance(action, ConversationReply): return HarnessDecision(speech=action.speech)
                return self._registry.execute(action, context)
            except (ValueError, ToolRejected) as exc: last_error = exc
        if self._limits.max_model_calls < 2: raise HarnessBudgetExceeded("model-call-budget-exhausted") from last_error
        assert last_error is not None
        raise last_error
