"""Explicit hard budgets for one harness turn."""
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class HarnessLimits:
    max_model_calls: int = 2
    max_tool_steps: int = 1
    max_hints_per_activity: int = 3
    max_attempts_per_activity: int = 3
    min_difficulty: int = 1
    max_difficulty: int = 5
    def __post_init__(self) -> None:
        for name in ("max_model_calls", "max_tool_steps", "max_hints_per_activity", "max_attempts_per_activity"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1: raise ValueError(f"{name} must be a positive integer")
        if self.max_model_calls > 2: raise ValueError("max_model_calls cannot exceed initial call plus one repair")
        if self.max_tool_steps != 1: raise ValueError("max_tool_steps must be exactly one")
        if self.min_difficulty < 1 or self.max_difficulty < self.min_difficulty: raise ValueError("invalid difficulty range")
