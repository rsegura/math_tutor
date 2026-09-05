"""Provider-neutral model boundary."""
from typing import Protocol
from math_tutor.harness.context import HarnessContext
class ModelAdapter(Protocol):
    def complete(self, *, prompt: str, context: HarnessContext, repair: bool, validation_error: str | None = None) -> object: ...
