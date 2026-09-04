"""Provider-neutral outcomes returned by application commands."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class CommandStatus(Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    PERSISTENCE_FAILED = "persistence-failed"


@dataclass(frozen=True, slots=True)
class CommandResult:
    command_id: str
    status: CommandStatus
    reason: str
    payload: object | None = None
    replayed: bool = False

    def as_replay(self) -> "CommandResult":
        return replace(self, replayed=True)
