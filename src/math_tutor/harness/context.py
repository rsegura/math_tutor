"""Deliberately bounded, child-safe context supplied to a model."""
from dataclasses import dataclass
import unicodedata
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.domain.regulation import RegulationPolicy

class ContextError(ValueError): pass

CHILD_SAFE_POLICY = ("Habla en español con lenguaje breve, respetuoso y educativo. No diagnostiques, no uses etiquetas médicas, no compares con otros niños y no prometas mejoría. Respeta inmediatamente una petición de parar.")
_FORBIDDEN_PROFILE_TERMS = ("diagnos", "lesión", "neurol", "clínic", "medical", "dirección", "teléfono", "historial médico")
_MAX_TEXT_CHARS = 400
_MAX_COLLECTION_ENTRIES = 20
_MAX_HISTORY_CHARS = 1200
_PRESENTATION_VALUES = frozenset({"concrete-and-playful", "clear-and-encouraging", "age-respectful", "short", "medium", "short-instructions"})
_ADAPTATION_VALUES = frozenset({"short-instructions", "slow-pace", "extra-repetition", "concrete-examples", "reduced-choice"})

def _clean(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip(): raise ContextError(f"{label} must be a trimmed nonempty string")
    if len(value) > _MAX_TEXT_CHARS: raise ContextError(f"{label} exceeds character budget")
    normalized = "".join(character for character in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(character))
    forbidden = tuple("".join(character for character in unicodedata.normalize("NFKD", term.casefold()) if not unicodedata.combining(character)) for term in _FORBIDDEN_PROFILE_TERMS)
    if any(term in normalized for term in forbidden): raise ContextError(f"{label} contains diagnostic or unrelated data")
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
    expected_answer_kind: ExpectedAnswerKind = ExpectedAnswerKind.INTEGER
    expected_answer_fields: tuple[str, ...] = ("answer",)
    def __post_init__(self) -> None:
        for value, label in ((self.activity_id,"activity id"),(self.template_id,"template id"),(self.objective_id,"objective id"),(self.prompt_es,"prompt")): _clean(value,label)
        if not isinstance(self.difficulty, int) or isinstance(self.difficulty, bool): raise ContextError("difficulty must be an integer")
        if len(self.hint_ids) != len(self.hint_texts): raise ContextError("reviewed hint ids and texts must align")
        if len(self.hint_ids) > _MAX_COLLECTION_ENTRIES: raise ContextError("hints exceed entry budget")
        for value in self.hint_ids: _clean(value, "hint id")
        for value in self.hint_texts: _clean(value, "hint text")
        if not isinstance(self.hints_used, int) or not 0 <= self.hints_used <= len(self.hint_ids): raise ContextError("invalid hints used")
        if isinstance(self.attempts_used, bool) or not isinstance(self.attempts_used, int) or self.attempts_used < 0: raise ContextError("invalid attempts used")
        if not isinstance(self.expected_answer_kind, ExpectedAnswerKind): raise ContextError("expected answer kind must be typed")
        if not self.expected_answer_fields: raise ContextError("expected answer fields cannot be empty")
        for value in self.expected_answer_fields: _clean(value, "expected answer field")

@dataclass(frozen=True, slots=True)
class LearnerState:
    competency_states: tuple[tuple[str, str], ...]
    presentation: tuple[str, ...]
    def __post_init__(self) -> None:
        if any(not isinstance(pair, tuple) or len(pair) != 2 for pair in self.competency_states): raise ContextError("competency states must contain objective-state pairs")
        values = tuple(value for pair in self.competency_states for value in pair) + tuple(self.presentation)
        if len(self.competency_states) > _MAX_COLLECTION_ENTRIES or len(self.presentation) > _MAX_COLLECTION_ENTRIES: raise ContextError("learner state exceeds entry budget")
        for value in values: _clean(value, "learner state field")
        if any(value not in _PRESENTATION_VALUES for value in self.presentation): raise ContextError("presentation contains unrelated data")
        if sum(len(value) for value in values) > _MAX_HISTORY_CHARS: raise ContextError("learner state exceeds character budget")

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
    adaptations: tuple[str, ...] = ()
    duration_minutes: int = 10
    max_activities: int = 1
    activities_used: int = 0
    regulation_policy: RegulationPolicy | None = None
    regulation_revision: int = 0
    consecutive_regulation_turns: int = 0
    regulation_activity_sequence: int = 0
    def __post_init__(self) -> None:
        if self.child_safe_policy != CHILD_SAFE_POLICY: raise ContextError("child safe policy is fixed")
        for value, label in ((self.session_id,"session id"),(self.learner_id,"learner id"),(self.generation_id,"generation id")): _clean(value,label)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (self.expected_session_version, self.expected_profile_version)): raise ContextError("expected versions must be positive integers")
        if not set(self.active_objective_ids).issubset(self.authorised_objective_ids): raise ContextError("active objective must belong to authorised plan")
        if self.activity.objective_id not in self.active_objective_ids: raise ContextError("current activity objective must be active")
        if any(objective not in self.authorised_objective_ids for objective, _ in self.learner_state.competency_states): raise ContextError("learner state objective must be authorised")
        for collection, label in ((self.authorised_objective_ids, "authorised objectives"), (self.active_objective_ids, "active objectives"), (self.recent_history, "history")):
            if len(collection) > _MAX_COLLECTION_ENTRIES: raise ContextError(f"{label} exceed entry budget")
            for value in collection: _clean(value, label)
        if sum(len(value) for value in self.recent_history) > _MAX_HISTORY_CHARS: raise ContextError("history character budget exceeded")
        if len(set(self.adaptations)) != len(self.adaptations) or any(value not in _ADAPTATION_VALUES for value in self.adaptations): raise ContextError("adaptations contain unsupported values")
        if isinstance(self.duration_minutes, bool) or not isinstance(self.duration_minutes, int) or not 5 <= self.duration_minutes <= 30: raise ContextError("duration minutes out of range")
        if isinstance(self.max_activities, bool) or not isinstance(self.max_activities, int) or not 1 <= self.max_activities <= 20: raise ContextError("maximum activities out of range")
        if isinstance(self.activities_used, bool) or not isinstance(self.activities_used, int) or not 0 <= self.activities_used <= self.max_activities: raise ContextError("activities used out of range")
        if self.regulation_policy is not None and not isinstance(self.regulation_policy, RegulationPolicy): raise ContextError("regulation policy must be typed")
        for name in ("regulation_revision", "consecutive_regulation_turns", "regulation_activity_sequence"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0: raise ContextError(f"{name.replace('_', ' ')} must be nonnegative")
        if self.regulation_policy is not None and self.consecutive_regulation_turns > self.regulation_policy.max_consecutive_regulation_turns: raise ContextError("consecutive regulation turns exceed policy cap")

def build_harness_context(*, max_history_turns: int, max_history_chars: int = _MAX_HISTORY_CHARS, **values: object) -> HarnessContext:
    if isinstance(max_history_turns, bool) or not isinstance(max_history_turns, int) or max_history_turns < 0: raise ContextError("max history turns must be a nonnegative integer")
    if isinstance(max_history_chars, bool) or not isinstance(max_history_chars, int) or max_history_chars < 0: raise ContextError("max history chars must be a nonnegative integer")
    allowed = {"session_id", "learner_id", "expected_session_version", "expected_profile_version", "generation_id", "authorised_objective_ids", "active_objective_ids", "activity", "learner_state", "recent_history", "current_turn", "adaptations", "duration_minutes", "max_activities", "activities_used", "regulation_policy", "regulation_revision", "consecutive_regulation_turns", "regulation_activity_sequence"}
    unknown = set(values) - allowed
    if unknown: raise ContextError(f"unknown context field: {sorted(unknown)[0]}")
    missing = allowed - {"adaptations", "duration_minutes", "max_activities", "activities_used", "regulation_policy", "regulation_revision", "consecutive_regulation_turns", "regulation_activity_sequence"} - set(values)
    if missing: raise ContextError(f"missing context field: {sorted(missing)[0]}")
    history = tuple(values.pop("recent_history"))
    for item in history: _clean(item, "history")
    selected = history[-max_history_turns:] if max_history_turns else ()
    if sum(len(item) for item in selected) > max_history_chars: raise ContextError("history character budget exceeded")
    return HarnessContext(child_safe_policy=CHILD_SAFE_POLICY, recent_history=selected, **values)
