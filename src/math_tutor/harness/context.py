"""Deliberately bounded, child-safe context supplied to a model."""
from dataclasses import dataclass

class ContextError(ValueError): pass

CHILD_SAFE_POLICY = ("Habla en español con lenguaje breve, respetuoso y educativo. No diagnostiques, no uses etiquetas médicas, no compares con otros niños y no prometas mejoría. Respeta inmediatamente una petición de parar.")
_FORBIDDEN_PROFILE_TERMS = ("diagnos", "lesión", "neurol", "clínic", "medical")

def _clean(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip(): raise ContextError(f"{label} must be a trimmed nonempty string")
    return value

@dataclass(frozen=True, slots=True)
class TurnEvidence:
    turn_id: str
    transcript: str
    stt_confidence: float
    def __post_init__(self) -> None:
        _clean(self.turn_id, "turn id"); _clean(self.transcript, "transcript")
        if isinstance(self.stt_confidence, bool) or not isinstance(self.stt_confidence, (int, float)) or not 0 <= self.stt_confidence <= 1: raise ContextError("stt confidence must be between zero and one")

@dataclass(frozen=True, slots=True)
class ActivityContext:
    activity_id: str
    template_id: str
    objective_id: str
    difficulty: int
    prompt_es: str
    hint_ids: tuple[str, ...]
    hint_texts: tuple[str, ...]
    hints_used: int
    attempts_used: int = 0
    def __post_init__(self) -> None:
        for value, label in ((self.activity_id,"activity id"),(self.template_id,"template id"),(self.objective_id,"objective id"),(self.prompt_es,"prompt")): _clean(value,label)
        if not isinstance(self.difficulty, int) or isinstance(self.difficulty, bool): raise ContextError("difficulty must be an integer")
        if len(self.hint_ids) != len(self.hint_texts): raise ContextError("reviewed hint ids and texts must align")
        if not isinstance(self.hints_used, int) or not 0 <= self.hints_used <= len(self.hint_ids): raise ContextError("invalid hints used")
        if isinstance(self.attempts_used, bool) or not isinstance(self.attempts_used, int) or self.attempts_used < 0: raise ContextError("invalid attempts used")

@dataclass(frozen=True, slots=True)
class LearnerState:
    competency_states: tuple[tuple[str, str], ...]
    presentation: tuple[str, ...]
    def __post_init__(self) -> None:
        values = tuple(value for pair in self.competency_states for value in pair) + tuple(self.presentation)
        if any(term in value.casefold() for value in values for term in _FORBIDDEN_PROFILE_TERMS): raise ContextError("learner state field contains diagnostic or unrelated data")
        if any(not isinstance(value, str) or not value.strip() for value in values): raise ContextError("learner state fields must be nonempty strings")

@dataclass(frozen=True, slots=True)
class HarnessContext:
    child_safe_policy: str
    session_id: str
    learner_id: str
    expected_session_version: int
    expected_profile_version: int
    generation_id: str
    authorised_objective_ids: tuple[str, ...]
    active_objective_ids: tuple[str, ...]
    activity: ActivityContext
    learner_state: LearnerState
    recent_history: tuple[str, ...]
    current_turn: TurnEvidence
    def __post_init__(self) -> None:
        if self.child_safe_policy != CHILD_SAFE_POLICY: raise ContextError("child safe policy is fixed")
        for value, label in ((self.session_id,"session id"),(self.learner_id,"learner id"),(self.generation_id,"generation id")): _clean(value,label)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (self.expected_session_version, self.expected_profile_version)): raise ContextError("expected versions must be positive integers")
        if not set(self.active_objective_ids).issubset(self.authorised_objective_ids): raise ContextError("active objective must belong to authorised plan")
        if self.activity.objective_id not in self.active_objective_ids: raise ContextError("current activity objective must be active")
        if any(objective not in self.authorised_objective_ids for objective, _ in self.learner_state.competency_states): raise ContextError("learner state objective must be authorised")

def build_harness_context(*, max_history_turns: int, **values: object) -> HarnessContext:
    if isinstance(max_history_turns, bool) or not isinstance(max_history_turns, int) or max_history_turns < 0: raise ContextError("max history turns must be a nonnegative integer")
    history = tuple(values.pop("recent_history"))
    if any(not isinstance(item, str) or not item.strip() for item in history): raise ContextError("history must contain nonempty strings")
    return HarnessContext(child_safe_policy=CHILD_SAFE_POLICY, recent_history=history[-max_history_turns:] if max_history_turns else (), **values)
