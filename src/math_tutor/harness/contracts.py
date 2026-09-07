"""Small, provider-neutral contract exposed to the pedagogical model."""
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

class ToolName(Enum):
    RECORD_ANSWER = "record_answer"
    GIVE_HINT = "give_hint"
    ADAPT_DIFFICULTY = "adapt_difficulty"
    PROPOSE_SKILL_UPDATE = "propose_skill_update"
    END_SESSION = "end_session"
    REGULATE_CONVERSATION = "regulate_conversation"

class SpeechKind(Enum):
    SOCIAL = "social"
    MATHEMATICAL = "mathematical"

@dataclass(frozen=True, slots=True)
class ToolProposal:
    name: ToolName
    arguments: Mapping[str, object]
    def __post_init__(self) -> None:
        if not isinstance(self.name, ToolName): raise ValueError("unknown-tool")
        if not isinstance(self.arguments, Mapping): raise ValueError("tool-arguments-must-be-mapping")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))

@dataclass(frozen=True, slots=True)
class ConversationReply:
    speech: str
    kind: SpeechKind

@dataclass(frozen=True, slots=True)
class HarnessDecision:
    speech: str | None = None
    terminal: bool = False
    applied_tool: ToolName | None = None
    reason: str = "accepted"
    selected_evidence_id: str | None = None
